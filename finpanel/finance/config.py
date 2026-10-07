from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Login — generate the hash with `python -m finance.security <password>`
    admin_username: str = "admin"
    admin_password_hash: str = ""
    secret_key: str = ""
    session_https_only: bool = True

    # Where the SQLite database lives (mount as a volume in production).
    data_dir: str = "data"

    # Claude API — powers the chat assistant and screenshot reading.
    # Everything else in the panel works without it.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"

    # Public domain (used by Caddy for HTTPS).
    domain: str = ""

    # "Today" for reports (guarantee day, periods) is taken in this zone.
    timezone: str = "Asia/Tashkent"


def get_settings() -> Settings:
    return Settings()
