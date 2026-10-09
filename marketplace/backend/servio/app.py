"""REST API for the services marketplace mobile app."""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Iterator, Literal
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from servio import integrations
from servio.catalog import CITIES, CITY_IDS
from servio.config import Settings
from servio.db import connect, init_db, iso, now
from servio.i18n import pick_lang, tr
from servio.payments import (
    checkout_url,
    extend_subscription,
    handle_click,
    handle_payme,
    subscription_until,
)


# ---------- request bodies ----------

class CodeRequest(BaseModel):
    phone: str


class VerifyRequest(BaseModel):
    phone: str
    code: str


class MeUpdate(BaseModel):
    name: str | None = Field(None, max_length=80)
    role: Literal["client", "specialist"] | None = None
    city: str | None = Field(None, max_length=40)
    lang: Literal["ru", "uz"] | None = None


class SpecialistProfileIn(BaseModel):
    bio: str = Field("", max_length=3000)
    experience_years: int = Field(0, ge=0, le=80)
    price_from: int | None = Field(None, ge=0)
    remote: bool = False
    category_ids: list[int] = Field(min_length=1, max_length=20)


class OrderIn(BaseModel):
    category_id: int
    title: str = Field(min_length=3, max_length=120)
    description: str = Field("", max_length=3000)
    city: str = Field("", max_length=40)
    budget: int | None = Field(None, ge=0)
    remote: bool = False
    when_text: str = Field("", max_length=120)


class ResponseIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    price: int | None = Field(None, ge=0)


class ChooseIn(BaseModel):
    response_id: int


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    text: str = Field("", max_length=2000)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class CheckoutIn(BaseModel):
    plan_id: str
    provider: str = "dev"


class PushTokenIn(BaseModel):
    token: str = Field(min_length=1, max_length=200)


# ---------- helpers ----------

def normalize_phone(raw: str) -> str:
    """Accepts Uzbek mobile numbers: +998 XX XXX XX XX, with or without the country code."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 9:
        digits = "998" + digits
    if len(digits) != 12 or not digits.startswith("998"):
        raise HTTPException(422, "Некорректный номер телефона")
    return "+" + digits


def check_city(city: str | None) -> None:
    if city and city not in CITY_IDS:
        raise HTTPException(422, "Выберите город из списка")


def row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


def user_out(db: sqlite3.Connection, user: dict) -> dict:
    return {
        **user,
        "subscription_until": subscription_until(db, user["id"]),
        "has_specialist_profile": db.execute(
            "SELECT 1 FROM specialist_profiles WHERE user_id = ?", (user["id"],)
        ).fetchone() is not None,
    }


def specialist_card(db: sqlite3.Connection, user_id: int, lang: str) -> dict | None:
    r = db.execute(
        """
        SELECT u.id, u.name, u.city, p.bio, p.experience_years, p.price_from, p.remote,
               (SELECT ROUND(AVG(rating), 1) FROM reviews WHERE specialist_id = u.id) AS rating,
               (SELECT COUNT(*) FROM reviews WHERE specialist_id = u.id) AS reviews_count
        FROM users u JOIN specialist_profiles p ON p.user_id = u.id
        WHERE u.id = ?
        """,
        (user_id,),
    ).fetchone()
    if r is None:
        return None
    card = dict(r)
    card["remote"] = bool(card["remote"])
    card["categories"] = [
        dict(c) for c in db.execute(
            f"""SELECT c.id, c.name_{lang} AS name FROM categories c
               JOIN specialist_categories sc ON sc.category_id = c.id
               WHERE sc.user_id = ? ORDER BY name""",
            (user_id,),
        )
    ]
    return card


def order_out(r: sqlite3.Row) -> dict:
    o = dict(r)
    o["remote"] = bool(o["remote"])
    return o


def order_select(lang: str) -> str:
    return f"""
    SELECT o.*, c.name_{lang} AS category_name, u.name AS client_name,
           (SELECT COUNT(*) FROM responses r WHERE r.order_id = o.id) AS responses_count
    FROM orders o JOIN categories c ON c.id = o.category_id JOIN users u ON u.id = o.client_id
    """


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    init_db(settings.database_path)
    app = FastAPI(title=f"{settings.app_name} API")

    @app.exception_handler(StarletteHTTPException)
    async def translated_errors(request: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, str):
            exc.detail = tr(exc.detail, pick_lang(request.headers.get("accept-language")))
        return await http_exception_handler(request, exc)

    def get_lang(accept_language: str = Header("ru")) -> str:
        return pick_lang(accept_language)

    def get_db() -> Iterator[sqlite3.Connection]:
        db = connect(settings.database_path)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def current_user(
        authorization: str = Header(""), db: sqlite3.Connection = Depends(get_db),
        lang: str = Depends(get_lang),
    ) -> dict:
        token = authorization.removeprefix("Bearer ").strip()
        r = db.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
            (token,),
        ).fetchone()
        if not token or r is None:
            raise HTTPException(401, "Требуется вход")
        user = dict(r)
        if user["lang"] != lang:  # push notifications go out in the app's current language
            db.execute("UPDATE users SET lang = ? WHERE id = ?", (lang, user["id"]))
            user["lang"] = lang
        return user

    def notify(db: sqlite3.Connection, user_id: int, title: str, body: str, data: dict) -> None:
        tokens = [r[0] for r in db.execute(
            "SELECT token FROM push_tokens WHERE user_id = ?", (user_id,)
        )]
        lang = db.execute("SELECT lang FROM users WHERE id = ?", (user_id,)).fetchone()[0]
        integrations.send_push(settings, tokens, tr(title, lang), body, data)

    def load_order(db: sqlite3.Connection, order_id: int, lang: str) -> dict:
        r = db.execute(order_select(lang) + " WHERE o.id = ?", (order_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "Заказ не найден")
        return order_out(r)

    def own_order(db: sqlite3.Connection, order_id: int, user: dict) -> dict:
        order = load_order(db, order_id, user["lang"])
        if order["client_id"] != user["id"]:
            raise HTTPException(403, "Это не ваш заказ")
        return order

    def chat_for(db: sqlite3.Connection, chat_id: int, user: dict) -> dict:
        r = db.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
        if r is None or user["id"] not in (r["client_id"], r["specialist_id"]):
            raise HTTPException(404, "Чат не найден")
        return dict(r)

    # ---------- auth ----------

    @app.post("/auth/request-code")
    def request_code(body: CodeRequest, db: sqlite3.Connection = Depends(get_db)):
        phone = normalize_phone(body.phone)
        code = f"{secrets.randbelow(10000):04d}"
        expires = iso(now() + timedelta(seconds=settings.code_ttl_seconds))
        db.execute(
            "INSERT OR REPLACE INTO auth_codes (phone, code, expires_at, attempts) VALUES (?, ?, ?, 0)",
            (phone, code, expires),
        )
        integrations.send_sms_code(settings, phone, code)
        out: dict = {"phone": phone, "sent": True}
        if settings.sms_provider == "dev":
            out["debug_code"] = code
        return out

    @app.post("/auth/verify")
    def verify(body: VerifyRequest, db: sqlite3.Connection = Depends(get_db)):
        phone = normalize_phone(body.phone)
        r = db.execute("SELECT * FROM auth_codes WHERE phone = ?", (phone,)).fetchone()
        if r is None or datetime.fromisoformat(r["expires_at"]) < now():
            raise HTTPException(400, "Код устарел, запросите новый")
        if r["attempts"] >= settings.code_max_attempts:
            raise HTTPException(429, "Слишком много попыток, запросите новый код")
        if not secrets.compare_digest(r["code"], body.code.strip()):
            db.execute("UPDATE auth_codes SET attempts = attempts + 1 WHERE phone = ?", (phone,))
            db.commit()  # get_db only commits on success
            raise HTTPException(400, "Неверный код")
        db.execute("DELETE FROM auth_codes WHERE phone = ?", (phone,))
        user = db.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        is_new = user is None
        if is_new:
            db.execute("INSERT INTO users (phone, created_at) VALUES (?, ?)", (phone, iso(now())))
            user = db.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        token = secrets.token_urlsafe(32)
        db.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user["id"], iso(now())),
        )
        return {"token": token, "is_new": is_new, "user": user_out(db, dict(user))}

    @app.post("/auth/logout")
    def logout(authorization: str = Header(""), db: sqlite3.Connection = Depends(get_db)):
        db.execute("DELETE FROM sessions WHERE token = ?", (authorization.removeprefix("Bearer ").strip(),))
        return {"ok": True}

    # ---------- profile ----------

    @app.get("/me")
    def me(user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
        return user_out(db, user)

    @app.patch("/me")
    def update_me(body: MeUpdate, user: dict = Depends(current_user),
                  db: sqlite3.Connection = Depends(get_db)):
        changes = body.model_dump(exclude_none=True)
        check_city(changes.get("city"))
        for field, value in changes.items():
            db.execute(f"UPDATE users SET {field} = ? WHERE id = ?", (value, user["id"]))
        if changes.get("role") == "specialist" and settings.trial_days > 0:
            had_any = db.execute(
                "SELECT 1 FROM subscriptions WHERE user_id = ?", (user["id"],)
            ).fetchone()
            if not had_any:
                extend_subscription(db, user["id"], "trial", settings.trial_days)
        r = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        return user_out(db, dict(r))

    @app.post("/me/push-token")
    def save_push_token(body: PushTokenIn, user: dict = Depends(current_user),
                        db: sqlite3.Connection = Depends(get_db)):
        db.execute("INSERT OR REPLACE INTO push_tokens (token, user_id) VALUES (?, ?)",
                   (body.token, user["id"]))
        return {"ok": True}

    @app.put("/me/specialist")
    def save_specialist_profile(body: SpecialistProfileIn, user: dict = Depends(current_user),
                                db: sqlite3.Connection = Depends(get_db)):
        ids = sorted(set(body.category_ids))
        found = db.execute(
            f"SELECT COUNT(*) FROM categories WHERE parent_id IS NOT NULL AND id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchone()[0]
        if found != len(ids):
            raise HTTPException(422, "Выберите услуги из каталога")
        db.execute(
            """INSERT INTO specialist_profiles (user_id, bio, experience_years, price_from, remote)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET bio = excluded.bio,
                 experience_years = excluded.experience_years,
                 price_from = excluded.price_from, remote = excluded.remote""",
            (user["id"], body.bio, body.experience_years, body.price_from, int(body.remote)),
        )
        db.execute("DELETE FROM specialist_categories WHERE user_id = ?", (user["id"],))
        db.executemany("INSERT INTO specialist_categories (user_id, category_id) VALUES (?, ?)",
                       [(user["id"], i) for i in ids])
        return specialist_card(db, user["id"], user["lang"])

    # ---------- catalog ----------

    @app.get("/categories")
    def categories(db: sqlite3.Connection = Depends(get_db), lang: str = Depends(get_lang)):
        rows = [dict(r) for r in db.execute(
            f"SELECT id, parent_id, name_{lang} AS name FROM categories ORDER BY id"
        )]
        sections = [{**r, "children": []} for r in rows if r["parent_id"] is None]
        by_id = {s["id"]: s for s in sections}
        for r in rows:
            if r["parent_id"] is not None:
                by_id[r["parent_id"]]["children"].append({"id": r["id"], "name": r["name"]})
        return sections

    @app.get("/cities")
    def cities(lang: str = Depends(get_lang)):
        return [{"id": cid, "name": uz if lang == "uz" else ru} for cid, ru, uz in CITIES]

    @app.get("/specialists")
    def specialists(category_id: int | None = None, city: str | None = None,
                    q: str | None = None, db: sqlite3.Connection = Depends(get_db),
                    lang: str = Depends(get_lang)):
        sql = "SELECT DISTINCT u.id FROM users u JOIN specialist_profiles p ON p.user_id = u.id"
        where, args = ["u.role = 'specialist'"], []
        if category_id is not None:
            sql += """ JOIN specialist_categories sc ON sc.user_id = u.id
                       JOIN categories c ON c.id = sc.category_id"""
            where.append("(c.id = ? OR c.parent_id = ?)")
            args += [category_id, category_id]
        if city:
            where.append("(u.city = ? OR p.remote = 1)")
            args.append(city)
        if q:
            where.append("(u.name LIKE ? OR p.bio LIKE ?)")
            args += [f"%{q}%", f"%{q}%"]
        ids = [r[0] for r in db.execute(f"{sql} WHERE {' AND '.join(where)} LIMIT 100", args)]
        cards = [specialist_card(db, i, lang) for i in ids]
        return sorted(cards, key=lambda c: (-(c["rating"] or 0), -c["reviews_count"]))

    @app.get("/specialists/{user_id}")
    def specialist(user_id: int, db: sqlite3.Connection = Depends(get_db),
                   lang: str = Depends(get_lang)):
        card = specialist_card(db, user_id, lang)
        if card is None:
            raise HTTPException(404, "Специалист не найден")
        card["reviews"] = [dict(r) for r in db.execute(
            """SELECT r.id, r.rating, r.text, r.created_at, u.name AS client_name, o.title AS order_title
               FROM reviews r JOIN users u ON u.id = r.client_id JOIN orders o ON o.id = r.order_id
               WHERE r.specialist_id = ? ORDER BY r.id DESC LIMIT 50""",
            (user_id,),
        )]
        return card

    # ---------- orders ----------

    @app.post("/orders")
    def create_order(body: OrderIn, user: dict = Depends(current_user),
                     db: sqlite3.Connection = Depends(get_db)):
        cat = db.execute("SELECT parent_id FROM categories WHERE id = ?", (body.category_id,)).fetchone()
        if cat is None or cat["parent_id"] is None:
            raise HTTPException(422, "Выберите конкретную услугу")
        check_city(body.city)
        order_id = db.execute(
            """INSERT INTO orders (client_id, category_id, title, description, city, budget,
                                   remote, when_text, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user["id"], body.category_id, body.title, body.description, body.city or user["city"],
             body.budget, int(body.remote), body.when_text, iso(now())),
        ).lastrowid
        order = load_order(db, order_id, user["lang"])
        matching = db.execute(
            """SELECT DISTINCT u.id FROM users u
               JOIN specialist_categories sc ON sc.user_id = u.id
               JOIN specialist_profiles p ON p.user_id = u.id
               WHERE sc.category_id = ? AND u.id != ? AND (u.city = ? OR p.remote = 1 OR ?)""",
            (body.category_id, user["id"], order["city"], int(body.remote)),
        ).fetchall()
        for (spec_id,) in matching:
            notify(db, spec_id, "Новый заказ", order["title"], {"order_id": order_id})
        return order

    @app.get("/orders/mine")
    def my_orders(user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
        return [order_out(r) for r in db.execute(
            order_select(user["lang"]) + " WHERE o.client_id = ? ORDER BY o.id DESC", (user["id"],)
        )]

    @app.get("/orders/feed")
    def feed(user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
        """Open orders matching the specialist's services and city (or remote ones)."""
        remote_ok = db.execute(
            "SELECT remote FROM specialist_profiles WHERE user_id = ?", (user["id"],)
        ).fetchone()
        rows = db.execute(
            order_select(user["lang"]) + """
               WHERE o.status = 'open' AND o.client_id != :uid
                 AND o.category_id IN (SELECT category_id FROM specialist_categories WHERE user_id = :uid)
                 AND (o.remote = 1 OR :remote = 1 OR o.city = :city)
               ORDER BY o.id DESC LIMIT 200""",
            {"uid": user["id"], "city": user["city"], "remote": remote_ok[0] if remote_ok else 0},
        ).fetchall()
        responded = {r[0] for r in db.execute(
            "SELECT order_id FROM responses WHERE specialist_id = ?", (user["id"],)
        )}
        return [{**order_out(r), "responded": r["id"] in responded} for r in rows]

    @app.get("/orders/assigned")
    def assigned(user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
        return [order_out(r) for r in db.execute(
            order_select(user["lang"]) + " WHERE o.specialist_id = ? ORDER BY o.id DESC", (user["id"],)
        )]

    @app.get("/orders/{order_id}")
    def get_order(order_id: int, user: dict = Depends(current_user),
                  db: sqlite3.Connection = Depends(get_db)):
        order = load_order(db, order_id, user["lang"])
        mine = order["client_id"] == user["id"]
        my_response = db.execute(
            "SELECT * FROM responses WHERE order_id = ? AND specialist_id = ?", (order_id, user["id"])
        ).fetchone()
        if not mine and order["status"] != "open" and my_response is None:
            raise HTTPException(404, "Заказ не найден")
        order["is_mine"] = mine
        order["my_response"] = row(my_response)
        if my_response is not None:
            order["chat_id"] = db.execute(
                "SELECT id FROM chats WHERE order_id = ? AND specialist_id = ?", (order_id, user["id"])
            ).fetchone()[0]
        order["has_review"] = db.execute(
            "SELECT 1 FROM reviews WHERE order_id = ?", (order_id,)
        ).fetchone() is not None
        return order

    @app.get("/orders/{order_id}/responses")
    def order_responses(order_id: int, user: dict = Depends(current_user),
                        db: sqlite3.Connection = Depends(get_db)):
        own_order(db, order_id, user)
        out = []
        for r in db.execute(
            """SELECT r.*, ch.id AS chat_id FROM responses r
               JOIN chats ch ON ch.order_id = r.order_id AND ch.specialist_id = r.specialist_id
               WHERE r.order_id = ? ORDER BY r.id""",
            (order_id,),
        ):
            out.append({**dict(r), "specialist": specialist_card(db, r["specialist_id"], user["lang"])})
        return out

    @app.post("/orders/{order_id}/responses")
    def respond(order_id: int, body: ResponseIn, user: dict = Depends(current_user),
                db: sqlite3.Connection = Depends(get_db)):
        order = load_order(db, order_id, user["lang"])
        if order["client_id"] == user["id"]:
            raise HTTPException(400, "Нельзя откликнуться на свой заказ")
        if order["status"] != "open":
            raise HTTPException(400, "Заказ уже закрыт")
        if db.execute("SELECT 1 FROM specialist_profiles WHERE user_id = ?", (user["id"],)).fetchone() is None:
            raise HTTPException(400, "Сначала заполните анкету специалиста")
        if subscription_until(db, user["id"]) is None:
            raise HTTPException(402, "Чтобы откликаться на заказы, оформите подписку")
        try:
            response_id = db.execute(
                "INSERT INTO responses (order_id, specialist_id, message, price, created_at) VALUES (?, ?, ?, ?, ?)",
                (order_id, user["id"], body.message, body.price, iso(now())),
            ).lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Вы уже откликнулись на этот заказ")
        chat_id = db.execute(
            "INSERT INTO chats (order_id, client_id, specialist_id, created_at) VALUES (?, ?, ?, ?)",
            (order_id, order["client_id"], user["id"], iso(now())),
        ).lastrowid
        db.execute(
            "INSERT INTO messages (chat_id, sender_id, text, created_at) VALUES (?, ?, ?, ?)",
            (chat_id, user["id"], body.message, iso(now())),
        )
        notify(db, order["client_id"], "Новый отклик", f"{user['name']}: {body.message[:80]}",
               {"order_id": order_id})
        return {"id": response_id, "chat_id": chat_id}

    @app.post("/orders/{order_id}/choose")
    def choose(order_id: int, body: ChooseIn, user: dict = Depends(current_user),
               db: sqlite3.Connection = Depends(get_db)):
        order = own_order(db, order_id, user)
        if order["status"] != "open":
            raise HTTPException(400, "Исполнитель уже выбран или заказ закрыт")
        resp = db.execute(
            "SELECT * FROM responses WHERE id = ? AND order_id = ?", (body.response_id, order_id)
        ).fetchone()
        if resp is None:
            raise HTTPException(404, "Отклик не найден")
        db.execute("UPDATE orders SET status = 'in_progress', specialist_id = ? WHERE id = ?",
                   (resp["specialist_id"], order_id))
        notify(db, resp["specialist_id"], "Вас выбрали исполнителем", order["title"], {"order_id": order_id})
        return load_order(db, order_id, user["lang"])

    @app.post("/orders/{order_id}/complete")
    def complete(order_id: int, user: dict = Depends(current_user),
                 db: sqlite3.Connection = Depends(get_db)):
        order = own_order(db, order_id, user)
        if order["status"] != "in_progress":
            raise HTTPException(400, "Сначала выберите исполнителя")
        db.execute("UPDATE orders SET status = 'completed' WHERE id = ?", (order_id,))
        return load_order(db, order_id, user["lang"])

    @app.post("/orders/{order_id}/close")
    def close(order_id: int, user: dict = Depends(current_user),
              db: sqlite3.Connection = Depends(get_db)):
        order = own_order(db, order_id, user)
        if order["status"] not in ("open", "in_progress"):
            raise HTTPException(400, "Заказ уже завершён")
        db.execute("UPDATE orders SET status = 'closed' WHERE id = ?", (order_id,))
        return load_order(db, order_id, user["lang"])

    @app.post("/orders/{order_id}/review")
    def review(order_id: int, body: ReviewIn, user: dict = Depends(current_user),
               db: sqlite3.Connection = Depends(get_db)):
        order = own_order(db, order_id, user)
        if order["status"] != "completed" or order["specialist_id"] is None:
            raise HTTPException(400, "Отзыв можно оставить после выполнения заказа")
        try:
            db.execute(
                "INSERT INTO reviews (order_id, client_id, specialist_id, rating, text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (order_id, user["id"], order["specialist_id"], body.rating, body.text, iso(now())),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Отзыв уже оставлен")
        return {"ok": True}

    # ---------- chats ----------

    @app.get("/chats")
    def chats(user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
        return [dict(r) for r in db.execute(
            """SELECT ch.id, ch.order_id, o.title AS order_title,
                      CASE WHEN ch.client_id = :uid THEN sp.name ELSE cl.name END AS other_name,
                      CASE WHEN ch.client_id = :uid THEN ch.specialist_id ELSE ch.client_id END AS other_id,
                      m.text AS last_text, m.created_at AS last_at
               FROM chats ch
               JOIN orders o ON o.id = ch.order_id
               JOIN users cl ON cl.id = ch.client_id
               JOIN users sp ON sp.id = ch.specialist_id
               LEFT JOIN messages m ON m.id = (SELECT MAX(id) FROM messages WHERE chat_id = ch.id)
               WHERE ch.client_id = :uid OR ch.specialist_id = :uid
               ORDER BY m.id DESC""",
            {"uid": user["id"]},
        )]

    @app.get("/chats/{chat_id}/messages")
    def chat_messages(chat_id: int, after_id: int = 0, user: dict = Depends(current_user),
                      db: sqlite3.Connection = Depends(get_db)):
        chat_for(db, chat_id, user)
        return [dict(r) for r in db.execute(
            "SELECT * FROM messages WHERE chat_id = ? AND id > ? ORDER BY id LIMIT 500",
            (chat_id, after_id),
        )]

    @app.post("/chats/{chat_id}/messages")
    def send_message(chat_id: int, body: MessageIn, user: dict = Depends(current_user),
                     db: sqlite3.Connection = Depends(get_db)):
        chat = chat_for(db, chat_id, user)
        msg_id = db.execute(
            "INSERT INTO messages (chat_id, sender_id, text, created_at) VALUES (?, ?, ?, ?)",
            (chat_id, user["id"], body.text, iso(now())),
        ).lastrowid
        other = chat["specialist_id"] if user["id"] == chat["client_id"] else chat["client_id"]
        notify(db, other, user["name"] or "Новое сообщение", body.text[:100], {"chat_id": chat_id})
        return dict(db.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone())

    # ---------- subscription ----------

    @app.get("/plans")
    def plans():
        return {
            "currency": settings.currency,
            "plans": [p.model_dump() for p in settings.plans],
            "providers": settings.payment_providers(),
        }

    @app.post("/subscription/checkout")
    def checkout(body: CheckoutIn, user: dict = Depends(current_user),
                 db: sqlite3.Connection = Depends(get_db)):
        plan = settings.plan(body.plan_id)
        if plan is None:
            raise HTTPException(404, "Тариф не найден")
        if body.provider not in settings.payment_providers():
            raise HTTPException(400, "Способ оплаты недоступен")
        payment_id = db.execute(
            "INSERT INTO payments (user_id, plan_id, provider, amount, created_at) VALUES (?, ?, ?, ?, ?)",
            (user["id"], plan.id, body.provider, plan.price, iso(now())),
        ).lastrowid
        if body.provider == "dev":
            db.execute("UPDATE payments SET status = 'succeeded' WHERE id = ?", (payment_id,))
            extend_subscription(db, user["id"], plan.id, plan.days)
            return {"payment_id": payment_id, "status": "succeeded", "confirmation_url": None,
                    "subscription_until": subscription_until(db, user["id"])}
        return {"payment_id": payment_id, "status": "pending",
                "confirmation_url": checkout_url(settings, body.provider, payment_id, plan.price, user["lang"]),
                "subscription_until": subscription_until(db, user["id"])}

    @app.get("/payments/{payment_id}")
    def payment_status(payment_id: int, user: dict = Depends(current_user),
                       db: sqlite3.Connection = Depends(get_db)):
        p = db.execute("SELECT id, status FROM payments WHERE id = ? AND user_id = ?",
                       (payment_id, user["id"])).fetchone()
        if p is None:
            raise HTTPException(404, "Заказ не найден")
        return {**dict(p), "subscription_until": subscription_until(db, user["id"])}

    @app.post("/payments/payme")
    async def payme_endpoint(request: Request):
        try:
            body = json.loads(await request.body())
        except ValueError:
            return {"jsonrpc": "2.0", "id": None, "error": {
                "code": -32700, "message": {"ru": "Ошибка разбора JSON", "uz": "JSON xatosi", "en": "Parse error"}}}
        db = connect(settings.database_path)
        try:
            return handle_payme(settings, db, request.headers.get("authorization", ""), body)
        finally:
            db.close()

    async def click_endpoint(request: Request, action: int) -> dict:
        form = dict(parse_qsl((await request.body()).decode()))
        db = connect(settings.database_path)
        try:
            return handle_click(settings, db, form, action)
        finally:
            db.close()

    @app.post("/payments/click/prepare")
    async def click_prepare(request: Request):
        return await click_endpoint(request, 0)

    @app.post("/payments/click/complete")
    async def click_complete(request: Request):
        return await click_endpoint(request, 1)

    return app
