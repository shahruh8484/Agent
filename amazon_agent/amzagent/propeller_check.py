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
            spec["__url__"] = url
            return spec
    print("tried:", *dict.fromkeys(urls), sep="\n  ")
    return None


_docs: dict[str, dict] = {}


def _load_doc(url: str) -> dict:
    """Another file of a multi-file spec (JSON or YAML), cached."""
    if url not in _docs:
        _docs[url] = {}
        try:
            text = requests.get(url, timeout=30).text
            try:
                _docs[url] = json.loads(text)
            except ValueError:
                import yaml

                _docs[url] = yaml.safe_load(text) or {}
        except (requests.RequestException, ImportError, yaml_error()) as exc:
            print(f"ref {url}: {exc}")
    return _docs[url]


def _pointer(doc, pointer: str):
    for part in [p for p in pointer.lstrip("/").split("/") if p]:
        part = part.replace("~1", "/").replace("~0", "~")
        doc = doc.get(part, {}) if isinstance(doc, dict) else {}
    return doc


def resolve(spec: dict, node, depth: int = 0, base: str | None = None):
    """Inline $refs, including ones into other files of a multi-file spec,
    and drop prose keys."""
    base = base or spec.get("__url__", "")
    if depth > 8 or not isinstance(node, dict):
        return node
    if "$ref" in node:
        ref = node["$ref"]
        file_part, _, pointer = ref.partition("#")
        if file_part:
            url = requests.compat.urljoin(base, file_part)
            doc = _load_doc(url)
            return resolve(doc, _pointer(doc, pointer), depth + 1, url)
        return resolve(spec, _pointer(spec, pointer), depth + 1, base)
    out = {}
    for key, value in node.items():
        if key in ("title", "__url__"):
            continue
        if isinstance(value, dict):
            out[key] = resolve(spec, value, depth + 1, base)
        elif isinstance(value, list):
            out[key] = [resolve(spec, v, depth + 1, base) for v in value]
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
        for key in ("format", "minimum", "maximum", "maxLength", "pattern", "default",
                    "example"):
            if key in prop:
                extra.append(f"{key}={prop[key]}")
        if prop.get("description"):
            extra.append("— " + " ".join(str(prop["description"]).split())[:140])
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


METHODS = ("get", "post", "put", "patch", "delete")


def print_spec(spec: dict) -> None:
    section("2. Endpoints (adv)")
    items = {}
    for path, item in spec.get("paths", {}).items():
        if "/adv" not in path and "campaign" not in path:
            continue
        item = resolve(spec, item) if isinstance(item, dict) else {}
        items[path] = item
        print(" ", ",".join(m.upper() for m in item if m in METHODS) or "?", path)

    section("3. Create-campaign request schema")
    found = False
    for path, item in items.items():
        if not path.rstrip("/").endswith("/campaigns") or "post" not in item:
            continue
        found = True
        op = item["post"]
        body = op.get("requestBody", {}).get("content", {})
        schema = next(iter(body.values()), {}).get("schema") if body else None
        if schema is None:  # swagger 2.0
            schema = next((p.get("schema") for p in op.get("parameters", [])
                           if isinstance(p, dict) and p.get("in") == "body"), None)
        print(f"POST {path}")
        lines = flatten(schema or {})
        for line in lines:
            print(" ", line)
        # Nested structures the flat list can't show (oneOf, free-form
        # objects, arrays of refs): print them raw.
        props = (schema or {}).get("properties") or {}
        for name in ("targeting", "rates", "creatives", "audience"):
            if name in props:
                print(f"\n  {name} (raw):")
                print("   ", json.dumps(props[name], ensure_ascii=False)[:2500])
        if not lines:  # unexpected layout: show it raw rather than nothing
            print(json.dumps(op, ensure_ascii=False)[:6000])
    if not found:
        path_item = spec.get("paths", {}).get("/adv/campaigns") or spec.get("paths", {}).get(
            "/adv/campaigns/")
        print("no POST /adv/campaigns found; raw path item:")
        print(json.dumps(path_item, ensure_ascii=False)[:3000])


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
