"""Standalone creative/landing-page cloning service: upload reference ad
creatives and/or a reference landing page (files or a URL), and the agent
generates a brand-new, original creative set and landing page "of the
same type" and publishes it at /lp/{slug} on this service's own domain.

No lead-capture/CPA integration and no Facebook campaign creation — see
the main fbadsagent.web dashboard for that. Run locally with:

    python -m fbadsagent.cloner

See fbadsagent/web/security.py to generate ADMIN_PASSWORD_HASH (shared
auth module), and README.md's "Cloner service" section for deployment.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from fbadsagent.cloner.models import ClonerProject
from fbadsagent.cloner.pipeline import run_cloner_project
from fbadsagent.cloner.store import ClonerProjectStore
from fbadsagent.config import Settings, get_settings
from fbadsagent.web.security import verify_password

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def create_app(
    settings: Settings | None = None,
    project_store: ClonerProjectStore | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if not settings.secret_key:
        raise RuntimeError(
            "SECRET_KEY is not set. Add a long random value to .env — it signs the "
            "login session cookies. Generate one with: "
            "python -c \"import secrets; print(secrets.token_hex(32))\""
        )

    project_store = project_store or ClonerProjectStore(
        Path(settings.data_dir) / "cloner_projects.json"
    )
    # image_generator writes to <output_dir>/creatives/*.png — point output_dir
    # at data_dir so generated images land in the mounted volume.
    generation_settings = settings.model_copy(update={"output_dir": settings.data_dir})
    creatives_dir = Path(settings.data_dir) / "creatives"
    creatives_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="Landing Page & Creative Cloner")
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=settings.session_https_only,
    )
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.mount("/creative-assets", StaticFiles(directory=str(creatives_dir)), name="creative-assets")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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
                {"error": "Неверное имя пользователя или пароль"},
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
                "projects": project_store.list_projects(),
                "domain": settings.domain.strip(),
            },
        )

    @app.post("/projects/add")
    async def add_project(
        request: Request,
        name: str = Form(...),
        description: str = Form(""),
        language: str = Form("Uzbek"),
        landing_style: str = Form("static"),
        checkout_url: str = Form(""),
        reference_landing_urls: str = Form(""),
        reference_landing_screenshots: list[UploadFile] = File(default=[]),
        reference_creatives: list[UploadFile] = File(default=[]),
    ):
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=302)

        project_id = uuid.uuid4().hex[:12]
        url_list = [u.strip() for u in reference_landing_urls.splitlines() if u.strip()]

        screenshot_paths = await _save_uploads(
            reference_landing_screenshots,
            Path(settings.data_dir) / "reference_screenshots" / project_id,
        )
        creative_paths = await _save_uploads(
            reference_creatives,
            Path(settings.data_dir) / "reference_creatives" / project_id,
        )

        project_store.save(
            ClonerProject(
                id=project_id,
                name=name,
                description=description,
                language=language.strip() or "Uzbek",
                landing_style=landing_style if landing_style in ("static", "quiz") else "static",
                checkout_url=checkout_url.strip(),
                created_at=_now(),
                reference_landing_urls=url_list,
                reference_landing_screenshot_paths=screenshot_paths,
                reference_creative_paths=creative_paths,
            )
        )
        return RedirectResponse("/", status_code=302)

    @app.post("/projects/generate")
    def generate_project(request: Request, project_id: str = Form(...)):
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=302)
        project = project_store.get_project(project_id)
        if project is not None:
            updated = run_cloner_project(project, generation_settings)
            project_store.save(updated)
        return RedirectResponse("/", status_code=302)

    @app.post("/projects/delete")
    def delete_project(request: Request, project_id: str = Form(...)):
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=302)
        project_store.remove(project_id)
        return RedirectResponse("/", status_code=302)

    @app.get("/lp/{slug}")
    def public_landing_page(slug: str, request: Request):
        project = project_store.get_by_slug(slug)
        if project is None or project.status != "done":
            return JSONResponse({"error": "not found"}, status_code=404)
        if project.landing_style == "quiz":
            context = {
                "project": project,
                "quiz_questions_json": [q.model_dump() for q in project.quiz_questions],
            }
            return templates.TemplateResponse(request, "landing_quiz.html", context)
        return templates.TemplateResponse(request, "landing_static.html", {"project": project})

    return app


async def _save_uploads(uploads: list[UploadFile], dest_dir: Path) -> list[str]:
    files = [f for f in uploads if f.filename]
    if not files:
        return []
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, upload in enumerate(files):
        ext = Path(upload.filename).suffix or ".png"
        dest = dest_dir / f"{i}{ext}"
        dest.write_bytes(await upload.read())
        paths.append(str(dest))
    return paths
