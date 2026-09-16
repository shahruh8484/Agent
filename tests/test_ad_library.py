import pytest

from fbadsagent.facebook.ad_library import AdLibraryClient, AdLibraryError
from fbadsagent.config import Settings


def test_search_requires_access_token():
    client = AdLibraryClient(Settings(fb_access_token=""))
    with pytest.raises(AdLibraryError):
        client.search_competitor_ads("shoes")


def test_search_parses_ads(mocker, settings):
    fake_response = mocker.Mock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "data": [
            {
                "page_name": "CompetitorCo",
                "ad_creative_bodies": ["Buy our shoes now, 50% off!"],
                "ad_creative_link_titles": ["Best Shoes 2026"],
                "ad_snapshot_url": "https://facebook.com/ads/snapshot/1",
                "ad_delivery_start_time": "2026-01-01",
                "publisher_platforms": ["facebook", "instagram"],
            }
        ]
    }
    mocker.patch("fbadsagent.facebook.ad_library.requests.get", return_value=fake_response)

    client = AdLibraryClient(settings)
    ads = client.search_competitor_ads("shoes")

    assert len(ads) == 1
    assert ads[0].page_name == "CompetitorCo"
    assert "50% off" in ads[0].ad_creative_body
    assert ads[0].publisher_platforms == ["facebook", "instagram"]


def test_search_raises_on_error_status(mocker, settings):
    fake_response = mocker.Mock()
    fake_response.status_code = 400
    fake_response.text = "Bad request"
    mocker.patch("fbadsagent.facebook.ad_library.requests.get", return_value=fake_response)

    client = AdLibraryClient(settings)
    with pytest.raises(AdLibraryError):
        client.search_competitor_ads("shoes")
