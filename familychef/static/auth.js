"use strict";
// Sign-in (Google / email code), account panel and settings sync.
// Uses globals from i18n.js (t, LANG) and app.js ($, $$, esc, api, store, state, money,
// selectRegion, restoreForm, renderPlan, readForm).

const auth = {
  config: { google_client_id: null, email: false, email_test_mode: false, require_login: false },
  user: null,
  settings: {},
  gisLoaded: null,
};

function authEnabled() { return !!(auth.config.google_client_id || auth.config.email); }
function inAndroidApp() { return !!window.FamilyChefAndroid; }

async function apiJson(method, path, body) {
  const res = await fetch(path, {
    method, credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { const e = new Error(data.detail || "generic"); e.status = res.status; e.code = data.detail; throw e; }
  return data;
}

function authErrorText(e) {
  const key = "auth.err." + (e.code || "generic");
  const s = t(key);
  return s === key ? t("auth.err.generic") : s;
}

// ------------------------------------------------------------ header button
function renderAccountButton() {
  const btn = $("#accountBtn");
  if (!authEnabled() && !auth.user) { btn.hidden = true; return; }
  btn.hidden = false;
  if (auth.user) {
    const initial = (auth.user.name || auth.user.email || "?").trim().charAt(0).toUpperCase();
    btn.innerHTML = auth.user.picture
      ? `<img src="${esc(auth.user.picture)}" alt="" referrerpolicy="no-referrer"><span>${esc(auth.user.name)}</span>`
      : `<b class="avatar">${esc(initial)}</b><span>${esc(auth.user.name)}</span>`;
  } else {
    btn.textContent = t("auth.signIn");
  }
}

// ------------------------------------------------------------ sign-in UI
function renderSignIn(container, { gate = false } = {}) {
  const c = auth.config;
  container.innerHTML = `
    <h2>${esc(t("auth.title"))}</h2>
    <p class="hint">${esc(t(gate ? "auth.gateWhy" : "auth.why"))}</p>
    ${c.google_client_id ? `<div class="google-slot"></div>` : ""}
    ${c.google_client_id && c.email ? `<div class="or"><span>${esc(t("auth.or"))}</span></div>` : ""}
    ${c.email ? `
      <form class="email-step1">
        <label>${esc(t("auth.email"))}
          <input type="email" name="email" required autocomplete="email" inputmode="email" maxlength="200">
        </label>
        <button class="btn primary" type="submit">${esc(t("auth.getCode"))}</button>
      </form>
      <form class="email-step2" hidden>
        <p class="sent hint"></p>
        <p class="testcode warning" hidden></p>
        <label>${esc(t("auth.code"))}
          <input name="code" required autocomplete="one-time-code" inputmode="numeric" maxlength="6" pattern="[0-9]{6}">
        </label>
        <label>${esc(t("auth.name"))}
          <input name="name" autocomplete="name" maxlength="60">
        </label>
        <button class="btn primary" type="submit">${esc(t("auth.enter"))}</button>
        <div class="row small">
          <button class="linkish change" type="button">${esc(t("auth.changeEmail"))}</button>
          <button class="linkish resend" type="button" disabled></button>
        </div>
      </form>` : ""}
    <p class="error" role="alert"></p>
    <p class="hint small">${esc(t("auth.consent"))} <a href="/privacy" target="_blank">${esc(t("footer.privacy"))}</a></p>`;

  const err = $(".error", container);
  const showErr = e => { err.textContent = e ? authErrorText(e) : ""; };

  const slot = $(".google-slot", container);
  if (slot) mountGoogleButton(slot, showErr);

  if (!c.email) return;
  const f1 = $(".email-step1", container), f2 = $(".email-step2", container);
  let email = "", timer = null;
  const resend = $(".resend", f2);
  const startTimer = () => {
    let s = 60; clearInterval(timer);
    resend.disabled = true; resend.textContent = t("auth.resendIn", { s });
    timer = setInterval(() => {
      s -= 1;
      if (s <= 0) { clearInterval(timer); resend.disabled = false; resend.textContent = t("auth.resend"); }
      else resend.textContent = t("auth.resendIn", { s });
    }, 1000);
  };
  const requestCode = async () => {
    showErr(null);
    try {
      const res = await apiJson("POST", "/api/auth/email/start", { email, lang: LANG });
      f1.hidden = true; f2.hidden = false;
      $(".sent", f2).textContent = t("auth.codeSent", { email });
      const tc = $(".testcode", f2);
      tc.hidden = !res.test_code; tc.textContent = res.test_code ? t("auth.testMode", { code: res.test_code }) : "";
      f2.code.focus();
      startTimer();
    } catch (e) { showErr(e); }
  };
  f1.addEventListener("submit", async e => {
    e.preventDefault();
    email = f1.email.value.trim();
    const b = $("button", f1); b.disabled = true;
    await requestCode();
    b.disabled = false;
  });
  resend.addEventListener("click", requestCode);
  f2.code.addEventListener("input", () => showErr(null));
  $(".change", f2).addEventListener("click", () => { clearInterval(timer); f2.hidden = true; f1.hidden = false; showErr(null); });
  f2.addEventListener("submit", async e => {
    e.preventDefault();
    showErr(null);
    const b = $("button[type=submit]", f2); b.disabled = true;
    try {
      const res = await apiJson("POST", "/api/auth/email/verify",
        { email, code: f2.code.value.trim(), name: f2.name.value.trim() });
      clearInterval(timer);
      await onSignedIn(res.user);
    } catch (e2) { showErr(e2); } finally { b.disabled = false; }
  });
}

function loadGis() {
  if (!auth.gisLoaded) {
    auth.gisLoaded = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://accounts.google.com/gsi/client";
      s.async = true; s.onload = resolve; s.onerror = reject;
      document.head.appendChild(s);
    });
  }
  return auth.gisLoaded;
}

async function googleCredential(credential, showErr) {
  try {
    const res = await apiJson("POST", "/api/auth/google", { credential });
    await onSignedIn(res.user);
  } catch (e) { showErr(e); }
}

function mountGoogleButton(slot, showErr) {
  if (inAndroidApp()) {
    // Google does not allow its sign-in page inside a WebView: the app shows the native account picker.
    if (typeof window.FamilyChefAndroid.googleSignIn !== "function") { slot.remove(); return; }
    slot.innerHTML = `<button type="button" class="btn google">
      <svg viewBox="0 0 48 48" width="18" height="18" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.5l6.7-6.7C35.6 2.4 30.2 0 24 0 14.6 0 6.6 5.4 2.7 13.2l7.8 6.1C12.4 13.6 17.7 9.5 24 9.5z"/><path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.5 5.8c4.4-4 6.8-10 6.8-17.2z"/><path fill="#FBBC05" d="M10.5 28.7c-.5-1.4-.8-3-.8-4.7s.3-3.2.8-4.7l-7.8-6.1C1 16.6 0 20.2 0 24s1 7.4 2.7 10.8l7.8-6.1z"/><path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.8-5.8l-7.5-5.8c-2.1 1.4-4.8 2.3-8.3 2.3-6.3 0-11.6-4.1-13.5-9.8l-7.8 6.1C6.6 42.6 14.6 48 24 48z"/></svg>
      ${esc(t("auth.google"))}</button>`;
    window.fcAndroidGoogle = token => googleCredential(token, showErr);
    window.fcAndroidGoogleError = code => {
      if (code === "cancelled") return;
      showErr({ code: "google_invalid" });
      const detail = String(code).split("|")[1];
      if (detail) {
        const err = slot.closest(".auth-box")?.querySelector(".error");
        if (err) err.textContent += ` (${detail})`;
      }
    };
    $("button", slot).addEventListener("click", () => window.FamilyChefAndroid.googleSignIn(auth.config.google_client_id));
    return;
  }
  loadGis().then(() => {
    google.accounts.id.initialize({
      client_id: auth.config.google_client_id,
      callback: r => googleCredential(r.credential, showErr),
      ux_mode: "popup",
    });
    google.accounts.id.renderButton(slot, {
      theme: "outline", size: "large", text: "continue_with", shape: "pill", locale: LANG, width: 280,
    });
  }).catch(() => slot.remove());
}

// ------------------------------------------------------------ session state
async function onSignedIn(user) {
  auth.user = user;
  const me = await apiJson("GET", "/api/me");
  auth.settings = me.settings || {};
  setEntitlements(me.entitlements);
  const dlg = $("#loginDialog");
  if (dlg.open) dlg.close();
  await applyServerSettings();
  if (!auth.settings.form) await saveSettings();  // first sign-in: keep what was entered on this device
  updateGate();
  renderAccountButton();
  renderProfile();
}

async function applyServerSettings() {
  const s = auth.settings;
  if (s.prices) for (const [code, ov] of Object.entries(s.prices)) store.set("prices:" + code, ov);
  if (s.form) {
    $("#pantryRows").innerHTML = "";
    if (s.form.region && s.form.region !== state.region?.code) await selectRegion(s.form.region, s.form.city);
    else if (s.form.city) $("#city").value = s.form.city;
    restoreForm(s.form);
    store.set("form", s.form);
  }
}

async function saveSettings() {
  if (!auth.user) return;
  const prices = {};
  for (const r of state.regions) {
    const ov = store.get("prices:" + r.code, null);
    if (ov && Object.keys(ov).length) prices[r.code] = ov;
  }
  auth.settings = { form: readForm(), prices };
  try { await apiJson("PUT", "/api/me", { settings: auth.settings }); } catch { /* offline: retry next time */ }
}

function updateGate() {
  const gated = auth.config.require_login && authEnabled() && !auth.user;
  $("#planForm").hidden = gated;
  $("#authGate").hidden = !gated;
  document.documentElement.classList.toggle("gated", gated);
  if (gated) $("#appTitle").textContent = "FamilyChef";
  else if (typeof refreshShellTexts === "function" && shell) $("#appTitle").textContent = t("tab." + shell.tab);
  if (gated) { $("#result").hidden = true; renderSignIn($("#authGate"), { gate: true }); }
}

function openSignIn() {
  renderSignIn($("#loginBody"));
  $("#loginDialog").showModal();
}

// ------------------------------------------------------------ account panel
async function openAccount() {
  if (isAppMode()) { goTab("profile"); return; }
  if (!auth.user) { openSignIn(); return; }
  await renderAccount($("#accountBody"), () => $("#accountDialog").close());
  $("#accountDialog").showModal();
}

// The account panel: inside the dialog on desktop, on the Profile tab in the app.
async function renderAccount(body, close = () => {}) {
  const u = auth.user;
  body.innerHTML = `
    <h2>${esc(t("acct.title"))}</h2>
    <p class="hint">${esc(t("acct.signedInAs", { who: u.email || u.name }))}</p>
    <form class="row name-form">
      <label class="grow">${esc(t("acct.name"))}<input name="name" value="${esc(u.name)}" maxlength="60" required></label>
      <button class="btn ghost" type="submit">${esc(t("acct.save"))}</button>
    </form>
    <p class="hint small">${esc(t("acct.synced"))}</p>
    <p class="acct-plan"><b>${esc(t("acct.plan"))}:</b> ${plus.ent.plus
      ? "★ " + esc(t("acct.plus", { date: fmtDate(plus.ent.plus_until, false) }))
      : esc(t("acct.free"))}${plus.ent.limits && !plus.ent.plus
      ? ` · <button type="button" class="linkish" id="acctPlusBtn">${esc(t("plan.getPlus"))}</button>` : ""}</p>
    <h3>${esc(t("acct.plans"))}</h3>
    <div class="plans"><p class="hint">…</p></div>
    <div class="row acct-actions">
      <button class="btn ghost logout" type="button">${esc(t("acct.logout"))}</button>
      <button class="btn danger delete-acct" type="button">${esc(t("acct.deleteAccount"))}</button>
    </div>`;

  $("#acctPlusBtn", body)?.addEventListener("click", () => { close(); openPaywall("history"); });
  $(".name-form", body).addEventListener("submit", async e => {
    e.preventDefault();
    const b = $("button", e.target);
    try {
      const res = await apiJson("PUT", "/api/me", { name: e.target.name.value.trim() });
      auth.user = res.user; renderAccountButton(); b.textContent = t("acct.saved");
      setTimeout(() => { b.textContent = t("acct.save"); }, 1500);
    } catch (err) { alert(authErrorText(err)); }
  });
  $(".logout", body).addEventListener("click", async () => {
    await apiJson("POST", "/api/auth/logout").catch(() => {});
    auth.user = null; auth.settings = {};
    forgetLocalPlan();
    close();
    renderAccountButton(); updateGate(); loadEntitlements(); renderProfile();
  });
  $(".delete-acct", body).addEventListener("click", async () => {
    if (!confirm(t("acct.deleteConfirm"))) return;
    await apiJson("DELETE", "/api/me");
    auth.user = null; auth.settings = {};
    forgetLocalPlan();
    close();
    renderAccountButton(); updateGate(); renderProfile();
    alert(t("acct.deleted"));
  });
  await renderPlansList($(".plans", body), close);
}

async function renderPlansList(box, close = () => {}) {
  let plans = [];
  try { plans = await apiJson("GET", "/api/plans"); } catch { /* ignore */ }
  if (!plans.length) { box.innerHTML = `<p class="hint">${esc(t("acct.noPlans"))}</p>`; return; }
  const date = ts => new Date(ts * 1000).toLocaleDateString(LANG, { day: "numeric", month: "short", year: "numeric" });
  const cur = (x, c) => { try { return new Intl.NumberFormat(LANG, { style: "currency", currency: c, maximumFractionDigits: 0 }).format(x); } catch { return `${x} ${c}`; } };
  box.innerHTML = `<ul class="plan-list">${plans.map(p => `
    <li data-id="${p.id}">
      <div><strong>${esc(date(p.created_at))}</strong> · ${esc(p.title)}<br><small>${esc(cur(p.cost, p.currency))}</small></div>
      <div class="row">
        <button class="btn ghost open" type="button">${esc(t("acct.open"))}</button>
        <button class="btn icon del" type="button" aria-label="${esc(t("acct.delete"))}">×</button>
      </div>
    </li>`).join("")}</ul>`;
  box.querySelectorAll("li").forEach(li => {
    const id = li.dataset.id;
    $(".open", li).addEventListener("click", async () => {
      const saved = await apiJson("GET", `/api/plans/${id}`);
      if (saved.result.region.code !== state.region?.code) await selectRegion(saved.result.region.code, saved.request.city);
      state.plan = saved.result;
      renderPlan();
      close();
      showResult();
    });
    $(".del", li).addEventListener("click", async () => {
      await apiJson("DELETE", `/api/plans/${id}`);
      li.remove();
      if (!box.querySelector("li")) box.innerHTML = `<p class="hint">${esc(t("acct.noPlans"))}</p>`;
    });
  });
}

// On sign-out the menu kept on this device goes too (shared phones).
function forgetLocalPlan() {
  store.set("lastPlan", null);
  state.plan = null;
  $("#result").hidden = true;
}

// ------------------------------------------------------------ init
async function initAuth() {
  try { auth.config = await apiJson("GET", "/api/auth/config"); } catch { /* offline */ }
  $("#accountBtn").addEventListener("click", () => (auth.user ? openAccount() : openSignIn()));
  if (authEnabled()) {
    try {
      const me = await apiJson("GET", "/api/me");
      auth.user = me.user; auth.settings = me.settings || {};
      setEntitlements(me.entitlements);
      await applyServerSettings();
    } catch { auth.user = null; }
  }
  renderAccountButton();
  updateGate();
  if (location.pathname === "/account") {
    history.replaceState(null, "", "/");
    auth.user ? openAccount() : (authEnabled() && openSignIn());
  }
}

function refreshAuthTexts() {
  renderAccountButton();
  updateGate();
  renderPlanHint();
}
