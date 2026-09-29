"""Panel tab "Facebook": connection, projects (offer + landing page), pixel
code and ad drafts (texts written by the agent, images uploaded by the
owner). Sending campaigns to Facebook comes in the next stage."""
from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.datastructures import UploadFile

from amzagent.content.llm import LLMError, get_llm
from amzagent.fb.client import (
    DEFAULT_API_VERSION,
    check_connection,
    load_connection,
    pixel_code,
    save_connection,
)
from amzagent.fb.texts import LANGUAGES, write_ad_texts
from amzagent.panel_settings import effective

CHECK_FLAG = "fb_last_check"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
FAILED_RE = re.compile(r"\bне (сохран|создан|удалось)")
COUNTRIES = {"UZ": "Узбекистан", "KZ": "Казахстан", "KG": "Киргизия", "TJ": "Таджикистан",
             "RU": "Россия", "US": "США"}


def _money(raw, default: float) -> float:
    try:
        return max(0.0, round(float(str(raw).replace(",", ".")), 2))
    except (TypeError, ValueError):
        return default


def _project_fields(form) -> dict:
    return {
        "name": str(form.get("name") or "").strip()[:80],
        "lander_url": str(form.get("lander_url") or "").strip()[:500],
        "country": str(form.get("country") or "UZ") if form.get("country") in COUNTRIES else "UZ",
        "language": str(form.get("language") or "uz") if form.get("language") in LANGUAGES
        else "uz",
        "product": str(form.get("product") or "").strip()[:3000],
        "daily_budget": _money(form.get("daily_budget"), 10.0),
        "max_cpl": _money(form.get("max_cpl"), 0.0),
    }


def register_fb_routes(app: FastAPI, templates, settings, store, logged_in, to_login) -> None:
    media_root = (Path(settings.data_dir) / "fb_media").resolve()

    def back(request: Request, message: str, anchor: str = "") -> RedirectResponse:
        request.session["flash"] = message
        return RedirectResponse(f"/admin/fb{anchor}", status_code=303)

    @app.get("/admin/fb", response_class=HTMLResponse)
    def fb_page(request: Request):
        if not logged_in(request):
            return to_login()
        conn = load_connection(store)
        try:
            last_check = json.loads(store.get_flag(CHECK_FLAG, "null"))
        except ValueError:
            last_check = None
        projects = store.list_fb_projects()
        for p in projects:
            p["ads"] = store.list_fb_ads(p["id"])
        base, lead = pixel_code(conn["pixel_id"])
        token = conn["token"]
        flash = request.session.pop("flash", None)
        return templates.TemplateResponse(request, "fb.html", {
            "conn": conn, "token_hint": f"…{token[-6:]}" if token else "",
            "last_check": last_check, "projects": projects, "pixel_base": base,
            "pixel_lead": lead, "countries": COUNTRIES, "languages": LANGUAGES,
            "flash": flash, "flash_bad": bool(flash and FAILED_RE.search(flash)),
        })

    @app.post("/admin/fb/connection")
    async def fb_save_connection(request: Request):
        if not logged_in(request):
            return to_login()
        form = await request.form()
        conn = load_connection(store)
        token = str(form.get("token") or "").strip()
        if token:  # empty field = keep the saved token
            conn["token"] = token
        if form.get("forget_token"):
            conn["token"] = ""
        for key in ("ad_account", "page_id", "pixel_id"):
            conn[key] = "".join(ch for ch in str(form.get(key) or "") if ch.isalnum() or ch == "_")
        conn["api_version"] = str(form.get("api_version") or DEFAULT_API_VERSION).strip()[:10]
        save_connection(store, conn)
        return back(request, "Подключение Facebook сохранено. Нажмите «Проверить», чтобы "
                             "убедиться, что всё доступно.", "#conn")

    @app.post("/admin/fb/check")
    def fb_check(request: Request):
        if not logged_in(request):
            return to_login()
        results = check_connection(load_connection(store))
        store.set_flag(CHECK_FLAG, json.dumps({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "results": results}))
        ok = all(r[1] for r in results)
        return back(request, "Facebook: всё подключено." if ok else
                    "Facebook: не удалось проверить подключение — подробности в блоке "
                    "«Подключение».", "#conn")

    @app.post("/admin/fb/projects")
    async def fb_add_project(request: Request):
        if not logged_in(request):
            return to_login()
        fields = _project_fields(await request.form())
        if not fields["name"] or not fields["lander_url"].startswith(("http://", "https://")):
            return back(request, "Проект не сохранён: нужны название и адрес лендинга "
                                 "(начинается с https://).", "#projects")
        pid = store.add_fb_project(**fields)
        return back(request, f"Проект «{fields['name']}» создан.", f"#p{pid}")

    @app.post("/admin/fb/projects/{project_id}")
    async def fb_edit_project(request: Request, project_id: int):
        if not logged_in(request):
            return to_login()
        if store.get_fb_project(project_id) is None:
            raise HTTPException(404)
        fields = _project_fields(await request.form())
        if not fields["name"] or not fields["lander_url"].startswith(("http://", "https://")):
            return back(request, "Проект не сохранён: нужны название и адрес лендинга.",
                        f"#p{project_id}")
        store.update_fb_project(project_id, **fields)
        return back(request, "Проект сохранён.", f"#p{project_id}")

    @app.post("/admin/fb/projects/{project_id}/delete")
    def fb_delete_project(request: Request, project_id: int):
        if not logged_in(request):
            return to_login()
        store.delete_fb_project(project_id)
        return back(request, "Проект удалён.", "#projects")

    @app.post("/admin/fb/projects/{project_id}/texts")
    async def fb_write_texts(request: Request, project_id: int):
        if not logged_in(request):
            return to_login()
        project = store.get_fb_project(project_id)
        if project is None:
            raise HTTPException(404)
        form = await request.form()
        language = str(form.get("language") or project["language"])
        if not project["product"].strip():
            return back(request, "Тексты не созданы: опишите товар и оффер в проекте.",
                        f"#p{project_id}")
        try:
            llm = get_llm(effective(settings, store))
            variants = write_ad_texts(llm, project["product"], language)
        except LLMError as exc:
            return back(request, f"Тексты не созданы: {exc}", f"#p{project_id}")
        for v in variants:
            store.add_fb_ad(project_id, language=language, **v)
        return back(request, f"Агент написал {len(variants)} варианта объявления — проверьте "
                             "и поправьте их ниже.", f"#p{project_id}")

    @app.post("/admin/fb/projects/{project_id}/ads")
    def fb_add_blank_ad(request: Request, project_id: int):
        if not logged_in(request):
            return to_login()
        project = store.get_fb_project(project_id)
        if project is None:
            raise HTTPException(404)
        ad_id = store.add_fb_ad(project_id, language=project["language"])
        return back(request, "Добавлено пустое объявление.", f"#ad{ad_id}")

    @app.post("/admin/fb/ads/{ad_id}")
    async def fb_edit_ad(request: Request, ad_id: int):
        if not logged_in(request):
            return to_login()
        ad = store.get_fb_ad(ad_id)
        if ad is None:
            raise HTTPException(404)
        form = await request.form()
        fields = {
            "primary_text": str(form.get("primary_text") or "").strip()[:500],
            "headline": str(form.get("headline") or "").strip()[:60],
            "description": str(form.get("description") or "").strip()[:60],
        }
        if form.get("language") in LANGUAGES:
            fields["language"] = str(form.get("language"))
        upload = form.get("image")
        if isinstance(upload, UploadFile) and upload.filename:
            data = await upload.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                return back(request, "Картинка не сохранена: больше 8 МБ.", f"#ad{ad_id}")
            try:
                from PIL import Image

                img = Image.open(io.BytesIO(data))
                img.load()
            except Exception:
                return back(request, "Картинка не сохранена: это не изображение JPG/PNG.",
                            f"#ad{ad_id}")
            media_root.mkdir(parents=True, exist_ok=True)
            name = f"ad{ad_id}-{hashlib.sha1(data).hexdigest()[:10]}.jpg"
            img.convert("RGB").save(media_root / name, "JPEG", quality=90)
            fields["image"] = name
        if form.get("remove_image"):
            fields["image"] = ""
        store.update_fb_ad(ad_id, **fields)
        return back(request, "Объявление сохранено.", f"#ad{ad_id}")

    @app.post("/admin/fb/ads/{ad_id}/delete")
    def fb_delete_ad(request: Request, ad_id: int):
        if not logged_in(request):
            return to_login()
        ad = store.get_fb_ad(ad_id)
        store.delete_fb_ad(ad_id)
        return back(request, "Объявление удалено.", f"#p{ad['project_id']}" if ad else "")

    @app.get("/admin/fb/media/{name}")
    def fb_media(request: Request, name: str):
        if not logged_in(request):
            return to_login()
        path = (media_root / name).resolve()
        if media_root not in path.parents or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)
