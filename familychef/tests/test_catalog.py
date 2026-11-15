import csv

from familychef.catalog import DATA_DIR, get_catalog


def test_every_region_has_prices_in_its_own_table():
    cat = get_catalog()
    assert set(cat.regions) == {"RU", "BY", "KZ", "UZ", "DE", "US"}
    for code, region in cat.regions.items():
        assert (DATA_DIR / "prices" / f"{code}.csv").exists()
        assert len(region.prices) >= 35, code
        assert region.cities and region.cities[0].id == "default"


def test_prices_are_local_not_converted():
    """Each region uses its own currency and its own packaging: the same
    product is not one USD price multiplied by an exchange rate."""
    cat = get_catalog()
    currencies = {r.currency for r in cat.regions.values()}
    assert len(currencies) == len(cat.regions)
    us, de, ru = (cat.regions[c] for c in ("US", "DE", "RU"))
    assert us.prices["milk"].pack_size == 3785  # gallon
    assert ru.prices["milk"].pack_size == 930
    assert us.prices["eggs"].pack_size == 12 and ru.prices["eggs"].pack_size == 10
    # Relative prices differ between regions (buckwheat is niche in the US),
    # which would be impossible with a single converted price list.
    def ratio(r):
        b, rice = r.prices["buckwheat"], r.prices["rice"]
        return (b.price / b.pack_size) / (rice.price / rice.pack_size)
    assert ratio(us) > 3 * ratio(ru)


def test_regional_availability():
    cat = get_catalog()
    assert "pork" not in cat.regions["UZ"].prices


def test_price_csv_headers():
    for path in (DATA_DIR / "prices").glob("*.csv"):
        with path.open(encoding="utf-8") as fh:
            assert next(csv.reader(fh)) == ["ingredient", "pack_size", "pack_label", "price"]


def test_recipes_cover_every_slot():
    cat = get_catalog()
    for slot in ("breakfast", "lunch", "dinner"):
        assert sum(slot in r.slots for r in cat.recipes.values()) >= 10
