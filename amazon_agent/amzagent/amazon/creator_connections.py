"""Import of Amazon Creator Connections opportunities.

The Creators API has no endpoint that lists Creator Connections campaigns
(only earnings reports), so the dashboard takes the text of the
"New Opportunities" / "Accepted" page as copied from the browser
(Ctrl+A, Ctrl+C) and this module pulls out every ASIN and its
"Estimated EPC". Live product data (rating, reviews, price, sales rank)
is then fetched through the API as for any other product.

Bonus commission only applies to campaigns you accepted in Associates
Central, so accept them there before importing.
"""
from __future__ import annotations

import re

ASIN_RE = re.compile(r"\b(B0[A-Z0-9]{8})\b")
EPC_RE = re.compile(r"EPC[^$\n]*\$\s*([\d]+(?:\.\d+)?)", re.IGNORECASE)


def parse_opportunities(text: str) -> dict[str, float | None]:
    """{asin: estimated EPC in $ or None}, in page order."""
    matches = list(ASIN_RE.finditer(text.upper()))
    result: dict[str, float | None] = {}
    for i, m in enumerate(matches):
        asin = m.group(1)
        # The EPC line sits between this ASIN and the next one.
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        epc_match = EPC_RE.search(text, m.end(), end)
        epc = float(epc_match.group(1)) if epc_match else None
        if asin not in result or (epc is not None and result[asin] is None):
            result[asin] = epc
    return result
