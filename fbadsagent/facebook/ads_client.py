"""Wrapper around the Facebook Marketing API (facebook-business SDK).

Safety defaults: everything this client creates is created **PAUSED** and
campaigns are created with an explicit spend cap. In ``dry_run`` mode (the
default used by the CLI unless ``--live`` is passed) no network call is
made at all — the exact payloads that *would* be sent are returned instead,
so the agent can be inspected/approved before it ever touches a real ad
account.
"""
from __future__ import annotations

import uuid
from typing import Any

from fbadsagent.config import Settings
from fbadsagent.models import AdCreativeCopy, CampaignPlan, GeneratedImage, ProductInput


class AdsClientError(RuntimeError):
    pass


class FacebookAdsClient:
    def __init__(self, settings: Settings):
        self._settings = settings

    def _require_credentials(self) -> None:
        missing = [
            name
            for name, value in (
                ("FB_ACCESS_TOKEN", self._settings.fb_access_token),
                ("FB_APP_ID", self._settings.fb_app_id),
                ("FB_APP_SECRET", self._settings.fb_app_secret),
                ("FB_AD_ACCOUNT_ID", self._settings.fb_ad_account_id),
                ("FB_PAGE_ID", self._settings.fb_page_id),
            )
            if not value
        ]
        if missing:
            raise AdsClientError(
                "Missing Facebook credentials: " + ", ".join(missing) +
                ". Set them in .env before running with --live."
            )

    def build_campaign_payloads(
        self,
        product: ProductInput,
        creatives: list[AdCreativeCopy],
        images: list[GeneratedImage],
        landing_url: str,
    ) -> list[dict[str, Any]]:
        """Build the create-campaign/adset/creative/ad payloads without sending them."""
        image_by_variant = {img.variant_id: img for img in images}
        campaign_payload = {
            "name": f"{product.name} - AI Agent Campaign",
            "objective": "OUTCOME_SALES",
            "status": "PAUSED",
            "special_ad_categories": [],
        }
        adset_payload = {
            "name": f"{product.name} - AdSet",
            "daily_budget": int(product.daily_budget * 100),  # cents
            "billing_event": "IMPRESSIONS",
            "optimization_goal": "OFFSITE_CONVERSIONS",
            "targeting": {
                "geo_locations": {"countries": product.target_countries},
            },
            "status": "PAUSED",
        }
        ad_payloads = []
        for creative in creatives:
            image = image_by_variant.get(creative.variant_id)
            ad_payloads.append(
                {
                    "variant_id": creative.variant_id,
                    "creative": {
                        "name": f"{product.name} - {creative.variant_id}",
                        "object_story_spec": {
                            "page_id": self._settings.fb_page_id,
                            "link_data": {
                                "message": creative.primary_text,
                                "link": landing_url,
                                "name": creative.headline,
                                "description": creative.description,
                                "call_to_action": {
                                    "type": creative.call_to_action,
                                    "value": {"link": landing_url},
                                },
                                "image_hash": None,
                                "image_path": image.path if image else None,
                            },
                        },
                    },
                    "ad": {
                        "name": f"{product.name} - {creative.variant_id}",
                        "status": "PAUSED",
                    },
                }
            )
        return [
            {"type": "campaign", "payload": campaign_payload},
            {"type": "adset", "payload": adset_payload},
            *[{"type": "ad", "payload": p} for p in ad_payloads],
        ]

    def create_campaign(
        self,
        product: ProductInput,
        creatives: list[AdCreativeCopy],
        images: list[GeneratedImage],
        landing_url: str,
        dry_run: bool = True,
    ) -> CampaignPlan:
        payloads = self.build_campaign_payloads(product, creatives, images, landing_url)

        if dry_run:
            return CampaignPlan(
                campaign_name=payloads[0]["payload"]["name"],
                objective=payloads[0]["payload"]["objective"],
                daily_budget=product.daily_budget,
                status="PAUSED",
                dry_run=True,
                payloads=payloads,
            )

        self._require_credentials()
        # Imported lazily so dry-run mode (the default, and all tests) never
        # requires the facebook-business SDK to be importable/configured.
        from facebook_business.adobjects.adaccount import AdAccount
        from facebook_business.adobjects.adimage import AdImage
        from facebook_business.adobjects.adcreative import AdCreative
        from facebook_business.adobjects.ad import Ad
        from facebook_business.adobjects.adset import AdSet
        from facebook_business.adobjects.campaign import Campaign
        from facebook_business.api import FacebookAdsApi

        FacebookAdsApi.init(
            self._settings.fb_app_id,
            self._settings.fb_app_secret,
            self._settings.fb_access_token,
        )
        account = AdAccount(self._settings.fb_ad_account_id)

        campaign_params = dict(payloads[0]["payload"])
        campaign = account.create_campaign(params=campaign_params)
        campaign_id = campaign.get_id()

        adset_params = dict(payloads[1]["payload"])
        adset_params["campaign_id"] = campaign_id
        adset_params["promoted_object"] = {"page_id": self._settings.fb_page_id}
        adset = account.create_ad_set(params=adset_params)
        adset_id = adset.get_id()

        ad_ids: list[str] = []
        for entry in payloads[2:]:
            ad_spec = entry["payload"]
            link_data = ad_spec["creative"]["object_story_spec"]["link_data"]
            image_path = link_data.pop("image_path", None)
            link_data.pop("image_hash", None)
            if image_path:
                image = AdImage(parent_id=self._settings.fb_ad_account_id)
                image[AdImage.Field.filename] = image_path
                image.remote_create()
                link_data["image_hash"] = image[AdImage.Field.hash]

            creative = account.create_ad_creative(params=ad_spec["creative"])
            ad_params = dict(ad_spec["ad"])
            ad_params["adset_id"] = adset_id
            ad_params["creative"] = {"creative_id": creative.get_id()}
            ad = account.create_ad(params=ad_params)
            ad_ids.append(ad.get_id())

        return CampaignPlan(
            campaign_name=campaign_params["name"],
            objective=campaign_params["objective"],
            daily_budget=product.daily_budget,
            status="PAUSED",
            dry_run=False,
            campaign_id=campaign_id,
            adset_id=adset_id,
            ad_ids=ad_ids,
            payloads=payloads,
        )


def new_variant_id() -> str:
    return uuid.uuid4().hex[:8]
