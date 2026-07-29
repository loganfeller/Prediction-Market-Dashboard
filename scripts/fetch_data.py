"""
Fetches official indicator data (FRED + Bank of Japan) and prediction-
market-implied data (Polymarket) for four indicators: headline CPI (YoY),
the Fed funds rate, the ECB deposit facility rate, and the BOJ policy rate.

Writes plain JSON files to /docs/data that the static frontend reads directly.
Designed to run on a schedule via GitHub Actions (see .github/workflows/fetch-data.yml).

Polymarket's Gamma API is fully public and read-only for market data -- no
API key needed. The Bank of Japan's own Time-Series Data Search API is also
fully public and needs no key.

Required environment variable (set as a GitHub Actions secret):
  FRED_API_KEY - https://fred.stlouisfed.org/docs/api/api_key.html

NOTE on FRED series frequency: CPIAUCSL and FEDFUNDS are MONTHLY series, so
pulling the most recent N observations gives N months of history. ECBDFR is
a DAILY series -- pulling N observations there gives only N days. To keep
the ECB chart comparable to the others, we pull a larger daily window and
collapse it to one observation per month.

NOTE on the BOJ policy rate: FRED's international series for Japan's policy
rate (tried IRSTCB01JPM156N, then INTDSRJPM193N) both turned out to be
stale/discontinued despite appearing current on FRED's own pages. The BOJ's
own official API (launched publicly in Feb 2026) is used instead as the
primary source. Series code FM02'STRACLUCON ("Call Rate, Uncollateralized
Overnight/Average") was confirmed live through April 2026 directly on the
BOJ's statistics site.

NOTE on rate-decision markets: Fed/ECB/BOJ rate-decision events on Polymarket
are structured as several yes/no questions per meeting (e.g. "Will the ECB
announce a 25 bps increase..." or "Will the Fed decrease interest rates by
25 bps..." -- the direction word can appear either before or after "bps"
depending on phrasing). To compute a market-implied expected RATE (not just
a set of probabilities), we parse the bps change out of each question's
title and add it to the most recent official rate reading, then attach that
as each row's numeric "strike" -- which the frontend already knows how to
turn into a probability-weighted expected value.
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

# BOJ policy rate is fetched separately, from the Bank of Japan's own API
# (see module docstring for why).
BOJ_API_BASE = "https://www.stat-search.boj.or.jp/api/v1"
BOJ_SERIES_CODE = "FM02'STRACLUCON"  # Call Rate, Uncollateralized Overnight/Average

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
    "boj_rate": [
        "bank-of-japan-decision-in-july-659",
    ],
}

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"


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


def fetch_boj_series(series_code, limit=36):
    """
    Pull the most recent `limit` monthly observations for a BOJ series via
    the Bank of Japan's own
