"""
Cloud Run Job: warm the price cache.

FMP is rate-capped (~300 req/min, one symbol per call, no batch endpoint),
so loading all ~355 universe prices live on a screen takes ~70s cold. This
job fetches every bank's quote and writes it to the price_cache table, so the
dashboard reads warm prices instantly (bounded staleness = the schedule
interval). Intended to run every ~2 minutes during US market hours via Cloud
Scheduler.

Quote-denial fallback: the FMP Starter plan DENIES the /quote endpoints
(403) but allows the chart/EOD history endpoints. When the quote batch comes
back essentially empty (the plan-denial signature) or raises, the job falls
back to latest EOD closes via fmp_client.get_eod_close_batch and stamps each
cache row with the close's REAL trading date — never now() — so the Home
staleness badge stays honest (EOD data is yesterday's close, shown as such).

Reads FMP_API_KEY from Secret Manager (mounted in Cloud Run). If FMP isn't
configured the job exits 2 without touching the cache.

Exit codes:
  0  — refreshed ≥90% of the universe (either path)
  1  — partial (worth investigating; dashboard still reads last good cache)
  2  — FMP not configured
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Below this fraction of priced quotes, the quote endpoint is treated as
# plan-denied (a healthy run prices ~95%+; a Starter-plan 403 prices ~0%)
# and the job falls back to EOD closes.
QUOTE_DENIAL_COVERAGE = 0.20


def _eod_timestamp(date_str: str, now):
    """Honest updated_at for an EOD close dated `date_str` (YYYY-MM-DD):
    20:00 UTC on the trading date — the 4pm ET close in EDT, one hour BEFORE
    the close in EST — clamped to `now`. Always ≤ the moment the close
    actually existed, so the staleness badge can never overstate freshness."""
    from datetime import datetime, timezone
    ts = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=20, tzinfo=timezone.utc)
    return min(ts, now)


def _write_eod_closes(eod: dict[str, dict]) -> int:
    """Upsert EOD closes, then re-stamp updated_at to each row's real EOD
    close time (upsert_prices stamps NOW(), which would fabricate freshness
    for day-old closes). Rows without a parseable EOD date are dropped
    entirely — never written under a guessed timestamp. Returns rows written.
    """
    from datetime import datetime, timezone
    from sqlalchemy import text
    from data.db import get_engine
    from data.price_cache_store import upsert_prices

    now = datetime.now(timezone.utc)
    writable: dict[str, dict] = {}
    by_ts: dict[object, list[str]] = {}
    for t, q in (eod or {}).items():
        if not q or q.get("price") is None:
            continue
        try:
            ts = _eod_timestamp(q.get("date"), now)
        except (TypeError, ValueError):
            continue
        writable[t] = q
        by_ts.setdefault(ts, []).append(t.upper())

    n_written = upsert_prices(writable)
    if not n_written:
        return 0

    eng = get_engine()
    with eng.begin() as conn:
        for ts, tickers in by_ts.items():
            for i in range(0, len(tickers), 500):
                chunk = tickers[i:i + 500]
                params = {f"t{j}": tk for j, tk in enumerate(chunk)}
                placeholders = ", ".join(f":t{j}" for j in range(len(chunk)))
                # Bind as an ISO string: Postgres casts it to timestamptz,
                # SQLite stores it verbatim in the shape _parse_ts expects.
                params["ts"] = ts.strftime("%Y-%m-%d %H:%M:%S+00")
                conn.execute(text(
                    f"UPDATE price_cache SET updated_at = :ts "
                    f"WHERE ticker IN ({placeholders})"), params)
    return n_written


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
    from config import MARKET_BENCHMARKS
    tickers = sorted(set(get_universe().keys()) | set(DEFAULT_WATCHLIST)
                     | {t for t, _ in MARKET_BENCHMARKS})
    print(f"[{time.strftime('%H:%M:%S')}] fetching {len(tickers)} quotes...", flush=True)

    init_price_cache_schema()

    t0 = time.time()
    # Pace under FMP's ~300/min cap so the full-universe burst isn't throttled
    # (an unpaced cold burst loses ~13% of quotes to 429s).
    path = "quote"
    quotes: dict[str, dict] = {}
    try:
        quotes = fmp_client.get_quote_batch(tickers, max_per_min=270)
    except Exception as e:
        print(f"⚠ quote batch raised {type(e).__name__}: {e}", flush=True)
    priced = sum(1 for q in quotes.values() if q and q.get("price"))

    if priced / max(1, len(tickers)) >= QUOTE_DENIAL_COVERAGE:
        n_written = upsert_prices(quotes)
    else:
        # Plan-denial signature (FMP Starter 403s /quote → ~0% priced).
        # Fall back to EOD closes, stamped with their real trading date.
        print(f"⚠ quote coverage {priced}/{len(tickers)} — plan-denial "
              "signature; falling back to EOD closes", flush=True)
        path = "eod"
        eod = fmp_client.get_eod_close_batch(tickers, max_per_min=270)
        n_written = _write_eod_closes(eod)

    elapsed = time.time() - t0
    coverage = n_written / max(1, len(tickers))
    print(f"[{time.strftime('%H:%M:%S')}] done in {elapsed:.0f}s — warmed via "
          f"{path.upper()} path: {n_written}/{len(tickers)} rows written "
          f"({coverage*100:.0f}% coverage)", flush=True)

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
