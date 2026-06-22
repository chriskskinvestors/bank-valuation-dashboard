"""
Bank-sector ETF deep-dive data for the Market & Macro "Bank Sector" section
(docs/HOME-MACRO-PLAN.md §2 — owner chose the single-ETF deep-dive lens).

Source: FMP end-of-day price history via data.fmp_client.get_history. Only
the EOD-backed windows (3M/1Y/5Y) are offered — the FMP Starter plan denies
the intraday historical-chart endpoints behind 1W/1M (see the FMP-Starter
memory). No key → get_history returns an empty frame and the renderer shows
an honest note (never fabricated prices).

The reducers (compute_stats, drawdown_series) are pure and unit-tested on
synthetic OHLCV; the live render is verified in production where the key is
mounted, since this environment has no FMP key and prod is IAP-gated.
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

# Selectable windows. All are served from EOD daily bars (sliced client-side
# from one fetch) so none depends on FMP's intraday historical-chart
# endpoints, which the Starter plan denies (see the FMP-Starter memory).
PERIODS = ["1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y"]


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


def get_etf_history(ticker: str, period: str = "1Y") -> pd.DataFrame:
    """Cleaned (date, close, volume) EOD frame for `ticker`, sliced to
    `period`, ascending by date. Empty frame when FMP has no key or the
    fetch fails.

    One EOD fetch serves every window: pull 5Y for the long windows and 1Y
    for the rest (1Y is the proven-working EOD call), then slice by date —
    so adding short windows never touches the plan-denied intraday endpoints.
    """
    base = "5Y" if period in ("3Y", "5Y") else "1Y"
    df = get_history(ticker, period=base)
    if df is None or df.empty or "close" not in df.columns:
        return pd.DataFrame(columns=["date", "close", "volume"])
    cols = ["date", "close"] + (["volume"] if "volume" in df.columns else [])
    d = df[cols].copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    if "volume" in d.columns:
        d["volume"] = pd.to_numeric(d["volume"], errors="coerce")
    d = d.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    if d.empty:
        return d
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


def compute_stats(df: pd.DataFrame) -> dict:
    """Headline deep-dive stats over the window. All None when unusable.

    Returns: last, last_date, period_return_pct, period_high, period_high_date,
    period_low, drawdown_from_high_pct, avg_volume.
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
    hi_idx = int(d["close"].idxmax())
    period_high = float(d["close"].iloc[hi_idx])
    period_low = float(d["close"].min())

    out["last"] = last
    out["last_date"] = d["date"].iloc[-1]
    out["period_return_pct"] = (last / first - 1.0) * 100.0 if first else None
    out["period_high"] = period_high
    out["period_high_date"] = d["date"].iloc[hi_idx]
    out["period_low"] = period_low
    out["drawdown_from_high_pct"] = (last / period_high - 1.0) * 100.0 if period_high else None
    if "volume" in d.columns:
        vol = d["volume"].dropna()
        out["avg_volume"] = float(vol.mean()) if not vol.empty else None
    return out
