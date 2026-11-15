"""FastAPI app: JSON API + single-page web UI."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from familychef import __version__
from familychef.catalog import CatalogError, get_catalog
from familychef.models import DIETS, FAVORITES, PlanRequest
from familychef.planner import CATEGORY_NAMES, PlanError, build_plan

STATIC_DIR = Path(__file__).parent / "static"
DATA_DIR = Path(__file__).parent / "data"


def create_app() -> FastAPI:
    app = FastAPI(title="FamilyChef", version=__version__)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
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
    def regions() -> list[dict]:
        cat = get_catalog()
        return [
            {
                "code": r.code, "name": r.name, "currency": r.currency, "locale": r.locale,
                "lang": r.lang, "price_date": r.price_date, "price_basis": r.price_basis,
                "budget_hint_per_adult_week": r.budget_hint,
                "cities": [{"id": c.id, "name": c.name, "index": c.index} for c in r.cities],
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
        lang = "en" if lang == "en" else "ru"
        items = []
        for ing in cat.ingredients.values():
            p = reg.prices.get(ing.id)
            items.append({
                "id": ing.id, "name": ing.name(lang), "unit": ing.unit,
                "category": ing.category, "category_name": CATEGORY_NAMES[lang][ing.category],
                "available": p is not None,
                "pack_size": p.pack_size if p else None,
                "pack_label": p.pack_label if p else None,
                "price": p.price if p else None,
            })
        return {"currency": reg.currency, "items": items}

    @app.get("/api/options")
    def options() -> dict:
        return {"diets": list(DIETS), "favorites": list(FAVORITES)}

    @app.post("/api/plan")
    def plan(req: PlanRequest) -> dict:
        try:
            return build_plan(get_catalog(), req)
        except (PlanError, CatalogError) as exc:
            raise HTTPException(422, str(exc)) from exc

    return app


app = create_app()
