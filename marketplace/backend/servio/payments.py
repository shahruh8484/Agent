"""Specialist subscriptions and their payment through Payme and Click.

Both providers call our server to confirm a payment:
- Payme Merchant API (JSON-RPC): https://developer.help.paycom.uz/metody-merchant-api/
- Click SHOP API (Prepare/Complete): https://docs.click.uz/click-api-request/
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import sqlite3
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

from servio.config import Settings
from servio.db import iso, now

# ---------- subscriptions ----------


def subscription_until(db: sqlite3.Connection, user_id: int) -> str | None:
    r = db.execute("SELECT MAX(ends_at) FROM subscriptions WHERE user_id = ?", (user_id,)).fetchone()[0]
    return r if r and datetime.fromisoformat(r) > now() else None


def extend_subscription(db: sqlite3.Connection, user_id: int, plan_id: str, days: int) -> None:
    current = subscription_until(db, user_id)
    start = datetime.fromisoformat(current) if current else now()
    db.execute(
        "INSERT INTO subscriptions (user_id, plan_id, starts_at, ends_at) VALUES (?, ?, ?, ?)",
        (user_id, plan_id, iso(start), iso(start + timedelta(days=days))),
    )


def fulfil(db: sqlite3.Connection, settings: Settings, payment: sqlite3.Row) -> None:
    db.execute("UPDATE payments SET status = 'succeeded' WHERE id = ?", (payment["id"],))
    plan = settings.plan(payment["plan_id"])
    if plan is not None:
        extend_subscription(db, payment["user_id"], plan.id, plan.days)


def checkout_url(settings: Settings, provider: str, payment_id: int, amount: int, lang: str) -> str:
    if provider == "payme":
        params = (
            f"m={settings.payme_merchant_id};ac.order_id={payment_id};a={amount * 100};"
            f"l={lang};c={settings.payment_return_url}"
        )
        host = "https://test.paycom.uz" if settings.payme_test else "https://checkout.paycom.uz"
        return f"{host}/{base64.b64encode(params.encode()).decode()}"
    if provider == "click":
        return "https://my.click.uz/services/pay?" + urlencode({
            "service_id": settings.click_service_id,
            "merchant_id": settings.click_merchant_id,
            "amount": amount,
            "transaction_param": payment_id,
            "return_url": settings.payment_return_url,
        })
    raise ValueError(provider)


# ---------- Payme ----------

PAYME_TIMEOUT_MS = 43_200_000  # unperformed transactions expire after 12 hours


class PaymeError(Exception):
    def __init__(self, code: int, ru: str, uz: str, en: str, data: str | None = None):
        self.code, self.message, self.data = code, {"ru": ru, "uz": uz, "en": en}, data


def _ms() -> int:
    return int(time.time() * 1000)


def _payme_payment(db: sqlite3.Connection, params: dict) -> sqlite3.Row:
    """Validates account + amount, as both CheckPerform and Create must."""
    order_id = str((params.get("account") or {}).get("order_id", ""))
    p = db.execute(
        "SELECT * FROM payments WHERE id = ? AND provider = 'payme'",
        (int(order_id) if order_id.isdigit() else -1,),
    ).fetchone()
    if p is None:
        raise PaymeError(-31050, "Заказ не найден", "Buyurtma topilmadi", "Order not found", "order_id")
    if p["status"] != "pending":
        raise PaymeError(-31051, "Заказ уже оплачен или отменён", "Buyurtma to'langan yoki bekor qilingan",
                         "Order is already paid or cancelled", "order_id")
    if params.get("amount") != p["amount"] * 100:
        raise PaymeError(-31001, "Неверная сумма", "Noto'g'ri summa", "Invalid amount")
    return p


def _payme_tx(db: sqlite3.Connection, payme_id: str) -> sqlite3.Row:
    tx = db.execute("SELECT * FROM payme_transactions WHERE payme_id = ?", (payme_id,)).fetchone()
    if tx is None:
        raise PaymeError(-31003, "Транзакция не найдена", "Tranzaksiya topilmadi", "Transaction not found")
    return tx


def _payme_expire(db: sqlite3.Connection, tx: sqlite3.Row) -> None:
    db.execute(
        "UPDATE payme_transactions SET state = -1, reason = 4, cancel_time = ? WHERE id = ?",
        (_ms(), tx["id"]),
    )
    db.execute("UPDATE payments SET status = 'canceled' WHERE id = ?", (tx["payment_id"],))


def _cannot_perform() -> PaymeError:
    return PaymeError(-31008, "Невозможно выполнить операцию", "Amalni bajarib bo'lmaydi",
                      "Unable to perform operation")


def handle_payme(settings: Settings, db: sqlite3.Connection, authorization: str, body: dict) -> dict:
    req_id = body.get("id")
    try:
        expected = "Basic " + base64.b64encode(f"Paycom:{settings.payme_key}".encode()).decode()
        if not settings.payme_key or not hmac.compare_digest(authorization, expected):
            raise PaymeError(-32504, "Недостаточно привилегий", "Ruxsat yo'q", "Insufficient privileges")
        method, params = body.get("method"), body.get("params") or {}
        handler = PAYME_METHODS.get(method)
        if handler is None:
            raise PaymeError(-32601, "Метод не найден", "Metod topilmadi", "Method not found", method)
        result = handler(settings, db, params)
        db.commit()
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except PaymeError as e:
        db.commit()  # keep expirations recorded while reporting the error
        error = {"code": e.code, "message": e.message}
        if e.data:
            error["data"] = e.data
        return {"jsonrpc": "2.0", "id": req_id, "error": error}


def _check_perform(settings, db, params):
    _payme_payment(db, params)
    return {"allow": True}


def _create(settings, db, params):
    existing = db.execute(
        "SELECT * FROM payme_transactions WHERE payme_id = ?", (params.get("id"),)
    ).fetchone()
    if existing is not None:
        if existing["state"] != 1:
            raise _cannot_perform()
        if _ms() - existing["create_time"] > PAYME_TIMEOUT_MS:
            _payme_expire(db, existing)
            raise _cannot_perform()
        return {"create_time": existing["create_time"], "transaction": str(existing["id"]), "state": 1}
    payment = _payme_payment(db, params)
    busy = db.execute(
        "SELECT 1 FROM payme_transactions WHERE payment_id = ? AND state = 1", (payment["id"],)
    ).fetchone()
    if busy:
        raise PaymeError(-31050, "Заказ ожидает оплаты в другой транзакции",
                         "Buyurtma boshqa tranzaksiyada to'lovni kutmoqda",
                         "Order is awaiting payment in another transaction", "order_id")
    created = _ms()
    tx_id = db.execute(
        """INSERT INTO payme_transactions (payme_id, payment_id, amount, state, payme_time, create_time)
           VALUES (?, ?, ?, 1, ?, ?)""",
        (params["id"], payment["id"], params["amount"], int(params.get("time") or created), created),
    ).lastrowid
    db.execute("UPDATE payments SET provider_id = ? WHERE id = ?", (params["id"], payment["id"]))
    return {"create_time": created, "transaction": str(tx_id), "state": 1}


def _perform(settings, db, params):
    tx = _payme_tx(db, params.get("id"))
    if tx["state"] == 1:
        if _ms() - tx["create_time"] > PAYME_TIMEOUT_MS:
            _payme_expire(db, tx)
            raise _cannot_perform()
        performed = _ms()
        db.execute("UPDATE payme_transactions SET state = 2, perform_time = ? WHERE id = ?",
                   (performed, tx["id"]))
        fulfil(db, settings, db.execute("SELECT * FROM payments WHERE id = ?", (tx["payment_id"],)).fetchone())
        return {"transaction": str(tx["id"]), "perform_time": performed, "state": 2}
    if tx["state"] == 2:
        return {"transaction": str(tx["id"]), "perform_time": tx["perform_time"], "state": 2}
    raise _cannot_perform()


def _cancel(settings, db, params):
    tx = _payme_tx(db, params.get("id"))
    if tx["state"] == 1:
        cancelled = _ms()
        db.execute("UPDATE payme_transactions SET state = -1, cancel_time = ?, reason = ? WHERE id = ?",
                   (cancelled, params.get("reason"), tx["id"]))
        db.execute("UPDATE payments SET status = 'canceled' WHERE id = ?", (tx["payment_id"],))
        return {"transaction": str(tx["id"]), "cancel_time": cancelled, "state": -1}
    if tx["state"] == 2:
        # The subscription was already granted; refunds are handled manually.
        raise PaymeError(-31007, "Подписка уже активирована, отмена невозможна",
                         "Obuna faollashtirilgan, bekor qilib bo'lmaydi",
                         "Subscription already activated, cannot cancel")
    return {"transaction": str(tx["id"]), "cancel_time": tx["cancel_time"], "state": tx["state"]}


def _tx_out(tx: sqlite3.Row) -> dict:
    return {
        "create_time": tx["create_time"],
        "perform_time": tx["perform_time"],
        "cancel_time": tx["cancel_time"],
        "transaction": str(tx["id"]),
        "state": tx["state"],
        "reason": tx["reason"],
    }


def _check(settings, db, params):
    return _tx_out(_payme_tx(db, params.get("id")))


def _statement(settings, db, params):
    rows = db.execute(
        "SELECT * FROM payme_transactions WHERE payme_time BETWEEN ? AND ? ORDER BY payme_time",
        (params.get("from", 0), params.get("to", 0)),
    ).fetchall()
    return {"transactions": [
        {"id": t["payme_id"], "time": t["payme_time"], "amount": t["amount"],
         "account": {"order_id": str(t["payment_id"])}, **_tx_out(t)}
        for t in rows
    ]}


PAYME_METHODS = {
    "CheckPerformTransaction": _check_perform,
    "CreateTransaction": _create,
    "PerformTransaction": _perform,
    "CancelTransaction": _cancel,
    "CheckTransaction": _check,
    "GetStatement": _statement,
}


# ---------- Click ----------


def handle_click(settings: Settings, db: sqlite3.Connection, form: dict[str, str], action: int) -> dict:
    """Click calls Prepare (action=0) and then Complete (action=1) for each payment."""
    out = {
        "click_trans_id": form.get("click_trans_id"),
        "merchant_trans_id": form.get("merchant_trans_id"),
    }
    id_key = "merchant_prepare_id" if action == 0 else "merchant_confirm_id"

    def fail(code: int, note: str) -> dict:
        return {**out, id_key: None, "error": code, "error_note": note}

    required = ["click_trans_id", "service_id", "merchant_trans_id", "amount", "action", "sign_time", "sign_string"]
    if action == 1:
        required.append("merchant_prepare_id")
    if any(k not in form for k in required):
        return fail(-8, "Error in request from click")
    sign_source = (
        form["click_trans_id"] + form["service_id"] + settings.click_secret_key + form["merchant_trans_id"]
        + (form["merchant_prepare_id"] if action == 1 else "")
        + form["amount"] + form["action"] + form["sign_time"]
    )
    if not settings.click_secret_key or not hmac.compare_digest(
        hashlib.md5(sign_source.encode()).hexdigest(), form["sign_string"]
    ):
        return fail(-1, "SIGN CHECK FAILED!")
    if form["action"] != str(action):
        return fail(-3, "Action not found")

    trans_id = form["merchant_trans_id"]
    payment = db.execute(
        "SELECT * FROM payments WHERE id = ? AND provider = 'click'",
        (int(trans_id) if trans_id.isdigit() else -1,),
    ).fetchone()
    if payment is None:
        return fail(-5, "User does not exist")
    if action == 1 and form["merchant_prepare_id"] != str(payment["id"]):
        return fail(-6, "Transaction does not exist")
    if payment["status"] == "succeeded":
        return fail(-4, "Already paid")
    if payment["status"] == "canceled":
        return fail(-9, "Transaction cancelled")
    try:
        amount_ok = abs(float(form["amount"]) - payment["amount"]) < 0.01
    except ValueError:
        amount_ok = False
    if not amount_ok:
        return fail(-2, "Incorrect parameter amount")

    if action == 0:
        db.execute("UPDATE payments SET provider_id = ? WHERE id = ?", (form["click_trans_id"], payment["id"]))
    elif int(form.get("error") or 0) < 0:
        # Click reports the payment failed on its side (e.g. insufficient funds).
        db.execute("UPDATE payments SET status = 'canceled' WHERE id = ?", (payment["id"],))
        db.commit()
        return fail(-9, "Transaction cancelled")
    else:
        fulfil(db, settings, payment)
    db.commit()
    return {**out, id_key: payment["id"], "error": 0, "error_note": "Success"}
