"""Agent export — table `valuation_history`: the series behind the Corporate
Profile "P/TBV / P/E" card (ui.bank_detail._valuation_history_chart).

Same code as the chart: inputs go through
analysis.valuation_history.build_valuation_series and the per-quarter
TBV/share comes from ui.financial_highlights._per_share_for_ends — nothing
is re-derived here. What differs is only WHERE the inputs come from: the
chart loads them through its live seams, this export reads persisted caches
only (cache.get(..., max_age_s=None)), never SEC/FMP:

  prices        fmp_history:<T>:<window> — the chart's own get_history cache
                entry. The longest cached EOD window wins (ALL, then 5Y, 3Y,
                2Y, 1Y, 6M, 3M); its label is the `chart_period` column. The
                intraday windows (1W 15-min, 1M hourly bars) are not used.
  quarter-ends  the deep FDIC store (data.fdic_history_store) for ALL, else
                the warm fdic_hist:<T> entry — the read order of
                data.loaders.load_fdic_hist minus its live fallback.
  SEC facts     the persisted slim companyfacts blob. The read-time lag
                overlay (sec_client._overlay) is NOT applied: it reads SEC
                submissions / filing instances live. For a bank whose
                companyfacts lags its newest 10-Q the chart steps in that
                filing and this export does not — its fundamentals are the
                blob's (older, correctly dated by fund_effective_date).

Frequency: the chart is daily; ALL daily for ~600 banks since ~2009 is
~2.5M rows, so the export keeps the month-end trading day, every
fundamentals step day (the first trading day a newly filed TBV/EPS
applies — the chart's jump), the first and the latest trading day. Every
exported row equals the chart's row for that date.
"""
from __future__ import annotations

import pandas as pd

# Cached EOD windows, longest first (see module docstring).
PRICE_WINDOWS = ("ALL", "5Y", "3Y", "2Y", "1Y", "6M", "3M")

COLUMNS_VALUATION_HISTORY: dict[str, str] = {
    "ticker": "Bank ticker.",
    "date": "Trading date (datetime.date) of the price bar.",
    "point_type": "Why this day is exported: start | filing_step (first day new "
                  "fundamentals apply) | month_end (last trading day of the month) | "
                  "latest (last day in the cached price history).",
    "chart_period": "Price window the row came from (ALL/5Y/3Y/2Y/1Y/6M/3M) — the "
                    "Corporate Profile valuation card's timeframe whose cached FMP "
                    "history was available.",
    "close": "Closing price, $/share (FMP historical EOD, market data).",
    "tbv_ps": "Tangible book value per common share in effect that day, $/share. "
              "Reconstructed from SEC XBRL (HoldCo): (parent equity − preferred − "
              "intangibles) / common shares. None = not resolvable at that "
              "balance sheet (never 0).",
    "eps_ttm": "Trailing-twelve-month diluted EPS in effect that day, $/share: sum "
               "of four single-quarter SEC XBRL diluted EPS (Q4 = FY − 9M). None "
               "when any of the four quarters is absent.",
    "ptbv": "Price / tangible book value, x (close / tbv_ps; None when tbv_ps "
            "absent or ≤ 0).",
    "pe": "Price / TTM EPS, x (close / eps_ttm; None when eps_ttm absent or ≤ 0).",
    "fund_period_end": "Quarter-end (FDIC REPDTE anchor, datetime.date) the "
                       "in-effect TBV/EPS belong to.",
    "tbv_balance_sheet_date": "SEC balance-sheet date (datetime.date) the TBV "
                              "equity/intangibles/shares were read at (within 12 "
                              "days of fund_period_end); None when no equity fact.",
    "fund_effective_date": "Date (datetime.date) the in-effect fundamentals "
                           "stepped in — the 10-Q/10-K filing date (no lookahead).",
    "fund_effective_basis": "filed = fund_effective_date is the filing date; "
                            "period_end = no filing date known, the quarter-end "
                            "was used.",
    "fund_age_days": "Days between date and fund_effective_date (staleness of the "
                     "in-effect fundamentals; the chart carries them forward "
                     "until the next filing).",
    "tbv_ps_basis": "Always 'reconstructed_xbrl': the chart's TBV/share is "
                    "reconstructed from SEC companyfacts, never a company-"
                    "reported figure.",
    "tbv_intangibles_basis": "How the intangibles deduction was built at that "
                             "balance sheet (sec_client._intangible_adjustment_at "
                             "basis text).",
    "tbv_shares_source": "Share-count source for tbv_ps (cover-page / year-end, "
                         "or the iXBRL per-class sum).",
    "fund_filing_form": "Form (10-Q/10-K) that first reported the equity behind "
                        "tbv_ps.",
    "fund_filing_accession": "SEC accession number of that filing.",
}


# ── read-only input seams ────────────────────────────────────────────────────
def _cached_price_history(ticker: str) -> tuple[pd.DataFrame | None, str | None]:
    """(frame, window) from the chart's own get_history cache entry, parsed
    the way fmp_client.get_history parses a cache hit; (None, None) on miss."""
    from data import cache
    for window in PRICE_WINDOWS:
        hit = cache.get(f"fmp_history:{ticker.upper()}:{window}", max_age_s=None)
        recs = hit.get("_v") if isinstance(hit, dict) else None
        if not recs:
            continue
        df = pd.DataFrame(recs)
        if "date" not in df.columns or "close" not in df.columns:
            continue
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).reset_index(drop=True)
        if not df.empty:
            return df, window
    return None, None


def _cached_quarter_ends(ticker: str, quarters: int) -> list:
    """FDIC REPDTEs (ascending datetimes) the chart anchors on: the read
    order of data.loaders.load_fdic_hist_df(ticker, quarters) without its
    live FDIC fallback."""
    from data import cache
    recs: list = []
    if max(quarters, 20) > 20:
        try:
            from data.fdic_history_store import deep_group_history
            deep = deep_group_history(ticker, limit=max(quarters, 20))
        except Exception:
            deep = []
        if len(deep) >= min(quarters, 20):
            recs = deep
    if not recs:
        recs = cache.get(f"fdic_hist:{ticker}", max_age_s=None) or []
    fh = pd.DataFrame(recs[:quarters])
    if fh.empty or "REPDTE" not in fh.columns:
        return []
    ds = pd.to_datetime(fh["REPDTE"]).dropna().sort_values()
    return [d.to_pydatetime() for d in ds]


def _cached_facts(cik) -> dict | None:
    """The persisted slim companyfacts blob (no lag overlay — see module
    docstring)."""
    from data import cache, sec_client
    return cache.get(sec_client.company_facts_cache_key(cik), max_age_s=None)


# ── build ────────────────────────────────────────────────────────────────────
def _series_for(ticker: str, info: dict) -> tuple[pd.DataFrame | None, str | None, str]:
    """(chart frame, window, miss reason) — the chart's exact series for the
    longest cached window, built from cached inputs."""
    from analysis.valuation_history import build_valuation_series
    from ui.financial_highlights import _per_share_for_ends
    cik = info.get("cik") if info else None
    cert = info.get("fdic_cert") if info else None
    if not cik:
        return None, None, "no_cik"
    px, window = _cached_price_history(ticker)
    if px is None:
        return None, None, "no_cached_price_history"
    # The chart: ALL anchors on the deep store (MAX = 200 quarters, floor 44),
    # every other window on the 44-quarter card load.
    if not cert:
        return None, window, "no_fdic_cert"
    ends = _cached_quarter_ends(ticker, 200 if window == "ALL" else 44)
    if len(ends) < 4:
        return None, window, "no_cached_fdic_history"
    facts = _cached_facts(cik)
    if not facts:
        return None, window, "no_cached_companyfacts"
    ps = _per_share_for_ends(cik, ends, quarterly=False, facts=facts)
    val = build_valuation_series(ends, ps, facts, px)
    if val is None:
        return None, window, "no_valuation_series"
    return val, window, ""


def _sample(val: pd.DataFrame) -> pd.DataFrame:
    """Month-end + step + first + latest rows of a chart frame, labelled."""
    val = val.sort_values("date").reset_index(drop=True)
    d = val["date"]
    month_end = d.dt.to_period("M") != d.shift(-1).dt.to_period("M")
    step = val["eff_date"].ne(val["eff_date"].shift())
    pt = pd.Series(None, index=val.index, dtype=object)
    pt[month_end] = "month_end"
    pt[step] = "filing_step"
    pt.iloc[0] = "start"
    pt.iloc[-1] = "latest"
    out = val[pt.notna()].copy()
    out["point_type"] = pt[pt.notna()]
    return out


def _to_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce").map(lambda t: None if pd.isna(t) else t.date())


def _none_if_nan(s: pd.Series) -> pd.Series:
    return s.astype(object).where(s.notna(), None)


def build_valuation_history(tickers: list[str]) -> pd.DataFrame:
    """Rows per (ticker, date) — see COLUMNS_VALUATION_HISTORY. Tickers with
    no CIK / cached price history / cached FDIC quarters / cached facts / no
    series are skipped; df.attrs["misses"] counts them and
    df.attrs["miss_reasons"] maps ticker → reason."""
    from data.bank_mapping import get_bank_info
    frames, reasons = [], {}
    for t in tickers:
        try:
            # The card's own info (ui.bank_detail renders it from get_bank_info).
            val, window, why = _series_for(t, get_bank_info(t) or {})
        except Exception as e:  # one bank's failure never sinks the table
            val, window, why = None, None, f"error: {type(e).__name__}: {e}"
        if val is None:
            reasons[t] = why
            continue
        s = _sample(val)
        frames.append(pd.DataFrame({
            "ticker": t,
            "date": _to_date(s["date"]),
            "point_type": s["point_type"],
            "chart_period": window,
            "close": _none_if_nan(s["close"]),
            "tbv_ps": _none_if_nan(s["tbvps"]),
            "eps_ttm": _none_if_nan(s["ttm_eps"]),
            "ptbv": _none_if_nan(s["ptbv"]),
            "pe": _none_if_nan(s["pe"]),
            "fund_period_end": _to_date(s["period_end"]),
            "tbv_balance_sheet_date": _to_date(s["bs_date"]),
            "fund_effective_date": _to_date(s["eff_date"]),
            "fund_effective_basis": s["eff_basis"],
            "fund_age_days": (s["date"] - s["eff_date"]).dt.days,
            "tbv_ps_basis": "reconstructed_xbrl",
            "tbv_intangibles_basis": _none_if_nan(s["adj_basis"]),
            "tbv_shares_source": _none_if_nan(s["sh_src"]),
            "fund_filing_form": _none_if_nan(s["form"]),
            "fund_filing_accession": _none_if_nan(s["accn"]),
        }))
    cols = list(COLUMNS_VALUATION_HISTORY)
    df = (pd.concat(frames, ignore_index=True)[cols] if frames
          else pd.DataFrame(columns=cols))
    df.attrs["misses"] = len(reasons)
    df.attrs["miss_reasons"] = reasons
    return df
