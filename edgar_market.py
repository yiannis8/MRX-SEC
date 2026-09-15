#!/usr/bin/env python3
"""
edgar_market.py — incremental, market-wide crawl of SEC 424(b)(2) pricing
supplements into a single CSV store, tuned for a nightly job.

    export EDGAR_UA="Name name@marex.com"

    # backfill one window (run per quarter; each run appends)
    python edgar_market.py --start 2025-01-01 --end 2025-03-31

    # nightly: last N business days (re-checks recent days so final
    # supplements that follow a preliminary one are picked up)
    python edgar_market.py --days-back 10

Compared with edgar_notes.py this makes far fewer requests:
  * Enumeration: EDGAR's per-day index files (~300 KB each) rather than the
    quarterly master index (~50 MB).
  * Document names: EDGAR full-text search returns the primary document for
    ~100 filings per request; only stragglers cost one index.json each. No
    per-issuer submissions JSON (which for the big banks is several MB and
    paginated).
  * Documents: only the first ~400 KB of each supplement is read — the cover
    page is all the parser needs.
  * Fee-exhibit size fallback (2 extra requests per note) is off by default.
  * Throttle at 9 req/s with enough workers to saturate it.

Parsing itself is delegated to edgar_notes.py so the two stay consistent.
The store is one row per *filing* (preliminary and final kept separate);
merging per ISIN happens at read time in edgar_data.load_market().
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

import edgar_notes as en

SEC = en.SEC
EFTS = "https://efts.sec.gov/LATEST/search-index"
RATE = 9.0
DOC_HEAD_BYTES = 400_000
DATA_DIR = Path(os.environ.get("EDGAR_DATA_DIR", "data"))
NOTES_PATH = DATA_DIR / "notes.csv"
FAILED_PATH = DATA_DIR / "failed.json"
MAX_RETRIES = 3

en.THROTTLE = en.Throttle(RATE)


# --------------------------------------------------------------------------- #
# HTTP helpers on top of edgar_notes.fetch
# --------------------------------------------------------------------------- #

def fetch_head(url: str, max_bytes: int = DOC_HEAD_BYTES, tries: int = 4) -> str | None:
    """GET the first `max_bytes` of a document and stop. EDGAR ignores Range
    headers on archive documents, so stream and cut."""
    for attempt in range(tries):
        en.THROTTLE.wait()
        try:
            with en.session().get(url, timeout=45, stream=True) as r:
                if r.status_code == 404:
                    return None
                if r.status_code != 200:
                    time.sleep(2 ** attempt + 1)
                    continue
                buf = bytearray()
                for chunk in r.iter_content(65_536):
                    buf.extend(chunk)
                    if len(buf) >= max_bytes:
                        break
                enc = r.encoding or "utf-8"
                return buf.decode(enc, errors="replace")
        except requests.RequestException:
            time.sleep(2 ** attempt)
    return None


# --------------------------------------------------------------------------- #
# Step 1 — enumerate filings from the daily index
# --------------------------------------------------------------------------- #

def business_days(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def enumerate_day(d: date, forms: set[str]) -> list[dict]:
    q = (d.month - 1) // 3 + 1
    url = f"{SEC}/Archives/edgar/daily-index/{d.year}/QTR{q}/master.{d:%Y%m%d}.idx"
    txt = en.fetch(url)
    if not txt:
        return []
    rows = []
    for line in txt.splitlines():
        parts = line.split("|")
        if len(parts) != 5:
            continue
        cik, name, form, filed, fname = (p.strip() for p in parts)
        if form not in forms:
            continue
        m = re.search(r"(\d{10}-\d{2}-\d{6})", fname)
        if not m:
            continue
        rows.append(dict(accession=m.group(1), cik=cik, issuer=name, form=form,
                         filed=d.isoformat()))
    return rows


# --------------------------------------------------------------------------- #
# Step 2 — primary document names via full-text search, index.json fallback
# --------------------------------------------------------------------------- #

def efts_documents(start: date, end: date, forms: set[str]) -> dict[str, str]:
    """accession -> primary document filename for every hit in the window.
    Full-text search pages 100 at a time and caps at 10,000 hits per query,
    so windows are queried a day at a time."""
    mapping: dict[str, str] = {}
    for d in business_days(start, end):
        offset = 0
        while True:
            params = {"q": '"424(b)(2)"', "forms": ",".join(sorted(forms)),
                      "dateRange": "custom", "startdt": d.isoformat(),
                      "enddt": d.isoformat(), "from": offset}
            en.THROTTLE.wait()
            try:
                r = en.session().get(EFTS, params=params, timeout=45)
                if r.status_code != 200:
                    break
                data = r.json()
            except (requests.RequestException, ValueError):
                break
            hits = data.get("hits", {}).get("hits", [])
            for h in hits:
                _id = h.get("_id", "")            # "0001234567-26-000123:doc.htm"
                if ":" in _id:
                    acc, doc = _id.split(":", 1)
                    if doc and not mapping.get(acc):
                        mapping[acc] = doc
            total = data.get("hits", {}).get("total", {}).get("value", 0)
            offset += len(hits)
            if not hits or offset >= total or offset >= 9900:
                break
    return mapping


def index_json_document(cik: str, accession: str) -> str | None:
    nod = accession.replace("-", "")
    j = en.fetch(f"{SEC}/Archives/edgar/data/{int(cik)}/{nod}/index.json")
    if not j:
        return None
    try:
        items = json.loads(j)["directory"]["item"]
    except Exception:
        return None
    cands = [i["name"] for i in items
             if i["name"].lower().endswith((".htm", ".html"))
             and "ex-filingfees" not in i["name"].lower()
             and not i["name"].lower().startswith("r")]
    return cands[0] if cands else None


def doc_url(cik: str, accession: str, doc: str) -> str:
    return f"{SEC}/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{doc}"


# --------------------------------------------------------------------------- #
# Step 3 — parse
# --------------------------------------------------------------------------- #

def process(row: dict, use_fee: bool) -> tuple[dict | None, str | None]:
    raw = fetch_head(row["url"])
    if not raw:
        return None, "fetch_failed"
    rec = en.parse_doc(en.to_text(raw))
    if rec["size_usd"] is None and use_fee and not rec["preliminary"]:
        rec["size_usd"] = en.fee_exhibit_size(row["cik"], row["accession"])
        if rec["size_usd"]:
            rec["parse_flags"] = rec["parse_flags"].replace("no_size", "size_from_fee_exhibit")
    rec.update(accession=row["accession"], cik=row["cik"], issuer=row["issuer"],
               form=row["form"], filed=row["filed"], url=row["url"],
               crawled_at=datetime.utcnow().isoformat(timespec="seconds"))
    return rec, None


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #

STORE_COLS = en.COLS + ["crawled_at"]


def load_store() -> pd.DataFrame:
    if NOTES_PATH.exists():
        return pd.read_csv(NOTES_PATH, dtype=str, keep_default_na=False, na_values=[""])
    return pd.DataFrame(columns=STORE_COLS)


def save_store(df: pd.DataFrame) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for c in STORE_COLS:
        if c not in df:
            df[c] = None
    df = df[STORE_COLS]
    df = df.copy()
    df["size_usd"] = pd.to_numeric(df["size_usd"], errors="coerce").round().astype("Int64")
    df["tenor_years"] = pd.to_numeric(df["tenor_years"], errors="coerce").round(2)
    for c in ("structured", "preliminary"):
        df[c] = df[c].map(lambda v: str(v).lower() in ("true", "1")).astype(bool)
    df.sort_values(["filed", "accession"]).to_csv(NOTES_PATH, index=False)


def load_failed() -> dict[str, int]:
    if FAILED_PATH.exists():
        return json.loads(FAILED_PATH.read_text())
    return {}


def save_failed(failed: dict[str, int]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FAILED_PATH.write_text(json.dumps(failed, indent=0, sort_keys=True))


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #

def crawl(start: date, end: date, forms: set[str], workers: int, use_fee: bool,
          log=print) -> dict:
    store = load_store()
    failed = load_failed()
    done = set(store["accession"].dropna()) if len(store) else set()

    log(f"enumerating {sorted(forms)} {start} → {end}")
    filings: list[dict] = []
    for d in business_days(start, end):
        rows = enumerate_day(d, forms)
        log(f"  {d}  {len(rows)} filings")
        filings.extend(rows)
    todo = [f for f in filings
            if f["accession"] not in done and failed.get(f["accession"], 0) < MAX_RETRIES]
    log(f"{len(filings)} filings in window, {len(todo)} new")
    if not todo:
        return {"filings": len(filings), "new": 0, "parsed": 0, "failed": 0}

    log("resolving document names (full-text search)")
    docs = efts_documents(start, end, forms)
    missing = [f for f in todo if f["accession"] not in docs]
    log(f"  {len(todo) - len(missing)} from search, {len(missing)} via index.json")
    for f in missing:
        d = index_json_document(f["cik"], f["accession"])
        if d:
            docs[f["accession"]] = d
    for f in todo:
        d = docs.get(f["accession"])
        f["url"] = doc_url(f["cik"], f["accession"], d) if d else None
    todo = [f for f in todo if f["url"]]

    log(f"parsing {len(todo)} documents with {workers} workers")
    recs, t0, n = [], time.time(), 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process, f, use_fee): f for f in todo}
        for fut in cf.as_completed(futs):
            rec, err = fut.result()
            f = futs[fut]
            if rec:
                recs.append(rec)
                failed.pop(f["accession"], None)
            else:
                failed[f["accession"]] = failed.get(f["accession"], 0) + 1
            n += 1
            if n % 100 == 0:
                rate = n / max(1e-9, time.time() - t0)
                log(f"  {n}/{len(todo)}  {rate:.1f} doc/s")

    if recs:
        new = pd.DataFrame(recs)
        store = pd.concat([store, new], ignore_index=True)
        store = store.drop_duplicates("accession", keep="last")
        save_store(store)
    save_failed(failed)
    log(f"done: {len(recs)} parsed, {len(todo) - len(recs)} failed, "
        f"store now {len(store)} filings")
    return {"filings": len(filings), "new": len(todo), "parsed": len(recs),
            "failed": len(todo) - len(recs)}


def previous_business_days(n: int, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    end = today - timedelta(days=1)
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    start, k = end, 1
    while k < n:
        start -= timedelta(days=1)
        if start.weekday() < 5:
            k += 1
    return start, end


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--days-back", type=int, default=None,
                    help="crawl the last N business days (nightly mode)")
    ap.add_argument("--forms", default="424B2")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--fee-fallback", action="store_true",
                    help="query the EX-FILING FEES exhibit when the cover has no total (slow)")
    args = ap.parse_args()

    ua = os.environ.get("EDGAR_UA", "")
    if not ua:
        sys.exit("Set EDGAR_UA to 'Your Name your@email' — EDGAR rejects generic agents.")
    en.UA = ua

    if args.days_back:
        start, end = previous_business_days(args.days_back)
    elif args.start:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)
    else:
        sys.exit("give --start/--end or --days-back")

    forms = {f.strip().upper() for f in args.forms.split(",")}
    crawl(start, end, forms, args.workers, args.fee_fallback)


if __name__ == "__main__":
    main()
