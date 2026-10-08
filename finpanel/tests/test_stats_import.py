import io
from datetime import datetime

import openpyxl
import pytest
from fastapi.testclient import TestClient

from finance import calc, stats_import
from finance.app import create_app
from finance.config import Settings
from finance.security import hash_password
from tests.conftest import TODAY

HEADER1 = ["Дата", "Трафик", "", "Показатели", "", "", "", "Конверсии", "", "", "", "", "", "", "Финансы", ""]
HEADER2 = ["Дата", "Клики", "Уники", "Epc", "Cr", "Approve", "Вв%", "Σ", "Σв", "✓", "Ожидание", "✗", "Треш", "Дубли", "✓", "Ожидание"]
DATA = [
    ["Итого", 1369, 1369, 2.18, 73.85, "12.86%", "26.15%", 1369, 1011, 130, 787, 94, 358, 358, 2990, 18101],
    [datetime(2026, 10, 9), 0, 0, 0, 0, "0%", "0%", 0, 0, 0, 0, 0, 0, 0, 0, 0],
    [datetime(2026, 10, 8), 269, 269, 1.1, 80.6, "5.99%", "19.3%", 269, 217, 13, 197, 7, 52, 52, 299, 4531],
    [datetime(2026, 10, 1), 156, 156, 2.5, 65.3, "16.67%", "34.6%", 156, 102, 17, 66, 19, 54, 54, 391, 1518],
]


def xlsx_bytes(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_guess_and_extract():
    rows = stats_import.read_table("stat.xlsx", xlsx_bytes([HEADER1, HEADER2] + DATA))
    header_idx, mapping = stats_import.guess_columns(rows)
    assert mapping == {"date": 0, "leads": 7, "valid": 8, "approves": 9}
    days, problems = stats_import.extract(rows, mapping)
    assert problems == []
    assert [(d.date, d.leads, d.valid, d.approves) for d in days] == [
        ("2026-10-01", 156, 102, 17),
        ("2026-10-08", 269, 217, 13),
    ]  # totals row and empty day skipped


def test_csv_cp1251_semicolon():
    text = "Дата;Всего;Валидные;Подтверждено\n08.10.2026;269;217;13\nИтого;269;217;13\n"
    rows = stats_import.read_table("stat.csv", text.encode("cp1251"))
    _, mapping = stats_import.guess_columns(rows)
    assert mapping == {"date": 0, "leads": 1, "valid": 2, "approves": 3}
    days, _ = stats_import.extract(rows, mapping)
    assert [(d.leads, d.valid, d.approves) for d in days] == [(269, 217, 13)]


def test_bad_files():
    with pytest.raises(stats_import.StatsFileError):
        stats_import.read_table("stat.xls", b"x")
    with pytest.raises(stats_import.StatsFileError):
        stats_import.read_table("stat.xlsx", b"not a zip")


def test_upload_flow_overwrites_days(repo, tmp_path):
    web = repo.add_web("Макс")
    adv = repo.add_advertiser("Санж")
    link = repo.add_link(web, adv, "", "2026-07-01", "approve", 25, 10, "approve", 23, 10)
    repo.upsert_traffic_stat(link, "2026-10-08", 675, 546, 21)  # wrong older numbers
    repo.upsert_traffic_stat(link, "2026-10-09", 500, 400, 30)  # empty day in the export -> cleared
    repo.upsert_traffic_stat(link, "2026-09-30", 100, 90, 9)    # outside the file's period -> kept

    s = Settings(secret_key="x", admin_password_hash=hash_password("p"), session_https_only=False, data_dir=str(tmp_path))
    client = TestClient(create_app(s, repo=repo, today_fn=lambda: TODAY))
    client.post("/login", data={"username": "admin", "password": "p"})
    assert client.get("/import/stats").status_code == 200

    data = xlsx_bytes([HEADER1, HEADER2] + DATA)
    preview = client.post("/import/stats/preview", data={"link_id": link},
                          files={"file": ("stat.xlsx", data, "application/octet-stream")})
    assert preview.status_code == 200 and "Загрузить 2 дн." in preview.text

    tsv = stats_import.to_tsv(stats_import.read_table("stat.xlsx", data))
    form = {"tsv": tsv, "link_id": link, "col_date": 0, "col_leads": 7, "col_valid": 8, "col_approves": 9}
    re = client.post("/import/stats/repreview", data=form)
    assert re.status_code == 200 and "2026-10-08" in re.text
    r = client.post("/import/stats/commit", data=form, follow_redirects=False)
    assert r.headers["location"].startswith("/traffic") and "error" not in r.headers["location"]

    rows = {s["date"]: s for s in repo.traffic_stats()}
    assert (rows["2026-10-08"]["leads"], rows["2026-10-08"]["approves"]) == (269, 13)
    total = calc.traffic_report(repo, None, None, TODAY).total
    assert (total.leads, total.valid, total.approves) == (425 + 100, 319 + 90, 30 + 9)
    assert "2026-10-09" not in rows
