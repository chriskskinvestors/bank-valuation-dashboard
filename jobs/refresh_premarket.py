"""
Cloud Run Job: warm the pre-market move cache.

During the pre-market window (4:00–9:30 a.m. ET) the regular-session price job
doesn't run, so the warm cache holds only yesterday's close. This job fetches
aftermarket (extended-hours) quotes for the bank universe, computes each bank's
pre-market move vs its last regular close, and persists {ticker: pct} so the
Home Movers pane can show pre-market gappers. The pane reads the blob cache-only.

Schedule: every ~3–5 min during 4:00–9:30 a.m. ET on weekdays (Cloud Scheduler;
trigger configured outside the repo). Outside that window the cache simply goes
stale and the Home panes stop showing pre-market data (gated on is_premarket()).

Moves come from data.fmp_client.aftermarket_move, which returns None on a wide /
one-sided / missing quote — so the cache never carries a fabricated move.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

CACHE_KEY = "premarket_moves:v1"


def main() -> int:
    from data.bank_universe import get_universe_tickers
    from data.price_cache_store import get_prices
    from data import fmp_client
    from data import cache

    tickers = get_universe_tickers()
    if not tickers:
        print("[premarket] no universe tickers; skipping", flush=True)
        return 0

    # Prior regular-session close per bank (the baseline the move is measured
    # against) — read from the warm price cache, no extra network.
    try:
        warm = get_prices(tickers)
    except Exception as e:
        print(f"[premarket] warm price read failed: {type(e).__name__}: {e}",
              flush=True)
        warm = {}

    print(f"[{time.strftime('%H:%M:%S')}] fetching aftermarket quotes for "
          f"{len(tickers)} banks...", flush=True)
    try:
        aftq = fmp_client.get_aftermarket_quote_batch(tickers, max_per_min=270)
    except Exception as e:
        print(f"[premarket] aftermarket batch raised {type(e).__name__}: {e}",
              flush=True)
        return 1

    out: dict[str, float] = {}
    for tk in tickers:
        aq = aftq.get(tk) or {}
        last = (warm.get(tk) or {}).get("price")
        try:
            mv = fmp_client.aftermarket_move(aq.get("bid"), aq.get("ask"), last)
        except Exception:
            mv = None
        if mv is not None:
            out[tk] = round(float(mv), 2)

    try:
        cache.put(CACHE_KEY, {"asof": datetime.now(timezone.utc).isoformat(),
                              "value": out})
    except Exception as e:
        print(f"[premarket] cache write failed: {type(e).__name__}: {e}",
              flush=True)
        return 1

    print(f"[{time.strftime('%H:%M:%S')}] warmed {len(out)} pre-market moves "
          f"(of {len(tickers)} banks)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
