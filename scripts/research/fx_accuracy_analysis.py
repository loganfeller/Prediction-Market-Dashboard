"""
Research script (not part of the daily dashboard pipeline): checks how well
Polymarket FX prediction markets have calibrated historically, by comparing
each resolved market price against its actual outcome.

This exists to independently check claims like Polymarket own stated
"accurate more than 94% of the time a month before an outcome is known" for
its Exchange Rate category -- that figure is Polymarket own marketing
statistic with no published methodology, so this script recomputes a
similar figure from scratch using Polymarket own public data.

Two separate Polymarket APIs are used:
  Gamma API  (gamma-api.polymarket.com) -- catalog data: which markets
             exist, their titles, resolution status, and final outcome.
  CLOB API   (clob.polymarket.com)      -- price history for a specific
             market token, used to find the implied probability at a
             chosen point in time before resolution.

Output: a JSON file (fx_accuracy_results.json) with one row per market
analyzed (title, resolution, price at the lookback window, and whether
the modal side was correct), plus summary calibration buckets, written to
the same folder as this script. A companion chart is left for a follow-up
step once the raw numbers are in hand.
"""

import json
import re
import time
from datetime import datetime, timedelta, timezone

import requests

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"

FINANCE_TAG_ID = 120  # confirmed via Polymarket public source code

# A market counts as FX-related if its title matches any of these patterns.
# Built from real example titles seen on Polymarket's Exchange Rate /
# Foreign Exchange / Currency category pages, e.g. "Will EUR/USD hit __ in
# 2026?", "Will USD/JPY hit __ in 2026?", "Argentina Official USD Exchange
# Rate end of 2026?".
FX_TITLE_PATTERNS = [
    re.compile(r"\b[A-Z]{3}/[A-Z]{3}\b"),          # e.g. EUR/USD, USD/JPY
    re.compile(r"exchange rate", re.IGNORECASE),
    re.compile(r"\bUSD\b.*\b(rial|rupee|won|real|peso|yen)\b", re.IGNORECASE),
]

# How long before resolution to check the market's implied price. Chosen to
# let a direct comparison against Polymarket own "accurate more than 94% of
# the time a month before" claim.
LOOKBACK_DAYS = 30

OUTPUT_PATH = "fx_accuracy_results.json"


def is_fx_market_title(title):
    if not title:
        return False
    return any(pattern.search(title) for pattern in FX_TITLE_PATTERNS)


def fetch_closed_finance_events(limit=500, max_pages=20):
    """Page through closed events tagged Finance, return the raw list."""
    events = []
    offset = 0
    for _ in range(max_pages):
        params = {
            "tag_id": FINANCE_TAG_ID,
            "closed": "true",
            "limit": limit,
            "offset": offset,
        }
        resp = requests.get(f"{GAMMA_API_BASE}/events", params=params, timeout=30)
        resp.raise_for_status()
        page = resp.json()
        if not page:
            break
        events.extend(page)
        if len(page) < limit:
            break
        offset += limit
        time.sleep(0.5)  # be polite to the API between pages
    return events


def extract_fx_markets(events):
    """Flatten events into individual markets, keeping only FX-titled ones."""
    fx_markets = []
    for event in events:
        for market in event.get("markets", []):
            title = market.get("question", market.get("slug", ""))
            if is_fx_market_title(title):
                fx_markets.append(market)
    return fx_markets


def get_final_outcome(market):
    """
    Returns 1.0 if the market resolved YES, 0.0 if NO, or None if the
    resolution can not be determined from the outcomePrices field.
    """
    try:
        prices = json.loads(market.get("outcomePrices", "[]"))
        if not prices:
            return None
        final_price = float(prices[0])
        # Resolved markets should settle very close to 0 or 1.
        if final_price > 0.9:
            return 1.0
        if final_price < 0.1:
            return 0.0
        return None  # ambiguous / not cleanly resolved
    except (ValueError, TypeError):
        return None


def get_price_at_lookback(clob_token_id, resolution_time, lookback_days=LOOKBACK_DAYS):
    """
    Query the CLOB price-history endpoint for the implied probability at
    approximately `lookback_days` before resolution_time.
    """
    target_time = resolution_time - timedelta(days=lookback_days)
    start_ts = int(target_time.timestamp())
    end_ts = int((target_time + timedelta(hours=12)).timestamp())

    params = {
        "market": clob_token_id,
        "startTs": start_ts,
        "endTs": end_ts,
        "fidelity": 60,  # minutes between points
    }
    try:
        resp = requests.get(f"{CLOB_API_BASE}/prices-history", params=params, timeout=30)
        resp.raise_for_status()
        history = resp.json().get("history", [])
    except Exception as e:
        print(f"[fx] price history request failed for token {clob_token_id}: {e}")
        return None

    if not history:
        return None
    # Take the point closest to the target time.
    closest = min(history, key=lambda p: abs(p.get("t", 0) - start_ts))
    return closest.get("p")


def analyze_market(market):
    """Build one result row for a single FX market, or None if incomplete."""
    title = market.get("question", market.get("slug", ""))
    outcome = get_final_outcome(market)
    if outcome is None:
        return None

    end_date_str = market.get("endDate")
    if not end_date_str:
        return None
    try:
        resolution_time = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
    except ValueError:
        return None

    try:
        clob_token_ids = json.loads(market.get("clobTokenIds", "[]"))
    except (ValueError, TypeError):
        clob_token_ids = []
    if not clob_token_ids:
        return None

    price_at_lookback = get_price_at_lookback(clob_token_ids[0], resolution_time)
    if price_at_lookback is None:
        return None

    predicted_yes = price_at_lookback >= 0.5
    actual_yes = outcome >= 0.5
    correct = predicted_yes == actual_yes

    return {
        "slug": market.get("slug"),
        "title": title,
        "resolution_time": resolution_time.isoformat(),
        "outcome": outcome,
        "implied_probability_at_lookback": round(price_at_lookback, 4),
        "modal_side_correct": correct,
    }


def summarize(results):
    """Overall accuracy plus a simple calibration-by-price-bucket table."""
    if not results:
        return {"overall_accuracy": None, "n": 0, "buckets": {}}

    n_correct = sum(1 for r in results if r["modal_side_correct"])
    overall_accuracy = n_correct / len(results)

    buckets = {}
    for r in results:
        p = r["implied_probability_at_lookback"]
        bucket_key = f"{int(p * 10) * 10}-{int(p * 10) * 10 + 10}"
        bucket = buckets.setdefault(bucket_key, {"n": 0, "n_resolved_yes": 0})
        bucket["n"] += 1
        if r["outcome"] >= 0.5:
            bucket["n_resolved_yes"] += 1

    for bucket in buckets.values():
        bucket["resolved_yes_rate"] = round(bucket["n_resolved_yes"] / bucket["n"], 4)

    return {
        "overall_accuracy": round(overall_accuracy, 4),
        "n": len(results),
        "buckets": buckets,
    }


def main():
    print("[fx] fetching closed Finance-tagged events...")
    events = fetch_closed_finance_events()
    print(f"[fx] fetched {len(events)} closed Finance events total")

    fx_markets = extract_fx_markets(events)
    print(f"[fx] {len(fx_markets)} markets matched FX title patterns")

    results = []
    for market in fx_markets:
        row = analyze_market(market)
        if row is not None:
            results.append(row)
        time.sleep(0.3)  # be polite to the CLOB API between requests

    print(f"[fx] successfully analyzed {len(results)} markets")

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "summary": summarize(results),
        "markets": results,
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)
    print(f"[fx] wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
