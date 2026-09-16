import json
from pathlib import Path

from fbadsagent.landing.generator import generate_landing_page
from fbadsagent.models import CompetitorInsights
from tests.conftest import FakeLLM


def test_generate_landing_page_writes_html(settings, product):
    llm = FakeLLM(
        response=json.dumps(
            {
                "headline": "Hear Every Detail",
                "subheadline": "Premium noise-cancelling audio, all day long.",
                "benefits": ["40h battery", "Active noise cancelling", "IPX7 waterproof"],
                "cta_text": "Get Yours",
            }
        )
    )
    insights = CompetitorInsights(recommended_angle="battery life")

    page = generate_landing_page(llm, settings, product, insights)

    assert page.headline == "Hear Every Detail"
    assert "40h battery" in page.benefits
    html_path = Path(page.html_path)
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert "Hear Every Detail" in html
    assert "Get Yours" in html


def test_generate_landing_page_falls_back_on_bad_json(settings, product):
    llm = FakeLLM(response="nonsense")
    insights = CompetitorInsights()

    page = generate_landing_page(llm, settings, product, insights)

    assert page.headline == product.name
    assert Path(page.html_path).exists()
