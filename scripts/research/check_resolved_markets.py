"""
Small standalone script: looks up a short, known list of specific Polymarket
event slugs directly and prints, for each underlying market: the final
resolved outcome, total trading volume, and the implied probability at two
points before resolution (1 day before and 30 days before) -- letting a
side-by-side comparison of "how confident was the market close to the
decision" vs "how confident was it a month out," similar to what the FEDS
paper checked for Kalshi Fed-rate contracts.

Uses two Polymarket APIs:
  Gamma API (gamma-api.polymarket.com) -- event/market catalog data,
             final resolution, and total volume.
  CLOB API  (clob.polymarket.com)      -- price history for a specific
             market token, used for the two pre-resolution snapshots.
"""

import json
import time
from datetime import datetime, timedelta

import requests

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"

# Add or edit entries here as more candidates get confirmed.
EVENT_SLUGS_TO_CHECK = {
    "Fed decision, July 2026": "fed-decision-in-july-181",
    "ECB rate decision, July 2026": "ecb-interest-rates-july-2026",
    "BOJ rate decision, June 2026": "bank-of-japan-decision-in-june",
    "UK inflation, June 2026": "june-inflation-uk-annual-20260617152237894",
}

SNAPSHOT_DAYS_BEFORE = [1, 30]


def fetch_event(slug):
    resp = requests.get(f"{GAMMA_API_BASE}/events", params={"slug": slug}, timeout=30)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    return results[0]


def get_price_snapshot(clob_token_id, target_time):
    """Return the implied YES probability closest to target_time, or None."""
    start_ts = int(target_time.timestamp())
    end_ts = int((target_time + timedelta(hours=12)).timestamp())
    params = {
        "market": clob_token_id,
        "startTs": start_ts,
        "endTs": end_ts,
        "interval": "max",
        "fidelity": 720,  # 12-hour granularity, coarser and more likely to have data for older points
    }
    try:
        resp = requests.get(f"{CLOB_API_BASE}/prices-history", params=params, timeout=15)
        resp.raise_for_status()
        history = resp.json().get("history", [])
    except Exception as e:
        print(f"        (price snapshot request failed: {e})")
        return None
    if not history:
        return None
    closest = min(history, key=lambda p: abs(p.get("t", 0) - start_ts))
    return closest.get("p")


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

        volume = float(market.get("volume", 0) or 0)

        end_date_str = market.get("endDate")
        resolution_time = None
        if end_date_str:
            try:
                resolution_time = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            except ValueError:
                resolution_time = None

        try:
            clob_token_ids = json.loads(market.get("clobTokenIds", "[]"))
        except (ValueError, TypeError):
            clob_token_ids = []
        token_id = clob_token_ids[0] if clob_token_ids else None

        snapshots = {}
        if token_id and resolution_time:
            for days_before in SNAPSHOT_DAYS_BEFORE:
                target = resolution_time - timedelta(days=days_before)
                price = get_price_snapshot(token_id, target)
                snapshots[days_before] = price
                time.sleep(0.3)

        print(f"    - {title!r}")
        print(f"        final_price={final_price}  volume=${volume:,.0f}")
        for days_before in SNAPSHOT_DAYS_BEFORE:
            print(f"        price {days_before} day(s) before resolution: {snapshots.get(days_before)}")


def main():
    for label, slug in EVENT_SLUGS_TO_CHECK.items():
        summarize_event(label, slug)


if __name__ == "__main__":
    main()
