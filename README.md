# Indicator Tracker

Compares official U.S. economic indicators (via FRED) against what Polymarket's
prediction-market contracts currently imply for the next release. MVP scope:
CPI (YoY) and the Fed funds rate.

## How it's wired together

```
scripts/fetch_data.py   → pulls FRED + Polymarket data, writes data/*.json
.github/workflows/      → runs fetch_data.py daily via GitHub Actions, commits the JSON
data/*.json             → plain data files, no database
docs/                   → static frontend (GitHub Pages serves this folder directly)
```

No backend server. GitHub Actions does the fetching on a schedule, and the
frontend is a static site that just reads the resulting JSON files.

Polymarket was chosen over Kalshi for the market-data side of this project
because its Gamma API is fully public and read-only — no API key, wallet, or
signed requests needed, just a plain GET by market slug. (Kalshi's API
requires RSA-PSS signed requests for every call, which added real complexity
for no benefit at this MVP stage.)

## One-time setup

1. **Create a new GitHub repo** and push this folder to it.

2. **Get a FRED API key** (free, instant): https://fred.stlouisfed.org/docs/api/api_key.html
   This is the *only* credential this project needs — Polymarket's data is
   fully public.

3. **Add the secret to the repo** (Settings → Secrets and variables → Actions):
   - `FRED_API_KEY`

4. **Enable GitHub Pages**: Settings → Pages → Source: "Deploy from a branch" →
   Branch: `main`, folder: `/docs`. Your dashboard will be live at
   `https://<username>.github.io/<repo-name>/`.

5. **Run the workflow once manually** to populate real data: Actions tab →
   "Fetch indicator data" → Run workflow. After that it runs automatically every
   day at 13:00 UTC.

Until you've set the secret and run the workflow, the dashboard will show the
sample data currently in `data/*.json` (clearly labeled with a `note` field) —
that's there so the frontend has something real-shaped to render immediately
and so you can sanity-check the layout before wiring up live data.

## Polymarket market slugs

Polymarket market slugs change each release cycle (a new CPI market opens
each month, a new Fed-decision market opens each FOMC cycle). `fetch_data.py`
looks up markets by exact slug — if a fetch comes back empty for an
indicator, check https://polymarket.com for the current slug of that event
and update the `POLYMARKET_SLUGS` dictionary at the top of the script. No
credentials are needed to look these up; you can check them directly in a
browser or with a plain `curl`.

## Extending

- **More indicators**: add an entry to `FRED_SERIES` / `POLYMARKET_SLUGS`
  in `fetch_data.py`, and to the `INDICATORS` array in `docs/app.js`.
- **More regions**: Euro Area (ECB/Frankfurter) and Japan were part of the
  original project scope but are out of scope for this MVP — the fetch script
  and frontend are both structured so a new indicator/region is a config
  addition, not a rewrite.
