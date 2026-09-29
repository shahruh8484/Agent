"""Minimal Meta Graph API client (read-only for now): checks that the token,
ad account, page and pixel the owner entered are reachable."""
from __future__ import annotations

import json
from typing import Any

import requests

GRAPH_URL = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v23.0"
CONN_FLAG = "fb_connection"
ACCOUNT_STATUS = {1: "активен", 2: "отключён", 3: "есть неоплаченный долг", 7: "на проверке",
                  8: "ожидает закрытия", 9: "в льготном периоде", 100: "ожидает закрытия",
                  101: "закрыт"}


class FacebookError(Exception):
    pass


def load_connection(store) -> dict:
    try:
        data = json.loads(store.get_flag(CONN_FLAG, "{}"))
    except ValueError:
        data = {}
    return {"token": "", "ad_account": "", "page_id": "", "pixel_id": "",
            "api_version": DEFAULT_API_VERSION, **data}


def save_connection(store, conn: dict) -> None:
    store.set_flag(CONN_FLAG, json.dumps(conn))


def ad_account_id(value: str) -> str:
    """"act_123" whatever the owner typed ("123" or "act_123")."""
    value = value.strip()
    return value if value.startswith("act_") or not value else f"act_{value}"


class FacebookClient:
    def __init__(self, token: str, api_version: str = DEFAULT_API_VERSION, timeout: int = 20):
        if not token:
            raise FacebookError("токен не указан")
        self._token, self._version, self._timeout = token, api_version, timeout

    def get(self, path: str, **params) -> dict[str, Any]:
        try:
            resp = requests.get(f"{GRAPH_URL}/{self._version}/{path.lstrip('/')}",
                                params={**params, "access_token": self._token},
                                timeout=self._timeout)
        except requests.RequestException as exc:
            raise FacebookError(f"нет связи с Facebook: {exc}") from exc
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400 or "error" in data:
            err = data.get("error", {}) if isinstance(data, dict) else {}
            raise FacebookError(err.get("message") or f"HTTP {resp.status_code}")
        return data


def check_connection(conn: dict, client: FacebookClient | None = None) -> list[tuple[str, bool, str]]:
    """[(what, ok, detail)] for the token, ad account, page and pixel."""
    try:
        client = client or FacebookClient(conn.get("token", ""), conn.get("api_version")
                                          or DEFAULT_API_VERSION)
    except FacebookError as exc:
        return [("Токен", False, str(exc))]
    out: list[tuple[str, bool, str]] = []

    def probe(label: str, path: str, fields: str, describe) -> None:
        try:
            out.append((label, True, describe(client.get(path, fields=fields))))
        except FacebookError as exc:
            out.append((label, False, str(exc)))

    probe("Токен", "me", "id,name", lambda d: f"{d.get('name', '')} (id {d.get('id')})")
    if conn.get("ad_account"):
        probe("Рекламный аккаунт", ad_account_id(conn["ad_account"]),
              "name,account_status,currency",
              lambda d: f"{d.get('name')} · {d.get('currency')} · "
                        f"{ACCOUNT_STATUS.get(d.get('account_status'), d.get('account_status'))}")
    if conn.get("page_id"):
        probe("Страница", conn["page_id"], "name", lambda d: d.get("name", ""))
    if conn.get("pixel_id"):
        probe("Пиксель", conn["pixel_id"], "name", lambda d: d.get("name", ""))
    return out


PIXEL_BASE = """<!-- Meta Pixel: вставьте перед </head> на КАЖДОЙ странице лендинга -->
<script>
!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;
n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,
document,'script','https://connect.facebook.net/en_US/fbevents.js');
fbq('init', '{pixel}');
fbq('track', 'PageView');
</script>
<noscript><img height="1" width="1" style="display:none"
src="https://www.facebook.com/tr?id={pixel}&ev=PageView&noscript=1"/></noscript>"""

PIXEL_LEAD = """<!-- Заявка: вызовите, когда форма успешно отправлена (или на странице «Спасибо») -->
<script>fbq('track', 'Lead');</script>"""


def pixel_code(pixel_id: str) -> tuple[str, str]:
    return PIXEL_BASE.replace("{pixel}", pixel_id or "ВАШ_PIXEL_ID"), PIXEL_LEAD
