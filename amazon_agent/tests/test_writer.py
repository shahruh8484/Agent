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
