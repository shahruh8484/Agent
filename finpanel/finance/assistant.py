"""The chat assistant: a finance helper / bookkeeper backed by OpenAI or
Claude (whichever key is configured).

It reads the current books (built into the system prompt) and answers
questions. To change the books it *proposes* actions via tool calls —
nothing is written until the user presses "Подтвердить" in the chat, so a
misread screenshot or a misunderstood message never silently lands in
the accounting.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from datetime import date, timedelta

from finance import calc
from finance.db import Repo

logger = logging.getLogger(__name__)

MAX_HISTORY = 20

_DATE = {"type": "string", "description": "Дата в формате YYYY-MM-DD."}
_ADV_PAY = {"type": "string", "enum": ["approve", "valid"], "description": "За что платит рекл: approve — за апрув, valid — за валидный лид."}
_WEB_PAY = {"type": "string", "enum": ["approve", "lead"], "description": "За что платим вебу: approve — за апрув, lead — за любой лид."}
_RATE_FIELDS = {
    "valid_from": {**_DATE, "description": "С какой даты действуют ставки (YYYY-MM-DD)."},
    "adv_pay_type": _ADV_PAY,
    "adv_rate": {"type": "number", "description": "Сколько рекл платит, $ за единицу."},
    "guarantee_pct": {"type": "number", "description": "Гарант апрува рекла в %, 0 если нет."},
    "web_pay_type": _WEB_PAY,
    "web_rate": {"type": "number", "description": "Сколько платим вебу, $ за единицу."},
    "web_guarantee_pct": {"type": "number", "description": "Гарант, который владелец даёт вебу, в % от лидов (вебу платят за max(апрувы, лиды × %)); 0 если нет. Если владелец говорит, что гарант передаётся вебу, — тот же %, что у рекла."},
}
_LINK_KEY = {
    "web": {"type": "string", "description": "Имя веба."},
    "advertiser": {"type": "string", "description": "Имя рекла."},
    "offer": {"type": "string", "description": "Оффер; пустая строка, если у пары веб-рекл одна связка."},
}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties, "required": required},
    }


TOOLS = [
    _tool("add_web", "Добавить веба (поставщика трафика) или обновить его условия / имя в сети.",
          {"name": {"type": "string"}, "terms": {"type": "string", "description": "Условия выплат, например 'предоплата' или 'раз в неделю'."},
           "network_name": {"type": "string", "description": "Как веб называется в кабинете сети: номер (#106), почта или логин."}},
          ["name"]),
    _tool("add_advertiser", "Добавить рекла (покупателя трафика) или обновить его условия / имя в сети.",
          {"name": {"type": "string"}, "terms": {"type": "string"},
           "network_name": {"type": "string", "description": "Как рекл называется в кабинете сети, например 'Khadya Nur'."}},
          ["name"]),
    _tool("add_link", "Создать связку веб → оффер → рекл со ставками (направление 'Трафик').",
          {**_LINK_KEY, **_RATE_FIELDS},
          ["web", "advertiser", "offer", "valid_from", "adv_pay_type", "adv_rate", "guarantee_pct", "web_pay_type", "web_rate", "web_guarantee_pct"]),
    _tool("set_link_rate", "Изменить ставки СУЩЕСТВУЮЩЕЙ связки начиная с даты (старые дни считаются по старым ставкам). "
                           "Используй это, а не add_link, когда связка уже есть в списке.",
          {**_LINK_KEY, **_RATE_FIELDS},
          ["web", "advertiser", "offer", "valid_from", "adv_pay_type", "adv_rate", "guarantee_pct", "web_pay_type", "web_rate", "web_guarantee_pct"]),
    _tool("add_traffic_stat", "Записать статистику связки за день (перезаписывает этот день, если он уже был).",
          {**_LINK_KEY, "date": _DATE,
           "leads": {"type": "integer"}, "valid": {"type": "integer", "description": "Валидные лиды; если неизвестно — равно leads."},
           "approves": {"type": "integer"}},
          ["web", "advertiser", "offer", "date", "leads", "valid", "approves"]),
    _tool("add_payment", "Записать движение денег: рекл прислал деньги, вы оплатили вебу, курьерка перевела наложку.",
          {"party_type": {"type": "string", "enum": ["advertiser", "web", "courier", "owner"],
                          "description": "advertiser — деньги от рекла; web — выплата вебу; courier — деньги от службы доставки; "
                                         "owner — свои деньги владельца: вложил (+) или вывел (−), в прибыль не идут. "
                                         "Отрицательная сумма — возврат в обратную сторону."},
           "party_name": {"type": "string", "description": "Имя рекла или веба; для courier — пустая строка."},
           "direction": {"type": "string", "enum": ["traffic", "product"],
                         "description": "traffic — перепродажа трафика, product — свой товар. Реклы всегда traffic, курьерка всегда product."},
           "amount": {"type": "number"},
           "currency": {"type": "string", "enum": ["usd", "uzs"]},
           "date": _DATE,
           "note": {"type": "string"},
           "wallet": {"type": "string", "description": "Кошелёк, на который пришли / с которого ушли деньги (имя из списка кошельков); пустая строка, если не сказано."},},
          ["party_type", "party_name", "direction", "amount", "currency", "date"]),
    _tool("add_accrual", "Начисление вручную, когда нет статистики лидов за период (например, история до панели): "
                         "сколько веб заработал (вы ему должны) или сколько рекл должен вам за трафик. "
                         "Если владелец говорит «заплатил X и ещё должен Y» — начисление = X + Y.",
          {"party_type": {"type": "string", "enum": ["advertiser", "web"]},
           "party_name": {"type": "string"},
           "direction": {"type": "string", "enum": ["traffic", "product"], "description": "Для рекла всегда traffic."},
           "amount": {"type": "number", "description": "Сумма в $."},
           "date": _DATE,
           "note": {"type": "string"}},
          ["party_type", "party_name", "direction", "amount", "date"]),
    _tool("add_expense", "Записать прочий расход (сервер, программист, сервисы, комиссии и т.п.).",
          {"direction": {"type": "string", "enum": ["traffic", "product", "general"],
                         "description": "К какому направлению относится; general — общий на оба."},
           "category": {"type": "string", "description": "Короткая категория: Сервер, Программист, Комиссия..."},
           "amount": {"type": "number"},
           "currency": {"type": "string", "enum": ["usd", "uzs"]},
           "date": _DATE,
           "note": {"type": "string"},
           "wallet": {"type": "string", "description": "Кошелёк, на который пришли / с которого ушли деньги (имя из списка кошельков); пустая строка, если не сказано."},},
          ["direction", "category", "amount", "currency", "date"]),
    _tool("add_product", "Добавить свой товар (или обновить себестоимость).",
          {"name": {"type": "string"}, "unit_cost_usd": {"type": "number", "description": "Себестоимость 1 шт в $."},
           "initial_stock": {"type": "integer", "description": "Начальный остаток на складе, 0 если не нужно."},
           "date": _DATE},
          ["name", "unit_cost_usd", "initial_stock", "date"]),
    _tool("set_product_web_rate", "Ставка веба по своему товару начиная с даты.",
          {"web": {"type": "string"}, "product": {"type": "string"}, "valid_from": _DATE,
           "pay_type": _WEB_PAY, "rate_usd": {"type": "number"}},
          ["web", "product", "valid_from", "pay_type", "rate_usd"]),
    _tool("add_product_stat", "Записать лиды/апрувы веба по своему товару за день (перезаписывает день).",
          {"web": {"type": "string"}, "product": {"type": "string"}, "date": _DATE,
           "leads": {"type": "integer"}, "approves": {"type": "integer"}},
          ["web", "product", "date", "leads", "approves"]),
    _tool("add_order", "Записать отправленный заказ своего товара (товар списывается со склада).",
          {"product": {"type": "string"}, "qty": {"type": "integer", "description": "Сколько штук в заказе."},
           "amount_uzs": {"type": "number", "description": "Сумма заказа в сумах."},
           "delivery_uzs": {"type": "number", "description": "Стоимость доставки в сумах, 0 если неизвестна."},
           "ship_date": _DATE,
           "web": {"type": "string", "description": "От какого веба заказ; пустая строка, если неизвестно."},
           "note": {"type": "string"}},
          ["product", "qty", "amount_uzs", "delivery_uzs", "ship_date", "web"]),
    _tool("set_order_status", "Отметить заказ выкупленным (delivered), невыкупом (returned — товар вернётся на склад) или снова в пути (shipped).",
          {"order_id": {"type": "integer"}, "status": {"type": "string", "enum": ["delivered", "returned", "shipped"]},
           "date": _DATE,
           "delivery_uzs": {"type": "number", "description": "Если стала известна цена доставки — в сумах; иначе -1."}},
          ["order_id", "status", "date", "delivery_uzs"]),
    _tool("add_stock_purchase", "Записать закупку товара на склад.",
          {"product": {"type": "string"}, "qty": {"type": "integer"},
           "cost_usd": {"type": "number", "description": "Сколько заплатили за всю партию, $; 0 если уже учтено."},
           "date": _DATE,
           "wallet": {"type": "string", "description": "Кошелёк, на который пришли / с которого ушли деньги (имя из списка кошельков); пустая строка, если не сказано."},},
          ["product", "qty", "cost_usd", "date"]),
    _tool("update_settings", "Изменить курс сума, % оператора или % налога. Передавай -1 для того, что не меняется.",
          {"usd_uzs_rate": {"type": "number"}, "operator_pct": {"type": "number"}, "tax_pct": {"type": "number"}},
          ["usd_uzs_rate", "operator_pct", "tax_pct"]),
]

TOOL_NAMES = {t["name"] for t in TOOLS}

# Read-only tools: run right away on the server, the result goes back to the
# model. They never change data, so they need no confirmation.
READ_TOOLS = [
    _tool("get_report",
          "Посчитать точный отчёт панели за любой период: прибыль по трафику (по связкам), по товару, расходы, "
          "итог, кассу и закрыт ли период (гарант применён). Вызывай ВСЕГДА, когда спрашивают про даты/период, "
          "которых нет в готовых итогах.",
          {"start": {"type": "string", "description": "Первый день, YYYY-MM-DD."},
           "end": {"type": "string", "description": "Последний день включительно, YYYY-MM-DD."}},
          ["start", "end"]),
]
READ_TOOL_NAMES = {t["name"] for t in READ_TOOLS}
MAX_TOOL_ROUNDS = 4

SYSTEM_PROMPT = """Ты — финансовый помощник и бухгалтер владельца бизнеса. Говоришь по-русски, коротко и по делу, как опытный финдиректор: цифры, вывод, что делать.

У бизнеса два направления.

1. «Трафик» — перепродажа трафика. Владелец покупает лиды у вебов и продаёт их реклам, зарабатывая на разнице.
   - Рекл платит за апрув (approve) или за валидный лид (valid). У каждого рекла свой гарант апрува в %: если реальный апрув ниже, рекл платит как за гарант. Гарант считается за позапрошлый день (дни новее — предварительные), целыми апрувами с отбрасыванием дроби: 453 лида × 10% = 45.
   - Вебу платят за апрув (approve) или за любой лид (lead) — лид засчитывается всегда. Если вебу платят за апрув, владелец может давать ему свой гарант (web_guarantee_pct): тогда вебу платят за max(апрувы, лиды × гарант%).
   - Реклы обычно платят вперёд раз в неделю. Вебам — кому вперёд, кому позже. У каждого рекла и веба есть баланс.
2. «Мой товар» — владелец сам рекл: покупает лиды у вебов (те же вебы, но баланс отдельный), оператор прозванивает, заказ уходит наложенным платежом, служба доставки раз в неделю переводит деньги.
   - Клиент платит в сумах; курс задаётся в настройках. Оператор получает % от суммы только выкупленного заказа. Невыкуп сейчас бесплатный. Налоги — % в настройках.

Правила:
- Никогда не выдумывай и не пересчитывай цифры сам. Называй только цифры из данных ниже или из результата get_report. Про любой период, которого нет в «Итогах по периодам» (конкретные даты, «с 1 по 8», «сентябрь», «прошлая неделя»), сначала вызови get_report и отвечай его цифрами. Если данных не хватает — так и скажи.
- Последние 2 дня — предварительные (гарант рекла ещё не применён), прибыль за них занижена. Говоря «в плюсе ли я», опирайся на закрытые дни и скажи это.
- Чтобы внести или изменить данные — ОБЯЗАТЕЛЬНО вызывай инструменты в этом же ответе. Только по вызову инструмента у владельца появится кнопка «Подтвердить»; текст без вызова ничего не записывает. Инструменты не выполняются сразу: владелец увидит список и подтвердит. Поэтому не пиши «записал», пиши «вот что внесу — подтверди».
- Если из сообщения или скрина непонятно, кто это, какая дата, какая валюта или какая связка — сначала уточни, не угадывай.
- Используй имена вебов, реклов и товаров ровно как в списках ниже. Нового участника сначала добавь (add_web / add_advertiser / add_product).
- «Сегодня», «вчера», «позавчера» переводи в даты относительно сегодняшней даты ниже.
- На скриншотах из кабинетов внимательно выпиши цифры по каждой строке, покажи их и предложи действия.
- Скрин «Общая статистика» из CPA-сети: сверху фильтры «Веб-мастер» (например «#106 (почта)») и «Рекламодатель» (например «Khadya Nur») — найди по ним веба и рекла в списках ниже (поле «в сети»), а значит и связку. Если не находишь или фильтр пустой — спроси, чья это статистика. Строки таблицы — дни (строку «Итого» не вноси). Колонки «Конверсии»: Σ — все лиды (leads), Σв — валидные (valid), зелёная галочка — апрувы (approves), часы — в обработке, красный крестик — отклонённые; остальное не нужно. На каждый день — отдельный add_traffic_stat. Повторный скрин за те же дни нормален: апрувы досчитываются задним числом, запись дня перезаписывается.
- Когда спрашивают «я в плюсе или минусе» — отвечай по прибыли (начисления) и отдельно по деньгам на руках (касса), и объясни разницу одной фразой. Касса — это движение денег (предоплаты реклов, выплаты вебам), она не равна прибыли."""


# --------------------------------------------------------------------------
# Context for the model
# --------------------------------------------------------------------------


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _party_line(row) -> str:
    extra = [x for x in (row["terms"], f"в сети: {row['note']}" if row["note"] else "") if x]
    return row["name"] + (f" ({'; '.join(extra)})" if extra else "")


def build_context(repo: Repo, today: date) -> str:
    s = repo.settings()
    lines = [
        f"Сегодня: {today.isoformat()} (вчера {(today - timedelta(days=1)).isoformat()}, "
        f"позавчера {(today - timedelta(days=2)).isoformat()}).",
        f"Настройки: курс {s['usd_uzs_rate']:,.0f} сум/$, оператор {s['operator_pct']}%, налог {s['tax_pct']}%.",
        "",
        "Кошельки: " + (", ".join(f"{w.name} ({'сум' if w.currency == 'uzs' else '$'}, остаток {w.balance:,.2f})"
                                  for w in calc.wallet_states(repo)) or "нет"),
        "Вебы: " + (", ".join(_party_line(w) for w in repo.webs()) or "нет"),
        "Реклы: " + (", ".join(_party_line(a) for a in repo.advertisers()) or "нет"),
        "",
        "Связки (трафик) и текущие ставки:",
    ]
    links = repo.links()
    if not links:
        lines.append("  нет")
    for l in links:
        r = repo.rate_on(l["id"], today.isoformat())
        rate = (
            f"рекл платит ${r.adv_rate} за {'апрув' if r.adv_pay_type == 'approve' else 'валид'}"
            f", гарант {r.guarantee_pct}%; вебу ${r.web_rate} за {'апрув' if r.web_pay_type == 'approve' else 'лид'}"
            + (f", гарант вебу {r.web_guarantee_pct}%" if r.web_guarantee_pct else "")
            if r else "ставки не заданы"
        )
        lines.append(f"  #{l['id']} {l['web_name']} → {l['advertiser_name']} оффер '{l['offer']}': {rate}"
                     + ("" if l["active"] else " [выключена]"))

    lines += ["", "Товары:"]
    products = repo.products()
    if not products:
        lines.append("  нет")
    stock = {st.product_id: st for st in calc.stock_info(repo, today)}
    for p in products:
        st = stock.get(p["id"])
        lines.append(f"  {p['name']}: себестоимость ${p['unit_cost_usd']}/шт, на складе {st.on_hand if st else 0} шт"
                     + (f", хватит ~{st.days_left} дн." if st and st.days_left else ""))
    for r in repo.product_web_rates():
        lines.append(f"  ставка: {r['web_name']} по {r['product_name']} с {r['valid_from']} — ${r['rate_usd']} за "
                     + ("апрув" if r["pay_type"] == "approve" else "лид"))

    open_orders = repo.orders(status="shipped", limit=60)
    lines += ["", f"Заказы в пути ({len(open_orders)} последних):"]
    for o in open_orders:
        lines.append(f"  #{o['id']} {o['ship_date']} {o['product_name']} {o['qty']} шт {o['amount_uzs']:,.0f} сум"
                     + (f" от {o['web_name']}" if o["web_name"] else ""))

    lines += ["", "Балансы (всё время):"]
    for b in calc.advertiser_balances(repo, today):
        lines.append(f"  рекл {b.name}: получено {_money(b.paid)}, отработано {_money(b.accrued)}, баланс {_money(b.balance)}")
    for direction, label in (("traffic", "трафик"), ("product", "товар")):
        for b in calc.web_balances(repo, today, direction):
            lines.append(f"  веб {b.name} ({label}): выплачено {_money(b.paid)}, начислено {_money(b.accrued)}, баланс {_money(b.balance)}")
    cb = calc.courier_balance(repo)
    lines.append(f"  курьерка должна перечислить: {cb.owed_uzs:,.0f} сум")

    lines += ["", "Итоги по периодам (прибыль по начислениям / касса):"]
    for key, label in (("today", "сегодня"), ("7d", "7 дней"), ("this_month", "этот месяц"), ("all", "всё время")):
        start, end = calc.period_range(key, today)
        sm = calc.summary(repo, start, end, today)
        lines.append(
            f"  {label}: трафик {_money(sm.traffic.net_profit)}, товар {_money(sm.product.profit)}, "
            f"общие расходы {_money(sm.general_expenses)}, итого {_money(sm.total_profit)}; касса {_money(sm.cash.net)}"
        )

    warn = calc.alerts(repo, today)
    if warn:
        lines += ["", "Предупреждения:"] + [f"  - {a.text}" for a in warn]
    return "\n".join(lines)


def _summary_lines(repo: Repo, start: str, end: str, today: date, links: dict) -> list[str]:
    sm = calc.summary(repo, start, end, today)
    t = sm.traffic
    lines = [
        f"  ТРАФИК: реклы начислили {_money(t.total.adv_amount)} (из них за счёт гаранта {_money(t.total.guarantee_bonus)}), "
        f"вебам начислено {_money(t.total.web_amount)}, расходы трафика {_money(t.expenses)} → прибыль {_money(t.net_profit)}; "
        f"лидов {t.total.leads}, апрувов {t.total.approves}",
    ]
    for link_id, agg in sorted(t.by_link.items(), key=lambda kv: -kv[1].profit):
        l = links.get(link_id)
        name = f"{l['web_name']} → {l['advertiser_name']}" + (f" ({l['offer']})" if l and l["offer"] else "") if l else f"связка #{link_id}"
        lines.append(f"    {name}: лидов {agg.leads}, апрувов {agg.approves}, рекл {_money(agg.adv_amount)}, "
                     f"веб {_money(agg.web_amount)}, прибыль {_money(agg.profit)}"
                     + (" (в плюсе только за счёт гаранта)" if agg.only_by_guarantee else ""))
    manual = [c for c in repo.accruals(start, end) if c["direction"] == "traffic"]
    if manual:
        lines.append(f"    ручные начисления по трафику: {len(manual)} шт, учтены в суммах выше")
    p = sm.product
    lines.append(f"  МОЙ ТОВАР: выручка {_money(p.revenue)}, себестоимость {_money(p.cogs)}, оператор {_money(p.operator)}, "
                 f"доставка {_money(p.delivery)}, налог {_money(p.tax)}, вебам {_money(p.web_cost)}, расходы {_money(p.expenses)} "
                 f"→ прибыль {_money(p.profit)}; выкуплено {p.delivered}, невыкуп {p.returned}")
    lines.append(f"  ОБЩИЕ РАСХОДЫ: {_money(sm.general_expenses)}")
    lines.append(f"  ИТОГО ПРИБЫЛЬ: {_money(sm.total_profit)}")
    c = sm.cash
    lines.append(f"  КАССА (движение денег, не прибыль): пришло {_money(c.inflow)}, ушло {_money(c.outflow)}, разница {_money(c.net)}"
                 + (f"; свои деньги вложил/вывел {_money(c.owner)}" if c.owner else ""))
    return lines


def period_report(repo: Repo, start: str, end: str, today: date) -> str:
    """Exact panel figures for any date range — what the assistant quotes
    instead of guessing."""
    try:
        d1, d2 = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        return "Ошибка: даты нужны в формате YYYY-MM-DD."
    if d1 > d2:
        d1, d2 = d2, d1
    start, end = d1.isoformat(), d2.isoformat()
    links = {l["id"]: l for l in repo.links()}
    closed_until = today - timedelta(days=calc.GUARANTEE_LAG_DAYS)
    lines = [f"Отчёт панели за {start} — {end}:"]
    if d2 <= closed_until:
        lines.append("  Все дни закрыты, гарант применён — цифры окончательные.")
        lines += _summary_lines(repo, start, end, today, links)
    elif d1 > closed_until:
        lines.append("  Все дни ПРЕДВАРИТЕЛЬНЫЕ: гарант рекла ещё не применён, прибыль по трафику занижена, апрувы могут досчитаться.")
        lines += _summary_lines(repo, start, end, today, links)
    else:
        lines.append(f"  Дни после {closed_until.isoformat()} предварительные (гарант ещё не применён).")
        lines += _summary_lines(repo, start, end, today, links)
        lines.append("")
        lines.append(f"Только закрытые дни {start} — {closed_until.isoformat()} (на это опирайся, говоря «в плюсе ли я»):")
        lines += _summary_lines(repo, start, closed_until.isoformat(), today, links)
    return "\n".join(lines)


def run_read_tool(repo: Repo, today: date, name: str, args: dict) -> str:
    if name == "get_report":
        return period_report(repo, str(args.get("start", "")), str(args.get("end", "")), today)
    return f"Неизвестный инструмент {name}"


# --------------------------------------------------------------------------
# Applying proposed actions
# --------------------------------------------------------------------------


def _usd(repo: Repo, amount: float, currency: str) -> tuple[float, float | None, float | None]:
    if currency == "uzs":
        rate = repo.usd_uzs_rate()
        return amount / rate, amount, rate
    return amount, None, None


def _require(row, what: str, name: str):
    if row is None:
        raise ValueError(f"{what} «{name}» не найден. Сначала добавьте его.")
    return row


def _find_link(repo: Repo, a: dict):
    link = repo.find_link(a["web"], a["advertiser"], a.get("offer", ""))
    if link is None:
        raise ValueError(f"Связка {a['web']} → {a['advertiser']} '{a.get('offer', '')}' не найдена (или их несколько — укажите оффер).")
    return link


def _wallet(repo: Repo, a: dict) -> int | None:
    name = (a.get("wallet") or "").strip()
    if not name:
        return None
    return _require(repo.find_wallet(name), "Кошелёк", name)["id"]


def apply_action(repo: Repo, name: str, a: dict) -> str:
    """Run one confirmed action. Returns a short description of what was
    done; raises ValueError with a readable message on bad input."""
    if name == "add_web":
        repo.add_web(a["name"], a.get("terms", ""), a.get("network_name", ""))
        return f"Веб {a['name']} сохранён."
    if name == "add_advertiser":
        repo.add_advertiser(a["name"], a.get("terms", ""), a.get("network_name", ""))
        return f"Рекл {a['name']} сохранён."
    if name in ("add_link", "set_link_rate"):
        rate_args = (a["valid_from"], a["adv_pay_type"], float(a["adv_rate"]), float(a["guarantee_pct"]),
                     a["web_pay_type"], float(a["web_rate"]), float(a.get("web_guarantee_pct") or 0))
        if name == "add_link":
            web = _require(repo.find_web(a["web"]), "Веб", a["web"])
            adv = _require(repo.find_advertiser(a["advertiser"]), "Рекл", a["advertiser"])
            existed = repo.same_link(web["id"], adv["id"], a.get("offer", ""))
            repo.add_link(web["id"], adv["id"], a.get("offer", ""), *rate_args)
            if existed:
                return f"Связка {web['name']} → {adv['name']} уже была — ставки с {a['valid_from']} обновлены."
            return f"Связка {web['name']} → {adv['name']} создана."
        link = _find_link(repo, a)
        repo.set_link_rate(link["id"], *rate_args)
        return f"Ставки связки #{link['id']} с {a['valid_from']} обновлены."
    if name == "add_traffic_stat":
        link = _find_link(repo, a)
        repo.upsert_traffic_stat(link["id"], a["date"], int(a["leads"]), int(a["valid"]), int(a["approves"]))
        return f"Статистика связки #{link['id']} за {a['date']} записана."
    if name == "add_payment":
        party_id = None
        if a["party_type"] == "web":
            party_id = _require(repo.find_web(a["party_name"]), "Веб", a["party_name"])["id"]
        elif a["party_type"] == "advertiser":
            party_id = _require(repo.find_advertiser(a["party_name"]), "Рекл", a["party_name"])["id"]
        direction = {"advertiser": "traffic", "courier": "product", "owner": "general"}.get(a["party_type"], a["direction"])
        usd, uzs, rate = _usd(repo, float(a["amount"]), a["currency"])
        repo.add_payment(a["date"], direction, a["party_type"], party_id, usd, uzs, rate, a.get("note", ""),
                         wallet_id=_wallet(repo, a))
        return f"Платёж {_money(usd)} записан."
    if name == "add_accrual":
        finder = repo.find_web if a["party_type"] == "web" else repo.find_advertiser
        party = _require(finder(a["party_name"]), "Веб" if a["party_type"] == "web" else "Рекл", a["party_name"])
        repo.add_accrual(a["date"], a["direction"], a["party_type"], party["id"], float(a["amount"]), a.get("note", ""))
        return f"Начисление {_money(float(a['amount']))} для {party['name']} записано."
    if name == "add_expense":
        usd, uzs, rate = _usd(repo, float(a["amount"]), a["currency"])
        repo.add_expense(a["date"], a["direction"], a["category"], usd, uzs, rate, a.get("note", ""),
                         wallet_id=_wallet(repo, a))
        return f"Расход {a['category']} {_money(usd)} записан."
    if name == "add_product":
        repo.add_product(a["name"], float(a["unit_cost_usd"]), int(a.get("initial_stock") or 0), a["date"])
        return f"Товар {a['name']} сохранён."
    if name == "set_product_web_rate":
        web = _require(repo.find_web(a["web"]), "Веб", a["web"])
        product = _require(repo.find_product(a["product"]), "Товар", a["product"])
        repo.set_product_web_rate(web["id"], product["id"], a["valid_from"], a["pay_type"], float(a["rate_usd"]))
        return f"Ставка {web['name']} по {product['name']} сохранена."
    if name == "add_product_stat":
        web = _require(repo.find_web(a["web"]), "Веб", a["web"])
        product = _require(repo.find_product(a["product"]), "Товар", a["product"])
        repo.upsert_product_stat(web["id"], product["id"], a["date"], int(a["leads"]), int(a["approves"]))
        return f"Статистика {web['name']} по {product['name']} за {a['date']} записана."
    if name == "add_order":
        product = _require(repo.find_product(a["product"]), "Товар", a["product"])
        web_id = None
        if a.get("web"):
            web_id = _require(repo.find_web(a["web"]), "Веб", a["web"])["id"]
        order_id = repo.add_order(a["ship_date"], product["id"], int(a["qty"]), float(a["amount_uzs"]),
                                  float(a.get("delivery_uzs") or 0), web_id, note=a.get("note", ""))
        return f"Заказ #{order_id} записан."
    if name == "set_order_status":
        delivery = a.get("delivery_uzs")
        delivery = None if delivery is None or float(delivery) < 0 else float(delivery)
        repo.set_order_status(int(a["order_id"]), a["status"], a["date"], delivery)
        return f"Заказ #{a['order_id']}: статус {a['status']}."
    if name == "add_stock_purchase":
        product = _require(repo.find_product(a["product"]), "Товар", a["product"])
        if int(a["qty"]) <= 0:
            raise ValueError("Количество закупки должно быть больше нуля.")
        repo.add_stock_move(a["date"], product["id"], int(a["qty"]), "purchase", cost_usd=float(a.get("cost_usd") or 0),
                            wallet_id=_wallet(repo, a))
        return f"Закупка {a['qty']} шт {product['name']} записана."
    if name == "update_settings":
        changed = []
        for key in ("usd_uzs_rate", "operator_pct", "tax_pct"):
            value = a.get(key)
            if value is not None and float(value) >= 0:
                repo.set_setting(key, float(value))
                changed.append(key)
        return "Настройки обновлены." if changed else "Нечего менять."
    raise ValueError(f"Неизвестное действие: {name}")


_PARTY = {"advertiser": "от рекла", "web": "вебу", "courier": "от курьерки", "owner": "свои деньги"}
_DIR = {"traffic": "трафик", "product": "товар", "general": "общее"}
_STATUS = {"delivered": "выкуплен", "returned": "невыкуп (на склад)", "shipped": "в пути"}


def describe_action(name: str, a: dict) -> str:
    """One readable line per proposed action, shown before confirmation."""
    text = _describe(name, a)
    if a.get("wallet") and name in ("add_payment", "add_expense", "add_stock_purchase"):
        text += f" · кошелёк: {a['wallet']}"
    return text


def _describe(name: str, a: dict) -> str:
    try:
        if name == "add_web":
            return (f"Веб {a['name']}" + (f" ({a['terms']})" if a.get("terms") else "")
                    + (f", в сети: {a['network_name']}" if a.get("network_name") else ""))
        if name == "add_advertiser":
            return (f"Рекл {a['name']}" + (f" ({a['terms']})" if a.get("terms") else "")
                    + (f", в сети: {a['network_name']}" if a.get("network_name") else ""))
        if name in ("add_link", "set_link_rate"):
            verb = "Новая связка" if name == "add_link" else "Новые ставки связки"
            return (
                f"{verb} {a['web']} → {a['advertiser']}" + (f" ({a['offer']})" if a.get("offer") else "")
                + f" с {a['valid_from']}: рекл ${a['adv_rate']} за {'апрув' if a['adv_pay_type'] == 'approve' else 'валид'}"
                + f", гарант {a['guarantee_pct']}%, вебу ${a['web_rate']} за {'апрув' if a['web_pay_type'] == 'approve' else 'лид'}"
                + (f", гарант вебу {a['web_guarantee_pct']}%" if a.get("web_guarantee_pct") else "")
            )
        if name == "add_traffic_stat":
            return (f"Статистика {a['web']} → {a['advertiser']}" + (f" ({a['offer']})" if a.get("offer") else "")
                    + f" за {a['date']}: лидов {a['leads']}, валид {a['valid']}, апрувов {a['approves']}")
        if name == "add_payment":
            cur = "сум" if a["currency"] == "uzs" else "$"
            who = a.get("party_name") or ""
            return f"Платёж {_PARTY.get(a['party_type'], '')} {who}: {a['amount']:,} {cur}, {a['date']} ({_DIR.get(a['direction'], '')})".replace("  ", " ")
        if name == "add_accrual":
            who = ("вебу " if a["party_type"] == "web" else "рекл должен: ") + a["party_name"]
            return f"Начисление вручную {who}: ${float(a['amount']):,.2f}, {a['date']}" + (f" ({a['note']})" if a.get("note") else "")
        if name == "add_expense":
            cur = "сум" if a["currency"] == "uzs" else "$"
            return f"Расход «{a['category']}» {a['amount']:,} {cur}, {a['date']} ({_DIR.get(a['direction'], '')})"
        if name == "add_product":
            return f"Товар {a['name']}: себестоимость ${a['unit_cost_usd']}, начальный остаток {a.get('initial_stock', 0)} шт"
        if name == "set_product_web_rate":
            return f"Ставка {a['web']} по {a['product']} с {a['valid_from']}: ${a['rate_usd']} за {'апрув' if a['pay_type'] == 'approve' else 'лид'}"
        if name == "add_product_stat":
            return f"{a['web']} по {a['product']} за {a['date']}: лидов {a['leads']}, апрувов {a['approves']}"
        if name == "add_order":
            return (f"Заказ {a['ship_date']}: {a['product']} {a['qty']} шт за {a['amount_uzs']:,.0f} сум, доставка {float(a.get('delivery_uzs') or 0):,.0f} сум"
                    + (f", от {a['web']}" if a.get("web") else ""))
        if name == "set_order_status":
            return f"Заказ #{a['order_id']} — {_STATUS.get(a['status'], a['status'])}, {a['date']}"
        if name == "add_stock_purchase":
            return f"Закупка {a['product']}: {a['qty']} шт за ${a.get('cost_usd', 0)}, {a['date']}"
        if name == "update_settings":
            parts = []
            if float(a.get("usd_uzs_rate", -1)) >= 0:
                parts.append(f"курс {a['usd_uzs_rate']:,} сум/$")
            if float(a.get("operator_pct", -1)) >= 0:
                parts.append(f"оператор {a['operator_pct']}%")
            if float(a.get("tax_pct", -1)) >= 0:
                parts.append(f"налог {a['tax_pct']}%")
            return "Настройки: " + ", ".join(parts)
    except (KeyError, TypeError, ValueError):
        pass
    return f"{name}: {a}"


# --------------------------------------------------------------------------
# LLM backend
# --------------------------------------------------------------------------


class AssistantError(RuntimeError):
    pass


class ClaudeBackend:
    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise AssistantError("ANTHROPIC_API_KEY не задан — чат и распознавание скринов недоступны.")
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def _create(self, system: str, messages: list[dict], tools: list[dict]):
        try:
            return self._client.beta.messages.create(
                model=self._model,
                max_tokens=16000,
                system=system,
                messages=messages,
                tools=tools,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except self._anthropic.APIStatusError as exc:
            logger.exception("Claude API error")
            raise AssistantError(f"Ошибка Claude API ({exc.status_code}): {exc.message}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise AssistantError("Нет связи с Claude API.") from exc

    def respond(self, system: str, messages: list[dict], read_tool=None) -> tuple[str, list[dict]]:
        """read_tool(name, args) -> str runs read-only tools (reports) and
        feeds the result back; write tools are only collected as proposals."""
        tools = TOOLS + READ_TOOLS if read_tool else TOOLS
        msgs = list(messages)
        texts, actions = [], []
        for _ in range(MAX_TOOL_ROUNDS):
            response = self._create(system, msgs, tools)
            if response.stop_reason == "refusal":
                return "Модель отказалась отвечать на этот запрос. Переформулируйте, пожалуйста.", []
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            if text:
                texts.append(text)
            uses = [b for b in response.content if b.type == "tool_use"]
            actions += [{"name": b.name, "input": dict(b.input)} for b in uses if b.name in TOOL_NAMES]
            if not read_tool or response.stop_reason != "tool_use" or not any(b.name in READ_TOOL_NAMES for b in uses):
                break
            results = [
                {"type": "tool_result", "tool_use_id": b.id,
                 "content": read_tool(b.name, dict(b.input)) if b.name in READ_TOOL_NAMES
                 else "Предложено, ждёт подтверждения владельца."}
                for b in uses
            ]
            msgs = msgs + [{"role": "assistant", "content": response.content}, {"role": "user", "content": results}]
        return "\n\n".join(texts), actions


def _openai_tools(tools: list[dict]) -> list[dict]:
    return [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
        for t in tools
    ]


OPENAI_TOOLS = _openai_tools(TOOLS)
OPENAI_READ_TOOLS = _openai_tools(READ_TOOLS)


def _to_openai_content(content: list[dict] | str) -> list[dict] | str:
    """Our messages use the Anthropic content shape (text / base64 image
    blocks); convert image blocks to OpenAI's data-URL form."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if block["type"] == "image":
            src = block["source"]
            parts.append({"type": "image_url", "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}})
        else:
            parts.append({"type": "text", "text": block["text"]})
    return parts


class OpenAIBackend:
    def __init__(self, api_key: str, model: str, client=None):
        if not api_key and client is None:
            raise AssistantError("OPENAI_API_KEY не задан — чат и распознавание скринов недоступны.")
        import openai

        self._openai = openai
        self._client = client or openai.OpenAI(api_key=api_key)
        self._model = model

    def _complete(self, chat: list[dict], tool_choice: str = "auto", tools: list[dict] | None = None):
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=chat,
                tools=tools or OPENAI_TOOLS,
                tool_choice=tool_choice,
                max_completion_tokens=4000,
            )
        except self._openai.APIStatusError as exc:
            logger.exception("OpenAI API error")
            raise AssistantError(f"Ошибка OpenAI API ({exc.status_code}): {exc.message}") from exc
        except self._openai.APIConnectionError as exc:
            raise AssistantError("Нет связи с OpenAI API.") from exc
        return response.choices[0].message

    @staticmethod
    def _calls(message) -> list[tuple[object, str, dict]]:
        """(call, name, args) for every well-formed function call."""
        calls = []
        for call in message.tool_calls or []:
            if call.type != "function" or call.function.name not in TOOL_NAMES | READ_TOOL_NAMES:
                continue
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                logger.warning("Skipping tool call with invalid JSON: %s", call.function.name)
                continue
            calls.append((call, call.function.name, args))
        return calls

    def respond(self, system: str, messages: list[dict], read_tool=None) -> tuple[str, list[dict]]:
        """read_tool(name, args) -> str runs read-only tools (reports) and
        feeds the result back; write tools are only collected as proposals."""
        chat = [{"role": "system", "content": system}] + [
            {"role": m["role"], "content": _to_openai_content(m["content"])} for m in messages
        ]
        tools = OPENAI_TOOLS + OPENAI_READ_TOOLS if read_tool else OPENAI_TOOLS
        texts, actions = [], []
        for _ in range(MAX_TOOL_ROUNDS):
            message = self._complete(chat, tools=tools)
            text = (message.content or "").strip()
            if text:
                texts.append(text)
            calls = self._calls(message)
            actions += [{"name": name, "input": args} for _, name, args in calls if name in TOOL_NAMES]
            if not read_tool or not any(name in READ_TOOL_NAMES for _, name, _ in calls):
                break
            # Every tool call in the assistant turn needs a matching tool reply.
            raw = [c for c in message.tool_calls or [] if c.type == "function"]
            chat.append({"role": "assistant", "content": message.content, "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in raw
            ]})
            parsed = {id(c): (name, args) for c, name, args in calls}
            for c in raw:
                name, args = parsed.get(id(c), (c.function.name, None))
                if args is None:
                    result = "Ошибка: некорректный вызов."
                elif name in READ_TOOL_NAMES:
                    result = read_tool(name, args)
                else:
                    result = "Предложено, ждёт подтверждения владельца."
                chat.append({"role": "tool", "tool_call_id": c.id, "content": result})
        text = "\n\n".join(texts)
        if not actions and _announces_actions(text):
            # GPT models sometimes describe the change in prose ("проверь и
            # подтверди") without emitting the tool call, which leaves the
            # owner with nothing to confirm. Ask once more, tools required.
            followup = chat + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": "Оформи ровно то, что ты предложил выше, вызовами инструментов. Ничего не добавляй от себя."},
            ]
            message = self._complete(followup, tool_choice="required")
            actions = [{"name": name, "input": args} for _, name, args in self._calls(message) if name in TOOL_NAMES]
        return text, actions


_PROPOSAL = re.compile(r"подтверд|предлага\w*\s+(внести|создать|записать|добавить)|вот что (внес|запиш|добав)", re.IGNORECASE)


def _announces_actions(text: str) -> bool:
    """Reply reads like 'here is what I'll record — confirm', not a question."""
    return bool(text) and "?" not in text and bool(_PROPOSAL.search(text))


def make_backend(settings):
    from finance.config import assistant_provider

    provider = assistant_provider(settings)
    if provider == "openai":
        return OpenAIBackend(settings.openai_api_key, settings.openai_model)
    if provider == "anthropic":
        return ClaudeBackend(settings.anthropic_api_key, settings.anthropic_model)
    raise AssistantError(
        "Помощник не подключён: добавьте OPENAI_API_KEY (или ANTHROPIC_API_KEY) в .env на сервере "
        "и перезапустите панель."
    )


def history_to_messages(history: list[dict]) -> list[dict]:
    """Past chat as plain text turns. Proposed actions are summarized in
    text so the model knows what was applied, without replaying tool_use
    blocks that never got tool_results."""
    status_label = {"pending": "ждёт подтверждения", "applied": "применено", "rejected": "отклонено"}
    msgs = []
    for m in history[-MAX_HISTORY:]:
        content = m["content"]
        if m["actions"]:
            listed = "; ".join(describe_action(a["name"], a["input"]) for a in m["actions"])
            content += f"\n[Предложенные действия: {listed}. Статус: {status_label.get(m['actions_status'], m['actions_status'])}]"
        msgs.append({"role": m["role"], "content": content or "(пусто)"})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


def user_content(text: str, image: tuple[bytes, str] | None) -> list[dict] | str:
    if not image:
        return text
    data, media_type = image
    return [
        {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                     "data": base64.standard_b64encode(data).decode()}},
        {"type": "text", "text": text or "Разбери этот скрин и предложи, что внести."},
    ]
