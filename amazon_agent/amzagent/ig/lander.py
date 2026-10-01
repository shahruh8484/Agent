"""Lander copy for an iGaming project, written by the LLM within the rules
regulated markets set for gambling ads: 18+ only, no promises of winning or
income, no "investment" framing, nothing aimed at minors, a responsible
gambling notice on every page."""
from __future__ import annotations

import json
import re

from amzagent.content.llm import LLM, LLMError, parse_json

LANGUAGES = {"pt": "Português (Brasil)", "es": "Español", "en": "English"}
COUNTRIES = {"BR": "Бразилия", "MX": "Мексика", "PE": "Перу", "CL": "Чили",
             "CO": "Колумбия", "NG": "Нигерия"}
COUNTRY_LANGUAGE = {"BR": "pt", "MX": "es", "PE": "es", "CL": "es", "CO": "es", "NG": "en"}

# Fixed texts on every lander, per language. Not written by the LLM.
SAFETY = {
    "pt": {"age": "Somente para maiores de 18 anos.",
           "responsible": "Jogue com responsabilidade. Apostas envolvem risco de perda e não "
                          "são uma forma de ganhar dinheiro. Defina limites de tempo e valor; o operador "
                          "oferece ferramentas de limite de depósito e autoexclusão.",
           "ad": "Conteúdo publicitário: recebemos comissão se você se cadastrar pelo nosso link.",
           "operator": "Operador licenciado", "help": "Precisa de ajuda com o jogo?",
           "support": "Termos do bônus, suporte ao cliente e ferramentas de jogo responsável: "
                      "no site do operador.",
           "gate_q": "Você tem 18 anos ou mais?", "gate_yes": "Sim, tenho 18+",
           "gate_no": "Não", "gate_bye": "Este site é apenas para maiores de 18 anos.",
           "faq": "Perguntas frequentes", "how": "Como começar"},
    "es": {"age": "Solo para mayores de 18 años.",
           "responsible": "Juega con responsabilidad. Las apuestas implican riesgo de pérdida y "
                          "no son una forma de ganar dinero. Pon límites de tiempo y dinero; el operador "
                          "ofrece herramientas de límite de depósito y autoexclusión.",
           "ad": "Contenido publicitario: recibimos una comisión si te registras con nuestro "
                 "enlace.",
           "operator": "Operador con licencia", "help": "¿Necesitas ayuda con el juego?",
           "support": "Términos del bono, atención al cliente y herramientas de juego "
                      "responsable: en el sitio del operador.",
           "gate_q": "¿Tienes 18 años o más?", "gate_yes": "Sí, tengo 18+", "gate_no": "No",
           "gate_bye": "Este sitio es solo para mayores de 18 años.",
           "faq": "Preguntas frecuentes", "how": "Cómo empezar"},
    "en": {"age": "18+ only.",
           "responsible": "Gamble responsibly. Betting carries a risk of loss and is not a way "
                          "to make money. Set time and spending limits; the operator offers deposit limit and "
                          "self-exclusion tools.",
           "ad": "Advertising: we earn a commission if you sign up through our link.",
           "operator": "Licensed operator", "help": "Need help with gambling?",
           "support": "Bonus terms, customer support and responsible gambling tools: on the "
                      "operator's site.",
           "gate_q": "Are you 18 or older?", "gate_yes": "Yes, I'm 18+", "gate_no": "No",
           "gate_bye": "This site is for adults 18+ only.",
           "faq": "Frequently asked questions", "how": "How to start"},
}
# Trust badges under the hero: facts true of every licensed operator in the
# country, not marketing claims. (icon, title, text); "pix" only for Brazil.
BADGES = {
    "pt": [("🛡️", "Operador licenciado", "Autorizado pelo Ministério da Fazenda (SPA/MF), site .bet.br"),
           ("⚡", "Depósito e saque via PIX", "Pagamentos em reais, direto pela sua conta", "pix"),
           ("🎯", "Jogo responsável", "Limites de depósito, pausas e autoexclusão")],
    "es": [("🛡️", "Operador con licencia", "Autorizado por el regulador del país"),
           ("🎯", "Juego responsable", "Límites de depósito, pausas y autoexclusión")],
    "en": [("🛡️", "Licensed operator", "Authorised by the country's regulator"),
           ("🎯", "Responsible gambling", "Deposit limits, breaks and self-exclusion")],
}


def badges_for(language: str, country: str) -> list[tuple[str, str, str]]:
    out = []
    for b in BADGES.get(language, BADGES["en"]):
        if len(b) > 3 and b[3] == "pix" and country != "BR":
            continue
        out.append(b[:3])
    return out


HELP_URL = "https://www.gamblingtherapy.org/"

# Phrases regulators and ad networks reject in gambling ads (checked in the
# panel, lower-case substrings).
FORBIDDEN = {
    "pt": ("garantid", "ganhar dinheiro", "ganhe dinheiro", "renda extra", "investimento",
           "invista", "lucro", "fique rico", "sem risco", "dinheiro fácil", "vitória certa"),
    "es": ("garantiz", "ganar dinero", "gana dinero", "ingreso extra", "inversión", "invierte",
           "ganancia segura", "hazte rico", "sin riesgo", "dinero fácil"),
    "en": ("guarantee", "make money", "extra income", "investment", "invest ", "get rich",
           "risk-free", "risk free", "easy money", "sure win"),
}

# Copy that speaks as the operator ("our site", "we offer"): the lander is
# the affiliate's, and the advertiser must be clearly identified.
FIRST_PERSON = {
    "pt": ("nosso", "nossa", "oferecemos", "temos ", "nosso site"),
    "es": ("nuestro", "nuestra", "ofrecemos", "tenemos "),
    "en": (" our ", "we offer", "we have "),
}

FIELDS = ("title", "headline", "intro", "bonus_title", "bonus_text", "cta")

SYSTEM = ("You write landing page copy for a licensed betting operator's affiliate. The copy "
          "must follow gambling advertising rules and be honest with the reader.")


def empty_lander() -> dict:
    return {**{k: "" for k in FIELDS}, "steps": [], "faq": []}


def load_lander(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    out = empty_lander()
    if isinstance(data, dict):
        for k in FIELDS:
            out[k] = str(data.get(k) or "")
        out["steps"] = [str(x) for x in data.get("steps") or [] if str(x).strip()][:6]
        out["faq"] = [{"q": str(f.get("q", "")), "a": str(f.get("a", ""))}
                      for f in data.get("faq") or [] if isinstance(f, dict) and f.get("q")][:8]
    return out


def lander_text(lander: dict) -> str:
    parts = [lander.get(k, "") for k in FIELDS] + list(lander.get("steps") or [])
    for f in lander.get("faq") or []:
        parts += [f.get("q", ""), f.get("a", "")]
    return "\n".join(parts)


def compliance_issues(text: str, language: str) -> list[str]:
    """Forbidden phrases found in the text (for the owner to fix)."""
    low = (text or "").lower()
    low = f" {low} "
    words = FORBIDDEN.get(language, ()) + FORBIDDEN["en"] + FIRST_PERSON.get(language, ())
    found = [w.strip() for w in words if w in low]
    return sorted(set(found))


def write_lander(llm: LLM, project: dict) -> dict:
    language = project.get("language") or "pt"
    prompt = (
        "TASK: write a betting lander.\n"
        f"Language: {LANGUAGES.get(language, language)}\n"
        f"Country: {project.get('country')}\n"
        f"Operator (licensed): {project.get('brand') or project.get('name')}\n"
        f"Offer details from the owner (the only facts you may use):\n"
        f"{(project.get('offer') or '').strip()[:2000]}\n\n"
        "Rules (gambling advertising law and honesty):\n"
        "- adults only; never address or appeal to minors; no cartoons, school or youth themes;\n"
        "- never promise or suggest winning, profit, income or getting rich; betting is "
        "entertainment with a risk of loss, never an investment or a solution to money problems;\n"
        "- no 'guaranteed', 'risk-free', 'sure win', no fake urgency or countdowns, no fake "
        "reviews, no celebrities;\n"
        "- use only bonus terms given above; if none are given, say the bonus terms are on "
        "the operator's site; mention that bonus wagering conditions apply;\n"
        "- you are an independent affiliate, NOT the operator: never write in the first person "
        "as the operator (no 'we offer', 'our site', 'our bonus'); name the operator and say "
        "'on the operator's site' for terms, support and tools;\n"
        "- the first signup step is always to click the button on this page (never tell the "
        "reader to type the operator's address);\n"
        "- calm, factual tone; short sentences.\n"
        "Fields: title (browser title, max 60 chars), headline (max 70), intro (2-3 sentences), "
        "bonus_title (max 50), bonus_text (1-2 sentences), steps (3-4 short signup steps), "
        "faq (3-4 items: q, a — include one about deposit/withdrawal and one about limits and "
        "responsible play), cta (button text, max 30).\n"
        'Return JSON: {"title": "...", "headline": "...", "intro": "...", "bonus_title": "...", '
        '"bonus_text": "...", "steps": ["..."], "faq": [{"q": "...", "a": "..."}], "cta": "..."}'
    )
    data = parse_json(llm.generate(SYSTEM, prompt, max_tokens=2000))
    if not isinstance(data, dict) or not data.get("headline"):
        raise LLMError("Expected a JSON object with the lander copy")
    lander = load_lander(json.dumps(data))
    for k, limit in (("title", 80), ("headline", 100), ("bonus_title", 80), ("cta", 40)):
        lander[k] = lander[k][:limit]
    return lander


def lines(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"\r?\n", text or "") if x.strip()]
