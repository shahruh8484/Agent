"""Outbound integrations: SMS codes via Eskiz.uz and Expo push notifications.

Each one has a "dev" mode so the app runs end-to-end without accounts or keys.
"""
from __future__ import annotations

import logging
from threading import Lock

import requests

from servio.config import Settings

log = logging.getLogger(__name__)

ESKIZ_API = "https://notify.eskiz.uz/api"
_eskiz_token: str | None = None
_eskiz_lock = Lock()


def _eskiz_login(settings: Settings) -> str:
    resp = requests.post(
        f"{ESKIZ_API}/auth/login",
        data={"email": settings.eskiz_email, "password": settings.eskiz_password},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["data"]["token"]


def _eskiz_send(settings: Settings, phone: str, text: str) -> None:
    """Eskiz tokens live ~30 days; log in lazily and once more on 401."""
    global _eskiz_token
    for attempt in range(2):
        with _eskiz_lock:
            if _eskiz_token is None:
                _eskiz_token = _eskiz_login(settings)
            token = _eskiz_token
        resp = requests.post(
            f"{ESKIZ_API}/message/sms/send",
            headers={"Authorization": f"Bearer {token}"},
            data={"mobile_phone": phone.lstrip("+"), "message": text, "from": settings.eskiz_from},
            timeout=10,
        )
        if resp.status_code == 401 and attempt == 0:
            with _eskiz_lock:
                _eskiz_token = None
            continue
        resp.raise_for_status()
        return


def send_sms_code(settings: Settings, phone: str, code: str) -> None:
    text = settings.sms_template.format(code=code)
    if settings.sms_provider == "dev":
        log.info("SMS to %s: %s", phone, text)
        return
    if settings.sms_provider == "eskiz":
        _eskiz_send(settings, phone, text)
        return
    raise ValueError(f"Unknown sms_provider: {settings.sms_provider}")


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
