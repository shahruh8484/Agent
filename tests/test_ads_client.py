from fbadsagent.facebook.ads_client import FacebookAdsClient
from fbadsagent.models import AdCreativeCopy


def test_create_campaign_dry_run_does_not_require_credentials(product):
    from fbadsagent.config import Settings

    client = FacebookAdsClient(Settings())  # no credentials set at all
    creative = AdCreativeCopy(
        variant_id="abc123",
        primary_text="Great earbuds",
        headline="Earbuds",
        description="Buy now",
    )

    plan = client.create_campaign(
        product, [creative], [], "https://example.com/product", dry_run=True
    )

    assert plan.dry_run is True
    assert plan.status == "PAUSED"
    assert plan.campaign_id is None
    types = [p["type"] for p in plan.payloads]
    assert types == ["campaign", "adset", "ad"]


def test_create_campaign_live_without_credentials_raises(settings, product):
    from fbadsagent.config import Settings

    client = FacebookAdsClient(Settings())  # missing FB credentials
    creative = AdCreativeCopy(
        variant_id="abc123", primary_text="x", headline="x", description="x"
    )

    try:
        client.create_campaign(
            product, [creative], [], "https://example.com", dry_run=False
        )
        assert False, "expected AdsClientError"
    except Exception as exc:
        assert "Missing Facebook credentials" in str(exc)


def test_build_campaign_payloads_uses_daily_budget_in_cents(settings, product):
    client = FacebookAdsClient(settings)
    payloads = client.build_campaign_payloads(product, [], [], "https://example.com")
    adset_payload = payloads[1]["payload"]
    assert adset_payload["daily_budget"] == int(product.daily_budget * 100)
