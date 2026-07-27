"""
Fetches official indicator data (FRED) and prediction-market-implied data
(Polymarket) for three indicators: headline CPI (YoY), the Fed funds rate,
and the ECB deposit facility rate.

Writes plain JSON files to /docs/data that the static frontend reads directly.
Designed to run on a schedule via GitHub Actions (see .github/workflows/fetch-data.yml).

Polymarket's Gamma API is fully public and read-only for market data -- no
API key, wallet, or signed requests needed.

Required environment variable (set as a GitHub Actions secret):
  FRED_API_KEY - https://fred.stlouisfed.org/docs/api/api_key.html

Polymarket needs no credentials at all for reading market data.

NOTE on FRED series frequency: CPIAUCSL and FEDFUNDS are MONTHLY series, so
pulling the most recent N observations gives N months of history. ECBDFR is
a DAILY series -- pulling N observations there gives only N days. To keep
the ECB chart comparable to the other two, we pull a larger daily window and
collapse it to one observation per month.

NOTE on rate-decision markets: Fed and ECB rate-decision events on Polymarket
are structured as several yes/no questions per meeting, e.g. "Will the ECB
announce a 25 bps increase at the September 2026 meeting?" rather than a
single number. To compute a market-implied expected RATE (not just a set of
probabilities), we parse the bps change out of each question's title and
add it to the most recent official rate reading, then attach that as each
row's numeric "strike" -- which the frontend already knows how to turn into
a probability-weighted expected value.
"""

import json
import os
import re
from datetime import datetime, timezone

import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")

FRED_API_KEY = os.environ.get("FRED_API_KEY")

FRED_SERIES = {
    "cpi": "CPIAUCSL",
    "fed_rate": "FEDFUNDS",
    "ecb_rate": "ECBDFR",
}

FRED_FETCH_LIMIT = {
    "cpi": 36,
    "fed_rate": 36,
    "ecb_rate": 400,
}

POLYMARKET_SLUGS = {
    "cpi": [
        "cpi-yoy-below-2-5-percent-july-2026",
        "cpi-yoy-2-5-2-6-percent-july-2026",
        "cpi-yoy-2-6-2-7-percent-july-2026",
        "cpi-yoy-2-7-2-8-percent-july-2026",
        "cpi-yoy-2-8-2-9-percent-july-2026",
        "cpi-yoy-above-2-9-percent-july-2026",
    ],
    "fed_rate": [
        "fed-decision-in-july-181",
    ],
    "ecb_rate": [
        "ecb-interest-rates-september-2026-20260616222636097",
    ],
}

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"

# Matches e.g. "25 bps increase", "50+ bps decrease". The "+" in "50+" (meaning
# "50 or more") is treated as exactly 50 for this calculation -- a deliberate
# simplification, since the open-ended tail bracket has no single exact value.
BPS_PATTERN = re.compile(r"(\d+)\+?\s*bps\s*(increase|decrease)", re.IGNORECASE)


def fetch_fred_series(series_id, limit=36):
    """Pull the most recent `limit` observations for a FRED series."""
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
    obs = list(reversed(obs))
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


def last_observation_per_month(series):
    """Collapse a daily series down to one observation per calendar month."""
    by_month = {}
    for row in series:
        month_key = row["date"][:7]
        by_month[month_key] = row
    return [by_month[key] for key in sorted(by_month.keys())]


def parse_bps_change(title):
    """
    Extract a signed basis-point change from a rate-decision market title.
    Order-independent: matches both "25 bps increase" (ECB phrasing) and
    "decrease interest rates by 25 bps" (Fed phrasing). Returns 0 for
    "no change", a signed int otherwise, or None if nothing matches.
    """
    if not title:
        return None
    if re.search(r"no change", title, re.IGNORECASE):
        return 0
    bps_match = re.search(r"(\d+)\+?\s*bps", title, re.IGNORECASE)
    if not bps_match:
        return None
    bps = int(bps_match.group(1))
    if re.search(r"\bdecrease\b", title, re.IGNORECASE):
        return -bps
    if re.search(r"\bincrease\b", title, re.IGNORECASE):
        return bps
    return None


def fetch_polymarket_market(slug):
    """Fetch a single Polymarket market by slug. No authentication needed."""
    resp = requests.get(f"{GAMMA_API_BASE}/markets", params={"slug": slug}, timeout=30)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        print(f"[polymarket] no market found for slug '{slug}' -- may need updating")
        return None
    return results[0]


def fetch_polymarket_event_markets(event_slug):
    """Fetch an EVENT by slug and return its underlying markets."""
    resp = requests.get(f"{GAMMA_API_BASE}/events", params={"slug": event_slug}, timeout=30)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        print(f"[polymarket] no event found for slug '{event_slug}' -- may need updating")
        return []
    return results[0].get("markets", [])


def parse_market_to_row(market, baseline_rate=None):
    """
    Extract the fields the frontend needs from a raw Gamma API market object.
    If baseline_rate is given, also attempts to attach a numeric "strike" --
    the implied rate level if this outcome resolves -- by parsing a bps
    change out of the market's title.
    """
    prices = json.loads(market.get("outcomePrices", "[]"))
    yes_price = float(prices[0]) if prices else None
    title = market.get("question", market.get("slug"))

    row = {
        "slug": market.get("slug"),
        "title": title,
        "implied_probability": round(yes_price, 4) if yes_price is not None else None,
        "volume": float(market.get("volume", 0) or 0),
        "close_time": market.get("endDate"),
    }

    if baseline_rate is not None:
        bps_change = parse_bps_change(title)
        if bps_change is not None:
            row["strike"] = round(baseline_rate + bps_change / 100, 4)

    return row


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


def fetch_polymarket_event(event_slug, baseline_rate=None):
    """Fetch all markets under an event slug and convert each to a row."""
    markets = fetch_polymarket_event_markets(event_slug)
    rows = []
    for market in markets:
        row = parse_market_to_row(market, baseline_rate=baseline_rate)
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

    for indicator in ("cpi", "fed_rate", "ecb_rate"):
        existing = load_existing(indicator)

        official = existing["official"]
        fred_raw = fetch_fred_series(FRED_SERIES[indicator], limit=FRED_FETCH_LIMIT[indicator])
        if fred_raw is not None:
            if indicator == "cpi":
                official = cpi_yoy_from_index(fred_raw)
            elif indicator == "ecb_rate":
                official = last_observation_per_month(fred_raw)
            else:
                official = fred_raw

        market = existing["market"]
        if indicator in ("fed_rate", "ecb_rate"):
            baseline_rate = official[-1]["value"] if official else None
            poly_rows = fetch_polymarket_event(POLYMARKET_SLUGS[indicator][0], baseline_rate=baseline_rate)
        else:
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
