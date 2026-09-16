import pytest

from fbadsagent.config import Settings
from fbadsagent.models import ProductInput


@pytest.fixture
def settings(tmp_path):
    return Settings(
        fb_access_token="test-token",
        fb_app_id="test-app-id",
        fb_app_secret="test-app-secret",
        fb_ad_account_id="act_123",
        fb_page_id="page_123",
        image_provider="stub",
        output_dir=str(tmp_path / "output"),
    )


@pytest.fixture
def web_settings(settings, tmp_path):
    from fbadsagent.web.security import hash_password

    settings.secret_key = "test-secret-key"
    settings.admin_username = "admin"
    settings.admin_password_hash = hash_password("correct-horse")
    settings.session_https_only = False  # TestClient talks plain http
    settings.fb_ad_account_ids = "act_111,act_222"
    settings.data_dir = str(tmp_path / "data")
    return settings


@pytest.fixture
def product():
    return ProductInput(
        name="Wireless Earbuds Pro",
        description="Noise-cancelling wireless earbuds with 40h battery life",
        landing_url="https://example.com/product",
        price=49.99,
        currency="USD",
        target_countries=["US"],
        daily_budget=25.0,
        keywords=["wireless earbuds"],
    )


class FakeLLM:
    """Deterministic stand-in for LLMProvider that never hits the network."""

    def __init__(self, response: str = ""):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, prompt: str, max_tokens: int = 1024) -> str:
        self.calls.append((system, prompt))
        return self.response
