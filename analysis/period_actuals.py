"""Period-matched reported actuals for the consensus-vs-actual comparison.

A consensus estimate is for a SPECIFIC forward period — a quarter like 2026Q2 or
a full year like 2026. Comparing it to the bank's latest trailing/annualized
snapshot is a category error: a single-quarter EPS estimate held up against a
trailing-twelve-month actual reads as a +284% "beat". Worse, the snapshot exists
even for a quarter that has not happened yet, so a future period showed actuals
at all. Both are cardinal-rule violations (a plausible-wrong number).

`period_actuals(ticker, period)` returns the actual reported figures for THE SAME
period, in the metrics key space, or None when that period has not been reported
yet — so the UI renders n/a instead of a guess.

Scope — deliberately narrow so every number is defensible:
  * Only the dollar income & balance-sheet metrics we can derive cleanly from the
    FDIC call report for an exact period (net income, net interest income,
    noninterest income/expense, provision, deposits, loans, assets). These are
    the same FDIC fields the rest of the platform already displays as the
    reported "actual" for these metrics — we are only making them period-correct.
  * Per-share figures (EPS, TBV/sh), margins (NIM %), and returns (ROAA, ROATCE)
    are intentionally OMITTED. FDIC carries no EPS, EPS is a HoldCo concept while
    the call report is the bank subsidiary, and there is no clean single-quarter
    basis for the annualized ratios. A guessed value there is exactly what the
    cardinal rule forbids, so those metrics stay n/a until period-matched HoldCo
    figures are wired in.
"""
from __future__ import annotations

import re

from analysis.valuation import _infer_quarter, _derive_quarterly_value
from data.loaders import load_fdic_hist

# FDIC YTD income fields → consensus-actual key. For a quarter we derive the
# single-quarter value (YTD differencing); for a year we take the Q4 YTD (the
# full-year figure). FDIC reports thousands, so ×1000 to raw dollars — the basis
# compare_consensus_to_actual expects for $-amounts.
_FLOW_FIELDS = {
    "NETINC": "net_income",
    "NIM": "net_interest_income",   # FDIC 'NIM' field = net interest income ($000)
    "NONII": "nonint_income",
    "NONIX": "nonint_expense",
    "ELNATR": "provision",
}
# FDIC point-in-time balance-sheet fields → key. ×1000 to raw dollars.
_STOCK_FIELDS = {
    "DEP": "total_deposits",
    "LNLSNET": "total_loans",
    "ASSET": "total_assets",
}


def _parse_period(period: str):
    """('Q', year, qtr) for a quarter, ('Y', year, None) for a year, else None.
    Accepts the canonical forms normalize_period() emits (e.g. '2026Q2', '2026')."""
    s = (period or "").strip().upper()
    m = re.fullmatch(r"(\d{4})Q([1-4])", s)
    if m:
        return ("Q", int(m.group(1)), int(m.group(2)))
    m = re.fullmatch(r"(\d{4})", s)
    if m:
        return ("Y", int(m.group(1)), None)
    return None


def _record_year(rec) -> int | None:
    rd = rec.get("REPDTE")
    try:
        return rd.year if hasattr(rd, "year") else int(str(rd)[:4])
    except (TypeError, ValueError):
        return None


def period_actuals(ticker: str, period: str) -> dict | None:
    """Reported actuals for (ticker, period) in the metrics key space, or None if
    the period has not been reported yet. $-amounts are RAW dollars.

    A quarter is "reported" once its call report is in the FDIC history; a year is
    "reported" once that year's Q4 (full-year) call report is in."""
    parsed = _parse_period(period)
    if parsed is None:
        return None
    kind, year, qtr = parsed

    hist = load_fdic_hist(ticker)
    if not hist:
        return None

    # Locate the target record: the exact (year, quarter) for a quarter, or the
    # year's Q4 for an annual period (a year is reported only once Q4 lands).
    target_qtr = qtr if kind == "Q" else 4
    idx = next(
        (i for i, rec in enumerate(hist)
         if _record_year(rec) == year
         and _infer_quarter(rec.get("REPDTE")) == target_qtr),
        None,
    )
    if idx is None:
        return None  # period not reported yet

    out: dict = {}
    for field, key in _FLOW_FIELDS.items():
        # Quarter → single-quarter (YTD differenced); year → Q4 YTD (full year).
        val = (_derive_quarterly_value(field, hist, idx) if kind == "Q"
               else hist[idx].get(field))
        if val is not None:
            out[key] = val * 1000
    for field, key in _STOCK_FIELDS.items():
        val = hist[idx].get(field)
        if val is not None:
            out[key] = val * 1000
    return out or None
