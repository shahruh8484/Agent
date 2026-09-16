from fbadsagent.models import AdCreativeCopy, GeneratedImage, SavedCreativeSet
from fbadsagent.web.creative_store import CreativeStore


def make_set(set_id="abc123") -> SavedCreativeSet:
    return SavedCreativeSet(
        id=set_id,
        product_name="Wireless Earbuds Pro",
        created_at="2026-09-16 12:00 UTC",
        creatives=[
            AdCreativeCopy(
                variant_id="v1", primary_text="Great sound.", headline="Earbuds", description="Buy now"
            )
        ],
        images=[GeneratedImage(variant_id="v1", path="/data/creatives/v1.png", prompt="earbuds", provider="stub")],
    )


def test_empty_store(tmp_path):
    store = CreativeStore(tmp_path / "creatives.json")
    assert store.list_sets() == []


def test_add_and_list_newest_first(tmp_path):
    store = CreativeStore(tmp_path / "creatives.json")
    store.add_set(make_set("first"))
    store.add_set(make_set("second"))

    ids = [s.id for s in store.list_sets()]
    assert ids == ["second", "first"]


def test_add_set_preserves_creatives_and_images(tmp_path):
    store = CreativeStore(tmp_path / "creatives.json")
    store.add_set(make_set())

    saved = store.list_sets()[0]
    assert saved.creatives[0].headline == "Earbuds"
    assert saved.images[0].path == "/data/creatives/v1.png"


def test_remove_set(tmp_path):
    store = CreativeStore(tmp_path / "creatives.json")
    store.add_set(make_set("a"))
    store.add_set(make_set("b"))
    store.remove_set("a")

    assert [s.id for s in store.list_sets()] == ["b"]
