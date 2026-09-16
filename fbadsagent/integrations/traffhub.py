"""Client for the traff-hub.com CPA network API.

Documented endpoints (from the partner dashboard: Settings -> API):

- Send a lead:       POST/GET https://api.traff-hub.com/lead/add
  Required: api_key, phone, fio, ip, hash (per-campaign key from the
  traff-hub dashboard). Optional: referrer, sub1-sub5, language, price,
  comment, ewid.

- List lead statuses: POST/GET https://api.traff-hub.com/conversion/list
  Params: api_key, transaction_id, status, from (YYYY-MM-DD),
  to (YYYY-MM-DD), page, onPage.

Auth is a plain `api_key` parameter (no header scheme, no OAuth) — sent
in the JSON body here, which the docs list as a supported way to call
both endpoints.

There is no "list offers" endpoint: offers/campaigns are picked manually
in the traff-hub dashboard, which is also where a campaign's `hash` (the
value send_lead needs) comes from. This client only covers reporting
leads generated on your own landing pages back to traff-hub, and checking
their status.
"""
from __future__ import annotations

import requests

DEFAULT_BASE_URL = "https://api.traff-hub.com"

LEAD_STATUSES = {
    10: "pending",
    11: "confirmed",
    12: "rejected",
    13: "trash",
}


class TraffHubError(RuntimeError):
    pass


class TraffHubClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL):
        if not api_key:
            raise TraffHubError("api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/") if base_url else DEFAULT_BASE_URL

    def send_lead(
        self,
        phone: str,
        fio: str,
        ip: str,
        campaign_hash: str,
        referrer: str | None = None,
        language: str | None = None,
        price: float | None = None,
        comment: str | None = None,
        ewid: str | None = None,
        sub1: str | None = None,
        sub2: str | None = None,
        sub3: str | None = None,
        sub4: str | None = None,
        sub5: str | None = None,
    ) -> dict:
        payload = {
            "api_key": self._api_key,
            "phone": phone,
            "fio": fio,
            "ip": ip,
            "hash": campaign_hash,
        }
        optional = {
            "referrer": referrer,
            "language": language,
            "price": price,
            "comment": comment,
            "ewid": ewid,
            "sub1": sub1,
            "sub2": sub2,
            "sub3": sub3,
            "sub4": sub4,
            "sub5": sub5,
        }
        payload.update({k: v for k, v in optional.items() if v is not None})
        return self._post("/lead/add", payload)

    def list_conversions(
        self,
        transaction_id: str | list[str] | None = None,
        status: int | list[int] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        page: int | None = None,
        on_page: int | None = None,
    ) -> dict:
        payload: dict = {"api_key": self._api_key}
        if transaction_id is not None:
            payload["transaction_id"] = transaction_id
        if status is not None:
            payload["status"] = status
        if date_from is not None:
            payload["from"] = date_from
        if date_to is not None:
            payload["to"] = date_to
        if page is not None:
            payload["page"] = page
        if on_page is not None:
            payload["onPage"] = on_page
        return self._post("/conversion/list", payload)

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self._base_url}{path}"
        response = requests.post(url, json=payload, timeout=30)
        if response.status_code != 200:
            raise TraffHubError(
                f"traff-hub API returned {response.status_code}: {response.text}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise TraffHubError(
                f"traff-hub API returned a non-JSON response: {response.text[:300]}"
            ) from exc
