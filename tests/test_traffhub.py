import pytest

from fbadsagent.integrations.traffhub import TraffHubClient, TraffHubError


def test_client_requires_api_key():
    with pytest.raises(TraffHubError):
        TraffHubClient("")


def test_send_lead_posts_required_and_optional_fields(mocker):
    fake_response = mocker.Mock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"success": True, "transaction_id": "abc123"}
    post = mocker.patch("fbadsagent.integrations.traffhub.requests.post", return_value=fake_response)

    client = TraffHubClient("my-key")
    result = client.send_lead(
        phone="+10000000000",
        fio="Jane Doe",
        ip="1.2.3.4",
        campaign_hash="campaign-hash-1",
        price=49.99,
        sub1="fb-campaign-1",
    )

    assert result == {"success": True, "transaction_id": "abc123"}
    url, kwargs = post.call_args
    assert url[0] == "https://api.traff-hub.com/lead/add"
    payload = kwargs["json"]
    assert payload["api_key"] == "my-key"
    assert payload["phone"] == "+10000000000"
    assert payload["fio"] == "Jane Doe"
    assert payload["ip"] == "1.2.3.4"
    assert payload["hash"] == "campaign-hash-1"
    assert payload["price"] == 49.99
    assert payload["sub1"] == "fb-campaign-1"
    assert "sub2" not in payload


def test_list_conversions_only_sends_provided_filters(mocker):
    fake_response = mocker.Mock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"data": []}
    post = mocker.patch("fbadsagent.integrations.traffhub.requests.post", return_value=fake_response)

    client = TraffHubClient("my-key")
    client.list_conversions(status=11, date_from="2026-09-01", page=1, on_page=50)

    url, kwargs = post.call_args
    assert url[0] == "https://api.traff-hub.com/conversion/list"
    payload = kwargs["json"]
    assert payload == {
        "api_key": "my-key",
        "status": 11,
        "from": "2026-09-01",
        "page": 1,
        "onPage": 50,
    }


def test_raises_on_non_200(mocker):
    fake_response = mocker.Mock()
    fake_response.status_code = 403
    fake_response.text = "Invalid api_key"
    mocker.patch("fbadsagent.integrations.traffhub.requests.post", return_value=fake_response)

    client = TraffHubClient("bad-key")
    with pytest.raises(TraffHubError):
        client.list_conversions()


def test_raises_on_non_json_response(mocker):
    fake_response = mocker.Mock()
    fake_response.status_code = 200
    fake_response.json.side_effect = ValueError("no json")
    fake_response.text = "<html>not json</html>"
    mocker.patch("fbadsagent.integrations.traffhub.requests.post", return_value=fake_response)

    client = TraffHubClient("my-key")
    with pytest.raises(TraffHubError):
        client.list_conversions()


def test_custom_base_url_is_used(mocker):
    fake_response = mocker.Mock()
    fake_response.status_code = 200
    fake_response.json.return_value = {}
    post = mocker.patch("fbadsagent.integrations.traffhub.requests.post", return_value=fake_response)

    client = TraffHubClient("my-key", base_url="https://staging.traff-hub.com/")
    client.list_conversions()

    url, _ = post.call_args
    assert url[0] == "https://staging.traff-hub.com/conversion/list"
