"use strict";
// Free plan vs FamilyChef Plus on the page. The server enforces the same rules (limits.py);
// here we only show the right buttons and the Plus screen.

const plus = {
  ent: { limits: false, unlimited: true, max_days: 7, plus: false, plus_until: 0, next_free_at: null },
};

function setEntitlements(ent) {
  if (ent) plus.ent = ent;
  renderPlanHint();
  const free = !plus.ent.unlimited;
  $$("#shopping .price input").forEach(i => { i.readOnly = free; });
}

function isFree() { return !plus.ent.unlimited; }

function fmtDate(ts, withWeekday = true) {
  const opts = { day: "numeric", month: "long" };
  if (withWeekday) opts.weekday = "long";
  return new Date(ts * 1000).toLocaleDateString(LANG, opts);
}

function renderPlanHint() {
  const box = $("#planHint");
  const e = plus.ent;
  if (!e.limits) { box.hidden = true; return; }
  box.hidden = false;
  if (e.plus) {
    box.className = "plan-hint plus";
    box.textContent = "★ " + t("plan.plusHint", { date: fmtDate(e.plus_until, false) });
    return;
  }
  box.className = "plan-hint";
  if (e.next_free_at) {
    box.innerHTML = `<p>${esc(t("plan.freeUsed", { date: fmtDate(e.next_free_at) }))}</p>
      <div class="row"><button type="button" class="btn ghost" id="openLastBtn">${esc(t("plan.openLast"))}</button>
      <button type="button" class="btn primary" id="getPlusBtn">${esc(t("plan.getPlus"))}</button></div>`;
    $("#openLastBtn").addEventListener("click", openLastPlan);
  } else {
    box.innerHTML = `<p>${esc(t("plan.freeHint"))}</p>
      <button type="button" class="linkish" id="getPlusBtn">${esc(t("plan.getPlus"))} →</button>`;
  }
  $("#getPlusBtn").addEventListener("click", () => openPaywall("week"));
}

async function openLastPlan() {
  try {
    const list = await apiJson("GET", "/api/plans");
    if (!list.length) return;
    const saved = await apiJson("GET", `/api/plans/${list[0].id}`);
    if (saved.result.region.code !== state.region?.code) await selectRegion(saved.result.region.code, saved.request.city);
    state.plan = saved.result;
    renderPlan();
    showResult();
  } catch { /* ignore */ }
}

function openPaywall(reason, extra = {}) {
  const lead = reason === "free_used"
    ? t("pw.free_used", { date: extra.next_free_at ? fmtDate(extra.next_free_at) : "" })
    : t("pw." + (reason || "week"));
  $("#paywallBody").innerHTML = `
    <div class="paywall">
      <div class="pw-badge">★</div>
      <h2>${esc(t("pw.title"))}</h2>
      <p class="lead">${esc(lead)}</p>
      <ul>${["pw.f1", "pw.f2", "pw.f3", "pw.f4", "pw.f5"].map(k => `<li>${esc(t(k))}</li>`).join("")}</ul>
      <p class="hint">${esc(t("pw.soon"))}</p>
      <form method="dialog"><button class="btn primary big">${esc(t("pw.ok"))}</button></form>
    </div>`;
  $("#paywallDialog").showModal();
}

// Called by app.js when the server answers 402.
function handleLimitError(e) {
  const d = e.data || {};
  if (d.detail === "free_used") {
    plus.ent.next_free_at = d.next_free_at;
    renderPlanHint();
    openPaywall("free_used", d);
    return true;
  }
  if (d.detail === "plus_required") { openPaywall(d.feature); return true; }
  return false;
}

async function loadEntitlements() {
  try { setEntitlements(await apiJson("GET", "/api/entitlements")); } catch { /* offline */ }
}
