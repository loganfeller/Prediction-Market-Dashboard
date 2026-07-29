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
the same folder as this script.

NOTE on pagination: the Gamma API appears to cap page size at 100 events
regardless of the limit requested, and also enforces a hard limit on how
deep offset-based pagination can go (a 422 response), observed empirically
around offset 2100. The fetch loop below accounts for both: it keeps
paging until it gets an actually empty page OR a 422, rather than stopping
as soon as a page comes back smaller than requested.
"""

import json
import re
import time
from datetime import datetime, timedelta, timezone

import requests

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"

# Confirmed directly via the Gamma API tags-by-slug lookup (see
# find_fx_tag_id below) -- the broad parent Finance tag (120) was tried
# first and confirmed NOT to surface FX-specific content on its own, so
# these more specific, overlapping FX-related tags are queried instead
# and the results merged/deduplicated by event id.
FX_TAG_IDS = [101705, 103636, 897, 102721, 105093, 102719]

# A market counts as FX-related if its title matches any of these patterns.
# Built from real example titles seen on Polymarket Exchange Rate, Foreign
# Exchange, and Currency category pages, e.g. "Will EUR/USD hit __ in
# 2026?", "Will USD/JPY hit __ in 2026?", "Argentina Official USD Exchange
# Rate end of 2026?".
FX_TITLE_PATTERNS = [
    re.compile(r"\b[A-Z]{3}/[A-Z]{3}\b"),          # e.g. EUR/USD, USD/JPY
    re.compile(r"exchange rate", re.IGNORECASE),
    re.compile(r"\bUSD\b.*\b(rial|rupee|won|real|peso|yen)\b", re.IGNORECASE),
]

# How long before resolution to check the market implied price. Chosen to
# allow a direct comparison against Polymarket own "accurate more than 94%
# of the time a month before" claim.
LOOKBACK_DAYS = 30

OUTPUT_PATH = "fx_accuracy_results.json"


def is_fx_market_title(title):
    if not title:
        return False
    return any(pattern.search(title) for pattern in FX_TITLE_PATTERNS)


def fetch_closed_events_for_tag(tag_id, limit=100, max_pages=100):
    """Page through closed events for a single tag id, return the raw list."""
    events = []
    offset = 0
    for page_num in range(max_pages):
        params = {
            "tag_id": tag_id,
            "closed": "true",
            "limit": limit,
            "offset": offset,
        }
        resp = requests.get(f"{GAMMA_API_BASE}/events", params=params, timeout=30)
        if resp.status_code == 422:
            print(f"[fx] tag {tag_id} page {page_num}: got 422 at offset {offset} -- pagination limit reached, stopping here")
            break
        resp.raise_for_status()
        page = resp.json()
        print(f"[fx] tag {tag_id} page {page_num}: fetched {len(page)} events at offset {offset}")
        if not page:
            break
        events.extend(page)
        offset += limit
        time.sleep(0.5)
    return events


def fetch_closed_fx_events():
    """Query every FX-related tag id and merge results, deduped by event id."""
    events_by_id = {}
    for tag_id in FX_TAG_IDS:
        events = fetch_closed_events_for_tag(tag_id)
        for event in events:
            events_by_id[event.get("id")] = event
    return list(events_by_id.values())


def extract_fx_markets(events):
    """Flatten events into individual markets, keeping only FX-titled ones."""
    fx_markets = []
    total_markets_seen = 0
    sample_titles = []
    for event in events:
        for market in event.get("markets", []):
            total_markets_seen += 1
            title = market.get("question", market.get("slug", ""))
            if len(sample_titles) < 15:
                sample_titles.append(title)
            if is_fx_market_title(title):
                fx_markets.append(market)

    print(f"[fx] total individual markets seen across all events: {total_markets_seen}")
    print(f"[fx] sample titles seen (up to 15): {sample_titles}")
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
        if final_price > 0.9:
            return 1.0
        if final_price < 0.1:
            return 0.0
        return None
    except (ValueError, TypeError):
        return None


def get_price_at_lookback(clob_token_id, resolution_time, lookback_days=LOOKBACK_DAYS):
    """
    Query the CLOB price-history endpoint for the implied probability at
    approximately lookback_days before resolution_time.
    """
    target_time = resolution_time - timedelta(days=lookback_days)
    start_ts = int(target_time.timestamp())
    end_ts = int((target_time + timedelta(hours=12)).timestamp())

    params = {
        "market": clob_token_id,
        "startTs": start_ts,
        "endTs": end_ts,
        "interval": "max",  # required alongside startTs/endTs per working examples
        "fidelity": 60,
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
