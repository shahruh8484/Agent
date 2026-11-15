"use strict";
// UI translations. To add a language: copy the "en" block, translate the values,
// add the code to LANGS. Server-side texts (dishes, steps, countries) come from the API.

const LANGS = { en: "English", ru: "Русский" };

const I18N = {
  en: {
    "app.tagline": "Weekly family meal plan, ingredient amounts and a shopping list at your local prices",
    "form.country": "Country",
    "form.city": "City",
    "form.adults": "Adults",
    "form.children": "Children (3–12)",
    "form.budget": "Weekly budget, {cur}",
    "form.budgetHint": "A budget menu in this area costs about {sum} a week for your family.",
    "form.meals": "Which meals to plan",
    "slot.breakfast": "Breakfast",
    "slot.lunch": "Lunch",
    "slot.dinner": "Dinner",
    "form.diet": "Diet",
    "diet.vegetarian": "Vegetarian",
    "diet.vegan": "Vegan",
    "diet.pescatarian": "No meat, fish OK",
    "diet.no_pork": "No pork",
    "diet.no_fish": "No fish",
    "diet.gluten_free": "Gluten-free",
    "diet.lactose_free": "Dairy-free",
    "form.favorites": "We'd like more",
    "fav.quick": "Quick dishes",
    "fav.soup": "Soups",
    "fav.fish": "Fish",
    "fav.kids": "Kid-friendly",
    "form.exclude": "Don't use (allergy or dislike)",
    "form.pickProduct": "— choose a product —",
    "form.add": "Add",
    "form.remove": "Remove",
    "form.pantry": "Already at home",
    "form.pantryAdd": "+ Add a product",
    "form.product": "Product",
    "form.howMuch": "Amount",
    "form.submit": "Make a menu",
    "form.working": "Calculating…",
    "err.budget": "Enter a weekly budget.",
    "err.people": "Add at least one person.",
    "err.meals": "Choose at least one meal.",
    "err.load": "Could not load data: {msg}",
    "err.http": "Error {status}",
    "sum.cost": "shopping list",
    "sum.budget": "budget",
    "sum.left": "left over",
    "sum.short": "short",
    "sum.perDay": "per person per day",
    "sum.meta": "{region}, {city} · {adults} adults + {children} children ({portions} portions) · prices: {basis}, {date}",
    "menu.title": "Weekly menu",
    "menu.min": "{n} min",
    "list.title": "Shopping list",
    "list.share": "Share",
    "list.copied": "Copied",
    "list.print": "Print",
    "list.hint": "Price different in your store? Change the price per pack and tap “Recalculate” — the menu will adapt to your prices.",
    "list.need": "need {qty}",
    "list.atHome": " ({qty} more at home)",
    "list.bought": "Bought",
    "list.packPrice": "Price per pack",
    "list.recalc": "Recalculate with my prices",
    "list.resetPrices": "Reset my prices",
    "list.another": "Another menu",
    "list.shareTitle": "Shopping list",
    "list.shareHead": "FamilyChef — shopping for the week ({region}), total {sum}",
    "pantry.used": "Using from home",
    "pantry.none": "Nothing listed.",
    "left.title": "Perishables left over",
    "left.hint": "Use them for snacks or add them to “Already at home” next week. Grains and pantry staples will keep.",
    "left.none": "Almost nothing left over.",
    "recipe.meta": "{min} min · {portions} portions · ≈ {cost}",
    "recipe.close": "Close",
    "footer.text": "FamilyChef · prices are estimates and are updated per region",
    "footer.privacy": "Privacy",
    "unit.g": "g", "unit.kg": "kg", "unit.ml": "ml", "unit.l": "l", "unit.pcs": "pcs",
  },
  ru: {
    "app.tagline": "Меню на неделю, расчёт продуктов и список покупок по ценам вашего региона",
    "form.country": "Страна",
    "form.city": "Город",
    "form.adults": "Взрослые",
    "form.children": "Дети (3–12 лет)",
    "form.budget": "Бюджет на неделю, {cur}",
    "form.budgetHint": "Ориентир для экономного меню в регионе: около {sum} в неделю на вашу семью.",
    "form.meals": "Какие приёмы пищи планировать",
    "slot.breakfast": "Завтрак",
    "slot.lunch": "Обед",
    "slot.dinner": "Ужин",
    "form.diet": "Питание",
    "diet.vegetarian": "Вегетарианское",
    "diet.vegan": "Веганское",
    "diet.pescatarian": "Без мяса, рыба можно",
    "diet.no_pork": "Без свинины",
    "diet.no_fish": "Без рыбы",
    "diet.gluten_free": "Без глютена",
    "diet.lactose_free": "Без молочного",
    "form.favorites": "Хотим чаще",
    "fav.quick": "Быстрые блюда",
    "fav.soup": "Супы",
    "fav.fish": "Рыбу",
    "fav.kids": "Детские блюда",
    "form.exclude": "Не используем (аллергия или не любим)",
    "form.pickProduct": "— выберите продукт —",
    "form.add": "Добавить",
    "form.remove": "Убрать",
    "form.pantry": "Уже есть дома",
    "form.pantryAdd": "+ Добавить продукт",
    "form.product": "Продукт",
    "form.howMuch": "Сколько",
    "form.submit": "Составить меню",
    "form.working": "Считаем…",
    "err.budget": "Укажите бюджет на неделю.",
    "err.people": "Укажите хотя бы одного человека.",
    "err.meals": "Выберите хотя бы один приём пищи.",
    "err.load": "Не удалось загрузить данные: {msg}",
    "err.http": "Ошибка {status}",
    "sum.cost": "список покупок",
    "sum.budget": "бюджет",
    "sum.left": "останется",
    "sum.short": "не хватает",
    "sum.perDay": "на человека в день",
    "sum.meta": "{region}, {city} · {adults} взр. + {children} дет. ({portions} порции) · цены: {basis}, {date}",
    "menu.title": "Меню на неделю",
    "menu.min": "{n} мин",
    "list.title": "Список покупок",
    "list.share": "Поделиться",
    "list.copied": "Скопировано",
    "list.print": "Печать",
    "list.hint": "Цена отличается от вашего магазина? Исправьте цену за упаковку и нажмите «Пересчитать» — меню подстроится под ваши цены.",
    "list.need": "нужно {qty}",
    "list.atHome": " (ещё {qty} есть дома)",
    "list.bought": "Куплено",
    "list.packPrice": "Цена за упаковку",
    "list.recalc": "Пересчитать с моими ценами",
    "list.resetPrices": "Сбросить мои цены",
    "list.another": "Другой вариант меню",
    "list.shareTitle": "Список покупок",
    "list.shareHead": "FamilyChef — покупки на неделю ({region}), итого {sum}",
    "pantry.used": "Используем из дома",
    "pantry.none": "Ничего не указано.",
    "left.title": "Останется скоропортящегося",
    "left.hint": "Используйте в перекусах или внесите в «Уже есть дома» на следующую неделю. Крупы и бакалея останутся в запасе.",
    "left.none": "Почти без остатков.",
    "recipe.meta": "{min} мин · на {portions} порции · ≈ {cost}",
    "recipe.close": "Закрыть",
    "footer.text": "FamilyChef · цены ориентировочные, обновляются по регионам",
    "footer.privacy": "Конфиденциальность",
    "unit.g": "г", "unit.kg": "кг", "unit.ml": "мл", "unit.l": "л", "unit.pcs": "шт",
  },
};

// Languages of the former USSR default to Russian; everything else to English.
const RU_FAMILY = ["ru", "uk", "be", "kk", "uz", "ky", "tg", "az", "hy", "ka", "tk"];

function detectLang() {
  try {
    const saved = localStorage.getItem("fc:lang");
    if (saved && I18N[JSON.parse(saved)]) return JSON.parse(saved);
  } catch { /* ignore */ }
  for (const l of navigator.languages || [navigator.language || "en"]) {
    const code = String(l).toLowerCase().slice(0, 2);
    if (I18N[code]) return code;
    if (RU_FAMILY.includes(code)) return "ru";
  }
  return "en";
}

let LANG = detectLang();

function t(key, vars = {}) {
  const s = (I18N[LANG] && I18N[LANG][key]) ?? I18N.en[key] ?? key;
  return s.replace(/\{(\w+)\}/g, (_, k) => (k in vars ? vars[k] : `{${k}}`));
}

function applyStaticTranslations(root = document) {
  document.documentElement.lang = LANG;
  root.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll("[data-i18n-aria]").forEach(el => { el.setAttribute("aria-label", t(el.dataset.i18nAria)); });
}
