from amzagent.propeller_check import flatten, resolve

SPEC = {
    "components": {"schemas": {
        "Rate": {"type": "object", "required": ["amount"],
                 "properties": {"amount": {"type": "number"},
                                "countries": {"type": "array", "items": {"type": "string"}}}},
        "Create": {"type": "object", "required": ["name", "direction"], "properties": {
            "name": {"type": "string", "maxLength": 100},
            "direction": {"type": "string", "enum": ["onclick", "nativeads"]},
            "rates": {"type": "array", "items": {"$ref": "#/components/schemas/Rate"}},
            "targeting": {"type": "object", "properties": {
                "country": {"type": "object", "properties": {
                    "list": {"type": "array", "items": {"type": "string"}}}}}},
        }},
    }},
}


def test_flatten_resolves_refs_and_nesting():
    lines = flatten(resolve(SPEC, {"$ref": "#/components/schemas/Create"}))
    assert "name: string REQUIRED maxLength=100" in lines
    assert "direction: string REQUIRED enum=onclick,nativeads" in lines
    assert "rates[].amount: number REQUIRED" in lines
    assert "targeting.country.list: array" in lines


def test_resolve_follows_refs_into_other_files(monkeypatch):
    import amzagent.propeller_check as pc

    files = {
        "https://x/v5/docs/adv/paths/campaigns.json":
            {"post": {"requestBody": {"content": {"application/json": {
                "schema": {"$ref": "../schemas/create.json#/Create"}}}}}},
        "https://x/v5/docs/adv/schemas/create.json":
            {"Create": {"type": "object", "required": ["name"],
                        "properties": {"name": {"type": "string"}}}},
    }
    monkeypatch.setattr(pc, "_load_doc", lambda url: files[url])
    spec = {"__url__": "https://x/v5/docs/adv/openapi.json",
            "paths": {"/adv/campaigns": {"$ref": "paths/campaigns.json"}}}
    item = resolve(spec, spec["paths"]["/adv/campaigns"])
    schema = item["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert flatten(schema) == ["name: string REQUIRED"]
