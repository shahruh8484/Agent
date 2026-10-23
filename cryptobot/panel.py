"""Web control panel for running EVEDEX bots.

Reads what each bot writes into its state dir (see evedex.BotFiles) and lets you
pause/resume a bot or close its position. Bots and the panel are separate processes,
so the panel can be restarted without touching open trades.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from cryptobot.evedex import EvedexClient, EvedexError

PAGE = Path(__file__).parent / "panel.html"


def _read_csv(path: Path, tail: int) -> list[dict]:
    if not path.exists():
        return []
    df = pd.read_csv(path).tail(tail)
    return json.loads(df.to_json(orient="records"))


def create_app(state_dir: str | Path = "paper_state", password: str | None = None, client_factory=EvedexClient) -> FastAPI:
    state_dir = Path(state_dir)
    password = password if password is not None else os.getenv("PANEL_PASSWORD", "")
    security = HTTPBasic(auto_error=False)

    def auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
        if not password:
            return
        if credentials is None or not secrets.compare_digest(credentials.password.encode(), password.encode()):
            raise HTTPException(401, "wrong password", headers={"WWW-Authenticate": "Basic"})

    app = FastAPI(title="cryptobot panel", dependencies=[Depends(auth)])

    def bot_dirs() -> dict[str, Path]:
        if not state_dir.exists():
            return {}
        return {d.name: d for d in sorted(state_dir.iterdir()) if (d / "status.json").exists()}

    def bot_dir(name: str) -> Path:
        dirs = bot_dirs()
        if name not in dirs:  # also rules out path traversal: only known dirs are accepted
            raise HTTPException(404, f"no bot {name}")
        return dirs[name]

    def status_of(d: Path) -> dict:
        status = json.loads((d / "status.json").read_text())
        poll = status.get("poll_seconds", 60)
        status["alive"] = time.time() - status.get("updated_at", 0) < poll * 3 + 30
        return status

    def set_paused(d: Path, paused: bool) -> None:
        (d / "control.json").write_text(json.dumps({"paused": paused}))

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE.read_text(encoding="utf-8")

    @app.get("/api/bots")
    def list_bots() -> list[dict]:
        return [
            {
                "name": name,
                "status": status_of(d),
                "trades": _read_csv(d / "trades.csv", 50),
                "equity": _read_csv(d / "equity.csv", 2000),
            }
            for name, d in bot_dirs().items()
        ]

    @app.post("/api/bots/{name}/pause")
    def pause(name: str) -> dict:
        set_paused(bot_dir(name), True)
        return {"paused": True}

    @app.post("/api/bots/{name}/resume")
    def resume(name: str) -> dict:
        set_paused(bot_dir(name), False)
        return {"paused": False}

    @app.post("/api/bots/{name}/close")
    def close(name: str) -> dict:
        d = bot_dir(name)
        config = status_of(d)["config"]
        # Pause first so the bot doesn't re-enter on the next candle.
        set_paused(d, True)
        try:
            result = client_factory(config["env"]).close(config["instrument"])
        except EvedexError as exc:
            raise HTTPException(502, f"EVEDEX: {exc}")
        return {"paused": True, **result}

    return app
