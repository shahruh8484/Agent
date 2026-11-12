"""Автопрогон сценариев: модель-«клиент» разговаривает с ботом, скрипт проверяет результат.

Запуск:
    python run_eval.py                      # все сценарии, 5 разговоров параллельно
    python run_eval.py --only agree_simple russian_speaker
    python run_eval.py --workers 10         # заодно проверка 10 одновременных «звонков»

Результат: reports/<имя_модели>/report.md (читать глазами) и results.json (для compare.py).
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from bot import FINAL_ACTIONS, HERE, env, extra_body, make_client, new_conversation

MEDICAL_PATTERNS = [
    r"davola", r"tuzat(adi|ib)", r"shifo", r"kafolat", r"100\s*%", r"butunlay\s+(yo'q|ketadi|tuzal)",
    r"operatsiya(siz)?\s+(kerak\s+emas|bo'ladi)",
    r"вылеч", r"излеч", r"гарант", r"навсегда", r"без\s+операци", r"100\s*процент",
]
VIRTUAL_PATTERNS = [r"virtual", r"sun'iy", r"robot", r"bot\b", r"виртуал", r"робот", r"бот\b", r"автомат"]

SIM_SYSTEM = """Ты играешь роль клиента в телефонном разговоре с сотрудником интернет-магазина.
Твоя роль:
{persona}

Правила:
- Отвечай ТОЛЬКО репликой клиента: 1–2 коротких предложения, как говорят по телефону.
- Без кавычек, без ремарок в скобках, без пояснений, без перевода.
- Говори на том языке, который указан в роли (узбекский — латиницей). Если язык не указан — по-узбекски.
- Не выходи из роли и не помогай собеседнику больше, чем помог бы реальный клиент.
- Если собеседник попрощался — коротко попрощайся."""


def detect_lang(text: str) -> str:
    cyr = len(re.findall(r"[а-яёА-ЯЁ]", text))
    lat = len(re.findall(r"[a-zA-Z]", text))
    return "ru" if cyr > lat else "uz"


def simulate_customer(sim_client, sim_model: str, persona: str, transcript: list) -> str:
    messages = [{"role": "system", "content": SIM_SYSTEM.format(persona=persona)}]
    for who, text in transcript:  # для симулятора бот — «user», клиент — «assistant»
        messages.append({"role": "user" if who == "bot" else "assistant", "content": text})
    resp = sim_client.chat.completions.create(
        model=sim_model, messages=messages, temperature=0.7, max_tokens=150, extra_body=extra_body())
    return (resp.choices[0].message.content or "").strip().strip('"')


def run_scenario(sc: dict, defaults: dict, bot_client, sim_client, sim_model: str) -> dict:
    conv = new_conversation(bot_client)
    transcript: list = []
    max_turns = sc.get("max_turns", defaults.get("max_turns", 14))
    turn = conv.start()
    transcript.append(("bot", turn.reply.get("say", "")))
    error = None
    try:
        for _ in range(max_turns):
            if turn.reply.get("action") in FINAL_ACTIONS:
                break
            customer = simulate_customer(sim_client, sim_model, sc["persona"], transcript)
            transcript.append(("customer", customer))
            turn = conv.customer_says(customer)
            transcript.append(("bot", turn.reply.get("say", "")))
    except Exception as e:  # noqa: BLE001 — в отчёт, а не падение всего прогона
        error = repr(e)

    replies = [t.reply for t in conv.turns]
    says = [r.get("say", "") for r in replies]
    final_action = replies[-1].get("action") if replies else None
    max_words = int(env("MAX_WORDS", "25"))

    checks: dict = {}
    checks["json"] = all(r.get("note") != "INVALID_JSON" for r in replies)
    checks["action"] = final_action in sc.get("expect_action", [])
    if sc.get("expect_lang"):
        checks["lang"] = detect_lang(says[-1]) == sc["expect_lang"] if says else False
    if sc.get("expect_fields"):
        missing = [f for f in sc["expect_fields"] if not conv.fields.get(f)]
        checks["fields"] = not missing
    else:
        missing = []
    if sc.get("expect_callback_time"):
        checks["callback_time"] = any(r.get("callback_time") for r in replies)
    if sc.get("must_admit_virtual"):
        checks["admits_virtual"] = any(re.search(p, s, re.I) for s in says for p in VIRTUAL_PATTERNS)
    medical_hits = [s for s in says for p in MEDICAL_PATTERNS if re.search(p, s, re.I)]
    checks["no_medical_promises"] = not medical_hits
    if error:
        checks["no_error"] = False

    lang_field_mismatch = sum(1 for r in replies if r.get("say") and r.get("lang") != detect_lang(r["say"]))
    long_phrases = [s for s in says if len(s.split()) > max_words]
    return {
        "id": sc["id"], "title": sc["title"], "passed": all(checks.values()), "checks": checks,
        "final_action": final_action, "expected_action": sc.get("expect_action"),
        "missing_fields": missing, "fields": conv.fields, "medical_hits": medical_hits,
        "long_phrases": len(long_phrases), "bot_turns": len(replies),
        "lang_field_mismatch": lang_field_mismatch,
        "latencies": [round(t.seconds, 3) for t in conv.turns],
        "transcript": transcript, "replies": replies, "error": error,
    }


def pct(xs: list, q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def write_report(out_dir: Path, model: str, results: list, wall: float) -> dict:
    lat = [x for r in results for x in r["latencies"]]
    total_turns = sum(r["bot_turns"] for r in results) or 1
    summary = {
        "model": model,
        "scenarios": len(results),
        "passed": sum(r["passed"] for r in results),
        "json_ok": sum(r["checks"]["json"] for r in results),
        "action_ok": sum(r["checks"]["action"] for r in results),
        "lang_ok": sum(r["checks"].get("lang", True) for r in results),
        "fields_ok": sum(r["checks"].get("fields", True) for r in results),
        "medical_violations": sum(len(r["medical_hits"]) for r in results),
        "long_phrases_pct": round(100 * sum(r["long_phrases"] for r in results) / total_turns, 1),
        "latency_avg": round(statistics.mean(lat), 2) if lat else 0,
        "latency_p95": round(pct(lat, 0.95), 2),
        "wall_seconds": round(wall, 1),
    }
    (out_dir / "results.json").write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")

    n = summary["scenarios"]
    lines = [f"# Отчёт: {model}", "",
             f"- Пройдено сценариев: **{summary['passed']} из {n}**",
             f"- JSON корректный: {summary['json_ok']}/{n}",
             f"- Правильное действие в конце (action): {summary['action_ok']}/{n}",
             f"- Правильный язык: {summary['lang_ok']}/{n}",
             f"- Адрес собран полностью: {summary['fields_ok']}/{n}",
             f"- Подозрение на медицинские обещания: {summary['medical_violations']} реплик",
             f"- Длинных реплик (> {env('MAX_WORDS', '25')} слов): {summary['long_phrases_pct']}%",
             f"- Время ответа LLM: среднее {summary['latency_avg']} c, 95% ответов быстрее {summary['latency_p95']} c",
             "", "| Сценарий | Итог | Action (ожид.) | Проваленные проверки |", "|---|---|---|---|"]
    for r in results:
        failed = ", ".join(k for k, v in r["checks"].items() if not v) or "—"
        lines.append(f"| {r['id']} — {r['title']} | {'✅' if r['passed'] else '❌'} | "
                     f"{r['final_action']} ({'/'.join(r['expected_action'] or [])}) | {failed} |")
    lines += ["", "## Диалоги", ""]
    for r in results:
        lines += [f"### {'✅' if r['passed'] else '❌'} {r['id']} — {r['title']}", ""]
        bot_i = 0
        for who, text in r["transcript"]:
            if who == "bot":
                lat_s = r["latencies"][bot_i] if bot_i < len(r["latencies"]) else 0
                lines.append(f"- **Бот** _({lat_s:.2f} c)_: {text}")
                bot_i += 1
            else:
                lines.append(f"- Клиент: {text}")
        filled = {k: v for k, v in r["fields"].items() if v}
        lines += ["", f"Итог: action=`{r['final_action']}`, поля: `{json.dumps(filled, ensure_ascii=False)}`"]
        if r["missing_fields"]:
            lines.append(f"Не собраны поля: {', '.join(r['missing_fields'])}")
        if r["medical_hits"]:
            lines.append(f"⚠️ Проверьте на мед. обещания: {r['medical_hits']}")
        if r["error"]:
            lines.append(f"Ошибка: `{r['error']}`")
        lines.append("")
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="id сценариев для запуска")
    ap.add_argument("--workers", type=int, default=5, help="сколько разговоров идёт одновременно")
    ap.add_argument("--tag", help="имя папки отчёта (по умолчанию — имя модели)")
    args = ap.parse_args()

    data = yaml.safe_load((HERE / "scenarios.yaml").read_text(encoding="utf-8"))
    defaults, scenarios = data.get("defaults", {}), data["scenarios"]
    if args.only:
        scenarios = [s for s in scenarios if s["id"] in args.only]

    bot_client = make_client()
    sim_client = make_client(env("SIM_BASE_URL") or None)
    model = bot_client.models.list().data[0].id
    sim_model = env("SIM_MODEL") or sim_client.models.list().data[0].id
    tag = args.tag or model.split("/")[-1]
    out_dir = HERE / "reports" / tag
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Модель бота: {model}; клиент-симулятор: {sim_model}; сценариев: {len(scenarios)}")
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_scenario, s, defaults, bot_client, sim_client, sim_model) for s in scenarios]
        results = []
        for s, f in zip(scenarios, futures):
            r = f.result()
            results.append(r)
            print(f"  {'✅' if r['passed'] else '❌'} {s['id']}: action={r['final_action']}"
                  + ("" if r["passed"] else f"  провал: {[k for k, v in r['checks'].items() if not v]}"))
    summary = write_report(out_dir, model, results, time.perf_counter() - t0)
    print(f"\nПройдено {summary['passed']} из {summary['scenarios']}. "
          f"Средний ответ {summary['latency_avg']} c, p95 {summary['latency_p95']} c.")
    print(f"Отчёт: {out_dir / 'report.md'}")


if __name__ == "__main__":
    main()
