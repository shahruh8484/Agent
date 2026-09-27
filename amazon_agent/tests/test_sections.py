import json

import bcrypt
from fastapi.testclient import TestClient

from amzagent.agent.runner import Deps, run_cycle, update_site_plan
from amzagent.content.sections import OTHER, group_products
from amzagent.models import ProductCopy
from amzagent.web.app import create_app
from tests.conftest import FakeCatalog, FakeLLM, make_product


def _copy():
    return ProductCopy(summary="s", push_title="t", push_text="x", product_type="thing")


class OneReply:
    def __init__(self, reply):
        self.reply = reply

    def generate(self, system, prompt, max_tokens=0):
        return json.dumps(self.reply)


def test_grouping_keeps_order_drops_tiny_groups_and_collects_the_rest():
    items = [(make_product(a), _copy()) for a in ("A1", "A2", "A3", "A4", "A5", "A6")]
    reply = [
        {"name": "Chargers", "asins": ["A5", "A3"]},          # sorted by rank: A3, A5
        {"name": "Prime Gadgets", "asins": ["A2", "A6", "A3"]},  # A3 taken; mark stripped
        {"name": "Lonely", "asins": ["A4"]},                  # < 2 products
        {"name": "Ghosts", "asins": ["ZZZ", "YYY"]},          # unknown ASINs
    ]
    sections = group_products(OneReply(reply), items, "English")
    assert [(s.name, s.asins) for s in sections] == [
        ("Gadgets", ["A2", "A6"]), ("Chargers", ["A3", "A5"]), (OTHER, ["A1", "A4"])]
    assert len({s.slug for s in sections}) == 3


def _site(settings, store, n=6):
    niche = store.add_niche("earbuds")
    run_cycle(Deps(settings=settings, store=store, llm=FakeLLM(),
                   catalog=FakeCatalog([make_product(f"A{i}", reviews=1000 * (i + 1))
                                        for i in range(n)])))
    return niche


def test_cycle_builds_sections_and_guides_once(settings, store):
    niche = _site(settings, store)
    plan = store.get_site_plan(niche.id)
    assert [s.name for s in plan.sections] == ["Earbuds", "Chargers"]
    assert plan.sections[0].picks and plan.sections[0].guide_title
    llm = FakeLLM()
    update_site_plan(Deps(settings=settings, store=store, llm=llm), niche)
    assert llm.prompts == []  # same products: not rebuilt


def test_site_pages_show_sections_and_guides(settings, store):
    niche = _site(settings, store)
    plan = store.get_site_plan(niche.id)
    first = plan.sections[0]
    settings.admin_password_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    client = TestClient(create_app(settings, store, start_loop=False))

    home = client.get(f"/s/{niche.slug}/").text
    assert 'href="#earbuds"' in home and f"/s/{niche.slug}/c/{first.slug}" in home
    guide = client.get(f"/s/{niche.slug}/c/{first.slug}").text
    assert "Best Earbuds: Our Picks Compared" in guide and "How to choose" in guide
    assert "Best for commuting" in guide and f"/go/{niche.slug}/{first.asins[0]}" in guide
    assert client.get(f"/s/{niche.slug}/c/nope").status_code == 404
    product = client.get(f"/s/{niche.slug}/p/{first.asins[0]}").text
    assert f'/s/{niche.slug}/c/{first.slug}">Earbuds</a>' in product  # breadcrumb
    assert f"/s/{niche.slug}/c/{first.slug}" in client.get("/sitemap.xml").text
    assert f"/s/{niche.slug}/c/{first.slug}" in client.get("/").text  # hub links the guides


def test_new_products_show_under_more_picks_until_the_plan_is_rebuilt(settings, store):
    niche = _site(settings, store)
    plan = store.get_site_plan(niche.id)
    plan.sections = plan.sections[:1]  # pretend the rest was added later
    store.set_site_plan(niche.id, plan)
    client = TestClient(create_app(settings, store, start_loop=False))
    home = client.get(f"/s/{niche.slug}/").text
    assert 'href="#more-picks"' in home


def test_one_site_gives_the_whole_domain_its_name(settings, store):
    _site(settings, store)
    client = TestClient(create_app(settings, store, start_loop=False))
    assert "Sound Picks</a>" in client.get("/").text  # hub header uses the site's title
    assert "Sound Picks is a participant" in client.get("/privacy").text
    store.add_niche("chargers")  # a second site with a title of its own would differ
    from amzagent.models import SiteCopy
    store.set_site_copy(store.list_niches()[-1].id,
                        SiteCopy(site_title="Charge Hub", tagline="t", intro="i"))
    assert "Example</a>" in client.get("/").text  # back to the domain name


def test_guides_have_faq_date_and_amazon_labelled_buttons(settings, store):
    niche = _site(settings, store)
    first = store.get_site_plan(niche.id).sections[0]
    client = TestClient(create_app(settings, store, start_loop=False))
    guide = client.get(f"/s/{niche.slug}/c/{first.slug}").text
    assert "Frequently asked questions" in guide and "How loud are they?" in guide
    assert "Updated " in guide and 'href="/how-we-choose"' in guide
    assert ">Check price</a>" not in guide  # every Amazon button says so
    page = client.get("/how-we-choose").text
    assert "brand-funded commission" in page
    assert "cannot pay to be listed" not in client.get("/affiliate-disclosure").text


def test_an_old_plan_without_faq_is_rebuilt(settings, store):
    niche = _site(settings, store)
    plan = store.get_site_plan(niche.id)
    plan.version = 1
    store.set_site_plan(niche.id, plan)
    llm = FakeLLM()
    update_site_plan(Deps(settings=settings, store=store, llm=llm), niche)
    assert llm.prompts and store.get_site_plan(niche.id).version >= 2


def test_every_product_is_reachable_from_the_site_home(settings, store):
    settings.products_per_site = 24
    niche = _site(settings, store, n=24)  # 12 per section: more than the 8 shown
    plan = store.get_site_plan(niche.id)
    plan.sections[1].picks = []  # a section without a guide
    store.set_site_plan(niche.id, plan)
    client = TestClient(create_app(settings, store, start_loop=False))
    home = client.get(f"/s/{niche.slug}/").text
    for s in plan.sections:
        assert f'href="/s/{niche.slug}/c/{s.slug}"' in home
    plain = client.get(f"/s/{niche.slug}/c/{plan.sections[1].slug}").text
    for asin in plan.sections[1].asins:  # all of its products, no guide needed
        assert f"/s/{niche.slug}/p/{asin}" in plain
