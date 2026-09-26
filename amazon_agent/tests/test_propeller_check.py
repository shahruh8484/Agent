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
