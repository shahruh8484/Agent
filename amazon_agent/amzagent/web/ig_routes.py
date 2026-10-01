"""Panel tab "iGaming": projects (a licensed operator's offer + our lander on
the project's own domain), lander copy, click tracking and postbacks from
the partner program. Push campaigns for these landers come in the next stage.

Public routes (on the project's domain the app maps "/" and "/go" here):
  /l/{id}/        the lander (on the panel domain only as the owner's preview)
  /l/{id}/go      logs a click with a new click_id, redirects to the offer
  /pb/ig          postback: the partner program reports reg / ftd / dep
  /caddy/ask      Caddy asks whether to get a certificate for a domain
"""
from __future__ import annotations

import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile

from amzagent.agent.igaming import (
    PLATFORMS,
    campaigns_of,
    add_creative,
    creatives_of,
    deposit_zones,
    draw_ig_creatives,
    remove_creative,
    set_zone,
    forbidden_in_push,
    launch_ig_campaign,
    write_push_text,
)
from amzagent.agent.runner import (
    KILLED,
    build_deps,
    resume_campaign,
    set_campaign_budget,
    stop_campaign,
)
from amzagent.content.llm import LLMError, get_llm
from amzagent.ig.lander import (
    COUNTRIES,
    COUNTRY_LANGUAGE,
    HELP_URL,
    LANGUAGES,
    SAFETY,
    compliance_issues,
    lander_text,
    lines,
    load_lander,
    write_lander,
)
from amzagent.panel_settings import effective
from amzagent.web.period import PRESETS, parse_period
from amzagent.push.ai_creatives import CreativeError
from amzagent.push.propeller import MIN_DAILY_AMOUNT

KEY_FLAG = "ig_postback_key"
FAILED_RE = re.compile(r"\bне (сохран|создан|удалось|запущен|возвращена|отключена)")
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")
SAFE_PARAM = re.compile(r"^[A-Za-z0-9_\-.]{1,64}$")
LANDER_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "ig" / "templates"))
# Partner programs name the same things differently in their postbacks.
CLICK_KEYS = ("click_id", "clickid", "subid", "sub_id", "sub1", "cid")
EVENT_KEYS = ("event", "status", "goal", "type")
PAYOUT_KEYS = ("payout", "sum", "amount", "revenue")
EVENTS = {"reg": "reg", "registration": "reg", "lead": "reg", "signup": "reg",
          "ftd": "ftd", "deposit": "ftd", "first_deposit": "ftd", "sale": "ftd", "dep": "dep",
          "redeposit": "dep", "rdep": "dep",
          # CPA networks that pay for one action (e.g. Actionpay) report its
          # status instead: created -> accepted / rejected -> paid.
          "created": "ftd", "pending": "ftd", "accepted": "ftd", "approved": "ftd",
          "confirmed": "ftd", "paid": "ftd",
          "rejected": "rej", "declined": "rej", "canceled": "rej", "cancelled": "rej",
          "rej": "rej"}
STATUS_NAMES = {"active": "работает", "capped": "пауза: лимит", "paced": "растягиваю бюджет",
                "dry_run": "тест (не отправлена)", "killed": "отключена", "stopped": "остановлена",
                "error": "ошибка", "creating": "создаётся"}
EVENT_NAMES = {"reg": "регистрация", "ftd": "первый депозит", "dep": "повторный депозит",
               "rej": "отклонён сетью"}


DEVICES = {"mobile": "Телефоны и планшеты", "desktop": "Компьютеры"}


def _utc_offset_minutes(tz_name: str) -> int:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        offset = datetime.now(ZoneInfo(tz_name)).utcoffset()
    except (ZoneInfoNotFoundError, ValueError):
        return 0
    return int(offset.total_seconds() // 60) if offset else 0


def norm_domain(raw: str) -> str:
    host = re.sub(r"^[a-z]+://", "", (raw or "").strip().lower()).split("/")[0].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def request_host(request: Request) -> str:
    return norm_domain(request.headers.get("host", ""))


def _clean(value: str | None) -> str | None:
    return value if value and SAFE_PARAM.match(value) else None


def _money(raw, default: float = 0.0, digits: int = 2) -> float:
    try:
        return max(0.0, round(float(str(raw).replace(",", ".")), digits))
    except (TypeError, ValueError):
        return default


def _first(params, keys) -> str:
    for k in keys:
        if params.get(k):
            return str(params.get(k))
    return ""


def register_ig_routes(app: FastAPI, templates, settings, store, logged_in, to_login, *,
                       bot_ua, device_of, client_ip, ip_sig, network_stats=None) -> None:

    def back(request: Request, message: str, anchor: str = "") -> RedirectResponse:
        request.session["flash"] = message
        return RedirectResponse(f"/admin/ig{anchor}", status_code=303)

    def postback_key() -> str:
        key = store.get_flag(KEY_FLAG, "")
        if not key:
            key = secrets.token_urlsafe(18)
            store.set_flag(KEY_FLAG, key)
        return key

    def project_fields(form) -> tuple[dict, str]:
        country = str(form.get("country") or "BR")
        country = country if country in COUNTRIES else "BR"
        language = str(form.get("language") or "")
        fields = {
            "name": str(form.get("name") or "").strip()[:80],
            "domain": norm_domain(str(form.get("domain") or "")),
            "country": country,
            "language": language if language in LANGUAGES else COUNTRY_LANGUAGE[country],
            "brand": str(form.get("brand") or "").strip()[:80],
            "license_url": str(form.get("license_url") or "").strip()[:300],
            "offer_url": str(form.get("offer_url") or "").strip()[:1000],
            "payout": _money(form.get("payout")),
            "offer": str(form.get("offer") or "").strip()[:3000],
            "bid_cpc": min(max(_money(form.get("bid_cpc"), 0.01, 4) or 0.01, 0.001), 1.0),
            "daily_budget": max(_money(form.get("daily_budget"), MIN_DAILY_AMOUNT),
                                MIN_DAILY_AMOUNT),
            "platform": str(form.get("platform")) if form.get("platform") in PLATFORMS
            else "mobile",
            "kill_spend": _money(form.get("kill_spend")),
        }
        if not fields["name"]:
            return fields, "нужно название"
        if not fields["offer_url"].startswith("https://"):
            return fields, "ссылка оффера должна начинаться с https://"
        if fields["license_url"] and not fields["license_url"].startswith(("https://", "http://")):
            return fields, "сайт оператора должен начинаться с https://"
        if fields["domain"]:
            if not DOMAIN_RE.match(fields["domain"]):
                return fields, f"«{fields['domain']}» не похож на домен"
            if fields["domain"] == norm_domain(settings.domain):
                return fields, "нужен отдельный домен, не домен Amazon-сайта"
        return fields, ""

    def domain_taken(domain: str, project_id: int | None) -> bool:
        other = store.get_ig_project_by_domain(domain) if domain else None
        return bool(other and other["id"] != project_id)

    # --- admin -----------------------------------------------------------------

    @app.get("/admin/ig", response_class=HTMLResponse)
    def ig_page(request: Request, period: str | None = None, date_from: str | None = None,
                date_to: str | None = None, campaign: str | None = None,
                device: str | None = None):
        if not logged_in(request):
            return to_login()
        # The chosen period and filters stick (in the session) across button presses.
        if any(v is not None for v in (period, date_from, date_to, campaign, device)):
            request.session["ig_filters"] = [period, date_from, date_to, campaign, device]
        else:
            period, date_from, date_to, campaign, device = (
                request.session.get("ig_filters") or ["7d", None, None, None, None])
        campaign = campaign if campaign and campaign.isdigit() else None
        device = device if device in DEVICES else None
        tz_name = effective(settings, store).panel_timezone
        chosen = parse_period(period, date_from, date_to, tz_name)
        offset = _utc_offset_minutes(tz_name)
        net, net_zones, period_error = {}, {}, ""
        if not chosen.is_all and network_stats is not None:
            net, net_zones, period_error = network_stats(chosen)
        since, until = chosen.since, chosen.until
        projects = store.list_ig_projects()
        all_campaigns = []
        for p in projects:
            all_campaigns += [(c["id"], p["name"]) for c in campaigns_of(store, p["id"])]
        for p in projects:
            mine = {str(c["id"]) for c in campaigns_of(store, p["id"])}
            only = campaign if campaign in mine else None
            p["only"] = only
            p["l"] = load_lander(p["lander"])
            p["issues"] = compliance_issues(lander_text(p["l"]), p["language"])
            p["stats"] = store.ig_stats(p["id"], since, until, only, device)
            p["stats_all"] = store.ig_stats(p["id"], campaign=only, device=device)
            p["zones"] = store.ig_zone_stats(p["id"], limit=1000, since=since, until=until,
                                             campaign=only, device=device)
            p["daily"] = store.ig_daily(p["id"], since, until, only, device, offset)
            warnings = []
            if "{click_id}" not in p["offer_url"]:
                warnings.append("В ссылке оффера нет {click_id} — депозиты не свяжутся с "
                                "кликами, площадки нельзя будет оценить по деньгам.")
            if not p["domain"]:
                warnings.append("Не указан домен — лендинг пока доступен только вам как "
                                "предпросмотр.")
            if p["country"] == "BR" and ".bet.br" not in p["license_url"]:
                warnings.append("В Бразилии можно рекламировать только операторов с лицензией "
                                "(сайт на .bet.br). Укажите сайт оператора.")
            if not p["l"]["headline"]:
                warnings.append("Лендинг пустой — нажмите «Агент: написать лендинг».")
            p["push_issues"] = forbidden_in_push(p)
            p["pictures"] = creatives_of(p)
            p["campaigns"] = []
            spent_by_zone: dict[str, float] = {}
            for c in campaigns_of(store, p["id"]):
                if only and str(c["id"]) != only:
                    continue
                total = store.ig_stats(p["id"], since, until, str(c["id"]), device)
                if chosen.is_all:
                    spend = c["spend"]
                    zone_rows = [(z["zone"], z["spent"]) for z in store.zone_stats(c["id"])]
                else:
                    spend = (net.get(c["external_id"] or "") or {}).get("spent", 0.0)
                    zone_rows = [(str(z.get("zone_id")), z.get("spent", 0.0))
                                 for z in net_zones.get(c["external_id"] or "", [])]
                for zone, spent in zone_rows:
                    spent_by_zone[zone] = spent_by_zone.get(zone, 0.0) + spent
                c.update(total=total, period_spend=spend, profit=total["revenue"] - spend,
                         excluded=len(store.blacklisted_zones(c["id"])),
                         winners=deposit_zones(store, c["id"]))
                p["campaigns"].append(c)
            running = [c for c in p["campaigns"]
                       if c["status"] in ("active", "paced", "capped") and not c["zones_only"]]
            off = set.intersection(*[store.blacklisted_zones(c["id"]) for c in running]) \
                if running else set()
            for z in p["zones"]:
                z["spent"] = spent_by_zone.get(z["zone"], 0.0)
                z["off"] = z["zone"] in off
            p["can_toggle"] = bool(running)
            p["spent"] = sum(c["period_spend"] for c in p["campaigns"])
            p["warnings"] = warnings
        flash = request.session.pop("flash", None)
        base = settings.public_base_url()
        return templates.TemplateResponse(request, "ig.html", {
            "projects": projects, "countries": COUNTRIES, "languages": LANGUAGES,
            "postback": f"{base}/pb/ig?key={postback_key()}&click_id={{clickid}}"
                        "&event={event}&payout={payout}",
            "event_names": EVENT_NAMES, "main_domain": settings.domain, "platforms": PLATFORMS,
            "status_names": STATUS_NAMES, "period": chosen, "presets": PRESETS,
            "campaign": campaign, "device": device, "devices": DEVICES,
            "all_campaigns": all_campaigns,
            "period_error": period_error, "timezone": effective(settings, store).panel_timezone,
            "flash": flash, "flash_bad": bool(flash and FAILED_RE.search(flash)),
        })

    @app.post("/admin/ig/projects")
    async def ig_add_project(request: Request):
        if not logged_in(request):
            return to_login()
        fields, error = project_fields(await request.form())
        if not error and domain_taken(fields["domain"], None):
            error = "этот домен уже занят другим проектом"
        if error:
            return back(request, f"Проект не сохранён: {error}.", "#projects")
        pid = store.add_ig_project(**fields)
        return back(request, f"Проект «{fields['name']}» создан. Теперь агент может написать "
                             "лендинг.", f"#p{pid}")

    @app.post("/admin/ig/projects/{project_id}")
    async def ig_edit_project(request: Request, project_id: int):
        if not logged_in(request):
            return to_login()
        if store.get_ig_project(project_id) is None:
            raise HTTPException(404)
        fields, error = project_fields(await request.form())
        if not error and domain_taken(fields["domain"], project_id):
            error = "этот домен уже занят другим проектом"
        if error:
            return back(request, f"Проект не сохранён: {error}.", f"#p{project_id}")
        store.update_ig_project(project_id, **fields)
        return back(request, "Проект сохранён.", f"#p{project_id}")

    @app.post("/admin/ig/projects/{project_id}/delete")
    def ig_delete_project(request: Request, project_id: int):
        if not logged_in(request):
            return to_login()
        store.delete_ig_project(project_id)
        return back(request, "Проект удалён.", "#projects")

    @app.post("/admin/ig/projects/{project_id}/write")
    def ig_write_lander(request: Request, project_id: int):
        if not logged_in(request):
            return to_login()
        project = store.get_ig_project(project_id)
        if project is None:
            raise HTTPException(404)
        if not project["offer"].strip():
            return back(request, "Лендинг не создан: опишите оффер (бонус, условия, депозит).",
                        f"#p{project_id}")
        try:
            lander = write_lander(get_llm(effective(settings, store)), project)
        except LLMError as exc:
            return back(request, f"Лендинг не создан: {exc}", f"#p{project_id}")
        store.update_ig_project(project_id, lander=json.dumps(lander, ensure_ascii=False))
        issues = compliance_issues(lander_text(lander), project["language"])
        note = (" Проверьте: найдены запрещённые слова — " + ", ".join(issues) + "."
                if issues else " Проверьте тексты и поправьте, если нужно.")
        return back(request, "Агент написал лендинг." + note, f"#lander{project_id}")

    @app.post("/admin/ig/projects/{project_id}/lander")
    async def ig_save_lander(request: Request, project_id: int):
        if not logged_in(request):
            return to_login()
        if store.get_ig_project(project_id) is None:
            raise HTTPException(404)
        form = await request.form()
        lander = {k: str(form.get(k) or "").strip()[:600]
                  for k in ("title", "headline", "intro", "bonus_title", "bonus_text", "cta")}
        lander["steps"] = lines(str(form.get("steps") or ""))[:6]
        faq = []
        for line in lines(str(form.get("faq") or ""))[:8]:
            q, _, a = line.partition("|")
            if q.strip() and a.strip():
                faq.append({"q": q.strip()[:200], "a": a.strip()[:600]})
        lander["faq"] = faq
        store.update_ig_project(project_id, lander=json.dumps(lander, ensure_ascii=False))
        return back(request, "Лендинг сохранён.", f"#lander{project_id}")

    @app.post("/admin/ig/projects/{project_id}/push")
    async def ig_save_push(request: Request, project_id: int):
        if not logged_in(request):
            return to_login()
        if store.get_ig_project(project_id) is None:
            raise HTTPException(404)
        form = await request.form()
        store.update_ig_project(project_id,
                                push_title=str(form.get("push_title") or "").strip()[:30],
                                push_text=str(form.get("push_text") or "").strip()[:50])
        return back(request, "Текст пуша сохранён.", f"#push{project_id}")

    @app.post("/admin/ig/projects/{project_id}/push/write")
    def ig_write_push(request: Request, project_id: int):
        if not logged_in(request):
            return to_login()
        project = store.get_ig_project(project_id)
        if project is None:
            raise HTTPException(404)
        try:
            title, text = write_push_text(get_llm(effective(settings, store)), project)
        except LLMError as exc:
            return back(request, f"Пуш не создан: {exc}", f"#push{project_id}")
        store.update_ig_project(project_id, push_title=title, push_text=text)
        return back(request, "Агент написал текст пуша — проверьте его.", f"#push{project_id}")

    @app.post("/admin/ig/projects/{project_id}/pictures/draw")
    def ig_draw_pictures(request: Request, project_id: int):
        if not logged_in(request):
            return to_login()
        if store.get_ig_project(project_id) is None:
            raise HTTPException(404)
        try:
            made = draw_ig_creatives(build_deps(settings, store), project_id)
        except (CreativeError, LLMError) as exc:
            return back(request, f"Картинки не созданы: {exc}", f"#push{project_id}")
        return back(request, f"Агент нарисовал картинок: {made}. Проверьте их — лишние можно "
                             "удалить. Новые картинки пойдут в следующую запущенную кампанию.",
                    f"#push{project_id}")

    @app.post("/admin/ig/projects/{project_id}/pictures/upload")
    async def ig_upload_picture(request: Request, project_id: int):
        if not logged_in(request):
            return to_login()
        if store.get_ig_project(project_id) is None:
            raise HTTPException(404)
        upload = (await request.form()).get("image")
        if not (isinstance(upload, UploadFile) and upload.filename):
            return back(request, "Картинка не сохранена: выберите файл.", f"#push{project_id}")
        data = await upload.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            return back(request, "Картинка не сохранена: больше 8 МБ.", f"#push{project_id}")
        try:
            add_creative(store, settings, project_id, data)
        except (OSError, ValueError):
            return back(request, "Картинка не сохранена: это не изображение JPG/PNG.",
                        f"#push{project_id}")
        return back(request, "Картинка добавлена: обрезана под пуш (492×328) и иконку (192×192).",
                    f"#push{project_id}")

    @app.post("/admin/ig/projects/{project_id}/pictures/{image}/delete")
    def ig_delete_picture(request: Request, project_id: int, image: str):
        if not logged_in(request):
            return to_login()
        remove_creative(store, project_id, image)
        return back(request, "Картинка убрана из проекта.", f"#push{project_id}")

    @app.post("/admin/ig/projects/{project_id}/launch")
    def ig_launch(request: Request, project_id: int):
        if not logged_in(request):
            return to_login()
        error = launch_ig_campaign(build_deps(settings, store), project_id)
        return back(request, f"Кампания не запущена: {error}" if error else
                    "Кампания отправлена на модерацию PropellerAds; начнёт работать после "
                    "одобрения.", f"#push{project_id}")

    @app.post("/admin/ig/campaigns/{campaign_id}/stop")
    def ig_stop(request: Request, campaign_id: int):
        if not logged_in(request):
            return to_login()
        c = store.get_campaign(campaign_id)
        if c is None or c["niche_id"] != 0:
            raise HTTPException(404)
        stop_campaign(build_deps(settings, store), c, KILLED, "stopped manually")
        return back(request, f"Кампания #{campaign_id} остановлена.",
                    f"#push{c['asin'][2:]}")

    @app.post("/admin/ig/projects/{project_id}/zones/{zone}/{action}")
    def ig_zone(request: Request, project_id: int, zone: str, action: str):
        if not logged_in(request):
            return to_login()
        if not zone.isdigit() or action not in ("off", "on"):
            raise HTTPException(404)
        error = set_zone(build_deps(settings, store), project_id, zone, action == "off")
        verb = "отключена" if action == "off" else "возвращена"
        return back(request, f"Площадка {zone} не {verb[:-1]}а: {error}" if error else
                    f"Площадка {zone} {verb} во всех работающих кампаниях проекта.",
                    f"#zones{project_id}")

    @app.post("/admin/ig/campaigns/{campaign_id}/budget")
    async def ig_budget(request: Request, campaign_id: int):
        if not logged_in(request):
            return to_login()
        c = store.get_campaign(campaign_id)
        if c is None or c["niche_id"] != 0:
            raise HTTPException(404)
        budget = _money((await request.form()).get("budget"), -1.0)
        error = (set_campaign_budget(build_deps(settings, store), campaign_id, budget)
                 if budget >= 0 else "нужно число")
        return back(request, f"Бюджет кампании #{campaign_id} не сохранён: {error}" if error else
                    f"Бюджет кампании #{campaign_id}: ${budget:.2f} в день.",
                    f"#push{c['asin'][2:]}")

    @app.post("/admin/ig/campaigns/{campaign_id}/whitelist")
    def ig_whitelist(request: Request, campaign_id: int):
        """New campaign of the same project only on the zones that brought
        deposits; the original keeps running."""
        if not logged_in(request):
            return to_login()
        c = store.get_campaign(campaign_id)
        if c is None or c["niche_id"] != 0:
            raise HTTPException(404)
        zones = deposit_zones(store, campaign_id)
        pid = int(c["asin"][2:])
        if not zones:
            return back(request, "Вайт-лист не запущен: у кампании нет площадок с "
                                 "депозитами.", f"#push{pid}")
        error = launch_ig_campaign(build_deps(settings, store), pid, zones=zones)
        return back(request, f"Кампания не запущена: {error}" if error else
                    f"Вайт-лист запущен на площадках {', '.join(zones)}: отправлен на модерацию. "
                    "Исходная кампания продолжает работать.", f"#push{pid}")

    @app.post("/admin/ig/campaigns/{campaign_id}/resume")
    def ig_resume(request: Request, campaign_id: int):
        if not logged_in(request):
            return to_login()
        c = store.get_campaign(campaign_id)
        if c is None or c["niche_id"] != 0:
            raise HTTPException(404)
        error = resume_campaign(build_deps(settings, store), campaign_id)
        return back(request, f"Кампания #{campaign_id} не возвращена: {error}" if error else
                    f"Кампания #{campaign_id} возвращена; агент не будет отключать её по "
                    "результатам, только её плохие зоны.", f"#push{c['asin'][2:]}")

    # --- public ------------------------------------------------------------------

    def served(request: Request, project_id: int) -> tuple[dict, bool]:
        """The project and whether this is the owner's preview. The lander
        lives on its own domain; on any other host only the owner sees it."""
        project = store.get_ig_project(project_id)
        if project is None:
            raise HTTPException(404)
        if project["domain"] and request_host(request) == project["domain"]:
            return project, logged_in(request)
        if logged_in(request):
            return project, True
        raise HTTPException(404)

    @app.get("/l/{project_id}/", response_class=HTMLResponse)
    def ig_lander(request: Request, project_id: int, c: str | None = None,
                  z: str | None = None):
        project, preview = served(request, project_id)
        campaign, zone = _clean(c), _clean(z)
        ua = request.headers.get("user-agent", "")
        if not preview and not bot_ua.search(ua or "bot"):
            ip = client_ip(request)
            store.log_ig_event(project_id, "visit", campaign=campaign, zone=zone,
                               device=device_of(request), ip=ip_sig(ip) if ip else None)
        on_domain = project["domain"] and request_host(request) == project["domain"]
        go = "/go" if on_domain else f"/l/{project_id}/go"
        params = "&".join(f"{k}={v}" for k, v in (("c", campaign), ("z", zone)) if v)
        lang = project["language"] if project["language"] in SAFETY else "en"
        return LANDER_TEMPLATES.TemplateResponse(request, "lander.html", {
            "project": project, "l": load_lander(project["lander"]), "s": SAFETY[lang],
            "lang": lang, "help_url": HELP_URL, "go_url": go + (f"?{params}" if params else ""),
        }, headers={"X-Robots-Tag": "noindex"})

    @app.get("/l/{project_id}/go")
    def ig_go(request: Request, project_id: int, c: str | None = None, z: str | None = None,
              js: str | None = None):
        project, preview = served(request, project_id)
        campaign, zone = _clean(c), _clean(z)
        ua = request.headers.get("user-agent", "")
        ip = client_ip(request)
        ip_key = ip_sig(ip) if ip else None
        if bot_ua.search(ua or "bot"):
            store.log_ig_event(project_id, "bot", campaign=campaign, zone=zone,
                               device=device_of(request), ip=ip_key)
            return HTMLResponse("<!doctype html><title>18+</title><p>Not available.</p>",
                                status_code=403)
        click_id = "preview" if preview else secrets.token_hex(8)
        if not preview:
            store.log_ig_event(project_id, "click", click_id=click_id, campaign=campaign,
                               zone=zone, device=device_of(request), ip=ip_key)
        url = (project["offer_url"].replace("{click_id}", click_id)
               .replace("{campaign}", campaign or "").replace("{zone}", zone or ""))
        return RedirectResponse(url, status_code=302)

    @app.get("/pb/ig", response_class=PlainTextResponse)
    def ig_postback(request: Request):
        params = request.query_params
        if not hmac.compare_digest(str(params.get("key") or ""), postback_key()):
            return PlainTextResponse("bad key", status_code=403)
        click_id = _clean(_first(params, CLICK_KEYS))
        event = EVENTS.get(_first(params, EVENT_KEYS).lower())
        click = store.get_ig_click(click_id) if click_id else None
        if click is None or event is None:
            return PlainTextResponse("unknown click or event")  # 200: don't make them retry
        if event in ("reg", "ftd", "rej") and store.has_ig_conversion(click_id, event):
            return PlainTextResponse("duplicate")
        project = store.get_ig_project(click["project_id"])
        if event == "rej":
            # The network turned the player down after its check: take back
            # what the deposit was credited with.
            payout = -store.ig_click_revenue(click_id, "ftd")
        else:
            # Amounts in another currency (Actionpay sends reais) aren't
            # converted: the project's CPA in dollars is used instead.
            # A macro the network doesn't know arrives as literal text
            # ("{payout}"): that counts as no amount at all.
            currency = str(params.get("currency") or "usd").lower()
            sent = _money(_first(params, PAYOUT_KEYS), -1.0)
            payout = sent if sent >= 0 and currency == "usd" else (
                project["payout"] if project and event == "ftd" else 0.0)
        store.log_ig_event(click["project_id"], event, click_id=click_id,
                           campaign=click["campaign"], zone=click["zone"],
                           device=click["device"], payout=payout)
        return PlainTextResponse("ok")

    @app.get("/caddy/ask")
    def caddy_ask(domain: str = ""):
        """Caddy gets a TLS certificate only for domains of our projects."""
        found = store.get_ig_project_by_domain(norm_domain(domain))
        return Response(status_code=200 if found else 404)
