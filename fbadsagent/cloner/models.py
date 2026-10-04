"""Data model for the standalone cloning service: one uploaded reference
project, plus whatever the pipeline generated from it."""
from __future__ import annotations

from pydantic import BaseModel, Field

from fbadsagent.models import AdCreativeCopy, GeneratedImage, QuizQuestion


class ClonerProject(BaseModel):
    """A reference bundle (creatives and/or a landing page) the agent
    clones the *type* of — not the exact content — into a new, original
    creative set and landing page, published at /lp/{slug}."""

    id: str
    name: str
    description: str = ""
    language: str = "Uzbek"
    # "static" (headline/subheadline/benefits/CTA) or "quiz" (an
    # interactive Q&A funnel) — see fbadsagent/landing/generator.py.
    landing_style: str = "static"
    # Where the published page's CTA button links to. No lead-capture/CPA
    # integration here — this service only generates and publishes.
    checkout_url: str = ""
    created_at: str

    reference_landing_urls: list[str] = Field(default_factory=list)
    reference_landing_screenshot_paths: list[str] = Field(default_factory=list)
    reference_creative_paths: list[str] = Field(default_factory=list)

    status: str = "pending"  # "pending" | "done" | "error"
    status_message: str = ""

    # Generated output, filled in once status == "done".
    slug: str = ""
    headline: str = ""
    subheadline: str = ""
    benefits: list[str] = Field(default_factory=list)
    cta_text: str = ""
    quiz_questions: list[QuizQuestion] = Field(default_factory=list)
    quiz_result_message: str = ""
    creative_copy: list[AdCreativeCopy] = Field(default_factory=list)
    creative_images: list[GeneratedImage] = Field(default_factory=list)
