from pathlib import Path

from fbadsagent.creatives.image_generator import generate_images
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
