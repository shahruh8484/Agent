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
  banned: [],   // [{id, name}] — dishes the family doesn't want suggested
  avoid: [],    // [{day, slot, recipe}] — replaced dishes, not to be put back into the same cell
  plan: null,
  seed: 0,
  lastRequest: null,
};

const NO_DECIMALS = new Set(["RUB", "KZT", "UZS"]);
const CAT_ICONS = { produce: "🥕", meat: "🍗", fish: "🐟", dairy: "🥛", grains: "🌾", bakery: "🍞", pantry: "🧂" };

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
  const nf = (n, d = 0) => new Intl.NumberFormat(LANG, { maximumFractionDigits: d }).format(n);
  if (unit === "g" && q >= 1000) return `${nf(q / 1000, 2)} ${t("unit.kg")}`;
  if (unit === "ml" && q >= 1000) return `${nf(q / 1000, 2)} ${t("unit.l")}`;
  return `${nf(q, unit === "pcs" ? 1 : 0)} ${t("unit." + unit)}`;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, body) {
  const res = await fetch(path, body ? {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : { credentials: "same-origin" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    let msg = data.detail;
    if (Array.isArray(msg)) msg = msg.map(d => d.msg).join("; ");
    const err = new Error(msg || t("err.http", { status: res.status }));
    err.status = res.status; err.data = data;
    throw err;
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
  $("#budgetLabel").textContent = t("form.budget", { cur: r.currency });
  const data = await api(`/api/ingredients?region=${r.code}&lang=${LANG}`);
  state.ingredients = data.items.filter(i => i.available);
  state.ingById = Object.fromEntries(data.items.map(i => [i.id, i]));
  const opts = groupedOptions();
  $("#excludeSelect").innerHTML = `<option value="">${esc(t("form.pickProduct"))}</option>` + opts;
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
  $("#budgetHint").textContent = hint > 0 ? t("form.budgetHint", { sum: money(Math.round(hint)) }) : "";
  if (!$("#budget").value && hint > 0) $("#budget").value = Math.round(hint);
}

function renderBanned() {
  $("#bannedBox").hidden = !state.banned.length;
  $("#bannedList").innerHTML = state.banned.map(b =>
    `<span class="chip">${esc(b.name)}<button type="button" data-id="${b.id}" aria-label="${esc(t("form.remove"))}">×</button></span>`
  ).join("");
}

function renderExclude() {
  $("#excludeList").innerHTML = state.exclude.map(id =>
    `<span class="chip">${esc(state.ingById[id]?.name || id)}<button type="button" data-id="${id}" aria-label="${esc(t("form.remove"))}">×</button></span>`
  ).join("");
}

function addPantryRow(item) {
  const row = document.createElement("div");
  row.className = "pantry-row";
  row.innerHTML = `
    <select aria-label="${esc(t("form.product"))}">${groupedOptions()}</select>
    <input type="number" min="0" step="any" inputmode="decimal" placeholder="${esc(t("form.howMuch"))}" aria-label="${esc(t("form.howMuch"))}">
    <span class="unit"></span>
    <button type="button" class="btn icon" aria-label="${esc(t("form.remove"))}">×</button>`;
  const sel = $("select", row), inp = $("input", row), unit = $(".unit", row);
  const syncUnit = () => { const u = state.ingById[sel.value]?.unit; unit.textContent = u ? t("unit." + u) : ""; };
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
    exclude_recipes: state.banned.map(b => b.id),
    banned: state.banned,
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
  state.banned = f.banned || [];
  renderBanned();
  (f.pantry || []).forEach(addPantryRow);
}

async function submitPlan(extra = {}, { scroll = true } = {}) {
  const form = readForm();
  $("#formError").textContent = "";
  if (!(form.budget > 0)) { $("#formError").textContent = t("err.budget"); return; }
  if (form.adults + form.children < 1) { $("#formError").textContent = t("err.people"); return; }
  if (!form.meals.length) { $("#formError").textContent = t("err.meals"); return; }
  store.set("form", form);
  const req = {
    ...form, lang: LANG, seed: state.seed,
    days: plus.ent.max_days, start_weekday: (new Date().getDay() + 6) % 7,
    price_overrides: isFree() ? {} : store.get(overridesKey(), {}),
    ...extra,
  };
  state.lastRequest = req;
  const btn = $("#submitBtn");
  btn.disabled = true; btn.textContent = t("form.working");
  try {
    state.plan = await api("/api/plan", req);
    store.set("lastPlan", state.plan);  // reopen it next time, like an app
    if (state.plan.entitlements) setEntitlements(state.plan.entitlements);
    saveSettings();
    renderPlan();
    showResult({ scroll });
  } catch (e) {
    if (e.message === "login_required") { openSignIn(); return; }
    if (e.status === 402 && handleLimitError(e)) return;
    $("#formError").textContent = e.message;
  } finally {
    btn.disabled = false; btn.textContent = t("form.submit");
  }
}

// -------------------------------------------------------------- result ----

function renderPlan() {
  const p = state.plan, tot = p.totals;
  const daily = tot.basis === "food";
  const shown = daily ? tot.food_cost : tot.cost;
  const pct = Math.min(100, Math.round(shown / tot.budget * 100));
  $("#menuTitle").textContent = t((p.days_count || p.days.length) === 1 ? "menu.titleDay" : "menu.title");
  $("#summary").innerHTML = `
    <div class="stat"><div class="v">${money(shown)}</div><div class="k">${esc(t(daily ? "sum.foodDay" : "sum.cost"))}</div></div>
    <div class="stat"><div class="v">${money(tot.budget)}</div><div class="k">${esc(t(daily ? "sum.budgetDay" : "sum.budget"))}</div></div>
    <div class="stat"><div class="v">${money(tot.remaining)}</div><div class="k">${esc(t(tot.remaining >= 0 ? "sum.left" : "sum.short"))}</div></div>
    <div class="stat"><div class="v">${money(tot.per_person_per_day)}</div><div class="k">${esc(t("sum.perDay"))}</div></div>
    <div class="bar ${tot.within_budget ? "" : "over"}"><span style="width:${pct}%"></span></div>
    <div class="meta">${esc(t("sum.meta", { region: p.region.name, city: p.region.city, adults: p.people.adults,
      children: p.people.children, portions: p.people.portions, basis: p.region.price_basis, date: p.region.price_date }))}</div>
    ${daily ? `<div class="meta">${esc(t("sum.shopNote", { sum: money(tot.cost) }))}</div>` : ""}`;

  $("#warnings").innerHTML = p.warnings.map(w => `<div class="warning">${esc(w)}</div>`).join("");

  const todayName = new Date().toLocaleDateString(LANG, { weekday: "long" }).toLowerCase();
  $("#week").innerHTML = p.days.map(d => `
    <div class="day ${d.name.toLowerCase() === todayName ? "today" : ""}">
      <h3 data-today="${esc(t("menu.today"))}">${esc(d.name)}</h3>
      ${d.meals.map(m => `
        <button type="button" class="meal" data-recipe="${m.recipe_id}" data-day="${d.day}" data-slot="${m.slot}">
          <span class="emoji" aria-hidden="true">${esc(m.emoji || "🍽️")}</span>
          <span>
            <span class="slot">${esc(m.slot_name)}</span>
            <span class="name">${esc(m.name)}</span>
            <span class="meta">${esc(t("menu.min", { n: m.minutes }))} · ≈ ${money(m.cost)}</span>
          </span>
        </button>`).join("")}
    </div>`).join("");
  renderDayBar();
  const today = $("#week .day.today");
  if (today && window.matchMedia("(max-width: 640px)").matches) {
    $("#week").scrollLeft = today.offsetLeft - 16;
  }

  const overrides = store.get(overridesKey(), {});
  const done = new Set(store.get("done:" + p.region.code, []));
  $("#shopping").innerHTML = p.shopping_list.map(cat => `
    <div class="card cat">
      <h3><span class="cat-name"><span class="cat-ico" aria-hidden="true">${CAT_ICONS[cat.category] || "🛒"}</span>${esc(cat.name)}</span>
        <span class="sub">${money(cat.subtotal)}</span></h3>
      ${cat.items.map(i => `
        <div class="item ${done.has(i.id) ? "done" : ""}" data-id="${i.id}">
          <input type="checkbox" ${done.has(i.id) ? "checked" : ""} aria-label="${esc(t("list.bought"))}">
          <div class="what">
            <strong>${esc(i.name)}</strong> — ${i.packs} × ${esc(i.pack_label)}
            <small>${esc(t("list.need", { qty: qty(i.to_buy, i.unit) }))}${i.from_pantry > 0 ? esc(t("list.atHome", { qty: qty(i.from_pantry, i.unit) })) : ""}</small>
          </div>
          <div class="price">
            <span class="line">${money(i.cost)}</span>
            <input type="number" min="0" step="any" inputmode="decimal" value="${i.pack_price}"
              class="${overrides[i.id] ? "changed" : ""}" title="${esc(t("list.packPrice"))}" aria-label="${esc(t("list.packPrice"))} ${esc(i.name)}">
          </div>
        </div>`).join("")}
    </div>`).join("");
  $("#recalcBtn").disabled = true;
  setEntitlements();

  $("#pantryUsedCard").innerHTML = `<h3>${esc(t("pantry.used"))}</h3>` + (p.pantry_used.length
    ? `<ul>${p.pantry_used.map(i => `<li>${esc(i.name)}: ${qty(i.quantity, i.unit)}</li>`).join("")}</ul>`
    : `<p class="hint">${esc(t("pantry.none"))}</p>`);
  $("#leftoversCard").innerHTML = `<h3>${esc(t("left.title"))}</h3>` + (p.leftovers.length
    ? `<ul>${p.leftovers.map(i => `<li>${esc(i.name)}: ${qty(i.quantity, i.unit)}</li>`).join("")}</ul>
       <p class="hint">${esc(t("left.hint"))}</p>`
    : `<p class="hint">${esc(t("left.none"))}</p>`);
  $("#notes").innerHTML = p.notes.map(n => `<li>${esc(n)}</li>`).join("");
}

function showRecipe(id, day, slot) {
  const r = state.plan.recipes[id];
  if (!r) return;
  $("#recipeBody").innerHTML = `
    <div class="recipe-hero">
      <div class="big-emoji" aria-hidden="true">${esc(r.emoji || "🍽️")}</div>
      <div><h3>${esc(r.name)}</h3>
        <p class="hint">${esc(t("recipe.meta", { min: r.minutes, portions: r.portions, cost: money(r.cost_per_cooking) }))}</p></div>
    </div>
    ${day ? `<div class="row recipe-actions">
      <button type="button" class="btn ghost" id="replaceBtn">↻ ${esc(t("menu.replace"))}</button>
      <button type="button" class="btn ghost" id="banBtn">⊘ ${esc(t("menu.ban"))}</button>
    </div>` : ""}
    <table>${r.ingredients.map(i => `<tr><td>${esc(i.name)}</td><td>${qty(i.quantity, i.unit)}</td></tr>`).join("")}</table>
    <p class="recipe-steps">${esc(r.steps)}</p>`;
  $("#recipeDialog").showModal();
  if (!day) return;
  const gate = fn => () => { if (isFree()) { $("#recipeDialog").close(); openPaywall("replace"); } else fn(); };
  $("#replaceBtn").addEventListener("click", gate(() => replaceMeal(+day, slot, id, false)));
  $("#banBtn").addEventListener("click", gate(() => replaceMeal(+day, slot, id, true)));
}

// Replace one dish: every other cell stays as it is, this cell gets a new dish.
async function replaceMeal(day, slot, recipeId, ban) {
  const name = state.plan.recipes[recipeId]?.name || recipeId;
  $("#recipeDialog").close();
  if (ban && !state.banned.some(b => b.id === recipeId)) {
    state.banned.push({ id: recipeId, name });
    renderBanned();
  }
  state.avoid.push({ day, slot, recipe: recipeId });
  const fixed = [];
  for (const d of state.plan.days) {
    for (const m of d.meals) {
      if (d.day === day && m.slot === slot) continue;
      if (ban && m.recipe_id === recipeId) continue;  // banned everywhere: replace other cells too
      fixed.push({ day: d.day, slot: m.slot, recipe: m.recipe_id });
    }
  }
  const cell = () => $(`.meal[data-day="${day}"][data-slot="${slot}"]`);
  cell()?.classList.add("busy");
  await submitPlan({ fixed, avoid: state.avoid }, { scroll: false });
  const el = cell();
  if (el) { el.classList.add("changed"); setTimeout(() => el.classList.remove("changed"), 2000); }
}

function shoppingText() {
  const p = state.plan;
  const lines = [t("list.shareHead", { region: p.region.name, sum: money(p.totals.cost) })];
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
  $$("[data-step]").forEach(b => b.addEventListener("click", () => {
    const [id, delta] = b.dataset.step.split(":");
    const inp = $("#" + id);
    inp.value = Math.max(+inp.min || 0, Math.min(+inp.max || 20, (parseInt(inp.value, 10) || 0) + +delta));
    inp.dispatchEvent(new Event("input"));
  }));

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
  $("#planForm").addEventListener("submit", e => { e.preventDefault(); state.seed = 0; state.avoid = []; submitPlan(); });
  $("#bannedList").addEventListener("click", e => {
    const id = e.target.dataset?.id;
    if (id) { state.banned = state.banned.filter(b => b.id !== id); renderBanned(); store.set("form", readForm()); saveSettings(); }
  });

  $("#week").addEventListener("click", e => {
    const b = e.target.closest(".meal");
    if (b) showRecipe(b.dataset.recipe, b.dataset.day, b.dataset.slot);
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
  $("#anotherBtn").addEventListener("click", () => {
    if (isFree()) { openPaywall("another"); return; }
    state.seed += 1; state.avoid = []; submitPlan();
  });
  $("#shopping").addEventListener("focusin", e => {
    if (isFree() && e.target.matches(".price input")) { e.target.blur(); openPaywall("prices"); }
  });
  $("#printBtn").addEventListener("click", () => window.print());
  if (window.FamilyChefAndroid) $("#printBtn").hidden = true;  // в Android-приложении печати нет
  $("#shareBtn").addEventListener("click", async () => {
    const text = shoppingText();
    if (window.FamilyChefAndroid) { window.FamilyChefAndroid.share(text); return; }
    try {
      if (navigator.share) await navigator.share({ title: t("list.shareTitle"), text });
      else { await navigator.clipboard.writeText(text); $("#shareBtn").textContent = t("list.copied"); }
    } catch { /* cancelled */ }
  });
}

async function loadRegions(keepRegion, keepCity) {
  state.regions = await api(`/api/regions?lang=${LANG}`);
  $("#region").innerHTML = state.regions.map(r => `<option value="${r.code}">${esc(r.name)} (${r.currency})</option>`).join("");
  await selectRegion(keepRegion, keepCity);
}

async function setLang(lang) {
  if (!I18N[lang] || lang === LANG) return;
  LANG = lang;
  store.set("lang", lang);
  applyStaticTranslations();
  renderExclude();
  $$("#pantryRows .pantry-row").forEach(row => {
    const u = state.ingById[$("select", row).value]?.unit;
    $(".unit", row).textContent = u ? t("unit." + u) : "";
  });
  await loadRegions(state.region?.code, $("#city").value);
  renderExclude();
  refreshAuthTexts();
  refreshShellTexts();
  if (state.plan) submitPlan();  // dish names, steps and labels come from the server
}

async function init() {
  applyStaticTranslations();
  $("#langSelect").innerHTML = Object.entries(LANGS)
    .map(([code, name]) => `<option value="${code}">${name}</option>`).join("");
  $("#langSelect").value = LANG;
  $("#langSelect").addEventListener("change", e => setLang(e.target.value));
  wire();
  try {
    const saved = store.get("form", null);
    await loadRegions(saved?.region || guessRegion(), saved?.city);
    restoreForm(saved);
    updateBudgetHint();
    await initAuth();
    if (!auth.user) await loadEntitlements();
    await restoreLastPlan();
    initShell();
    updateBudgetHint();
  } catch (e) {
    $("#formError").textContent = t("err.load", { msg: e.message });
  }
}

async function restoreLastPlan() {
  const last = store.get("lastPlan", null);
  if (!last || !last.days || state.plan) return;
  if (last.region?.code && last.region.code !== state.region?.code) await selectRegion(last.region.code);
  state.plan = last;
  renderPlan();
  $("#result").hidden = false;
}

function guessRegion() {
  const lang = (navigator.language || "en-US").toUpperCase();
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  const RU_TZ = ["Europe/Moscow", "Europe/Samara", "Europe/Volgograd", "Europe/Kaliningrad", "Europe/Kirov",
    "Europe/Ulyanovsk", "Europe/Astrakhan", "Europe/Saratov", "Asia/Yekaterinburg", "Asia/Omsk",
    "Asia/Novosibirsk", "Asia/Krasnoyarsk", "Asia/Irkutsk", "Asia/Yakutsk", "Asia/Vladivostok"];
  const CA_TZ = ["America/Toronto", "America/Vancouver", "America/Edmonton", "America/Winnipeg", "America/Halifax",
    "America/St_Johns", "America/Regina", "America/Montreal", "America/Moncton"];
  if (tz.startsWith("Asia/Almaty") || tz.startsWith("Asia/Qostanay") || tz.startsWith("Asia/Aqtobe") || lang.endsWith("-KZ")) return "KZ";
  if (tz === "Asia/Tashkent" || tz === "Asia/Samarkand" || lang.endsWith("-UZ")) return "UZ";
  if (tz === "Asia/Bishkek" || lang.endsWith("-KG")) return "KG";
  if (tz === "Asia/Dushanbe" || lang.endsWith("-TJ")) return "TJ";
  if (tz === "Asia/Baku" || lang.endsWith("-AZ")) return "AZ";
  if (tz === "Europe/Minsk" || lang.endsWith("-BY")) return "BY";
  if (RU_TZ.includes(tz) || lang.endsWith("-RU")) return "RU";
  if (tz === "Europe/London" || lang.endsWith("-GB")) return "GB";
  if (CA_TZ.includes(tz) || lang.endsWith("-CA")) return "CA";
  if (tz.startsWith("Australia/") || lang.endsWith("-AU")) return "AU";
  if (tz.startsWith("Europe/")) return "DE";  // nearest available price table: euro area
  return "US";
}

init();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
