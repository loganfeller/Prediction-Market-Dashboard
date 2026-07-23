"""
Fetches official indicator data (FRED) and prediction-market-implied data
(Polymarket) for the two MVP indicators: headline CPI (YoY) and the Fed
funds rate.

Writes plain JSON files to /data that the static frontend reads directly.
Designed to run on a schedule via GitHub Actions (see .github/workflows/fetch-data.yml).

Polymarket's Gamma API is fully public and read-only for market data — no
API key, wallet, or signed requests needed. This is why it replaced Kalshi
in this project: Kalshi's API requires RSA-PSS signed requests for every
call, while Polymarket just needs a plain GET.

Required environment variable (set as a GitHub Actions secret):
  FRED_API_KEY - https://fred.stlouisfed.org/docs/api/api_key.html

Polymarket needs no credentials at all for reading market data.
"""

import json
import os
from datetime import datetime, timezone

import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

FRED_API_KEY = os.environ.get("FRED_API_KEY")

FRED_SERIES = {
    "cpi": "CPIAUCSL",       # CPI for All Urban Consumers, index (used to derive YoY %)
    "fed_rate": "FEDFUNDS",  # Effective federal funds rate
}

# Polymarket market slugs change every release cycle (e.g. a new CPI market
# opens each month, a new Fed-decision market opens each FOMC cycle).
# Update these slugs when a fetch comes back empty — check
# https://polymarket.com for the current slug of each event.
POLYMARKET_SLUGS = {
    "cpi": [
        # one slug per strike/range in the current CPI release's market group
        "cpi-yoy-below-2-5-percent-july-2026",
        "cpi-yoy-2-5-2-6-percent-july-2026",
        "cpi-yoy-2-6-2-7-percent-july-2026",
        "cpi-yoy-2-7-2-8-percent-july-2026",
        "cpi-yoy-2-8-2-9-percent-july-2026",
        "cpi-yoy-above-2-9-percent-july-2026",
    ],
    "fed_rate": [
        "fed-decision-in-july",  # Polymarket often runs this as one multi-outcome market
    ],
}

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"


def fetch_fred_series(series_id, limit=36):
    """Pull the most recent `limit` monthly observations for a FRED series."""
    if not FRED_API_KEY:
        print(f"[fred] no FRED_API_KEY set, skipping {series_id}")
        return None

    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    }
    resp = requests.get(FRED_API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    obs = resp.json().get("observations", [])
    obs = list(reversed(obs))  # chronological order
    return [
        {"date": o["date"], "value": float(o["value"])}
        for o in obs
        if o["value"] not in ("", ".")
    ]


def cpi_yoy_from_index(index_series):
    """Convert a monthly CPI index series into year-over-year % change."""
    by_date = {row["date"]: row["value"] for row in index_series}
    dates = sorted(by_date.keys())
    out = []
    for d in dates:
        year, month, day = d.split("-")
        prior_year_date = f"{int(year) - 1}-{month}-{day}"
        if prior_year_date in by_date:
            pct = (by_date[d] / by_date[prior_year_date] - 1) * 100
            out.append({"date": d, "value": round(pct, 2)})
    return out


def fetch_polymarket_market(slug):
    """
    Fetch a single Polymarket market by slug. No authentication needed --
    this endpoint is fully public. Returns None if the slug isn't found
    (e.g. because a new release cycle's market has a different slug).
    """
    resp = requests.get(f"{GAMMA_API_BASE}/markets", params={"slug": slug}, timeout=30)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        print(f"[polymarket] no market found for slug '{slug}' -- may need updating")
        return None
    return results[0]


def parse_market_to_row(market):
    """Extract the fields the frontend needs from a raw Gamma API market object."""
    # outcomePrices/outcomes are JSON-encoded strings in the Gamma API response
    outcomes = json.loads(market.get("outcomes", "[]"))
    prices = json.loads(market.get("outcomePrices", "[]"))
    yes_price = float(prices[0]) if prices else None

    return {
        "slug": market.get("slug"),
        "title": market.get("question", market.get("slug")),
        "implied_probability": round(yes_price, 4) if yes_price is not None else None,
        "volume": float(market.get("volume", 0) or 0),
        "close_time": market.get("endDate"),
    }


def fetch_polymarket_markets(slugs):
    rows = []
    for slug in slugs:
        market = fetch_polymarket_market(slug)
        if market is None:
            continue
        row = parse_market_to_row(market)
        if row["implied_probability"] is not None:
            rows.append(row)
    return rows if rows else None


def load_existing(name):
    path = os.path.join(DATA_DIR, f"{name}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"official": [], "market": [], "last_updated": None}


def write_json(name, payload):
    path = os.path.join(DATA_DIR, f"{name}.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"[write] {path}")


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    for indicator in ("cpi", "fed_rate"):
        existing = load_existing(indicator)

        official = existing["official"]
        fred_raw = fetch_fred_series(FRED_SERIES[indicator])
        if fred_raw is not None:
            official = cpi_yoy_from_index(fred_raw) if indicator == "cpi" else fred_raw

        market = existing["market"]
        poly_rows = fetch_polymarket_markets(POLYMARKET_SLUGS[indicator])
        if poly_rows is not None:
            market = poly_rows

        write_json(indicator, {
            "official": official,
            "market": market,
            "last_updated": now,
        })


if __name__ == "__main__":
    main()
