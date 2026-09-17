import base64
from pathlib import Path

import pytest

from fbadsagent.creatives.image_generator import ImageGenerationError, generate_images
from fbadsagent.models import AdCreativeCopy


def test_stub_provider_writes_placeholder_file(settings):
    creative = AdCreativeCopy(
        variant_id="v1", primary_text="x", headline="x", description="x",
        image_prompt="a pair of earbuds",
    )
    images = generate_images(settings, [creative])

    assert len(images) == 1
    assert images[0].provider == "stub"
    assert Path(images[0].path).exists()
    assert Path(images[0].path).stat().st_size > 0


def test_gemini_provider_requires_api_key(settings):
    settings.image_provider = "gemini"
    settings.gemini_api_key = ""
    creative = AdCreativeCopy(
        variant_id="v1", primary_text="x", headline="x", description="x",
        image_prompt="earbuds",
    )
    with pytest.raises(ImageGenerationError):
        generate_images(settings, [creative])


def test_gemini_provider_writes_image_from_bytes(settings, mocker):
    settings.image_provider = "gemini"
    settings.gemini_api_key = "test-key"

    fake_part = mocker.Mock()
    fake_part.inline_data.data = b"fake-png-bytes"
    fake_candidate = mocker.Mock()
    fake_candidate.content.parts = [fake_part]
    fake_response = mocker.Mock()
    fake_response.candidates = [fake_candidate]

    fake_client = mocker.Mock()
    fake_client.models.generate_content.return_value = fake_response
    mocker.patch("google.genai.Client", return_value=fake_client)

    creative = AdCreativeCopy(
        variant_id="v1", primary_text="x", headline="x", description="x",
        image_prompt="earbuds",
    )
    images = generate_images(settings, [creative])

    assert len(images) == 1
    assert images[0].provider == "gemini"
    assert Path(images[0].path).read_bytes() == b"fake-png-bytes"


def test_gemini_provider_writes_image_from_base64_string(settings, mocker):
    settings.image_provider = "gemini"
    settings.gemini_api_key = "test-key"

    fake_part = mocker.Mock()
    fake_part.inline_data.data = base64.b64encode(b"fake-png-bytes").decode()
    fake_candidate = mocker.Mock()
    fake_candidate.content.parts = [fake_part]
    fake_response = mocker.Mock()
    fake_response.candidates = [fake_candidate]

    fake_client = mocker.Mock()
    fake_client.models.generate_content.return_value = fake_response
    mocker.patch("google.genai.Client", return_value=fake_client)

    creative = AdCreativeCopy(
        variant_id="v1", primary_text="x", headline="x", description="x",
        image_prompt="earbuds",
    )
    images = generate_images(settings, [creative])

    assert Path(images[0].path).read_bytes() == b"fake-png-bytes"


def test_gemini_provider_raises_when_no_image_returned(settings, mocker):
    settings.image_provider = "gemini"
    settings.gemini_api_key = "test-key"

    fake_part = mocker.Mock()
    fake_part.inline_data = None
    fake_candidate = mocker.Mock()
    fake_candidate.content.parts = [fake_part]
    fake_response = mocker.Mock()
    fake_response.candidates = [fake_candidate]

    fake_client = mocker.Mock()
    fake_client.models.generate_content.return_value = fake_response
    mocker.patch("google.genai.Client", return_value=fake_client)

    creative = AdCreativeCopy(
        variant_id="v1", primary_text="x", headline="x", description="x",
        image_prompt="earbuds",
    )
    with pytest.raises(ImageGenerationError, match="no image data"):
        generate_images(settings, [creative])


def test_gemini_provider_wraps_api_errors(settings, mocker):
    settings.image_provider = "gemini"
    settings.gemini_api_key = "test-key"

    fake_client = mocker.Mock()
    fake_client.models.generate_content.side_effect = RuntimeError("quota exceeded")
    mocker.patch("google.genai.Client", return_value=fake_client)

    creative = AdCreativeCopy(
        variant_id="v1", primary_text="x", headline="x", description="x",
        image_prompt="earbuds",
    )
    with pytest.raises(ImageGenerationError, match="quota exceeded"):
        generate_images(settings, [creative])
