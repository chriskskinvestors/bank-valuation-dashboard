"""
Cloud Run Job: warm the price cache.

FMP is rate-capped (~300 req/min, one symbol per call, no batch endpoint),
so loading all ~355 universe prices live on a screen takes ~70s cold. This
job fetches every bank's quote and writes it to the price_cache table, so the
dashboard reads warm prices instantly (bounded staleness = the schedule
interval). Intended to run every ~2 minutes during US market hours via Cloud
Scheduler.

Reads FMP_API_KEY from Secret Manager (mounted in Cloud Run). If FMP isn't
configured the job exits 2 without touching the cache.

Exit codes:
  0  — refreshed ≥90% of the universe
  1  — partial (worth investigating; dashboard still reads last good cache)
  2  — FMP not configured
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    import warnings
    warnings.filterwarnings("ignore")

    from data import fmp_client
    from data.price_cache_store import init_price_cache_schema, upsert_prices
    from data.bank_universe import get_universe_tickers
    from config import DEFAULT_WATCHLIST

    print(f"[{time.strftime('%H:%M:%S')}] price refresh starting", flush=True)

    if not fmp_client._has_key():
        print("⚠ FMP_API_KEY not configured — cannot warm price cache.", flush=True)
        return 2

    tickers = sorted(set(get_universe_tickers()) | set(DEFAULT_WATCHLIST))
    print(f"[{time.strftime('%H:%M:%S')}] fetching {len(tickers)} quotes...", flush=True)

    init_price_cache_schema()

    t0 = time.time()
    quotes = fmp_client.get_quote_batch(tickers)
    n_written = upsert_prices(quotes)
    elapsed = time.time() - t0

    priced = sum(1 for q in quotes.values() if q and q.get("price"))
    print(f"[{time.strftime('%H:%M:%S')}] done in {elapsed:.0f}s — "
          f"{priced}/{len(tickers)} priced, {n_written} rows written", flush=True)

    coverage = n_written / max(1, len(tickers))
    if coverage >= 0.90:
        return 0
    if coverage >= 0.50:
        print(f"⚠ only {coverage*100:.0f}% coverage — FMP throttling or "
              "thin tickers", flush=True)
        return 1
    print(f"⚠ low coverage {coverage*100:.0f}% — check FMP key / rate limit",
          flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
