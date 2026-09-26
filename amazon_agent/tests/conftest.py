import json
from datetime import datetime, timezone

import pytest

from amzagent.config import Settings
from amzagent.models import Product
from amzagent.store import Store


def make_product(asin: str, rating=4.6, reviews=2500, price=29.99, **kw) -> Product:
    fields = dict(
        asin=asin,
        title=f"Product {asin}",
        url=f"https://www.amazon.com/dp/{asin}?tag=test-20",
        image_url=f"https://m.media-amazon.com/images/I/{asin}.jpg",
        brand="Acme",
        features=["Long battery", "Waterproof"],
        price=price,
        price_display=f"${price}" if price else "",
        rating=rating,
        review_count=reviews,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    fields.update(kw)
    return Product(**fields)


class FakeCatalog:
    def __init__(self, products):
        self.products = products
        self.calls = []

    def search(self, keywords, search_index="All", max_price=None, page=1):
        self.calls.append((keywords, page))
        return self.products if page == 1 else []

    def get(self, asins):
        return [p for p in self.products if p.asin in asins]


class FakeLLM:
    def __init__(self):
        self.prompts = []

    def generate(self, system, prompt, max_tokens=2048):
        self.prompts.append(prompt)
        if "site_title" in prompt:
            return json.dumps({"site_title": "Sound Picks", "tagline": "Top rated audio",
                               "intro": "We pick well-rated products."})
        asins = [line.split("asin: ")[1] for line in prompt.splitlines() if "asin: " in line]
        return "```json\n" + json.dumps([
            {"asin": a, "summary": f"Good {a}.", "pros": ["Long battery"], "cons": ["Check size"],
             "push_title": "A title that is definitely longer than thirty chars",
             "push_text": "Body"} for a in asins
        ]) + "\n```"


class FakePush:
    def __init__(self):
        self.created, self.started, self.stopped, self.excluded = [], [], [], []
        self.spend_rows, self.zone_rows = [], []
        self._next = 1000

    def create_campaign(self, payload):
        self._next += 1
        self.created.append(payload)
        return str(self._next)

    def start(self, ids):
        self.started.extend(ids)

    def stop(self, ids):
        self.stopped.extend(ids)

    def exclude_zones(self, campaign_id, zones):
        self.excluded.append((campaign_id, zones))

    def spend(self, campaign_ids, days=30, by_zone=False):
        return self.zone_rows if by_zone else self.spend_rows


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        data_dir=str(tmp_path),
        domain="example.com",
        min_rating=4.0,
        min_reviews=100,
        products_per_site=5,
        auto_niches=0,
        campaigns_per_site=2,
        campaign_daily_budget=10,
        max_daily_spend=30,
        agent_interval_hours=0,
        session_https_only=False,
        secret_key="test",
    )


@pytest.fixture
def store(settings):
    return Store(settings.data_dir)
