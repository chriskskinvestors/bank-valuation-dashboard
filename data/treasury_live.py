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

from datetime import datetime, timezone

# FRED series id (what the rates board keys on) -> CBOE yield index (Yahoo
# symbol). The index value IS the yield in percent, quoted directly.
_CBOE = {
    "DGS3MO": "^IRX",   # 13-week T-bill discount rate
    "DGS5":   "^FVX",   # 5-year
    "DGS10":  "^TNX",   # 10-year
    "DGS30":  "^TYX",   # 30-year
}
# data/live_rates snapshot tenor -> FRED series id (what the rates board keys
# on). The snapshot's symbols are exactly _CBOE's.
_TENOR_TO_SID = {"3M": "DGS3MO", "5Y": "DGS5", "10Y": "DGS10", "30Y": "DGS30"}
_SNAP_MAX_AGE_S = 600  # same freshness bar as Home's rates pane


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
    in the job-warmed live snapshot, or {} if there is none / it is stale.
    Never raises, never fetches.

    READ-ONLY at render (UX review P1-05, 2026-09-30): this used to call Yahoo
    four times, serially, on the page-render thread whenever its 90 s cache had
    lapsed — the Market & Macro › Rates tab took ~36 s warm. The same four
    CBOE indices are already fetched by the refresh-live-yields job into
    data/live_rates' snapshot (jobs build, renders read), so read that. A
    missing/stale snapshot (off-hours, job down) returns {} and the board
    falls back to FRED daily — a stale yield is never shown as live."""
    try:
        import time
        from data import cache
        from data.live_rates import _SNAP_KEY
        snap = cache.get(_SNAP_KEY)
        if not snap:
            return {}
        ts = snap.get("_ts")
        if not ts or (time.time() - float(ts)) > _SNAP_MAX_AGE_S:
            return {}
        asof = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        vals = snap.get("_v") or {}
        out = {}
        for tenor, sid in _TENOR_TO_SID.items():
            row = vals.get(tenor)
            level = row[0] if row else None
            if level is not None and _plausible(level):
                out[sid] = {"yield": round(float(level), 3), "asof": asof}
        return out
    except Exception:
        return {}


def _decode(enc) -> dict:
    out = {}
    for k, v in (enc or {}).items():
        try:
            out[k] = {"yield": v["yield"], "asof": datetime.fromisoformat(v["asof"])}
        except Exception:
            continue
    return out
