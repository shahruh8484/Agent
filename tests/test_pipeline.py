import json

from fbadsagent.facebook.ads_client import FacebookAdsClient
from fbadsagent.orchestrator.pipeline import Pipeline
from fbadsagent.models import CompetitorAd
from tests.conftest import FakeLLM


class FakeAdLibrary:
    def __init__(self, ads):
        self._ads = ads

    def search_competitor_ads(self, query, limit=20):
        return self._ads


class ScriptedLLM:
    """Returns a different canned JSON response per call, in order."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, prompt: str, max_tokens: int = 1024) -> str:
        self.calls.append((system, prompt))
        return self._responses.pop(0)


def test_pipeline_runs_end_to_end_dry_run(settings, product):
    competitor_ads = [
        CompetitorAd(page_name="Rival", ad_creative_body="50% off earbuds", ad_creative_link_title="Sale")
    ]
    llm = ScriptedLLM(
        [
            json.dumps(
                {
                    "common_hooks": ["discount"],
                    "common_offers": ["50% off"],
                    "tone_observations": "urgent",
                    "recommended_angle": "emphasize battery life over price",
                }
            ),
            json.dumps(
                [
                    {
                        "primary_text": "40 hours of pure sound.",
                        "headline": "All-Day Battery",
                        "description": "Shop now",
                        "call_to_action": "SHOP_NOW",
                        "image_prompt": "earbuds on charging case",
                    }
                ]
            ),
            json.dumps(
                {
                    "headline": "Never Charge Twice a Day Again",
                    "subheadline": "40 hours of premium sound in one charge.",
                    "benefits": ["40h battery", "ANC", "Waterproof"],
                    "cta_text": "Get Yours",
                }
            ),
        ]
    )

    pipeline = Pipeline(
        settings=settings,
        llm=llm,
        ad_library=FakeAdLibrary(competitor_ads),
        ads_client=FacebookAdsClient(settings),
    )

    result = pipeline.run(product, dry_run=True)

    assert len(result.competitor_ads) == 1
    assert result.insights.recommended_angle == "emphasize battery life over price"
    assert len(result.creatives) == 1
    assert result.creatives[0].headline == "All-Day Battery"
    assert len(result.images) == 1
    assert result.landing_page is not None
    assert result.landing_page.headline == "Never Charge Twice a Day Again"
    assert result.campaign is not None
    assert result.campaign.dry_run is True
    assert result.campaign.status == "PAUSED"
    assert len(llm.calls) == 3


def test_pipeline_continues_when_ad_library_unavailable(settings, product):
    class BrokenAdLibrary:
        def search_competitor_ads(self, query, limit=20):
            from fbadsagent.facebook.ad_library import AdLibraryError

            raise AdLibraryError("no token")

    llm = FakeLLM(
        response=json.dumps(
            {"headline": "H", "subheadline": "S", "benefits": ["b"], "cta_text": "Go"}
        )
    )

    pipeline = Pipeline(
        settings=settings,
        llm=llm,
        ad_library=BrokenAdLibrary(),
        ads_client=FacebookAdsClient(settings),
    )

    result = pipeline.run(product, dry_run=True)

    assert result.competitor_ads == []
    assert result.insights.raw_ads_analyzed == 0
    assert result.campaign.dry_run is True
