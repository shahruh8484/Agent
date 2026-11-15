"""Loads the static catalog: ingredients, recipes, regions and per-region
price tables. Prices are stored per region in the local currency and in the
local pack sizes — they are never derived from another currency."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

SLOTS = ("breakfast", "lunch", "dinner")
DIET_FLAGS = ("meat", "pork", "poultry", "fish", "dairy", "egg", "gluten")


LANGS = ("ru", "en")


def norm_lang(lang: str | None) -> str:
    """Supported UI language; anything else falls back to English."""
    lang = (lang or "").lower()[:2]
    return lang if lang in LANGS else "en"


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Ingredient:
    id: str
    ru: str
    en: str
    unit: str
    category: str
    flags: frozenset[str]

    def name(self, lang: str) -> str:
        return self.en if lang == "en" else self.ru


@dataclass(frozen=True)
class Recipe:
    id: str
    ru: str
    en: str
    slots: tuple[str, ...]
    minutes: int
    tags: frozenset[str]
    popular_in: frozenset[str]
    ingredients: dict[str, float]
    steps: str
    steps_en: str = ""

    def name(self, lang: str) -> str:
        return self.en if lang == "en" else self.ru

    def steps_for(self, lang: str) -> str:
        return self.steps_en if lang == "en" and self.steps_en else self.steps

    def is_local(self, region: str) -> bool:
        return "*" in self.popular_in or region in self.popular_in


@dataclass(frozen=True)
class Price:
    pack_size: float  # in the ingredient's base unit
    pack_label: str
    price: float  # local currency, per pack


@dataclass(frozen=True)
class City:
    id: str
    name: str
    index: float
    name_en: str = ""

    def name_for(self, lang: str) -> str:
        return self.name_en if lang == "en" and self.name_en else self.name


@dataclass
class Region:
    code: str
    name: str
    currency: str
    locale: str
    lang: str
    price_date: str
    price_basis: str
    cities: list[City]
    budget_hint: float = 0.0
    name_en: str = ""
    price_basis_en: str = ""
    label_lang: str = "ru"  # language of pack labels in the price table
    prices: dict[str, Price] = field(default_factory=dict)

    def name_for(self, lang: str) -> str:
        return self.name_en if lang == "en" and self.name_en else self.name

    def price_basis_for(self, lang: str) -> str:
        return self.price_basis_en if lang == "en" and self.price_basis_en else self.price_basis

    def city(self, city_id: str | None) -> City:
        for c in self.cities:
            if c.id == (city_id or "default"):
                return c
        return self.cities[0]


@dataclass
class Catalog:
    ingredients: dict[str, Ingredient]
    recipes: dict[str, Recipe]
    regions: dict[str, Region]

    def region(self, code: str) -> Region:
        try:
            return self.regions[code.upper()]
        except KeyError as exc:
            raise CatalogError(f"Неизвестный регион: {code}") from exc


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_catalog(data_dir: Path = DATA_DIR) -> Catalog:
    ingredients = {
        raw["id"]: Ingredient(
            id=raw["id"], ru=raw["ru"], en=raw["en"], unit=raw["unit"],
            category=raw["category"], flags=frozenset(raw["flags"]),
        )
        for raw in _load_json(data_dir / "ingredients.json")["ingredients"]
    }
    for ing in ingredients.values():
        unknown = ing.flags - set(DIET_FLAGS)
        if unknown:
            raise CatalogError(f"{ing.id}: неизвестные флаги {unknown}")

    recipes: dict[str, Recipe] = {}
    for raw in _load_json(data_dir / "recipes.json")["recipes"]:
        missing = set(raw["ingredients"]) - set(ingredients)
        if missing:
            raise CatalogError(f"Рецепт {raw['id']}: неизвестные ингредиенты {missing}")
        bad_slots = set(raw["slots"]) - set(SLOTS)
        if bad_slots:
            raise CatalogError(f"Рецепт {raw['id']}: неизвестные приёмы пищи {bad_slots}")
        recipes[raw["id"]] = Recipe(
            id=raw["id"], ru=raw["ru"], en=raw["en"], slots=tuple(raw["slots"]),
            minutes=int(raw["minutes"]), tags=frozenset(raw["tags"]),
            popular_in=frozenset(raw["popular_in"]),
            ingredients={k: float(v) for k, v in raw["ingredients"].items()},
            steps=raw["steps"], steps_en=raw.get("steps_en", ""),
        )

    regions: dict[str, Region] = {}
    for raw in _load_json(data_dir / "regions.json")["regions"]:
        region = Region(
            code=raw["code"], name=raw["name"], currency=raw["currency"],
            locale=raw["locale"], lang=raw.get("lang", "ru"),
            price_date=raw["price_date"], price_basis=raw["price_basis"],
            cities=[City(c["id"], c["name"], float(c["index"]), c.get("name_en", ""))
                    for c in raw["cities"]],
            budget_hint=float(raw.get("budget_hint_per_adult_week", 0)),
            name_en=raw.get("name_en", ""), price_basis_en=raw.get("price_basis_en", ""),
            label_lang=raw.get("label_lang", raw.get("lang", "ru")),
        )
        region.prices = _load_prices(data_dir / "prices" / f"{region.code}.csv", ingredients)
        regions[region.code] = region

    return Catalog(ingredients=ingredients, recipes=recipes, regions=regions)


def _load_prices(path: Path, ingredients: dict[str, Ingredient]) -> dict[str, Price]:
    if not path.exists():
        raise CatalogError(f"Нет таблицы цен {path.name}")
    prices: dict[str, Price] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            ing_id = row["ingredient"].strip()
            if ing_id not in ingredients:
                raise CatalogError(f"{path.name}: неизвестный ингредиент {ing_id}")
            pack, price = float(row["pack_size"]), float(row["price"])
            if pack <= 0 or price <= 0:
                raise CatalogError(f"{path.name}: {ing_id} — фасовка и цена должны быть > 0")
            prices[ing_id] = Price(pack, row["pack_label"].strip(), price)
    return prices


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    return load_catalog()
