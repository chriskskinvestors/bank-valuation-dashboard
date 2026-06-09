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
    from data.bank_universe import get_universe
    from config import DEFAULT_WATCHLIST

    print(f"[{time.strftime('%H:%M:%S')}] price refresh starting", flush=True)

    if not fmp_client._has_key():
        print("⚠ FMP_API_KEY not configured — cannot warm price cache.", flush=True)
        return 2

    # Use the UNFILTERED base universe (file-based, no API calls). The filtered
    # get_universe_tickers() makes a live FDIC call per ticker (cert_is_active),
    # which rate-limits in a cold job and silently collapses the set to the
    # watchlist. The base set is a superset of what the UI screen requests, so
    # warming it guarantees coverage; dead tickers just return null (skipped).
    tickers = sorted(set(get_universe().keys()) | set(DEFAULT_WATCHLIST))
    print(f"[{time.strftime('%H:%M:%S')}] fetching {len(tickers)} quotes...", flush=True)

    init_price_cache_schema()

    t0 = time.time()
    # Pace under FMP's ~300/min cap so the full-universe burst isn't throttled
    # (an unpaced cold burst loses ~13% of quotes to 429s).
    quotes = fmp_client.get_quote_batch(tickers, max_per_min=270)

    # Also warm FMP's dividend yield (6h-cached, so only actually hits FMP a
    # couple times a day) and fold it into each quote — our XBRL dividend
    # derivation is unreliable, so the dashboard prefers this value.
    funds = fmp_client.get_fundamentals_batch(tickers, max_per_min=270)
    dy_n = 0
    for t, q in quotes.items():
        dy = (funds.get(t) or {}).get("dividend_yield")
        if q is not None and dy is not None:
            q["dividend_yield"] = dy
            dy_n += 1

    n_written = upsert_prices(quotes)
    elapsed = time.time() - t0

    priced = sum(1 for q in quotes.values() if q and q.get("price"))
    print(f"[{time.strftime('%H:%M:%S')}] done in {elapsed:.0f}s — "
          f"{priced}/{len(tickers)} priced, {n_written} rows written, "
          f"{dy_n} with dividend yield", flush=True)

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
