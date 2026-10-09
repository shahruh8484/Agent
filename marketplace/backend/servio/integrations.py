"""Outbound integrations: SMS codes, YooKassa payments, Expo push notifications.

Each one has a "dev" mode so the app runs end-to-end without accounts or keys.
"""
from __future__ import annotations

import logging
import uuid

import requests

from servio.config import Plan, Settings

log = logging.getLogger(__name__)


def send_sms_code(settings: Settings, phone: str, code: str) -> None:
    if settings.sms_provider == "dev":
        log.info("SMS code for %s: %s", phone, code)
        return
    if settings.sms_provider == "smsru":
        resp = requests.get(
            "https://sms.ru/sms/send",
            params={
                "api_id": settings.smsru_api_id,
                "to": phone.lstrip("+"),
                "msg": f"{settings.app_name}: код входа {code}",
                "json": 1,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return
    raise ValueError(f"Unknown sms_provider: {settings.sms_provider}")


def create_yookassa_payment(settings: Settings, plan: Plan, payment_id: int) -> tuple[str, str]:
    """Creates a payment and returns (provider_id, confirmation_url)."""
    resp = requests.post(
        "https://api.yookassa.ru/v3/payments",
        auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
        headers={"Idempotence-Key": str(uuid.uuid4())},
        json={
            "amount": {"value": f"{plan.price}.00", "currency": settings.currency},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": settings.payment_return_url},
            "description": f"{settings.app_name}: подписка «{plan.title}»",
            "metadata": {"payment_id": payment_id},
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["id"], data["confirmation"]["confirmation_url"]


def fetch_yookassa_status(settings: Settings, provider_id: str) -> str:
    """Webhook bodies are not signed, so the status is always re-read from the API."""
    resp = requests.get(
        f"https://api.yookassa.ru/v3/payments/{provider_id}",
        auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["status"]


def send_push(settings: Settings, tokens: list[str], title: str, body: str, data: dict) -> None:
    if not settings.push_enabled or not tokens:
        return
    try:
        requests.post(
            "https://exp.host/--/api/v2/push/send",
            json=[{"to": t, "title": title, "body": body, "data": data} for t in tokens],
            timeout=10,
        )
    except requests.RequestException:
        log.exception("Push delivery failed")
