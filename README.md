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
└── orchestrator/
    └── pipeline.py            # Wires every stage together
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
| Ad copy / competitor analysis / landing page copy | `ANTHROPIC_API_KEY` (default) or `OPENAI_API_KEY` | [console.anthropic.com](https://console.anthropic.com) or [platform.openai.com](https://platform.openai.com) |
| Creative images | `OPENAI_API_KEY` (set `IMAGE_PROVIDER=openai`) | Defaults to `IMAGE_PROVIDER=stub`, which writes a placeholder image and needs no key — useful for testing the pipeline before paying for image generation. |

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

## Safety defaults

- Campaigns, ad sets and ads are always created with `status: PAUSED`.
- `--live` is opt-in; the default is a dry run that only prints payloads.
- Credentials are validated up front with a clear error listing exactly
  which environment variables are missing.

## Tests

```bash
pytest
```

All 17 tests run offline — network calls (Ad Library, Anthropic/OpenAI,
Facebook Marketing API) are mocked or swapped for fakes.

## Roadmap / what's stubbed today

- **Image generation**: `stub` provider is a placeholder; `openai` provider
  uses DALL·E-style image generation. A dedicated ad-creative diffusion
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
