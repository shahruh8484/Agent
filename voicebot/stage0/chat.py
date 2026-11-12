"""Консольный чат: вы пишете как клиент, бот отвечает по промпту.

Запуск:  python chat.py
Команды: /new — начать новый звонок, /json — показывать полный JSON, /quit — выход.
"""
from __future__ import annotations

import json

from bot import FINAL_ACTIONS, new_conversation

C_BOT, C_INFO, C_END = "\033[96m", "\033[90m", "\033[0m"


def show(turn, conv, full_json: bool) -> None:
    r = turn.reply
    print(f"{C_BOT}БОТ: {r.get('say', '')}{C_END}")
    info = f"[{turn.seconds:.2f} c | язык={r.get('lang')} | action={r.get('action')}]"
    filled = {k: v for k, v in conv.fields.items() if v}
    if filled:
        info += f" поля: {filled}"
    if r.get("callback_time"):
        info += f" перезвон: {r['callback_time']}"
    print(C_INFO + info + C_END)
    if full_json:
        print(C_INFO + json.dumps(r, ensure_ascii=False, indent=2) + C_END)


def main() -> None:
    full_json = False
    conv = new_conversation()
    print(f"Модель: {conv.model}. Вы — клиент. /new — новый звонок, /json — JSON, /quit — выход.\n")
    show(conv.start(), conv, full_json)
    while True:
        try:
            text = input("ВЫ: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/json":
            full_json = not full_json
            print(f"Полный JSON: {'вкл' if full_json else 'выкл'}")
            continue
        if text == "/new":
            conv = new_conversation(conv.client)
            print("\n--- новый звонок ---")
            show(conv.start(), conv, full_json)
            continue
        turn = conv.customer_says(text)
        show(turn, conv, full_json)
        if turn.reply.get("action") in FINAL_ACTIONS:
            print(f"{C_INFO}--- бот завершил звонок ({turn.reply['action']}). /new — новый звонок ---{C_END}")


if __name__ == "__main__":
    main()
