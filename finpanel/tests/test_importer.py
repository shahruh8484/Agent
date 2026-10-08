import pytest

from finance import calc, importer
from tests.conftest import TODAY

# Copied from Google Sheets: tab-separated, header + totals row, Russian decimals.
SHEET = (
    "\tприход\tрасход\t\n"
    "04.08.2026\t497\t\tнексус\n"
    "05.08.2026\t\t612\tмакс\n"
    "05.08.2026\t\t4,53\tкомиссия\n"
    "08.08.2026\t\t100\tсервер\n"
    "13.08.2026\t2000\t\tсанж\n"
    "21.08.2026\t\t253\tмакс\n"
    "21.08.2026\t\t253\tмакс\n"
    "25.08.2026\t3000\t\tпополнение\n"
    "\t5497\t1222,53\t4274,47\n"
)


def test_parse_skips_header_and_totals():
    rows, problems = importer.parse(SHEET)
    assert problems == []
    assert len(rows) == 8
    assert rows[0].date == "2026-08-04" and rows[0].amount_in == 497 and rows[0].name == "нексус"
    assert rows[2].amount_out == pytest.approx(4.53)


def test_guesses(repo):
    rows, _ = importer.parse(SHEET)
    kinds = {n["name"]: n["kind"] for n in importer.summarize(repo, rows)}
    assert kinds == {
        "нексус": "advertiser", "санж": "advertiser", "макс": "web_traffic",
        "комиссия": "expense_traffic", "сервер": "expense_general", "пополнение": "owner",
    }


def test_apply_import_and_reimport_is_safe(repo):
    rows, _ = importer.parse(SHEET)
    mapping = {n["name"].lower(): n["kind"] for n in importer.summarize(repo, rows)}
    res = importer.apply(repo, rows, mapping)
    assert (res.payments, res.expenses, res.duplicates) == (6, 2, 0)  # both 253s kept
    assert sorted(res.created) == ["веб Макс", "рекл Нексус", "рекл Санж"]

    adv = {b.name: b.balance for b in calc.advertiser_balances(repo, TODAY)}
    assert adv == {"Нексус": 497, "Санж": 2000}  # no stats yet: all unworked prepayment
    web = calc.web_balances(repo, TODAY, "traffic")[0]
    assert web.paid == pytest.approx(612 + 253 + 253)

    cash = calc.cash_flow(repo, None, None)
    assert cash.inflow == pytest.approx(2497)
    assert cash.outflow == pytest.approx(1118 + 4.53 + 100)
    assert cash.owner == pytest.approx(3000)  # own money kept apart from business cash

    again = importer.apply(repo, rows, mapping)
    assert (again.payments, again.expenses, again.duplicates) == (0, 0, 8)

    # aliases remembered for next time
    assert repo.import_aliases()["пополнение"] == "owner"


def test_refund_from_web_and_bad_rows(repo):
    rows, problems = importer.parse("01.09.2026\t50\t\tмакс\n02.09.2026\tабв\t\tмакс\n03.09.2026\t10\t\t\n")
    assert len(rows) == 1 and len(problems) == 2
    res = importer.apply(repo, rows, {"макс": "web_traffic"})
    assert res.payments == 1
    assert repo.payments()[0]["amount_usd"] == -50  # web returned money


def test_import_pages(repo, tmp_path):
    from fastapi.testclient import TestClient

    from finance.app import create_app
    from finance.config import Settings
    from finance.security import hash_password

    s = Settings(secret_key="x", admin_password_hash=hash_password("p"), session_https_only=False, data_dir=str(tmp_path))
    client = TestClient(create_app(s, repo=repo, today_fn=lambda: TODAY))
    client.post("/login", data={"username": "admin", "password": "p"})
    assert client.get("/import").status_code == 200
    preview = client.post("/import/preview", data={"text": SHEET})
    assert preview.status_code == 200 and "Кто есть кто" in preview.text and "пополнение" in preview.text

    names = importer.summarize(repo, importer.parse(SHEET)[0])
    form = {"text": SHEET}
    for i, n in enumerate(names, start=1):
        form[f"name_{i}"] = n["name"]
        form[f"kind_{i}"] = n["kind"]
    r = client.post("/import/commit", data=form, follow_redirects=False)
    assert r.headers["location"].startswith("/money")
    assert "error" not in r.headers["location"]
    assert len(repo.payments()) == 6
    money = client.get("/money?period=all").text
    assert "Свои деньги" in money


def test_dates_with_commas():
    rows, problems = importer.parse("\tприход\tрасход\n01,10,2026\t10000\t\tсанж\n01,10,2026\t\t4,9\tкомиссия\n03.10.26\t400\t\tвикинг\n")
    assert problems == []
    assert [(r.date, r.amount_in, r.amount_out, r.name) for r in rows] == [
        ("2026-10-01", 10000, 0, "санж"),
        ("2026-10-01", 0, 4.9, "комиссия"),
        ("2026-10-03", 400, 0, "викинг"),
    ]
