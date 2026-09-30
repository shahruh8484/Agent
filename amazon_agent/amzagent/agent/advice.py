"""Recommendations the agent posts to the panel chat.

Rule-based (no LLM calls): once an hour the agent looks at campaigns and
zones and writes, in Russian, what the owner could do that the agent won't
do by itself — a dead zone in a whitelist, zones worth a whitelist, a
profitable campaign held back by pacing, a campaign losing money that the
rules leave running, a product whose bonus budget is running out. Each
piece of advice is posted once (per its key); nothing is changed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from amzagent.store import ACTIVE

ADVICE_FLAG = "advice_at"
ADVICE_EVERY_MINUTES = 60
ADVICE_PREFIX = "💡 Советы агента"
WIN_MIN_VISITS = 20  # a zone needs this many visits before it's called a winner
WIN_MIN_CLICKS = 3
LOSE_MIN_VISITS = 50
PACED_MIN_VISITS = 50


def _sent(store, key: str) -> bool:
    return bool(store.get_flag(f"advice:{key}"))


def _mark(store, key: str) -> None:
    store.set_flag(f"advice:{key}", datetime.now(timezone.utc).isoformat(timespec="seconds"))


def _fmt_time(t: dict | None) -> str:
    if not t:
        return ""
    if t["avg"] < 1:
        return ", и никто не задержался на странице (похоже на ботов)"
    return f", в среднем {int(t['avg']) // 60}:{int(t['avg']) % 60:02d} на странице"


def collect_advice(settings, store, manual: bool) -> list[tuple[str, str]]:
    """(key, text) for every piece of advice that applies right now."""
    from amzagent.agent.runner import CAPPED, PACED

    out: list[tuple[str, str]] = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cpc = settings.push_bid_cpc
    running = store.list_campaigns(statuses=(ACTIVE, PACED, CAPPED))
    whitelisted = {(c["asin"], z) for c in running if c.get("zones_only")
                   for z in c["zones_only"].split(",")}
    for c in running:
        if not c["external_id"] or c["niche_id"] == 0:  # iGaming: judged by deposits
            continue
        found = store.get_product(c["niche_id"], c["asin"])
        product = found[0] if found else None
        name = f"#{c['id']} ({(product.title if product else c['asin'])[:40]})"
        epc = product.epc if product else None
        breakeven = cpc / epc if epc else None  # share of visitors that must go to Amazon

        visits = store.events_by_zone(c["id"], "visit")
        clicks = store.events_by_zone(c["id"], "click")
        times = store.time_on_site(c["id"])
        excluded = store.blacklisted_zones(c["id"])

        # 1. Dead zones the agent leaves alone (whitelists; manual mode
        #    without zone pruning).
        agent_prunes = not c.get("zones_only") and (not manual or settings.manual_prune_zones)
        if not agent_prunes:
            for zone, n in sorted(visits.items(), key=lambda x: -x[1]):
                if zone in excluded or clicks.get(zone) or n < settings.zone_min_visits:
                    continue
                out.append((f"dead:{c['id']}:{zone}",
                            f"Кампания {name}: зона {zone} — {n} визитов и ни одного перехода "
                            f"на Amazon{_fmt_time(times.get(zone))}. Советую отключить её "
                            f"(«Зоны ▾» → «Отключить»)."))

        # 2. Zones good enough for a whitelist.
        if not c.get("zones_only"):
            wins = []
            for zone, n in visits.items():
                k = clicks.get(zone, 0)
                rate = k / n if n else 0
                if (zone not in excluded and n >= WIN_MIN_VISITS and k >= WIN_MIN_CLICKS
                        and rate >= max(0.05, 2 * (breakeven or 0))
                        and (c["asin"], zone) not in whitelisted):
                    wins.append((zone, k, n, rate))
            if wins:
                wins.sort(key=lambda w: -w[3])
                listed = ", ".join(f"{z} ({k} из {n}, {r:.0%})" for z, k, n, r in wins[:6])
                out.append((f"win:{c['id']}:{','.join(sorted(w[0] for w in wins))}",
                            f"Кампания {name}: зоны {listed} дают много переходов на Amazon. "
                            f"Советую создать вайт-лист с ними («Зоны ▾» → отметить → "
                            f"«Создать кампанию только с отмеченными зонами»)."))

        total_visits = sum(visits.values())
        total_clicks = sum(clicks.values())
        rate = total_clicks / total_visits if total_visits else 0

        # 3. Profitable but held back by pacing.
        if (c["status"] == PACED and breakeven and total_visits >= PACED_MIN_VISITS
                and rate >= 1.5 * breakeven):
            out.append((f"paced:{c['id']}:{today}",
                        f"Кампания {name} выгодная ({rate:.1%} переходов при безубыточных "
                        f"{breakeven:.1%}), но стоит на паузе «растягиваю бюджет». Можно "
                        f"снять галочку «Растягивать бюджет» или поднять бюджет кампании."))

        # 4. Losing money where the kill rules don't act (manual mode or
        #    campaigns kept by hand).
        if ((manual or c.get("manual_keep")) and breakeven and total_visits >= LOSE_MIN_VISITS
                and c["spend"] >= 2 * settings.kill_min_spend and rate < 0.5 * breakeven):
            out.append((f"lose:{c['id']}:{today}",
                        f"Кампания {name} в минусе: {rate:.1%} переходов на Amazon при "
                        f"безубыточных {breakeven:.1%} (EPC ${epc:.2f}, клик ${cpc:.3f}), "
                        f"потрачено ${c['spend']:.2f}. Советую остановить или оставить только "
                        f"лучшие зоны вайт-листом."))

        # 5. Creator Connections bonus budget running out.
        budget = (product.cc_budget or "").lower() if product else ""
        if budget in ("low", "medium"):
            out.append((f"budget:{c['asin']}:{budget}",
                        f"Кампания {name}: бюджет бонусов Creator Connections у товара "
                        f"«{budget}». "
                        + ("Бонус скоро закончится — остановите кампанию или перенесите её "
                           "зоны на похожий товар с бюджетом High."
                           if budget == "low" else
                           "Он начал заканчиваться: держите наготове похожий товар с бюджетом "
                           "High, чтобы перенести на него зоны.")))

        # 6. EPC too low for push clicks to pay off.
        if epc and cpc / epc > 0.10:
            out.append((f"epc:{c['id']}",
                        f"Кампания {name}: EPC всего ${epc:.2f} — чтобы окупить клик за "
                        f"${cpc:.3f}, на Amazon должен переходить каждый "
                        f"{int(round(epc / cpc))}-й посетитель ({cpc / epc:.0%}). "
                        f"Обычно это нереально; лучше товары с EPC от $1."))
    return out


def post_advice(deps, force: bool = False) -> int:
    """Post new advice to the chat (at most once an hour). Returns how many."""
    from amzagent.agent.runner import is_manual

    s, store = deps.settings, deps.store
    if not s.agent_advice:
        return 0
    now = datetime.now(timezone.utc)
    if not force:
        try:
            last = datetime.fromisoformat(store.get_flag(ADVICE_FLAG))
        except ValueError:
            last = None
        if last and now - last < timedelta(minutes=ADVICE_EVERY_MINUTES):
            return 0
    store.set_flag(ADVICE_FLAG, now.isoformat(timespec="seconds"))
    fresh = [(k, t) for k, t in collect_advice(s, store, is_manual(store)) if not _sent(store, k)]
    if not fresh:
        return 0
    store.add_chat("assistant", ADVICE_PREFIX + ":\n" + "\n".join(f"• {t}" for _, t in fresh)
                   + "\n\nНапишите «сделай», если хотите, чтобы я выполнил что-то из этого.")
    for key, _ in fresh:
        _mark(store, key)
    deps.say(f"posted {len(fresh)} recommendation(s) to the chat")
    return len(fresh)
