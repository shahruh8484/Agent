from fbadsagent.web.cpa_store import CpaNetworkStore


def test_empty_store_has_no_networks(tmp_path):
    store = CpaNetworkStore(tmp_path / "cpa.json")
    assert store.list_networks() == []


def test_add_network(tmp_path):
    store = CpaNetworkStore(tmp_path / "cpa.json")
    store.add_network("traff-hub", base_url="https://traff-hub.com/api", api_key="secret123")

    networks = store.list_networks()
    assert len(networks) == 1
    assert networks[0].name == "traff-hub"
    assert networks[0].base_url == "https://traff-hub.com/api"
    assert networks[0].api_key == "secret123"


def test_add_network_upserts_by_name(tmp_path):
    store = CpaNetworkStore(tmp_path / "cpa.json")
    store.add_network("traff-hub", api_key="old-key")
    store.add_network("traff-hub", api_key="new-key")

    networks = store.list_networks()
    assert len(networks) == 1
    assert networks[0].api_key == "new-key"


def test_remove_network(tmp_path):
    store = CpaNetworkStore(tmp_path / "cpa.json")
    store.add_network("traff-hub")
    store.add_network("other-network")
    store.remove_network("traff-hub")

    names = [n.name for n in store.list_networks()]
    assert names == ["other-network"]
