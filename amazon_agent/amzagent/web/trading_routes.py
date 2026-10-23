"""Panel tab "Трейдинг": watches the EVEDEX trading bot (cryptobot/, the `trader`
service in docker-compose) and pauses it or closes its position.

The bot and the panel share a volume (data/trading). Each bot writes status.json,
trades.csv and equity.csv into its own folder; the panel only writes control.json,
which the bot reads on its next poll. The panel never holds exchange keys."""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

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
}


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
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def equity_chart(rows: list[dict], width: int = 1000, height: int = 160) -> dict | None:
    """SVG polyline for balance + unrealized PnL; None until there are two points."""
    values = [_float(r.get("balance")) + _float(r.get("unrealized_pnl")) for r in rows]
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
    }


def register_trading_routes(app: FastAPI, templates, settings, logged_in, to_login) -> None:
    root = Path(settings.data_dir) / "trading"

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

    def back(request: Request, message: str, name: str = "") -> RedirectResponse:
        request.session["flash"] = message
        return RedirectResponse(f"/admin/trading{'#' + name if name else ''}", status_code=303)

    @app.get("/admin/trading", response_class=HTMLResponse)
    def trading_page(request: Request):
        if not logged_in(request):
            return to_login()
        return templates.TemplateResponse(request, "trading.html", {
            "bots": [load_bot(d) for d in folders().values()],
            "reasons": REASONS,
            "flash": request.session.pop("flash", None),
        })

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

    @app.post("/admin/trading/{name}/close")
    def trading_close(request: Request, name: str):
        if not logged_in(request):
            return to_login()
        # Paused too, so the bot doesn't re-enter on the next candle.
        update_control(folder(name), paused=True, close_requested=True)
        return back(request, "Команда отправлена: бот закроет позицию по рынку в течение минуты и встанет на паузу.", name)
