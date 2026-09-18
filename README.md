# Facebook Ads AI Agent

An AI agent that runs the end-to-end workflow for launching Facebook ad
campaigns for a product:

1. **Competitor research** — pulls live/recent ads for the product's niche
   from the public [Meta Ad Library API](https://www.facebook.com/ads/library/api/)
   (free, no paid "ad spy" subscription required).
2. **Competitor analysis** — an LLM extracts recurring hooks, offers, tone,
   and recommends a differentiated angle.
3. **Ad creative generation** — the LLM writes multiple ad copy variants
   (primary text, headline, description, CTA) tailored to that angle, plus
   an image prompt for each variant.
4. **Image generation** — creative images per variant (pluggable provider).
5. **Landing page generation** — a responsive HTML landing page is
   generated and rendered from LLM-written copy.
6. **Facebook campaign creation** — builds a Campaign → AdSet → Ad(s)
   structure via the Marketing API, **always created PAUSED**, with a
   dry-run mode on by default.
7. **Dashboard** — a login-protected web UI showing daily clicks, spend,
   leads, CPL/CPC/CTR per ad account, deployable to your own domain with
   HTTPS (see [Dashboard](#dashboard) below).

## Architecture

```
fbadsagent/
├── models.py                  # Pydantic data models shared across the pipeline
├── config.py                  # Settings loaded from .env
├── main.py                    # Typer CLI entrypoint
├── facebook/
│   ├── ad_library.py          # Meta Ad Library API client (competitor research)
│   └── ads_client.py          # Marketing API client (campaign/adset/ad creation)
├── llm/
│   ├── provider.py            # Anthropic/OpenAI text-generation abstraction
│   ├── competitor_analysis.py # Ads -> insights
│   └── copywriter.py          # Insights -> ad copy variants
├── creatives/
│   └── image_generator.py     # Image generation (openai | stub)
├── landing/
│   ├── generator.py           # Landing page copy + HTML rendering
│   └── templates/landing_base.html.j2
├── orchestrator/
│   └── pipeline.py            # Wires every stage together
└── web/                       # Login-protected analytics dashboard (FastAPI)
    ├── app.py                 # Routes: /login, /, /api/accounts, /api/insights
    ├── insights_client.py     # Facebook Marketing API Insights client
    ├── security.py            # bcrypt password hashing + hash-generator CLI
    ├── __main__.py             # `python -m fbadsagent.web` entrypoint
    └── templates/login.html, dashboard.html

Dockerfile, docker-compose.yml, deploy/Caddyfile   # deploy the dashboard to a domain
```

Every external integration (LLM, image generation, Facebook APIs) is
injected as a dependency into `Pipeline`, so each stage can be tested in
isolation with fakes/mocks — see `tests/`, which run fully offline.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in .env with your keys (see below)
```

### Credentials you'll need

| Purpose | Variable(s) | How to get it |
|---|---|---|
| Competitor research (Meta Ad Library) | `FB_ACCESS_TOKEN` | Create an app at [developers.facebook.com](https://developers.facebook.com), generate a user access token with the `ads_read` permission. Free, no ad account needed. |
| Creating real campaigns | `FB_ACCESS_TOKEN`, `FB_APP_ID`, `FB_APP_SECRET`, `FB_AD_ACCOUNT_ID`, `FB_PAGE_ID` | Add the "Marketing API" product to your app; the token's user needs admin access on the ad account and page. |
| Ad copy / competitor analysis / landing page copy | `ANTHROPIC_API_KEY` (default), `OPENAI_API_KEY`, or `GEMINI_API_KEY` | [console.anthropic.com](https://console.anthropic.com), [platform.openai.com](https://platform.openai.com), or [ai.google.dev](https://ai.google.dev) (Gemini has a free tier) |
| Creative images | `OPENAI_API_KEY` (set `IMAGE_PROVIDER=openai`) or `GEMINI_API_KEY` (set `IMAGE_PROVIDER=gemini`, free-tier eligible) | Defaults to `IMAGE_PROVIDER=stub`, which writes a placeholder image and needs no key — useful for testing the pipeline before paying for image generation. |

Nothing is required to explore the code and run the test suite — tests use
fakes for every external call.

## Usage

```bash
python -m fbadsagent.main \
    --name "Wireless Earbuds Pro" \
    --description "Noise-cancelling wireless earbuds with 40h battery life" \
    --price 49.99 --country US --daily-budget 25 \
    --landing-url https://example.com/product
```

By default this is a **dry run**: no campaign is created on Facebook. It
prints the exact payloads that *would* be sent, plus the competitor
insights, generated ad copy, and the path to the generated landing page
HTML file.

Add `--live` once you've reviewed the dry-run output and set the Facebook
credentials above, to actually create the campaign (still PAUSED, so
nothing spends until you explicitly enable it in Ads Manager):

```bash
python -m fbadsagent.main --name "..." --description "..." --live
```

## Dashboard

A login-protected web dashboard shows, per Facebook ad account: daily
spend, daily clicks, daily leads, and totals/averages (CPC, CPL, CTR,
impressions), sourced live from the Facebook Marketing API's Insights
endpoint.

### Run it locally

```bash
# 1. Generate a session secret and an admin password hash, put both in .env
python -c "import secrets; print(secrets.token_hex(32))"   # -> SECRET_KEY
python -m fbadsagent.web.security "your-password-here"     # -> ADMIN_PASSWORD_HASH

# 2. In .env, also set:
#    FB_ACCESS_TOKEN=...                 (needs the ads_read permission)
#    FB_AD_ACCOUNT_IDS=act_111,act_222   (the accounts you want to see)
#    SESSION_HTTPS_ONLY=false            (only for local http testing)

# 3. Run
python -m fbadsagent.web
# -> http://localhost:8000, log in with ADMIN_USERNAME / the password you hashed
```

### Deploy it to your own domain

This repo ships a `docker-compose.yml` that runs the dashboard behind
[Caddy](https://caddyserver.com/), which gets and renews an HTTPS
certificate for your domain automatically (Let's Encrypt) — no manual
certbot/nginx setup. This works on any VPS (DigitalOcean, Hetzner, a
Render/Railway "Docker" service, EC2, etc.) — pick whichever
host you already have an account on:

```bash
# On the server:
git clone <this repo> && cd Agent
cp .env.example .env
# Fill in .env: FB_* creds, ANTHROPIC_API_KEY, SECRET_KEY, ADMIN_PASSWORD_HASH,
# SESSION_HTTPS_ONLY=true, and DOMAIN=yourdomain.com

docker compose up -d --build
```

Before running, point your domain's **A record** at the server's public IP
(that part — buying/owning the domain and its DNS — has to happen in your
registrar/DNS provider's dashboard; it isn't something that can be done
from this repo). Once DNS resolves, Caddy will automatically obtain a
certificate for `DOMAIN` the first time it starts, and the dashboard is
reachable at `https://yourdomain.com` with the login you configured.

To update after a code change: `git pull && docker compose up -d --build`.

### CPA Networks page

Sidebar → **CPA Networks** stores credentials (name, base URL, API key)
for offer/affiliate networks, persisted the same way as FB Accounts
(`data/cpa_networks.json`, same Docker volume).

**traff-hub.com is actually wired up** (`fbadsagent/integrations/traffhub.py`):
- `send_lead(...)` — `POST /lead/add`, reports a lead generated on one of
  your landing pages back to traff-hub (required: phone, fio, ip, and the
  campaign's `hash` from the traff-hub dashboard).
- `list_conversions(...)` — `POST /conversion/list`, checks lead status
  (pending/confirmed/rejected/trash) by transaction id, status, or date range.
- There is **no "list offers" API** — offers/campaigns are picked manually
  in the traff-hub dashboard; its `hash` per campaign is what `send_lead`
  needs.
- Add a network named exactly `traff-hub` with its API key, then hit
  **Test** on that row to confirm the key works (calls `list_conversions`).

Any other network name just stores credentials — pulling data for it
needs a client written against that network's own docs, following
`traffhub.py`/`insights_client.py` as a pattern.

### Chat page

Sidebar → **Chat** is a conversation with the agent, grounded in the
dashboard's live state (FB accounts, CPA networks, landing pages,
creative sets — see `fbadsagent/web/chat_context.py`). Ask it questions,
or click **Ask for ideas** to get one concrete suggestion on demand.

It also posts proactively on its own: a background loop (started in the
app's lifespan, `_background_idea_loop` in `fbadsagent/web/app.py`) asks
the LLM for an idea every `CHAT_IDEA_INTERVAL_HOURS` (default 6, 0
disables it) and appends it to the chat, so opening the tab later shows
ideas the agent posted between visits. History persists in
`data/chat.json` (same volume as the other stores, capped at the most
recent 200 messages).

Needs an LLM key set per `LLM_PROVIDER` (same as Creatives, including the
free-tier `gemini` option) — without one, sending a message shows a clear
error inline and the background loop just logs and skips that cycle.

### Creatives page

Sidebar → **Creatives** generates ad copy + an image per variant from a
product name/description/price, using the same modules the CLI pipeline
uses (`fbadsagent/llm/copywriter.py`, `fbadsagent/creatives/image_generator.py`),
and stores the results as a browsable gallery (`data/creative_sets.json`;
images under `data/creatives/`, served at `/creative-assets/...`).

Needs an LLM key set per `LLM_PROVIDER` — `anthropic`, `openai`, or `gemini`
(the last has a free tier, see `.env.example`) — to write copy; without
one, generation fails with a clear error shown on the page rather than a
crash. Images use `IMAGE_PROVIDER=stub` by default
(placeholder, no cost); set `IMAGE_PROVIDER=openai` or `IMAGE_PROVIDER=gemini`
(free-tier eligible, same `GEMINI_API_KEY` as the LLM) for real ones. This
page skips the competitor-research step the CLI pipeline does — it's a
quicker "just generate creatives" path, not a replacement for the full
`python -m fbadsagent.main` pipeline.

### Landing Pages page

Sidebar → **Landing Pages** publishes lead-capture pages, each with a
name + phone form that POSTs straight to a CPA network's `send_lead`
(currently traff-hub only). Pages are public at `/lp/{slug}` (no login —
this is what you send ad traffic to); managing them (create/remove) is
login-protected under `/landing-pages`.

Flow to actually run traffic:
1. On traff-hub: create a campaign with **Тип кампании = API** (not
   "Партнёрская ссылка" — that needs their domain-parking flow instead)
   and copy its campaign hash.
2. On CPA Networks: make sure `traff-hub` has a working API key.
3. On Landing Pages: publish a page, paste that campaign hash in.
4. Point your Facebook ad's link at `https://yourdomain/lp/{slug}`.
   When someone submits the form, the lead is forwarded to traff-hub via
   `TraffHubClient.send_lead()` in real time.

Content is entered manually for now (title/headline/benefits/CTA) — the
CLI's AI landing-page generator (`fbadsagent/landing/generator.py`) isn't
wired into this page yet; a natural next step is generating that copy
with the LLM and publishing straight from the pipeline instead of typing
it by hand.

### Agent page

Sidebar → **Agent** is the fully autonomous pipeline: add a product once,
and the agent researches competitors, writes ad copy + generates images,
publishes a landing page wired to a CPA network, and creates a Facebook
campaign for it — on its own, either on a schedule or on demand.

**Campaigns the agent creates are always `status: PAUSED`.** This is a
hard safety guarantee (`fbadsagent/facebook/ads_client.py`,
`fbadsagent/web/agent_runner.py`) — the agent never spends real money by
itself. You review the campaign in Facebook Ads Manager and enable it
yourself when you're happy with it.

1. Add a product: name, description, price, daily budget, keywords, which
   Facebook ad account to launch under, and which CPA network + campaign
   hash the landing page should forward leads to (same as the Landing
   Pages page).
2. Click **Run now** to trigger a full pipeline run immediately, or set
   `AGENT_RUN_INTERVAL_HOURS` in `.env` (default 0 = off) so it runs every
   product on that interval by itself, unattended.
3. Each run is logged (`data/agent_runs.json`) with its outcome — success
   (with links to the landing page and, once you look it up, the FB
   campaign) or a clear error message if any step failed (no LLM
   configured, no FB ad account assigned, Facebook API rejected the
   campaign, etc.) — later steps still complete and get logged even if an
   earlier one failed partway (e.g. creatives + landing page can succeed
   even if the FB campaign step then fails).

Needs the same `LLM_PROVIDER` key as Creatives/Chat, `IMAGE_PROVIDER` for
creative images, and a Facebook access token with `ads_management` scope
(`FB_ACCESS_TOKEN` — broader than the `ads_read` scope used for the
Insights dashboard and Ad Library research) so it can actually create
campaigns via the Marketing API. Set `DOMAIN` in `.env` so the landing
pages it publishes get a real `https://yourdomain/lp/{slug}` URL instead
of a relative one.

Products and run history persist in `data/agent_products.json` and
`data/agent_runs.json` (same Docker volume as everything else). Core
orchestration lives in `fbadsagent/web/agent_runner.py:run_agent_for_product`,
reusing the same LLM/creative/landing-page/Facebook modules as the CLI
pipeline and the other dashboard pages — it's the same building blocks,
just chained together and triggered automatically instead of by hand.

### FB Accounts page

Sidebar → **FB Accounts** manages the access token and tracked `act_...`
ad accounts directly from the browser — no SSH or `.env` edit needed after
the initial deploy. Values are stored in a small JSON file at `DATA_DIR`
(default `data/accounts.json`), which `docker-compose.yml` mounts as a
named volume (`app_data`) so it survives `docker compose up --build`.
`FB_ACCESS_TOKEN` / `FB_AD_ACCOUNT_IDS` in `.env` only seed this store the
*first* time the app starts with no existing data file — after that, the
page is the source of truth.

Click **Sync from Facebook** instead of typing account IDs by hand — it
calls `GET /me/adaccounts` with the configured access token (falls back to
`FB_ACCESS_TOKEN` in `.env` if no token is set on this page) and adds
every ad account that token currently has permission on. For a System
User token, that's every ad account granted to it in Meta Business
Settings — granting that access to a new account is still a manual,
one-time step in Business Settings (Facebook requires a human admin to do
this, it can't be done via API by the app itself), but after that, syncing
picks it up automatically instead of copying account IDs one by one.

### What it shows

- Account selector (whatever's added on the FB Accounts page) and a
  date-range preset (today, last 7/14/30/90 days, this/last month).
- Summary cards: total spend, total clicks, total leads, impressions,
  average CPC, average CPL, average CTR.
- Daily line charts for spend, clicks and leads over the selected range.
- "Leads" is computed from the Insights API's `actions` field for
  `lead`-family action types (lead ads, pixel leads, Messenger lead
  flows) — if your leads come from a different conversion event, adjust
  `LEAD_ACTION_TYPES` in `fbadsagent/web/insights_client.py`.

## Safety defaults

- Campaigns, ad sets and ads are always created with `status: PAUSED`.
- `--live` is opt-in; the default is a dry run that only prints payloads.
- Credentials are validated up front with a clear error listing exactly
  which environment variables are missing.

## Tests

```bash
pytest
```

All 107 tests run offline — network calls (Ad Library, Insights API,
Anthropic/OpenAI, Facebook Marketing API) are mocked or swapped for fakes,
and the dashboard is tested through FastAPI's `TestClient`.

## Roadmap / what's stubbed today

- **Image generation**: `stub` provider is a placeholder; `openai` provider
  uses DALL·E-style image generation; `gemini` uses Gemini's native image
  generation (free-tier eligible). A dedicated ad-creative diffusion
  pipeline (product photo compositing, brand templates) is a natural next
  step.
- **Ad spy integrations**: currently uses the free Meta Ad Library API,
  which covers "what ads are competitors running." Paid tools like
  AdSpy/BigSpy/PowerAdSpy add cross-platform coverage and longer history —
  swapping/adding a provider means implementing the same
  `search_competitor_ads(query) -> list[CompetitorAd]` interface as
  `AdLibraryClient`.
- **Landing pages**: generates a single-template responsive page today.
  Multiple templates, A/B variants, and deploying to hosting (Vercel/S3)
  automatically are natural extensions.
- **Budget/performance optimization loop**: no feedback loop yet from ad
  performance back into copy/targeting — this pipeline covers the
  "launch" side, not ongoing optimization.
- **Dashboard**: single admin login today (one username/password from
  `.env`). Multi-user auth, historical data beyond what the Insights API
  retains (a small DB + daily sync job), and per-campaign (not just
  per-account) breakdowns are natural next steps.
