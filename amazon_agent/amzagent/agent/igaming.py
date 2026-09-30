"""Push campaigns for iGaming landers (PropellerAds), judged by money.

They live in the same campaigns table as the Amazon ones (niche_id 0,
asin "ig<project id>"), so the shared 24h limit, pacing, stats sync and
moderation checks cover them too. The Amazon kill and zone rules skip them;
these rules decide instead, on deposits reported by the partner program's
postback:
- a campaign that spent KILL_SPEND (default 3 x CPA) without a single
  deposit is stopped; one that spent twice that and earned back less than
  half of it too;
- a zone that spent half a CPA without a registration, or a whole CPA
  without a deposit, is excluded;
(deposits the network rejected after its check don't count)
- a zone sending bots (clicks held back) is excluded in either mode."""
from __future__ import annotations

from pathlib import Path

from amzagent.content.llm import LLM, LLMError, parse_json
from amzagent.ig.lander import FORBIDDEN, LANGUAGES
from amzagent.push import propeller
from amzagent.push.creatives import render_creatives
from amzagent.push.propeller import PropellerError
from amzagent.store import ACTIVE, DRY_RUN, ERROR

IG_NICHE = 0
KILL_CPA_MULTIPLE = 3.0
ZONE_NO_REG_CPA_SHARE = 0.5
PLATFORMS = {"mobile": "Телефоны и планшеты", "all": "Все устройства", "desktop": "Компьютеры"}


def is_ig(campaign: dict) -> bool:
    return campaign.get("niche_id") == IG_NICHE


def ig_asin(project_id: int) -> str:
    return f"ig{project_id}"


def project_of(store, campaign: dict) -> dict | None:
    asin = str(campaign.get("asin") or "")
    return store.get_ig_project(int(asin[2:])) if asin[2:].isdigit() else None


def campaigns_of(store, project_id: int) -> list[dict]:
    return [c for c in store.list_campaigns(niche_id=IG_NICHE) if c["asin"] == ig_asin(project_id)]


def write_push_text(llm: LLM, project: dict) -> tuple[str, str]:
    language = project.get("language") or "pt"
    prompt = (
        "TASK: write a betting push notification.\n"
        f"Language: {LANGUAGES.get(language, language)}\n"
        f"Operator (licensed): {project.get('brand') or project.get('name')}\n"
        f"Offer details (the only facts you may use):\n{(project.get('offer') or '')[:1500]}\n\n"
        "Rules: adults only; never promise winning, profit or income; no 'guaranteed', "
        "'risk-free', no fake urgency, no ALL CAPS; at most one emoji. Mention the welcome "
        "bonus only as the offer details describe it.\n"
        "title: max 30 chars; text: max 50 chars (\"18+\" is added after it).\n"
        'Return JSON: {"title": "...", "text": "..."}'
    )
    data = parse_json(llm.generate("You write compliant gambling ad copy.", prompt,
                                   max_tokens=300))
    if not isinstance(data, dict) or not data.get("title"):
        raise LLMError("Expected a JSON object with title and text")
    return str(data["title"])[:30], str(data.get("text", ""))[:50]


def push_text(project: dict) -> str:
    text = (project.get("push_text") or "").strip()
    return text if "18+" in text else f"{text} 18+".strip()


def forbidden_in_push(project: dict) -> list[str]:
    low = f"{project.get('push_title', '')} {project.get('push_text', '')}".lower()
    return sorted({w.strip() for w in FORBIDDEN.get(project.get("language"), ()) + FORBIDDEN["en"]
                   if w in low})


def _os_types(deps, platform: str) -> list | None:
    from amzagent.agent.runner import platform_os_types  # runner imports this module

    class _View:  # platform_os_types reads deps.settings.push_platform
        def __init__(self):
            self.settings = deps.settings.model_copy(update={"push_platform": platform})
            self.store, self.push = deps.store, deps.push
            self.say, self.say_once = deps.say, deps.say_once

    return platform_os_types(_View())


def launch_ig_campaign(deps, project_id: int) -> str | None:
    """New push campaign to the project's lander. The shared 24h limit
    applies as for any launch. Returns an error or None."""
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
    cid = store.add_campaign(IG_NICHE, ig_asin(project_id), "creating", budget)
    slug = f"ig{project_id}"
    name = f"ig{project_id}-c{cid}"
    render_creatives(Path(s.data_dir) / "media" / slug, name, p["brand"] or p["name"],
                     p["push_title"])
    base = f"{s.public_base_url()}/media/{slug}"
    payload = propeller.build_campaign_payload(
        name=f"iGaming {p['name']} #{cid}",
        target_url=f"https://{p['domain']}/?c={cid}&z={propeller.ZONE_MACRO}",
        title=p["push_title"], text=push_text(p),
        images=[(f"{base}/{name}-icon.png", f"{base}/{name}-image.png")],
        countries=[p["country"].lower()], bid_cpc=p["bid_cpc"], daily_budget=budget,
        os_types=_os_types(deps, p["platform"]),
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
             f"{p['country']}, ${budget:.2f}/day, bid ${p['bid_cpc']:.3f}")
    return None


def apply_ig_rules(deps, manual: bool) -> None:
    """Kill and zone rules for iGaming campaigns (see the module docstring).
    Bot zones go in both modes; the rest only in auto mode (zone pruning
    also in manual mode when manual_prune_zones is on)."""
    from amzagent.agent.runner import STOPPED, KILLED, exclude_zone, stop_campaign

    s, store = deps.settings, deps.store
    for c in store.list_campaigns(niche_id=IG_NICHE, statuses=(ACTIVE,)):
        if not c["external_id"]:
            continue
        p = project_of(store, c)
        if p is None:
            stop_campaign(deps, c, STOPPED, "iGaming project deleted")
            continue
        total, zones = store.ig_campaign_stats(c["id"])
        excluded = store.blacklisted_zones(c["id"])
        whitelist = bool(c.get("zones_only"))
        reasons: dict[str, str] = {}
        if s.bot_zone_min > 0:
            for zone, z in zones.items():
                if z["bot"] >= s.bot_zone_min and z["bot"] >= z["click"]:
                    reasons[zone] = f"bots: {z['bot']} clicks held back, {z['click']} real"
        payout = p["payout"]
        prune = payout > 0 and not whitelist and (not manual or s.manual_prune_zones)
        if prune:
            spent = {z["zone"]: z["spent"] for z in store.zone_stats(c["id"])}
            for zone, zone_spent in spent.items():
                z = zones.get(zone) or {"reg": 0, "ftd": 0, "rej": 0}
                if zone in reasons:
                    continue
                if z["ftd"] - z["rej"] <= 0 and zone_spent >= payout:
                    reasons[zone] = f"${zone_spent:.2f} spent (>= CPA ${payout:.2f}), no deposits"
                elif z["reg"] == 0 and zone_spent >= payout * ZONE_NO_REG_CPA_SHARE:
                    reasons[zone] = f"${zone_spent:.2f} spent, no registrations"
        for zone, reason in reasons.items():
            if zone not in excluded:
                error = exclude_zone(deps, c["id"], zone, reason)
                if error:
                    deps.say(f"campaign #{c['id']}: zone exclude failed: {error}")
        if manual or c.get("manual_keep") or payout <= 0:
            continue
        limit = p["kill_spend"] or payout * KILL_CPA_MULTIPLE
        spend = c["spend"]
        if spend >= limit and total["ftd"] - total["rej"] <= 0:
            stop_campaign(deps, c, KILLED, f"spent ${spend:.2f} (limit ${limit:.2f}), no deposits")
        elif spend >= 2 * limit and total["revenue"] < spend / 2:
            stop_campaign(deps, c, KILLED, f"spent ${spend:.2f}, earned ${total['revenue']:.2f}"
                                           " (less than half back)")
