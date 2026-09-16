from fbadsagent.web.account_store import AccountStore


def test_seeds_from_env_on_first_use(tmp_path):
    store = AccountStore(
        tmp_path / "accounts.json",
        seed_access_token="seed-token",
        seed_account_ids=["act_111", "act_222"],
    )
    assert store.get_access_token() == "seed-token"
    ids = [a.id for a in store.list_accounts()]
    assert ids == ["act_111", "act_222"]


def test_does_not_reseed_existing_file(tmp_path):
    path = tmp_path / "accounts.json"
    AccountStore(path, seed_access_token="first", seed_account_ids=["act_111"])
    store2 = AccountStore(path, seed_access_token="second", seed_account_ids=["act_999"])
    assert store2.get_access_token() == "first"
    assert [a.id for a in store2.list_accounts()] == ["act_111"]


def test_set_access_token(tmp_path):
    store = AccountStore(tmp_path / "accounts.json")
    store.set_access_token("  new-token  ")
    assert store.get_access_token() == "new-token"


def test_add_account_normalizes_id_and_dedupes(tmp_path):
    store = AccountStore(tmp_path / "accounts.json")
    store.add_account("1234567890", name="My Store")
    store.add_account("act_1234567890", name="Duplicate")

    accounts = store.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].id == "act_1234567890"
    assert accounts[0].name == "My Store"


def test_remove_account(tmp_path):
    store = AccountStore(tmp_path / "accounts.json", seed_account_ids=["act_111", "act_222"])
    store.remove_account("act_111")
    assert [a.id for a in store.list_accounts()] == ["act_222"]
