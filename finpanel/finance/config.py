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

    # AI for the chat assistant and screenshot reading — set one of the two
    # keys. Everything else in the panel works without either.
    # llm_provider: "openai" | "anthropic" | "" (auto: whichever key is set,
    # OpenAI first).
    llm_provider: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"

    # Public domain (used by Caddy for HTTPS).
    domain: str = ""

    # "Today" for reports (guarantee day, periods) is taken in this zone.
    timezone: str = "Asia/Tashkent"


def assistant_provider(settings: Settings) -> str:
    """Which AI backs the chat: 'openai', 'anthropic', or '' if no key."""
    choice = settings.llm_provider.strip().lower()
    if choice == "openai" and settings.openai_api_key:
        return "openai"
    if choice == "anthropic" and settings.anthropic_api_key:
        return "anthropic"
    if settings.openai_api_key:
        return "openai"
    if settings.anthropic_api_key:
        return "anthropic"
    return ""


def get_settings() -> Settings:
    return Settings()
