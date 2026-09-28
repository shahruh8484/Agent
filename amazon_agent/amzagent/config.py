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
    # Seconds between Creators API calls. New accounts get a low request
    # rate; too fast gives "Rate limit exceeded".
    amazon_throttling: float = 2.0

    # --- Product selection ---
    min_rating: float = 4.0
    min_reviews: int = 100
    products_per_site: int = 12
    # Sites imported from Creator Connections: the list is hand-picked, so
    # the shelf is bigger (best ones by rating, reviews and EPC).
    import_site_size: int = 30
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
    # Model for the dashboard chat (it reasons over data and calls tools);
    # empty = the same model as the site texts.
    chat_model: str = ""

    # --- Push creatives ---
    # ai: an image model draws a scene per variant (needs OPENAI_API_KEY);
    # simple: text on a gradient, free. ai falls back to simple on errors.
    push_creatives: str = "ai"
    push_creative_variants: int = 2
    openai_image_model: str = "gpt-image-1"
    openai_image_quality: str = "medium"  # low | medium | high
    # Draw a labelled illustration for site products that have no Amazon
    # photo (fallback mode); at most this many new ones per cycle.
    site_illustrations: bool = True
    illustrations_per_run: int = 12

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
    # Spread each campaign's daily budget over its (UTC) day: PropellerAds
    # push CPC has no pacing of its own and spends it in a few hours, so the
    # agent pauses a campaign that runs ahead of schedule and resumes it later.
    pace_daily_budget: bool = True
    # In manual mode, still exclude zones with no Amazon clicks (same
    # thresholds as in auto mode) on every running campaign.
    manual_prune_zones: bool = True
    # Exclude a zone once this many of its clicks to Amazon were held back as
    # automated and they outnumber the real ones (0 = off).
    bot_zone_min: int = 3
    # Platform of new campaigns: all, mobile (phones and tablets) or desktop.
    push_platform: str = "all"
    campaigns_per_site: int = 3
    # Kill rule: once a campaign has spent at least kill_min_spend, stop it
    # if each click through to Amazon cost more than max_cost_per_amazon_click.
    kill_min_spend: float = 1.0
    # Keep a campaign only if at least this % of its site visitors click
    # through to Amazon (0 = rule off).
    min_amazon_rate: float = 1.0
    # Optionally also stop it if one click to Amazon costs more than this
    # (0 = rule off).
    max_cost_per_amazon_click: float = 0.0
    # Zone rule (campaigns that passed the test): exclude a zone with zero
    # clicks to Amazon once it had this many visits or this much spend.
    zone_min_visits: int = 15
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
    # Time zone the dashboard shows times in (IANA name)
    panel_timezone: str = "Asia/Tashkent"
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
