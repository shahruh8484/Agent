"""Request / response schemas for the planner API."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from familychef.catalog import SLOTS

DIETS = ("vegetarian", "vegan", "pescatarian", "no_pork", "gluten_free", "lactose_free", "no_fish")
FAVORITES = ("quick", "soup", "fish", "kids")


class PantryItem(BaseModel):
    ingredient: str
    quantity: float = Field(gt=0, description="В базовой единице ингредиента: г, мл или шт.")


class PriceOverride(BaseModel):
    """Цена, которую пользователь видит в своём магазине."""
    price: float = Field(gt=0)
    pack_size: float | None = Field(default=None, gt=0)
    pack_label: str | None = None


class PlanRequest(BaseModel):
    region: str = "RU"
    city: str | None = None
    budget: float = Field(gt=0, description="Бюджет на неделю в местной валюте региона.")
    adults: int = Field(default=2, ge=0, le=20)
    children: int = Field(default=0, ge=0, le=20)
    meals: list[str] = Field(default_factory=lambda: list(SLOTS))
    diets: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list, description="ID ингредиентов: аллергии и нелюбимые продукты.")
    favorites: list[str] = Field(default_factory=list)
    pantry: list[PantryItem] = Field(default_factory=list)
    price_overrides: dict[str, PriceOverride] = Field(default_factory=dict)
    days: int = Field(default=7, ge=1, le=14)
    lang: str = "ru"
    seed: int = 0

    @field_validator("meals")
    @classmethod
    def _check_meals(cls, v: list[str]) -> list[str]:
        bad = set(v) - set(SLOTS)
        if bad or not v:
            raise ValueError(f"meals должны быть из {SLOTS}")
        return [s for s in SLOTS if s in v]

    @field_validator("diets")
    @classmethod
    def _check_diets(cls, v: list[str]) -> list[str]:
        bad = set(v) - set(DIETS)
        if bad:
            raise ValueError(f"Неизвестные диеты: {bad}")
        return v

    @field_validator("favorites")
    @classmethod
    def _check_favorites(cls, v: list[str]) -> list[str]:
        bad = set(v) - set(FAVORITES)
        if bad:
            raise ValueError(f"Неизвестные предпочтения: {bad}")
        return v

    @property
    def portions(self) -> float:
        """Дети 3–12 лет считаются как 0.6 взрослой порции."""
        return self.adults + 0.6 * self.children
