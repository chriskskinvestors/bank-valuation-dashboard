"""
Macro print board — latest US economic indicator readings for the Market &
Macro "Economy & Calendar" section (docs/HOME-MACRO-PLAN.md §5).

This pairs with data/macro_calendar.py: that module answers *when* the next
prints land; this one answers *what the last print said*. The indicator set
mirrors the calendar's tracked releases (CPI, PCE, jobs, GDP, retail, PPI,
claims) so the two halves of the section read as one.

Source: FRED series, via data.fred_client.fetch_series (CSV download, no key
needed). Every value is computed from the published series — never a guess.
Where a series is missing or too short to compute the basis, the row's
numeric fields are None and the renderer shows n/a.

Bases (how each indicator is reduced to a headline number):
  yoy_pct       year-over-year % change of an index (CPI, PCE, PPI). The
                "prior" column is the *previous period's* YoY reading, so the
                delta shows acceleration/deceleration in pp.
  mom_pct       month-over-month % change of a level (Retail Sales).
  mom_chg_k     month-over-month change in a level already denominated in
                thousands (Nonfarm Payrolls → thousands of jobs added).
  level_pct     the level itself, already a rate (Unemployment, GDP QoQ SAAR);
                delta vs the prior period in pp.
  level_k       the level converted to thousands (Initial Claims, reported in
                persons); delta vs the prior period in thousands.

`favorable` encodes the conventional direction of a *good* move (inflation
down, jobs up, unemployment down, ...) so the renderer can color the delta
without editorializing per-row.
"""

from __future__ import annotations

import pandas as pd

from data.fred_client import fetch_series

# Indicator spec — order is the display order of the print board.
# freq drives the "as of" period label (M=month, Q=quarter, W=week).
INDICATORS = [
    {"key": "cpi",        "label": "CPI",               "series_id": "CPIAUCSL",
     "basis": "yoy_pct",   "freq": "M", "importance": "high",   "favorable": "down"},
    {"key": "core_cpi",   "label": "Core CPI",          "series_id": "CPILFESL",
     "basis": "yoy_pct",   "freq": "M", "importance": "high",   "favorable": "down"},
    {"key": "pce",        "label": "PCE",               "series_id": "PCEPI",
     "basis": "yoy_pct",   "freq": "M", "importance": "high",   "favorable": "down"},
    {"key": "core_pce",   "label": "Core PCE",          "series_id": "PCEPILFE",
     "basis": "yoy_pct",   "freq": "M", "importance": "high",   "favorable": "down"},
    {"key": "nfp",        "label": "Nonfarm Payrolls",  "series_id": "PAYEMS",
     "basis": "mom_chg_k", "freq": "M", "importance": "high",   "favorable": "up"},
    {"key": "unrate",     "label": "Unemployment Rate", "series_id": "UNRATE",
     "basis": "level_pct", "freq": "M", "importance": "high",   "favorable": "down"},
    {"key": "gdp",        "label": "Real GDP (QoQ SAAR)", "series_id": "A191RL1Q225SBEA",
     "basis": "level_pct", "freq": "Q", "importance": "high",   "favorable": "up"},
    {"key": "retail",     "label": "Retail Sales",      "series_id": "RSAFS",
     "basis": "mom_pct",   "freq": "M", "importance": "medium", "favorable": "up"},
    {"key": "ppi",        "label": "PPI (Final Demand)", "series_id": "PPIFIS",
     "basis": "yoy_pct",   "freq": "M", "importance": "medium", "favorable": "down"},
    {"key": "claims",     "label": "Initial Jobless Claims", "series_id": "ICSA",
     "basis": "level_k",   "freq": "W", "importance": "medium", "favorable": "down"},
]


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Sorted, de-nulled (date, value) frame — empty if unusable."""
    if df is None or df.empty or "value" not in df.columns:
        return pd.DataFrame(columns=["date", "value"])
    d = df.dropna(subset=["value"]).sort_values("date").reset_index(drop=True)
    return d


def _yoy_at(d: pd.DataFrame, idx: int) -> float | None:
    """YoY % at row `idx`, found by matching the observation ~12 months prior
    by DATE (robust to gaps), not positional offset. None if unavailable."""
    if idx < 0 or idx >= len(d):
        return None
    cur = d.iloc[idx]
    target = cur["date"] - pd.DateOffset(years=1)
    prior = d[d["date"] <= target]
    if prior.empty:
        return None
    base = float(prior["value"].iloc[-1])
    if base == 0:
        return None
    return (float(cur["value"]) / base - 1.0) * 100.0


def compute_row(df: pd.DataFrame, basis: str) -> dict:
    """Reduce a FRED series to {latest, prior, delta, as_of} for one basis.

    Pure: takes a (date, value) frame, returns plain floats (or None). This is
    the testable core — see tests for hand-computed expectations.
    """
    d = _clean(df)
    out = {"latest": None, "prior": None, "delta": None, "as_of": None}
    if d.empty:
        return out
    out["as_of"] = d["date"].iloc[-1]
    n = len(d)

    if basis == "yoy_pct":
        latest = _yoy_at(d, n - 1)
        prior = _yoy_at(d, n - 2)
    elif basis == "mom_pct":
        if n < 2:
            return out
        prev = float(d["value"].iloc[-2])
        prev2 = float(d["value"].iloc[-3]) if n >= 3 else None
        latest = (float(d["value"].iloc[-1]) / prev - 1.0) * 100.0 if prev else None
        prior = (prev / prev2 - 1.0) * 100.0 if (prev2 and prev2 != 0) else None
    elif basis == "mom_chg_k":
        if n < 2:
            return out
        latest = float(d["value"].iloc[-1]) - float(d["value"].iloc[-2])
        prior = (float(d["value"].iloc[-2]) - float(d["value"].iloc[-3])) if n >= 3 else None
    elif basis == "level_pct":
        latest = float(d["value"].iloc[-1])
        prior = float(d["value"].iloc[-2]) if n >= 2 else None
    elif basis == "level_k":
        latest = float(d["value"].iloc[-1]) / 1000.0
        prior = (float(d["value"].iloc[-2]) / 1000.0) if n >= 2 else None
    else:
        return out

    out["latest"] = latest
    out["prior"] = prior
    if latest is not None and prior is not None:
        out["delta"] = latest - prior
    return out


def get_print_board() -> list[dict]:
    """The full print board: one row per indicator in INDICATORS, each merging
    the spec with computed {latest, prior, delta, as_of}. Rows whose series
    failed to load carry None numerics (renderer shows n/a)."""
    rows = []
    for spec in INDICATORS:
        df = fetch_series(spec["series_id"], years=6)
        computed = compute_row(df, spec["basis"])
        rows.append({**spec, **computed})
    return rows


# Credit-regime thresholds on the ICE BofA US High Yield OAS (percent, i.e.
# bps/100). Bands are conventional risk-condition zones, not a forecast:
# tight spreads = easy conditions (ok), widening = stress (warn/bad). Shared
# by the Credit & Spreads chart shading and the Regime one-glance panel.
CREDIT_REGIME_BANDS = [
    (3.5, "ok",   "Tight"),      # < 350 bps
    (5.0, "ok",   "Normal"),     # 350–500 bps
    (8.0, "warn", "Elevated"),   # 500–800 bps
    (float("inf"), "bad", "Stressed"),  # ≥ 800 bps
]


def credit_regime(hy_oas_pct: float | None) -> dict:
    """Classify the HY OAS level into a labeled credit regime.

    Returns {level: 'ok'|'warn'|'bad'|'na', label, oas_pct}. level drives the
    status dot; label is the band name. None/NaN → 'na' (never a guess)."""
    if hy_oas_pct is None or hy_oas_pct != hy_oas_pct:
        return {"level": "na", "label": "n/a", "oas_pct": None}
    for upper, level, label in CREDIT_REGIME_BANDS:
        if hy_oas_pct < upper:
            return {"level": level, "label": label, "oas_pct": hy_oas_pct}
    # unreachable (last band is inf), kept for total-function clarity
    return {"level": "bad", "label": "Stressed", "oas_pct": hy_oas_pct}


def curve_regime(spread_2y, spread_3m, spread_2y_prior=None) -> dict:
    """Labeled yield-curve state for the Regime panel.

    shape from the 10Y−2Y / 10Y−3M spreads; direction (steepening/flattening)
    from the 2s10s vs its prior reading. Returns {level, shape, direction}.
    None inputs → 'n/a'."""
    if spread_2y is None or spread_3m is None:
        return {"level": "na", "shape": "n/a", "direction": ""}
    if spread_2y < 0 and spread_3m < 0:
        shape, level = "Inverted", "bad"
    elif spread_2y < 0 or spread_3m < 0:
        shape, level = "Partially inverted", "warn"
    elif spread_2y > 0.5:
        shape, level = "Steep", "ok"
    else:
        shape, level = "Flat-to-normal", "ok"

    direction = ""
    if spread_2y_prior is not None:
        d = spread_2y - spread_2y_prior
        direction = "steepening" if d > 0.05 else ("flattening" if d < -0.05 else "stable")
    return {"level": level, "shape": shape, "direction": direction}


def fed_path(ff_now, ff_prior) -> dict:
    """Fed policy direction from the change in the effective funds rate over
    the lookback. Returns {level, direction, change}. None now → 'n/a'."""
    if ff_now is None:
        return {"level": "na", "direction": "n/a", "change": None}
    if ff_prior is None:
        return {"level": "ok", "direction": "—", "change": None}
    change = ff_now - ff_prior
    if change < -0.10:
        return {"level": "ok", "direction": "Easing", "change": change}
    if change > 0.10:
        return {"level": "warn", "direction": "Tightening", "change": change}
    return {"level": "ok", "direction": "On hold", "change": change}


def to_yoy(df: pd.DataFrame) -> pd.DataFrame:
    """(date, value) index series → (date, value) YoY-% series, date-aligned.
    Used by the inflation chart. Empty frame in → empty frame out."""
    d = _clean(df)
    if d.empty:
        return d
    yoy = []
    for i in range(len(d)):
        yoy.append(_yoy_at(d, i))
    res = pd.DataFrame({"date": d["date"], "value": yoy}).dropna(subset=["value"])
    return res.reset_index(drop=True)


def to_mom_change(df: pd.DataFrame) -> pd.DataFrame:
    """(date, value) level series → (date, value) month-over-month *change*
    series (same units as the level). Used for the payrolls bars."""
    d = _clean(df)
    if d.empty:
        return d
    d = d.copy()
    d["value"] = d["value"].diff()
    return d.dropna(subset=["value"]).reset_index(drop=True)
