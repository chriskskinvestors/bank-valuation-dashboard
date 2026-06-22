"""
Bank-sector ETF deep-dive data for the Market & Macro "Bank Sector" section
(docs/HOME-MACRO-PLAN.md §2 — owner chose the single-ETF deep-dive lens).

Source: FMP price history via data.fmp_client.get_history. Each selectable
window is served by ONE underlying fetch, sliced client-side (see PERIODS /
_fetch_period): the multi-day windows read EOD daily bars (1Y or 5Y); the
short windows (1D/1W) read FMP's 15-min intraday series — one "1W" fetch
covers both (1D = the latest session, 1W = the full week). No key →
get_history returns an empty frame and the renderer shows an honest note
(never fabricated prices).

Render reads cache_only (get_etf_history(..., cache_only=True)) so a cold
cache can't block the request thread on a live FMP call; the background job
jobs/refresh_home_snapshot keeps every ETFS × FETCH_PERIODS history warm.
FETCH_PERIODS is derived from PERIODS, so adding a window updates the warm
contract automatically (see the overlay-render-cache-only-warm memory).

The reducers (compute_stats, window_cutoff, latest_session, parse_market_data)
are pure and unit-tested on synthetic OHLCV; the live render is verified in
production where the key is mounted, since this environment has no FMP key and
prod is IAP-gated.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from data.fmp_client import get_history, _get

# Sector ETFs offered in the deep-dive selector. KRE is the default — the
# most-watched regional-bank proxy.
ETFS = [
    {"ticker": "KRE",  "name": "SPDR S&P Regional Banking ETF"},
    {"ticker": "KBE",  "name": "SPDR S&P Bank ETF"},
    {"ticker": "KBWB", "name": "Invesco KBW Bank ETF"},
    {"ticker": "QABA", "name": "First Trust NASDAQ ABA Community Bank ETF"},
]

# Selectable windows, served from ONE underlying get_history fetch each (sliced
# client-side, see _fetch_period). 1D/1W are 15-min intraday; the rest are EOD
# daily closes. Adding a window here costs no extra render call — its underlying
# fetch is already warmed via FETCH_PERIODS.
PERIODS = ["1D", "1W", "1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y"]

# Underlying get_history fetch that serves each window. Intraday windows (1D/1W)
# read FMP's 15-min "1W" series; multi-year windows read the 5Y EOD pull; every
# other window is sliced from the 1Y EOD pull (the default).
_FETCH_FOR = {"1D": "1W", "1W": "1W", "3Y": "5Y", "5Y": "5Y"}


def _fetch_period(period: str) -> str:
    """The get_history period whose single fetch serves `period` (default 1Y)."""
    return _FETCH_FOR.get(period, "1Y")


# Distinct underlying fetches the cache_only render reads — the WARM CONTRACT.
# jobs/refresh_home_snapshot must keep get_history(ticker, p) cached for every
# ticker in ETFS and every p here, or the render shows "no history". Derived
# from PERIODS so a new window can't silently break the contract.
FETCH_PERIODS = sorted({_fetch_period(p) for p in PERIODS})


def window_cutoff(period: str, last_date) -> pd.Timestamp:
    """Start date for `period` ending at `last_date`. Pure / unit-tested —
    DateOffset months/years (calendar-correct), YTD = Jan 1 of the latest
    observation's year. Unknown period falls back to 1Y."""
    ld = pd.Timestamp(last_date)
    if period == "1M":
        return ld - pd.DateOffset(months=1)
    if period == "3M":
        return ld - pd.DateOffset(months=3)
    if period == "6M":
        return ld - pd.DateOffset(months=6)
    if period == "YTD":
        return pd.Timestamp(year=ld.year, month=1, day=1)
    if period == "3Y":
        return ld - pd.DateOffset(years=3)
    if period == "5Y":
        return ld - pd.DateOffset(years=5)
    return ld - pd.DateOffset(years=1)  # "1Y" + fallback


def latest_session(df: pd.DataFrame) -> pd.DataFrame:
    """Rows on the latest calendar day present in an intraday frame, ascending
    by date. Pure / unit-tested. Mirrors ui.home._af_overlay_1d's session split
    so the 1D window shows one trading session. Empty in → empty out."""
    if df is None or df.empty or "date" not in df.columns:
        return pd.DataFrame(columns=getattr(df, "columns", ["date", "close"]))
    d = df.dropna(subset=["date"]).sort_values("date")
    if d.empty:
        return d.reset_index(drop=True)
    sess_day = d["date"].iloc[-1].normalize()
    return d[d["date"] >= sess_day].reset_index(drop=True)


def _clean_history(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a raw get_history frame to a clean (date, close[, volume]) frame:
    typed, NaN-dropped, ascending by date. Empty frame on unusable input."""
    if df is None or df.empty or "close" not in df.columns:
        return pd.DataFrame(columns=["date", "close", "volume"])
    cols = ["date", "close"] + (["volume"] if "volume" in df.columns else [])
    d = df[cols].copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    if "volume" in d.columns:
        d["volume"] = pd.to_numeric(d["volume"], errors="coerce")
    return d.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)


def get_etf_history(ticker: str, period: str = "1Y",
                    cache_only: bool = False) -> pd.DataFrame:
    """Cleaned (date, close, volume) frame for `ticker`, sliced to `period`,
    ascending by date. Empty frame when FMP has no key, the fetch fails, or
    (cache_only) the cache is cold.

    One fetch serves every window (see _fetch_period): the 15-min "1W" series
    for 1D/1W (1D = latest session, 1W = the full week), the 5Y or 1Y EOD pull
    for the rest — then slice by date. cache_only=True is passed on the render
    path so a cold cache can't block on a live FMP call; the warm job keeps
    FETCH_PERIODS populated.
    """
    df = get_history(ticker, period=_fetch_period(period), cache_only=cache_only)
    d = _clean_history(df)
    if d.empty:
        return d
    if period == "1D":
        return latest_session(d)
    if period == "1W":
        return d  # the full 15-min week, as fetched
    cutoff = window_cutoff(period, d["date"].iloc[-1])
    return d[d["date"] >= cutoff].reset_index(drop=True)


# Field map for the live market-data panel, verified against the FMP Premium
# feed 2026-06-16 (KRE): /quote and /etf/info field names below are the real
# response keys, not guesses.
def parse_market_data(quote_row: dict | None, info_row: dict | None) -> dict:
    """Map an FMP /quote row (+ optional /etf/info row) to the market-data
    panel fields. Pure / unit-tested — every field defaults to None (n/a),
    never a guess. `change_pct` is already a percent (e.g. 0.159 = +0.16%)."""
    out = {k: None for k in (
        "price", "change", "change_pct", "prev_close", "open", "day_low",
        "day_high", "year_low", "year_high", "volume", "market_cap",
        "aum", "nav", "expense_ratio", "avg_volume")}
    r = quote_row or {}
    out.update(
        price=r.get("price"), change=r.get("change"),
        change_pct=r.get("changePercentage"), prev_close=r.get("previousClose"),
        open=r.get("open"), day_low=r.get("dayLow"), day_high=r.get("dayHigh"),
        year_low=r.get("yearLow"), year_high=r.get("yearHigh"),
        volume=r.get("volume"), market_cap=r.get("marketCap"),
    )
    i = info_row or {}
    out.update(
        aum=i.get("assetsUnderManagement"), nav=i.get("nav"),
        expense_ratio=i.get("expenseRatio"), avg_volume=i.get("avgVolume"),
    )
    return out


def _first_row(data) -> dict | None:
    return data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None


@st.cache_data(ttl=600, show_spinner=False)
def get_etf_market_data(ticker: str) -> dict:
    """Market-data snapshot for `ticker` (FMP Premium): /quote for the price
    block + /etf/info for fund fields (AUM, NAV, expense ratio, avg volume).
    All-None shape when FMP has no key or the calls fail — the renderer then
    shows n/a, never a fabricated quote.

    Cached 10 min: without this it fired TWO live FMP calls on EVERY macro
    rerun (every timeframe/ETF toggle), a needless render-thread stall. The
    fund fields are ~static and the quote is an overview, not a trading
    surface, so 10-min staleness is well within tolerance."""
    t = ticker.upper()
    quote_row = _first_row(_get("quote", {"symbol": t}))
    info_row = _first_row(_get("etf/info", {"symbol": t}))
    return parse_market_data(quote_row, info_row)


def compute_stats(df: pd.DataFrame, baseline: float | None = None) -> dict:
    """Headline deep-dive stats over the window. All None when unusable.

    Returns: last, last_date, period_return_pct, period_high, period_high_date,
    period_low, drawdown_from_high_pct, avg_volume.

    `baseline` overrides the return's denominator: the 1D view passes the prior
    session's close so the figure includes the opening gap (the day's full move,
    matching the quote's change %), instead of measuring from the session's open
    (the first plotted bar). None/0 → fall back to the first bar.
    """
    out = {k: None for k in (
        "last", "last_date", "period_return_pct", "period_high",
        "period_high_date", "period_low", "drawdown_from_high_pct", "avg_volume")}
    if df is None or df.empty or "close" not in df.columns:
        return out
    d = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    if d.empty:
        return out

    last = float(d["close"].iloc[-1])
    first = float(d["close"].iloc[0])
    base = float(baseline) if baseline else first
    hi_idx = int(d["close"].idxmax())
    period_high = float(d["close"].iloc[hi_idx])
    period_low = float(d["close"].min())

    out["last"] = last
    out["last_date"] = d["date"].iloc[-1]
    out["period_return_pct"] = (last / base - 1.0) * 100.0 if base else None
    out["period_high"] = period_high
    out["period_high_date"] = d["date"].iloc[hi_idx]
    out["period_low"] = period_low
    out["drawdown_from_high_pct"] = (last / period_high - 1.0) * 100.0 if period_high else None
    if "volume" in d.columns:
        vol = d["volume"].dropna()
        out["avg_volume"] = float(vol.mean()) if not vol.empty else None
    return out
