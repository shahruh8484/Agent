import io
import json

import pytest
from PIL import Image

from amzagent.agent.runner import Deps, redraw_campaign, run_cycle
from amzagent.push.ai_creatives import (
    IMAGE_RULES,
    CreativeError,
    cut_push_images,
    describe_scenes,
    make_ai_creatives,
)
from tests.conftest import FakeCatalog, FakeLLM, make_product


def png(w=1536, h=1024, color=(200, 120, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


class SceneLLM(FakeLLM):
    def generate(self, system, prompt, max_tokens=2048):
        if "photo scenes" in prompt:
            return json.dumps(["A glass of juice on a sunny counter.",
                               "Hands pouring juice at breakfast.", "extra"])
        return super().generate(system, prompt, max_tokens)


class FakePainter:
    def __init__(self, fail_first=False, fail_all=False):
        self.prompts, self.fail_first, self.fail_all = [], fail_first, fail_all

    def draw(self, prompt):
        self.prompts.append(prompt)
        if self.fail_all or (self.fail_first and len(self.prompts) == 1):
            raise CreativeError("rate limited")
        return png()


def _copy():
    from amzagent.models import ProductCopy
    return ProductCopy(summary="s", push_title="Juice without the mess", push_text="b",
                       product_type="juicer")


def test_cut_sizes_and_crop_non_3x2(tmp_path):
    icon, image = cut_push_images(png(1024, 1024), tmp_path, "x")
    assert Image.open(image).size == (492, 328)
    assert Image.open(icon).size == (192, 192)


def test_scenes_are_capped(tmp_path):
    assert len(describe_scenes(SceneLLM(), make_product("A"), _copy(), 2)) == 2


def test_variants_survive_one_failure(tmp_path):
    painter = FakePainter(fail_first=True)
    files = make_ai_creatives(SceneLLM(), painter, make_product("A"), _copy(), tmp_path, "A-c1", 2)
    assert files == [("A-c1-v2-icon.png", "A-c1-v2-image.png")]


def test_all_failing_raises(tmp_path):
    with pytest.raises(CreativeError):
        make_ai_creatives(SceneLLM(), FakePainter(fail_all=True), make_product("A"), _copy(),
                          tmp_path, "A-c1", 2)


def _deps(settings, store, painter):
    return Deps(settings=settings, store=store, llm=SceneLLM(), painter=painter,
                catalog=FakeCatalog([make_product(f"A{i}", reviews=1000 * (i + 1))
                                     for i in range(3)]))


def test_campaigns_get_ai_variants(settings, store):
    store.add_niche("juicers")
    painter = FakePainter()
    run_cycle(_deps(settings, store, painter))
    c = store.list_campaigns()[0]
    creatives = json.loads(c["payload"])["creatives"]
    assert len(creatives) == 2
    assert creatives[0]["image"].startswith("https://example.com/media/juicers/")
    assert creatives[0]["image"].endswith("-v1-image.png")
    assert all(IMAGE_RULES in p for p in painter.prompts)  # no-text/no-logo rules always sent


def test_falls_back_to_simple_images(settings, store):
    store.add_niche("juicers")
    deps = _deps(settings, store, FakePainter(fail_all=True))
    run_cycle(deps)
    creatives = json.loads(store.list_campaigns()[0]["payload"])["creatives"]
    assert len(creatives) == 1 and creatives[0]["image"].endswith("-image.png")
    assert "-v1-" not in creatives[0]["image"]
    assert any("using simple ones" in line for line in deps.log)


def test_redraw_dry_run_campaign(settings, store):
    store.add_niche("juicers")
    run_cycle(_deps(settings, store, None))  # simple images first
    c = store.list_campaigns()[0]
    assert redraw_campaign(_deps(settings, store, FakePainter()), c["id"])
    creatives = json.loads(store.get_campaign(c["id"])["payload"])["creatives"]
    assert len(creatives) == 2 and "-v1-" in creatives[0]["image"]
