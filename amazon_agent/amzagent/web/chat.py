"""Chat with the agent from the dashboard.

The owner asks in plain Russian ("why was #6 stopped?", "raise the limit
to $40", "turn off zone 123 on #6"); the LLM answers from live data it
reads through tools and performs the same actions the dashboard buttons
do. Every action goes through the agent's own functions, so limits and
safety rules still apply, and is written to the journal.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from amzagent.agent.runner import (
    KILLED,
    MANUAL_FLAG,
    NEXT_CYCLE_FLAG,
    PAUSE_FLAG,
    STATS_ERROR_FLAG,
    build_deps,
    change_settings,
    committed_24h,
    exclude_zone,
    include_zone,
    is_manual,
    launch_product,
    launch_whitelist,
    resume_campaign,
    resume_eta,
    spent_today,
    stop_all,
    stop_campaign,
)
from amzagent.config import Settings
from amzagent.panel_settings import EDITABLE, effective, parse_form
from amzagent.store import Store

logger = logging.getLogger(__name__)

HISTORY_MESSAGES = 12  # earlier turns the model sees (their numbers may be stale)
MAX_TOOL_ROUNDS = 8
ZONE_RE = re.compile(r"^\d{1,12}$")

SYSTEM = """Ты — автономный агент, который ведёт партнёрский бизнес владельца на Amazon.
Ты сам делаешь сайты-витрины с товарами Amazon (партнёрский тег владельца) и
запускаешь на страницы товаров пуш-рекламу в PropellerAds: одна кампания на
товар. Сейчас с владельцем ты общаешься в чате панели управления.

Как ты работаешь (это правила, по которым действует агент):
- Тест товара: когда кампания потратила KILL_MIN_SPEND (реальный расход или
  оценка по визитам × ставка), её отключают, если на Amazon перешло меньше
  MIN_AMAZON_RATE % посетителей сайта. Дополнительно (если > 0) —
  если переход на Amazon дороже MAX_COST_PER_AMAZON_CLICK.
- Зоны (площадки PropellerAds): у кампаний, прошедших тест, и у вернутых
  вручную, агент отключает зону без переходов на Amazon после ZONE_MIN_VISITS
  визитов или ZONE_MIN_SPEND расхода. Зоны с переходами остаются.
- Лимит: MAX_DAILY_SPEND — предел расхода за скользящие 24 часа. При
  достижении все кампании ставятся на паузу (статус capped), потом снимаются.
- Вернутая вручную кампания (manual_keep) не отключается правилами — только
  владельцем.
- Растягивание бюджета (pace_daily_budget): PropellerAds тратит дневной
  бюджет кампании за несколько часов, поэтому агент ставит кампанию,
  обогнавшую равномерный график (бюджет × прошедшая доля дня UTC + час
  запаса), на паузу (статус paced) и включает, когда время её догонит.
- Платформа (push_platform): all — все устройства, mobile — только телефоны
  и планшеты, desktop — только ПК. Действует на новые кампании (таргетинг
  os_type в PropellerAds); уже запущенные не меняются.
- Сообщения «💡 Советы агента» в истории чата — твои собственные советы,
  которые ты пишешь раз в час сам. Если владелец отвечает «сделай» (или
  «сделай 1 и 3»), выполни их своими инструментами: set_zone для отключения
  зон, whitelist_campaign для вайт-листа, stop_campaign, update_settings.
  Настройки, которых нет в инструментах, объясни, где поменять в панели.
- Статусы: active — работает; paced — пауза, растягиваю бюджет на день;
  capped — пауза по лимиту; killed — отключена
  (правилом или вручную); stopped — остановлена; error — не создалась;
  dry_run — тест без отправки; creating — создаётся.
- Creator Connections: у товара есть "bonus_budget" (high/medium/low) — сколько
  бонусного бюджета осталось у бренда. Low агент сам не рекламирует (бонус
  вот-вот кончится), high идут первыми.
- Режим: авто — всё выше агент делает сам (и сам запускает новые кампании
  по лучшим товарам, по 3 на сайт); ручной — агент не запускает и не
  отключает кампании, не трогает зоны и не подбирает ниши, только следит
  за лимитом и аварийными защитами; решения принимает владелец (через тебя
  или кнопки).
- Проверка кампаний идёт каждые 3 минуты, полный цикл — каждые
  AGENT_INTERVAL_HOURS часов. Amazon Creators API пока может отказывать
  (AssociateNotEligible) — тогда сайт в запасном режиме без фото и цен Amazon.

- «Пауза в PropellerAds»: кампания у нас active, но сама PropellerAds держит
  её на паузе (note "PropellerAds: paused"). Обычно это «Daily impressions —
  waiting for late clicks»: кампания набрала столько показов, что поздние
  клики по ним могут превысить дневной бюджет (daily_budget), и PropellerAds
  ждёт их, а потом сама перезапускает кампанию. Делать ничего не нужно; чтобы
  такие паузы были реже, можно поднять бюджет кампании (campaign_daily_budget).

Правила чата:
- Отвечай по-русски, коротко и по делу, простыми словами. Деньги в долларах.
- Ниже в каждом сообщении есть блок ТЕКУЩЕЕ СОСТОЯНИЕ — это свежие данные.
  Прошлые сообщения чата могут быть устаревшими: если там другие цифры или
  настройки, верь текущему состоянию.
- На вопросы «почему реклама не идёт / остановилась» отвечай по списку
  why_ads_not_running из текущего состояния, называя конкретные цифры.
- Цифры бери только из состояния и инструментов, не выдумывай. Если данных
  нет — скажи. Подробности (зоны, журнал, товары) — через инструменты.
- Действия (стоп/возврат кампании, зоны, настройки, запуск цикла, стоп всё)
  выполняй, только когда владелец прямо об этом просит. Если просьба
  неоднозначна или действие увеличит расходы, а владелец этого явно не
  сказал — сначала уточни.
- После действия скажи, что именно сделано, и результат (или ошибку).
- Если спрашивают совета — дай рекомендацию с цифрами.
- revenue_max / profit_max — переходы на Amazon × EPC «up to» из Creator
  Connections (минус расход): лучший возможный результат, реальный доход
  (процент с покупок) обычно ниже. Так и говори: «максимум»."""


def _tool(name: str, description: str, properties: dict | None = None,
          required: list[str] | None = None) -> dict:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties or {},
                           "required": required or []}}


_SETTING_TYPES = {bool: "boolean", float: "number", int: "integer", str: "string"}

TOOLS = [
    _tool("overview", "Сводка: сайты, настройки, расход за 24 ч и лимит, число кампаний, "
                      "ошибки статистики, включена ли реклама."),
    _tool("list_campaigns", "Все кампании со статистикой за всё время: показы, клики, "
                            "расход, визиты, переходы на Amazon, статус и причина.",
          {"only_running": {"type": "boolean",
                            "description": "только active/capped"}}),
    _tool("campaign_zones", "Зоны (площадки) одной кампании со статистикой.",
          {"campaign_id": {"type": "integer"},
           "limit": {"type": "integer", "description": "сколько зон, по расходу (30)"}},
          ["campaign_id"]),
    _tool("journal", "Последние записи журнала агента.",
          {"limit": {"type": "integer", "description": "сколько записей (5)"}}),
    _tool("site_products", "Товары сайта по порядку (лучшие первыми): ASIN, название, EPC "
                           "и последняя кампания на товар.",
          {"site_id": {"type": "integer"}}, ["site_id"]),
    _tool("launch_product", "Запустить рекламу на товар сайта (в любом режиме; лимит "
                            "за 24 ч соблюдается).",
          {"site_id": {"type": "integer"}, "asin": {"type": "string"}},
          ["site_id", "asin"]),
    _tool("set_mode", "Переключить режим: auto — агент сам управляет кампаниями, "
                      "manual — только владелец.",
          {"mode": {"type": "string", "enum": ["auto", "manual"]}}, ["mode"]),
    _tool("stop_campaign", "Остановить кампанию (товар больше не запускается).",
          {"campaign_id": {"type": "integer"}}, ["campaign_id"]),
    _tool("resume_campaign", "Вернуть остановленную/отключённую кампанию. Правила её "
                             "больше не отключают, зоны без переходов чистятся. Если под лимитом "
                             "за 24 ч нет места — встаёт в очередь (capped) и включится сама.",
          {"campaign_id": {"type": "integer"}}, ["campaign_id"]),
    _tool("whitelist_campaign", "Создать новую кампанию на тот же товар, что у кампании "
                                "campaign_id, которая крутится только на зонах zones "
                                "(вайт-лист). Исходная продолжает работать; лимит за 24 ч "
                                "действует; новой агент зоны не отключает.",
          {"campaign_id": {"type": "integer"},
           "zones": {"type": "array", "items": {"type": "string"}}},
          ["campaign_id", "zones"]),
    _tool("set_zone", "Отключить или снова включить зону в кампании.",
          {"campaign_id": {"type": "integer"}, "zone": {"type": "string"},
           "action": {"type": "string", "enum": ["exclude", "include"]}},
          ["campaign_id", "zone", "action"]),
    _tool("update_settings", "Изменить настройки рекламы и бюджета. Передай только "
                             "меняемые поля.",
          {"changes": {"type": "object", "properties": {
              k: {"type": _SETTING_TYPES[t]} for k, (t, _, _) in EDITABLE.items()},
              "additionalProperties": False}},
          ["changes"]),
    _tool("run_cycle", "Запустить полный цикл агента сейчас (товары, сайт, кампании). "
                       "discover > 0 — сначала подобрать столько новых ниш.",
          {"discover": {"type": "integer"}}),
    _tool("kill_switch", "Стоп всё (on=true: остановить все кампании и не запускать "
                         "новые) или снять стоп (on=false).",
          {"on": {"type": "boolean"}}, ["on"]),
]


def _round(value, digits: int = 2):
    return round(value, digits) if isinstance(value, float) else value


class ChatAgent:
    def __init__(self, settings: Settings, store: Store,
                 run_cycle: Callable[[int], None] | None = None, backend=None):
        self.settings, self.store = settings, store
        self._run_cycle = run_cycle
        self._backend = backend  # tests inject a fake; otherwise built per reply

    # --- tools -------------------------------------------------------------

    def _overview(self, _args: dict) -> dict:
        s = effective(self.settings, self.store)
        sites = []
        for n in self.store.list_niches():
            stats = self.store.niche_stats(n.id)
            sites.append({"id": n.id, "name": n.keywords, "slug": n.slug, "enabled": n.enabled,
                          "products": len(self.store.list_products(n.id)),
                          "visits_7d": stats["visit"], "amazon_clicks_7d": stats["click"]})
        running = self.store.list_campaigns(statuses=("active",))
        return {
            "now_panel_time": _now_local(s.panel_timezone),
            "mode": "manual" if is_manual(self.store) else "auto",
            "ads_live": s.push_live,
            "stop_all_on": self.store.get_flag(PAUSE_FLAG) == "1",
            "spent_24h": _round(spent_today(self.store)),
            "limit_24h": s.max_daily_spend,
            "paused_at_limit": len(self.store.list_campaigns(statuses=("capped",))),
            "ads_resume_at": _local(resume_eta(self.store, s.max_daily_spend), s.panel_timezone),
            "next_full_cycle_at": _local(_flag_time(self.store.get_flag(NEXT_CYCLE_FLAG)),
                                         s.panel_timezone),
            "running_campaigns": len(running),
            "why_ads_not_running": self.diagnose(),
            "stats_error": self.store.get_flag(STATS_ERROR_FLAG) or None,
            "settings": {k: getattr(s, k) for k in EDITABLE},
            "agent_interval_hours": s.agent_interval_hours,
            "sites": sites,
        }

    def diagnose(self) -> list[str]:
        """Plain reasons why ads are or aren't running right now, computed
        from the agent's own state (so the model doesn't have to guess)."""
        s = effective(self.settings, self.store)
        tz = s.panel_timezone
        out: list[str] = []
        if self.store.get_flag(PAUSE_FLAG) == "1":
            out.append("Включён «Стоп всё»: все кампании остановлены, новые не запускаются.")
        if not s.push_live:
            out.append("Реклама выключена в настройках (тестовый режим): в PropellerAds "
                       "ничего не отправляется.")
        spent = spent_today(self.store)
        capped = self.store.list_campaigns(statuses=("capped",))
        if capped:
            eta = _local(resume_eta(self.store, s.max_daily_spend), tz)
            out.append(
                f"{len(capped)} кампаний на паузе по суточному лимиту: за 24 ч потрачено "
                f"${(spent or 0):.2f} при лимите ${s.max_daily_spend:.2f}. Возобновятся, когда "
                f"расход за 24 ч опустится ниже ${s.max_daily_spend - 1:.2f}"
                + (f" — примерно в {eta}." if eta else "."))
        paced = self.store.list_campaigns(statuses=("paced",))
        if paced:
            out.append(f"{len(paced)} кампаний на короткой паузе: агент растягивает их дневной "
                       f"бюджет на весь день (они опередили график и включатся сами, когда "
                       f"время их догонит).")
        for c in self.store.list_campaigns(statuses=("active",)):
            note = c["note"] or ""
            if note.startswith("PropellerAds: paused"):
                out.append(f"Кампания #{c['id']} ({c['asin']}): PropellerAds сама поставила её на "
                           f"паузу — обычно «Daily impressions»: ждёт поздних кликов, чтобы "
                           f"не превысить дневной бюджет ${c['daily_budget']:.0f}, и потом "
                           f"сама перезапустит.")
            elif "moderation" in note:
                out.append(f"Кампания #{c['id']} ({c['asin']}) на модерации в PropellerAds.")
        if spent is None and s.push_live:
            out.append("Расход за 24 ч ещё не получен из PropellerAds — новые кампании "
                       "не запускаются, пока его нет.")
        error = self.store.get_flag(STATS_ERROR_FLAG)
        if error:
            out.append(f"Статистика PropellerAds не читается: {error[:150]}")
        if is_manual(self.store):
            out.append("Ручной режим: агент сам новые кампании не запускает"
                       + ("; зоны без переходов отключает." if s.manual_prune_zones
                          else " и зоны не отключает."))
        elif spent is not None and s.push_live:
            committed = committed_24h(self.store) or 0.0
            if committed + s.campaign_daily_budget > s.max_daily_spend:
                out.append(f"Новые кампании не запускаются: занято ${committed:.2f} из лимита "
                           f"${s.max_daily_spend:.2f}, а новой нужно "
                           f"${s.campaign_daily_budget:.2f}.")
        working = [c for c in self.store.list_campaigns(statuses=("active",))
                   if not (c["note"] or "").startswith("PropellerAds: paused")]
        if not working and not out:
            out.append("Работающих кампаний нет.")
        return out or ["Ничего не мешает: кампании работают."]

    def snapshot(self) -> str:
        """Fresh state sent with every message."""
        state = self._overview({})
        state.pop("settings", None)
        s = effective(self.settings, self.store)
        state["key_settings"] = {k: getattr(s, k) for k in (
            "max_daily_spend", "campaign_daily_budget", "push_bid_cpc", "kill_min_spend",
            "min_amazon_rate", "max_cost_per_amazon_click", "campaigns_per_site")}
        state["campaigns_now"] = [
            {k: c[k] for k in ("id", "title", "status", "note", "manual_keep", "spent",
                               "site_visits", "amazon_clicks", "to_amazon_percent")}
            for c in self._campaigns({"only_running": True})]
        return json.dumps(state, ensure_ascii=False, default=str)

    def _campaigns(self, args: dict) -> list:
        from amzagent.web.app import _campaign_rows  # the dashboard's own numbers

        s = effective(self.settings, self.store)
        rows = _campaign_rows(self.store, s.push_bid_cpc)
        if args.get("only_running"):
            rows = [c for c in rows if c["status"] in ("active", "capped", "paced")]
        return [{
            "id": c["id"], "asin": c["asin"], "title": c["title"][:70],
            "status": c["status"], "note": c["note"], "manual_keep": bool(c.get("manual_keep")),
            "daily_budget": c["daily_budget"], "spent": _round(c["spend"]),
            "spent_estimate_from_visits": _round(c["spend_est"]),
            "impressions": c["impressions"], "ad_clicks": c["ad_clicks"],
            "site_visits": c["visits"], "amazon_clicks": c["clicks"],
            "to_amazon_percent": _round(100 * c["to_amazon"]) if c["to_amazon"] else 0,
            "cost_per_amazon_click": _round(c["cost_per_click"]),
            "by_device": c["devices"] or None,  # mobile/desktop visits, Amazon clicks, rate
            "epc_up_to": c["epc"],
            "revenue_max": _round(c["revenue"]),
            "profit_max": _round(c["profit"]),
            "zones": len(c["zones"]), "zones_excluded": sum(z["excluded"] for z in c["zones"]),
            "created_at": c["created_at"],
        } for c in rows[:60]]

    def _zones(self, args: dict) -> dict:
        from amzagent.web.app import _zone_rows

        cid = int(args["campaign_id"])
        if self.store.get_campaign(cid) is None:
            return {"error": f"кампании #{cid} нет"}
        zones = _zone_rows(self.store, cid)
        limit = max(1, min(int(args.get("limit") or 30), 100))
        return {"total": len(zones), "excluded": sum(z["excluded"] for z in zones),
                "zones": [{"zone": z["zone"], "impressions": z["impressions"],
                           "ad_clicks": z["clicks"], "spent": _round(z["spent"]),
                           "site_visits": z["visits"], "amazon_clicks": z["amazon"],
                           "excluded": z["excluded"]} for z in zones[:limit]]}

    def _journal(self, args: dict) -> list:
        limit = max(1, min(int(args.get("limit") or 5), 20))
        return [{"at": r["ts"], "ok": r["ok"], "log": (r["log"] or "")[-1500:]}
                for r in self.store.list_runs(limit)]

    def _site_products(self, args: dict) -> dict:
        niche_id = int(args["site_id"])
        if self.store.get_niche(niche_id) is None:
            return {"error": f"сайта {niche_id} нет"}
        latest: dict[str, dict] = {}
        for c in self.store.list_campaigns(niche_id=niche_id):
            latest.setdefault(c["asin"], c)
        out = []
        for p, copy in self.store.list_products(niche_id):
            c = latest.get(p.asin)
            out.append({"asin": p.asin, "title": p.title[:70], "epc": p.epc,
                        "bonus_budget": p.cc_budget or None,
                        "has_texts": copy is not None,
                        "campaign": {"id": c["id"], "status": c["status"]} if c else None})
        return {"products": out}

    def _launch(self, deps, args: dict) -> dict:
        error = launch_product(deps, int(args["site_id"]), str(args["asin"]).strip().upper())
        return {"error": error} if error else {"ok": True}

    def _set_mode(self, args: dict) -> dict:
        mode = args.get("mode")
        if mode not in ("auto", "manual"):
            return {"error": "mode — auto или manual"}
        self.store.set_flag(MANUAL_FLAG, "1" if mode == "manual" else "0")
        return {"ok": True, "mode": mode}

    def _stop(self, deps, args: dict) -> dict:
        c = self.store.get_campaign(int(args["campaign_id"]))
        if c is None:
            return {"error": "кампания не найдена"}
        if c["status"] in (KILLED, "stopped"):
            return {"error": "кампания уже остановлена"}
        stop_campaign(deps, c, KILLED, "stopped manually (chat)")
        return {"ok": True, "status": self.store.get_campaign(c["id"])["status"]}

    def _resume(self, deps, args: dict) -> dict:
        error = resume_campaign(deps, int(args["campaign_id"]))
        if error:
            return {"error": error}
        status = self.store.get_campaign(int(args["campaign_id"]))["status"]
        return {"ok": True, "status": status,
                "note": "ждёт места под лимитом за 24 ч, включится сама" if status == "capped"
                        else "работает"}

    def _whitelist(self, deps, args: dict) -> dict:
        zones = [str(z) for z in args.get("zones") or [] if ZONE_RE.match(str(z))]
        error = launch_whitelist(deps, int(args["campaign_id"]), zones)
        return {"error": error} if error else {"ok": True, "zones": zones}

    def _set_zone(self, deps, args: dict) -> dict:
        zone, action = str(args.get("zone", "")), args.get("action")
        if not ZONE_RE.match(zone) or action not in ("exclude", "include"):
            return {"error": "зона — число, action — exclude или include"}
        fn = exclude_zone if action == "exclude" else include_zone
        error = fn(deps, int(args["campaign_id"]), zone)
        return {"error": error} if error else {"ok": True}

    def _update_settings(self, args: dict) -> dict:
        changes = args.get("changes") or {}
        unknown = [k for k in changes if k not in EDITABLE]
        if unknown or not changes:
            return {"error": f"нельзя менять: {unknown}" if unknown else "нет изменений"}
        current = effective(self.settings, self.store)
        form = {k: ("1" if getattr(current, k) else "") if t is bool else str(getattr(current, k))
                for k, (t, _, _) in EDITABLE.items()}
        for k, v in changes.items():
            form[k] = ("1" if v in (True, "true", "1", 1) else "") if EDITABLE[k][0] is bool \
                else str(v)
        values, errors = parse_form(form)
        if errors:
            return {"error": "; ".join(errors)}
        return {"ok": True, "note": change_settings(self.settings, self.store, values)}

    def _run(self, args: dict) -> dict:
        if self._run_cycle is None:
            return {"error": "запуск цикла недоступен"}
        self._run_cycle(max(0, min(int(args.get("discover") or 0), 5)))
        return {"ok": True, "note": "цикл запущен в фоне, ход виден в журнале"}

    def _kill_switch(self, deps, args: dict) -> dict:
        on = bool(args.get("on"))
        self.store.set_flag(PAUSE_FLAG, "1" if on else "0")
        if on:
            stop_all(deps)
        return {"ok": True, "stop_all_on": on}

    def run_tool(self, deps, name: str, args: dict) -> dict | list:
        readers = {"overview": self._overview, "list_campaigns": self._campaigns,
                   "campaign_zones": self._zones, "journal": self._journal,
                   "update_settings": self._update_settings, "run_cycle": self._run,
                   "site_products": self._site_products, "set_mode": self._set_mode}
        actors = {"stop_campaign": self._stop, "resume_campaign": self._resume,
                  "launch_product": self._launch, "whitelist_campaign": self._whitelist,
                  "set_zone": self._set_zone, "kill_switch": self._kill_switch}
        try:
            if name in readers:
                return readers[name](args)
            if name in actors:
                return actors[name](deps, args)
        except (KeyError, TypeError, ValueError) as exc:
            return {"error": f"неверные параметры: {exc}"}
        return {"error": f"нет такого инструмента: {name}"}

    # --- conversation ------------------------------------------------------

    def reply(self, message: str) -> dict:
        """Answer one owner message. Returns {"reply", "actions"}."""
        s = effective(self.settings, self.store)
        history = [{"role": m["role"], "content": m["content"]}
                   for m in self.store.list_chat(HISTORY_MESSAGES)]
        self.store.add_chat("user", message)
        deps = build_deps(self.settings, self.store)
        deps.log.clear()  # only what the chat does goes to the journal
        actions: list[str] = []

        def call(name: str, args: dict) -> str:
            result = self.run_tool(deps, name, args)
            if name not in ("overview", "list_campaigns", "campaign_zones", "journal",
                            "site_products"):
                actions.append(f"{name} {json.dumps(args, ensure_ascii=False)} → "
                               f"{json.dumps(result, ensure_ascii=False)[:200]}")
            return json.dumps(result, ensure_ascii=False, default=str)

        try:
            backend = self._backend or make_backend(s)
            system = f"{SYSTEM}\n\nТЕКУЩЕЕ СОСТОЯНИЕ:\n{self.snapshot()}"
            text = backend.chat(system, history + [{"role": "user", "content": message}],
                                TOOLS, call)
        except Exception as exc:  # the chat must never take the panel down
            logger.exception("chat failed")
            text = f"Не получилось ответить: {exc}"
        text = text.strip() or "(пустой ответ)"
        self.store.add_chat("assistant", text)
        lines = [f"чат: {a}" for a in actions] + deps.log
        if lines:
            run_id = self.store.start_run(None, kind="chat")
            for line in lines:
                self.store.append_run_log(run_id, line)
            self.store.finish_run(run_id, True)
        return {"reply": text, "actions": actions}


def _flag_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _local(moment: datetime | None, tz: str) -> str | None:
    if moment is None:
        return None
    try:
        zone = ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    return moment.astimezone(zone).strftime("%Y-%m-%d %H:%M")


def _now_local(tz: str) -> str:
    try:
        zone = ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    return datetime.now(zone).strftime("%Y-%m-%d %H:%M")


# --- LLM backends with tool use ---------------------------------------------


class OpenAIChat:
    def __init__(self, settings: Settings):
        import openai

        self._client = openai.OpenAI(api_key=settings.openai_api_key)
        self._model = settings.chat_model or settings.openai_model

    def chat(self, system: str, messages: list[dict], tools: list[dict],
             call: Callable[[str, dict], str]) -> str:
        msgs: list = [{"role": "system", "content": system}, *messages]
        spec = [{"type": "function", "function": t} for t in tools]
        for _ in range(MAX_TOOL_ROUNDS):
            resp = self._client.chat.completions.create(
                model=self._model, messages=msgs, tools=spec, max_tokens=1500)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return msg.content or ""
            msgs.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except ValueError:
                    args = {}
                msgs.append({"role": "tool", "tool_call_id": tc.id,
                             "content": call(tc.function.name, args)})
        return "Слишком много шагов — уточните вопрос."


class AnthropicChat:
    def __init__(self, settings: Settings):
        import anthropic

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.chat_model or settings.anthropic_model

    def chat(self, system: str, messages: list[dict], tools: list[dict],
             call: Callable[[str, dict], str]) -> str:
        msgs: list = list(messages)
        spec = [{"name": t["name"], "description": t["description"],
                 "input_schema": t["parameters"]} for t in tools]
        for _ in range(MAX_TOOL_ROUNDS):
            resp = self._client.messages.create(
                model=self._model, system=system, messages=msgs, tools=spec, max_tokens=1500)
            text = "".join(b.text for b in resp.content if b.type == "text")
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses:
                return text
            msgs.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
            msgs.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "content": call(b.name, b.input)}
                for b in uses]})
        return "Слишком много шагов — уточните вопрос."


def make_backend(settings: Settings):
    if settings.llm_provider == "openai" and settings.openai_api_key:
        return OpenAIChat(settings)
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicChat(settings)
    raise RuntimeError("не настроен ключ нейросети (OPENAI_API_KEY / ANTHROPIC_API_KEY)")
