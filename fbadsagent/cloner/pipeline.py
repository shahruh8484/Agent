"""Core cloning pipeline: given a project's reference creatives/landing
pages, infer the general style and offer type and generate a brand-new,
original creative set and landing page of the same type — then this
service publishes the result at /lp/{slug}. No lead-capture/CPA
integration and no Facebook campaign creation here; see
fbadsagent/web/agent_runner.py for the full ads-launch pipeline this one
is derived from.
"""
from __future__ import annotations

import logging
import uuid

from fbadsagent.config import Settings
from fbadsagent.cloner.models import ClonerProject
from fbadsagent.cloner.store import slugify
from fbadsagent.creatives.image_generator import ImageGenerationError, generate_images
from fbadsagent.landing.generator import generate_landing_copy, generate_quiz_landing_copy
from fbadsagent.landing.reference_fetcher import ReferenceFetchError, fetch_reference_text
from fbadsagent.llm.copywriter import generate_ad_variants
from fbadsagent.llm.provider import LLMError, get_llm_provider
from fbadsagent.models import CompetitorInsights, ProductInput, QuizQuestion

logger = logging.getLogger(__name__)

LANDING_SCREENSHOT_SYSTEM_PROMPT = (
    "You analyze a screenshot of a reference landing page. Describe its "
    "headline, subheadline, key benefits or bullet points, call-to-action "
    "text, and overall structure/tone in plain text, so a copywriter who "
    "cannot see the image can use it as a style reference."
)

CREATIVE_SYSTEM_PROMPT = (
    "You analyze one or more reference Facebook ad creative images. "
    "Describe their visual style, layout, imagery, color scheme, and the "
    "type of hook or angle they use in plain text, so a copywriter who "
    "cannot see the images can write new, original ad copy in the same "
    "style for a similar type of offer."
)


def _error(project: ClonerProject, message: str) -> ClonerProject:
    logger.info("Cloner project %s failed: %s", project.id, message)
    return project.model_copy(update={"status": "error", "status_message": message})


def run_cloner_project(project: ClonerProject, settings: Settings) -> ClonerProject:
    """Runs the full pipeline for one project and returns the updated
    record (status "done" with generated content, or "error" with a
    message) — never raises, so a failure is always a clear result."""
    try:
        llm = get_llm_provider(settings)
    except LLMError as exc:
        return _error(project, f"No LLM configured: {exc}")

    reference_texts: list[str] = []
    for url in project.reference_landing_urls:
        try:
            reference_texts.append(fetch_reference_text(url))
        except ReferenceFetchError as exc:
            logger.info("Reference URL skipped for %s: %s", project.name, exc)

    if project.reference_landing_screenshot_paths:
        try:
            reference_texts.append(
                llm.generate_with_images(
                    LANDING_SCREENSHOT_SYSTEM_PROMPT,
                    "Describe this reference landing page screenshot.",
                    project.reference_landing_screenshot_paths,
                )
            )
        except LLMError as exc:
            logger.info("Reference landing screenshots skipped for %s: %s", project.name, exc)

    if project.reference_creative_paths:
        try:
            reference_texts.append(
                llm.generate_with_images(
                    CREATIVE_SYSTEM_PROMPT,
                    "Describe these reference ad creative images.",
                    project.reference_creative_paths,
                )
            )
        except LLMError as exc:
            logger.info("Reference creatives skipped for %s: %s", project.name, exc)

    product_input = ProductInput(
        name=project.name,
        description=project.description or project.name,
        language=project.language,
    )
    insights = CompetitorInsights(
        recommended_angle=project.description
        or f"Match the style and structure of the provided reference material for {project.name}.",
        raw_ads_analyzed=0,
    )

    try:
        creatives = generate_ad_variants(
            llm, product_input, insights, n=3, reference_texts=reference_texts
        )
    except LLMError as exc:
        return _error(project, f"Ad copy generation failed: {exc}")

    try:
        images = generate_images(settings, creatives)
    except ImageGenerationError as exc:
        return _error(project, f"Image generation failed: {exc}")

    is_quiz = project.landing_style == "quiz"
    try:
        if is_quiz:
            copy = generate_quiz_landing_copy(llm, product_input, insights, reference_texts)
        else:
            copy = generate_landing_copy(llm, product_input, insights, reference_texts)
    except LLMError as exc:
        return _error(project, f"Landing page generation failed: {exc}")

    slug = f"{slugify(project.name)}-{uuid.uuid4().hex[:6]}"
    update = {
        "status": "done",
        "status_message": "Сгенерировано и опубликовано.",
        "slug": slug,
        "headline": copy["headline"],
        "subheadline": copy["subheadline"],
        "cta_text": copy["cta_text"],
        "creative_copy": creatives,
        "creative_images": images,
    }
    if is_quiz:
        # model_copy(update=...) sets raw values without validation, so
        # convert the generator's plain dicts into QuizQuestion ourselves.
        update["quiz_questions"] = [QuizQuestion(**q) for q in copy["quiz_questions"]]
        update["quiz_result_message"] = copy["quiz_result_message"]
    else:
        update["benefits"] = copy["benefits"]

    return project.model_copy(update=update)
