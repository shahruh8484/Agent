import json

from fbadsagent.llm.copywriter import generate_ad_variants
from fbadsagent.llm.competitor_analysis import analyze_competitor_ads
from fbadsagent.models import CompetitorAd, CompetitorInsights
from tests.conftest import FakeLLM


def test_generate_ad_variants_parses_json_array(product):
    llm = FakeLLM(
        response=json.dumps(
            [
                {
                    "primary_text": "Feel the difference.",
                    "headline": "Premium Sound",
                    "description": "Shop today",
                    "call_to_action": "SHOP_NOW",
                    "image_prompt": "earbuds on a table",
                },
                {
                    "primary_text": "40 hours of battery life.",
                    "headline": "All-Day Battery",
                    "description": "Limited stock",
                    "call_to_action": "LEARN_MORE",
                    "image_prompt": "earbuds charging case",
                },
            ]
        )
    )
    insights = CompetitorInsights(recommended_angle="battery life")

    variants = generate_ad_variants(llm, product, insights, n=2)

    assert len(variants) == 2
    assert variants[0].headline == "Premium Sound"
    assert variants[1].call_to_action == "LEARN_MORE"
    assert len(llm.calls) == 1


def test_generate_ad_variants_falls_back_on_bad_json(product):
    llm = FakeLLM(response="not json at all")
    insights = CompetitorInsights()

    variants = generate_ad_variants(llm, product, insights, n=3)

    assert len(variants) == 1
    assert variants[0].headline == product.name


def test_analyze_competitor_ads_empty_list_skips_llm(product):
    llm = FakeLLM(response="{}")
    insights = analyze_competitor_ads(llm, product.name, [])
    assert insights.raw_ads_analyzed == 0
    assert llm.calls == []


def test_analyze_competitor_ads_parses_response(product):
    llm = FakeLLM(
        response=json.dumps(
            {
                "common_hooks": ["free shipping"],
                "common_offers": ["20% off"],
                "tone_observations": "urgent",
                "recommended_angle": "focus on comfort",
            }
        )
    )
    ads = [
        CompetitorAd(page_name="A", ad_creative_body="body", ad_creative_link_title="title")
    ]
    insights = analyze_competitor_ads(llm, product.name, ads)

    assert insights.raw_ads_analyzed == 1
    assert insights.common_hooks == ["free shipping"]
    assert insights.recommended_angle == "focus on comfort"
