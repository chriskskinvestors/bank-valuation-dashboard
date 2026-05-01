"""
Nightly universe refresh.

Runs as a Cloud Run Job triggered by Cloud Scheduler:
  • Walk every bank in the universe
  • Force-refetch SEC fundamentals + FDIC Call Report data
  • Re-populate the Postgres cache
  • Run validation; log any new errors

Effect: every weekday morning at 6am ET, the dashboard cache is
pre-warmed before users arrive. First-request latency drops from
30-60s (cold cache) to <1s (warm cache).

Run locally:
    DATABASE_URL=sqlite:///cache.db python -m jobs.refresh_universe

Run as Cloud Run Job:
    gcloud run jobs deploy refresh-universe --image=$IMG ...
    gcloud run jobs execute refresh-universe
"""

from __future__ import annotations
import os
import sys
import time
import warnings
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

warnings.filterwarnings("ignore")


def refresh_one(ticker: str) -> dict:
    """Refresh a single bank's SEC + FDIC data. Returns a status dict."""
    from data.bank_mapping import get_cik, get_fdic_cert
    from data import sec_client, fdic_client, cache, validation
    from analysis.metrics import build_bank_metrics

    row = {"ticker": ticker, "ok": True, "errors": [], "warnings": []}

    cik = get_cik(ticker)
    cert = get_fdic_cert(ticker)
    if not cik and not cert:
        row["ok"] = False
        row["errors"].append("no_mapping")
        return row

    # Bust the cache for this ticker (forces fresh fetch)
    cache.invalidate(f"sec:{ticker}")
    cache.invalidate(f"fdic:{ticker}")
    cache.invalidate(f"fdic_hist:{ticker}")

    fdic_data, fdic_hist = {}, []
    sec_data = {}
    try:
        if cert:
            df = fdic_client.fetch_financials(cert, limit=8)
            if not df.empty:
                fdic_hist = df.to_dict("records")
                fdic_data = fdic_hist[0]
                cache.put_fdic(ticker, fdic_data)
                cache.put(f"fdic_hist:{ticker}", fdic_hist)
        if cik:
            sec_data = sec_client.get_latest_fundamentals(cik) or {}
            if sec_data:
                cache.put_sec(ticker, sec_data)
    except Exception as e:
        row["errors"].append(f"fetch:{type(e).__name__}:{str(e)[:80]}")

    # Run validation
    try:
        if sec_data or fdic_data:
            metrics = build_bank_metrics(ticker, fdic_data, sec_data, {"price": 50}, fdic_hist)
            findings = validation.validate_bank_metrics(metrics, sec_data=sec_data, fdic_data=fdic_data)
            for f in findings:
                if f.severity == "error":
                    row["errors"].append(f"{f.field}:{f.message[:60]}")
                elif f.severity == "warning":
                    row["warnings"].append(f.field)
    except Exception as e:
        row["errors"].append(f"validate:{type(e).__name__}")

    if row["errors"]:
        row["ok"] = False
    return row


def main():
    from data.bank_universe import get_universe_tickers
    from config import DEFAULT_WATCHLIST

    universe = sorted(set(get_universe_tickers()) | set(DEFAULT_WATCHLIST))
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Refreshing {len(universe)} banks", flush=True)

    t0 = time.time()
    results = []
    workers = int(os.environ.get("REFRESH_WORKERS", "8"))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(refresh_one, t): t for t in universe}
        done = 0
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                results.append({"ticker": futures[fut], "ok": False, "errors": [f"crash:{e}"]})
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(universe)} ({time.time()-t0:.0f}s)", flush=True)

    ok = sum(1 for r in results if r["ok"])
    failed = [r for r in results if not r["ok"]]
    warns = sum(len(r["warnings"]) for r in results)

    elapsed = time.time() - t0
    print()
    print("━" * 60)
    print(f"REFRESH COMPLETE ({elapsed:.0f}s)")
    print(f"  Banks OK:       {ok}/{len(universe)}")
    print(f"  Banks failed:   {len(failed)}")
    print(f"  Total warnings: {warns}")
    print("━" * 60)

    if failed:
        print("\nFailures (first 20):")
        for r in failed[:20]:
            print(f"  {r['ticker']:<6} {' | '.join(r['errors'][:3])[:200]}")

    # Exit code reflects severity
    return 0 if len(failed) < len(universe) * 0.05 else 1  # tolerate <5% failures


if __name__ == "__main__":
    sys.exit(main())
