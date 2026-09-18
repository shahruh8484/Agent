import json
from pathlib import Path

from fbadsagent.landing.generator import (
    QUIZ_SYSTEM_PROMPT,
    generate_landing_copy,
    generate_landing_page,
    generate_quiz_landing_copy,
)
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


def test_generate_landing_copy_includes_reference_texts_in_prompt(product):
    llm = FakeLLM(
        response=json.dumps(
            {
                "headline": "Hear Every Detail",
                "subheadline": "Premium audio.",
                "benefits": ["40h battery"],
                "cta_text": "Get Yours",
            }
        )
    )
    insights = CompetitorInsights(recommended_angle="battery life")

    generate_landing_copy(
        llm, product, insights, reference_texts=["Competitor headline: Never miss a beat"]
    )

    system, prompt = llm.calls[0]
    assert "Competitor headline: Never miss a beat" in prompt
    assert "do not copy their wording" in prompt


def test_generate_landing_copy_without_reference_texts_omits_section(product):
    llm = FakeLLM(
        response=json.dumps(
            {"headline": "x", "subheadline": "x", "benefits": ["x"], "cta_text": "x"}
        )
    )
    insights = CompetitorInsights()

    generate_landing_copy(llm, product, insights)

    _, prompt = llm.calls[0]
    assert "Competitor landing page" not in prompt


def test_generate_landing_copy_includes_language_in_prompt(product):
    llm = FakeLLM(
        response=json.dumps(
            {"headline": "x", "subheadline": "x", "benefits": ["x"], "cta_text": "x"}
        )
    )
    insights = CompetitorInsights()
    product.language = "Uzbek"

    generate_landing_copy(llm, product, insights)

    _, prompt = llm.calls[0]
    assert "Write all copy in Uzbek." in prompt


def test_generate_quiz_landing_copy_includes_language_in_prompt(product):
    llm = FakeLLM(
        response=json.dumps(
            {
                "headline": "x",
                "subheadline": "x",
                "quiz_questions": [],
                "quiz_result_message": "x",
                "cta_text": "x",
            }
        )
    )
    insights = CompetitorInsights()
    product.language = "Uzbek"

    generate_quiz_landing_copy(llm, product, insights)

    _, prompt = llm.calls[0]
    assert "Write all copy in Uzbek." in prompt


def test_generate_quiz_landing_copy_parses_questions(product):
    llm = FakeLLM(
        response=json.dumps(
            {
                "headline": "A Few Quick Questions",
                "subheadline": "Find out what fits your routine.",
                "quiz_questions": [
                    {"text": "How often do you listen to music?", "options": ["Daily", "Sometimes"]},
                    {"text": "Do you exercise?", "options": ["Yes", "No"]},
                ],
                "quiz_result_message": "Based on your answers, these earbuds fit your routine.",
                "cta_text": "See My Recommendation",
            }
        )
    )
    insights = CompetitorInsights(recommended_angle="convenience")

    copy = generate_quiz_landing_copy(llm, product, insights)

    assert copy["headline"] == "A Few Quick Questions"
    assert len(copy["quiz_questions"]) == 2
    assert copy["quiz_questions"][0]["text"] == "How often do you listen to music?"
    assert copy["quiz_result_message"] == "Based on your answers, these earbuds fit your routine."
    assert copy["cta_text"] == "See My Recommendation"

    system, _ = llm.calls[0]
    assert system == QUIZ_SYSTEM_PROMPT
    assert "Never invent a doctor" in system
    assert "Never invent statistics" in system
    assert "Never claim the product cures" in system


def test_generate_quiz_landing_copy_falls_back_on_bad_json(product):
    llm = FakeLLM(response="nonsense")
    insights = CompetitorInsights()

    copy = generate_quiz_landing_copy(llm, product, insights)

    assert copy["headline"] == product.name
    assert copy["quiz_questions"] == []
    assert copy["quiz_result_message"] == product.description


def test_generate_quiz_landing_copy_includes_reference_texts_with_honesty_caveat(product):
    llm = FakeLLM(
        response=json.dumps(
            {
                "headline": "x",
                "subheadline": "x",
                "quiz_questions": [],
                "quiz_result_message": "x",
                "cta_text": "x",
            }
        )
    )
    insights = CompetitorInsights()

    generate_quiz_landing_copy(
        llm, product, insights, reference_texts=["Fake doctor says X cures everything"]
    )

    _, prompt = llm.calls[0]
    assert "Fake doctor says X cures everything" in prompt
    assert "not copy any claims, statistics, personas" in prompt
