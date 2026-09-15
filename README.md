# Marex · US structured-note market monitor

Streamlit app comparing Marex against every other SEC-registered structured-note
issuer, with the underlying data refreshed nightly by GitHub Actions.

## Repo layout

| Path | Purpose |
|---|---|
| `app.py` | Streamlit UI — rolling market share, rank over time, YTD pace, league table, product mix, drill-down |
| `edgar_market.py` | Fast incremental crawler → `data/notes.csv` (one row per filing) |
| `edgar_data.py` | Loads the store, merges preliminary/final per ISIN, cleans names, flags Marex |
| `edgar_notes.py` | Original scraper — its cover-page parser is reused by `edgar_market.py` |
| `data/notes.csv` | The store. Committed to the repo; updated by the nightly job |
| `data/failed.json` | Accessions that failed to fetch/parse, with retry counts |
| `.github/workflows/nightly.yml` | 05:30 UTC Tue–Sat: crawl last 10 business days, commit |
| `.github/workflows/backfill.yml` | Manual: crawl a date window (run per quarter of history) |
| `.streamlit/config.toml` | Marex theme tokens |
| `app_single_period.py` | Earlier single-period app, kept for reference |

## One-time setup

1. Push this repo to GitHub.
2. Repo → Settings → Secrets and variables → Actions → **New repository secret**
   `EDGAR_UA` = `Your Name your@marex.com` (EDGAR rejects generic user agents).
3. Repo → Settings → Actions → General → Workflow permissions → **Read and write**
   (so the bot can commit the store).
4. Backfill: Actions → *Backfill EDGAR history* → Run workflow, once per quarter:
   `2025-01-01`→`2025-03-31`, `2025-04-01`→`2025-06-30`, … up to yesterday.
   Each run is 30–40 minutes and appends to the store. Run them one at a time.
5. Deploy on Streamlit Community Cloud: main file `app.py`. Add the same
   `EDGAR_UA` under the app's Secrets so the in-app catch-up can run.

After that the nightly job keeps the store current. Streamlit Cloud redeploys
on each commit, so the app opens with yesterday's filings already loaded. If
the store is ever behind (job failed, opened before 05:30), the app fetches the
gap itself on open.

## Running locally

```bash
pip install -r requirements.txt
export EDGAR_UA="Your Name your@marex.com"
python edgar_market.py --days-back 5       # or --start/--end
streamlit run app.py
```

## How the crawler stays fast

Requests to EDGAR are capped at 10/s, so speed = fewer requests:
per-day index files instead of the 50 MB quarterly index; full-text search to
get ~100 document names per request instead of one submissions JSON per issuer;
only the first 400 KB of each supplement is read; the fee-exhibit size fallback
is off by default (`--fee-fallback` to enable). A normal day is ~400 requests,
about a minute.

## Notes on the data

* Marex is identified by filer name (`MAREX` in the EDGAR company name).
* Only structured notes are shown (vanilla fixed/floating takedowns excluded by
  the parser's `structured` flag). Everything the SEC index lists as 424B2 is crawled.
* Preliminary and final supplements for the same ISIN are merged; the final
  filing wins on size. "Priced notes only" hides ISINs with no final yet.
* Market-share denominators always include every issuer, not just the plotted peers.
