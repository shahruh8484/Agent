"""The finance panel: login-protected web UI over the books, plus the chat
assistant.

Run locally with:
    python -m finance
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from finance import calc, importer
from finance.assistant import (
    SYSTEM_PROMPT,
    AssistantError,
    apply_action,
    build_context,
    describe_action,
    history_to_messages,
    make_backend,
    user_content,
)
from finance.config import Settings, assistant_provider, get_settings
from finance.db import DB, Repo
from finance.security import verify_password

TEMPLATES_DIR = Path(__file__).parent / "templates"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def fmt_usd(value: float | None, sign: bool = False) -> str:
    if value is None:
        return "—"
    if abs(value) < 0.005:
        value = 0.0
    s = f"${abs(value):,.2f}"
    if value < 0:
        return "−" + s
    return ("+" + s) if sign and value > 0 else s


def fmt_uzs(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}".replace(",", " ") + " сум"


def fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    if float(value).is_integer():
        return f"{int(value):,}".replace(",", " ")
    return f"{value:,.{digits}f}".replace(",", " ")


def create_app(
    settings: Settings | None = None,
    repo: Repo | None = None,
    backend_factory: Callable[[], object] | None = None,
    today_fn: Callable[[], date] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if not settings.secret_key:
        raise RuntimeError(
            "SECRET_KEY не задан. Сгенерируйте: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    repo = repo or Repo(DB(Path(settings.data_dir) / "finance.db"))
    tz = ZoneInfo(settings.timezone)
    today_fn = today_fn or (lambda: datetime.now(tz).date())
    backend_factory = backend_factory or (lambda: make_backend(settings))
    provider_label = {"openai": f"OpenAI ({settings.openai_model})", "anthropic": f"Claude ({settings.anthropic_model})"}

    app = FastAPI(title="Finance Panel")
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=settings.session_https_only,
    )
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["usd"] = fmt_usd
    templates.env.filters["uzs"] = fmt_uzs
    templates.env.filters["num"] = fmt_num

    def authed(request: Request) -> bool:
        return bool(request.session.get("authenticated"))

    def login_redirect() -> RedirectResponse:
        return RedirectResponse("/login", status_code=302)

    def back(path: str, error: str | None = None, msg: str | None = None, **params) -> RedirectResponse:
        query = {k: v for k, v in params.items() if v}
        if error:
            query["error"] = error
        if msg:
            query["msg"] = msg
        return RedirectResponse(path + ("?" + urlencode(query) if query else ""), status_code=302)

    def render(request: Request, name: str, nav: str, **ctx):
        today = today_fn()
        return templates.TemplateResponse(
            request,
            name,
            {
                "nav": nav,
                "user": request.session.get("user"),
                "today": today.isoformat(),
                "error": request.query_params.get("error"),
                "msg": request.query_params.get("msg"),
                "periods": calc.PERIODS,
                "settings": repo.settings(),
                **ctx,
            },
        )

    def period_ctx(request: Request) -> tuple[str, str | None, str | None]:
        period = request.query_params.get("period", "this_month")
        if period not in dict(calc.PERIODS):
            period = "this_month"
        start, end = calc.period_range(period, today_fn())
        return period, start, end

    def run(path: str, fn: Callable[[], str | None]) -> RedirectResponse:
        """Run a form action, redirecting back with its message or error."""
        try:
            message = fn()
        except (ValueError, KeyError) as exc:
            return back(path, error=str(exc))
        return back(path, msg=message or "Сохранено.")

    def amount_usd(amount: float, currency: str) -> tuple[float, float | None, float | None]:
        if currency == "uzs":
            rate = repo.usd_uzs_rate()
            return amount / rate, amount, rate
        return amount, None, None

    # ------------------------------------------------------------------ auth

    @app.get("/login")
    def login_form(request: Request):
        if authed(request):
            return RedirectResponse("/", status_code=302)
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login")
    def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
        ok = username == settings.admin_username and verify_password(password, settings.admin_password_hash)
        if not ok:
            return templates.TemplateResponse(
                request, "login.html", {"error": "Неверный логин или пароль"}, status_code=401
            )
        request.session["authenticated"] = True
        request.session["user"] = username
        return RedirectResponse("/", status_code=302)

    @app.get("/logout")
    def logout(request: Request):
        request.session.clear()
        return login_redirect()

    # ------------------------------------------------------------------ home

    @app.get("/")
    def home(request: Request):
        if not authed(request):
            return login_redirect()
        today = today_fn()
        period, start, end = period_ctx(request)
        return render(
            request,
            "home.html",
            "home",
            period=period,
            s=calc.summary(repo, start, end, today),
            alerts=calc.alerts(repo, today),
            adv_balances=calc.advertiser_balances(repo, today),
            web_traffic=calc.web_balances(repo, today, "traffic"),
            web_product=calc.web_balances(repo, today, "product"),
            courier=calc.courier_balance(repo),
            stock=calc.stock_info(repo, today),
        )

    # --------------------------------------------------------------- traffic

    @app.get("/traffic")
    def traffic_page(request: Request):
        if not authed(request):
            return login_redirect()
        today = today_fn()
        period, start, end = period_ctx(request)
        links = repo.links()
        rates = {l["id"]: repo.rate_on(l["id"], today.isoformat()) for l in links}
        rep = calc.traffic_report(repo, start, end, today)
        link_names = {
            l["id"]: f"{l['web_name']} → {l['advertiser_name']}" + (f" ({l['offer']})" if l["offer"] else "")
            for l in links
        }
        return render(
            request,
            "traffic.html",
            "traffic",
            period=period,
            rep=rep,
            links=links,
            rates=rates,
            link_names=link_names,
            webs=repo.webs(),
            advertisers=repo.advertisers(),
            adv_balances={b.party_id: b for b in calc.advertiser_balances(repo, today)},
            web_balances={b.party_id: b for b in calc.web_balances(repo, today, "traffic")},
            payments=[p for p in repo.payments(start, end) if p["direction"] == "traffic"],
        )

    @app.post("/traffic/advertiser")
    def traffic_add_advertiser(request: Request, name: str = Form(...), terms: str = Form(""), note: str = Form("")):
        if not authed(request):
            return login_redirect()
        return run("/traffic", lambda: repo.add_advertiser(name, terms, note) and f"Рекл {name} сохранён.")

    @app.post("/webs/add")
    def add_web(request: Request, name: str = Form(...), terms: str = Form(""), note: str = Form(""), next: str = Form("/webs")):
        if not authed(request):
            return login_redirect()
        return run(_safe_next(next), lambda: repo.add_web(name, terms, note) and f"Веб {name} сохранён.")

    @app.post("/traffic/link")
    def traffic_add_link(
        request: Request,
        web_id: int = Form(...),
        advertiser_id: int = Form(...),
        offer: str = Form(""),
        valid_from: str = Form(...),
        adv_pay_type: str = Form(...),
        adv_rate: float = Form(...),
        guarantee_pct: float = Form(0),
        web_pay_type: str = Form(...),
        web_rate: float = Form(...),
        web_guarantee_pct: float = Form(0),
    ):
        if not authed(request):
            return login_redirect()
        return run("/traffic", lambda: repo.add_link(
            web_id, advertiser_id, offer, valid_from, adv_pay_type, adv_rate, guarantee_pct, web_pay_type, web_rate,
            web_guarantee_pct,
        ) and "Связка создана.")

    @app.post("/traffic/rate")
    def traffic_set_rate(
        request: Request,
        link_id: int = Form(...),
        valid_from: str = Form(...),
        adv_pay_type: str = Form(...),
        adv_rate: float = Form(...),
        guarantee_pct: float = Form(0),
        web_pay_type: str = Form(...),
        web_rate: float = Form(...),
        web_guarantee_pct: float = Form(0),
    ):
        if not authed(request):
            return login_redirect()

        def do():
            if repo.link(link_id) is None:
                raise ValueError("Связка не найдена.")
            repo.set_link_rate(link_id, valid_from, adv_pay_type, adv_rate, guarantee_pct, web_pay_type, web_rate,
                              web_guarantee_pct)
            return f"Ставки с {valid_from} сохранены."

        return run("/traffic", do)

    @app.post("/traffic/link/toggle")
    def traffic_toggle_link(request: Request, link_id: int = Form(...), active: int = Form(...)):
        if not authed(request):
            return login_redirect()
        repo.set_link_active(link_id, bool(active))
        return back("/traffic")

    @app.post("/traffic/stat")
    def traffic_add_stat(
        request: Request,
        link_id: int = Form(...),
        date_: str = Form(..., alias="date"),
        leads: int = Form(...),
        valid: str = Form(""),
        approves: int = Form(...),
        note: str = Form(""),
    ):
        if not authed(request):
            return login_redirect()

        def do():
            if repo.link(link_id) is None:
                raise ValueError("Связка не найдена.")
            valid_count = int(valid) if valid.strip() else leads
            repo.upsert_traffic_stat(link_id, date_, leads, valid_count, approves, note)
            return f"Статистика за {date_} сохранена."

        return run("/traffic", do)

    @app.post("/traffic/stat/delete")
    def traffic_delete_stat(request: Request, stat_id: int = Form(...)):
        if not authed(request):
            return login_redirect()
        repo.delete_traffic_stat(stat_id)
        return back("/traffic", msg="Строка удалена.")

    # --------------------------------------------------------------- product

    @app.get("/product")
    def product_page(request: Request):
        if not authed(request):
            return login_redirect()
        today = today_fn()
        period, start, end = period_ctx(request)
        orders = repo.orders(limit=300)
        order_money = {o["id"]: calc.order_money(o) for o in orders}
        return render(
            request,
            "product.html",
            "product",
            period=period,
            rep=calc.product_report(repo, start, end),
            products=repo.products(),
            webs=repo.webs(),
            stock=calc.stock_info(repo, today),
            web_rates=repo.product_web_rates(),
            stats=repo.product_stats(start, end),
            orders=orders,
            order_money=order_money,
            in_transit=[o for o in orders if o["status"] == "shipped"],
            moves=repo.stock_moves(limit=50),
            courier=calc.courier_balance(repo),
            web_balances={b.party_id: b for b in calc.web_balances(repo, today, "product")},
            payments=[p for p in repo.payments(start, end) if p["direction"] == "product"],
        )

    @app.post("/product/add")
    def product_add(
        request: Request,
        name: str = Form(...),
        unit_cost_usd: float = Form(...),
        initial_stock: int = Form(0),
    ):
        if not authed(request):
            return login_redirect()
        today = today_fn().isoformat()
        return run("/product", lambda: repo.add_product(name, unit_cost_usd, initial_stock, today) and f"Товар {name} сохранён.")

    @app.post("/product/stock")
    def product_stock(
        request: Request,
        product_id: int = Form(...),
        date_: str = Form(..., alias="date"),
        qty: int = Form(...),
        kind: str = Form("purchase"),
        cost_usd: float = Form(0),
        note: str = Form(""),
    ):
        if not authed(request):
            return login_redirect()

        def do():
            if repo.product(product_id) is None:
                raise ValueError("Товар не найден.")
            if kind not in ("purchase", "adjust"):
                raise ValueError("Неизвестный тип движения.")
            if kind == "purchase" and qty <= 0:
                raise ValueError("Количество закупки должно быть больше нуля.")
            repo.add_stock_move(date_, product_id, qty, kind, cost_usd=cost_usd if kind == "purchase" else 0, note=note)
            return "Склад обновлён."

        return run("/product", do)

    @app.post("/product/stock/delete")
    def product_stock_delete(request: Request, move_id: int = Form(...)):
        if not authed(request):
            return login_redirect()
        repo.delete_stock_move(move_id)
        return back("/product", msg="Движение удалено.")

    @app.post("/product/rate")
    def product_rate(
        request: Request,
        web_id: int = Form(...),
        product_id: int = Form(...),
        valid_from: str = Form(...),
        pay_type: str = Form(...),
        rate_usd: float = Form(...),
    ):
        if not authed(request):
            return login_redirect()
        return run("/product", lambda: repo.set_product_web_rate(web_id, product_id, valid_from, pay_type, rate_usd) or "Ставка сохранена.")

    @app.post("/product/stat")
    def product_stat(
        request: Request,
        web_id: int = Form(...),
        product_id: int = Form(...),
        date_: str = Form(..., alias="date"),
        leads: int = Form(...),
        approves: int = Form(...),
        note: str = Form(""),
    ):
        if not authed(request):
            return login_redirect()
        return run("/product", lambda: repo.upsert_product_stat(web_id, product_id, date_, leads, approves, note) or f"Статистика за {date_} сохранена.")

    @app.post("/product/stat/delete")
    def product_stat_delete(request: Request, stat_id: int = Form(...)):
        if not authed(request):
            return login_redirect()
        repo.delete_product_stat(stat_id)
        return back("/product", msg="Строка удалена.")

    @app.post("/product/order")
    def product_order(
        request: Request,
        product_id: int = Form(...),
        ship_date: str = Form(...),
        qty: int = Form(...),
        amount_uzs: float = Form(...),
        delivery_uzs: float = Form(0),
        web_id: str = Form(""),
        count: int = Form(1),
        note: str = Form(""),
    ):
        if not authed(request):
            return login_redirect()

        def do():
            n = max(1, min(count, 500))
            for _ in range(n):
                repo.add_order(ship_date, product_id, qty, amount_uzs, delivery_uzs, int(web_id) if web_id else None, note=note)
            return f"Заказов записано: {n}."

        return run("/product", do)

    @app.post("/product/order/status")
    def product_order_status(
        request: Request,
        order_ids: str = Form(...),
        status: str = Form(...),
        date_: str = Form(..., alias="date"),
        delivery_uzs: str = Form(""),
    ):
        if not authed(request):
            return login_redirect()

        def do():
            ids = [int(x) for x in order_ids.replace(",", " ").split() if x.strip().isdigit()]
            if not ids:
                raise ValueError("Укажите номера заказов.")
            delivery = float(delivery_uzs) if delivery_uzs.strip() else None
            for order_id in ids:
                repo.set_order_status(order_id, status, date_, delivery)
            return f"Обновлено заказов: {len(ids)}."

        return run("/product", do)

    @app.post("/product/order/delete")
    def product_order_delete(request: Request, order_id: int = Form(...)):
        if not authed(request):
            return login_redirect()
        repo.delete_order(order_id)
        return back("/product", msg=f"Заказ #{order_id} удалён.")

    # ----------------------------------------------------------------- webs

    @app.get("/webs")
    def webs_page(request: Request):
        if not authed(request):
            return login_redirect()
        today = today_fn()
        return render(
            request,
            "webs.html",
            "webs",
            webs=repo.webs(),
            advertisers=repo.advertisers(),
            traffic={b.party_id: b for b in calc.web_balances(repo, today, "traffic")},
            product={b.party_id: b for b in calc.web_balances(repo, today, "product")},
            adv_balances=calc.advertiser_balances(repo, today),
            accruals=repo.accruals(),
        )

    @app.post("/accruals/add")
    def accrual_add(
        request: Request,
        party: str = Form(...),
        direction: str = Form("traffic"),
        date_: str = Form(..., alias="date"),
        amount: float = Form(...),
        note: str = Form(""),
        next: str = Form("/webs"),
    ):
        if not authed(request):
            return login_redirect()

        def do():
            party_type, _, party_id = party.partition(":")
            if not party_id.isdigit():
                raise ValueError("Выберите рекла или веба.")
            repo.add_accrual(date_, direction, party_type, int(party_id), amount, note)
            return f"Начисление {fmt_usd(amount)} записано."

        return run(_safe_next(next), do)

    @app.post("/accruals/delete")
    def accrual_delete(request: Request, accrual_id: int = Form(...), next: str = Form("/webs")):
        if not authed(request):
            return login_redirect()
        repo.delete_accrual(accrual_id)
        return back(_safe_next(next), msg="Начисление удалено.")

    # ---------------------------------------------------------------- money

    @app.get("/money")
    def money_page(request: Request):
        if not authed(request):
            return login_redirect()
        period, start, end = period_ctx(request)
        return render(
            request,
            "money.html",
            "money",
            period=period,
            payments=repo.payments(start, end),
            expenses=repo.expenses(start, end),
            cash=calc.cash_flow(repo, start, end),
            webs=repo.webs(),
            advertisers=repo.advertisers(),
        )

    @app.post("/money/payment")
    def money_payment(
        request: Request,
        party: str = Form(...),
        direction: str = Form("traffic"),
        date_: str = Form(..., alias="date"),
        amount: float = Form(...),
        currency: str = Form("usd"),
        note: str = Form(""),
        next: str = Form("/money"),
    ):
        if not authed(request):
            return login_redirect()

        def do():
            # party is "advertiser:ID", "web:ID" or "courier"
            party_type, _, party_id = party.partition(":")
            d = {"advertiser": "traffic", "courier": "product", "owner": "general"}.get(party_type, direction)
            usd, uzs, rate = amount_usd(amount, currency)
            repo.add_payment(date_, d, party_type, int(party_id) if party_id else None, usd, uzs, rate, note)
            return f"Платёж {fmt_usd(usd)} записан."

        return run(_safe_next(next), do)

    @app.post("/money/payment/delete")
    def money_payment_delete(request: Request, payment_id: int = Form(...), next: str = Form("/money")):
        if not authed(request):
            return login_redirect()
        repo.delete_payment(payment_id)
        return back(_safe_next(next), msg="Платёж удалён.")

    @app.post("/money/expense")
    def money_expense(
        request: Request,
        direction: str = Form(...),
        category: str = Form(...),
        date_: str = Form(..., alias="date"),
        amount: float = Form(...),
        currency: str = Form("usd"),
        note: str = Form(""),
    ):
        if not authed(request):
            return login_redirect()

        def do():
            usd, uzs, rate = amount_usd(amount, currency)
            repo.add_expense(date_, direction, category, usd, uzs, rate, note)
            return f"Расход {fmt_usd(usd)} записан."

        return run("/money", do)

    @app.post("/money/expense/delete")
    def money_expense_delete(request: Request, expense_id: int = Form(...)):
        if not authed(request):
            return login_redirect()
        repo.delete_expense(expense_id)
        return back("/money", msg="Расход удалён.")

    # --------------------------------------------------------------- import

    @app.get("/import")
    def import_page(request: Request):
        if not authed(request):
            return login_redirect()
        return render(request, "import.html", "import", stage="paste", text="")

    @app.post("/import/preview")
    def import_preview(request: Request, text: str = Form("")):
        if not authed(request):
            return login_redirect()
        rows, problems = importer.parse(text)
        if not rows:
            return render(request, "import.html", "import", stage="paste", text=text,
                          error="Не нашёл ни одной строки с датой и суммой. Скопируйте столбцы: дата, приход, расход, имя.")
        return render(
            request,
            "import.html",
            "import",
            stage="map",
            text=text,
            rows=rows,
            problems=problems,
            names=importer.summarize(repo, rows),
            kinds=importer.KINDS,
            first=min(r.date for r in rows),
            last=max(r.date for r in rows),
            total_in=sum(r.amount_in for r in rows),
            total_out=sum(r.amount_out for r in rows),
        )

    @app.post("/import/commit")
    async def import_commit(request: Request):
        if not authed(request):
            return login_redirect()
        form = await request.form()
        rows, _ = importer.parse(str(form.get("text", "")))
        mapping = {}
        for key, value in form.multi_items():
            if key.startswith("name_"):
                idx = key.removeprefix("name_")
                mapping[str(value).strip().lower()] = str(form.get(f"kind_{idx}", "skip"))
        try:
            res = importer.apply(repo, rows, mapping)
        except ValueError as exc:
            return back("/import", error=str(exc))
        msg = (
            f"Импорт готов: платежей {res.payments}, расходов {res.expenses}"
            + (f", уже были (пропущены) {res.duplicates}" if res.duplicates else "")
            + (f", пропущено по выбору {res.skipped}" if res.skipped else "")
            + (f". Добавлены: {', '.join(res.created)}" if res.created else "")
            + "."
        )
        if res.errors:
            return back("/money", msg=msg, error="; ".join(res.errors[:10]), period="all")
        return back("/money", msg=msg, period="all")

    # ------------------------------------------------------------- settings

    @app.get("/settings")
    def settings_page(request: Request):
        if not authed(request):
            return login_redirect()
        return render(
            request, "settings.html", "settings",
            chat_provider=provider_label.get(assistant_provider(settings), ""),
        )

    @app.post("/settings")
    def settings_save(
        request: Request,
        usd_uzs_rate: float = Form(...),
        operator_pct: float = Form(...),
        tax_pct: float = Form(...),
        restock_days: float = Form(...),
        advertiser_low_days: float = Form(...),
    ):
        if not authed(request):
            return login_redirect()

        def do():
            if usd_uzs_rate <= 0:
                raise ValueError("Курс должен быть больше нуля.")
            for key, value in (
                ("usd_uzs_rate", usd_uzs_rate),
                ("operator_pct", operator_pct),
                ("tax_pct", tax_pct),
                ("restock_days", restock_days),
                ("advertiser_low_days", advertiser_low_days),
            ):
                repo.set_setting(key, value)
            return "Настройки сохранены."

        return run("/settings", do)

    # ----------------------------------------------------------------- chat

    @app.get("/chat")
    def chat_page(request: Request):
        if not authed(request):
            return login_redirect()
        messages = repo.chat_messages()
        for m in messages:
            m["described"] = [describe_action(a["name"], a["input"]) for a in m["actions"]]
        return render(request, "chat.html", "chat", messages=messages,
                      chat_enabled=bool(assistant_provider(settings)))

    @app.post("/chat/send")
    async def chat_send(request: Request, message: str = Form(""), image: UploadFile | None = File(None)):
        if not authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        message = message.strip()
        img = None
        if image is not None and image.filename:
            data = await image.read()
            media_type = image.content_type or ""
            if media_type not in IMAGE_TYPES:
                return JSONResponse({"error": "Поддерживаются PNG, JPG, WEBP, GIF."}, status_code=400)
            if len(data) > MAX_IMAGE_BYTES:
                return JSONResponse({"error": "Картинка больше 8 МБ."}, status_code=400)
            img = (data, media_type)
        if not message and not img:
            return JSONResponse({"error": "Пустое сообщение."}, status_code=400)

        try:
            backend = backend_factory()
            system = SYSTEM_PROMPT + "\n\n--- ТЕКУЩИЕ ДАННЫЕ ---\n" + build_context(repo, today_fn())
            msgs = history_to_messages(repo.chat_messages(limit=40))
            msgs.append({"role": "user", "content": user_content(message, img)})
            text, actions = backend.respond(system, msgs)
        except AssistantError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        repo.add_chat_message("user", ("[скриншот] " if img else "") + message)
        if not text and actions:
            text = "Вот что предлагаю внести — проверь и подтверди:"
        reply_id = repo.add_chat_message("assistant", text or "(пустой ответ)", actions)
        return JSONResponse({
            "id": reply_id,
            "reply": text,
            "actions": [describe_action(a["name"], a["input"]) for a in actions],
        })

    @app.post("/chat/{message_id}/apply")
    def chat_apply(request: Request, message_id: int):
        if not authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        m = repo.chat_message(message_id)
        if m is None or not m["actions"]:
            return JSONResponse({"error": "Нечего применять."}, status_code=404)
        if m["actions_status"] != "pending":
            return JSONResponse({"error": "Уже обработано."}, status_code=409)
        results, failed = [], 0
        for a in m["actions"]:
            try:
                results.append("✓ " + apply_action(repo, a["name"], a["input"]))
            except (ValueError, KeyError, TypeError) as exc:
                failed += 1
                results.append(f"✗ {describe_action(a['name'], a['input'])}: {exc}")
        result = "\n".join(results)
        repo.set_chat_actions_status(message_id, "applied", result)
        return JSONResponse({"result": result, "failed": failed})

    @app.post("/chat/{message_id}/reject")
    def chat_reject(request: Request, message_id: int):
        if not authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        m = repo.chat_message(message_id)
        if m is None or m["actions_status"] != "pending":
            return JSONResponse({"error": "Нечего отклонять."}, status_code=404)
        repo.set_chat_actions_status(message_id, "rejected", "Отклонено.")
        return JSONResponse({"result": "Отклонено."})

    @app.post("/chat/clear")
    def chat_clear(request: Request):
        if not authed(request):
            return login_redirect()
        repo.clear_chat()
        return back("/chat")

    return app


def _safe_next(path: str) -> str:
    """Only allow redirecting back to our own pages."""
    return path if path.startswith("/") and not path.startswith("//") else "/"
