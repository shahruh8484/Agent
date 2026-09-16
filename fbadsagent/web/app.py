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
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from fbadsagent.config import Settings, get_settings
from fbadsagent.integrations.traffhub import TraffHubClient, TraffHubError
from fbadsagent.models import LandingPageConfig
from fbadsagent.web.account_store import AccountStore
from fbadsagent.web.cpa_store import CpaNetworkStore
from fbadsagent.web.insights_client import FacebookInsightsClient, InsightsError
from fbadsagent.web.landing_store import LandingPageStore, slugify
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


class LeadSubmission(BaseModel):
    fio: str
    phone: str


def create_app(
    settings: Settings | None = None,
    insights_client: FacebookInsightsClient | None = None,
    account_store: AccountStore | None = None,
    cpa_store: CpaNetworkStore | None = None,
    landing_store: LandingPageStore | None = None,
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
    landing_store = landing_store or LandingPageStore(Path(settings.data_dir) / "landing_pages.json")

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
                "test_message": None,
            },
        )

    @app.post("/cpa-networks/test")
    def test_cpa_network(request: Request, name: str = Form(...)):
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=302)

        network = next((n for n in cpa_store.list_networks() if n.name == name), None)
        if network is None:
            message = f"{name}: not found."
        elif name.strip().lower() != "traff-hub":
            message = (
                f"{name}: no API client implemented for this network yet "
                "(only traff-hub is wired up so far)."
            )
        elif not network.api_key:
            message = f"{name}: no API key saved."
        else:
            try:
                client = TraffHubClient(network.api_key, network.base_url)
                client.list_conversions(page=1, on_page=1)
                message = f"{name}: connection OK."
            except TraffHubError as exc:
                message = f"{name}: {exc}"

        return templates.TemplateResponse(
            request,
            "cpa_networks.html",
            {
                "user": request.session.get("user"),
                "networks": cpa_store.list_networks(),
                "test_message": message,
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

    @app.get("/landing-pages")
    def landing_pages_page(request: Request, error: str | None = None):
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=302)
        return templates.TemplateResponse(
            request,
            "landing_pages.html",
            {
                "user": request.session.get("user"),
                "pages": landing_store.list_pages(),
                "error": error,
            },
        )

    @app.post("/landing-pages/add")
    def add_landing_page(
        request: Request,
        title: str = Form(...),
        headline: str = Form(...),
        subheadline: str = Form(""),
        benefits: str = Form(""),
        cta_text: str = Form("Get Started"),
        cpa_network: str = Form(...),
        campaign_hash: str = Form(...),
    ):
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=302)
        slug = slugify(title)
        benefit_list = [line.strip() for line in benefits.splitlines() if line.strip()]
        landing_store.add_page(
            LandingPageConfig(
                slug=slug,
                title=title,
                headline=headline,
                subheadline=subheadline,
                benefits=benefit_list,
                cta_text=cta_text or "Get Started",
                cpa_network=cpa_network,
                campaign_hash=campaign_hash,
            )
        )
        return RedirectResponse("/landing-pages", status_code=302)

    @app.post("/landing-pages/delete")
    def delete_landing_page(request: Request, slug: str = Form(...)):
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=302)
        landing_store.remove_page(slug)
        return RedirectResponse("/landing-pages", status_code=302)

    @app.get("/lp/{slug}")
    def public_landing_page(slug: str, request: Request):
        page = landing_store.get_page(slug)
        if page is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return templates.TemplateResponse(request, "landing_public.html", {"page": page})

    @app.post("/lp/{slug}/lead")
    def submit_lead(slug: str, request: Request, lead: LeadSubmission):
        page = landing_store.get_page(slug)
        if page is None:
            return JSONResponse({"success": False, "error": "Page not found."}, status_code=404)

        network = next(
            (n for n in cpa_store.list_networks() if n.name == page.cpa_network), None
        )
        if network is None or not network.api_key:
            return JSONResponse(
                {"success": False, "error": "This offer is not configured yet."},
                status_code=400,
            )
        if page.cpa_network.strip().lower() != "traff-hub":
            return JSONResponse(
                {"success": False, "error": "No client implemented for this network yet."},
                status_code=400,
            )

        forwarded_for = request.headers.get("x-forwarded-for", "")
        client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else (
            request.client.host if request.client else "0.0.0.0"
        )

        try:
            client = TraffHubClient(network.api_key, network.base_url)
            client.send_lead(
                phone=lead.phone,
                fio=lead.fio,
                ip=client_ip,
                campaign_hash=page.campaign_hash,
                referrer=request.headers.get("referer"),
            )
        except TraffHubError as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=502)

        return JSONResponse({"success": True})

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
