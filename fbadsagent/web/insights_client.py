"""Client for the Facebook Marketing API Insights endpoint — the data
source for the dashboard's daily clicks/spend/leads charts.

Docs: https://developers.facebook.com/docs/marketing-api/insights
"""
from __future__ import annotations

import requests

from fbadsagent.config import Settings
from fbadsagent.models import AccountInsightsSummary, DailyInsight

GRAPH_BASE = "https://graph.facebook.com"

# Action types the Marketing API uses for lead-generation results, across
# lead ads, pixel-based leads on a landing page, and Messenger lead flows.
LEAD_ACTION_TYPES = {
    "lead",
    "offsite_conversion.fb_pixel_lead",
    "onsite_conversion.lead_grouped",
    "onsite_conversion.total_messaging_connection",
}


class InsightsError(RuntimeError):
    pass


class FacebookInsightsClient:
    def __init__(self, settings: Settings):
        self._settings = settings

    def get_account_insights(
        self, account_id: str, date_preset: str = "last_30d"
    ) -> AccountInsightsSummary:
        if not self._settings.fb_access_token:
            raise InsightsError(
                "FB_ACCESS_TOKEN is not set. Add it to .env to load real ad account data."
            )

        account_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        url = f"{GRAPH_BASE}/{self._settings.fb_api_version}/{account_id}/insights"
        params = {
            "access_token": self._settings.fb_access_token,
            "date_preset": date_preset,
            "time_increment": 1,
            "level": "account",
            "fields": "spend,clicks,impressions,ctr,cpc,actions,account_name",
        }

        response = requests.get(url, params=params, timeout=30)
        if response.status_code != 200:
            raise InsightsError(
                f"Facebook Insights API returned {response.status_code}: {response.text}"
            )

        rows = response.json().get("data", [])
        return _summarize(account_id, date_preset, rows)


def _summarize(account_id: str, date_preset: str, rows: list[dict]) -> AccountInsightsSummary:
    daily: list[DailyInsight] = []
    account_name: str | None = None

    for row in rows:
        account_name = row.get("account_name") or account_name
        spend = float(row.get("spend", 0) or 0)
        clicks = int(float(row.get("clicks", 0) or 0))
        leads = _count_leads(row.get("actions") or [])
        daily.append(
            DailyInsight(
                date=row.get("date_start", ""),
                spend=spend,
                clicks=clicks,
                impressions=int(float(row.get("impressions", 0) or 0)),
                leads=leads,
                ctr=float(row.get("ctr", 0) or 0),
                cpc=float(row.get("cpc", 0) or 0),
                cpl=round(spend / leads, 2) if leads else None,
            )
        )

    total_spend = round(sum(d.spend for d in daily), 2)
    total_clicks = sum(d.clicks for d in daily)
    total_impressions = sum(d.impressions for d in daily)
    total_leads = sum(d.leads for d in daily)

    return AccountInsightsSummary(
        account_id=account_id,
        account_name=account_name,
        date_preset=date_preset,
        daily=daily,
        total_spend=total_spend,
        total_clicks=total_clicks,
        total_impressions=total_impressions,
        total_leads=total_leads,
        avg_ctr=round(total_clicks / total_impressions * 100, 2) if total_impressions else 0.0,
        avg_cpc=round(total_spend / total_clicks, 2) if total_clicks else 0.0,
        avg_cpl=round(total_spend / total_leads, 2) if total_leads else None,
    )


def _count_leads(actions: list[dict]) -> int:
    return sum(
        int(float(a.get("value", 0) or 0))
        for a in actions
        if a.get("action_type") in LEAD_ACTION_TYPES
    )
