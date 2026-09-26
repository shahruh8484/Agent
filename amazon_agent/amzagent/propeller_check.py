"""Read-only PropellerAds check, run on the server before PUSH_LIVE=true:

    docker compose exec app python -m amzagent.propeller_check

1. Calls a couple of read-only endpoints with PROPELLER_API_TOKEN (balance,
   campaign list) to prove the token works.
2. Downloads PropellerAds' own OpenAPI spec and prints the endpoints and
   the campaign-creation request schema, so the payload built in
   amzagent/push/propeller.py can be checked against the real API.

Creates nothing and spends nothing. Never prints the token.
"""
from __future__ import annotations

import json
import re
import sys

import requests

from amzagent.config import get_settings
from amzagent.push import propeller
from amzagent.push.propeller import PropellerClient, PropellerError

SPEC_CANDIDATES = [
    "https://ssp-api.propellerads.com/v5/docs/swagger.json",
    "https://ssp-api.propellerads.com/v5/docs/openapi.json",
    "https://ssp-api.propellerads.com/v5/swagger.json",
    "https://ssp-api.propellerads.com/v5/openapi.json",
    "https://ssp-api.propellerads.com/v5/docs/?format=openapi",
    "https://ssp-api.propellerads.com/v5/docs/api-docs.json",
]
DOCS_PAGE = "https://ssp-api.propellerads.com/v5/docs/"


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def short(value, limit: int = 600) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + " …"


def check_token(client: PropellerClient) -> bool:
    section("1. Token")
    ok = False
    for label, path, params in (
        ("balance", propeller.BALANCE_PATH, None),
        ("campaigns", propeller.CAMPAIGNS_PATH, {"page": 1, "page_size": 3}),
    ):
        try:
            data = client._request("GET", path, params=params)
        except PropellerError as exc:
            print(f"{label}: FAILED — {exc}")
            continue
        ok = True
        if isinstance(data, dict) and isinstance(data.get("result"), list):
            data = {**data, "result": data["result"][:2]}
        print(f"{label}: OK — {short(data)}")
    return ok


INITIALIZER = "https://ssp-api.propellerads.com/v5/docs/adv/swagger-initializer.js"
SPEC_REF_RE = re.compile(r"""["'`]([^"'`\s]+\.(?:json|ya?ml)(?:\?[^"'`\s]*)?)["'`]""")


def yaml_error() -> type[Exception]:
    try:
        import yaml

        return yaml.YAMLError
    except ImportError:
        return ValueError


def _load_spec(url: str) -> dict | None:
    try:
        resp = requests.get(url, timeout=30)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    text = resp.text
    try:
        spec = json.loads(text)
    except ValueError:
        try:
            import yaml  # PyYAML ships with uvicorn[standard]

            spec = yaml.safe_load(text)
        except (ImportError, yaml_error()):
            return None
    return spec if isinstance(spec, dict) and "paths" in spec else None


def find_spec() -> dict | None:
    urls: list[str] = []
    # The docs page loads adv/swagger-initializer.js, which names the spec.
    try:
        js = requests.get(INITIALIZER, timeout=20).text
        print("initializer:", re.sub(r"\s+", " ", js)[:700])
        urls += [requests.compat.urljoin(INITIALIZER, ref) for ref in SPEC_REF_RE.findall(js)]
        for ref in SPEC_REF_RE.findall(js):
            urls.append(requests.compat.urljoin(DOCS_PAGE, ref))
    except requests.RequestException as exc:
        print(f"initializer: {exc}")
    urls += SPEC_CANDIDATES + [
        "https://ssp-api.propellerads.com/v5/docs/adv/swagger.json",
        "https://ssp-api.propellerads.com/v5/docs/adv/openapi.json",
        "https://ssp-api.propellerads.com/v5/docs/adv/swagger.yaml",
        "https://ssp-api.propellerads.com/v5/docs/adv/openapi.yaml",
    ]
    for url in dict.fromkeys(urls):
        spec = _load_spec(url)
        if spec:
            print(f"spec: {url}")
            return spec
    print("tried:", *dict.fromkeys(urls), sep="\n  ")
    return None


def resolve(spec: dict, node, depth: int = 0):
    if depth > 6 or not isinstance(node, dict):
        return node
    if "$ref" in node:
        target = spec
        for part in node["$ref"].lstrip("#/").split("/"):
            target = target.get(part, {})
        return resolve(spec, target, depth + 1)
    out = {}
    for key, value in node.items():
        if key in ("description", "example", "examples", "title"):
            continue
        if isinstance(value, dict):
            out[key] = resolve(spec, value, depth + 1)
        elif isinstance(value, list):
            out[key] = [resolve(spec, v, depth + 1) for v in value]
        else:
            out[key] = value
    return out


def flatten(schema: dict, prefix: str = "", depth: int = 0) -> list[str]:
    """One line per field: name  type  [required]  enum/format."""
    lines: list[str] = []
    if depth > 5 or not isinstance(schema, dict):
        return lines
    for variant in schema.get("allOf", []):
        lines += flatten(variant, prefix, depth + 1)
    required = set(schema.get("required", []))
    for name, prop in (schema.get("properties") or {}).items():
        full = f"{prefix}{name}"
        kind = prop.get("type", "object" if "properties" in prop else "?")
        extra = []
        if name in required:
            extra.append("REQUIRED")
        if "enum" in prop:
            extra.append("enum=" + ",".join(map(str, prop["enum"]))[:160])
        for key in ("format", "minimum", "maximum", "maxLength", "pattern"):
            if key in prop:
                extra.append(f"{key}={prop[key]}")
        lines.append(f"{full}: {kind} {' '.join(extra)}".rstrip())
        if kind == "object":
            lines += flatten(prop, full + ".", depth + 1)
        elif kind == "array" and isinstance(prop.get("items"), dict):
            items = prop["items"]
            if "properties" in items:
                lines += flatten(items, full + "[].", depth + 1)
            elif "enum" in items:
                lines.append(f"{full}[]: enum=" + ",".join(map(str, items["enum"]))[:160])
    return lines


def print_spec(spec: dict) -> None:
    section("2. Endpoints (adv)")
    for path, ops in sorted(spec.get("paths", {}).items()):
        if "/adv" not in path and "campaign" not in path:
            continue
        print(" ", ", ".join(m.upper() for m in ops if m in ("get", "post", "put", "patch",
                                                             "delete")), path)
    section("3. Create-campaign request schema")
    for path, ops in spec.get("paths", {}).items():
        if not path.rstrip("/").endswith("/campaigns") or "post" not in ops:
            continue
        op = ops["post"]
        body = op.get("requestBody", {}).get("content", {})
        schema = next(iter(body.values()), {}).get("schema") if body else None
        if schema is None:  # swagger 2.0
            schema = next((p.get("schema") for p in op.get("parameters", [])
                           if p.get("in") == "body"), None)
        print(f"POST {path}")
        for line in flatten(resolve(spec, schema or {})):
            print(" ", line)


def probe_validation(client: PropellerClient) -> None:
    """POST deliberately incomplete bodies: the API rejects them and its
    validation errors name the required fields and allowed values. Nothing
    can be created because the bodies are invalid."""
    section("4. Validation probe (creates nothing)")
    for label, body in (
        ("empty body", {}),
        ("push direction only", {"direction": propeller.PUSH_DIRECTION}),
    ):
        try:
            resp = client._session.request(
                "POST", client._base + propeller.CAMPAIGNS_PATH, json=body, timeout=30
            )
        except requests.RequestException as exc:
            print(f"{label}: request failed — {exc}")
            continue
        if resp.status_code < 400:
            # Should never happen with these bodies; say so loudly.
            print(f"{label}: UNEXPECTED HTTP {resp.status_code} — {resp.text[:800]}")
            continue
        print(f"{label}: HTTP {resp.status_code}")
        print(resp.text[:4000])


def docs_references() -> None:
    section("5. Docs page references")
    try:
        page = requests.get(DOCS_PAGE, timeout=20)
    except requests.RequestException as exc:
        print(f"docs page: {exc}")
        return
    print(f"HTTP {page.status_code}, {len(page.text)} bytes")
    refs = re.findall(r"""(?:src|href|url)\s*[=:]\s*["']([^"']+)["']""", page.text)
    for ref in dict.fromkeys(refs):
        print(" ", ref)


def main() -> int:
    settings = get_settings()
    try:
        client = PropellerClient(settings.propeller_api_token)
    except PropellerError as exc:
        print(f"PROPELLER_API_TOKEN: {exc}")
        return 1
    token_ok = check_token(client)
    spec = find_spec()
    if spec:
        print_spec(spec)
    else:
        section("2. Spec")
        print("Could not download the OpenAPI spec automatically.")
        docs_references()
    if token_ok:
        probe_validation(client)
    section("Result")
    print("token works" if token_ok else "token check FAILED (see above)")
    return 0 if token_ok else 1


if __name__ == "__main__":
    sys.exit(main())
