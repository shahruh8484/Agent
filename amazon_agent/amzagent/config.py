from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Amazon Creators API (replaced PA-API 5, which was retired May 2026) ---
    amazon_credential_id: str = ""
    amazon_credential_secret: str = ""
    # 3.1 = NA (amazon.com/.ca/.com.mx/.com.br), 3.2 = EU/UK/IN/ME, 3.3 = JP/AU/SG
    amazon_credential_version: str = "3.1"
    amazon_partner_tag: str = ""  # your Associates tracking id, e.g. mysite-20
    amazon_country: str = "US"

    # --- Product selection ---
    min_rating: float = 4.0
    min_reviews: int = 100
    products_per_site: int = 12
    # The agent keeps at least this many sites running, picking the niches
    # itself (LLM ideas, checked against the live Amazon catalog). 0 = only
    # the niches you add by hand.
    auto_niches: int = 3

    # --- LLM (site copy + push ad copy) ---
    llm_provider: str = "anthropic"  # anthropic | openai
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # --- Push creatives ---
    # ai: an image model draws a scene per variant (needs OPENAI_API_KEY);
    # simple: text on a gradient, free. ai falls back to simple on errors.
    push_creatives: str = "ai"
    push_creative_variants: int = 2
    openai_image_model: str = "gpt-image-1"
    openai_image_quality: str = "medium"  # low | medium | high

    # --- PropellerAds (push notification campaigns) ---
    propeller_api_token: str = ""
    # false = build every campaign payload and log it, but send nothing.
    # Flip to true once you've checked a dry run on the dashboard.
    push_live: bool = False
    push_countries: str = "US"
    push_bid_cpc: float = 0.03
    campaign_daily_budget: float = 10.0  # PropellerAds minimum for push CPC is $10
    # Hard cap on the sum of daily budgets of every running campaign. The
    # agent never launches a campaign that would push the total above it.
    max_daily_spend: float = 30.0
    campaigns_per_site: int = 3
    # Kill rule: once a campaign has spent at least kill_min_spend, stop it
    # if each click through to Amazon cost more than max_cost_per_amazon_click.
    kill_min_spend: float = 5.0
    max_cost_per_amazon_click: float = 0.40
    # Zone rule: blacklist a zone after it spent this much with zero
    # clicks through to Amazon.
    zone_min_spend: float = 1.0

    # --- Agent loop ---
    # How often (hours) the agent runs its full cycle for every niche:
    # refresh products -> rebuild site -> optimize campaigns -> launch new
    # ones. 0 disables the loop (you can still press "Run now").
    agent_interval_hours: int = 6

    # --- Web / deploy ---
    domain: str = ""  # public domain the sites and dashboard are served on
    # Name shown on the public home page (defaults to the domain name)
    site_name: str = ""
    # Shown on the Contact and Privacy pages (the contact form works without it)
    contact_email: str = ""
    data_dir: str = "data"
    admin_username: str = "admin"
    admin_password_hash: str = ""
    secret_key: str = ""
    session_https_only: bool = True

    def push_countries_list(self) -> list[str]:
        return [c.strip().lower() for c in self.push_countries.split(",") if c.strip()]

    def public_base_url(self) -> str:
        return f"https://{self.domain}" if self.domain else "http://localhost:8000"


def get_settings() -> Settings:
    return Settings()
