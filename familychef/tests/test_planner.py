import math

import pytest

from familychef.catalog import get_catalog
from familychef.models import PlanRequest
from familychef.planner import DIET_FORBIDS, PlanError, build_plan

BUDGETS = {"RU": 6000, "BY": 200, "KZ": 30000, "UZ": 900000, "DE": 70, "US": 110}


def plan(**kw):
    kw.setdefault("region", "RU")
    kw.setdefault("budget", BUDGETS[kw["region"]])
    return build_plan(get_catalog(), PlanRequest(**kw))


def all_meals(p):
    return [m for d in p["days"] for m in d["meals"]]


@pytest.mark.parametrize("region", sorted(BUDGETS))
def test_full_week_in_local_currency(region):
    p = plan(region=region, adults=2, children=1)
    assert p["region"]["currency"] == get_catalog().regions[region].currency
    assert len(p["days"]) == 7 and len(all_meals(p)) == 21
    lines = [i for c in p["shopping_list"] for i in c["items"]]
    assert p["totals"]["cost"] == pytest.approx(sum(i["cost"] for i in lines), rel=1e-6, abs=1)
    assert p["totals"]["within_budget"]
    for i in lines:  # bought packs cover what is needed
        assert i["packs"] * i["pack_size"] >= i["to_buy"] - 5


def test_no_dish_twice_a_day_and_variety_caps():
    p = plan(adults=2)
    for d in p["days"]:
        ids = [m["recipe_id"] for m in d["meals"]]
        assert len(ids) == len(set(ids))
    counts = {}
    for m in all_meals(p):
        counts[m["recipe_id"]] = counts.get(m["recipe_id"], 0) + 1
    assert max(counts.values()) <= 3


@pytest.mark.parametrize("diet", ["vegan", "vegetarian", "no_pork", "gluten_free", "lactose_free"])
def test_diets_respected(diet):
    cat = get_catalog()
    p = plan(region="DE", adults=2, diets=[diet])
    for m in all_meals(p):
        for ing in cat.recipes[m["recipe_id"]].ingredients:
            assert not (cat.ingredients[ing].flags & DIET_FORBIDS[diet]), (m["name"], ing)


def test_exclusions_respected():
    p = plan(adults=2, exclude=["onion", "milk"])
    for r in p["recipes"].values():
        assert not {"onion", "milk"} & {i["id"] for i in r["ingredients"]}


def test_pork_never_planned_in_uzbekistan():
    p = plan(region="UZ", adults=3)
    lines = {i["id"] for c in p["shopping_list"] for i in c["items"]}
    assert "pork" not in lines


def test_pantry_lowers_bill():
    base = plan(adults=2, children=2)
    potatoes = sum(i["needed"] for c in base["shopping_list"] for i in c["items"] if i["id"] == "potato")
    with_pantry = plan(adults=2, children=2, pantry=[{"ingredient": "potato", "quantity": 5000},
                                                     {"ingredient": "rice", "quantity": 2000}])
    assert with_pantry["totals"]["cost"] < base["totals"]["cost"]
    assert any(i["id"] == "potato" for i in with_pantry["pantry_used"]) or potatoes == 0


def test_portions_scale_with_family():
    small = plan(adults=1, budget=10000)
    big = plan(adults=4, children=2, budget=20000)
    assert big["totals"]["cost"] > small["totals"]["cost"]
    # Whole packs: a single person pays more per portion than a family.
    assert big["totals"]["cost"] / 5.2 < small["totals"]["cost"]


def test_city_index_raises_prices():
    base = plan(adults=2, budget=100000)
    msk = plan(adults=2, budget=100000, city="msk")
    assert msk["totals"]["cost"] > base["totals"]["cost"]


def test_user_price_override_is_used():
    p = plan(adults=2, price_overrides={"potato": {"price": 999}})
    line = next((i for c in p["shopping_list"] for i in c["items"] if i["id"] == "potato"), None)
    if line:
        assert line["pack_price"] == 999


def test_low_budget_gives_warning_and_cheapest_plan():
    p = plan(adults=4, budget=500)
    assert not p["totals"]["within_budget"]
    assert p["warnings"]
    roomy = plan(adults=4, budget=100000)
    assert p["totals"]["cost"] <= roomy["totals"]["cost"]


def test_tighter_budget_never_costs_more():
    tight = plan(adults=2, budget=3500)
    loose = plan(adults=2, budget=50000)
    assert tight["totals"]["cost"] <= loose["totals"]["cost"]


def test_only_dinners():
    p = plan(adults=2, meals=["dinner"])
    assert all(len(d["meals"]) == 1 and d["meals"][0]["slot"] == "dinner" for d in p["days"])


def test_impossible_filters_raise():
    with pytest.raises(PlanError):
        plan(adults=2, diets=["vegan"], exclude=["oats", "bread"], meals=["breakfast"])


def test_deterministic_for_same_seed():
    a, b = plan(adults=2, seed=3), plan(adults=2, seed=3)
    assert [m["recipe_id"] for m in all_meals(a)] == [m["recipe_id"] for m in all_meals(b)]
