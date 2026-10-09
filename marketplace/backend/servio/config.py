"""Settings loaded from environment variables (prefix SERVIO_) or .env."""
from __future__ import annotations

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Plan(BaseModel):
    id: str
    title: str
    days: int
    price: int  # in whole currency units (RUB)


DEFAULT_PLANS = [
    Plan(id="month", title="1 месяц", days=30, price=990),
    Plan(id="quarter", title="3 месяца", days=90, price=2490),
    Plan(id="year", title="12 месяцев", days=365, price=7990),
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SERVIO_", env_file=".env", extra="ignore")

    app_name: str = "Servio"
    database_path: str = "data/servio.db"
    currency: str = "RUB"

    # SMS login. "dev" returns the code in the API response instead of sending it.
    sms_provider: str = "dev"  # dev | smsru
    smsru_api_id: str = ""
    code_ttl_seconds: int = 300
    code_max_attempts: int = 5

    # Specialists pay a subscription to respond to orders.
    trial_days: int = 7
    payment_provider: str = "dev"  # dev | yookassa
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    payment_return_url: str = "servio://subscription"

    # Expo push notifications (https://docs.expo.dev/push-notifications/sending-notifications/)
    push_enabled: bool = False

    plans: list[Plan] = DEFAULT_PLANS

    def plan(self, plan_id: str) -> Plan | None:
        return next((p for p in self.plans if p.id == plan_id), None)
