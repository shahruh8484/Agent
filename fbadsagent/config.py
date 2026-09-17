from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Facebook / Meta Marketing API
    fb_access_token: str = ""
    fb_app_id: str = ""
    fb_app_secret: str = ""
    fb_ad_account_id: str = ""
    fb_ad_account_ids: str = ""  # comma-separated list of act_... ids for the dashboard
    fb_page_id: str = ""
    fb_api_version: str = "v20.0"

    # Ad Library (competitor research)
    ad_library_countries: str = "US"

    # LLM
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"

    # Image generation
    image_provider: str = "stub"
    openai_image_model: str = "gpt-image-1"

    # Output
    output_dir: str = "output"

    # Dashboard auth (see fbadsagent/web) — generate the hash with
    # `python -m fbadsagent.web.security <password>`
    admin_username: str = "admin"
    admin_password_hash: str = ""
    secret_key: str = ""
    session_https_only: bool = True

    # Where the dashboard persists ad accounts + access token added through
    # the "FB Accounts" page (mount this as a volume in production so it
    # survives container rebuilds — see docker-compose.yml).
    data_dir: str = "data"

    # How often (hours) the chat page's background loop posts a proactive
    # idea on its own. Set to 0 to disable the background loop entirely.
    chat_idea_interval_hours: int = 6

    def fb_ad_account_ids_list(self) -> list[str]:
        ids = [x.strip() for x in self.fb_ad_account_ids.split(",") if x.strip()]
        if not ids and self.fb_ad_account_id:
            ids = [self.fb_ad_account_id]
        return ids


def get_settings() -> Settings:
    return Settings()
