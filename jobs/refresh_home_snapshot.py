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

    _warm_overlay_history()
    _warm_bank_sector_history()
    _warm_feed_insider_aggregate(tickers)
    _warm_rates_bundle()
    return 0


def _warm_rates_bundle() -> None:
    """Warm the Rates · Credit anchor bundle (home_rates_full_snap) off the render
    path: ~25 daily-FRED series, each one fetch_series → {level,d1,w1,m1,ytd,lo,
    hi}. The board then reads one cache row instead of fanning out those FRED
    fetches on a request. Writes the served_snapshot shape (cached_at + value)."""
    try:
        from data import cache
        from data.live_rates import build_rates_anchor_bundle
        bundle = build_rates_anchor_bundle()
        got = sum(1 for v in bundle.values() if v)
        cache.put("home_rates_full_snap", {
            "cached_at": datetime.now().isoformat(), "value": bundle})
        print(f"[{time.strftime('%H:%M:%S')}] rates bundle: {got}/{len(bundle)} "
              f"series", flush=True)
    except Exception as e:
        print(f"[home-snap] rates bundle failed: {type(e).__name__}: {e}",
              flush=True)


def _warm_feed_insider_aggregate(tickers: list[str]) -> None:
    """Build the Home feed's universe-wide insider aggregate here, off the render
    path. The feed's _af_feed_items_live then reads ONE cache row instead of
    fanning out a Form-4 GCS read per bank — turning a 15-35s render-thread
    rebuild into a single cache hit. Dedupe by CIK (multi-class names like
    BPOP/BPOPM share one CIK) so a bank's trades don't render twice."""
    try:
        from data.bank_mapping import get_cik
        from data.form4_client import build_open_market_universe_cache
        ciks, seen = {}, set()
        for t in tickers:
            c = get_cik(t)
            if c and c not in seen:
                ciks[t] = c
                seen.add(c)
        n = build_open_market_universe_cache(ciks, days=14, limit=60)
        print(f"[{time.strftime('%H:%M:%S')}] feed insider aggregate: {n} tx "
              f"across {len(ciks)} banks", flush=True)
    except Exception as e:
        print(f"[home-snap] feed insider aggregate failed: "
              f"{type(e).__name__}: {e}", flush=True)


# The above-the-fold overlay chart reads ETF price history cache_only on the
# render thread (a cold/expired cache there used to loop N×15s live FMP calls and
# block the whole Home grid ~84s). Warm every ETF × timeframe the overlay can
# request here, off the request path. Symbols/periods mirror ui/home _AF_ETFS and
# _AF_TF_FETCH (kept literal so this job doesn't import streamlit-heavy ui/home).
# "1W" is the 15-min intraday window the 1D overlay tab reads (the only intraday
# path); the daily windows (3M/6M/1Y/2Y) cover every other timeframe button.
_OVERLAY_ETFS = ["SPY", "QQQ", "DIA", "IWM", "IWO", "IWN", "IJR", "KRE", "KBE",
                 "XLF", "KBWB"]
_OVERLAY_PERIODS = ["1W", "3M", "6M", "1Y", "2Y"]


def _warm_overlay_history() -> None:
    try:
        from data import fmp_client
    except Exception:
        return
    n = 0
    for tk in _OVERLAY_ETFS:
        for p in _OVERLAY_PERIODS:
            try:
                df = fmp_client.get_history(tk, period=p)  # live → persists cache
                if df is not None and not df.empty:
                    n += 1
            except Exception:
                pass
    print(f"[{time.strftime('%H:%M:%S')}] warmed {n} overlay histories "
          f"({len(_OVERLAY_ETFS)}x{len(_OVERLAY_PERIODS)})", flush=True)
    # Same deal for the ETF table's aftermarket column — live on render was ~13s.
    try:
        aq = fmp_client.get_aftermarket_quote_batch(_OVERLAY_ETFS)  # live → cache
        print(f"[{time.strftime('%H:%M:%S')}] warmed {len(aq)} aftermarket quotes",
              flush=True)
    except Exception:
        pass


def _warm_bank_sector_history() -> None:
    """Warm the Market & Macro "Bank Sector" deep-dive histories. That render
    reads get_history cache_only (one fetch per window, sliced client-side), so
    a cold cache shows "no history". The ETF list + the distinct fetch periods
    are imported from data.bank_etf (ETFS × FETCH_PERIODS) so this can't drift
    from what the render reads — adding a window there warms automatically.
    Distinct from _OVERLAY_* above: those are the Home overlay's needs; this
    covers QABA and the 5Y EOD pull the overlay doesn't fetch."""
    try:
        from data import fmp_client
        from data.bank_etf import ETFS, FETCH_PERIODS
    except Exception:
        return
    n = 0
    for e in ETFS:
        for p in FETCH_PERIODS:
            try:
                df = fmp_client.get_history(e["ticker"], period=p)  # live → persists cache
                if df is not None and not df.empty:
                    n += 1
            except Exception:
                pass
    print(f"[{time.strftime('%H:%M:%S')}] warmed {n} bank-sector histories "
          f"({len(ETFS)}x{len(FETCH_PERIODS)})", flush=True)


if __name__ == "__main__":
    sys.exit(main())
