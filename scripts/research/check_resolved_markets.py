"""
Small standalone script: looks up a short, known list of specific Polymarket
event slugs directly and prints their final resolved outcome for each
underlying market. No pagination, no tag-hunting, no CLOB price history --
just a direct, reliable check of ground truth for a handful of markets
picked by hand for the memo international section.
"""

import json

import requests

GAMMA_API_BASE = "https://gamma-api.polymarket.com"

# Add or edit entries here as more candidates get confirmed.
EVENT_SLUGS_TO_CHECK = {
    "Fed decision, July 2026": "fed-decision-in-july-181",
    "ECB rate decision, July 2026": "ecb-interest-rates-july-2026",
    "BOJ rate decision, June 2026": "bank-of-japan-decision-in-june",
    "UK inflation, June 2026": "june-inflation-uk-annual-20260617152237894",
}


def fetch_event(slug):
    resp = requests.get(f"{GAMMA_API_BASE}/events", params={"slug": slug}, timeout=30)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    return results[0]


def summarize_event(label, slug):
    event = fetch_event(slug)
    if event is None:
        print(f"[check] {label} ({slug}): EVENT NOT FOUND")
        return

    print(f"\n[check] {label} ({slug})")
    for market in event.get("markets", []):
        title = market.get("question", market.get("slug"))
        try:
            prices = json.loads(market.get("outcomePrices", "[]"))
            final_price = float(prices[0]) if prices else None
        except (ValueError, TypeError):
            final_price = None
        closed = market.get("closed")
        print(f"    - {title!r}: final_price={final_price} closed={closed}")


def main():
    for label, slug in EVENT_SLUGS_TO_CHECK.items():
        summarize_event(label, slug)


if __name__ == "__main__":
    main()
