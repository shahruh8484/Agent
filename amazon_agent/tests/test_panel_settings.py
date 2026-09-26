import bcrypt
from fastapi.testclient import TestClient

from amzagent.agent.runner import Deps, build_deps, run_cycle
from amzagent.panel_settings import effective, parse_form
from amzagent.store import ACTIVE, STOPPED
from amzagent.web.app import create_app
from tests.conftest import FakeCatalog, FakeLLM, FakePush, make_product


def test_parse_form_validates():
    values, errors = parse_form({"campaign_daily_budget": "5", "push_countries": "US, CA",
                                 "max_daily_spend": "30"})
    assert any("минимум 10" in e for e in errors)
    assert values["push_countries"] == "us,ca" and values["push_live"] is False
    _, errors = parse_form({"campaign_daily_budget": "20", "max_daily_spend": "15"})
    assert any("лимит меньше" in e for e in errors)
    _, errors = parse_form({"push_countries": "usa"})
    assert errors


def test_overrides_reach_the_agent(settings, store):
    values, _ = parse_form({"push_live": "1", "campaign_daily_budget": "12",
                            "max_daily_spend": "40", "campaigns_per_site": "1"})
    from amzagent.panel_settings import save_overrides
    save_overrides(store, values)
    eff = effective(settings, store)
    assert eff.push_live and eff.campaign_daily_budget == 12 and eff.campaigns_per_site == 1
    assert build_deps(settings, store).settings.max_daily_spend == 40
    assert settings.campaign_daily_budget == 10  # .env value itself untouched


def _client(settings, store):
    settings.admin_password_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    client = TestClient(create_app(settings, store, start_loop=False))
    client.post("/login", data={"username": "admin", "password": "pw"})
    return client


def test_settings_form_saves_and_lowering_cap_stops_newest(settings, store):
    settings.push_live = True
    store.add_niche("earbuds")
    push = FakePush()
    run_cycle(Deps(settings=settings, store=store, catalog=FakeCatalog(
        [make_product(f"A{i}", reviews=1000 * (i + 1)) for i in range(3)]), llm=FakeLLM(),
        push=push))
    assert store.running_daily_budget() == 20

    client = _client(settings, store)
    page = client.post("/settings", data={
        "push_live": "1", "campaign_daily_budget": "10", "max_daily_spend": "10",
        "campaigns_per_site": "2", "push_bid_cpc": "0.03", "push_countries": "us",
        "push_creatives": "simple", "kill_min_spend": "5", "max_cost_per_amazon_click": "0.4",
        "zone_min_spend": "1", "auto_niches": "0"})
    assert "Лишние кампании остановлены" in page.text
    assert store.running_daily_budget() == 10
    assert {c["status"] for c in store.list_campaigns()} == {ACTIVE, STOPPED}
    assert 'value="10.00"' in page.text  # form shows the saved value


def test_budget_change_applies_to_running_campaigns(settings, store):
    store.add_niche("earbuds")
    run_cycle(Deps(settings=settings, store=store, catalog=FakeCatalog(
        [make_product(f"A{i}") for i in range(3)]), llm=FakeLLM()))
    client = _client(settings, store)
    page = client.post("/settings", data={"campaign_daily_budget": "15", "max_daily_spend": "60"})
    assert "применён к 2 кампаниям" in page.text
    assert {c["daily_budget"] for c in store.list_campaigns()} == {15}


def test_bad_values_are_rejected(settings, store):
    client = _client(settings, store)
    page = client.post("/settings", data={"campaign_daily_budget": "3"})
    assert "Настройки не сохранены" in page.text
    assert effective(settings, store).campaign_daily_budget == 10
