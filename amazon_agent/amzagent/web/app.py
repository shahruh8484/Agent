"""FastAPI app: the public affiliate sites and the admin dashboard.

Public (no login):
  /s/{slug}/                 site home — the niche's selected products
  /s/{slug}/p/{asin}         product page (push ads land here)
  /s/{slug}/about            affiliate disclosure + privacy
  /go/{slug}/{asin}          logs a click, redirects to Amazon
  /media/{slug}/{file}       push creative images

Admin (login): /, /login, /logout and the POST actions below.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from amzagent.agent.runner import (
    KILLED,
    PAUSE_FLAG,
    build_deps,
    run_cycle,
    stop_all,
    stop_campaign,
)
from amzagent.amazon.creator_connections import parse_opportunities, parse_opportunity_details
from amzagent.config import Settings, get_settings
from amzagent.models import Product
from amzagent.store import ACTIVE, STOPPED, Store

logger = logging.getLogger(__name__)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
SITE_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "site" / "templates")
)
PRICE_MAX_AGE = timedelta(hours=24)
SAFE_PARAM = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def price_is_fresh(product: Product) -> bool:
    """Amazon only allows showing a price fetched within the last 24h."""
    if product.price is None or not product.fetched_at:
        return False
    try:
        fetched = datetime.fromisoformat(product.fetched_at)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - fetched < PRICE_MAX_AGE


def _clean(value: str | None) -> str | None:
    return value if value and SAFE_PARAM.match(value) else None


def create_app(settings: Settings | None = None, store: Store | None = None,
               start_loop: bool = True) -> FastAPI:
    settings = settings or get_settings()
    store = store or Store(settings.data_dir)
    media_root = (Path(settings.data_dir) / "media").resolve()

    app = FastAPI(title="Amazon affiliate agent")
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key or "dev-only-insecure-key",
        https_only=settings.session_https_only,
        same_site="lax",
    )
    app.state.settings = settings
    app.state.store = store
    store.close_interrupted_runs()

    def run_in_background(niche_id: int | None = None, discover: int = 0) -> None:
        def target():
            # Button presses queue behind a running cycle instead of being dropped.
            run_cycle(build_deps(settings, store), niche_id, discover, wait=True)

        threading.Thread(target=target, daemon=True).start()

    if start_loop and settings.agent_interval_hours > 0:
        def loop():
            time.sleep(30)  # let the server come up first
            while True:
                try:
                    run_cycle(build_deps(settings, store))
                except Exception:
                    logger.exception("agent cycle crashed")
                time.sleep(settings.agent_interval_hours * 3600)

        threading.Thread(target=loop, daemon=True).start()

    # --- public sites -----------------------------------------------------

    def site_or_404(slug: str):
        niche = store.get_niche_by_slug(slug)
        if niche is None or not niche.enabled:
            raise HTTPException(404)
        copy = store.get_site_copy(niche.id)
        if copy is None:
            raise HTTPException(404)
        return niche, copy

    def site_ctx(request: Request, niche, site_copy, **extra) -> dict:
        return {
            "request": request,
            "niche": niche,
            "site": site_copy,
            "price_is_fresh": price_is_fresh,
            "year": datetime.now(timezone.utc).year,
            **extra,
        }

    @app.get("/s/{slug}/", response_class=HTMLResponse)
    def site_home(request: Request, slug: str):
        niche, copy = site_or_404(slug)
        products = [(p, c) for p, c in store.list_products(niche.id) if c]
        return SITE_TEMPLATES.TemplateResponse(
            request, "index.html", site_ctx(request, niche, copy, products=products)
        )

    @app.get("/s/{slug}")
    def site_home_redirect(slug: str):
        return RedirectResponse(f"/s/{slug}/", status_code=301)

    @app.get("/s/{slug}/p/{asin}", response_class=HTMLResponse)
    def product_page(request: Request, slug: str, asin: str,
                     c: str | None = None, z: str | None = None):
        niche, copy = site_or_404(slug)
        found = store.get_product(niche.id, asin)
        if found is None or found[1] is None:
            raise HTTPException(404)
        product, product_copy = found
        campaign_id = int(c) if c and c.isdigit() else None
        zone = _clean(z)
        store.log_event("visit", niche.id, asin, campaign_id, zone)
        go = f"/go/{slug}/{asin}"
        if campaign_id:
            go += f"?c={campaign_id}" + (f"&z={zone}" if zone else "")
        others = [(p, pc) for p, pc in store.list_products(niche.id) if pc and p.asin != asin][:4]
        return SITE_TEMPLATES.TemplateResponse(
            request, "product.html",
            site_ctx(request, niche, copy, product=product, copy=product_copy,
                     go_url=go, others=others),
        )

    @app.get("/s/{slug}/about", response_class=HTMLResponse)
    def about_page(request: Request, slug: str):
        niche, copy = site_or_404(slug)
        return SITE_TEMPLATES.TemplateResponse(
            request, "about.html", site_ctx(request, niche, copy)
        )

    @app.get("/go/{slug}/{asin}")
    def outbound(slug: str, asin: str, c: str | None = None, z: str | None = None):
        niche = store.get_niche_by_slug(slug)
        found = store.get_product(niche.id, asin) if niche else None
        if found is None:
            raise HTTPException(404)
        campaign_id = int(c) if c and c.isdigit() else None
        store.log_event("click", niche.id, asin, campaign_id, _clean(z))
        return RedirectResponse(found[0].url, status_code=302)

    @app.get("/media/{slug}/{filename}")
    def media(slug: str, filename: str):
        path = (media_root / slug / filename).resolve()
        if media_root not in path.parents or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    # --- admin ------------------------------------------------------------

    def logged_in(request: Request) -> bool:
        return request.session.get("user") == settings.admin_username

    def to_login() -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return TEMPLATES.TemplateResponse(request, "login.html", {"request": request, "error": None})

    @app.post("/login", response_class=HTMLResponse)
    def login(request: Request, username: str = Form(...), password: str = Form(...)):
        ok = (
            settings.admin_password_hash
            and username == settings.admin_username
            and bcrypt.checkpw(password.encode(), settings.admin_password_hash.encode())
        )
        if not ok:
            return TEMPLATES.TemplateResponse(
                request, "login.html", {"request": request, "error": "Неверный логин или пароль"},
                status_code=401,
            )
        request.session["user"] = username
        return RedirectResponse("/admin", status_code=303)

    @app.get("/logout")
    def logout(request: Request):
        request.session.clear()
        return to_login()

    @app.get("/", response_class=HTMLResponse)
    def hub(request: Request):
        """Public home page: every live site with a few of its products."""
        sites = []
        for n in store.list_niches():
            copy = store.get_site_copy(n.id)
            if not n.enabled or copy is None:
                continue
            products = [p for p, c in store.list_products(n.id) if c][:4]
            if products:
                sites.append({"niche": n, "site": copy, "products": products})
        title = settings.site_name or (settings.domain.split(".")[0].title() if settings.domain
                                       else "Top Picks")
        return SITE_TEMPLATES.TemplateResponse(
            request, "hub.html",
            {"request": request, "title": title, "sites": sites,
             "year": datetime.now(timezone.utc).year},
        )

    @app.get("/admin", response_class=HTMLResponse)
    def dashboard(request: Request):
        if not logged_in(request):
            return to_login()
        niches = []
        for n in store.list_niches():
            niches.append({
                "niche": n,
                "site": store.get_site_copy(n.id),
                "products": len(store.list_products(n.id)),
                "stats": store.niche_stats(n.id),
            })
        campaigns = []
        slugs = {n.id: n.slug for n in store.list_niches()}
        for c in store.list_campaigns()[:200]:
            clicks = store.count_events(c["id"], "click")
            c.update(
                slug=slugs.get(c["niche_id"], "?"),
                visits=store.count_events(c["id"], "visit"),
                clicks=clicks,
                cost_per_click=(c["spend"] / clicks) if clicks else None,
            )
            campaigns.append(c)
        return TEMPLATES.TemplateResponse(
            request, "dashboard.html",
            {
                "request": request,
                "settings": settings,
                "niches": niches,
                "campaigns": campaigns,
                "runs": store.list_runs(15),
                "paused": store.get_flag(PAUSE_FLAG) == "1",
                "flash": request.session.pop("flash", None),
                "running": store.run_in_progress(),
                "running_budget": store.running_daily_budget(),
                "active_count": len(store.list_campaigns(statuses=(ACTIVE,))),
            },
        )

    @app.post("/niches")
    def add_niche(request: Request, keywords: str = Form(...), search_index: str = Form("All"),
                  language: str = Form("English"), max_price: str = Form("")):
        if not logged_in(request):
            return to_login()
        try:
            price = float(max_price) if max_price.strip() else None
        except ValueError:
            price = None
        niche = store.add_niche(keywords.strip(), search_index.strip(), language.strip(), price)
        run_in_background(niche.id)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/import")
    def import_opportunities(request: Request, text: str = Form(...),
                             name: str = Form("Top Deals"), language: str = Form("English")):
        if not logged_in(request):
            return to_login()
        asins = parse_opportunities(text)
        if not asins:
            request.session["flash"] = "В тексте не найдено ни одного ASIN."
            return RedirectResponse("/admin", status_code=303)
        name = name.strip() or "Top Deals"
        existing = next(
            (n for n in store.list_niches() if n.asins and n.keywords.lower() == name.lower()),
            None,
        )
        details = parse_opportunity_details(text)
        if existing:
            total = store.merge_niche_asins(existing.id, asins, details)
            niche_id = existing.id
        else:
            niche_id = store.add_niche(name, language=language.strip() or "English",
                                       asins=asins, asin_meta=details).id
            total = len(asins)
        with_epc = sum(1 for v in asins.values() if v is not None)
        request.session["flash"] = (
            f"Импортировано {len(asins)} товаров ({with_epc} с EPC), всего на сайте "
            f"«{name}»: {total}. Агент проверяет их на Amazon — обновите страницу через минуту."
        )
        run_in_background(niche_id)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/niches/{niche_id}/run")
    def run_niche(request: Request, niche_id: int):
        if not logged_in(request):
            return to_login()
        run_in_background(niche_id)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/niches/{niche_id}/toggle")
    def toggle_niche(request: Request, niche_id: int):
        if not logged_in(request):
            return to_login()
        niche = store.get_niche(niche_id)
        if niche:
            store.set_niche_enabled(niche_id, not niche.enabled)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/niches/{niche_id}/delete")
    def delete_niche(request: Request, niche_id: int):
        if not logged_in(request):
            return to_login()
        # Stop its campaigns first so nothing keeps spending on a dead site.
        deps = build_deps(settings, store)
        for c in store.list_campaigns(niche_id=niche_id, statuses=(ACTIVE,)):
            stop_campaign(deps, c, STOPPED, "site deleted")
        store.delete_niche(niche_id)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/discover")
    def discover(request: Request, count: int = Form(1)):
        if not logged_in(request):
            return to_login()
        run_in_background(discover=max(1, min(count, 5)))
        return RedirectResponse("/admin", status_code=303)

    @app.post("/run")
    def run_all(request: Request):
        if not logged_in(request):
            return to_login()
        run_in_background()
        return RedirectResponse("/admin", status_code=303)

    @app.post("/campaigns/{campaign_id}/stop")
    def stop_campaign_route(request: Request, campaign_id: int):
        if not logged_in(request):
            return to_login()
        c = store.get_campaign(campaign_id)
        if c:
            # Manual stop counts as a verdict on the product: don't relaunch.
            stop_campaign(build_deps(settings, store), c, KILLED, "stopped manually")
        return RedirectResponse("/admin", status_code=303)

    @app.post("/killswitch")
    def killswitch(request: Request, on: str = Form(...)):
        if not logged_in(request):
            return to_login()
        store.set_flag(PAUSE_FLAG, "1" if on == "1" else "0")
        if on == "1":
            stop_all(build_deps(settings, store))
        return RedirectResponse("/admin", status_code=303)

    return app
