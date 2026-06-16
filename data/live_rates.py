"""
Live(ish) Treasury yields — the CNBC-style intraday curve.

FRED's DGS series are daily (≈1 business-day lag); for intraday movement we
pull the yield-quoted instruments off yfinance:
  • CBOE yield indices  ^IRX (3M) · ^FVX (5Y) · ^TNX (10Y) · ^TYX (30Y)
  • CME 2-Year *yield* future  2YY=F  (quoted directly in yield — no
    note-future → yield conversion needed)
All are already in percent (e.g. ^TNX = 4.447 → 4.447%), so no scaling.

This is yfinance "market data" (≈15-min delayed) — labeled as such per the
provenance rules, distinct from the authoritative daily FRED series used for
the rest of the curve, Fed Funds, and the credit OAS spreads. A short-TTL
cross-instance snapshot keeps the Home render fast; the refresh-live-yields
job warms it during market hours. Any tenor that fails to resolve returns
None so the caller falls back to FRED daily — never a guessed yield.
"""
from __future__ import annotations

import time

_SNAP_KEY = "home_live_yields_snap"

# tenor → yfinance symbol (all yield-quoted, in percent)
LIVE_YIELD_SYMBOLS = {
    "3M": "^IRX", "2Y": "2YY=F", "5Y": "^FVX", "10Y": "^TNX", "30Y": "^TYX",
}


def _fetch_one(sym: str):
    """(intraday_level, prior_close, ~1wk_ago_close) for a yield symbol, or
    None on any failure. Level is the latest intraday 1-min bar when
    available, else the latest daily close; the change baselines come from
    the daily series."""
    try:
        import yfinance as yf
        t = yf.Ticker(sym)
        daily = t.history(period="10d", interval="1d")
        if daily is None or daily.empty or "Close" not in daily:
            return None
        closes = daily["Close"].dropna().tolist()
        if not closes:
            return None
        level = float(closes[-1])
        prior = float(closes[-2]) if len(closes) >= 2 else None
        wk = float(closes[-6]) if len(closes) >= 6 else float(closes[0])
        try:
            intr = t.history(period="1d", interval="1m")
            if intr is not None and not intr.empty:
                iv = intr["Close"].dropna()
                if not iv.empty:
                    level = float(iv.iloc[-1])
        except Exception:
            pass
        return (level, prior, wk)
    except Exception:
        return None


def _build() -> dict:
    return {ten: _fetch_one(sym) for ten, sym in LIVE_YIELD_SYMBOLS.items()}


def live_yields(max_age_s: int = 600) -> dict:
    """{tenor: [level, prior_close, ~1wk_close]} from the job-warmed snapshot.

    READ-ONLY — never builds at render time (a cold yfinance fetch would
    block the whole Home grid; same trap as the FRED bundle). The
    refresh_live_yields job populates it during market hours; if it's
    absent or older than max_age_s, returns {} so the rates pane falls back
    to FRED daily (never a stale yield shown as live). JSON round-trip turns
    the tuples into lists — callers index, don't unpack."""
    from data import cache
    snap = cache.get(_SNAP_KEY)
    if not snap:
        return {}
    ts = snap.get("_ts")
    if not ts or (time.time() - float(ts)) > max_age_s:
        return {}
    return snap.get("_v") or {}


def refresh() -> dict:
    """Build the live curve from yfinance and persist it (called by the
    refresh_live_yields job — the ONLY place the network fetch happens)."""
    from data import cache
    data = _build()
    cache.put(_SNAP_KEY, {"_ts": time.time(), "_v": data})
    return data
