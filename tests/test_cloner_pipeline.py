import json

from fbadsagent.cloner.models import ClonerProject
from fbadsagent.cloner.pipeline import run_cloner_project
from fbadsagent.landing.reference_fetcher import ReferenceFetchError
from fbadsagent.llm.provider import LLMError
from tests.test_agent_runner import ScriptedLLM


def make_project(**overrides) -> ClonerProject:
    defaults = dict(
        id="c1",
        name="Glycofort",
        description="A dietary supplement for blood sugar support",
        created_at="2026-01-01 00:00 UTC",
    )
    defaults.update(overrides)
    return ClonerProject(**defaults)


AD_COPY_JSON = json.dumps(
    [
        {
            "primary_text": "x",
            "headline": "x",
            "description": "x",
            "call_to_action": "SHOP_NOW",
            "image_prompt": "x",
        }
    ]
)

STATIC_LANDING_JSON = json.dumps(
    {"headline": "x", "subheadline": "x", "benefits": ["x"], "cta_text": "x"}
)


def test_run_cloner_project_success(settings, mocker):
    scripted_llm = ScriptedLLM([AD_COPY_JSON, STATIC_LANDING_JSON])
    mocker.patch("fbadsagent.cloner.pipeline.get_llm_provider", return_value=scripted_llm)

    project = make_project()
    result = run_cloner_project(project, settings)

    assert result.status == "done"
    assert result.slug.startswith("glycofort-")
    assert result.headline == "x"
    assert result.benefits == ["x"]
    assert len(result.creative_copy) == 1
    assert len(result.creative_images) == 1


def test_run_cloner_project_no_llm_configured(settings, mocker):
    mocker.patch(
        "fbadsagent.cloner.pipeline.get_llm_provider", side_effect=LLMError("no api key")
    )
    project = make_project()

    result = run_cloner_project(project, settings)

    assert result.status == "error"
    assert "No LLM configured" in result.status_message


def test_run_cloner_project_quiz_style(settings, mocker):
    quiz_json = json.dumps(
        {
            "headline": "A Few Quick Questions",
            "subheadline": "Find your fit.",
            "quiz_questions": [{"text": "Do you exercise often?", "options": ["Yes", "No"]}],
            "quiz_result_message": "This matches your routine.",
            "cta_text": "See My Recommendation",
        }
    )
    scripted_llm = ScriptedLLM([AD_COPY_JSON, quiz_json])
    mocker.patch("fbadsagent.cloner.pipeline.get_llm_provider", return_value=scripted_llm)

    project = make_project(landing_style="quiz")
    result = run_cloner_project(project, settings)

    assert result.status == "done"
    assert len(result.quiz_questions) == 1
    assert result.quiz_questions[0].text == "Do you exercise often?"
    assert result.quiz_result_message == "This matches your routine."


def test_run_cloner_project_uses_reference_urls(settings, mocker):
    scripted_llm = ScriptedLLM([AD_COPY_JSON, STATIC_LANDING_JSON])
    mocker.patch("fbadsagent.cloner.pipeline.get_llm_provider", return_value=scripted_llm)
    mock_fetch = mocker.patch(
        "fbadsagent.cloner.pipeline.fetch_reference_text",
        return_value="Reference headline: Lower Your Sugar Naturally",
    )

    project = make_project(reference_landing_urls=["https://competitor.com/offer"])
    result = run_cloner_project(project, settings)

    assert result.status == "done"
    mock_fetch.assert_called_once_with("https://competitor.com/offer")
    ad_copy_prompt = scripted_llm.calls[0][1]
    assert "Reference headline: Lower Your Sugar Naturally" in ad_copy_prompt


def test_run_cloner_project_uses_reference_images(settings, tmp_path, mocker):
    scripted_llm = ScriptedLLM(
        [AD_COPY_JSON, STATIC_LANDING_JSON],
        image_response="Bold orange CTA button, lifestyle photography.",
    )
    mocker.patch("fbadsagent.cloner.pipeline.get_llm_provider", return_value=scripted_llm)

    creative_path = tmp_path / "creative.png"
    creative_path.write_bytes(b"fake-png-bytes")
    project = make_project(reference_creative_paths=[str(creative_path)])

    result = run_cloner_project(project, settings)

    assert result.status == "done"
    assert len(scripted_llm.image_calls) == 1
    _, _, image_paths = scripted_llm.image_calls[0]
    assert image_paths == [str(creative_path)]
    ad_copy_prompt = scripted_llm.calls[0][1]
    assert "Bold orange CTA button, lifestyle photography." in ad_copy_prompt


def test_run_cloner_project_tolerates_reference_fetch_failure(settings, mocker):
    scripted_llm = ScriptedLLM([AD_COPY_JSON, STATIC_LANDING_JSON])
    mocker.patch("fbadsagent.cloner.pipeline.get_llm_provider", return_value=scripted_llm)
    mocker.patch(
        "fbadsagent.cloner.pipeline.fetch_reference_text",
        side_effect=ReferenceFetchError("timed out"),
    )

    project = make_project(reference_landing_urls=["https://unreachable.example"])
    result = run_cloner_project(project, settings)

    assert result.status == "done"
