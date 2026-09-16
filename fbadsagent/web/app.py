"""FastAPI dashboard: login-protected view of daily clicks, spend, leads
and per-account analytics, backed by the Facebook Marketing API Insights
endpoint.

Run locally with:
    python -m fbadsagent.web

See fbadsagent/web/security.py to generate ADMIN_PASSWORD_HASH, and the
README for deploying this behind a domain with HTTPS.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from fbadsagent.config import Settings, get_settings
from fbadsagent.web.account_store import AccountStore
from fbadsagent.web.cpa_store import CpaNetworkStore
from fbadsagent.web.insights_client import FacebookInsightsClient, InsightsError
from fbadsagent.web.security import verify_password

TEMPLATES_DIR = Path(__file__).parent / "templates"

DATE_PRESETS = [
    ("today", "Today"),
    ("yesterday", "Yesterday"),
    ("last_7d", "Last 7 days"),
    ("last_14d", "Last 14 days"),
    ("last_30d", "Last 30 days"),
    ("last_90d", "Last 90 days"),
    ("this_month", "This month"),
    ("last_month", "Last month"),
]


def create_app(
    settings: Settings | None = None,
    insights_client: FacebookInsightsClient | None = None,
    account_store: AccountStore | None = None,
    cpa_store: CpaNetworkStore | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if not settings.secret_key:
        raise RuntimeError(
            "SECRET_KEY is not set. Add a long random value to .env — it signs the "
            "dashboard's login session cookies. Generate one with: "
            "python -c \"import secrets; print(secrets.token_hex(32))\""
        )

    insights_client = insights_client or FacebookInsightsClient(settings)
    account_store = account_store or AccountStore(
        Path(settings.data_dir) / "accounts.json",
        seed_access_token=settings.fb_access_token,
        seed_account_ids=settings.fb_ad_account_ids_list(),
    )
    cpa_store = cpa_store or CpaNetworkStore(Path(settings.data_dir) / "cpa_networks.json")

    app = FastAPI(title="Facebook Ads Agent Dashboard")
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=settings.session_https_only,
    )
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    def is_authenticated(request: Request) -> bool:
        return bool(request.session.get("authenticated"))

    @app.get("/login")
    def login_form(request: Request):
        if is_authenticated(request):
            return RedirectResponse("/", status_code=302)
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login")
    def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
        valid = username == settings.admin_username and verify_password(
            password, settings.admin_password_hash
        )
        if not valid:
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": "Invalid username or password"},
                status_code=401,
            )
        request.session["authenticated"] = True
        request.session["user"] = username
        return RedirectResponse("/", status_code=302)

    @app.get("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=302)

    @app.get("/")
    def dashboard(request: Request):
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=302)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "user": request.session.get("user"),
                "accounts": account_store.list_accounts(),
                "date_presets": DATE_PRESETS,
            },
        )

    @app.get("/accounts")
    def accounts_page(request: Request, error: str | None = None):
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=302)
        return templates.TemplateResponse(
            request,
            "accounts.html",
            {
                "user": request.session.get("user"),
                "accounts": account_store.list_accounts(),
                "access_token_set": bool(account_store.get_access_token()),
                "error": error,
            },
        )

    @app.post("/accounts/token")
    def update_access_token(request: Request, access_token: str = Form(...)):
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=302)
        account_store.set_access_token(access_token)
        return RedirectResponse("/accounts", status_code=302)

    @app.post("/accounts/add")
    def add_account(
        request: Request, account_id: str = Form(...), account_name: str = Form("")
    ):
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=302)
        account_store.add_account(account_id, account_name)
        return RedirectResponse("/accounts", status_code=302)

    @app.post("/accounts/delete")
    def delete_account(request: Request, account_id: str = Form(...)):
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=302)
        account_store.remove_account(account_id)
        return RedirectResponse("/accounts", status_code=302)

    @app.get("/cpa-networks")
    def cpa_networks_page(request: Request):
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=302)
        return templates.TemplateResponse(
            request,
            "cpa_networks.html",
            {
                "user": request.session.get("user"),
                "networks": cpa_store.list_networks(),
            },
        )

    @app.post("/cpa-networks/add")
    def add_cpa_network(
        request: Request,
        name: str = Form(...),
        base_url: str = Form(""),
        api_key: str = Form(""),
    ):
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=302)
        cpa_store.add_network(name, base_url, api_key)
        return RedirectResponse("/cpa-networks", status_code=302)

    @app.post("/cpa-networks/delete")
    def delete_cpa_network(request: Request, name: str = Form(...)):
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=302)
        cpa_store.remove_network(name)
        return RedirectResponse("/cpa-networks", status_code=302)

    @app.get("/api/accounts")
    def api_accounts(request: Request):
        if not is_authenticated(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse(
            {"accounts": [a.id for a in account_store.list_accounts()]}
        )

    @app.get("/api/insights")
    def api_insights(request: Request, account_id: str, date_preset: str = "last_30d"):
        if not is_authenticated(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            summary = insights_client.get_account_insights(
                account_id, date_preset, access_token=account_store.get_access_token() or None
            )
        except InsightsError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(summary.model_dump())

    return app
