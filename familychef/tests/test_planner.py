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


def test_english_plan_is_fully_translated():
    import re
    p = plan(region="UZ", adults=2, lang="en")
    assert p["region"]["name"] == "Uzbekistan"
    texts = [d["name"] for d in p["days"]]
    texts += [m["name"] + m["slot_name"] for d in p["days"] for m in d["meals"]]
    texts += [r["steps"] + r["name"] + "".join(i["name"] for i in r["ingredients"]) for r in p["recipes"].values()]
    texts += [c["name"] + "".join(i["name"] + i["pack_label"] for i in c["items"]) for c in p["shopping_list"]]
    texts += p["notes"] + p["warnings"]
    cyrillic = [s for s in texts if re.search("[А-Яа-яЁё]", s)]
    assert not cyrillic, cyrillic[:3]


def test_errors_follow_language():
    with pytest.raises(PlanError, match="No suitable dishes"):
        plan(adults=2, diets=["vegan"], exclude=["oats", "bread"], meals=["breakfast"], lang="en")
    with pytest.raises(PlanError, match="Нет подходящих блюд"):
        plan(adults=2, diets=["vegan"], exclude=["oats", "bread"], meals=["breakfast"], lang="ru")


def test_pack_label_localization():
    from familychef.catalog import Price
    from familychef.planner import pack_label
    p = Price(1361, "3 lb bag", 4.49)
    assert pack_label(p, "g", "en", "en") == "3 lb bag"
    assert pack_label(p, "g", "ru", "en") == "1,36 кг"
    assert pack_label(Price(930, "930 мл", 95), "ml", "en", "ru") == "930 ml"
    assert pack_label(Price(10, "десяток", 110), "pcs", "en", "ru") == "10 pcs"


def cells(p):
    return {(d["day"], m["slot"]): m["recipe_id"] for d in p["days"] for m in d["meals"]}


def test_replace_one_dish_keeps_the_rest():
    base = plan(adults=2)
    before = cells(base)
    target = (3, "dinner")
    fixed = [{"day": d, "slot": s, "recipe": r} for (d, s), r in before.items() if (d, s) != target]
    avoid = [{"day": 3, "slot": "dinner", "recipe": before[target]}]
    after = cells(plan(adults=2, fixed=fixed, avoid=avoid))
    assert after[target] != before[target]
    assert {k: v for k, v in after.items() if k != target} == {k: v for k, v in before.items() if k != target}


def test_replacing_twice_does_not_bring_back_the_first_dish():
    before = cells(plan(adults=2))
    target = (2, "lunch")
    fixed = [{"day": d, "slot": s, "recipe": r} for (d, s), r in before.items() if (d, s) != target]
    avoid = [{"day": 2, "slot": "lunch", "recipe": before[target]}]
    second = cells(plan(adults=2, fixed=fixed, avoid=avoid))[target]
    avoid.append({"day": 2, "slot": "lunch", "recipe": second})
    third = cells(plan(adults=2, fixed=fixed, avoid=avoid))[target]
    assert third not in (before[target], second)


def test_excluded_recipes_never_appear():
    first = plan(adults=2)
    banned = sorted({m["recipe_id"] for d in first["days"] for m in d["meals"]})[:5]
    again = plan(adults=2, exclude_recipes=banned)
    assert not set(banned) & {m["recipe_id"] for d in again["days"] for m in d["meals"]}


def test_seasonal_prices():
    def price(p, ing):
        return next(i["pack_price"] for c in p["shopping_list"] for i in c["items"] if i["id"] == ing)
    from familychef.catalog import get_catalog
    cat = get_catalog()
    uz = cat.regions["UZ"]
    assert cat.season_factor(uz, "tomato", uz.base_month) == 1.0
    assert cat.season_factor(uz, "tomato", 8) < 0.5            # summer tomatoes
    assert cat.season_factor(uz, "potato", 5) > 1.3            # potatoes before the new harvest
    assert cat.season_factor(uz, "rice", 8) == 1.0             # staples are not seasonal
    assert 0.75 < cat.season_factor(cat.regions["US"], "tomato", 8) < 1.0  # imports smooth it out
    nov = plan(region="UZ", adults=2, month=11)
    aug = plan(region="UZ", adults=2, month=8, price_overrides={"potato": {"price": 7777}})
    assert aug["region"]["month"] == 8 and any("август" in n for n in aug["notes"])
    if any(i["id"] == "potato" for c in aug["shopping_list"] for i in c["items"]):
        assert price(aug, "potato") == 7777                    # the user's own price wins
    assert aug["totals"]["cost"] <= nov["totals"]["cost"]
