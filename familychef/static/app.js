"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem("fc:" + key); return v ? JSON.parse(v) : fallback; }
    catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem("fc:" + key, JSON.stringify(value)); } catch { /* private mode */ }
  },
};

const state = {
  regions: [],
  region: null,
  ingredients: [],
  ingById: {},
  exclude: [],
  plan: null,
  seed: 0,
  lastRequest: null,
};

const UNIT = { g: "г", ml: "мл", pcs: "шт" };
const NO_DECIMALS = new Set(["RUB", "KZT", "UZS"]);

function money(x) {
  const r = state.region;
  if (!r) return String(x);
  const digits = NO_DECIMALS.has(r.currency) ? 0 : 2;
  try {
    return new Intl.NumberFormat(r.locale, {
      style: "currency", currency: r.currency, maximumFractionDigits: digits, minimumFractionDigits: digits,
    }).format(x);
  } catch { return `${x} ${r.currency}`; }
}

function qty(q, unit) {
  const nf = (n, d = 0) => new Intl.NumberFormat("ru-RU", { maximumFractionDigits: d }).format(n);
  if (unit === "g" && q >= 1000) return `${nf(q / 1000, 2)} кг`;
  if (unit === "ml" && q >= 1000) return `${nf(q / 1000, 2)} л`;
  return `${nf(q, unit === "pcs" ? 1 : 0)} ${UNIT[unit] || unit}`;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, body) {
  const res = await fetch(path, body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : undefined);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    let msg = data.detail;
    if (Array.isArray(msg)) msg = msg.map(d => d.msg).join("; ");
    throw new Error(msg || `Ошибка ${res.status}`);
  }
  return data;
}

// ---------------------------------------------------------------- form ----

function overridesKey() { return "prices:" + state.region.code; }

async function selectRegion(code, keepCity) {
  state.region = state.regions.find(r => r.code === code) || state.regions[0];
  const r = state.region;
  $("#region").value = r.code;
  $("#city").innerHTML = r.cities.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  if (keepCity && r.cities.some(c => c.id === keepCity)) $("#city").value = keepCity;
  $("#currencyLabel").textContent = r.currency;
  const data = await api(`/api/ingredients?region=${r.code}`);
  state.ingredients = data.items.filter(i => i.available);
  state.ingById = Object.fromEntries(data.items.map(i => [i.id, i]));
  const opts = groupedOptions();
  $("#excludeSelect").innerHTML = `<option value="">— выберите продукт —</option>` + opts;
  $$("#pantryRows select").forEach(sel => { const v = sel.value; sel.innerHTML = opts; sel.value = v; });
  updateBudgetHint();
}

function groupedOptions() {
  const groups = {};
  for (const i of Object.values(state.ingById)) (groups[i.category_name] ||= []).push(i);
  return Object.entries(groups).map(([name, items]) =>
    `<optgroup label="${esc(name)}">` +
    items.map(i => `<option value="${i.id}">${esc(i.name)}</option>`).join("") +
    `</optgroup>`).join("");
}

function updateBudgetHint() {
  const r = state.region;
  if (!r || !r.budget_hint_per_adult_week) return;
  const adults = +$("#adults").value || 0, kids = +$("#children").value || 0;
  const city = r.cities.find(c => c.id === $("#city").value) || { index: 1 };
  const hint = r.budget_hint_per_adult_week * (adults + 0.6 * kids) * city.index;
  $("#budgetHint").textContent = hint > 0
    ? `Ориентир для экономного меню в регионе: около ${money(Math.round(hint))} в неделю на вашу семью.`
    : "";
  if (!$("#budget").value && hint > 0) $("#budget").value = Math.round(hint);
}

function renderExclude() {
  $("#excludeList").innerHTML = state.exclude.map(id =>
    `<span class="chip">${esc(state.ingById[id]?.name || id)}<button type="button" data-id="${id}" aria-label="Убрать">×</button></span>`
  ).join("");
}

function addPantryRow(item) {
  const row = document.createElement("div");
  row.className = "pantry-row";
  row.innerHTML = `
    <select aria-label="Продукт">${groupedOptions()}</select>
    <input type="number" min="0" step="any" inputmode="decimal" placeholder="Сколько" aria-label="Количество">
    <span class="unit"></span>
    <button type="button" class="btn icon" aria-label="Удалить">×</button>`;
  const sel = $("select", row), inp = $("input", row), unit = $(".unit", row);
  const syncUnit = () => { unit.textContent = UNIT[state.ingById[sel.value]?.unit] || ""; };
  sel.addEventListener("change", syncUnit);
  $("button", row).addEventListener("click", () => row.remove());
  if (item) { sel.value = item.ingredient; inp.value = item.quantity; }
  syncUnit();
  $("#pantryRows").appendChild(row);
}

function readForm() {
  const checked = id => $$(`#${id} input:checked`).map(i => i.value);
  const pantry = $$("#pantryRows .pantry-row").map(row => ({
    ingredient: $("select", row).value, quantity: parseFloat($("input", row).value),
  })).filter(p => p.ingredient && p.quantity > 0);
  return {
    region: state.region.code,
    city: $("#city").value,
    budget: parseFloat($("#budget").value),
    adults: parseInt($("#adults").value, 10) || 0,
    children: parseInt($("#children").value, 10) || 0,
    meals: checked("meals"),
    diets: checked("diets"),
    favorites: checked("favorites"),
    exclude: [...state.exclude],
    pantry,
  };
}

function restoreForm(f) {
  if (!f) return;
  $("#adults").value = f.adults ?? 2;
  $("#children").value = f.children ?? 0;
  if (f.budget) $("#budget").value = f.budget;
  for (const id of ["meals", "diets", "favorites"]) {
    if (f[id]) $$(`#${id} input`).forEach(i => { i.checked = f[id].includes(i.value); });
  }
  state.exclude = f.exclude || [];
  renderExclude();
  (f.pantry || []).forEach(addPantryRow);
}

async function submitPlan(extra = {}) {
  const form = readForm();
  $("#formError").textContent = "";
  if (!(form.budget > 0)) { $("#formError").textContent = "Укажите бюджет на неделю."; return; }
  if (form.adults + form.children < 1) { $("#formError").textContent = "Укажите хотя бы одного человека."; return; }
  if (!form.meals.length) { $("#formError").textContent = "Выберите хотя бы один приём пищи."; return; }
  store.set("form", form);
  const req = { ...form, price_overrides: store.get(overridesKey(), {}), seed: state.seed, ...extra };
  state.lastRequest = req;
  const btn = $("#submitBtn");
  btn.disabled = true; btn.textContent = "Считаем…";
  try {
    state.plan = await api("/api/plan", req);
    renderPlan();
    $("#result").hidden = false;
    $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    $("#formError").textContent = e.message;
  } finally {
    btn.disabled = false; btn.textContent = "Составить меню";
  }
}

// -------------------------------------------------------------- result ----

function renderPlan() {
  const p = state.plan, t = p.totals;
  const pct = Math.min(100, Math.round(t.cost / t.budget * 100));
  $("#summary").innerHTML = `
    <div class="stat"><div class="v">${money(t.cost)}</div><div class="k">список покупок</div></div>
    <div class="stat"><div class="v">${money(t.budget)}</div><div class="k">бюджет</div></div>
    <div class="stat"><div class="v">${money(t.remaining)}</div><div class="k">${t.remaining >= 0 ? "останется" : "не хватает"}</div></div>
    <div class="stat"><div class="v">${money(t.per_person_per_day)}</div><div class="k">на человека в день</div></div>
    <div class="bar ${t.within_budget ? "" : "over"}"><span style="width:${pct}%"></span></div>
    <div class="meta">${esc(p.region.name)}, ${esc(p.region.city)} · ${p.people.adults} взр. + ${p.people.children} дет. (${p.people.portions} порции) · цены: ${esc(p.region.price_basis)}, ${esc(p.region.price_date)}</div>`;

  $("#warnings").innerHTML = p.warnings.map(w => `<div class="warning">${esc(w)}</div>`).join("");

  $("#week").innerHTML = p.days.map(d => `
    <div class="day"><h3>${esc(d.name)}</h3>
      ${d.meals.map(m => `
        <button type="button" class="meal" data-recipe="${m.recipe_id}">
          <span class="slot">${esc(m.slot_name)}</span>
          <span class="name">${esc(m.name)}</span>
          <span class="meta">${m.minutes} мин · ≈ ${money(m.cost)}</span>
        </button>`).join("")}
    </div>`).join("");

  const overrides = store.get(overridesKey(), {});
  const done = new Set(store.get("done:" + p.region.code, []));
  $("#shopping").innerHTML = p.shopping_list.map(cat => `
    <div class="card cat">
      <h3>${esc(cat.name)} <span class="sub">${money(cat.subtotal)}</span></h3>
      ${cat.items.map(i => `
        <div class="item ${done.has(i.id) ? "done" : ""}" data-id="${i.id}">
          <input type="checkbox" ${done.has(i.id) ? "checked" : ""} aria-label="Куплено">
          <div class="what">
            <strong>${esc(i.name)}</strong> — ${i.packs} × ${esc(i.pack_label)}
            <small>нужно ${qty(i.to_buy, i.unit)}${i.from_pantry > 0 ? ` (ещё ${qty(i.from_pantry, i.unit)} есть дома)` : ""}</small>
          </div>
          <div class="price">
            <span class="line">${money(i.cost)}</span>
            <input type="number" min="0" step="any" inputmode="decimal" value="${i.pack_price}"
              class="${overrides[i.id] ? "changed" : ""}" title="Цена за упаковку" aria-label="Цена за упаковку ${esc(i.name)}">
          </div>
        </div>`).join("")}
    </div>`).join("");
  $("#recalcBtn").disabled = true;

  $("#pantryUsedCard").innerHTML = `<h3>Используем из дома</h3>` + (p.pantry_used.length
    ? `<ul>${p.pantry_used.map(i => `<li>${esc(i.name)}: ${qty(i.quantity, i.unit)}</li>`).join("")}</ul>`
    : `<p class="hint">Ничего не указано.</p>`);
  $("#leftoversCard").innerHTML = `<h3>Останется скоропортящегося</h3>` + (p.leftovers.length
    ? `<ul>${p.leftovers.map(i => `<li>${esc(i.name)}: ${qty(i.quantity, i.unit)}</li>`).join("")}</ul>
       <p class="hint">Используйте в перекусах или внесите в «Уже есть дома» на следующую неделю. Крупы и бакалея останутся в запасе.</p>`
    : `<p class="hint">Почти без остатков.</p>`);
  $("#notes").innerHTML = p.notes.map(n => `<li>${esc(n)}</li>`).join("");
}

function showRecipe(id) {
  const r = state.plan.recipes[id];
  if (!r) return;
  $("#recipeBody").innerHTML = `
    <h3>${esc(r.name)}</h3>
    <p class="hint">${r.minutes} мин · на ${r.portions} порции · ≈ ${money(r.cost_per_cooking)}</p>
    <table>${r.ingredients.map(i => `<tr><td>${esc(i.name)}</td><td>${qty(i.quantity, i.unit)}</td></tr>`).join("")}</table>
    <p>${esc(r.steps)}</p>`;
  $("#recipeDialog").showModal();
}

function shoppingText() {
  const p = state.plan;
  const lines = [`FamilyChef — покупки на неделю (${p.region.name}), итого ${money(p.totals.cost)}`];
  for (const cat of p.shopping_list) {
    lines.push("", cat.name + ":");
    for (const i of cat.items) lines.push(`☐ ${i.name} — ${i.packs} × ${i.pack_label} (${money(i.cost)})`);
  }
  return lines.join("\n");
}

// ---------------------------------------------------------------- wire ----

function wire() {
  $("#region").addEventListener("change", async e => {
    $("#budget").value = "";
    state.exclude = state.exclude.filter(id => state.ingById[id]);
    await selectRegion(e.target.value);
  });
  $("#city").addEventListener("change", () => { $("#budget").value = ""; updateBudgetHint(); });
  ["#adults", "#children"].forEach(s => $(s).addEventListener("input", updateBudgetHint));

  $("#excludeAdd").addEventListener("click", () => {
    const v = $("#excludeSelect").value;
    if (v && !state.exclude.includes(v)) { state.exclude.push(v); renderExclude(); }
    $("#excludeSelect").value = "";
  });
  $("#excludeList").addEventListener("click", e => {
    const id = e.target.dataset?.id;
    if (id) { state.exclude = state.exclude.filter(x => x !== id); renderExclude(); }
  });
  $("#pantryAdd").addEventListener("click", () => addPantryRow());
  $("#planForm").addEventListener("submit", e => { e.preventDefault(); state.seed = 0; submitPlan(); });

  $("#week").addEventListener("click", e => {
    const b = e.target.closest(".meal");
    if (b) showRecipe(b.dataset.recipe);
  });

  $("#shopping").addEventListener("change", e => {
    const item = e.target.closest(".item");
    if (!item) return;
    const id = item.dataset.id, code = state.plan.region.code;
    if (e.target.type === "checkbox") {
      const done = new Set(store.get("done:" + code, []));
      e.target.checked ? done.add(id) : done.delete(id);
      item.classList.toggle("done", e.target.checked);
      store.set("done:" + code, [...done]);
    } else if (e.target.type === "number") {
      const price = parseFloat(e.target.value);
      if (!(price > 0)) return;
      const overrides = store.get(overridesKey(), {});
      overrides[id] = { price };
      store.set(overridesKey(), overrides);
      e.target.classList.add("changed");
      $("#recalcBtn").disabled = false;
    }
  });
  $("#recalcBtn").addEventListener("click", () => submitPlan());
  $("#resetPricesBtn").addEventListener("click", () => { store.set(overridesKey(), {}); submitPlan(); });
  $("#anotherBtn").addEventListener("click", () => { state.seed += 1; submitPlan(); });
  $("#printBtn").addEventListener("click", () => window.print());
  $("#shareBtn").addEventListener("click", async () => {
    const text = shoppingText();
    try {
      if (navigator.share) await navigator.share({ title: "Список покупок", text });
      else { await navigator.clipboard.writeText(text); $("#shareBtn").textContent = "Скопировано"; }
    } catch { /* cancelled */ }
  });
}

async function init() {
  wire();
  try {
    state.regions = await api("/api/regions");
    $("#region").innerHTML = state.regions.map(r => `<option value="${r.code}">${esc(r.name)} (${r.currency})</option>`).join("");
    const saved = store.get("form", null);
    await selectRegion(saved?.region || guessRegion(), saved?.city);
    restoreForm(saved);
    updateBudgetHint();
  } catch (e) {
    $("#formError").textContent = "Не удалось загрузить данные: " + e.message;
  }
}

function guessRegion() {
  const lang = (navigator.language || "ru-RU").toUpperCase();
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  if (tz.startsWith("Asia/Almaty") || tz.startsWith("Asia/Qostanay") || lang.endsWith("-KZ")) return "KZ";
  if (tz === "Asia/Tashkent" || tz === "Asia/Samarkand" || lang.endsWith("-UZ")) return "UZ";
  if (tz === "Europe/Minsk" || lang.endsWith("-BY")) return "BY";
  if (tz === "Europe/Berlin" || lang.startsWith("DE")) return "DE";
  if (tz.startsWith("America/") && lang.startsWith("EN")) return "US";
  return "RU";
}

init();
