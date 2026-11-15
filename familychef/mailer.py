"""Sending sign-in codes by email.

FC_EMAIL_PROVIDER:
  (empty)  email sign-in is off
  resend   Resend.com HTTP API: RESEND_API_KEY, FC_EMAIL_FROM ("FamilyChef <login@familychef.pro>")
  smtp     any SMTP server: SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, FC_EMAIL_FROM
  dev      no email is sent; the code is logged and shown on screen. FOR TESTING ONLY:
           anyone could sign in to any address.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

logger = logging.getLogger("familychef.mail")

SUBJECT = {"ru": "Код для входа в FamilyChef: {code}", "en": "Your FamilyChef sign-in code: {code}"}
BODY = {
    "ru": "Ваш код для входа в FamilyChef: {code}\n\nКод действует 10 минут. Если вы не запрашивали вход, "
          "просто проигнорируйте это письмо.",
    "en": "Your FamilyChef sign-in code is {code}\n\nThe code is valid for 10 minutes. If you did not try "
          "to sign in, you can ignore this email.",
}


class MailError(RuntimeError):
    pass


def provider() -> str:
    return os.environ.get("FC_EMAIL_PROVIDER", "").strip().lower()


def enabled() -> bool:
    return provider() in ("resend", "smtp", "dev")


def is_dev() -> bool:
    return provider() == "dev"


def send_code(to: str, code: str, lang: str = "en") -> None:
    lang = "ru" if lang == "ru" else "en"
    subject, body = SUBJECT[lang].format(code=code), BODY[lang].format(code=code)
    sender = os.environ.get("FC_EMAIL_FROM", "FamilyChef <login@familychef.pro>")
    kind = provider()
    if kind == "dev":
        logger.warning("TEST MODE: sign-in code for %s is %s (no email sent)", to, code)
        return
    if kind == "resend":
        key = os.environ.get("RESEND_API_KEY", "")
        if not key:
            raise MailError("RESEND_API_KEY is not set")
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps({"from": sender, "to": [to], "subject": subject, "text": body}).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            raise MailError(f"Resend HTTP {exc.code}: {exc.read().decode(errors='replace')[:200]}") from exc
        except urllib.error.URLError as exc:
            raise MailError(f"Resend: {exc.reason}") from exc
        return
    if kind == "smtp":
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = sender, to, subject
        msg.set_content(body)
        host, port = os.environ.get("SMTP_HOST", ""), int(os.environ.get("SMTP_PORT", "587"))
        if not host:
            raise MailError("SMTP_HOST is not set")
        try:
            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(host, port, timeout=15)
                server.starttls(context=ssl.create_default_context())
            with server:
                if os.environ.get("SMTP_USER"):
                    server.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
                server.send_message(msg)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailError(f"SMTP: {exc}") from exc
        return
    raise MailError("email sign-in is not configured")
