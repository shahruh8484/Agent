from fbadsagent.models import AgentProduct, AgentRunResult
from fbadsagent.web.product_store import AgentRunLogStore, ProductStore


def make_product(product_id="p1") -> AgentProduct:
    return AgentProduct(
        id=product_id,
        name="Wireless Earbuds Pro",
        description="Noise-cancelling earbuds",
        price=49.99,
        daily_budget=25.0,
        keywords=["earbuds"],
        fb_ad_account_id="act_123",
        cpa_network="traff-hub",
        campaign_hash="abc123",
    )


def test_empty_product_store(tmp_path):
    store = ProductStore(tmp_path / "products.json")
    assert store.list_products() == []


def test_add_and_get_product(tmp_path):
    store = ProductStore(tmp_path / "products.json")
    store.add_product(make_product())

    products = store.list_products()
    assert len(products) == 1
    assert store.get_product("p1").name == "Wireless Earbuds Pro"
    assert store.get_product("missing") is None


def test_add_product_upserts_by_id(tmp_path):
    store = ProductStore(tmp_path / "products.json")
    store.add_product(make_product())
    updated = make_product()
    updated.daily_budget = 99.0
    store.add_product(updated)

    products = store.list_products()
    assert len(products) == 1
    assert products[0].daily_budget == 99.0


def test_remove_product(tmp_path):
    store = ProductStore(tmp_path / "products.json")
    store.add_product(make_product("a"))
    store.add_product(make_product("b"))
    store.remove_product("a")

    assert [p.id for p in store.list_products()] == ["b"]


def make_run(product_id="p1", status="success") -> AgentRunResult:
    return AgentRunResult(
        product_id=product_id,
        product_name="Wireless Earbuds Pro",
        created_at="2026-09-17 12:00 UTC",
        status=status,
        message="ok",
    )


def test_empty_run_log(tmp_path):
    store = AgentRunLogStore(tmp_path / "runs.json")
    assert store.list_runs() == []


def test_run_log_newest_first(tmp_path):
    store = AgentRunLogStore(tmp_path / "runs.json")
    store.append(make_run("p1"))
    store.append(make_run("p2"))

    runs = store.list_runs()
    assert [r.product_id for r in runs] == ["p2", "p1"]


def test_run_log_trims_to_max_entries(tmp_path):
    store = AgentRunLogStore(tmp_path / "runs.json", max_entries=3)
    for i in range(5):
        store.append(make_run(f"p{i}"))

    runs = store.list_runs()
    assert len(runs) == 3
    assert [r.product_id for r in runs] == ["p4", "p3", "p2"]
