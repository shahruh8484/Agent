import pytest

from finance import calc
from tests.conftest import TODAY

# TODAY = 2026-10-10 -> guarantee applies to 2026-10-08 and earlier.


def make_link(repo, adv_pay="approve", adv_rate=10.0, guarantee=30.0, web_pay="lead", web_rate=2.0, valid_from="2026-10-01"):
    web = repo.add_web("Sanya")
    adv = repo.add_advertiser("Ahmed")
    return repo.add_link(web, adv, "Glyco", valid_from, adv_pay, adv_rate, guarantee, web_pay, web_rate)


def test_guarantee_applies_when_approve_below_it(repo):
    link = make_link(repo)
    # 100 leads, 20 approves (20%) < guarantee 30% -> rekl pays for 30.
    repo.upsert_traffic_stat(link, "2026-10-08", 100, 100, 20)
    rep = calc.traffic_report(repo, None, None, TODAY)
    assert rep.total.adv_amount == pytest.approx(300)
    assert rep.total.guarantee_bonus == pytest.approx(100)
    assert rep.total.web_amount == pytest.approx(200)  # 100 leads * $2
    assert rep.total.profit == pytest.approx(100)
    # without the guarantee it would be exactly 0 — not a loss, so not flagged
    assert not rep.total.only_by_guarantee


def test_only_by_guarantee_flag(repo):
    link = make_link(repo, web_rate=2.5)
    repo.upsert_traffic_stat(link, "2026-10-08", 100, 100, 20)
    agg = calc.traffic_report(repo, None, None, TODAY).total
    # adv 300 (by guarantee), web 250 -> +50; without guarantee 200-250 = -50
    assert agg.profit == pytest.approx(50)
    assert agg.only_by_guarantee


def test_guarantee_not_applied_to_recent_days(repo):
    link = make_link(repo)
    repo.upsert_traffic_stat(link, "2026-10-09", 100, 100, 20)  # yesterday: preliminary
    rep = calc.traffic_report(repo, None, None, TODAY)
    assert rep.total.adv_amount == pytest.approx(200)
    assert rep.total.guarantee_bonus == 0
    assert rep.total.preliminary


def test_approve_above_guarantee_pays_real_approves(repo):
    link = make_link(repo)
    repo.upsert_traffic_stat(link, "2026-10-05", 100, 100, 45)
    assert calc.traffic_report(repo, None, None, TODAY).total.adv_amount == pytest.approx(450)


def test_valid_lead_and_approve_pay_types(repo):
    link = make_link(repo, adv_pay="valid", adv_rate=3.0, guarantee=0, web_pay="approve", web_rate=5.0)
    repo.upsert_traffic_stat(link, "2026-10-05", 100, 80, 25)
    t = calc.traffic_report(repo, None, None, TODAY).total
    assert t.adv_amount == pytest.approx(240)  # 80 valid * $3
    assert t.web_amount == pytest.approx(125)  # 25 approves * $5


def test_rate_change_keeps_old_days_on_old_rate(repo):
    link = make_link(repo, guarantee=0)
    repo.set_link_rate(link, "2026-10-06", "approve", 12.0, 0, "lead", 2.0)
    repo.upsert_traffic_stat(link, "2026-10-05", 10, 10, 5)
    repo.upsert_traffic_stat(link, "2026-10-06", 10, 10, 5)
    rows = {r.date: r for r in calc.traffic_report(repo, None, None, TODAY).rows}
    assert rows["2026-10-05"].adv_amount == pytest.approx(50)
    assert rows["2026-10-06"].adv_amount == pytest.approx(60)


def test_advertiser_and_web_balances(repo):
    link = make_link(repo, guarantee=0)
    ahmed = repo.find_advertiser("Ahmed")["id"]
    sanya = repo.find_web("Sanya")["id"]
    repo.add_payment("2026-10-01", "traffic", "advertiser", ahmed, 1000)
    repo.add_payment("2026-10-02", "traffic", "web", sanya, 150)
    repo.upsert_traffic_stat(link, "2026-10-05", 100, 100, 30)  # adv 300, web 200
    adv = calc.advertiser_balances(repo, TODAY)[0]
    assert adv.balance == pytest.approx(700)  # prepayment left
    web = calc.web_balances(repo, TODAY, "traffic")[0]
    assert web.balance == pytest.approx(-50)  # you owe the web $50
    # the web has no product-direction activity -> separate, empty balance
    assert calc.web_balances(repo, TODAY, "product") == []


def setup_product(repo):
    product = repo.add_product("Glycofort", 2.2, initial_stock=1070, date="2026-10-01")
    web = repo.add_web("Sanya")
    return product, web


def test_order_lifecycle_and_unit_economics(repo):
    product, web = setup_product(repo)
    # 990 000 sum at 11 500 = $86.09; 5 units * 2.2 = $11; operator 15%
    o1 = repo.add_order("2026-10-02", product, 5, 990_000, delivery_uzs=23_000, web_id=web)
    o2 = repo.add_order("2026-10-02", product, 5, 990_000, web_id=web)
    o3 = repo.add_order("2026-10-02", product, 5, 990_000, web_id=web)
    repo.set_order_status(o1, "delivered", "2026-10-05")
    repo.set_order_status(o2, "returned", "2026-10-05")

    rep = calc.product_report(repo, None, None)
    revenue = 990_000 / 11_500
    assert rep.revenue == pytest.approx(revenue)
    assert rep.cogs == pytest.approx(11)
    assert rep.operator == pytest.approx(revenue * 0.15)
    assert rep.delivery == pytest.approx(23_000 / 11_500)
    assert rep.tax == 0
    assert rep.delivered == 1 and rep.returned == 1 and rep.shipped == 3
    assert rep.buyout_pct == 50.0

    # stock: 1070 - 15 shipped + 5 returned
    st = calc.stock_info(repo, TODAY)[0]
    assert st.on_hand == 1060
    assert st.in_transit == 5  # o3

    # flipping the return back to delivered takes the units off the shelf again
    repo.set_order_status(o2, "delivered", "2026-10-06")
    assert calc.stock_info(repo, TODAY)[0].on_hand == 1055
    assert o3


def test_operator_pct_frozen_at_delivery(repo):
    product, _ = setup_product(repo)
    o = repo.add_order("2026-10-02", product, 1, 115_000)
    repo.set_order_status(o, "delivered", "2026-10-03")
    repo.set_setting("operator_pct", 20)
    rep = calc.product_report(repo, None, None)
    assert rep.operator == pytest.approx(10 * 0.15)  # still 15% of $10


def test_rate_change_does_not_rewrite_old_orders(repo):
    product, _ = setup_product(repo)
    o = repo.add_order("2026-10-02", product, 1, 115_000)
    repo.set_setting("usd_uzs_rate", 12_000)
    repo.set_order_status(o, "delivered", "2026-10-03")
    assert calc.product_report(repo, None, None).revenue == pytest.approx(10)


def test_product_web_cost_and_max_cpl(repo):
    product, web = setup_product(repo)
    repo.set_product_web_rate(web, product, "2026-10-01", "lead", 1.0)
    repo.upsert_product_stat(web, product, "2026-10-02", 100, 40)
    for _ in range(4):
        o = repo.add_order("2026-10-02", product, 1, 115_000)  # $10
        repo.set_order_status(o, "delivered", "2026-10-04")
    o = repo.add_order("2026-10-02", product, 1, 115_000)
    repo.set_order_status(o, "returned", "2026-10-04")

    rep = calc.product_report(repo, None, None)
    assert rep.web_cost == pytest.approx(100)
    # per delivered: 10 - 2.2 - 1.5 operator = 6.3; buyout 80%; approve 40%
    assert rep.net_per_delivered == pytest.approx(6.3)
    assert rep.max_cpa == pytest.approx(6.3 * 0.8)
    assert rep.max_cpl == pytest.approx(6.3 * 0.8 * 0.4)


def test_courier_balance(repo):
    product, _ = setup_product(repo)
    o = repo.add_order("2026-10-02", product, 5, 990_000)
    repo.set_order_status(o, "delivered", "2026-10-03")
    repo.add_payment("2026-10-06", "product", "courier", None, 500_000 / 11_500, 500_000, 11_500)
    assert calc.courier_balance(repo).owed_uzs == pytest.approx(490_000)


def test_cash_flow_counts_purchases_and_expenses(repo):
    product, web = setup_product(repo)
    repo.add_stock_move("2026-10-03", product, 500, "purchase", cost_usd=1100)
    repo.add_expense("2026-10-03", "general", "Сервер", 20)
    adv = repo.add_advertiser("Ahmed")
    repo.add_payment("2026-10-03", "traffic", "advertiser", adv, 2000)
    repo.add_payment("2026-10-03", "product", "web", web, 300)
    cash = calc.cash_flow(repo, "2026-10-01", "2026-10-10")
    assert cash.inflow == pytest.approx(2000)
    assert cash.outflow == pytest.approx(1420)


def test_summary_total(repo):
    link = make_link(repo, guarantee=0)
    repo.upsert_traffic_stat(link, "2026-10-05", 100, 100, 30)  # +100
    repo.add_expense("2026-10-05", "general", "Программист", 30)
    repo.add_expense("2026-10-05", "traffic", "Комиссия", 5)
    s = calc.summary(repo, None, None, TODAY)
    assert s.total_profit == pytest.approx(100 - 5 - 30)


def test_alerts(repo):
    link = make_link(repo, guarantee=0, web_rate=4.0)  # breakeven approve 40%
    ahmed = repo.find_advertiser("Ahmed")["id"]
    repo.add_payment("2026-10-01", "traffic", "advertiser", ahmed, 100)
    repo.upsert_traffic_stat(link, "2026-10-07", 100, 100, 20)  # adv 200 > paid 100
    product = repo.add_product("Glycofort", 2.2, initial_stock=10, date="2026-10-01")
    for _ in range(6):
        repo.add_order("2026-10-05", product, 1, 115_000)

    texts = [a.text for a in calc.alerts(repo, TODAY)]
    assert any("Ahmed должен вам" in t for t in texts)
    assert any("ниже точки безубыточности" in t for t in texts)
    assert any("Пора докупать" in t for t in texts)


def test_period_range():
    assert calc.period_range("today", TODAY) == ("2026-10-10", "2026-10-10")
    assert calc.period_range("7d", TODAY) == ("2026-10-04", "2026-10-10")
    assert calc.period_range("last_month", TODAY) == ("2026-09-01", "2026-09-30")
    assert calc.period_range("all", TODAY) == (None, None)


def test_guarantee_passed_on_to_web(repo):
    # Max -> Sanzh: Sanzh pays $25/approve with 10% guarantee, Max gets
    # $23/approve with the same 10% guarantee passed on.
    web = repo.add_web("Max")
    adv = repo.add_advertiser("Sanzh")
    link = repo.add_link(web, adv, "", "2026-07-01", "approve", 25, 10, "approve", 23, 10)
    repo.upsert_traffic_stat(link, "2026-10-01", 200, 200, 15)
    agg = calc.traffic_report(repo, None, None, TODAY).total
    assert agg.adv_amount == pytest.approx(500)       # 20 approves * $25
    assert agg.web_amount == pytest.approx(460)       # 20 approves * $23
    assert agg.web_guarantee_cost == pytest.approx(115)
    assert agg.profit == pytest.approx(40)
    assert agg.profit_without_guarantee == pytest.approx(15 * 2)
    assert not agg.only_by_guarantee

    # yesterday is still preliminary: no guarantee on either side yet
    repo.upsert_traffic_stat(link, "2026-10-09", 200, 200, 15)
    row = [r for r in calc.traffic_report(repo, "2026-10-09", "2026-10-09", TODAY).rows][0]
    assert (row.adv_amount, row.web_amount) == (375, 345)


def test_old_database_gets_web_guarantee_column(tmp_path):
    import sqlite3

    from finance.db import DB, Repo

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE link_rates (id INTEGER PRIMARY KEY, link_id INTEGER NOT NULL, valid_from TEXT NOT NULL,"
        " adv_pay_type TEXT NOT NULL, adv_rate REAL NOT NULL, guarantee_pct REAL NOT NULL DEFAULT 0,"
        " web_pay_type TEXT NOT NULL, web_rate REAL NOT NULL);"
        "INSERT INTO link_rates(link_id, valid_from, adv_pay_type, adv_rate, guarantee_pct, web_pay_type, web_rate)"
        " VALUES (1, '2026-07-01', 'approve', 25, 10, 'approve', 23);"
    )
    conn.commit()
    conn.close()
    repo = Repo(DB(path))
    assert repo.rate_on(1, "2026-10-01").web_guarantee_pct == 0


def test_manual_accrual_sets_balances_and_profit(repo):
    maks = repo.add_web("Max")
    sanzh = repo.add_advertiser("Sanzh")
    repo.add_payment("2026-08-01", "traffic", "web", maks, 13112)
    repo.add_accrual("2026-10-08", "traffic", "web", maks, 13112 + 2231, "итог до сегодня")
    web = calc.web_balances(repo, TODAY, "traffic")[0]
    assert web.balance == pytest.approx(-2231)  # you owe Max

    repo.add_payment("2026-08-01", "traffic", "advertiser", sanzh, 17000)
    repo.add_accrual("2026-10-08", "product", "advertiser", sanzh, 16000)  # forced to traffic
    adv = calc.advertiser_balances(repo, TODAY)[0]
    assert adv.balance == pytest.approx(1000)  # his prepayment left

    rep = calc.traffic_report(repo, None, None, TODAY)
    assert rep.total.profit == pytest.approx(16000 - 15343)
    assert rep.by_web[maks].web_amount == pytest.approx(15343)
    assert calc.traffic_report(repo, "2026-10-09", None, TODAY).total.profit == 0
