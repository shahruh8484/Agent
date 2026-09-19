"""Telegram bot front-end for the finance agent: free-text messages get
parsed into transactions or balance/report queries. Run with:

    python -m fbadsagent.finance.telegram_bot

Create a bot with @BotFather, put its token in TELEGRAM_BOT_TOKEN, and list
the Telegram user IDs allowed to use it in FINANCE_TELEGRAM_ALLOWED_USER_IDS
(comma-separated) — otherwise anyone who finds the bot can log transactions.
"""
from __future__ import annotations

import logging
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from fbadsagent.config import Settings, get_settings
from fbadsagent.finance.nlp import parse_finance_message
from fbadsagent.finance.replies import format_balance_reply, format_report_reply, handle_finance_intent
from fbadsagent.finance.store import FinanceStore
from fbadsagent.llm.provider import LLMError, LLMProvider, get_llm_provider

logger = logging.getLogger(__name__)

NO_ACCESS_MESSAGE = "У тебя нет доступа к этому боту."

WELCOME_MESSAGE = (
    "Привет! Я веду учёт финансов компании. Просто пиши, например:\n"
    "«потратил 50000 на еду» или «доход 2000000 зарплата»\n"
    "Также можно спросить «какой баланс» или «отчёт за месяц».\n"
    "Команды: /balance, /report [today|week|month|all]"
)

FALLBACK_SYSTEM_PROMPT = (
    "You are a helpful finance assistant for a small company's bookkeeping "
    "Telegram bot. Answer briefly, in the same language the user wrote in. "
    "You can log transactions (\"spent 50000 on food\") and answer balance/"
    "report questions (\"what's my balance\", \"report for this month\") when "
    "asked directly — if the user's message wasn't recognized as one of "
    "those, gently suggest rephrasing rather than making up numbers."
)

REPORT_PERIODS = ("today", "week", "month", "all")


def is_authorized(settings: Settings, user_id: int | None) -> bool:
    allowed = settings.finance_telegram_allowed_user_ids_list()
    if not allowed:
        return True
    return user_id in allowed


def handle_text_message(
    store: FinanceStore,
    llm: LLMProvider,
    settings: Settings,
    text: str,
    telegram_user_id: int | None = None,
) -> str:
    """Pure dispatch logic shared by the bot handlers and tests: parses the
    message and either mutates the store (add_transaction) or formats a
    balance/report reply, falling back to a plain conversational answer."""
    intent = parse_finance_message(llm, text)
    if intent is None:
        return llm.generate(FALLBACK_SYSTEM_PROMPT, text, max_tokens=400)
    return handle_finance_intent(
        store, settings, intent, source="telegram", telegram_user_id=telegram_user_id
    )


def build_application(settings: Settings, store: FinanceStore) -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Create a bot with @BotFather and add "
            "the token to .env."
        )

    def _user_id(update: Update) -> int | None:
        return update.effective_user.id if update.effective_user else None

    async def _reject_if_unauthorized(update: Update) -> bool:
        if is_authorized(settings, _user_id(update)):
            return False
        await update.message.reply_text(NO_ACCESS_MESSAGE)
        return True

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await _reject_if_unauthorized(update):
            return
        await update.message.reply_text(WELCOME_MESSAGE)

    async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await _reject_if_unauthorized(update):
            return
        await update.message.reply_text(
            format_balance_reply(store, settings.finance_default_currency)
        )

    async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await _reject_if_unauthorized(update):
            return
        period = context.args[0] if context.args else "month"
        if period not in REPORT_PERIODS:
            period = "month"
        await update.message.reply_text(
            format_report_reply(store, period, settings.finance_default_currency)
        )

    async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await _reject_if_unauthorized(update):
            return
        message = (update.message.text or "").strip()
        if not message:
            return
        try:
            llm = get_llm_provider(settings)
            reply = handle_text_message(store, llm, settings, message, telegram_user_id=_user_id(update))
        except LLMError as exc:
            reply = f"Не удалось обработать сообщение: {exc}"
        except Exception:
            logger.exception("Finance bot failed to handle message")
            reply = "Что-то пошло не так, попробуй ещё раз."
        await update.message.reply_text(reply)

    application = Application.builder().token(settings.telegram_bot_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("report", report))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    return application


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    store = FinanceStore(Path(settings.data_dir) / "finance.json")
    application = build_application(settings, store)
    logger.info("Finance Telegram bot starting (polling)...")
    application.run_polling()


if __name__ == "__main__":
    main()
