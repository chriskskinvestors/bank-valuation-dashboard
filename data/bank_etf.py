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

from data.fmp_client import get_history

# Sector ETFs offered in the deep-dive selector. KRE is the default — the
# most-watched regional-bank proxy.
ETFS = [
    {"ticker": "KRE",  "name": "SPDR S&P Regional Banking ETF"},
    {"ticker": "KBE",  "name": "SPDR S&P Bank ETF"},
    {"ticker": "KBWB", "name": "Invesco KBW Bank ETF"},
    {"ticker": "QABA", "name": "First Trust NASDAQ ABA Community Bank ETF"},
]

# Windows that map to FMP's historical-price-eod/full (plan-allowed).
PERIODS = ["3M", "1Y", "5Y"]


def get_etf_history(ticker: str, period: str = "1Y") -> pd.DataFrame:
    """Cleaned (date, close, volume) EOD frame for `ticker` over `period`,
    ascending by date. Empty frame when FMP has no key or the fetch fails."""
    df = get_history(ticker, period=period)
    if df is None or df.empty or "close" not in df.columns:
        return pd.DataFrame(columns=["date", "close", "volume"])
    cols = ["date", "close"] + (["volume"] if "volume" in df.columns else [])
    d = df[cols].copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    if "volume" in d.columns:
        d["volume"] = pd.to_numeric(d["volume"], errors="coerce")
    d = d.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    return d


def drawdown_series(df: pd.DataFrame) -> pd.DataFrame:
    """(date, value) underwater series: % below the running peak close, ≤ 0.
    Empty in → empty out. Pure — no network."""
    if df is None or df.empty or "close" not in df.columns:
        return pd.DataFrame(columns=["date", "value"])
    d = df.dropna(subset=["close"]).sort_values("date")
    if d.empty:
        return pd.DataFrame(columns=["date", "value"])
    peak = d["close"].cummax()
    dd = (d["close"] / peak - 1.0) * 100.0
    return pd.DataFrame({"date": d["date"].values, "value": dd.values})


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
