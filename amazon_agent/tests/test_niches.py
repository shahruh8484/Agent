import json

from amzagent.agent.runner import Deps, run_cycle
from amzagent.selection.niches import discover_niches, propose_niches
from tests.conftest import FakeLLM, make_product


class IdeasLLM(FakeLLM):
    """Proposes fixed niche ideas; otherwise behaves like FakeLLM."""

    def generate(self, system, prompt, max_tokens=2048):
        if "Propose" in prompt:
            return json.dumps([
                {"keywords": "dog grooming tools", "search_index": "PetSupplies"},
                {"keywords": "cheap stickers", "search_index": "OfficeProducts"},
                {"keywords": "rare widgets", "search_index": "NotAnIndex"},
                {"keywords": "Wireless Earbuds", "search_index": "Electronics"},  # exists
            ])
        return super().generate(system, prompt, max_tokens)


class NicheCatalog:
    """dog grooming: 8 good $35 products; stickers: 8 good $4 products;
    rare widgets: only 2 products."""

    def search(self, keywords, search_index="All", max_price=None, page=1):
        if page > 1:
            return []
        if keywords == "dog grooming tools":
            return [make_product(f"D{i}", price=35.0) for i in range(8)]
        if keywords == "cheap stickers":
            return [make_product(f"S{i}", price=4.0) for i in range(8)]
        return [make_product(f"R{i}") for i in range(2)]

    def get(self, asins):
        return []


def test_propose_filters_existing_and_unknown_indexes():
    ideas = propose_niches(IdeasLLM(), 4, ["wireless earbuds"], "US")
    assert [c.keywords for c in ideas] == ["dog grooming tools", "cheap stickers", "rare widgets"]
    assert ideas[2].search_index == "All"


def test_amazon_data_decides_the_winner():
    log = []
    winners = discover_niches(IdeasLLM(), NicheCatalog(), 2, ["wireless earbuds"], "US",
                              4.0, 100, log.append)
    # Widgets fail the product-count check; cheap stickers rank below dog tools.
    assert [c.keywords for c in winners] == ["dog grooming tools", "cheap stickers"]
    assert winners[0].total > winners[1].total
    assert any("rare widgets" in line and "rejected" in line for line in log)


def test_cycle_discovers_and_builds_sites(settings, store):
    settings.auto_niches = 1
    deps = Deps(settings=settings, store=store, catalog=NicheCatalog(), llm=IdeasLLM())
    run_cycle(deps)
    niches = store.list_niches()
    assert [n.keywords for n in niches] == ["dog grooming tools"]
    assert niches[0].search_index == "PetSupplies"
    assert len(store.list_products(niches[0].id)) == 5  # products_per_site
    assert store.get_site_copy(niches[0].id) is not None

    # Target already met: the next cycle adds nothing.
    run_cycle(Deps(settings=settings, store=store, catalog=NicheCatalog(), llm=IdeasLLM()))
    assert len(store.list_niches()) == 1


def test_manual_discover_request(settings, store):
    deps = Deps(settings=settings, store=store, catalog=NicheCatalog(), llm=IdeasLLM())
    run_cycle(deps, discover=2)
    assert len(store.list_niches()) == 2


def test_discovery_needs_amazon_and_llm(settings, store):
    deps = Deps(settings=settings, store=store, catalog=None, llm=IdeasLLM())
    run_cycle(deps, discover=2)
    assert store.list_niches() == []
    assert any("needs both" in line for line in deps.log)


def test_discovery_paused_while_amazon_refuses(settings, store):
    from tests.test_creator_connections import DeniedCatalog

    llm = IdeasLLM()
    deps = Deps(settings=settings, store=store, catalog=DeniedCatalog(), llm=llm)
    run_cycle(deps, discover=3)
    assert store.list_niches() == []
    assert llm.prompts == []  # no LLM money spent on ideas
    assert any("paused until the Amazon API answers" in line for line in deps.log)
