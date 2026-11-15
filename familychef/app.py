"""FastAPI app: JSON API + single-page web UI."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from familychef import __version__, mailer
from familychef import limits
from familychef.accounts import Accounts, AuthError, verify_google_token
from familychef.catalog import CatalogError, get_catalog, norm_lang
from familychef.models import DIETS, FAVORITES, PlanRequest
from familychef.planner import CATEGORY_NAMES, PlanError, build_plan, pack_label

logger = logging.getLogger("familychef")
COOKIE = "fc_session"

STATIC_DIR = Path(__file__).parent / "static"
DATA_DIR = Path(__file__).parent / "data"


class GoogleLogin(BaseModel):
    credential: str = Field(min_length=20, max_length=5000)


class EmailStart(BaseModel):
    email: str = Field(max_length=200)
    lang: str = "en"


class EmailVerify(BaseModel):
    email: str = Field(max_length=200)
    code: str = Field(max_length=12)
    name: str = Field(default="", max_length=60)


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    settings: dict | None = None


def _google_client_id() -> str:
    return os.environ.get("FC_GOOGLE_CLIENT_ID", "").strip()


def _require_login() -> bool:
    return os.environ.get("FC_REQUIRE_LOGIN", "").strip() in ("1", "true", "yes")


def create_app(accounts: Accounts | None = None) -> FastAPI:
    app = FastAPI(title="FamilyChef", version=__version__)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.state.accounts = accounts

    def acc() -> Accounts:
        if app.state.accounts is None:
            app.state.accounts = Accounts()
        return app.state.accounts

    def current_user(request: Request) -> dict | None:
        return acc().session_user(request.cookies.get(COOKIE))

    def need_user(user: dict | None = Depends(current_user)) -> dict:
        if user is None:
            raise HTTPException(401, "login_required")
        return user

    def start_session(request: Request, response: Response, user_id: int) -> dict:
        token = acc().create_session(user_id)
        response.set_cookie(COOKIE, token, max_age=180 * 86400, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https", path="/")
        return {"user": acc().get_user(user_id)}

    @app.exception_handler(AuthError)
    def auth_error(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse({"detail": exc.code}, status_code=exc.status)

    @app.exception_handler(limits.LimitError)
    def limit_error(_: Request, exc: limits.LimitError) -> JSONResponse:
        # 402 Payment Required: the page shows the FamilyChef Plus screen.
        return JSONResponse({"detail": exc.code, **exc.extra}, status_code=402)

    def entitlements(user: dict | None) -> dict:
        last = acc().last_free_plan_at(user["id"]) if user else None
        return limits.entitlements(user, last)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/account", include_in_schema=False)
    def account_page() -> FileResponse:
        # Same single-page app; the page opens the account panel for this path.
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/privacy", include_in_schema=False)
    def privacy() -> FileResponse:
        return FileResponse(STATIC_DIR / "privacy.html")

    @app.get("/sw.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        # Served from the root so its scope covers the whole site.
        return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/.well-known/assetlinks.json", include_in_schema=False)
    def assetlinks() -> JSONResponse:
        # Digital Asset Links: lets the Android app (Trusted Web Activity) open
        # this site full-screen without the browser address bar.
        import json
        return JSONResponse(json.loads((DATA_DIR / "assetlinks.json").read_text(encoding="utf-8")))

    @app.get("/download", include_in_schema=False)
    def download_apk() -> FileResponse:
        # Android-приложение для установки напрямую, пока его нет в Google Play.
        return FileResponse(STATIC_DIR / "downloads" / "FamilyChef.apk",
                            media_type="application/vnd.android.package-archive",
                            filename="FamilyChef.apk")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/api/regions")
    def regions(lang: str = Query("ru")) -> list[dict]:
        cat = get_catalog()
        lang = norm_lang(lang)
        return [
            {
                "code": r.code, "name": r.name_for(lang), "currency": r.currency, "locale": r.locale,
                "lang": r.lang, "price_date": r.price_date, "price_basis": r.price_basis_for(lang),
                "budget_hint_per_adult_week": r.budget_hint,
                "cities": [{"id": c.id, "name": c.name_for(lang), "index": c.index} for c in r.cities],
            }
            for r in cat.regions.values()
        ]

    @app.get("/api/ingredients")
    def ingredients(region: str = Query("RU"), lang: str = Query("ru")) -> dict:
        cat = get_catalog()
        try:
            reg = cat.region(region)
        except CatalogError as exc:
            raise HTTPException(404, str(exc)) from exc
        lang = norm_lang(lang)
        items = []
        for ing in cat.ingredients.values():
            p = reg.prices.get(ing.id)
            items.append({
                "id": ing.id, "name": ing.name(lang), "unit": ing.unit,
                "category": ing.category, "category_name": CATEGORY_NAMES[lang][ing.category],
                "available": p is not None,
                "pack_size": p.pack_size if p else None,
                "pack_label": pack_label(p, ing.unit, lang, reg.label_lang) if p else None,
                "price": p.price if p else None,
            })
        return {"currency": reg.currency, "items": items}

    @app.get("/api/options")
    def options() -> dict:
        return {"diets": list(DIETS), "favorites": list(FAVORITES)}

    @app.post("/api/plan")
    def plan(req: PlanRequest, user: dict | None = Depends(current_user)) -> dict:
        if user is None and _require_login():
            raise HTTPException(401, "login_required")
        ent = entitlements(user)
        req = limits.check(req, ent)
        try:
            result = build_plan(get_catalog(), req)
        except (PlanError, CatalogError) as exc:
            raise HTTPException(422, str(exc)) from exc
        if user is not None:
            kind = "plus" if ent["unlimited"] else "free"
            result["plan_id"] = acc().save_plan(user["id"], req.model_dump(), result, kind)
            result["entitlements"] = entitlements(acc().get_user(user["id"]))
        return result

    # ------------------------------------------------------------ accounts
    @app.get("/api/auth/config")
    def auth_config() -> dict:
        return {
            "google_client_id": _google_client_id() or None,
            "email": mailer.enabled(),
            "email_test_mode": mailer.is_dev(),
            "require_login": _require_login(),
        }

    @app.post("/api/auth/google")
    def login_google(body: GoogleLogin, request: Request, response: Response) -> dict:
        client_id = _google_client_id()
        if not client_id:
            raise HTTPException(404, "google_disabled")
        claims = verify_google_token(body.credential, client_id)
        return start_session(request, response, acc().login_google(claims))

    @app.post("/api/auth/email/start")
    def email_start(body: EmailStart, request: Request) -> dict:
        if not mailer.enabled():
            raise HTTPException(404, "email_disabled")
        ip = request.client.host if request.client else ""
        code = acc().start_email_code(body.email, ip)
        try:
            mailer.send_code(body.email.strip().lower(), code, norm_lang(body.lang))
        except mailer.MailError as exc:
            logger.error("email send failed: %s", exc)
            raise HTTPException(502, "email_failed") from exc
        out = {"ok": True}
        if mailer.is_dev():
            out["test_code"] = code
        return out

    @app.post("/api/auth/email/verify")
    def email_verify(body: EmailVerify, request: Request, response: Response) -> dict:
        user_id = acc().verify_email_code(body.email, body.code, body.name)
        return start_session(request, response, user_id)

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response) -> dict:
        acc().end_session(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(user: dict = Depends(need_user)) -> dict:
        return {"user": user, "settings": acc().get_settings(user["id"]),
                "entitlements": entitlements(user)}

    @app.get("/api/entitlements")
    def my_entitlements(user: dict | None = Depends(current_user)) -> dict:
        return entitlements(user)

    @app.put("/api/me")
    def update_me(body: ProfileUpdate, user: dict = Depends(need_user)) -> dict:
        if body.name is not None:
            acc().update_name(user["id"], body.name)
        if body.settings is not None:
            acc().save_settings(user["id"], body.settings)
        fresh = acc().get_user(user["id"])
        return {"user": fresh, "settings": acc().get_settings(user["id"]),
                "entitlements": entitlements(fresh)}

    @app.delete("/api/me")
    def delete_me(request: Request, response: Response, user: dict = Depends(need_user)) -> dict:
        acc().delete_user(user["id"])
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/plans")
    def list_plans(user: dict = Depends(need_user)) -> list[dict]:
        # Menu history is a Plus feature; the free plan keeps the latest menu.
        return acc().list_plans(user["id"], limit=20 if entitlements(user)["unlimited"] else 1)

    @app.get("/api/plans/{plan_id}")
    def get_plan(plan_id: int, user: dict = Depends(need_user)) -> dict:
        found = acc().get_plan(user["id"], plan_id)
        if not found:
            raise HTTPException(404, "not_found")
        return found

    @app.delete("/api/plans/{plan_id}")
    def delete_plan(plan_id: int, user: dict = Depends(need_user)) -> dict:
        acc().delete_plan(user["id"], plan_id)
        return {"ok": True}

    return app


app = create_app()
