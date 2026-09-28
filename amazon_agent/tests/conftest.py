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
        if prompt.startswith("TASK: group products"):
            asins = [line.split(" | ")[0] for line in prompt.splitlines() if " | " in line
                     and not line.startswith("Products")]
            half = max(2, len(asins) // 2)
            return json.dumps([{"name": "Earbuds", "asins": asins[:half]},
                               {"name": "Chargers", "asins": asins[half:]}])
        if prompt.startswith("TASK: write a head-to-head"):
            return json.dumps({"intro": "Two good ones.", "rows": [
                {"aspect": "Battery", "a": "Long", "b": "not listed"}],
                "choose_a": "Choose the first for battery.", "choose_b": "Choose the second.",
                "verdict": "Both fine."})
        if prompt.startswith("TASK: write a gift list"):
            asins = [line.split("asin: ")[1] for line in prompt.splitlines() if "asin: " in line]
            return json.dumps({"intro": "Great gifts.",
                               "items": [{"asin": a, "blurb": f"A gift: {a}."} for a in asins]})
        if prompt.startswith("TASK: write an advice article"):
            return json.dumps({"title": "How to Choose Earbuds", "summary": "What matters.",
                               "parts": [{"heading": "Fit", "paragraphs": ["Try tips."]},
                                         {"heading": "Battery", "paragraphs": ["Check hours."]}]})
        if prompt.startswith("TASK: write a buying guide"):
            asins = [line.split("asin: ")[1] for line in prompt.splitlines() if "asin: " in line]
            return json.dumps({
                "guide_title": "Best Earbuds: Our Picks Compared", "intro": "A short guide.",
                "how_to_choose": ["Check fit", "Check battery"],
                "picks": [{"asin": a, "best_for": "Best for commuting", "blurb": f"About {a}."}
                          for a in asins],
                "verdict": "Pick the first one.",
                "faq": [{"q": "How loud are they?", "a": "Check the noise rating."}],
                "who_for": "Anyone who commutes.", "care_tips": ["Clean the tips weekly."]})
        if "site_title" in prompt:
            return json.dumps({"site_title": "Sound Picks", "tagline": "Top rated audio",
                               "intro": "We pick well-rated products."})
        asins = [line.split("asin: ")[1] for line in prompt.splitlines() if "asin: " in line]
        return "```json\n" + json.dumps([
            {"asin": a, "summary": f"Good {a}.", "pros": ["Long battery"], "cons": ["Check size"],
             "push_title": "A title that is definitely longer than thirty chars",
             "push_text": "Body", "product_type": "earbuds",
             "buying_tips": ["Check fit", "Check battery life"]} for a in asins
        ]) + "\n```"


class FakePush:
    def __init__(self):
        self.created, self.started, self.stopped, self.excluded = [], [], [], []
        self.spend_rows, self.zone_rows = [], []
        self.statuses: dict[str, int] = {}
        self.replaced = []
        self.url_updates = []
        self._next = 1000

    def create_campaign(self, payload):
        for c in payload["creatives"]:  # images must arrive inline, not as URLs
            assert c["image"].startswith("data:image/jpeg;base64,")
        self._next += 1
        self.created.append(payload)
        return str(self._next)

    def start(self, ids):
        self.started.extend(ids)

    def stop(self, ids):
        self.stopped.extend(ids)

    def exclude_zones(self, campaign_id, zones):
        self.excluded.append((campaign_id, zones))

    def campaign_status(self, campaign_id):
        return self.statuses.get(campaign_id, 2)

    def update_target_url(self, campaign_id, url):
        self.url_updates.append((campaign_id, url))

    def set_excluded_zones(self, campaign_id, zones):
        self.replaced.append((campaign_id, list(zones)))

    def stats_between(self, campaign_ids, start, end, by_zone=False):
        return [{"impressions": 0, "clicks": 0, "zone_id": "", **r}
                for r in getattr(self, "today_rows", [])]

    def spend_last_hours(self, campaign_ids, hours=24):
        return self.spend(campaign_ids)

    def spend(self, campaign_ids, days=30, by_zone=False):
        rows = self.zone_rows if by_zone else self.spend_rows
        return [{"impressions": 0, "clicks": 0, "zone_id": "", **r} for r in rows]


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
        pace_daily_budget=False,  # time-of-day dependent; tested on its own
        bounce_zone_min_visits=0,  # visits logged by tests carry no time; tested on its own
        agent_interval_hours=0,
        session_https_only=False,
        secret_key="test",
    )


@pytest.fixture
def store(settings):
    return Store(settings.data_dir)
