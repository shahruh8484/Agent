"""Client for the public Meta Ad Library API (ads_archive endpoint).

This is a free, legitimate API for researching ads that are currently or
were previously running on Facebook/Instagram — the standard way to do
"competitor ad spy" research without a paid third-party spy tool. It
requires only a standard user access token with the ``ads_read``
permission (no Business Verification needed for commercial-ad search).

Docs: https://www.facebook.com/ads/library/api/
"""
from __future__ import annotations

import requests

from fbadsagent.config import Settings
from fbadsagent.models import CompetitorAd

GRAPH_BASE = "https://graph.facebook.com"


class AdLibraryError(RuntimeError):
    pass


class AdLibraryClient:
    def __init__(self, settings: Settings):
        self._settings = settings

    def search_competitor_ads(self, query: str, limit: int = 20) -> list[CompetitorAd]:
        """Search the Ad Library for ads whose text matches ``query``."""
        if not self._settings.fb_access_token:
            raise AdLibraryError(
                "FB_ACCESS_TOKEN is not set. Get one at https://developers.facebook.com "
                "with the 'ads_read' permission to enable competitor research."
            )

        url = f"{GRAPH_BASE}/{self._settings.fb_api_version}/ads_archive"
        params = {
            "access_token": self._settings.fb_access_token,
            "search_terms": query,
            "ad_type": "ALL",
            "ad_reached_countries": self._settings.ad_library_countries,
            "ad_active_status": "ACTIVE",
            "limit": limit,
            "fields": ",".join(
                [
                    "page_name",
                    "ad_creative_bodies",
                    "ad_creative_link_titles",
                    "ad_snapshot_url",
                    "ad_delivery_start_time",
                    "publisher_platforms",
                ]
            ),
        }

        response = requests.get(url, params=params, timeout=30)
        if response.status_code != 200:
            raise AdLibraryError(
                f"Ad Library API returned {response.status_code}: {response.text}"
            )

        data = response.json()
        ads: list[CompetitorAd] = []
        for item in data.get("data", []):
            bodies = item.get("ad_creative_bodies") or [""]
            titles = item.get("ad_creative_link_titles") or [""]
            ads.append(
                CompetitorAd(
                    page_name=item.get("page_name", "Unknown"),
                    ad_creative_body=bodies[0] if bodies else "",
                    ad_creative_link_title=titles[0] if titles else "",
                    ad_snapshot_url=item.get("ad_snapshot_url", ""),
                    ad_delivery_start_time=item.get("ad_delivery_start_time"),
                    publisher_platforms=item.get("publisher_platforms", []),
                )
            )
        return ads
