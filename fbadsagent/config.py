from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Facebook / Meta Marketing API
    fb_access_token: str = ""
    fb_app_id: str = ""
    fb_app_secret: str = ""
    fb_ad_account_id: str = ""
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

    # Image generation
    image_provider: str = "stub"
    openai_image_model: str = "gpt-image-1"

    # Output
    output_dir: str = "output"


def get_settings() -> Settings:
    return Settings()
