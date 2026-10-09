"""Settings loaded from environment variables (prefix SERVIO_) or .env."""
from __future__ import annotations

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Plan(BaseModel):
    id: str
    days: int
    price: int  # whole so'm


DEFAULT_PLANS = [
    Plan(id="month", days=30, price=99_000),
    Plan(id="quarter", days=90, price=249_000),
    Plan(id="year", days=365, price=799_000),
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SERVIO_", env_file=".env", extra="ignore")

    app_name: str = "Servio"
    database_path: str = "data/servio.db"
    currency: str = "UZS"

    # SMS login. "dev" returns the code in the API response instead of sending it.
    sms_provider: str = "dev"  # dev | eskiz
    eskiz_email: str = ""
    eskiz_password: str = ""
    eskiz_from: str = "4546"
    # Eskiz only delivers texts that match a template approved in its cabinet.
    sms_template: str = "Servio: tasdiqlash kodi {code}"
    code_ttl_seconds: int = 300
    code_max_attempts: int = 5

    # Specialists pay a subscription to respond to orders.
    trial_days: int = 7
    # dev = subscription activates instantly; live = real Payme / Click payments
    payment_mode: str = "dev"
    payment_return_url: str = "servio://subscription"
    payme_merchant_id: str = ""
    payme_key: str = ""
    payme_test: bool = True  # test.paycom.uz checkout instead of checkout.paycom.uz
    click_service_id: str = ""
    click_merchant_id: str = ""
    click_secret_key: str = ""

    # Expo push notifications (https://docs.expo.dev/push-notifications/sending-notifications/)
    push_enabled: bool = False

    plans: list[Plan] = DEFAULT_PLANS

    def plan(self, plan_id: str) -> Plan | None:
        return next((p for p in self.plans if p.id == plan_id), None)

    def payment_providers(self) -> list[str]:
        if self.payment_mode == "dev":
            return ["dev"]
        out = []
        if self.payme_merchant_id and self.payme_key:
            out.append("payme")
        if self.click_service_id and self.click_merchant_id and self.click_secret_key:
            out.append("click")
        return out
