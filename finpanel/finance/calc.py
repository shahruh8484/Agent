"""The accounting itself: turns raw stats/payments/orders into profit,
balances and warnings. Pure reads over Repo — no writes.

Two views of "am I in plus or minus":
- profit (начисления): what was earned in the period, whether or not the
  money has arrived yet;
- cash (касса): money that actually came in minus money that went out.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from finance.db import Repo

PERIODS = [
    ("today", "Сегодня"),
    ("yesterday", "Вчера"),
    ("7d", "7 дней"),
    ("30d", "30 дней"),
    ("this_month", "Этот месяц"),
    ("last_month", "Прошлый месяц"),
    ("all", "Всё время"),
]

# Approves for day D are final on D+2, so the guarantee is applied to the
# day before yesterday and earlier; newer days are shown as preliminary.
GUARANTEE_LAG_DAYS = 2


def period_range(period: str, today: date) -> tuple[str | None, str | None]:
    if period == "today":
        return today.isoformat(), today.isoformat()
    if period == "yesterday":
        d = today - timedelta(days=1)
        return d.isoformat(), d.isoformat()
    if period == "7d":
        return (today - timedelta(days=6)).isoformat(), today.isoformat()
    if period == "30d":
        return (today - timedelta(days=29)).isoformat(), today.isoformat()
    if period == "this_month":
        return today.replace(day=1).isoformat(), today.isoformat()
    if period == "last_month":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1).isoformat(), last.isoformat()
    return None, None


def _in(d: str | None, start: str | None, end: str | None) -> bool:
    if d is None:
        return False
    return (start is None or d >= start) and (end is None or d <= end)


def pct(part: float, whole: float) -> float | None:
    return round(part / whole * 100, 1) if whole else None


# --------------------------------------------------------------------------
# Direction 1: traffic resale
# --------------------------------------------------------------------------


@dataclass
class TrafficRow:
    """Money for one link on one day."""

    stat_id: int
    link_id: int
    date: str
    leads: int
    valid: int
    approves: int
    final: bool
    adv_amount: float = 0.0
    guarantee_bonus: float = 0.0      # extra the rekl paid you because of his guarantee
    web_amount: float = 0.0
    web_guarantee_cost: float = 0.0   # extra you paid the web because of your guarantee to him
    missing_rate: bool = False

    @property
    def profit(self) -> float:
        return self.adv_amount - self.web_amount


def guaranteed_approves(leads: int, pct_value: float) -> int:
    """Guarantee counts whole approves, fraction dropped: 453 leads at 10%
    is 45 approves, not 45.3. Rounded first to absorb float noise
    (e.g. 300 * 7 / 100 = 21.000000000000004)."""
    return math.floor(round(leads * pct_value / 100, 6))


def traffic_row(repo: Repo, stat, today: date) -> TrafficRow:
    final = stat["date"] <= (today - timedelta(days=GUARANTEE_LAG_DAYS)).isoformat()
    row = TrafficRow(
        stat_id=stat["id"],
        link_id=stat["link_id"],
        date=stat["date"],
        leads=stat["leads"],
        valid=stat["valid"],
        approves=stat["approves"],
        final=final,
    )
    rate = repo.rate_on(stat["link_id"], stat["date"])
    if rate is None:
        row.missing_rate = True
        return row

    if rate.adv_pay_type == "valid":
        row.adv_amount = stat["valid"] * rate.adv_rate
    else:
        paid_approves = float(stat["approves"])
        if final and rate.guarantee_pct > 0:
            paid_approves = max(paid_approves, guaranteed_approves(stat["leads"], rate.guarantee_pct))
        row.adv_amount = paid_approves * rate.adv_rate
        row.guarantee_bonus = (paid_approves - stat["approves"]) * rate.adv_rate

    if rate.web_pay_type == "lead":
        row.web_amount = stat["leads"] * rate.web_rate
    else:
        web_approves = float(stat["approves"])
        if final and rate.web_guarantee_pct > 0:
            web_approves = max(web_approves, guaranteed_approves(stat["leads"], rate.web_guarantee_pct))
        row.web_amount = web_approves * rate.web_rate
        row.web_guarantee_cost = (web_approves - stat["approves"]) * rate.web_rate
    return row


@dataclass
class Agg:
    leads: int = 0
    valid: int = 0
    approves: int = 0
    adv_amount: float = 0.0
    guarantee_bonus: float = 0.0
    web_amount: float = 0.0
    web_guarantee_cost: float = 0.0
    preliminary: bool = False

    def add(self, r: TrafficRow) -> None:
        self.leads += r.leads
        self.valid += r.valid
        self.approves += r.approves
        self.adv_amount += r.adv_amount
        self.guarantee_bonus += r.guarantee_bonus
        self.web_amount += r.web_amount
        self.web_guarantee_cost += r.web_guarantee_cost
        self.preliminary = self.preliminary or not r.final

    @property
    def profit(self) -> float:
        return self.adv_amount - self.web_amount

    @property
    def approve_pct(self) -> float | None:
        return pct(self.approves, self.leads)

    @property
    def profit_without_guarantee(self) -> float:
        """What the link would make if neither side applied a guarantee."""
        return self.profit - self.guarantee_bonus + self.web_guarantee_cost

    @property
    def only_by_guarantee(self) -> bool:
        return self.profit > 0 and self.profit_without_guarantee < 0


@dataclass
class TrafficReport:
    total: Agg
    by_link: dict[int, Agg]
    by_advertiser: dict[int, Agg]
    by_web: dict[int, Agg]
    rows: list[TrafficRow]
    expenses: float

    @property
    def net_profit(self) -> float:
        return self.total.profit - self.expenses


def traffic_report(repo: Repo, start: str | None, end: str | None, today: date) -> TrafficReport:
    links = {l["id"]: l for l in repo.links()}
    total, by_link, by_adv, by_web, rows = Agg(), {}, {}, {}, []
    for stat in repo.traffic_stats(start, end):
        r = traffic_row(repo, stat, today)
        rows.append(r)
        link = links.get(r.link_id)
        total.add(r)
        by_link.setdefault(r.link_id, Agg()).add(r)
        if link:
            by_adv.setdefault(link["advertiser_id"], Agg()).add(r)
            by_web.setdefault(link["web_id"], Agg()).add(r)
    for c in repo.accruals(start, end):
        if c["direction"] != "traffic":
            continue
        if c["party_type"] == "advertiser":
            total.adv_amount += c["amount_usd"]
            by_adv.setdefault(c["party_id"], Agg()).adv_amount += c["amount_usd"]
        else:
            total.web_amount += c["amount_usd"]
            by_web.setdefault(c["party_id"], Agg()).web_amount += c["amount_usd"]
    expenses = sum(e["amount_usd"] for e in repo.expenses(start, end) if e["direction"] == "traffic")
    return TrafficReport(total, by_link, by_adv, by_web, rows, expenses)


# --------------------------------------------------------------------------
# Direction 2: own product
# --------------------------------------------------------------------------


@dataclass
class OrderMoney:
    revenue: float
    cogs: float
    operator: float
    tax: float
    delivery: float

    @property
    def net(self) -> float:
        return self.revenue - self.cogs - self.operator - self.tax - self.delivery


def order_money(order) -> OrderMoney:
    """Money for one closed order. Only a delivered (bought-out) order
    brings revenue and costs product/operator/tax; a returned one costs
    just whatever delivery was recorded for it (0 while returns are free)."""
    rate = order["rate"]
    delivery = (order["delivery_uzs"] or 0) / rate
    if order["status"] != "delivered":
        return OrderMoney(0, 0, 0, 0, delivery)
    revenue = order["amount_uzs"] / rate
    return OrderMoney(
        revenue=revenue,
        cogs=order["qty"] * order["unit_cost_usd"],
        operator=revenue * (order["operator_pct"] or 0) / 100,
        tax=revenue * (order["tax_pct"] or 0) / 100,
        delivery=delivery,
    )


def product_web_cost(repo: Repo, stat) -> float | None:
    rate = repo.product_rate_on(stat["web_id"], stat["product_id"], stat["date"])
    if rate is None:
        return None
    pay_type, rate_usd = rate
    return (stat["leads"] if pay_type == "lead" else stat["approves"]) * rate_usd


@dataclass
class ProductReport:
    revenue: float = 0.0
    cogs: float = 0.0
    operator: float = 0.0
    tax: float = 0.0
    delivery: float = 0.0
    web_cost: float = 0.0
    expenses: float = 0.0
    leads: int = 0
    approves: int = 0
    shipped: int = 0
    delivered: int = 0
    returned: int = 0
    units_sold: int = 0
    stats_missing_rate: int = 0
    by_web: dict[int, dict] = field(default_factory=dict)

    @property
    def profit(self) -> float:
        return (
            self.revenue - self.cogs - self.operator - self.tax - self.delivery
            - self.web_cost - self.expenses
        )

    @property
    def approve_pct(self) -> float | None:
        return pct(self.approves, self.leads)

    @property
    def buyout_pct(self) -> float | None:
        return pct(self.delivered, self.delivered + self.returned)

    @property
    def avg_check(self) -> float | None:
        return self.revenue / self.delivered if self.delivered else None

    @property
    def net_per_delivered(self) -> float | None:
        """What one bought-out order leaves before paying for traffic."""
        if not self.delivered:
            return None
        return (self.revenue - self.cogs - self.operator - self.tax - self.delivery) / self.delivered

    @property
    def max_cpa(self) -> float | None:
        """Most you can pay per approve and still be at zero."""
        if self.net_per_delivered is None or self.buyout_pct is None:
            return None
        return self.net_per_delivered * self.buyout_pct / 100

    @property
    def max_cpl(self) -> float | None:
        """Most you can pay per lead and still be at zero."""
        if self.max_cpa is None or self.approve_pct is None:
            return None
        return self.max_cpa * self.approve_pct / 100


def product_report(repo: Repo, start: str | None, end: str | None) -> ProductReport:
    rep = ProductReport()
    for o in repo.orders(limit=1_000_000):
        if _in(o["ship_date"], start, end):
            rep.shipped += 1
        if o["status"] == "shipped" or not _in(o["status_date"], start, end):
            continue
        m = order_money(o)
        rep.revenue += m.revenue
        rep.cogs += m.cogs
        rep.operator += m.operator
        rep.tax += m.tax
        rep.delivery += m.delivery
        if o["status"] == "delivered":
            rep.delivered += 1
            rep.units_sold += o["qty"]
        else:
            rep.returned += 1

    for s in repo.product_stats(start, end):
        rep.leads += s["leads"]
        rep.approves += s["approves"]
        cost = product_web_cost(repo, s)
        w = rep.by_web.setdefault(s["web_id"], {"name": s["web_name"], "leads": 0, "approves": 0, "cost": 0.0})
        w["leads"] += s["leads"]
        w["approves"] += s["approves"]
        if cost is None:
            rep.stats_missing_rate += 1
        else:
            rep.web_cost += cost
            w["cost"] += cost

    for c in repo.accruals(start, end):
        if c["direction"] == "product" and c["party_type"] == "web":
            rep.web_cost += c["amount_usd"]
            w = rep.by_web.setdefault(c["party_id"], {"name": c["party_name"], "leads": 0, "approves": 0, "cost": 0.0})
            w["cost"] += c["amount_usd"]
    rep.expenses = sum(e["amount_usd"] for e in repo.expenses(start, end) if e["direction"] == "product")
    return rep


@dataclass
class StockInfo:
    product_id: int
    name: str
    on_hand: int
    in_transit: int
    avg_daily_out: float
    days_left: float | None


def stock_info(repo: Repo, today: date, window_days: int = 14) -> list[StockInfo]:
    since = (today - timedelta(days=window_days - 1)).isoformat()
    result = []
    for p in repo.products():
        rows = repo.db.query("SELECT * FROM stock_moves WHERE product_id = ?", (p["id"],))
        on_hand = sum(r["qty"] for r in rows)
        shipped_recent = -sum(r["qty"] for r in rows if r["kind"] == "ship" and r["date"] >= since)
        returned_recent = sum(r["qty"] for r in rows if r["kind"] == "return" and r["date"] >= since)
        avg = max(shipped_recent - returned_recent, 0) / window_days
        in_transit = repo.db.one(
            "SELECT COALESCE(SUM(qty), 0) AS q FROM orders WHERE product_id = ? AND status = 'shipped'",
            (p["id"],),
        )["q"]
        result.append(
            StockInfo(
                product_id=p["id"],
                name=p["name"],
                on_hand=on_hand,
                in_transit=in_transit,
                avg_daily_out=round(avg, 1),
                days_left=round(on_hand / avg, 1) if avg > 0 else None,
            )
        )
    return result


# --------------------------------------------------------------------------
# Balances (all-time, independent of the selected period)
# --------------------------------------------------------------------------


@dataclass
class Balance:
    party_id: int | None
    name: str
    paid: float        # money that moved (to/from them)
    accrued: float     # what was earned/owed by the work done
    avg_daily: float = 0.0
    last_stat_date: str | None = None

    @property
    def balance(self) -> float:
        return self.paid - self.accrued

    @property
    def days_left(self) -> float | None:
        if self.balance <= 0 or self.avg_daily <= 0:
            return None
        return round(self.balance / self.avg_daily, 1)


def advertiser_balances(repo: Repo, today: date) -> list[Balance]:
    """balance > 0: prepayment not yet worked off (it's the rekl's money).
    balance < 0: the rekl owes you."""
    links = {l["id"]: l for l in repo.links()}
    week_ago = (today - timedelta(days=7)).isoformat()
    accrued: dict[int, float] = {}
    recent: dict[int, float] = {}
    last: dict[int, str] = {}
    for stat in repo.traffic_stats():
        link = links.get(stat["link_id"])
        if not link:
            continue
        r = traffic_row(repo, stat, today)
        a = link["advertiser_id"]
        accrued[a] = accrued.get(a, 0) + r.adv_amount
        if stat["date"] >= week_ago:
            recent[a] = recent.get(a, 0) + r.adv_amount
        last[a] = max(last.get(a, ""), stat["date"])
    paid: dict[int, float] = {}
    for p in repo.payments():
        if p["party_type"] == "advertiser":
            paid[p["party_id"]] = paid.get(p["party_id"], 0) + p["amount_usd"]
    for c in repo.accruals():
        if c["party_type"] == "advertiser":
            a = c["party_id"]
            accrued[a] = accrued.get(a, 0) + c["amount_usd"]
            last[a] = max(last.get(a, ""), c["date"])
    return [
        Balance(a["id"], a["name"], paid.get(a["id"], 0), accrued.get(a["id"], 0),
                recent.get(a["id"], 0) / 7, last.get(a["id"]))
        for a in repo.advertisers()
    ]


def web_balances(repo: Repo, today: date, direction: str) -> list[Balance]:
    """balance > 0: you prepaid, the web still owes traffic.
    balance < 0: you owe the web."""
    week_ago = (today - timedelta(days=7)).isoformat()
    accrued: dict[int, float] = {}
    recent: dict[int, float] = {}
    last: dict[int, str] = {}
    if direction == "traffic":
        links = {l["id"]: l for l in repo.links()}
        for stat in repo.traffic_stats():
            link = links.get(stat["link_id"])
            if not link:
                continue
            w = link["web_id"]
            amount = traffic_row(repo, stat, today).web_amount
            accrued[w] = accrued.get(w, 0) + amount
            if stat["date"] >= week_ago:
                recent[w] = recent.get(w, 0) + amount
            last[w] = max(last.get(w, ""), stat["date"])
    else:
        for stat in repo.product_stats():
            w = stat["web_id"]
            amount = product_web_cost(repo, stat) or 0
            accrued[w] = accrued.get(w, 0) + amount
            if stat["date"] >= week_ago:
                recent[w] = recent.get(w, 0) + amount
            last[w] = max(last.get(w, ""), stat["date"])
    paid: dict[int, float] = {}
    for p in repo.payments():
        if p["party_type"] == "web" and p["direction"] == direction:
            paid[p["party_id"]] = paid.get(p["party_id"], 0) + p["amount_usd"]
    for c in repo.accruals():
        if c["party_type"] == "web" and c["direction"] == direction:
            w = c["party_id"]
            accrued[w] = accrued.get(w, 0) + c["amount_usd"]
            last[w] = max(last.get(w, ""), c["date"])
    return [
        Balance(w["id"], w["name"], paid.get(w["id"], 0), accrued.get(w["id"], 0),
                recent.get(w["id"], 0) / 7, last.get(w["id"]))
        for w in repo.webs()
        if w["id"] in accrued or w["id"] in paid
    ]


@dataclass
class CourierBalance:
    delivered_uzs: float
    received_uzs: float

    @property
    def owed_uzs(self) -> float:
        """Cash the delivery service collected but hasn't sent you yet."""
        return self.delivered_uzs - self.received_uzs


def courier_balance(repo: Repo) -> CourierBalance:
    delivered = repo.db.one(
        "SELECT COALESCE(SUM(amount_uzs), 0) AS s FROM orders WHERE status = 'delivered'"
    )["s"]
    received = 0.0
    for p in repo.payments():
        if p["party_type"] == "courier":
            received += p["amount_uzs"] if p["amount_uzs"] is not None else p["amount_usd"] * (p["rate"] or repo.usd_uzs_rate())
    return CourierBalance(delivered, received)


# --------------------------------------------------------------------------
# Cash view and overall summary
# --------------------------------------------------------------------------


@dataclass
class Cash:
    inflow: float
    outflow: float
    owner: float = 0.0  # your own money put in (+) / taken out (-), kept apart

    @property
    def net(self) -> float:
        return self.inflow - self.outflow


def cash_flow(repo: Repo, start: str | None, end: str | None, direction: str | None = None) -> Cash:
    inflow = outflow = owner = 0.0
    for p in repo.payments(start, end):
        if p["party_type"] == "owner":
            if not direction:
                owner += p["amount_usd"]
            continue
        if direction and p["direction"] != direction:
            continue
        if p["party_type"] == "web":
            outflow += p["amount_usd"]
        else:
            inflow += p["amount_usd"]
    for e in repo.expenses(start, end):
        if direction and e["direction"] != direction:
            continue
        outflow += e["amount_usd"]
    if direction in (None, "product"):
        for m in repo.db.query("SELECT * FROM stock_moves WHERE kind = 'purchase'"):
            if _in(m["date"], start, end):
                outflow += m["cost_usd"]
    return Cash(inflow, outflow, owner)


@dataclass
class Summary:
    traffic: TrafficReport
    product: ProductReport
    general_expenses: float
    cash: Cash

    @property
    def total_profit(self) -> float:
        return self.traffic.net_profit + self.product.profit - self.general_expenses


def summary(repo: Repo, start: str | None, end: str | None, today: date) -> Summary:
    general = sum(e["amount_usd"] for e in repo.expenses(start, end) if e["direction"] == "general")
    return Summary(
        traffic=traffic_report(repo, start, end, today),
        product=product_report(repo, start, end),
        general_expenses=general,
        cash=cash_flow(repo, start, end),
    )


# --------------------------------------------------------------------------
# Warnings
# --------------------------------------------------------------------------


@dataclass
class Alert:
    level: str  # 'danger' | 'warn' | 'info'
    text: str


def alerts(repo: Repo, today: date) -> list[Alert]:
    out: list[Alert] = []
    s = repo.settings()

    for b in advertiser_balances(repo, today):
        if b.balance < -0.005:
            out.append(Alert("danger", f"Рекл {b.name} должен вам ${-b.balance:,.2f}: предоплата закончилась."))
        elif b.days_left is not None and b.days_left <= s["advertiser_low_days"]:
            out.append(Alert("warn", f"У рекла {b.name} предоплаты осталось ${b.balance:,.2f} (~{b.days_left} дн.), пора просить пополнение."))

    for direction, label in (("traffic", "трафик"), ("product", "товар")):
        for b in web_balances(repo, today, direction):
            if b.balance < -0.005:
                out.append(Alert("info", f"Вы должны вебу {b.name} ${-b.balance:,.2f} ({label})."))
            elif b.balance > 0.005:
                stale = b.last_stat_date is None or b.last_stat_date < (today - timedelta(days=3)).isoformat()
                if stale:
                    out.append(Alert("warn", f"Веб {b.name}: висит ваша предоплата ${b.balance:,.2f} ({label}), а трафика нет больше 3 дней."))

    # Risky links: you pay per lead, the rekl pays per approve.
    week_start = (today - timedelta(days=7)).isoformat()
    final_end = (today - timedelta(days=GUARANTEE_LAG_DAYS)).isoformat()
    rep = traffic_report(repo, week_start, final_end, today)
    for link in repo.links():
        agg = rep.by_link.get(link["id"])
        if not agg or not agg.leads:
            continue
        name = f"{link['web_name']} → {link['advertiser_name']}" + (f" ({link['offer']})" if link["offer"] else "")
        rate = repo.rate_on(link["id"], final_end)
        if rate and rate.web_pay_type == "lead" and rate.adv_pay_type == "approve" and rate.adv_rate:
            breakeven = rate.web_rate / rate.adv_rate * 100
            actual = agg.approve_pct or 0
            if actual < breakeven:
                covered = rate.guarantee_pct >= breakeven
                out.append(Alert(
                    "warn" if covered else "danger",
                    f"Связка {name}: апрув {actual}% ниже точки безубыточности {breakeven:.1f}%"
                    + (" — держитесь только за счёт гаранта." if covered else " — вы теряете деньги."),
                ))
                continue
        if agg.only_by_guarantee:
            out.append(Alert("warn", f"Связка {name}: в плюсе только за счёт гаранта (без него ${agg.profit_without_guarantee:,.2f})."))
        elif agg.profit < 0:
            out.append(Alert("danger", f"Связка {name}: минус ${-agg.profit:,.2f} за неделю."))

    for st in stock_info(repo, today):
        if st.on_hand <= 0:
            out.append(Alert("danger", f"Товар {st.name}: на складе {st.on_hand} шт."))
        elif st.days_left is not None and st.days_left <= s["restock_days"]:
            out.append(Alert("warn", f"Товар {st.name}: осталось {st.on_hand} шт., хватит примерно на {st.days_left} дн. Пора докупать."))

    prod = product_report(repo, (today - timedelta(days=29)).isoformat(), today.isoformat())
    if prod.max_cpl is not None and prod.leads:
        actual_cpl = prod.web_cost / prod.leads
        if actual_cpl > prod.max_cpl:
            out.append(Alert("danger", f"Товар: лид обходится ${actual_cpl:,.2f}, а максимум при текущих апруве и выкупе ${prod.max_cpl:,.2f}."))
    if prod.stats_missing_rate:
        out.append(Alert("info", f"Есть {prod.stats_missing_rate} строк статистики по товару без ставки веба — они не посчитаны в расходах."))
    if any(r.missing_rate for r in rep.rows):
        out.append(Alert("info", "Есть статистика трафика по связкам без ставок — она не посчитана."))
    return out
