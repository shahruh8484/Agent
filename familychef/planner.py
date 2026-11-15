"""Weekly meal planner.

The planner picks a recipe for every (day, meal) slot so that the *real
shopping bill* — whole packs at local shelf prices, minus what is already at
home — fits the budget, while keeping the menu varied and familiar for the
region.

Cost model: for every ingredient the planner sums what all chosen recipes
need, subtracts the pantry, rounds up to whole local packs and multiplies by
the local pack price. Because packs are rounded, recipes that share
ingredients (one bag of carrots for plov *and* soup) naturally come out
cheaper, which also reduces food waste.

Search: greedy construction followed by local search (single-slot swaps
until no improvement), from several deterministic starts; the best plan wins.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone

from familychef.catalog import SLOTS, Catalog, Price, Recipe, Region, norm_lang
from familychef.models import PlanRequest

DIET_FORBIDS: dict[str, set[str]] = {
    "vegetarian": {"meat", "pork", "poultry", "fish"},
    "vegan": {"meat", "pork", "poultry", "fish", "dairy", "egg"},
    "pescatarian": {"meat", "pork", "poultry"},
    "no_pork": {"pork"},
    "gluten_free": {"gluten"},
    "lactose_free": {"dairy"},
    "no_fish": {"fish"},
}

DAY_NAMES = {
    "ru": ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}
SLOT_NAMES = {
    "ru": {"breakfast": "Завтрак", "lunch": "Обед", "dinner": "Ужин"},
    "en": {"breakfast": "Breakfast", "lunch": "Lunch", "dinner": "Dinner"},
}
PERISHABLE = {"produce", "meat", "fish", "dairy", "bakery"}
CATEGORY_ORDER = ["produce", "meat", "fish", "dairy", "grains", "bakery", "pantry"]
CATEGORY_NAMES = {
    "ru": {"produce": "Овощи и фрукты", "meat": "Мясо и птица", "fish": "Рыба",
           "dairy": "Молочное и яйца", "grains": "Крупы, макароны, бобовые",
           "bakery": "Хлеб", "pantry": "Бакалея"},
    "en": {"produce": "Produce", "meat": "Meat & poultry", "fish": "Fish",
           "dairy": "Dairy & eggs", "grains": "Grains, pasta, legumes",
           "bakery": "Bakery", "pantry": "Pantry"},
}

# Objective weights. Cost is expressed in "points per % of budget", so a
# penalty of 6 means "worth spending 6 % of the budget to avoid".
W_COST = 100.0
W_OVER_BUDGET = 1000.0
P_REPEAT = 6.0
P_CONSECUTIVE = 3.0
P_CONSECUTIVE_BREAKFAST = 1.5
P_NOT_LOCAL = 4.0
B_FAVORITE = 3.0
N_STARTS = 4
MAX_PASSES = 25
EPS = 1e-9


MESSAGES = {
    "no_pack": {"ru": "Для {ing} нужна фасовка (pack_size): в регионе нет базовой цены",
                "en": "{ing} needs a pack size: there is no base price for it in this region"},
    "bad_pantry": {"ru": "Неизвестный продукт в запасах: {ing}", "en": "Unknown pantry item: {ing}"},
    "bad_ingredient": {"ru": "Неизвестный продукт: {ing}", "en": "Unknown ingredient: {ing}"},
    "no_people": {"ru": "Укажите хотя бы одного человека", "en": "Add at least one person"},
    "no_dishes": {"ru": "Нет подходящих блюд на {slot}: ослабьте ограничения или исключения",
                  "en": "No suitable dishes for {slot}: relax the diet filters or exclusions"},
    "no_plan": {"ru": "Не удалось составить меню с такими ограничениями",
                "en": "Could not build a menu with these restrictions"},
    "over_budget": {
        "ru": "Бюджета не хватает: самое экономное меню, которое удалось составить, стоит {cost} {cur} "
              "при бюджете {budget} {cur}. Увеличьте бюджет, добавьте продукты из дома или ослабьте ограничения.",
        "en": "The budget is not enough: the cheapest menu we could build costs {cost} {cur} with a budget "
              "of {budget} {cur}. Increase the budget, add what you have at home or relax the restrictions."},
    "note_season": {"ru": "Цены овощей, фруктов и зелени — с учётом сезона ({month}).",
                    "en": "Vegetable, fruit and herb prices are adjusted for the season ({month})."},
    "note_staples": {"ru": "Соль, перец и специи считаются домашним запасом и не входят в список покупок.",
                     "en": "Salt, pepper and spices are assumed to be at home and are not on the shopping list."},
    "note_prices": {
        "ru": "Цены — ориентир ({basis}, {date}). Исправьте цену любой позиции на цену своего магазина — меню пересчитается.",
        "en": "Prices are estimates ({basis}, {date}). Change any price to what your store charges and the menu will be recalculated."},
}
MONTHS = {
    "ru": ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
           "октябрь", "ноябрь", "декабрь"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"],
}
UNIT_LABELS = {"ru": {"g": "г", "kg": "кг", "ml": "мл", "l": "л", "pcs": "шт"},
               "en": {"g": "g", "kg": "kg", "ml": "ml", "l": "l", "pcs": "pcs"}}


def msg(lang: str, key: str, **kw) -> str:
    texts = MESSAGES[key]
    return texts.get(lang, texts["en"]).format(**kw)


def pack_label(price: Price, unit: str, lang: str, label_lang: str) -> str:
    """The store's own label when it is in the UI language, else a neutral size label."""
    if lang == label_lang:
        return price.pack_label
    u = UNIT_LABELS.get(lang, UNIT_LABELS["en"])
    size = price.pack_size
    if unit in ("g", "ml") and size >= 1000:
        num, word = f"{round(size / 1000, 2):g}", u["kg" if unit == "g" else "l"]
    else:
        num, word = f"{size:g}", u[unit]
    if lang == "ru":
        num = num.replace(".", ",")
    return f"{num} {word}"


class PlanError(ValueError):
    pass


@dataclass
class _Problem:
    region: Region
    prices: dict[str, Price]
    pantry: dict[str, float]
    portions: float
    budget: float
    slots: list[tuple[int, str]]  # (day index, slot)
    candidates: dict[str, list[Recipe]]  # slot -> eligible recipes
    slot_cands: list[list[Recipe]]  # per menu cell: allowed recipes (fixed / avoided applied)
    caps: dict[str, int]  # recipe id -> max uses
    favorites: set[str]
    lang: str = "ru"
    month: int = 1
    # A full week is judged by the real bill (whole packs). A shorter menu (e.g. one day)
    # is judged by the food actually eaten: buying a 1 kg bag of rice for one dinner
    # does not mean the dinner cost the whole bag.
    whole_packs: bool = True

    def ing_cost(self, ing: str, total: float) -> float:
        need = total - self.pantry.get(ing, 0.0)
        if need <= EPS:
            return 0.0
        p = self.prices[ing]
        if not self.whole_packs:
            return need / p.pack_size * p.price
        return math.ceil(need / p.pack_size - EPS) * p.price


def effective_prices(region: Region, city_id: str | None, overrides: dict,
                     lang: str = "ru", catalog: Catalog | None = None,
                     month: int | None = None) -> dict[str, Price]:
    """Shelf prices: base table × city index × seasonal factor; the user's own prices win."""
    index = region.city(city_id).index
    season = (lambda ing: catalog.season_factor(region, ing, month)) if catalog else (lambda ing: 1.0)
    prices = {
        ing: Price(p.pack_size, p.pack_label, round(p.price * index * season(ing), 2))
        for ing, p in region.prices.items()
    }
    for ing, ov in overrides.items():
        base = prices.get(ing)
        pack_size = ov.pack_size or (base.pack_size if base else None)
        if pack_size is None:
            raise PlanError(msg(lang, "no_pack", ing=ing))
        label = ov.pack_label or (base.pack_label if base and not ov.pack_size else f"{pack_size:g}")
        prices[ing] = Price(pack_size, label, ov.price)
    return prices


def eligible_recipes(catalog: Catalog, req: PlanRequest, prices: dict[str, Price]) -> list[Recipe]:
    forbidden: set[str] = set()
    for d in req.diets:
        forbidden |= DIET_FORBIDS[d]
    excluded = set(req.exclude)
    excluded_recipes = set(req.exclude_recipes)
    result = []
    for r in catalog.recipes.values():
        if r.id in excluded_recipes:
            continue
        if not set(r.slots) & set(req.meals):
            continue
        if any(ing not in prices for ing in r.ingredients):
            continue  # not sold in this region
        if excluded & set(r.ingredients):
            continue
        if any(catalog.ingredients[i].flags & forbidden for i in r.ingredients):
            continue
        result.append(r)
    return result


def _build_problem(catalog: Catalog, req: PlanRequest) -> _Problem:
    lang = norm_lang(req.lang)
    region = catalog.region(req.region)
    for item in req.pantry:
        if item.ingredient not in catalog.ingredients:
            raise PlanError(msg(lang, "bad_pantry", ing=item.ingredient))
    for ing in list(req.exclude) + list(req.price_overrides):
        if ing not in catalog.ingredients:
            raise PlanError(msg(lang, "bad_ingredient", ing=ing))
    if req.portions <= 0:
        raise PlanError(msg(lang, "no_people"))

    month = req.month or datetime.now(timezone.utc).month
    prices = effective_prices(region, req.city, req.price_overrides, lang, catalog, month)
    pool = eligible_recipes(catalog, req, prices)
    slots = [(d, s) for d in range(req.days) for s in req.meals]
    candidates = {s: [r for r in pool if s in r.slots] for s in req.meals}
    for s, cands in candidates.items():
        if not cands:
            raise PlanError(msg(lang, "no_dishes", slot=SLOT_NAMES[lang][s].lower()))

    # Variety caps: a breakfast may repeat up to 3x a week, a main dish 2x.
    # If filters leave too few recipes, relax the cap so a plan still exists.
    caps: dict[str, int] = {}
    for r in pool:
        main = any(s in ("lunch", "dinner") for s in r.slots)
        base = 2 if main else 3
        base = max(1, math.ceil(base * req.days / 7))
        caps[r.id] = base
    for s in req.meals:
        need = sum(1 for _, slot in slots if slot == s)
        cands = candidates[s]
        min_cap = math.ceil(need / len(cands))
        for r in cands:
            caps[r.id] = max(caps[r.id], min_cap)
    # lunch+dinner share the main pool
    mains = {r.id for s in ("lunch", "dinner") if s in candidates for r in candidates[s]}
    n_main_slots = sum(1 for _, s in slots if s in ("lunch", "dinner"))
    if mains:
        min_cap = math.ceil(n_main_slots / len(mains))
        for rid in mains:
            caps[rid] = max(caps[rid], min_cap)

    # Per-cell candidates: a fixed cell keeps its dish, an avoided dish is left out of its cell.
    by_id = {r.id: r for r in pool}
    fixed = {(m.day - 1, m.slot): by_id[m.recipe] for m in req.fixed
             if m.recipe in by_id and m.slot in by_id[m.recipe].slots}
    avoid: dict[tuple[int, str], set[str]] = {}
    for m in req.avoid:
        avoid.setdefault((m.day - 1, m.slot), set()).add(m.recipe)
    slot_cands: list[list[Recipe]] = []
    for cell in slots:
        if cell in fixed:
            slot_cands.append([fixed[cell]])
            continue
        allowed = [r for r in candidates[cell[1]] if r.id not in avoid.get(cell, ())]
        slot_cands.append(allowed or candidates[cell[1]])
    for r in fixed.values():
        caps[r.id] = max(caps[r.id], sum(1 for f in fixed.values() if f.id == r.id))

    pantry: dict[str, float] = {}
    for item in req.pantry:
        pantry[item.ingredient] = pantry.get(item.ingredient, 0.0) + item.quantity

    # The budget is entered per week; a shorter menu gets its share of it.
    period_budget = req.budget * min(req.days, 7) / 7
    return _Problem(
        region=region, prices=prices, pantry=pantry, portions=req.portions,
        budget=period_budget, slots=slots, candidates=candidates, slot_cands=slot_cands, caps=caps,
        favorites=set(req.favorites), lang=lang, month=month, whole_packs=req.days >= 7,
    )


class _State:
    def __init__(self, prob: _Problem):
        self.prob = prob
        self.assign: list[Recipe | None] = [None] * len(prob.slots)
        self.totals: dict[str, float] = {}
        self.cost = 0.0

    def _delta_cost(self, old: Recipe | None, new: Recipe | None) -> tuple[float, dict[str, float]]:
        prob = self.prob
        changes: dict[str, float] = {}
        for r, sign in ((old, -1.0), (new, 1.0)):
            if r is None:
                continue
            for ing, q in r.ingredients.items():
                changes[ing] = changes.get(ing, 0.0) + sign * q * prob.portions
        delta = 0.0
        new_totals = {}
        for ing, ch in changes.items():
            if abs(ch) < EPS:
                continue
            cur = self.totals.get(ing, 0.0)
            nt = cur + ch
            new_totals[ing] = nt
            delta += prob.ing_cost(ing, nt) - prob.ing_cost(ing, cur)
        return delta, new_totals

    def apply(self, k: int, new: Recipe | None) -> None:
        delta, new_totals = self._delta_cost(self.assign[k], new)
        self.totals.update(new_totals)
        self.cost += delta
        self.assign[k] = new

    def soft_penalty(self, assign: list[Recipe | None]) -> float | None:
        """Variety / locality / preference terms. None = hard constraint broken."""
        prob = self.prob
        counts: dict[str, int] = {}
        pen = 0.0
        by_day: dict[int, dict[str, Recipe]] = {}
        for (day, slot), r in zip(prob.slots, assign):
            if r is None:
                continue
            counts[r.id] = counts.get(r.id, 0) + 1
            by_day.setdefault(day, {})[slot] = r
            if not r.is_local(prob.region.code):
                pen += P_NOT_LOCAL
            if r.tags & prob.favorites:
                pen -= B_FAVORITE
        for rid, c in counts.items():
            if c > prob.caps[rid]:
                return None
            pen += P_REPEAT * (c - 1)
        for day, meals in by_day.items():
            ids = [r.id for r in meals.values()]
            if len(ids) != len(set(ids)):
                return None  # same dish twice in one day
            prev = by_day.get(day - 1)
            if not prev:
                continue
            prev_ids = {r.id for r in prev.values()}
            for slot, r in meals.items():
                if r.id in prev_ids:
                    if slot == "breakfast":
                        pen += P_CONSECUTIVE_BREAKFAST
                    elif "soup" not in r.tags:  # soup the next day is normal
                        pen += P_CONSECUTIVE
        return pen

    def cost_score(self, cost: float) -> float:
        b = self.prob.budget
        return W_COST * cost / b + W_OVER_BUDGET * max(0.0, cost / b - 1.0)

    def objective(self) -> float:
        pen = self.soft_penalty(self.assign)
        assert pen is not None
        return self.cost_score(self.cost) + pen

    def try_move(self, k: int, new: Recipe) -> float | None:
        """Objective after replacing slot k with `new` (state unchanged)."""
        delta, _ = self._delta_cost(self.assign[k], new)
        trial = list(self.assign)
        trial[k] = new
        pen = self.soft_penalty(trial)
        if pen is None:
            return None
        return self.cost_score(self.cost + delta) + pen


def _solve_once(prob: _Problem, rng: random.Random) -> _State:
    st = _State(prob)
    order = list(range(len(prob.slots)))
    # Greedy construction; fixed cells first so the rest of the menu fits around them.
    for k in sorted(order, key=lambda i: len(prob.slot_cands[i]) != 1):
        cands = list(prob.slot_cands[k])
        rng.shuffle(cands)
        best, best_val = None, math.inf
        for r in cands:
            val = st.try_move(k, r)
            if val is not None:
                val += rng.random() * 0.5  # tie-breaking noise for diverse starts
                if val < best_val:
                    best, best_val = r, val
        if best is None:  # every option breaks a cap — fall back to the cheapest
            best = cands[0]
        st.apply(k, best)

    if st.soft_penalty(st.assign) is None:
        raise PlanError(msg(prob.lang, "no_plan"))

    # Local search
    for _ in range(MAX_PASSES):
        improved = False
        rng.shuffle(order)
        for k in order:
            current = st.objective()
            best, best_val = None, current - 1e-6
            for r in prob.slot_cands[k]:
                if r is st.assign[k]:
                    continue
                val = st.try_move(k, r)
                if val is not None and val < best_val:
                    best, best_val = r, val
            if best is not None:
                st.apply(k, best)
                improved = True
        if not improved:
            break
    return st


def build_plan(catalog: Catalog, req: PlanRequest) -> dict:
    prob = _build_problem(catalog, req)
    best: _State | None = None
    for i in range(N_STARTS):
        st = _solve_once(prob, random.Random(req.seed * 1000 + i))
        if best is None or st.objective() < best.objective():
            best = st
    assert best is not None
    return _render(catalog, req, prob, best)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def _round_qty(q: float, unit: str) -> float:
    if unit == "pcs":
        return round(q * 2) / 2
    return float(round(q / 5) * 5) if q >= 50 else float(round(q))


def _money(x: float, currency: str) -> float:
    return round(x) if currency in ("UZS", "KZT", "RUB", "KGS") else round(x, 2)


def _render(catalog: Catalog, req: PlanRequest, prob: _Problem, st: _State) -> dict:
    lang = norm_lang(req.lang)
    cur = prob.region.currency
    portions = prob.portions

    def meal_cost(r: Recipe) -> float:
        return sum(
            q * portions / prob.prices[i].pack_size * prob.prices[i].price
            for i, q in r.ingredients.items()
        )

    days = []
    for d in range(req.days):
        meals = []
        for (day, slot), r in zip(prob.slots, st.assign):
            if day != d:
                continue
            meals.append({
                "slot": slot,
                "slot_name": SLOT_NAMES[lang][slot],
                "recipe_id": r.id,
                "emoji": r.emoji,
                "name": r.name(lang),
                "minutes": r.minutes,
                "cost": _money(meal_cost(r), cur),
            })
        weekday = (req.start_weekday + d) % 7
        days.append({"day": d + 1, "weekday": weekday, "name": DAY_NAMES[lang][weekday], "meals": meals})

    used = {r.id: r for r in st.assign if r is not None}
    recipes = {}
    for r in used.values():
        recipes[r.id] = {
            "id": r.id,
            "emoji": r.emoji,
            "name": r.name(lang),
            "minutes": r.minutes,
            "tags": sorted(r.tags),
            "portions": round(portions, 1),
            "steps": r.steps_for(lang),
            "cost_per_cooking": _money(meal_cost(r), cur),
            "ingredients": [
                {
                    "id": i,
                    "name": catalog.ingredients[i].name(lang),
                    "quantity": _round_qty(q * portions, catalog.ingredients[i].unit),
                    "unit": catalog.ingredients[i].unit,
                }
                for i, q in r.ingredients.items()
            ],
        }

    groups: dict[str, list[dict]] = {}
    leftovers, pantry_used = [], []
    total_cost = 0.0
    for ing_id, total in sorted(st.totals.items()):
        if total <= EPS:
            continue
        ing = catalog.ingredients[ing_id]
        have = prob.pantry.get(ing_id, 0.0)
        from_pantry = min(have, total)
        if from_pantry > EPS:
            pantry_used.append({"id": ing_id, "name": ing.name(lang),
                                "quantity": _round_qty(from_pantry, ing.unit), "unit": ing.unit})
        to_buy = total - from_pantry
        if to_buy <= EPS:
            continue
        p = prob.prices[ing_id]
        packs = math.ceil(to_buy / p.pack_size - EPS)
        line = packs * p.price
        total_cost += line
        spare = packs * p.pack_size - to_buy
        if spare > 0.15 * p.pack_size and ing.category in PERISHABLE:
            leftovers.append({"id": ing_id, "name": ing.name(lang),
                              "quantity": _round_qty(spare, ing.unit), "unit": ing.unit})
        groups.setdefault(ing.category, []).append({
            "id": ing_id,
            "name": ing.name(lang),
            "unit": ing.unit,
            "needed": _round_qty(total, ing.unit),
            "from_pantry": _round_qty(from_pantry, ing.unit),
            "to_buy": _round_qty(to_buy, ing.unit),
            "packs": packs,
            "pack_size": p.pack_size,
            "pack_label": pack_label(p, ing.unit, lang, prob.region.label_lang),
            "pack_price": p.price,
            "cost": _money(line, cur),
        })

    shopping = [
        {"category": c, "name": CATEGORY_NAMES[lang][c],
         "items": groups[c], "subtotal": _money(sum(i["cost"] for i in groups[c]), cur)}
        for c in CATEGORY_ORDER if c in groups
    ]
    total_cost = _money(total_cost, cur)
    # Cost of the food actually eaten (pantry excluded, packs not rounded).
    food_cost = 0.0
    for ing_id, total in st.totals.items():
        need = total - prob.pantry.get(ing_id, 0.0)
        if need > EPS:
            p = prob.prices[ing_id]
            food_cost += need / p.pack_size * p.price
    food_cost = _money(food_cost, cur)
    judged = total_cost if prob.whole_packs else food_cost
    period_budget = _money(prob.budget, cur)
    people = req.adults + req.children
    warnings = []
    if judged > period_budget:
        warnings.append(msg(lang, "over_budget", cost=f"{judged:g}", budget=f"{period_budget:g}", cur=cur))
    city = prob.region.city(req.city)
    return {
        "region": {"code": prob.region.code, "name": prob.region.name_for(lang),
                   "city": city.name_for(lang), "currency": cur, "locale": prob.region.locale,
                   "price_date": prob.region.price_date, "month": prob.month,
                   "price_basis": prob.region.price_basis_for(lang)},
        "people": {"adults": req.adults, "children": req.children, "portions": round(portions, 2)},
        "days": days,
        "recipes": recipes,
        "shopping_list": shopping,
        "pantry_used": pantry_used,
        "leftovers": leftovers,
        "totals": {
            "cost": total_cost,              # shopping list, whole packs
            "food_cost": food_cost,          # what the menu itself uses
            "basis": "packs" if prob.whole_packs else "food",
            "budget": period_budget,         # weekly budget, or its share for a shorter menu
            "weekly_budget": req.budget,
            "remaining": _money(period_budget - judged, cur),
            "per_person_per_day": _money(judged / max(people, 1) / req.days, cur),
            "within_budget": judged <= period_budget,
        },
        "days_count": req.days,
        "warnings": warnings,
        "notes": [
            msg(lang, "note_staples"),
            *([msg(lang, "note_season", month=MONTHS[lang][prob.month - 1])]
              if prob.region.seasonality else []),
            msg(lang, "note_prices", basis=prob.region.price_basis_for(lang),
                date=prob.region.price_date),
        ],
    }
