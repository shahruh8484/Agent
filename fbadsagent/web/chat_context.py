"""Builds the system prompt for the agent chat: who it is, plus a live
summary of the dashboard's current data so replies/ideas are grounded in
the user's actual accounts, offers, pages and creatives.
"""
from __future__ import annotations

from fbadsagent.web.account_store import AccountStore
from fbadsagent.web.cpa_store import CpaNetworkStore
from fbadsagent.web.creative_store import CreativeStore
from fbadsagent.web.landing_store import LandingPageStore

SYSTEM_PROMPT_HEADER = (
    "You are the assistant built into a Facebook Ads AI agent dashboard. "
    "The dashboard runs competitor research, ad copy/creative generation, "
    "landing pages with lead capture, and CPA network integrations "
    "(currently traff-hub.com). You talk with the person running this "
    "business - answer their questions, suggest concrete next actions, "
    "and when asked for ideas, propose specific angles/offers/creatives "
    "given their current setup below. Reply in the same language the user "
    "writes in. Be concise and concrete - prefer a short actionable list "
    "over a long explanation."
)


def build_context_summary(
    account_store: AccountStore,
    cpa_store: CpaNetworkStore,
    landing_store: LandingPageStore,
    creative_store: CreativeStore,
) -> str:
    accounts = account_store.list_accounts()
    networks = cpa_store.list_networks()
    pages = landing_store.list_pages()
    creative_sets = creative_store.list_sets()

    lines = ["Current dashboard state:"]

    if accounts:
        lines.append(
            "- Facebook ad accounts: "
            + ", ".join(f"{a.name or a.id} ({a.id})" for a in accounts)
        )
    else:
        lines.append("- Facebook ad accounts: none configured yet.")

    if networks:
        lines.append(
            "- CPA networks: " + ", ".join(n.name for n in networks)
        )
    else:
        lines.append("- CPA networks: none configured yet.")

    if pages:
        lines.append("- Published landing pages:")
        for p in pages:
            lines.append(
                f"  - /lp/{p.slug} \"{p.title}\" -> {p.cpa_network} "
                f"(campaign hash: {p.campaign_hash or 'not set'})"
            )
    else:
        lines.append("- Published landing pages: none yet.")

    if creative_sets:
        lines.append("- Generated creative sets:")
        for c in creative_sets[:10]:
            lines.append(f"  - {c.product_name} ({len(c.creatives)} variants, {c.created_at})")
    else:
        lines.append("- Generated creative sets: none yet.")

    return "\n".join(lines)


def build_system_prompt(
    account_store: AccountStore,
    cpa_store: CpaNetworkStore,
    landing_store: LandingPageStore,
    creative_store: CreativeStore,
) -> str:
    context = build_context_summary(account_store, cpa_store, landing_store, creative_store)
    return f"{SYSTEM_PROMPT_HEADER}\n\n{context}"
