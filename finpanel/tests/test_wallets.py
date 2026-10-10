import pytest
from fastapi.testclient import TestClient

from finance import calc
from finance.app import create_app
from finance.assistant import apply_action, build_context
from finance.config import Settings
from finance.security import hash_password
from tests.conftest import TODAY


def test_wallet_balances_follow_tagged_money(repo):
    payeer = repo.add_wallet("Payeer", "usd", 1000, "2026-10-01")
    card = repo.add_wallet("Карта", "uzs", 1_150_000, "2026-10-01")
    sanzh = repo.add_advertiser("Санж")
    maks = repo.add_web("Макс")
    product = repo.add_product("Glycofort", 2.2, 0, "2026-10-01")

    repo.add_payment("2026-09-30", "traffic", "advertiser", sanzh, 999, wallet_id=payeer)   # before reconcile: ignored
    repo.add_payment("2026-10-02", "traffic", "advertiser", sanzh, 3000, wallet_id=payeer)
    repo.add_payment("2026-10-03", "traffic", "web", maks, 1500, wallet_id=payeer)
    repo.add_expense("2026-10-03", "traffic", "Комиссия", 5, wallet_id=payeer)
    repo.add_payment("2026-10-04", "traffic", "web", maks, -100, wallet_id=payeer)          # web refunded 100
    repo.add_payment("2026-10-04", "product", "courier", None, 10, 115_000, 11_500, wallet_id=card)
    repo.add_stock_move("2026-10-05", product, 10, "purchase", cost_usd=20, wallet_id=card)
    repo.add_payment("2026-10-05", "traffic", "advertiser", sanzh, 50)                       # no wallet
    repo.add_transfer("2026-10-06", payeer, card, 100, 1_140_000, "обмен")

    states = {w.name: w for w in calc.wallet_states(repo)}
    assert states["Payeer"].balance == pytest.approx(1000 + 3000 - 1500 - 5 + 100 - 100)
    assert states["Карта"].balance == pytest.approx(1_150_000 + 115_000 - 20 * 11_500 + 1_140_000)
    assert states["Карта"].balance_usd == pytest.approx(states["Карта"].balance / 11_500)

    repo.set_wallet_balance(payeer, 2500, "2026-10-06")
    assert {w.name: w for w in calc.wallet_states(repo)}["Payeer"].balance == pytest.approx(2500)


def test_position_own_money(repo):
    wallet = repo.add_wallet("Payeer", "usd", 5000, "2026-10-01")
    sanzh = repo.add_advertiser("Санж")
    maks = repo.add_web("Макс")
    donik = repo.add_web("Доник")
    calc.reconcile(repo, TODAY, "advertiser", sanzh, "traffic", 4192, "2026-10-01")
    calc.reconcile(repo, TODAY, "web", maks, "traffic", -2921, "2026-10-01")
    calc.reconcile(repo, TODAY, "web", donik, "traffic", 3984.80, "2026-10-01")
    pos = calc.position(repo, TODAY)
    assert pos.wallets_usd == 5000
    assert pos.own == pytest.approx(5000 - 4192 + 3984.80 - 2921)
    assert wallet


def test_wallet_pages_and_forms(repo, tmp_path):
    s = Settings(secret_key="x", admin_password_hash=hash_password("p"), session_https_only=False, data_dir=str(tmp_path))
    client = TestClient(create_app(s, repo=repo, today_fn=lambda: TODAY))
    client.post("/login", data={"username": "admin", "password": "p"})
    assert "Кошельков пока нет" in client.get("/wallets").text
    client.post("/wallets/add", data={"name": "Payeer", "currency": "usd", "balance": 1000, "date": "2026-10-01"})
    client.post("/wallets/add", data={"name": "Карта", "currency": "uzs", "balance": 0, "date": "2026-10-01"})
    payeer, card = (w["id"] for w in repo.wallets())
    sanzh = repo.add_advertiser("Санж")
    client.post("/money/payment", data={"party": f"advertiser:{sanzh}", "date": "2026-10-02", "amount": 500,
                                        "currency": "usd", "wallet_id": payeer})
    client.post("/money/expense", data={"direction": "general", "category": "Сервер", "date": "2026-10-02",
                                        "amount": 20, "currency": "usd", "wallet_id": payeer})
    r = client.post("/wallets/transfer", data={"from_wallet": payeer, "to_wallet": card, "amount_out": 100,
                                               "amount_in": "", "date": "2026-10-03"}, follow_redirects=False)
    assert "error=" in r.headers["location"]  # different currencies need the received amount
    client.post("/wallets/transfer", data={"from_wallet": payeer, "to_wallet": card, "amount_out": 100,
                                           "amount_in": 1_150_000, "date": "2026-10-03"})
    states = {w.name: w for w in calc.wallet_states(repo)}
    assert states["Payeer"].balance == pytest.approx(1000 + 500 - 20 - 100)
    assert states["Карта"].balance == pytest.approx(1_150_000)
    page = client.get("/wallets").text
    assert "Ваши собственные деньги" in page and "Payeer" in page
    assert "Деньги сейчас" in client.get("/").text
    money = client.get("/money?period=all").text
    assert 'name="wallet_id"' in money and "Payeer" in money

    client.post("/wallets/reconcile", data={"wallet_id": payeer, "balance": 2000, "date": "2026-10-04"})
    assert {w.name: w for w in calc.wallet_states(repo)}["Payeer"].balance == pytest.approx(2000)


def test_assistant_wallet(repo):
    repo.add_wallet("Payeer", "usd", 0, "2026-10-01")
    repo.add_advertiser("Санж")
    apply_action(repo, "add_payment", {"party_type": "advertiser", "party_name": "Санж", "direction": "traffic",
                                       "amount": 300, "currency": "usd", "date": "2026-10-05", "wallet": "payeer"})
    assert calc.wallet_states(repo)[0].balance == 300
    assert "Кошельки: Payeer ($, остаток 300.00)" in build_context(repo, TODAY)
    with pytest.raises(ValueError, match="Кошелёк"):
        apply_action(repo, "add_expense", {"direction": "general", "category": "X", "amount": 1, "currency": "usd",
                                           "date": "2026-10-05", "wallet": "Нет такого"})


def test_position_counts_stock_at_cost(repo):
    repo.add_wallet("Payeer", "usd", 100, "2026-10-01")
    product = repo.add_product("Glycofort", 2.2, 1070, "2026-10-01")
    repo.add_order("2026-10-05", product, 5, 990_000)  # 5 units in transit, 1065 on hand
    pos = calc.position(repo, TODAY)
    assert pos.stock_usd == pytest.approx(1070 * 2.2)
    assert pos.own == pytest.approx(100 + 1070 * 2.2)
