import pytest

from amzagent.content.llm import LLMError, parse_json
from amzagent.content.writer import write_product_copy, write_site_copy
from tests.conftest import FakeLLM, make_product


def test_parse_json_handles_fences_and_chatter():
    assert parse_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here: [1, 2] done') == [1, 2]
    with pytest.raises(LLMError):
        parse_json("no json here")


def test_product_copy_truncates_push_fields_and_ignores_unknown_asins():
    class LLM:
        def generate(self, system, prompt, max_tokens=2048):
            entry = '"summary": "s", "push_title": "%s", "push_text": "b"' % ("T" * 50)
            return "[" + ", ".join(
                '{"asin": "%s", %s}' % (a, entry) for a in ("A", "B", "ZZZ")
            ) + ', "garbage"]'

    copies = write_product_copy(LLM(), [make_product("A"), make_product("B")], "English")
    assert set(copies) == {"A", "B"}
    assert len(copies["A"].push_title) == 30


def test_site_copy():
    copy = write_site_copy(FakeLLM(), "earbuds", "English")
    assert copy.site_title == "Sound Picks"


def test_product_copy_is_written_in_batches():
    llm = FakeLLM()
    products = [make_product(f"P{i}") for i in range(20)]
    copies = write_product_copy(llm, products, "English")
    assert len(copies) == 20
    assert len(llm.prompts) == 3  # 8 + 8 + 4


def test_one_failed_batch_does_not_lose_the_others():
    class Flaky(FakeLLM):
        def generate(self, system, prompt, max_tokens=2048):
            if "asin: P0" in prompt:
                return "sorry, cannot help"
            return super().generate(system, prompt, max_tokens)

    copies = write_product_copy(Flaky(), [make_product(f"P{i}") for i in range(10)], "English")
    assert set(copies) == {"P8", "P9"}


def test_site_names_never_use_amazon_marks():
    import json

    from amzagent.content.writer import write_site_copy

    class Stubborn:
        def __init__(self, titles):
            self.titles = list(titles)

        def generate(self, system, prompt, max_tokens=0):
            title = self.titles.pop(0)
            tagline = "Prime deals daily" if "Prime" in title else "Deals worth a look"
            return json.dumps({"site_title": title, "tagline": tagline,
                               "intro": "We pick well-rated products."})

    fixed = write_site_copy(Stubborn(["Prime Bargain Picks", "Bargain Nest"]), "Top Deals",
                            "English")
    assert fixed.site_title == "Bargain Nest"  # asked again, took the clean one
    never = write_site_copy(Stubborn(["Prime Picks", "Prime Finds"]), "Top Deals", "English")
    assert never.site_title == "Top Deals Guide" and "prime" not in never.tagline.lower()


def test_an_existing_trademarked_site_name_is_replaced(settings, store):
    from amzagent.agent.runner import Deps, run_cycle
    from amzagent.models import SiteCopy
    from tests.conftest import FakeCatalog

    niche = store.add_niche("earbuds")
    store.set_site_copy(niche.id, SiteCopy(site_title="Prime Bargain Picks", tagline="x",
                                           intro="y"))
    run_cycle(Deps(settings=settings, store=store, catalog=FakeCatalog([make_product("A1")]),
                   llm=FakeLLM()))
    assert store.get_site_copy(niche.id).site_title == "Sound Picks"
