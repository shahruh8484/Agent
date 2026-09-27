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


def test_hub_shows_guides_top_picks_categories_and_method(settings, store):
    niche = _site(settings, store)
    client = TestClient(create_app(settings, store, start_loop=False))
    hub = client.get("/").text
    assert "Buying guides" in hub and "picks compared" in hub
    assert "Top picks right now" in hub and f"/s/{niche.slug}/p/A5" in hub
    assert "Browse by category" in hub and 'href="/how-we-choose"' in hub


def test_pick_labels_follow_current_prices():
    from amzagent.models import GuidePick
    from amzagent.web.app import pick_labels

    def pick(asin, price):
        return (GuidePick(asin=asin), make_product(asin, price=price), None)

    labels = pick_labels([pick("TOP", 100), pick("TWO", 95), pick("CHEAP", 50),
                          pick("PRICEY", 180)])
    assert labels == {"TOP": "Our pick", "CHEAP": "Budget pick", "PRICEY": "Upgrade pick",
                      "TWO": "Runner-up"}
    assert pick_labels([pick("A", 100), pick("B", 90)]) == {"A": "Our pick", "B": "Runner-up"}


def test_guides_articles_deals_and_hub_blocks(settings, store):
    niche = _site(settings, store)
    plan = store.get_site_plan(niche.id)
    first = plan.sections[0]
    assert first.article and first.who_for and first.care_tips
    # one product on sale, with a fresh price
    p, c = store.get_product(niche.id, first.asins[0])
    p.savings_percent = 25
    store.update_product_data(niche.id, p)
    client = TestClient(create_app(settings, store, start_loop=False))

    guide = client.get(f"/s/{niche.slug}/c/{first.slug}").text
    for text in ("Our pick", "Who this is for", "How we picked", "Care and maintenance",
                 "don't physically test", f"/s/{niche.slug}/a/{first.article.slug}"):
        assert text in guide
    article = client.get(f"/s/{niche.slug}/a/{first.article.slug}").text
    assert "How to Choose Earbuds" in article and "Battery" in article
    assert client.get(f"/s/{niche.slug}/a/nope").status_code == 404
    assert "How to Choose Earbuds" in client.get("/advice").text
    deals = client.get("/deals").text
    assert "25% off" in deals and "as of" in deals
    hub = client.get("/").text
    assert "Deals right now" in hub and "Advice" in hub and 'href="/deals"' in hub
    assert f"/s/{niche.slug}/a/{first.article.slug}" in client.get("/sitemap.xml").text


def test_more_articles_head_to_heads_and_gift_lists(settings, store):
    niche = _site(settings, store)
    plan = store.get_site_plan(niche.id)
    first = plan.sections[0]
    assert {a.topic for a in first.all_articles} == {"choose", "mistakes", "care"}
    assert first.versus and first.versus.a == first.picks[0].asin
    assert any(c.title == "Gift Ideas Under $50" for c in plan.collections)  # $29.99 items
    assert any(c.title in ("Fall Home Refresh", "Holiday Gift Guide", "Back to School Essentials",
                           "Summer Travel and Outdoor Essentials", "New Year, Fresh Start",
                           "Valentine's Day Gift Ideas", "Spring Refresh Essentials",
                           "Mother's Day Gift Ideas", "Father's Day Gift Ideas")
               for c in plan.collections)
    client = TestClient(create_app(settings, store, start_loop=False))
    vs = client.get(f"/s/{niche.slug}/vs/{first.versus.slug}").text
    assert "Choose the" in vs and "Battery" in vs and "Check price on Amazon" in vs
    gift = next(c for c in plan.collections if c.max_price == 50)
    page = client.get(f"/s/{niche.slug}/g/{gift.slug}").text
    assert "Gift Ideas Under $50" in page and "A gift:" in page
    assert "Gift Ideas Under $50" in client.get("/gifts").text
    hub = client.get("/").text
    assert "Gift guides" in hub and "Head to head" in hub and 'href="/gifts"' in hub
    guide = client.get(f"/s/{niche.slug}/c/{first.slug}").text
    assert f"/s/{niche.slug}/vs/{first.versus.slug}" in guide
    for a in first.all_articles:
        assert client.get(f"/s/{niche.slug}/a/{a.slug}").status_code == 200
    assert f"/s/{niche.slug}/g/{gift.slug}" in client.get("/sitemap.xml").text


def test_budget_list_hides_items_that_got_pricier(settings, store):
    niche = _site(settings, store)
    plan = store.get_site_plan(niche.id)
    gift = next(c for c in plan.collections if c.max_price == 50)
    p, _ = store.get_product(niche.id, gift.items[0].asin)
    p.price = 80.0
    store.update_product_data(niche.id, p)
    client = TestClient(create_app(settings, store, start_loop=False))
    page = client.get(f"/s/{niche.slug}/g/{gift.slug}").text
    assert f"/s/{niche.slug}/p/{p.asin}" not in page


def test_small_product_changes_dont_rebuild_everything(settings, store):
    niche = _site(settings, store)
    plan = store.get_site_plan(niche.id)
    plan.asins = plan.asins + ["GONE1"]  # one product dropped since the build
    plan.signature = "old"
    plan.built_at = "2020-01-01T00:00:00+00:00"
    store.set_site_plan(niche.id, plan)
    llm = FakeLLM()
    deps = Deps(settings=settings, store=store, llm=llm)
    update_site_plan(deps, niche)
    assert llm.prompts == [] and any("small change" in line for line in deps.log)
