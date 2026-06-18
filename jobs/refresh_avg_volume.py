"""
Cloud Run Job: refresh each bank's average daily volume.

The Home "Unusual Volume" pane ranks banks by relative volume = today's
volume (warm price cache, refreshed every ~2 min) ÷ average daily volume.
Average volume barely moves intraday, so it does NOT belong in the 2-min
price job — it's a 63-trading-day mean recomputed once nightly here.

Source is the same FMP chart/EOD history the price job falls back to
(works on the Starter plan), so this job has no extra plan dependency.
Writes via price_cache_store.upsert_avg_volumes (UPDATE-only — never
touches price/updated_at, so it can't fabricate price freshness).

Exit codes:
  0  — computed avg volume for ≥80% of priced tickers
  1  — partial (dashboard still reads last good avg_volume)
  2  — FMP not configured
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

AVG_WINDOW = 63  # ~3 trading months, matching ui/bank_detail.py's convention


def _derived(ticker: str) -> dict | None:
    """Nightly history-derived fields, from two FMP calls:
      get_history (EOD):
        avg_volume  — AVG_WINDOW (63d) mean daily volume
        chg_1w      — price % change over the last 5 trading days
        relvol_1w/1m/6m — window mean volume ÷ the 63d avg (unusual-volume
                          over that lookback, for the Volume-pane periods)
      stock-price-change:
        chg_ytd     — FMP's year-anchored % change (read directly)
    Returns only the fields that have enough data; None if no usable history."""
    from data import fmp_client
    try:
        h = fmp_client.get_history(ticker, period="1Y")
        if h is None or h.empty:
            return None
        h = h.sort_values("date")
        out: dict = {}
        if "volume" in h:
            vols = h["volume"].dropna()
            if not vols.empty:
                avg = float(vols.tail(AVG_WINDOW).mean())
                if avg > 0:
                    out["avg_volume"] = avg
                    for col, win in (("relvol_1w", 5), ("relvol_1m", 21),
                                     ("relvol_6m", 126)):
                        w = vols.tail(win)
                        if len(w) >= max(2, win // 2):
                            out[col] = float(w.mean()) / avg
        if "close" in h:
            closes = h["close"].dropna()
            if len(closes) >= 6 and closes.iloc[-6]:
                out["chg_1w"] = (float(closes.iloc[-1]) /
                                 float(closes.iloc[-6]) - 1.0) * 100.0
        # YTD: read FMP's year-anchored figure directly (stock-price-change),
        # not re-derived from EOD history. FMP anchors to the first trading day
        # of the year correctly even when our 1Y window is short or gapped.
        try:
            pc = fmp_client.get_price_change(ticker)
            if pc and pc.get("ytd") is not None:
                out["chg_ytd"] = float(pc["ytd"])
        except (TypeError, ValueError):
            pass
        return out or None
    except Exception:
        return None


def main() -> int:
    import warnings
    warnings.filterwarnings("ignore")
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from data import fmp_client
    from data.price_cache_store import upsert_derived_metrics, init_price_cache_schema
    from data.bank_universe import get_universe
    from config import DEFAULT_WATCHLIST, MARKET_BENCHMARKS

    print(f"[{time.strftime('%H:%M:%S')}] avg-volume refresh starting", flush=True)
    if not fmp_client._has_key():
        print("⚠ FMP_API_KEY not configured — cannot compute avg volume.", flush=True)
        return 2

    init_price_cache_schema()
    tickers = sorted(set(get_universe().keys()) | set(DEFAULT_WATCHLIST)
                     | {t for t, _ in MARKET_BENCHMARKS})
    print(f"[{time.strftime('%H:%M:%S')}] computing {AVG_WINDOW}-day avg volume "
          f"for {len(tickers)} tickers...", flush=True)

    # Pace submission under FMP's ~300/min cap, same discipline as the price job.
    # _derived now makes TWO FMP calls per ticker (history + stock-price-change),
    # so halve the submission rate to keep total requests under the cap.
    interval = 60.0 / 135
    out: dict = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {}
        for t in tickers:
            futures[ex.submit(_derived, t)] = t
            time.sleep(interval)
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                v = fut.result()
            except Exception:
                v = None
            if v:
                out[t] = v

    n_written = upsert_derived_metrics(out)
    elapsed = time.time() - t0
    computed = len(out)
    print(f"[{time.strftime('%H:%M:%S')}] done in {elapsed:.0f}s — computed "
          f"{computed}/{len(tickers)}, wrote {n_written} rows", flush=True)

    coverage = computed / max(1, len(tickers))
    if coverage >= 0.80:
        return 0
    print(f"⚠ low avg-volume coverage {coverage*100:.0f}%", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
