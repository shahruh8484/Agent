"use strict";
// App layout for phones and the Android app: top bar, bottom tabs (Menu · Shopping · Family ·
// Profile), one screen at a time, day buttons instead of a long page. On wide screens the
// page stays a regular website with everything on one page.

const shell = { tab: "family", day: 0 };
const APP_QUERY = window.matchMedia("(max-width: 760px)");

function isAppMode() {
  return document.documentElement.classList.contains("app");
}

function applyMode() {
  const app = !!window.FamilyChefAndroid || APP_QUERY.matches
    || window.matchMedia("(display-mode: standalone)").matches;
  document.documentElement.classList.toggle("app", app);
  if (app) goTab(shell.tab, { keepScroll: true });
}

function goTab(name, { keepScroll = false } = {}) {
  shell.tab = name;
  try { sessionStorage.setItem("fc:tab", name); } catch { /* ignore */ }
  $$(".tab").forEach(el => el.classList.toggle("active", el.dataset.tab === name));
  $$("#tabBar [data-goto]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.goto === name)));
  $("#appTitle").textContent = document.documentElement.classList.contains("gated") ? "FamilyChef" : t("tab." + name);
  const hasPlan = !!state.plan && !$("#result").hidden;
  $("#menuEmpty").hidden = hasPlan;
  $("#shopEmpty").hidden = hasPlan;
  if (name === "profile") renderProfile();
  if (!keepScroll) window.scrollTo({ top: 0, behavior: "instant" });
}

// Show a freshly made (or opened) menu: the Menu tab in the app, scroll on the website.
function showResult({ scroll = true } = {}) {
  $("#result").hidden = false;
  if (isAppMode()) goTab("menu");
  else if (scroll) $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
}

// Day buttons ("Mon Tue …") above the menu in the app layout.
function renderDayBar() {
  const days = $$("#week .day");
  const bar = $("#dayBar");
  if (!days.length) { bar.innerHTML = ""; return; }
  let sel = days.findIndex(d => d.classList.contains("today"));
  if (sel < 0) sel = 0;
  shell.day = Math.min(sel, days.length - 1);
  bar.hidden = days.length < 2;
  // Short weekday names from the calendar of the phone (Mon 5 Jan 2026 is a Monday).
  const shortName = wd => {
    const s = new Date(2026, 0, 5 + wd).toLocaleDateString(LANG, { weekday: "short" }).replace(".", "");
    return s.charAt(0).toUpperCase() + s.slice(1);
  };
  bar.innerHTML = days.map((d, i) => {
    const full = $("h3", d).childNodes[0]?.textContent?.trim() || "";
    const wd = state.plan?.days?.[i]?.weekday;
    const short = wd === undefined ? full.slice(0, 3) : shortName(wd);
    return `<button type="button" role="tab" data-day="${i}" class="${d.classList.contains("today") ? "is-today" : ""}"
      aria-label="${esc(full)}">${esc(short)}</button>`;
  }).join("");
  selectDay(shell.day);
}

function selectDay(i) {
  shell.day = i;
  $$("#week .day").forEach((d, k) => d.classList.toggle("sel", k === i));
  $$("#dayBar button").forEach((b, k) => b.setAttribute("aria-selected", String(k === i)));
}

// Profile tab: the account (or sign-in), plan, language and links.
async function renderProfile() {
  const box = $("#profileTab");
  if (!box || !isAppMode()) return;
  const langOptions = Object.entries(LANGS)
    .map(([code, name]) => `<option value="${code}" ${code === LANG ? "selected" : ""}>${name}</option>`).join("");
  box.innerHTML = `
    <div class="card" id="profileAccount"></div>
    <div class="card settings-list">
      <label class="setting"><span>🌐 ${esc(t("profile.language"))}</span>
        <select id="langSelect2" aria-label="${esc(t("profile.language"))}">${langOptions}</select></label>
      <a class="setting" href="/privacy"><span>🔒 ${esc(t("profile.privacy"))}</span><span class="chev">›</span></a>
    </div>
    <p class="hint center">FamilyChef · familychef.pro</p>`;
  $("#langSelect2").addEventListener("change", e => setLang(e.target.value));
  const acc = $("#profileAccount");
  if (auth.user) {
    await renderAccount(acc);
  } else if (authEnabled()) {
    renderSignIn(acc);
  } else {
    acc.innerHTML = `<h2>FamilyChef</h2><p class="hint">${esc(t("app.tagline"))}</p>`;
  }
}

function refreshShellTexts() {
  if (!isAppMode()) return;
  $("#appTitle").textContent = document.documentElement.classList.contains("gated") ? "FamilyChef" : t("tab." + shell.tab);
  if (shell.tab === "profile") renderProfile();
}

function initShell() {
  $$("[data-goto]").forEach(b => b.addEventListener("click", () => goTab(b.dataset.goto)));
  $("#dayBar").addEventListener("click", e => {
    const b = e.target.closest("[data-day]");
    if (b) selectDay(+b.dataset.day);
  });
  let saved = null;
  try { saved = sessionStorage.getItem("fc:tab"); } catch { /* ignore */ }
  shell.tab = saved || (state.plan ? "menu" : "family");
  applyMode();
  APP_QUERY.addEventListener("change", applyMode);
}
