"""
Live data-quality audit against the deployed dashboard.

Runs against the SAME Postgres cache the deployed Cloud Run service uses
(via DATABASE_URL env var). Inventories what's actually populated vs.
what's missing, per-feature:

  • SEC fundamentals (book value, shares, NI, TBV) — coverage %
  • FDIC Call Report (assets, deposits, equity)        — coverage %
  • FRED macro series (Fed Funds, yields, etc.)        — set or empty?
  • Events table (8-K filings)                         — count + latest
  • Validation findings                                — error / warning
                                                         counts in DB

Output: tests/live_audit_report.csv + console summary.

Run locally with the production DATABASE_URL exported, or as a Cloud Run
Job in the same environment as the dashboard. Cloud Run Job version is
preferable so we audit the exact state the users see.
"""

from __future__ import annotations
import csv
import sys
import time
import traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ──────────────────────────────────────────────────────────────────────────
# Per-bank check: pull what's cached, validate, classify
# ──────────────────────────────────────────────────────────────────────────

def audit_one(ticker: str) -> dict:
    import warnings; warnings.filterwarnings("ignore")
    from data import cache, fdic_client, sec_client
    from data.bank_mapping import get_cik, get_fdic_cert
    from analysis.metrics import build_bank_metrics
    from data.validation import validate_bank_metrics, summary as vsummary

    row = {
        "ticker": ticker,
        "cik": None, "cert": None,
        "sec_present": False, "sec_age_days": None,
        "fdic_present": False, "fdic_age_days": None,
        "shares": None, "equity_b": None, "ni_ttm_b": None, "tbvps": None,
        "assets_b": None, "deposits_b": None,
        "n_warnings": 0, "n_errors": 0,
        "status": "OK", "notes": "",
    }

    try:
        cik = get_cik(ticker)
        cert = get_fdic_cert(ticker)
        row["cik"] = cik
        row["cert"] = cert

        # SEC — check cached, else fetch live
        sec = cache.get_sec(ticker)
        if sec:
            row["sec_present"] = True
            age_s = cache.sec_age(ticker) or 0
            row["sec_age_days"] = round(age_s / 86400, 2)
        elif cik:
            try:
                sec = sec_client.get_latest_fundamentals(cik) or {}
                if sec:
                    cache.put_sec(ticker, sec)
                    row["sec_present"] = True
                    row["sec_age_days"] = 0.0
            except Exception as e:
                row["notes"] += f"SEC fetch: {type(e).__name__}; "

        # FDIC
        fdic = cache.get_fdic(ticker)
        fdic_hist = cache.get(f"fdic_hist:{ticker}") or []
        if fdic:
            row["fdic_present"] = True
            age_s = cache.fdic_age(ticker) or 0
            row["fdic_age_days"] = round(age_s / 86400, 2)
        elif cert:
            try:
                df = fdic_client.fetch_financials(cert, limit=8)
                if not df.empty:
                    fdic_hist = df.to_dict("records")
                    fdic = fdic_hist[0]
                    cache.put_fdic(ticker, fdic)
                    cache.put(f"fdic_hist:{ticker}", fdic_hist)
                    row["fdic_present"] = True
                    row["fdic_age_days"] = 0.0
            except Exception as e:
                row["notes"] += f"FDIC fetch: {type(e).__name__}; "

        # Build the metrics that the dashboard would actually show
        if sec or fdic:
            try:
                m = build_bank_metrics(ticker, fdic or {}, sec or {},
                                       {"price": 50}, fdic_hist or [])
                row["shares"] = (sec or {}).get("shares_outstanding")
                row["equity_b"] = (sec or {}).get("book_value_total")
                row["equity_b"] = row["equity_b"] / 1e9 if row["equity_b"] else None
                row["ni_ttm_b"] = (sec or {}).get("net_income")
                row["ni_ttm_b"] = row["ni_ttm_b"] / 1e9 if row["ni_ttm_b"] else None
                row["tbvps"] = (sec or {}).get("tangible_book_value_per_share")
                if fdic:
                    row["assets_b"] = (fdic.get("ASSET") or 0) / 1e6  # FDIC values in thousands
                    row["deposits_b"] = (fdic.get("DEP") or 0) / 1e6

                # Validation
                findings = validate_bank_metrics(m, sec_data=sec, fdic_data=fdic)
                s = vsummary(findings)
                row["n_warnings"] = s["warnings"]
                row["n_errors"] = s["errors"]
            except Exception as e:
                row["status"] = "BUILD_ERROR"
                row["notes"] += f"build: {type(e).__name__}: {str(e)[:80]}; "

        # Classify status
        if not row["sec_present"] and not row["fdic_present"]:
            row["status"] = "NO_DATA"
        elif row["n_errors"] > 0:
            row["status"] = "ERRORS"
        elif row["n_warnings"] > 0 and row["status"] == "OK":
            row["status"] = "WARNINGS"

    except Exception as e:
        row["status"] = "CRASH"
        row["notes"] += f"top: {type(e).__name__}: {str(e)[:120]}"

    return row


def check_macro() -> dict:
    """Verify FRED macro series load. Returns counts of OK / failed series."""
    import warnings; warnings.filterwarnings("ignore")
    from data import fred_client
    series = ["FEDFUNDS", "DGS10", "DGS2", "T10Y2Y", "T10Y3M", "UNRATE", "DGS30", "CPIAUCSL"]
    ok, failed, failures = 0, 0, []
    for s in series:
        try:
            df = fred_client.fetch_series(s)
            if df is not None and not df.empty:
                ok += 1
            else:
                failed += 1
                failures.append(s)
        except Exception as e:
            failed += 1
            failures.append(f"{s}({type(e).__name__})")
    return {"ok": ok, "failed": failed, "total": len(series), "failures": failures}


def check_events() -> dict:
    """How many events ingested? Latest published_at? Sources active?"""
    from sqlalchemy import text
    from data.events.store import _get_engine

    eng = _get_engine()
    with eng.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM events")).scalar() or 0
        by_src = conn.execute(text(
            "SELECT source, COUNT(*) AS n, MAX(published_at) AS latest "
            "FROM events GROUP BY source ORDER BY n DESC"
        )).mappings().all()
        recent_count = conn.execute(text(
            "SELECT COUNT(*) FROM events "
            "WHERE published_at > NOW() - INTERVAL '7 days'"
            if _get_engine().dialect.name == "postgresql"
            else "SELECT COUNT(*) FROM events "
                 "WHERE published_at > datetime('now', '-7 days')"
        )).scalar() or 0
    return {
        "total_events": total,
        "events_last_7d": recent_count,
        "by_source": [dict(r) for r in by_src],
    }


def run():
    import warnings; warnings.filterwarnings("ignore")
    from data.bank_universe import get_universe_tickers
    from config import DEFAULT_WATCHLIST

    universe = sorted(set(get_universe_tickers()) | set(DEFAULT_WATCHLIST))
    print(f"▶ Auditing {len(universe)} banks against live cache...")

    t0 = time.time()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(audit_one, t): t for t in universe}
        done = 0
        for fut in as_completed(futures):
            results.append(fut.result())
            done += 1
            if done % 50 == 0 or done == len(universe):
                print(f"  {done}/{len(universe)} ({time.time()-t0:.0f}s)")

    # Per-bank summary
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    sec_present = sum(1 for r in results if r["sec_present"])
    fdic_present = sum(1 for r in results if r["fdic_present"])
    print()
    print("═" * 72)
    print("PER-BANK COVERAGE")
    print("═" * 72)
    print(f"  SEC data present:   {sec_present}/{len(results)} ({sec_present/len(results)*100:.1f}%)")
    print(f"  FDIC data present:  {fdic_present}/{len(results)} ({fdic_present/len(results)*100:.1f}%)")
    print(f"  Status breakdown:")
    for s, n in sorted(by_status.items()):
        print(f"    {s:<14} {n:>4}")

    # Banks with no data at all — these are the silent failures users will hit
    no_data = [r for r in results if r["status"] == "NO_DATA"]
    if no_data:
        print(f"\n  No-data banks ({len(no_data)}):")
        for r in no_data[:20]:
            print(f"    {r['ticker']:<6} cik={r['cik']} cert={r['cert']} note={r['notes'][:60]}")

    # Banks with build errors
    build_errs = [r for r in results if r["status"] == "BUILD_ERROR"]
    if build_errs:
        print(f"\n  Build errors ({len(build_errs)}):")
        for r in build_errs[:10]:
            print(f"    {r['ticker']:<6} {r['notes'][:100]}")

    # Macro
    macro = check_macro()
    print()
    print("═" * 72)
    print("MACRO (FRED)")
    print("═" * 72)
    print(f"  Series loading: {macro['ok']}/{macro['total']}")
    if macro["failures"]:
        print(f"  Failing: {macro['failures']}")

    # Events
    events = check_events()
    print()
    print("═" * 72)
    print("EVENTS")
    print("═" * 72)
    print(f"  Total events in DB: {events['total_events']}")
    print(f"  Last 7 days:        {events['events_last_7d']}")
    for src in events["by_source"]:
        print(f"    {src['source']:<20} {src['n']:>5}  latest: {src['latest']}")

    # Write CSV
    out = Path(__file__).parent / "live_audit_report.csv"
    if results:
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(sorted(results, key=lambda x: (x["status"], x["ticker"])))
        print(f"\nReport: {out}")


if __name__ == "__main__":
    run()
