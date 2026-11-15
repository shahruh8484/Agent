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

from familychef.catalog import SLOTS, Catalog, Price, Recipe, Region
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
    caps: dict[str, int]  # recipe id -> max uses
    favorites: set[str]

    def ing_cost(self, ing: str, total: float) -> float:
        need = total - self.pantry.get(ing, 0.0)
        if need <= EPS:
            return 0.0
        p = self.prices[ing]
        return math.ceil(need / p.pack_size - EPS) * p.price


def effective_prices(region: Region, city_id: str | None, overrides: dict) -> dict[str, Price]:
    index = region.city(city_id).index
    prices = {
        ing: Price(p.pack_size, p.pack_label, round(p.price * index, 2))
        for ing, p in region.prices.items()
    }
    for ing, ov in overrides.items():
        base = prices.get(ing)
        pack_size = ov.pack_size or (base.pack_size if base else None)
        if pack_size is None:
            raise PlanError(f"Для {ing} нужна фасовка (pack_size): в регионе нет базовой цены")
        label = ov.pack_label or (base.pack_label if base and not ov.pack_size else f"{pack_size:g}")
        prices[ing] = Price(pack_size, label, ov.price)
    return prices


def eligible_recipes(catalog: Catalog, req: PlanRequest, prices: dict[str, Price]) -> list[Recipe]:
    forbidden: set[str] = set()
    for d in req.diets:
        forbidden |= DIET_FORBIDS[d]
    excluded = set(req.exclude)
    result = []
    for r in catalog.recipes.values():
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
    region = catalog.region(req.region)
    for item in req.pantry:
        if item.ingredient not in catalog.ingredients:
            raise PlanError(f"Неизвестный продукт в запасах: {item.ingredient}")
    for ing in list(req.exclude) + list(req.price_overrides):
        if ing not in catalog.ingredients:
            raise PlanError(f"Неизвестный продукт: {ing}")
    if req.portions <= 0:
        raise PlanError("Укажите хотя бы одного человека")

    prices = effective_prices(region, req.city, req.price_overrides)
    pool = eligible_recipes(catalog, req, prices)
    slots = [(d, s) for d in range(req.days) for s in req.meals]
    candidates = {s: [r for r in pool if s in r.slots] for s in req.meals}
    for s, cands in candidates.items():
        if not cands:
            name = SLOT_NAMES["ru"][s].lower()
            raise PlanError(
                f"Нет подходящих блюд на {name}: ослабьте ограничения или исключения"
            )

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

    pantry: dict[str, float] = {}
    for item in req.pantry:
        pantry[item.ingredient] = pantry.get(item.ingredient, 0.0) + item.quantity

    return _Problem(
        region=region, prices=prices, pantry=pantry, portions=req.portions,
        budget=req.budget, slots=slots, candidates=candidates, caps=caps,
        favorites=set(req.favorites),
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
    # Greedy construction
    for k in order:
        slot = prob.slots[k][1]
        cands = list(prob.candidates[slot])
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
        raise PlanError("Не удалось составить меню с такими ограничениями")

    # Local search
    for _ in range(MAX_PASSES):
        improved = False
        rng.shuffle(order)
        for k in order:
            current = st.objective()
            slot = prob.slots[k][1]
            best, best_val = None, current - 1e-6
            for r in prob.candidates[slot]:
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
    return round(x) if currency in ("UZS", "KZT", "RUB") else round(x, 2)


def _render(catalog: Catalog, req: PlanRequest, prob: _Problem, st: _State) -> dict:
    lang = "en" if req.lang == "en" else "ru"
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
                "name": r.name(lang),
                "minutes": r.minutes,
                "cost": _money(meal_cost(r), cur),
            })
        days.append({"day": d + 1, "name": DAY_NAMES[lang][d % 7], "meals": meals})

    used = {r.id: r for r in st.assign if r is not None}
    recipes = {}
    for r in used.values():
        recipes[r.id] = {
            "id": r.id,
            "name": r.name(lang),
            "minutes": r.minutes,
            "tags": sorted(r.tags),
            "portions": round(portions, 1),
            "steps": r.steps,
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
            "pack_label": p.pack_label,
            "pack_price": p.price,
            "cost": _money(line, cur),
        })

    shopping = [
        {"category": c, "name": CATEGORY_NAMES[lang][c],
         "items": groups[c], "subtotal": _money(sum(i["cost"] for i in groups[c]), cur)}
        for c in CATEGORY_ORDER if c in groups
    ]
    total_cost = _money(total_cost, cur)
    people = req.adults + req.children
    warnings = []
    if total_cost > req.budget:
        warnings.append(
            f"Бюджета не хватает: самое экономное меню, которое удалось составить, стоит "
            f"{total_cost:g} {cur} при бюджете {req.budget:g} {cur}. Увеличьте бюджет, "
            f"добавьте продукты из дома или ослабьте ограничения."
        )
    city = prob.region.city(req.city)
    return {
        "region": {"code": prob.region.code, "name": prob.region.name, "city": city.name,
                   "currency": cur, "locale": prob.region.locale,
                   "price_date": prob.region.price_date, "price_basis": prob.region.price_basis},
        "people": {"adults": req.adults, "children": req.children, "portions": round(portions, 2)},
        "days": days,
        "recipes": recipes,
        "shopping_list": shopping,
        "pantry_used": pantry_used,
        "leftovers": leftovers,
        "totals": {
            "cost": total_cost,
            "budget": req.budget,
            "remaining": _money(req.budget - total_cost, cur),
            "per_person_per_day": _money(total_cost / max(people, 1) / req.days, cur),
            "within_budget": total_cost <= req.budget,
        },
        "warnings": warnings,
        "notes": [
            "Соль, перец и специи считаются домашним запасом и не входят в список покупок.",
            f"Цены — ориентир ({prob.region.price_basis}, {prob.region.price_date}). "
            "Исправьте цену любой позиции на цену своего магазина — меню пересчитается.",
        ],
    }
