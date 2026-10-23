"""Panel tab "Трейдинг": watches the EVEDEX trading bot (cryptobot/, the `trader`
service in docker-compose) and pauses it or closes its position.

The bot and the panel share a volume (data/trading). Each bot writes status.json,
trades.csv and equity.csv into its own folder; the panel only writes control.json,
which the bot reads on its next poll. The panel never holds exchange keys."""
from __future__ import annotations

import csv
import html
import json
import logging
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from jinja2 import Undefined
from fastapi.responses import HTMLResponse, RedirectResponse

logger = logging.getLogger(__name__)

REASONS = {
    "EMA cross up": "EMA пересекла вверх",
    "EMA cross down": "EMA пересекла вниз",
    "no signal": "нет сигнала",
    "in position": "в позиции",
    "position size below $1": "позиция меньше $1",
    "stop would be below zero": "стоп ниже нуля",
    "stale TP/SL without a position": "чистка старых стопов",
    "paused from the panel; not opening new positions": "пауза — новые входы отключены",
    "short position not opened by this bot; leaving it alone": "ручной шорт — бот не трогает",
    "closed from the panel": "закрыто из панели",
    "position limit reached": "лимит открытых позиций",
    "news negative; entry skipped": "плохие новости — вход пропущен",
    "news very negative; closing": "очень плохие новости — закрываем",
}
NEWS_MODES = {"shadow": "наблюдение", "on": "включён", "off": "выключен"}
NEWS_EFFECTS = {"block": "пропустить вход", "exit": "закрыть позицию"}


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _read_csv(path: Path, tail: int) -> list[dict]:
    try:
        with path.open(newline="") as f:
            return list(csv.DictReader(f))[-tail:]
    except FileNotFoundError:
        return []


def _float(value, default: float = 0.0) -> float:
    if isinstance(value, Undefined):  # a field the bot hasn't written yet (float() on it raises)
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def price(value) -> str:
    """Prices from $80k BTC down to $0.08 DOGE, with enough digits to tell a stop from the mark."""
    v = _float(value, None)
    if v is None:
        return "—"
    digits = 2 if abs(v) >= 100 else 4 if abs(v) >= 1 else 6
    text = f"{v:.{digits}f}"
    return text.rstrip("0").rstrip(".") if digits > 2 and "." in text else text


MIN_FUNDED = 1.0  # $; below this the account counts as empty


def equity_chart(rows: list[dict], width: int = 1000, height: int = 160) -> dict | None:
    """SVG polyline of the account value; None until there are two points.

    Newer bots write "account" (whole account, including margin held by positions);
    older rows only have the free balance plus this coin's unrealized PnL."""
    values = [_float(r.get("account")) if r.get("account") not in (None, "")
              else _float(r.get("balance")) + _float(r.get("unrealized_pnl")) for r in rows]
    # Start once the account is funded: growth from ~$0 to the first deposit isn't performance.
    start = next((i for i, v in enumerate(values) if v >= MIN_FUNDED), len(values))
    rows, values = rows[start:], values[start:]
    if len(values) < 2:
        return None
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.1 or 1.0
    lo, hi = lo - pad, hi + pad
    points = " ".join(
        f"{i / (len(values) - 1) * width:.1f},{height - (v - lo) / (hi - lo) * height:.1f}"
        for i, v in enumerate(values)
    )
    return {
        "points": points, "width": width, "height": height,
        "min": min(values), "max": max(values),
        "change_pct": (values[-1] / values[0] - 1) * 100 if values[0] else 0.0,
        "first": rows[0].get("time"), "last": rows[-1].get("time"),
    }


def parse_settings(form, schema: dict, rules: list, defaults: dict) -> tuple[dict, list[str]]:
    """Form -> overrides (only values that differ from the bot's .env defaults), checked against the
    schema the bot published. The bot checks again before applying; this is for a clear message now."""
    values, errors = {}, []
    for key, spec in schema.items():
        raw = form.get(key)
        if raw is None:
            continue
        raw = str(raw).strip()
        if spec.get("type") == "choice":
            if raw not in spec.get("options", {}):
                errors.append(f"{spec['label']}: недопустимое значение")
                continue
            values[key] = raw
            continue
        try:
            number = float(raw.replace(",", "."))
        except ValueError:
            errors.append(f"{spec['label']}: не число")
            continue
        if number != number or not spec["min"] <= number <= spec["max"]:
            errors.append(f"{spec['label']}: от {spec['min']} до {spec['max']}")
            continue
        if spec.get("type") == "int":
            if number != int(number):
                errors.append(f"{spec['label']}: целое число")
                continue
            number = int(number)
        values[key] = number
    merged = {**defaults, **values}
    for a, op, b, message in rules:
        if a in merged and b in merged and not (merged[a] < merged[b] if op == "<" else merged[a] <= merged[b]):
            errors.append(message)
    return {k: v for k, v in values.items() if defaults.get(k) != v}, errors


def load_bot(folder: Path) -> dict:
    status = _read_json(folder / "status.json")
    control = _read_json(folder / "control.json")
    state = status.get("state") or {}
    tpsl = state.get("tpsl") or []
    poll = _float(status.get("poll_seconds"), 60)
    return {
        "name": folder.name,
        "status": status,
        "config": status.get("config") or {},
        "state": state,
        "position": state.get("position"),
        "stop": next((t for t in tpsl if t.get("type") == "stop-loss"), None),
        "take_profit": next((t for t in tpsl if t.get("type") == "take-profit"), None),
        "alive": time.time() - _float(status.get("updated_at")) < poll * 3 + 30,
        "paused": bool(control.get("paused")),
        "close_requested": bool(control.get("close_requested")),
        "trades": list(reversed(_read_csv(folder / "trades.csv", 30))),
        "chart": equity_chart(_read_csv(folder / "equity.csv", 2000)),
        "news": status.get("news"),
    }


def register_trading_routes(app: FastAPI, templates, settings, logged_in, to_login) -> None:
    root = Path(settings.data_dir) / "trading"
    templates.env.filters["price"] = price

    def folders() -> dict[str, Path]:
        if not root.is_dir():
            return {}
        return {d.name: d for d in sorted(root.iterdir()) if (d / "status.json").is_file()}

    def folder(name: str) -> Path:
        found = folders()
        if name not in found:  # only known folders, so no path tricks
            raise HTTPException(404)
        return found[name]

    def update_control(d: Path, **fields) -> None:
        path = d / "control.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**_read_json(path), **fields}))
        tmp.replace(path)

    def back(request: Request, message: str, name: str = "", bad: bool = False) -> RedirectResponse:
        request.session["flash"] = message
        request.session["flash_bad"] = bad
        return RedirectResponse(f"/admin/trading{'#' + name if name else ''}", status_code=303)

    @app.get("/admin/trading", response_class=HTMLResponse)
    def trading_page(request: Request):
        if not logged_in(request):
            return to_login()
        try:
            bots = [load_bot(d) for d in folders().values()]
            page = templates.get_template("trading.html").render({
                "request": request, "bots": bots, "flash": request.session.pop("flash", None),
                "flash_bad": request.session.pop("flash_bad", False),
                "reasons": REASONS, "news_modes": NEWS_MODES, "news_effects": NEWS_EFFECTS,
            })
        except Exception as exc:
            # Bot files come from another process and version: show what broke instead of a bare 500.
            logger.exception("trading tab failed to render")
            where = traceback.extract_tb(exc.__traceback__)[-1]
            return HTMLResponse(
                "<!doctype html><meta charset=utf-8><title>Трейдинг — ошибка</title>"
                "<body style='font:15px system-ui;max-width:760px;margin:40px auto;padding:0 16px'>"
                "<h1 style='font-size:1.2rem'>Вкладка «Трейдинг» не смогла открыться</h1>"
                f"<p><code>{html.escape(type(exc).__name__)}: {html.escape(str(exc))[:500]}</code></p>"
                f"<p style='color:#666'>{html.escape(Path(where.filename).name)}, строка {where.lineno}</p>"
                "<p>Пришлите этот текст разработчику. Остальные вкладки: <a href='/admin'>Amazon</a> · "
                "<a href='/admin/fb'>Facebook</a> · <a href='/admin/ig'>iGaming</a></p>", status_code=500)
        return HTMLResponse(page)

    @app.post("/admin/trading/{name}/pause")
    def trading_pause(request: Request, name: str):
        if not logged_in(request):
            return to_login()
        update_control(folder(name), paused=True)
        return back(request, "Бот на паузе: новых сделок не будет, текущая закроется по сигналу или стопу.", name)

    @app.post("/admin/trading/{name}/resume")
    def trading_resume(request: Request, name: str):
        if not logged_in(request):
            return to_login()
        update_control(folder(name), paused=False)
        return back(request, "Бот снова торгует.", name)

    @app.post("/admin/trading/{name}/settings")
    async def trading_settings(request: Request, name: str):
        if not logged_in(request):
            return to_login()
        d = folder(name)
        config = _read_json(d / "status.json").get("config") or {}
        schema = config.get("settings_schema")
        if not schema:
            return back(request, "Эта версия бота не умеет менять настройки из панели — обновите сервер.", name, bad=True)
        overrides, errors = parse_settings(await request.form(), schema, config.get("settings_rules") or [],
                                           config.get("settings_defaults") or {})
        if errors:
            return back(request, "Настройки не сохранены: " + "; ".join(errors), name, bad=True)
        path = d / "settings.json"
        if overrides:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({**overrides, "updated_at": time.time()}))
            tmp.replace(path)
        else:
            path.unlink(missing_ok=True)
        with (d / "settings_history.csv").open("a", newline="") as f:
            csv.writer(f).writerow([time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), json.dumps(overrides)])
        return back(request, "Настройки сохранены. Бот применит их в течение минуты, сделки — с ближайшей свечи.", name)

    @app.post("/admin/trading/{name}/settings/reset")
    def trading_settings_reset(request: Request, name: str):
        if not logged_in(request):
            return to_login()
        d = folder(name)
        (d / "settings.json").unlink(missing_ok=True)
        with (d / "settings_history.csv").open("a", newline="") as f:
            csv.writer(f).writerow([time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "{}"])
        return back(request, "Вернул настройки из .env. Бот применит их в течение минуты.", name)

    @app.post("/admin/trading/{name}/close")
    def trading_close(request: Request, name: str):
        if not logged_in(request):
            return to_login()
        # Paused too, so the bot doesn't re-enter on the next candle.
        update_control(folder(name), paused=True, close_requested=True)
        return back(request, "Команда отправлена: бот закроет позицию по рынку в течение минуты и встанет на паузу.", name)
