"""Cloud Run Job: keep the Home metrics snapshot (watchlist_metrics_snap) warm.

The Home page reads ``watchlist_metrics_snap`` (full-universe aggregate metrics:
price/size/valuation per bank) to render its Movers / Unusual-Volume tables
instantly. app.py builds that snapshot lazily on a Home load and caches it 6h
(``load_all_data_fast``). So a COLD instance — right after a deploy, or one
scaling up under load — with a stale/absent snapshot pays a 60s+ rebuild on the
first render; during that window the page paints but the native controls aren't
wired yet (they look dead). It also means the Movers can be up to 6h stale.

This job rebuilds + persists the snapshot on a schedule, so cold instances always
serve a fresh one (no inline rebuild) AND the Movers stay as fresh as the
schedule. It mirrors app.py's load_fdic_data / load_sec_data / load_prices +
build path (minus IBKR, which is never present in a job) and writes the SAME
shape ``load_all_data_fast`` expects: {cached_at, n_tickers, metrics}.

Cost: almost all work is cache reads (the per-bank FDIC/SEC caches + the warm
price cache that jobs/refresh_prices keeps fresh) + metric computation; it does
NOT re-fan-out to FMP/SEC each run, so frequency scales compute, not API load.

Exit codes:
  0 — snapshot rebuilt + persisted
  1 — build failed (the page keeps serving the last good snapshot)
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

SNAP_KEY = "watchlist_metrics_snap"


def _load_fdic(tickers: list[str]) -> tuple[dict, dict]:
    """Latest + historical FDIC records — batched cache read, parallel fetch for
    misses. Mirrors app.load_fdic_data (without the @st.cache_data wrapper)."""
    import pandas as pd
    from data import cache, fdic_client
    from data.bank_mapping import get_fdic_cert

    latest: dict = {}
    hist: dict = {}
    certs = {t: get_fdic_cert(t) for t in tickers}
    certs = {t: c for t, c in certs.items() if c}
    batch = cache.get_multi([f"fdic:{t}" for t in certs]
                            + [f"fdic_hist:{t}" for t in certs])
    uncached = {}
    for t, cert in certs.items():
        c = batch.get(f"fdic:{t}")
        h = batch.get(f"fdic_hist:{t}")
        if c and h:
            latest[t] = c
            hist[t] = h
        else:
            uncached[t] = cert
    if uncached:
        for t, df in fdic_client.fetch_multiple_banks_parallel(uncached, limit=4).items():
            if df is None or df.empty:
                continue
            recs = df.to_dict("records")
            hist[t] = recs
            cache.put(f"fdic_hist:{t}", recs)
            row = df.iloc[0].to_dict()
            row = {k: (None if pd.isna(v) else v) for k, v in row.items()}
            cache.put_fdic(t, row)
            latest[t] = row
    return latest, hist


def _load_sec(tickers: list[str]) -> dict:
    """SEC companyfacts per bank — batched cache read, parallel fetch for misses.
    Mirrors app.load_sec_data."""
    from data import cache, sec_client
    from data.bank_mapping import get_cik

    out: dict = {}
    batch = cache.get_multi([f"sec:{t}" for t in tickers])
    uncached = {}
    for t in tickers:
        c = batch.get(f"sec:{t}")
        if c:
            out[t] = c
            continue
        cik = get_cik(t)
        if cik:
            uncached[t] = cik
    if uncached:
        for t, d in sec_client.fetch_multiple_banks_parallel(uncached).items():
            if d:
                cache.put_sec(t, d)
                out[t] = d
    return out


def _load_prices(tickers: list[str]) -> dict:
    """Warm price cache (kept fresh by jobs/refresh_prices) + live FMP for any
    gaps; empties for the rest. Mirrors app.load_prices minus the IBKR branch
    (no Streamlit session in a job)."""
    from data.ibkr_client import get_empty_price

    out: dict = {}
    try:
        from data.price_cache_store import get_prices as get_warm_prices
        out = get_warm_prices(tickers)
    except Exception as e:
        print(f"[home-snap] warm price read failed: {type(e).__name__}: {e}", flush=True)
    missing = [t for t in tickers if t not in out]
    if missing:
        try:
            from data.fmp_client import get_quote_batch, _has_key
            if _has_key():
                fresh = get_quote_batch(missing)
                out.update({t: q for t, q in fresh.items()
                            if q and q.get("price") is not None})
        except Exception as e:
            print(f"[home-snap] FMP fallback failed: {type(e).__name__}: {e}", flush=True)
    return {t: out.get(t) or get_empty_price() for t in tickers}


def main() -> int:
    import warnings
    warnings.filterwarnings("ignore")
    from data import cache
    from data.bank_universe import get_universe_tickers
    from analysis.metrics import build_all_bank_metrics

    t0 = time.time()
    tickers = sorted(get_universe_tickers())
    if not tickers:
        print("[home-snap] no universe tickers available — aborting", flush=True)
        return 1
    print(f"[{time.strftime('%H:%M:%S')}] building Home snapshot for "
          f"{len(tickers)} banks...", flush=True)

    fdic, hist = _load_fdic(tickers)
    sec = _load_sec(tickers)
    prices = _load_prices(tickers)
    metrics = build_all_bank_metrics(tickers, fdic, sec, prices, hist)
    if not metrics:
        print("[home-snap] build produced no metrics — keeping the last good "
              "snapshot", flush=True)
        return 1

    # Same shape app.load_all_data_fast validates (cached_at + n_tickers + metrics);
    # n_tickers must equal len(sorted(get_universe_tickers())) so the Home accepts it.
    cache.put(SNAP_KEY, {
        "cached_at": datetime.now().isoformat(),
        "n_tickers": len(tickers),
        "metrics": metrics,
    })
    print(f"[{time.strftime('%H:%M:%S')}] wrote {SNAP_KEY}: {len(metrics)} banks "
          f"in {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
