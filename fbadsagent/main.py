"""CLI entrypoint.

Example:
    python -m fbadsagent.main \\
        --name "Wireless Earbuds Pro" \\
        --description "Noise-cancelling wireless earbuds with 40h battery life" \\
        --price 49.99 --country US --daily-budget 25 \\
        --landing-url https://example.com/product

Add --live to actually push the campaign to Facebook (requires FB_* env vars).
Without it, the campaign step is a dry run: payloads are printed/saved but
nothing is sent to Facebook.
"""
from __future__ import annotations

import json
import logging

import typer

from fbadsagent.config import get_settings
from fbadsagent.models import ProductInput
from fbadsagent.orchestrator.pipeline import Pipeline

app = typer.Typer(add_completion=False)


@app.command()
def run(
    name: str = typer.Option(..., help="Product name"),
    description: str = typer.Option(..., help="Short product description"),
    landing_url: str = typer.Option(None, help="Existing checkout/landing URL, if any"),
    price: float = typer.Option(None, help="Product price"),
    currency: str = typer.Option("USD"),
    country: list[str] = typer.Option(["US"], help="Target country code(s)"),
    daily_budget: float = typer.Option(20.0, help="Daily ad budget"),
    keyword: list[str] = typer.Option([], help="Keyword(s) for competitor research"),
    live: bool = typer.Option(False, help="Actually create the campaign on Facebook (paused)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the full pipeline: competitor research -> creatives -> landing page -> campaign."""
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING)

    product = ProductInput(
        name=name,
        description=description,
        landing_url=landing_url,
        price=price,
        currency=currency,
        target_countries=list(country),
        daily_budget=daily_budget,
        keywords=list(keyword),
    )

    settings = get_settings()
    pipeline = Pipeline(settings=settings)
    result = pipeline.run(product, dry_run=not live)

    typer.echo(f"\n=== Competitor research ({len(result.competitor_ads)} ads) ===")
    typer.echo(result.insights.model_dump_json(indent=2) if result.insights else "n/a")

    typer.echo(f"\n=== Ad creative variants ({len(result.creatives)}) ===")
    for c in result.creatives:
        typer.echo(f"- [{c.variant_id}] {c.headline} | {c.primary_text[:80]}...")

    typer.echo("\n=== Landing page ===")
    if result.landing_page:
        typer.echo(f"Saved to: {result.landing_page.html_path}")

    typer.echo(f"\n=== Facebook campaign (dry_run={not live}) ===")
    if result.campaign:
        typer.echo(f"Status: {result.campaign.status}")
        if result.campaign.dry_run:
            typer.echo(json.dumps(result.campaign.payloads, indent=2, default=str))
        else:
            typer.echo(f"Campaign ID: {result.campaign.campaign_id}")
            typer.echo(f"AdSet ID: {result.campaign.adset_id}")
            typer.echo(f"Ad IDs: {result.campaign.ad_ids}")


if __name__ == "__main__":
    app()
