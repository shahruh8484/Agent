"""Push campaigns for iGaming landers (PropellerAds), judged by money.

They live in the same campaigns table as the Amazon ones (niche_id 0,
asin "ig<project id>"), so the shared 24h limit, pacing, stats sync and
moderation checks cover them too. The Amazon kill and zone rules skip them;
these rules decide instead, on deposits reported by the partner program's
postback:
- a campaign that spent KILL_SPEND (default 3 x CPA) without a single
  deposit is stopped; one that spent twice that and earned back less than
  half of it too;
- a zone that spent half a CPA without a registration (only when the
  partner program reports registrations at all), or a whole CPA without a
  deposit, is excluded;
(deposits the network rejected after its check don't count). Zones are
judged over all campaigns of the project and excluded from each of them.
- a zone whose visitors don't press the lander's button (ZONE_NO_CLICK_VISITS
  visits, no click) is excluded early, long before it spends a CPA;
- a zone sending bots (clicks held back) is excluded in either mode."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from amzagent.content.llm import LLM, LLMError, parse_json
from amzagent.ig.lander import (COUNTRY_UTC_OFFSET, FIRST_PERSON, FORBIDDEN, LANGUAGES,
                                THEME_BRIEF)
from amzagent.push import propeller
from amzagent.push.ai_creatives import IMAGE_RULES, CreativeError, cut_push_images
from amzagent.push.creatives import render_creatives
from amzagent.push.propeller import DESCRIPTION_MAX, PropellerError
from amzagent.store import ACTIVE, DRY_RUN, ERROR

IG_NICHE = 0
KILL_CPA_MULTIPLE = 3.0
PUSH_TEXT_MAX = DESCRIPTION_MAX - 4  # room for " 18+"
ZONE_NO_CLICK_VISITS = 50  # this many lander visits and nobody pressed the button
ZONE_NO_REG_CPA_SHARE = 0.5
PLATFORMS = {"mobile": "Телефоны и планшеты", "all": "Все устройства", "desktop": "Компьютеры"}


def is_ig(campaign: dict) -> bool:
    return campaign.get("niche_id") == IG_NICHE


def ig_asin(project_id: int) -> str:
    return f"ig{project_id}"


def project_of(store, campaign: dict) -> dict | None:
    asin = str(campaign.get("asin") or "")
    return store.get_ig_project(int(asin[2:])) if asin[2:].isdigit() else None


# --- show hours (the country's local time) ---------------------------------------

def schedule_of(project: dict) -> tuple[int, int] | None:
    """(from, to) local hours the project's campaigns show, or None for all
    day. "to" is exclusive and may be past midnight: (10, 1) = 10:00-01:00."""
    start = int(project.get("hours_from") or 0) % 24
    end = int(project.get("hours_to") or 0) % 24
    return None if start == end else (start, end)


def utc_offset(project: dict) -> int:
    return COUNTRY_UTC_OFFSET.get(project.get("country") or "", 0)


def _open_at(window: tuple[int, int], hour: int) -> bool:
    start, end = window
    return start <= hour < end if start < end else (hour >= start or hour < end)


def in_schedule(project: dict, now: datetime) -> bool:
    window = schedule_of(project)
    return window is None or _open_at(window, (now + timedelta(hours=utc_offset(project))).hour)


def scheduled_minutes(project: dict, start: datetime, end: datetime) -> float:
    """Minutes of [start, end) inside the show hours."""
    window = schedule_of(project)
    if window is None:
        return max(0.0, (end - start).total_seconds() / 60)
    total, t = 0.0, start
    while t < end:  # offsets are whole hours: each UTC hour is open or closed
        nxt = min(end, t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        if _open_at(window, (t + timedelta(hours=utc_offset(project))).hour):
            total += (nxt - t).total_seconds() / 60
        t = nxt
    return total


def ig_pace_allowance(project: dict, budget: float, now: datetime, start: datetime | None,
                      lead_minutes: float) -> float:
    """pace_allowance for show hours: the budget day (UTC) is spread over
    its open minutes only, so the money goes to the hours people play."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since = max(midnight, start) if start else midnight
    day = scheduled_minutes(project, midnight, midnight + timedelta(days=1)) or 1440.0
    done = scheduled_minutes(project, since, now) + lead_minutes
    return budget * min(1.0, max(0.0, done) / day)


def hours_label(project: dict) -> str:
    window = schedule_of(project)
    if window is None:
        return "круглосуточно"
    offset = utc_offset(project)
    return f"{window[0]:02d}:00–{window[1]:02d}:00 (UTC{offset:+d})"


def campaigns_of(store, project_id: int) -> list[dict]:
    return [c for c in store.list_campaigns(niche_id=IG_NICHE) if c["asin"] == ig_asin(project_id)]


def estimated_ig_spend(store, campaign: dict, since: str | None = None) -> float:
    """Real-time lower bound of what a campaign spent (since `since`): every
    visit to the lander is a paid click at the project's bid. PropellerAds'
    own figure lags up to an hour or more."""
    from amzagent.agent.runner import VISITS_PER_PAID_CLICK

    p = project_of(store, campaign)
    bid = p["bid_cpc"] if p else 0.0
    return store.count_ig_visits(campaign["id"], since) * bid / VISITS_PER_PAID_CLICK


def last_open_campaign(store, project_id: int) -> dict | None:
    """The project's newest campaign that ran on all zones (not a whitelist)
    at PropellerAds: its exclude list is the project's current one, with
    every exclusion and return made so far."""
    return next((c for c in campaigns_of(store, project_id)
                 if c["external_id"] and not c.get("zones_only")), None)


def project_excluded_zones(store, project_id: int) -> set[str]:
    """Zones the project has turned off: a new campaign starts without them."""
    last = last_open_campaign(store, project_id)
    return store.blacklisted_zones(last["id"]) if last else set()


def set_zone(deps, project_id: int, zone: str, off: bool) -> str | None:
    """Exclude a zone from (or return it to) every running campaign of a
    project; with none running, from the list the next campaign starts
    with. Returns an error or None."""
    from amzagent.agent.runner import AT_NETWORK, exclude_zone, include_zone

    running = [c for c in campaigns_of(deps.store, project_id)
               if c["status"] in AT_NETWORK and not c.get("zones_only")]
    if not running:
        last = last_open_campaign(deps.store, project_id)
        if last is None:
            return "у проекта ещё не было кампаний"
        if off:
            deps.store.blacklist_zone(last["id"], zone)
        else:
            deps.store.unblacklist_zone(last["id"], zone)
        return None
    for c in running:
        excluded = zone in deps.store.blacklisted_zones(c["id"])
        if off and not excluded:
            error = exclude_zone(deps, c["id"], zone, "manual")
        elif not off and excluded:
            error = include_zone(deps, c["id"], zone)
        else:
            continue
        if error:
            return error
    return None


MAX_CREATIVES = 6
SCENE_SYSTEM = ("You are an art director for push-notification ads of a licensed sports betting "
                "operator. You describe photos for an image model and follow gambling ad rules. "
                "Reply with JSON only.")


def creatives_of(project: dict) -> list[list[str]]:
    try:
        data = json.loads(project.get("creatives") or "[]")
    except ValueError:
        return []
    return [c for c in data if isinstance(c, list) and len(c) == 2]


def media_dir(settings, project_id: int) -> Path:
    return Path(settings.data_dir) / "media" / f"ig{project_id}"


def add_creative(store, settings, project_id: int, png: bytes) -> str:
    """Cut a picture into push image + icon and add it to the project."""
    p = store.get_ig_project(project_id)
    items = creatives_of(p)
    icon, image = cut_push_images(png, media_dir(settings, project_id),
                                  f"ig{project_id}-art-{secrets.token_hex(4)}")
    items.append([icon.name, image.name])
    store.update_ig_project(project_id, creatives=json.dumps(items[-MAX_CREATIVES:]))
    return image.name


def remove_creative(store, project_id: int, image_name: str) -> None:
    p = store.get_ig_project(project_id)
    items = [c for c in creatives_of(p) if c[1] != image_name]
    store.update_ig_project(project_id, creatives=json.dumps(items))


SCENE_SUBJECT = {
    "sport": "about the excitement of watching sport (e.g. adult friends watching a football "
             "match on TV at home, a stadium crowd at night, a football on the pitch under "
             "floodlights, an adult checking a match on a phone on the sofa)",
    "casino": "about an evening of casino-style entertainment at home or in a stylish lounge "
              "(e.g. an adult relaxing on a sofa playing a colourful game on a phone, a roulette "
              "wheel spinning in close-up with soft bokeh lights, an elegant dark lounge with "
              "warm neon accents, a live-dealer style table seen from a player's chair)",
}


def describe_ig_scenes(llm: LLM, project: dict, n: int) -> list[str]:
    theme = project.get("theme") if project.get("theme") in SCENE_SUBJECT else "sport"
    prompt = (
        "TASK: describe betting push photos.\n"
        f"Country: {project.get('country')}. Operator: licensed sports betting and casino site.\n"
        f"Push headline: {project.get('push_title')}\n\n"
        f"Describe {n} clearly different photo scenes {SCENE_SUBJECT[theme]}. Rules: people "
        "clearly adults (30+); no children or teenagers; no money, cash, coins, chips, "
        "jackpots, luxury, winning or celebrating a win; no real players, celebrities, team "
        "crests, jerseys of real clubs, game or brand logos; no alcohol; no text, numbers or "
        "symbols in the image. Return a JSON array of strings, one short paragraph each."
    )
    data = parse_json(llm.generate(SCENE_SYSTEM, prompt, max_tokens=800))
    scenes = [x for x in data if isinstance(x, str) and x.strip()] if isinstance(data, list) else []
    if not scenes:
        raise LLMError("No image scenes in model reply")
    return scenes[:n]


def draw_ig_creatives(deps, project_id: int, n: int = 3) -> int:
    """AI-drawn pictures for a project's pushes. Returns how many were made."""
    if deps.painter is None or deps.llm is None:
        raise CreativeError("рисование картинок не настроено (нужен OPENAI_API_KEY)")
    p = deps.store.get_ig_project(project_id)
    made, errors = 0, []
    for scene in describe_ig_scenes(deps.llm, p, n):
        try:
            png = deps.painter.draw(scene + IMAGE_RULES)
            add_creative(deps.store, deps.settings, project_id, png)
            made += 1
        except (CreativeError, OSError, ValueError) as exc:
            errors.append(str(exc))
    if not made:
        raise CreativeError("; ".join(errors) or "картинки не получились")
    return made


def write_push_text(llm: LLM, project: dict) -> tuple[str, str]:
    language = project.get("language") or "pt"
    prompt = (
        "TASK: write a betting push notification.\n"
        f"Theme: {THEME_BRIEF.get(project.get('theme') or 'sport', THEME_BRIEF['sport'])}\n"
        f"Language: {LANGUAGES.get(language, language)}\n"
        f"Operator (licensed): {project.get('brand') or project.get('name')}\n"
        f"Offer details (the only facts you may use):\n{(project.get('offer') or '')[:1500]}\n\n"
        "Rules: adults only; never promise winning, profit or income; no 'guaranteed', "
        "'risk-free', no fake urgency, no ALL CAPS; at most one emoji. Mention the welcome "
        "bonus only as the offer details describe it. You are an independent affiliate, not "
        "the operator: name the operator, never 'our casino', 'we offer' or other first person.\n"
        f"title: max 30 chars; text: max {PUSH_TEXT_MAX} chars (\"18+\" is added after it).\n"
        'Return JSON: {"title": "...", "text": "..."}'
    )
    data = parse_json(llm.generate("You write compliant gambling ad copy.", prompt,
                                   max_tokens=300))
    if not isinstance(data, dict) or not data.get("title"):
        raise LLMError("Expected a JSON object with title and text")
    return str(data["title"])[:30], str(data.get("text", ""))[:PUSH_TEXT_MAX]


def push_text(project: dict) -> str:
    text = (project.get("push_text") or "").strip()
    if "18+" in text:
        return text[:DESCRIPTION_MAX]
    return f"{text[:PUSH_TEXT_MAX].rstrip()} 18+".strip()


def forbidden_in_push(project: dict) -> list[str]:
    language = project.get("language")
    low = f" {project.get('push_title', '')} {project.get('push_text', '')} ".lower()
    words = FORBIDDEN.get(language, ()) + FORBIDDEN["en"] + FIRST_PERSON.get(language, ())
    return sorted({w.strip() for w in words if w in low})


def _os_types(deps, platform: str) -> list | None:
    from amzagent.agent.runner import platform_os_types  # runner imports this module

    class _View:  # platform_os_types reads deps.settings.push_platform
        def __init__(self):
            self.settings = deps.settings.model_copy(update={"push_platform": platform})
            self.store, self.push = deps.store, deps.push
            self.say, self.say_once = deps.say, deps.say_once

    return platform_os_types(_View())


def deposit_zones(store, campaign_id: int) -> list[str]:
    """Zones of a campaign whose players made deposits the network kept."""
    _, zones = store.ig_campaign_stats(campaign_id)
    good = [(z, v["ftd"] - v["rej"]) for z, v in zones.items() if v["ftd"] - v["rej"] > 0]
    return [z for z, _ in sorted(good, key=lambda x: -x[1]) if z.isdigit()]


def launch_ig_campaign(deps, project_id: int, zones: list[str] | None = None) -> str | None:
    """New push campaign to the project's lander; with `zones`, a whitelist
    that runs only there (the owner's pick: the kill rule leaves it alone).
    The shared 24h limit applies as for any launch. Returns an error or None."""
    from amzagent.agent.runner import committed_24h, media_file

    s, store = deps.settings, deps.store
    p = store.get_ig_project(project_id)
    if p is None:
        return "проект не найден"
    if not p["domain"]:
        return "у проекта нет домена лендинга"
    if "{click_id}" not in p["offer_url"]:
        return "в ссылке оффера нет {click_id} — депозиты не свяжутся с кампанией"
    if not (p["push_title"] or "").strip():
        return "нет текста пуша — нажмите «Агент: написать пуш» или впишите свой"
    bad = forbidden_in_push(p)
    if bad:
        return "в тексте пуша запрещённые слова: " + ", ".join(bad)
    budget = max(p["daily_budget"], propeller.MIN_DAILY_AMOUNT)
    if s.push_live:
        if deps.push is None:
            return "PropellerAds не подключён"
        committed = committed_24h(store)
        if committed is None:
            return "расход за 24 ч ещё не получен из PropellerAds — попробуйте через пару минут"
        if committed + budget > s.max_daily_spend:
            return (f"не хватает дневного лимита: занято ${committed:.2f} из "
                    f"${s.max_daily_spend:.2f} (поднимите лимит в настройках)")
    # A campaign on all zones starts without the ones the project turned off.
    excluded = sorted(project_excluded_zones(store, project_id)) if not zones else []
    cid = store.add_campaign(IG_NICHE, ig_asin(project_id), "creating", budget)
    for zone in excluded:
        store.blacklist_zone(cid, zone)
    if zones:
        store.update_campaign(cid, zones_only=",".join(zones), manual_keep=1)
    slug = f"ig{project_id}"
    base = f"{s.public_base_url()}/media/{slug}"
    pictures = creatives_of(p)
    if not pictures:  # no pictures of its own yet: the plain generated one
        name = f"ig{project_id}-c{cid}"
        render_creatives(media_dir(s, project_id), name, p["brand"] or p["name"],
                         p["push_title"])
        pictures = [[f"{name}-icon.png", f"{name}-image.png"]]
    payload = propeller.build_campaign_payload(
        name=f"iGaming {p['name']} #{cid}",
        target_url=f"https://{p['domain']}/?c={cid}&z={propeller.ZONE_MACRO}",
        title=p["push_title"], text=push_text(p),
        images=[(f"{base}/{icon}", f"{base}/{image}") for icon, image in pictures],
        countries=[p["country"].lower()], bid_cpc=p["bid_cpc"], daily_budget=budget,
        os_types=_os_types(deps, p["platform"]), zones=zones, excluded=excluded,
    )
    if not s.push_live:
        store.update_campaign(cid, status=DRY_RUN, payload=payload,
                              note="dry run: not sent (PUSH_LIVE=false)")
        deps.say(f"[iGaming {p['name']}] dry-run campaign #{cid}")
        return None
    try:
        external_id = deps.push.create_campaign(
            propeller.inline_images(payload, lambda url: media_file(deps, url)))
    except PropellerError as exc:
        store.update_campaign(cid, status=ERROR, payload=payload, note=str(exc)[:500])
        deps.say(f"[iGaming {p['name']}] campaign #{cid} failed: {exc}")
        return f"PropellerAds не создал кампанию: {exc}"
    store.update_campaign(cid, status=ACTIVE, external_id=external_id, payload=payload,
                          note="sent to PropellerAds moderation")
    deps.say(f"[iGaming {p['name']}] launched campaign #{cid} (PropellerAds {external_id}), "
             f"{p['country']}, ${budget:.2f}/day, bid ${p['bid_cpc']:.3f}"
             + (f", only zones {', '.join(zones)}" if zones else "")
             + (f", {len(excluded)} zones excluded" if excluded else ""))
    return None


def project_zones(store, project_id: int) -> tuple[dict[str, dict], dict[str, float]]:
    """Per zone over all campaigns of a project: ({zone: visit/click/bot/reg/
    ftd/rej counts}, {zone: spend at PropellerAds})."""
    counts = {r["zone"]: {"visit": r["visits"] or 0, "click": r["clicks"] or 0,
                          "bot": r["bots"] or 0, "reg": r["regs"] or 0, "ftd": r["ftds"] or 0,
                          "rej": r["rejs"] or 0}
              for r in store.ig_zone_stats(project_id, limit=100_000)}
    spent: dict[str, float] = {}
    for c in campaigns_of(store, project_id):
        for z in store.zone_stats(c["id"]):
            spent[z["zone"]] = spent.get(z["zone"], 0.0) + z["spent"]
    return counts, spent


def apply_ig_rules(deps, manual: bool) -> None:
    """Kill and zone rules for iGaming campaigns (see the module docstring).
    Bot zones go in both modes; the rest only in auto mode (zone pruning
    also in manual mode when manual_prune_zones is on, the kill rules when
    ig_manual_kill is on)."""
    from amzagent.agent.runner import (
        AT_NETWORK, KILLED, STOPPED, exclude_zone, stop_campaign)

    s, store = deps.settings, deps.store
    # Paused ones too (budget pacing, 24h limit): they resume on their own,
    # and should come back without the zones already judged bad.
    for c in store.list_campaigns(niche_id=IG_NICHE, statuses=AT_NETWORK):
        if not c["external_id"]:
            continue
        p = project_of(store, c)
        if p is None:
            stop_campaign(deps, c, STOPPED, "iGaming project deleted")
            continue
        total, _ = store.ig_campaign_stats(c["id"])
        # Zones are judged on the whole project: same lander, same offer, so a
        # zone that failed in one campaign is excluded from the others too.
        zones, zone_spent_all = project_zones(store, p["id"])
        excluded = store.blacklisted_zones(c["id"])
        whitelist = bool(c.get("zones_only"))
        reasons: dict[str, str] = {}
        if s.bot_zone_min > 0:
            for zone, z in zones.items():
                if z["bot"] >= s.bot_zone_min and z["bot"] >= z["click"]:
                    reasons[zone] = f"bots: {z['bot']} clicks held back, {z['click']} real"
        payout = p["payout"]
        # Some offers report only the paid deposit (Actionpay CPA): without
        # registrations in the postbacks, "no registration" means nothing.
        reports_regs = store.ig_stats(p["id"])["reg"] > 0
        prune = payout > 0 and not whitelist and (not manual or s.manual_prune_zones)
        if not whitelist and (not manual or s.manual_prune_zones):
            # Early signal, long before a zone spends a whole CPA: people from
            # it don't even press the lander's button.
            for zone, z in zones.items():
                if (zone not in reasons and z["visit"] >= ZONE_NO_CLICK_VISITS
                        and z["click"] == 0 and z["ftd"] == 0):
                    reasons[zone] = f"{z['visit']} lander visits, nobody pressed the button"
        if prune:
            for zone, zone_spent in zone_spent_all.items():
                z = zones.get(zone) or {"reg": 0, "ftd": 0, "rej": 0}
                if zone in reasons:
                    continue
                if z["ftd"] - z["rej"] <= 0 and zone_spent >= payout:
                    reasons[zone] = f"${zone_spent:.2f} spent (>= CPA ${payout:.2f}), no deposits"
                elif (reports_regs and z["reg"] == 0
                      and zone_spent >= payout * ZONE_NO_REG_CPA_SHARE):
                    reasons[zone] = f"${zone_spent:.2f} spent, no registrations"
        for zone, reason in reasons.items():
            if zone not in excluded:
                error = exclude_zone(deps, c["id"], zone, reason)
                if error:
                    deps.say(f"campaign #{c['id']}: zone exclude failed: {error}")
        if (manual and not s.ig_manual_kill) or c.get("manual_keep") or payout <= 0:
            continue
        limit = p["kill_spend"] or payout * KILL_CPA_MULTIPLE
        spend = max(c["spend"], estimated_ig_spend(store, c))
        if spend >= limit and total["ftd"] - total["rej"] <= 0:
            stop_campaign(deps, c, KILLED, f"spent ${spend:.2f} (limit ${limit:.2f}), no deposits")
        elif spend >= 2 * limit and total["revenue"] < spend / 2:
            stop_campaign(deps, c, KILLED, f"spent ${spend:.2f}, earned ${total['revenue']:.2f}"
                                           " (less than half back)")


# --- advice for the panel chat (see agent/advice.py) ---------------------------

LANDER_MIN_VISITS = 200
LANDER_MIN_CTR = 0.05
REGS_WITHOUT_DEPOSITS = 5
POSTBACK_SILENT_CLICKS = 300


def collect_ig_advice(settings, store, manual: bool) -> list[tuple[str, str]]:
    """(key, text) for iGaming campaigns: what the owner could do that the
    agent won't do by itself."""
    from amzagent.agent.runner import CAPPED, PACED

    out: list[tuple[str, str]] = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for p in store.list_ig_projects():
        payout = p["payout"]
        st = store.ig_stats(p["id"])
        name = f"iGaming «{p['name']}»"
        if st["click"] >= POSTBACK_SILENT_CLICKS and not (st["reg"] or st["ftd"] or st["rej"]):
            out.append((f"ig-silent:{p['id']}",
                        f"{name}: {st['click']} кликов на оффер и ни одного события из "
                        f"партнёрки. Проверьте постбэк (адрес, {{subid1}} / click_id, что он "
                        f"активен) и что в ссылке оффера стоит {{click_id}}."))
        if st["visit"] >= LANDER_MIN_VISITS and st["click"] / st["visit"] < LANDER_MIN_CTR:
            out.append((f"ig-ctr:{p['id']}:{st['visit'] // 500}",
                        f"{name}: на кнопку лендинга нажимают только {st['click']} из "
                        f"{st['visit']} ({st['click'] / st['visit']:.1%}). Попробуйте "
                        f"переписать лендинг или текст кнопки, проверьте, быстро ли он "
                        f"открывается на телефоне."))
        if st["reg"] >= REGS_WITHOUT_DEPOSITS and st["ftd"] == 0:
            out.append((f"ig-nodep:{p['id']}:{st['reg'] // 10}",
                        f"{name}: {st['reg']} регистраций и ни одного засчитанного депозита. "
                        f"Возможно, порог депозита оффера слишком высокий для пушей — "
                        f"спросите менеджера об оффере с меньшим минимальным депозитом."))
        if st["rej"] >= 2 and st["rej"] >= 0.3 * max(st["ftd"], 1):
            out.append((f"ig-rej:{p['id']}:{st['rej']}",
                        f"{name}: сеть отклонила {st['rej']} из {st['ftd']} депозитов. "
                        f"Посмотрите в партнёрке причину и какие площадки их дали — такие "
                        f"площадки лучше исключить."))
        for c in campaigns_of(store, p["id"]):
            if c["status"] not in (ACTIVE, PACED, CAPPED) or not c["external_id"]:
                continue
            total, zones = store.ig_campaign_stats(c["id"])
            cname = f"{name}, кампания #{c['id']}"
            wins = [z for z in deposit_zones(store, c["id"])
                    if z not in (c.get("zones_only") or "").split(",")]
            if len(wins) >= 2 and not c.get("zones_only"):
                shown = ", ".join(f"{z} ({zones[z]['ftd'] - zones[z]['rej']} деп.)"
                                  for z in wins[:6])
                out.append((f"ig-win:{c['id']}:{','.join(sorted(wins))}",
                            f"{cname}: площадки {shown} дали депозиты. Советую запустить "
                            f"вайт-лист только на них (кнопка «Вайт-лист из площадок с "
                            f"депозитами» у кампании)."))
            profit = total["revenue"] - c["spend"]
            if c["status"] in (PACED, CAPPED) and payout and c["spend"] >= payout and profit > 0:
                out.append((f"ig-paced:{c['id']}:{today}",
                            f"{cname} в плюсе (${profit:.2f}), но стоит на паузе "
                            f"({'растягиваю бюджет' if c['status'] == PACED else 'лимит 24 ч'}). "
                            f"Можно поднять бюджет проекта или общий лимит."))
            if ((manual or c.get("manual_keep")) and payout
                    and c["spend"] >= KILL_CPA_MULTIPLE * payout
                    and total["revenue"] < c["spend"] / 2):
                out.append((f"ig-lose:{c['id']}:{today}",
                            f"{cname} в минусе: потрачено ${c['spend']:.2f}, доход "
                            f"${total['revenue']:.2f}. Агент её не отключает (ручной режим или "
                            f"возвращена вручную) — советую остановить или оставить вайт-лист."))
    return out
