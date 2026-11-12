"""Сводная таблица по всем моделям, которые прогоняли через run_eval.py.

Запуск: python compare.py   →   reports/comparison.md
"""
from __future__ import annotations

import json

from bot import HERE


def main() -> None:
    runs = []
    for f in sorted((HERE / "reports").glob("*/results.json")):
        runs.append((f.parent.name, json.loads(f.read_text(encoding="utf-8"))))
    if not runs:
        print("Нет отчётов. Сначала запустите run_eval.py.")
        return

    lines = ["# Сравнение моделей", "",
             "| Модель | Пройдено | JSON | Action | Язык | Адрес | Мед. обещ. | Длинные фразы | Ответ ср. | p95 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for tag, d in runs:
        s, n = d["summary"], d["summary"]["scenarios"]
        lines.append(f"| {tag} | **{s['passed']}/{n}** | {s['json_ok']}/{n} | {s['action_ok']}/{n} | "
                     f"{s['lang_ok']}/{n} | {s['fields_ok']}/{n} | {s['medical_violations']} | "
                     f"{s['long_phrases_pct']}% | {s['latency_avg']} c | {s['latency_p95']} c |")

    ids = list(dict.fromkeys(r["id"] for _, d in runs for r in d["results"]))
    lines += ["", "## По сценариям", "", "| Сценарий | " + " | ".join(t for t, _ in runs) + " |",
              "|---|" + "---|" * len(runs)]
    for sid in ids:
        row = []
        for _, d in runs:
            r = next((x for x in d["results"] if x["id"] == sid), None)
            row.append("—" if r is None else ("✅" if r["passed"] else f"❌ {r['final_action']}"))
        lines.append(f"| {sid} | " + " | ".join(row) + " |")

    out = HERE / "reports" / "comparison.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:3 + len(runs)]))
    print(f"\nПолная таблица: {out}")


if __name__ == "__main__":
    main()
