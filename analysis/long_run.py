"""Long-run context: where today's value sits in the bank's OWN history
(DEEP-HISTORY-PLAN.md Phase 3, owner scope 2026-09-24).

Two inputs, one shape of answer:
  * a multiple's DAILY series (P/TBV, P/E — SEC per-share data, ~2009 →),
  * a fundamental's QUARTERLY call-report series (ROAA, NIM, efficiency,
    TCE/TA, NCO — bank-sub FDIC, 1992 →).

Every number here is a rank or an order statistic of values the dashboard
already shows; nothing is modeled or interpolated. Too few observations →
None (rendered n/a), never a percentile computed on a handful of points.
"""
from __future__ import annotations

import math

import pandas as pd

# A percentile on fewer points than this is noise dressed as precision:
# ~one trading year for a daily multiple, two years of quarters for a
# call-report ratio.
MIN_DAILY_OBS = 250
MIN_QUARTERLY_OBS = 8


def _clean(values) -> list[float]:
    out = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def percentile_rank(history, current) -> float | None:
    """Share of historical observations ≤ `current`, as a percentage.
    [1, 2, 3, 4, 5] with current 4 → 80.0 (4 of 5 at or below). None when
    current is absent or the history is empty."""
    hist = _clean(history)
    try:
        cur = float(current)
    except (TypeError, ValueError):
        return None
    if not hist or not math.isfinite(cur):
        return None
    return 100.0 * sum(1 for v in hist if v <= cur) / len(hist)


def context(history, current, min_obs: int, start=None, end=None) -> dict | None:
    """{current, median, p10, p90, low, high, pct_rank, n, start, end} for a
    series, or None when fewer than `min_obs` usable observations exist or
    `current` is absent. Quantiles are pandas' default (linear) on the
    cleaned history; `start`/`end` are the caller's span labels."""
    hist = _clean(history)
    if len(hist) < min_obs:
        return None
    rank = percentile_rank(hist, current)
    if rank is None:
        return None
    s = pd.Series(hist)
    return {
        "current": float(current),
        "median": float(s.median()),
        "p10": float(s.quantile(0.10)),
        "p90": float(s.quantile(0.90)),
        "low": float(s.min()),
        "high": float(s.max()),
        "pct_rank": rank,
        "n": len(hist),
        "start": start,
        "end": end,
    }


def multiple_context(val: pd.DataFrame, col: str) -> dict | None:
    """Context for a daily multiple column of the valuation series (the
    frame ui.bank_detail.valuation_series builds: one row per trading day
    with `date` and the multiple). Current = the latest day's value; history
    = every day with a value, including today."""
    if val is None or val.empty or col not in val.columns or "date" not in val.columns:
        return None
    d = val.dropna(subset=[col]).sort_values("date")
    if d.empty:
        return None
    return context(d[col].tolist(), d[col].iloc[-1], MIN_DAILY_OBS,
                   start=d["date"].iloc[0], end=d["date"].iloc[-1])


# Quarterly call-report ratios, as the Financial Highlights table shows
# them (FDIC-reported, YTD-annualized where FDIC annualizes).
FUNDAMENTALS = [
    ("roaa", "ROAA", "ROA"),
    ("nim", "Net interest margin", "NIMY"),
    ("efficiency", "Efficiency ratio", "EEFFR"),
    ("tce_ta", "TCE / tangible assets", None),      # computed below
    ("nco", "Net charge-offs / loans", "NTLNLSR"),
]


def _tce_ta(rec: dict) -> float | None:
    """Tangible common equity ÷ tangible assets × 100 — the exact formula
    the highlights table uses (equity − INTAN) ÷ (assets − INTAN). Absent
    equity or assets → None; absent intangibles are treated as zero, as
    there (a bank with no INTAN reported has none)."""
    try:
        eq = float(rec.get("EQTOT")) if rec.get("EQTOT") is not None else None
        asset = float(rec.get("ASSET")) if rec.get("ASSET") is not None else None
    except (TypeError, ValueError):
        return None
    if eq is None or asset is None:
        return None
    try:
        intan = float(rec.get("INTAN") or 0)
    except (TypeError, ValueError):
        intan = 0.0
    ta = asset - intan
    return (eq - intan) / ta * 100 if ta else None


def _value(rec: dict, field: str | None, key: str) -> float | None:
    if key == "tce_ta":
        return _tce_ta(rec)
    v = rec.get(field) if field else None
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def fundamentals_context(hist_records: list[dict]) -> dict[str, dict | None]:
    """{key: context-or-None} for each FUNDAMENTALS entry over the bank's
    stored call-report history (records newest first, any depth). Current =
    the newest record's value (None → n/a for that ratio even if history
    exists); history = every quarter with a value."""
    out: dict = {}
    recs = [r for r in (hist_records or []) if r and r.get("REPDTE")]
    dated = sorted(
        ((pd.to_datetime(r.get("REPDTE"), errors="coerce"), r) for r in recs),
        key=lambda t: (pd.Timestamp.min if pd.isna(t[0]) else t[0]))
    dated = [(d, r) for d, r in dated if not pd.isna(d)]
    for key, _label, field in FUNDAMENTALS:
        if not dated:
            out[key] = None
            continue
        series = [(d, _value(r, field, key)) for d, r in dated]
        live = [(d, v) for d, v in series if v is not None]
        current = series[-1][1]
        if current is None or not live:
            out[key] = None
            continue
        out[key] = context([v for _, v in live], current, MIN_QUARTERLY_OBS,
                           start=live[0][0], end=live[-1][0])
    return out
