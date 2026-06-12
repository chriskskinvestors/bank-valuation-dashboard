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


def refresh_one(ticker: str, price_data: dict | None = None) -> dict:
    """Refresh a single bank's SEC + FDIC data. Returns a status dict.

    price_data: real {price, close} from FMP so price-dependent metrics
    (P/E, P/TBV, market cap, yields) get validated with real prices. When
    None/absent, those metrics compute as None and are skipped by the range
    checks (no false flags from a synthetic price).
    """
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
            metrics = build_bank_metrics(
                ticker, fdic_data, sec_data,
                price_data or {"price": None}, fdic_hist)
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
    from data.bank_universe import refresh_universe_snapshot
    from config import DEFAULT_WATCHLIST

    # Rebuild the universe snapshot FIRST (live SEC×FDIC fetch + match,
    # ~6-7 min). Interactive processes serve this persisted snapshot — they
    # never pay the live-build cost themselves.
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Rebuilding universe snapshot…", flush=True)
    snapshot = refresh_universe_snapshot()
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Snapshot: {len(snapshot)} banks", flush=True)

    universe = sorted(set(snapshot.keys()) | set(DEFAULT_WATCHLIST))
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Refreshing {len(universe)} banks", flush=True)

    # Batch-fetch real market prices so price-dependent metrics (P/E, P/TBV,
    # market cap, yields) are validated against reality, not a synthetic price.
    # Falls back to empty quotes (price None) when FMP isn't configured.
    prices = {}
    try:
        from data import fmp_client
        prices = fmp_client.get_quote_batch(universe)
        n_priced = sum(1 for q in prices.values() if q and q.get("price"))
        print(f"[{time.strftime('%H:%M:%S')}] Fetched {n_priced}/{len(universe)} "
              "real prices for price-dependent validation", flush=True)
    except Exception as e:
        print(f"[warn] price batch fetch failed ({type(e).__name__}); "
              "price-dependent metrics will be skipped in validation", flush=True)

    t0 = time.time()
    results = []
    workers = int(os.environ.get("REFRESH_WORKERS", "8"))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(refresh_one, t, prices.get(t)): t for t in universe}
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

    # Reconciliation growth gate: a bank that validated cleanly yesterday and
    # fails today is a regression (new wrong-entity join, schema break, source
    # outage) and must fail the job loudly — a stable exception list is
    # tolerated, silent growth is not.
    from data import cache
    current = {r["ticker"]: r["errors"] for r in failed}
    prev = {}
    try:
        prev = (cache.get("nightly_validation_lastrun") or {}).get("failed", {})
    except Exception as e:
        print(f"[warn] could not load previous validation run: {type(e).__name__}")
    new_failures = sorted(set(current) - set(prev))
    resolved = sorted(set(prev) - set(current))
    if new_failures:
        print(f"\nNEW failures vs previous run ({len(new_failures)}):")
        for t in new_failures[:20]:
            print(f"  {t:<6} {' | '.join(current[t][:3])[:200]}")
    if resolved:
        print(f"Resolved since previous run: {', '.join(resolved[:20])}")
    try:
        cache.put("nightly_validation_lastrun", {
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "failed": current,
            "warnings": warns,
            "universe_size": len(universe),
        })
    except Exception as e:
        print(f"[warn] could not persist validation run: {type(e).__name__}")

    # Exit code reflects severity: new regressions or >5% failure rate fail
    # the execution (visible in Cloud Run job history).
    if new_failures:
        return 1
    return 0 if len(failed) < len(universe) * 0.05 else 1


if __name__ == "__main__":
    sys.exit(main())
