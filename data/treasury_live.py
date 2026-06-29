"""Live (~15-min delayed) Treasury yields from CBOE yield indices via yfinance.

FRED's daily constant-maturity series (DGS*) publish with a one-business-day
lag, so the Rates board would show Thursday's curve on Monday morning. These
CBOE yield indices quote the *current session's* yield directly in percent —
verified against the par curve: ``^TNX`` ≈ the 10Y yield (e.g. 4.39 = 4.39%),
NOT 10x — so the board can reflect today intraday.

This is market data, not a primary FRED series; the UI labels it as such. The
2-Year has no CBOE index, so it stays FRED-sourced. Every value is plausibility
-bounded and the whole thing is short-cached + fail-safe: ANY error returns the
last good cache or ``{}`` so callers fall back to FRED cleanly (never blank,
never a wrong number).
"""
from __future__ import annotations

from datetime import datetime

# FRED series id (what the rates board keys on) -> CBOE yield index (Yahoo
# symbol). The index value IS the yield in percent, quoted directly.
_CBOE = {
    "DGS3MO": "^IRX",   # 13-week T-bill discount rate
    "DGS5":   "^FVX",   # 5-year
    "DGS10":  "^TNX",   # 10-year
    "DGS30":  "^TYX",   # 30-year
}
_CACHE_KEY = "treasury_live_cboe:v1"
_TTL_SECONDS = 90  # near-live, but easy on Yahoo's rate limits


def _plausible(y) -> bool:
    """A real Treasury yield in percent — guards against a bad/scaled tick
    (e.g. a 10x index quirk) ever reaching the board."""
    try:
        y = float(y)
    except (TypeError, ValueError):
        return False
    return y == y and 0.0 < y < 25.0


def live_yields() -> dict:
    """{fred_sid: {"yield": float_pct, "asof": datetime}} for the CBOE tenors
    that resolved, or {} if none did / on any failure. Never raises."""
    from data import cache
    from data.freshness import is_fresh

    cached = cache.get(_CACHE_KEY)
    if is_fresh(cached, _TTL_SECONDS) and cached.get("yields") is not None:
        return _decode(cached["yields"])

    out = {}
    try:
        import yfinance as yf
        for sid, sym in _CBOE.items():
            try:
                h = yf.Ticker(sym).history(period="1d", interval="1m")
                if h is None or h.empty:           # off-hours: fall back to dailies
                    h = yf.Ticker(sym).history(period="5d", interval="1d")
                if h is None or h.empty or "Close" not in h:
                    continue
                s = h["Close"].dropna()
                if s.empty or not _plausible(s.iloc[-1]):
                    continue
                out[sid] = {"yield": round(float(s.iloc[-1]), 3),
                            "asof": s.index[-1].to_pydatetime()}
            except Exception:
                continue
    except Exception:
        # yfinance import / network blew up entirely — serve last good, else {}.
        return _decode(cached["yields"]) if (cached and cached.get("yields")) else {}

    if not out:
        return _decode(cached["yields"]) if (cached and cached.get("yields")) else {}

    enc = {k: {"yield": v["yield"], "asof": v["asof"].isoformat()} for k, v in out.items()}
    try:
        cache.put(_CACHE_KEY, {"cached_at": datetime.now().isoformat(), "yields": enc})
    except Exception:
        pass
    return out


def _decode(enc) -> dict:
    out = {}
    for k, v in (enc or {}).items():
        try:
            out[k] = {"yield": v["yield"], "asof": datetime.fromisoformat(v["asof"])}
        except Exception:
            continue
    return out
