import pytest

from fbadsagent.landing.reference_fetcher import (
    MAX_REFERENCE_CHARS,
    ReferenceFetchError,
    fetch_reference_text,
)


def test_fetch_reference_text_strips_tags_and_scripts(mocker):
    fake_response = mocker.Mock()
    fake_response.status_code = 200
    fake_response.text = (
        "<html><head><style>.x{color:red}</style></head>"
        "<body><h1>Never Miss a Beat</h1>"
        "<script>console.log('tracked')</script>"
        "<p>40 hour battery life</p></body></html>"
    )
    mocker.patch(
        "fbadsagent.landing.reference_fetcher.requests.get", return_value=fake_response
    )

    text = fetch_reference_text("https://competitor.com/offer")

    assert "Never Miss a Beat" in text
    assert "40 hour battery life" in text
    assert "console.log" not in text
    assert "color:red" not in text


def test_fetch_reference_text_truncates_long_pages(mocker):
    fake_response = mocker.Mock()
    fake_response.status_code = 200
    fake_response.text = "<p>" + ("word " * 5000) + "</p>"
    mocker.patch(
        "fbadsagent.landing.reference_fetcher.requests.get", return_value=fake_response
    )

    text = fetch_reference_text("https://competitor.com/offer")

    assert len(text) <= MAX_REFERENCE_CHARS


def test_fetch_reference_text_raises_on_error_status(mocker):
    fake_response = mocker.Mock()
    fake_response.status_code = 404
    mocker.patch(
        "fbadsagent.landing.reference_fetcher.requests.get", return_value=fake_response
    )

    with pytest.raises(ReferenceFetchError):
        fetch_reference_text("https://competitor.com/missing")


def test_fetch_reference_text_raises_on_connection_error(mocker):
    import requests

    mocker.patch(
        "fbadsagent.landing.reference_fetcher.requests.get",
        side_effect=requests.ConnectionError("refused"),
    )

    with pytest.raises(ReferenceFetchError):
        fetch_reference_text("https://unreachable.example")
