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


def closed_through(repo: Repo, today: date) -> str:
    """Last day whose guarantee is applied: the day the owner says the
    network has closed, or, if not set, the day before yesterday."""
    manual = repo.meta("closed_through")
    if manual:
        try:
            if date.fromisoformat(manual) <= today:
                return manual
        except ValueError:
            pass
    return (today - timedelta(days=GUARANTEE_LAG_DAYS)).isoformat()


def closed_is_manual(repo: Repo) -> bool:
    return bool(repo.meta("closed_through"))


def traffic_row(repo: Repo, stat, today: date) -> TrafficRow:
    final = stat["date"] <= closed_through(repo, today)
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
    base = stat["valid"] if rate.guarantee_base == "valid" else stat["leads"]

    if rate.adv_pay_type == "valid":
        row.adv_amount = stat["valid"] * rate.adv_rate
    else:
        paid_approves = float(stat["approves"])
        if final and rate.guarantee_pct > 0:
            paid_approves = max(paid_approves, guaranteed_approves(base, rate.guarantee_pct))
        row.adv_amount = paid_approves * rate.adv_rate
        row.guarantee_bonus = (paid_approves - stat["approves"]) * rate.adv_rate

    if rate.web_pay_type == "lead":
        row.web_amount = stat["leads"] * rate.web_rate
    else:
        web_approves = float(stat["approves"])
        if final and rate.web_guarantee_pct > 0:
            web_approves = max(web_approves, guaranteed_approves(base, rate.web_guarantee_pct))
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
    paid: float        # money that moved (to/from them) — since the checkpoint, if any
    accrued: float     # what was earned/owed by the work done — since the checkpoint, if any
    avg_daily: float = 0.0
    last_stat_date: str | None = None
    opening: float = 0.0            # balance agreed at the checkpoint
    checkpoint: str | None = None   # date of the last reconciliation

    @property
    def balance(self) -> float:
        return self.opening + self.paid - self.accrued

    @property
    def days_left(self) -> float | None:
        if self.balance <= 0 or self.avg_daily <= 0:
            return None
        return round(self.balance / self.avg_daily, 1)


class _Ledger:
    """Collects paid / accrued per party, honouring reconciliation
    checkpoints: anything dated on or before a party's checkpoint is
    already inside the agreed opening balance and is skipped — except
    traffic days that were still preliminary when the balance was agreed:
    whatever they gain afterwards (the guarantee once the day closes, late
    approves) still moves the balance."""

    def __init__(self, checkpoints: dict[int, dict], week_ago: str):
        self.cp = checkpoints
        self.week_ago = week_ago
        self.paid: dict[int, float] = {}
        self.accrued: dict[int, float] = {}
        self.recent: dict[int, float] = {}
        self.last: dict[int, str] = {}
        self.pending_now: dict[int, dict[str, float]] = {}

    def _counts(self, party: int, day: str) -> bool:
        cp = self.cp.get(party)
        return cp is None or day > cp["date"]

    def pay(self, party: int, day: str, amount: float) -> None:
        if self._counts(party, day):
            self.paid[party] = self.paid.get(party, 0) + amount

    def accrue(self, party: int, day: str, amount: float) -> None:
        # Recent volume (for "days left") and last activity ignore checkpoints.
        if day >= self.week_ago:
            self.recent[party] = self.recent.get(party, 0) + amount
        self.last[party] = max(self.last.get(party, ""), day)
        if self._counts(party, day):
            self.accrued[party] = self.accrued.get(party, 0) + amount

    def accrue_stat(self, party: int, day: str, amount: float) -> None:
        """Accrual from a traffic stat row."""
        self.accrue(party, day, amount)
        cp = self.cp.get(party)
        if cp is None or day > cp["date"]:
            return
        if cp["pending"] and day in cp["pending"]:
            days = self.pending_now.setdefault(party, {})
            days[day] = days.get(day, 0) + amount

    def balance(self, party: int, name: str) -> Balance:
        cp = self.cp.get(party)
        if cp and cp["pending"]:
            now = self.pending_now.get(party, {})
            change = sum(now.get(day, 0) - before for day, before in cp["pending"].items())
            if abs(change) > 1e-9:
                self.accrued[party] = self.accrued.get(party, 0) + change
        return Balance(
            party, name, self.paid.get(party, 0), self.accrued.get(party, 0),
            self.recent.get(party, 0) / 7, self.last.get(party),
            opening=cp["balance"] if cp else 0.0, checkpoint=cp["date"] if cp else None,
        )

    def touched(self, party: int) -> bool:
        return party in self.paid or party in self.accrued or party in self.cp or party in self.last


def advertiser_balances(repo: Repo, today: date) -> list[Balance]:
    """balance > 0: prepayment not yet worked off (it's the rekl's money).
    balance < 0: the rekl owes you."""
    links = {l["id"]: l for l in repo.links()}
    _snapshot_old_checkpoints(repo, today, "advertiser")
    led = _Ledger(repo.checkpoints("advertiser", "traffic"), (today - timedelta(days=7)).isoformat())
    for stat in repo.traffic_stats():
        link = links.get(stat["link_id"])
        if link:
            led.accrue_stat(link["advertiser_id"], stat["date"], traffic_row(repo, stat, today).adv_amount)
    for p in repo.payments():
        if p["party_type"] == "advertiser":
            led.pay(p["party_id"], p["date"], p["amount_usd"])
    for c in repo.accruals():
        if c["party_type"] == "advertiser":
            led.accrue(c["party_id"], c["date"], c["amount_usd"])
    return [led.balance(a["id"], a["name"]) for a in repo.advertisers()]


def web_balances(repo: Repo, today: date, direction: str) -> list[Balance]:
    """balance > 0: you prepaid, the web still owes traffic.
    balance < 0: you owe the web."""
    if direction == "traffic":
        _snapshot_old_checkpoints(repo, today, "web")
    led = _Ledger(repo.checkpoints("web", direction), (today - timedelta(days=7)).isoformat())
    if direction == "traffic":
        links = {l["id"]: l for l in repo.links()}
        for stat in repo.traffic_stats():
            link = links.get(stat["link_id"])
            if link:
                led.accrue_stat(link["web_id"], stat["date"], traffic_row(repo, stat, today).web_amount)
    else:
        for stat in repo.product_stats():
            led.accrue(stat["web_id"], stat["date"], product_web_cost(repo, stat) or 0)
    for p in repo.payments():
        if p["party_type"] == "web" and p["direction"] == direction:
            led.pay(p["party_id"], p["date"], p["amount_usd"])
    for c in repo.accruals():
        if c["party_type"] == "web" and c["direction"] == direction:
            led.accrue(c["party_id"], c["date"], c["amount_usd"])
    return [led.balance(w["id"], w["name"]) for w in repo.webs() if led.touched(w["id"])]


def reconcile(repo: Repo, today: date, party_type: str, party_id: int, direction: str,
              target: float, on_date: str) -> None:
    """Fix a web's / rekl's balance at `target` (your own figure) as of
    `on_date`. From then on only payments and traffic dated after that day
    move the balance — reloading older days (late approves) no longer
    shifts an agreed figure. Days still preliminary today are remembered
    as they stand, so their guarantee and late approves are added later."""
    if party_type == "advertiser":
        direction = "traffic"
    pending = None
    if direction == "traffic":
        first_open = _days_before(closed_through(repo, today), -1)
        pending = _pending_snapshot(repo, today, party_type, party_id, first_open, on_date) if on_date >= first_open else {}
    repo.set_checkpoint(party_type, party_id, direction, on_date, target, pending)


def _pending_snapshot(repo: Repo, today: date, party_type: str, party_id: int,
                      first_day: str, last_day: str, without_guarantee: bool = False) -> dict[str, float]:
    """Accrued per day for one party over [first_day, last_day]."""
    pending: dict[str, float] = {}
    day = date.fromisoformat(first_day)
    while day.isoformat() <= last_day:
        pending[day.isoformat()] = 0.0
        day += timedelta(days=1)
    links = {l["id"]: l for l in repo.links()}
    side = "advertiser_id" if party_type == "advertiser" else "web_id"
    for stat in repo.traffic_stats(first_day, last_day):
        link = links.get(stat["link_id"])
        if link and link[side] == party_id:
            row = traffic_row(repo, stat, today)
            if party_type == "advertiser":
                amount = row.adv_amount - (row.guarantee_bonus if without_guarantee else 0)
            else:
                amount = row.web_amount - (row.web_guarantee_cost if without_guarantee else 0)
            pending[stat["date"]] += amount
    return pending


def _snapshot_old_checkpoints(repo: Repo, today: date, party_type: str) -> None:
    """Checkpoints made before snapshots were kept: remember the days that
    were preliminary back then as they would have stood (no guarantee yet),
    once, so their guarantee and later approves count without doubling."""
    for party_id, cp in repo.checkpoints(party_type, "traffic").items():
        if cp["pending"] is not None:
            continue
        first = _days_before(cp["created_at"][:10], GUARANTEE_LAG_DAYS - 1)
        pending = (_pending_snapshot(repo, today, party_type, party_id, first, cp["date"], without_guarantee=True)
                   if cp["date"] >= first else {})
        repo.set_checkpoint_pending(cp["id"], pending)


def _days_before(day: str, n: int) -> str:
    return (date.fromisoformat(day) - timedelta(days=n)).isoformat()


@dataclass
class StatementLine:
    date: str
    kind: str            # 'opening' | 'traffic' | 'pending' | 'payment' | 'accrual'
    text: str
    leads: int = 0
    approves: int = 0
    guarantee: float = 0.0   # part of `accrued` that comes from the guarantee
    accrued: float = 0.0
    paid: float = 0.0
    final: bool = True
    balance: float = 0.0     # running balance after this line


def statement(repo: Repo, today: date, party_type: str, party_id: int) -> list[StatementLine]:
    """Day-by-day traffic ledger of one rekl or web, from its checkpoint
    (or the beginning), ending at the same balance the balance tables show:
    what to compare line by line with the network's own statement."""
    side = "advertiser_id" if party_type == "advertiser" else "web_id"
    if party_type == "advertiser":
        _snapshot_old_checkpoints(repo, today, "advertiser")
    else:
        _snapshot_old_checkpoints(repo, today, "web")
    cp = repo.checkpoints(party_type, "traffic").get(party_id)
    cp_date = cp["date"] if cp else ""
    lines: list[StatementLine] = []
    if cp:
        lines.append(StatementLine(cp_date, "opening", "Сверка: баланс на эту дату"))

    links = {l["id"]: l for l in repo.links()}
    days: dict[str, StatementLine] = {}
    pending_now: dict[str, float] = {}
    pending_guarantee: dict[str, float] = {}
    for stat in repo.traffic_stats():
        link = links.get(stat["link_id"])
        if not link or link[side] != party_id:
            continue
        row = traffic_row(repo, stat, today)
        amount = row.adv_amount if party_type == "advertiser" else row.web_amount
        guarantee = row.guarantee_bonus if party_type == "advertiser" else row.web_guarantee_cost
        day = stat["date"]
        if cp and day <= cp_date:
            if cp["pending"] and day in cp["pending"]:
                pending_now[day] = pending_now.get(day, 0) + amount
                pending_guarantee[day] = pending_guarantee.get(day, 0) + guarantee
            continue
        line = days.setdefault(day, StatementLine(day, "traffic", "Трафик"))
        line.leads += stat["leads"]
        line.approves += stat["approves"]
        line.guarantee += guarantee
        line.accrued += amount
        line.final = line.final and row.final
    lines += days.values()
    if cp and cp["pending"]:
        for day, before in cp["pending"].items():
            change = pending_now.get(day, 0) - before
            if abs(change) > 1e-9:
                lines.append(StatementLine(
                    day, "pending", "Доначислено после сверки (гарант / поздние апрувы)",
                    guarantee=pending_guarantee.get(day, 0), accrued=change,
                    final=day <= closed_through(repo, today)))
    for p in repo.payments():
        if p["party_type"] == party_type and p["party_id"] == party_id and p["direction"] == "traffic" \
                and not (cp and p["date"] <= cp_date):
            lines.append(StatementLine(p["date"], "payment", "Оплата" + (f" — {p['note']}" if p["note"] else ""),
                                       paid=p["amount_usd"]))
    for c in repo.accruals():
        if c["party_type"] == party_type and c["party_id"] == party_id and c["direction"] == "traffic" \
                and not (cp and c["date"] <= cp_date):
            lines.append(StatementLine(c["date"], "accrual", "Начисление" + (f" — {c['note']}" if c["note"] else ""),
                                       accrued=c["amount_usd"]))

    order = {"opening": 0, "pending": 1, "traffic": 2, "accrual": 3, "payment": 4}
    lines.sort(key=lambda l: (max(l.date, cp_date), order[l.kind], l.date))
    running = cp["balance"] if cp else 0.0
    for l in lines:
        if l.kind != "opening":
            running += l.paid - l.accrued
        l.balance = running
    return lines


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
    cash_by_direction: dict[str, Cash] = field(default_factory=dict)

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
        cash_by_direction=cash_by_direction(repo, start, end),
    )


@dataclass
class ExpenseLine:
    direction: str
    category: str
    by_month: dict[str, float]
    total: float = 0.0
    count: int = 0


def expense_breakdown(repo: Repo, start: str | None, end: str | None) -> tuple[list[str], list[ExpenseLine]]:
    """Expenses grouped by direction + category, split by month (YYYY-MM),
    biggest first: where the money goes."""
    months: set[str] = set()
    lines: dict[tuple[str, str], ExpenseLine] = {}
    for e in repo.expenses(start, end):
        month = e["date"][:7]
        months.add(month)
        key = (e["direction"], e["category"].strip().casefold())
        line = lines.setdefault(key, ExpenseLine(e["direction"], e["category"].strip(), {}))
        line.by_month[month] = line.by_month.get(month, 0) + e["amount_usd"]
        line.total += e["amount_usd"]
        line.count += 1
    return sorted(months), sorted(lines.values(), key=lambda l: -l.total)


CASH_DIRECTIONS = [("traffic", "Трафик"), ("product", "Мой товар"), ("general", "Общие расходы")]


def cash_by_direction(repo: Repo, start: str | None, end: str | None) -> dict[str, Cash]:
    """Cash split the same way as profit: traffic (rekl prepayments in,
    web payouts and traffic expenses out), product (courier payouts in,
    webs / purchases / product expenses out) and general expenses.
    Own money put in or taken out stays apart in Cash.owner of the total."""
    return {d: cash_flow(repo, start, end, d) for d, _ in CASH_DIRECTIONS}


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

    closed = closed_through(repo, today)
    if closed_is_manual(repo) and closed < _days_before(today.isoformat(), 4):
        out.append(Alert("warn", f"Дата «сеть закрыла по» — {closed}, давно не обновлялась: гарант после неё не применяется. Обновите на странице «Трафик»."))

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
    final_end = closed_through(repo, today)
    rep = traffic_report(repo, week_start, final_end, today)
    for link in repo.links():
        agg = rep.by_link.get(link["id"])
        if not agg or not agg.leads:
            continue
        name = f"{link['web_name']} → {link['advertiser_name']}" + (f" ({link['offer']})" if link["offer"] else "")
        if repo.is_trash_name(link["advertiser_name"]):
            if agg.web_amount:
                out.append(Alert("warn", f"Треш {link['web_name']}: {agg.leads} лидов ушли никому, а вы заплатили за них "
                                         f"${agg.web_amount:,.2f} за неделю."))
            continue
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
        if rate and rate.web_pay_type == "lead" and rate.adv_pay_type == "valid" and rate.adv_rate:
            max_trash = 100 - rate.web_rate / rate.adv_rate * 100
            trash = round(100 - agg.valid / agg.leads * 100, 1)
            if trash > max_trash:
                out.append(Alert(
                    "danger",
                    f"Связка {name}: треш {trash}% выше допустимого {max_trash:.1f}% — вы теряете деньги "
                    f"(минус ${-agg.profit:,.2f} за неделю)." if agg.profit < 0 else
                    f"Связка {name}: треш {trash}% выше допустимого {max_trash:.1f}%.",
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


# --------------------------------------------------------------------------
# Wallets: where the money physically is, and how much of it is yours
# --------------------------------------------------------------------------


@dataclass
class WalletState:
    id: int
    name: str
    currency: str
    opening: float
    since: str
    inflow: float = 0.0
    outflow: float = 0.0
    usd_rate: float = 1.0   # wallet currency units per $1

    @property
    def balance(self) -> float:
        return self.opening + self.inflow - self.outflow

    @property
    def balance_usd(self) -> float:
        return self.balance / self.usd_rate


def _in_wallet_currency(currency: str, amount_usd: float, amount_uzs: float | None, rate: float | None,
                        current_rate: float) -> float:
    if currency == "usd":
        return amount_usd
    if amount_uzs is not None:
        return amount_uzs
    return amount_usd * (rate or current_rate)


def wallet_states(repo: Repo) -> list[WalletState]:
    """Each wallet's balance in its own currency: the last reconciled amount
    plus every payment / expense / purchase / transfer tied to it and
    dated after that reconciliation. Money in: rekl and courier payments,
    own money put in. Money out: webs, expenses, stock purchases."""
    current_rate = repo.usd_uzs_rate()
    states = {
        w["id"]: WalletState(w["id"], w["name"], w["currency"], w["balance"], w["balance_date"],
                             usd_rate=1.0 if w["currency"] == "usd" else current_rate)
        for w in repo.wallets()
    }

    def book(wallet_id, day, amount, outgoing):
        st = states.get(wallet_id)
        if st is None or day <= st.since:
            return
        if amount < 0:  # refunds go the other way
            amount, outgoing = -amount, not outgoing
        if outgoing:
            st.outflow += amount
        else:
            st.inflow += amount

    for p in repo.payments():
        st = states.get(p["wallet_id"])
        if st:
            amt = _in_wallet_currency(st.currency, p["amount_usd"], p["amount_uzs"], p["rate"], current_rate)
            book(p["wallet_id"], p["date"], amt, outgoing=p["party_type"] == "web")
    for e in repo.expenses():
        st = states.get(e["wallet_id"])
        if st:
            book(e["wallet_id"], e["date"],
                 _in_wallet_currency(st.currency, e["amount_usd"], e["amount_uzs"], e["rate"], current_rate), True)
    for m in repo.db.query("SELECT * FROM stock_moves WHERE kind = 'purchase' AND wallet_id IS NOT NULL"):
        st = states.get(m["wallet_id"])
        if st:
            book(m["wallet_id"], m["date"], _in_wallet_currency(st.currency, m["cost_usd"], None, None, current_rate), True)
    for t in repo.transfers():
        book(t["from_wallet"], t["date"], t["amount_out"], True)
        book(t["to_wallet"], t["date"], t["amount_in"], False)
    return list(states.values())


@dataclass
class Position:
    """How much of the money on hand is actually yours."""

    wallets_usd: float          # money on all wallets, in $
    rekl_prepaid: float         # rekls' money you hold (their positive balances)
    rekl_owe_you: float         # rekls who owe you
    webs_owe_you: float         # webs you prepaid (they owe traffic)
    you_owe_webs: float         # webs you owe
    courier_owes_usd: float     # cash on delivery collected but not sent yet
    stock_usd: float = 0.0      # goods on the shelf and in transit, at cost

    @property
    def own(self) -> float:
        return (self.wallets_usd - self.rekl_prepaid + self.rekl_owe_you
                + self.webs_owe_you - self.you_owe_webs + self.courier_owes_usd + self.stock_usd)


def position(repo: Repo, today: date) -> Position:
    rekls = [b.balance for b in advertiser_balances(repo, today)]
    webs = [b.balance for d in ("traffic", "product") for b in web_balances(repo, today, d)]
    return Position(
        wallets_usd=sum(w.balance_usd for w in wallet_states(repo)),
        rekl_prepaid=sum(b for b in rekls if b > 0),
        rekl_owe_you=-sum(b for b in rekls if b < 0),
        webs_owe_you=sum(b for b in webs if b > 0),
        you_owe_webs=-sum(b for b in webs if b < 0),
        courier_owes_usd=max(courier_balance(repo).owed_uzs, 0) / repo.usd_uzs_rate(),
        stock_usd=stock_value(repo, today),
    )


def stock_value(repo: Repo, today: date) -> float:
    """Goods on the shelf plus parcels still on the way, at unit cost."""
    cost = {p["id"]: p["unit_cost_usd"] for p in repo.products()}
    return sum(max(s.on_hand, 0) * cost.get(s.product_id, 0) + s.in_transit * cost.get(s.product_id, 0)
               for s in stock_info(repo, today))


# --------------------------------------------------------------------------
# Month forecast
# --------------------------------------------------------------------------


@dataclass
class Forecast:
    month: str                 # YYYY-MM
    days_in_month: int
    closed_days: int           # days of this month already closed by the network
    margin_so_far: float       # traffic margin (rekls − webs) on closed days
    avg_daily: float           # average daily margin over the last 7 closed days
    margin_forecast: float
    product_so_far: float      # own-product profit so far (before expenses)
    expenses_so_far: float     # all expenses this month
    planned_fixed: float       # owner's plan of fixed monthly costs
    expenses_forecast: float
    traffic_expenses: float = 0.0   # traffic-direction expenses this month (commissions etc.)

    @property
    def traffic_forecast(self) -> float:
        """Traffic alone: margin to month end minus traffic expenses, no overheads."""
        return self.margin_forecast - self.traffic_expenses

    @property
    def profit_forecast(self) -> float:
        return self.margin_forecast + self.product_so_far - self.expenses_forecast

    @property
    def remaining_days(self) -> int:
        return self.days_in_month - self.closed_days


def month_forecast(repo: Repo, today: date) -> Forecast:
    """Closed days of this month as they are, the rest at the average daily
    margin of the last 7 closed days; expenses as spent so far, but not
    less than the planned fixed costs."""
    import calendar

    first = today.replace(day=1)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    last = first.replace(day=days_in_month)
    closed = date.fromisoformat(closed_through(repo, today))
    closed_in_month = min(closed, last)
    closed_days = (closed_in_month - first).days + 1 if closed_in_month >= first else 0

    margin_so_far = 0.0
    if closed_days:
        margin_so_far = traffic_report(repo, first.isoformat(), closed_in_month.isoformat(), today).total.profit
    week = traffic_report(repo, (closed - timedelta(days=6)).isoformat(), closed.isoformat(), today)
    avg_daily = week.total.profit / 7

    month_end = last.isoformat()
    expenses = sum(e["amount_usd"] for e in repo.expenses(first.isoformat(), month_end))
    product = product_report(repo, first.isoformat(), month_end)
    product_so_far = product.profit + product.expenses   # expenses are counted once, below
    planned = repo.settings().get("monthly_fixed", 0.0)
    margin_forecast = margin_so_far + avg_daily * (days_in_month - closed_days)
    return Forecast(
        month=first.isoformat()[:7], days_in_month=days_in_month, closed_days=closed_days,
        margin_so_far=margin_so_far, avg_daily=avg_daily, margin_forecast=margin_forecast,
        product_so_far=product_so_far, expenses_so_far=expenses, planned_fixed=planned,
        expenses_forecast=max(expenses, planned),
        traffic_expenses=sum(e["amount_usd"] for e in repo.expenses(first.isoformat(), month_end)
                             if e["direction"] == "traffic"),
    )
