"""Stock Chart sub-tab (Overview section) — SNL plan §12.

Price + volume chart with optional peer overlays. Owner decision (2026-06-12):
KEEP BOTH — this lives under Overview while Price & Trends stays under
Valuation. With peers selected the price pane switches to an indexed
(%-from-window-start) comparison so different price levels are comparable;
volume stays subject-only. Peer options are ordered nearest-by-asset-size
first from the universe cohort.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from plotly.subplots import make_subplots
import plotly.graph_objects as go

from data.bank_mapping import get_name
from ui.chrome import title_bar, ledger
from utils.chart_style import (
    apply_standard_layout,
    COLOR_PRIMARY,
    COLOR_FILL_PRIMARY,
    COLOR_GREY_LIGHT,
)

_PERIODS = ["1M", "3M", "6M", "YTD", "1Y", "2Y", "5Y"]
# Muted-but-distinct peer line colors (subject keeps the navy).
_PEER_COLORS = ["#d97706", "#059669", "#7c3aed", "#dc2626", "#0891b2",
                "#be185d", "#65a30d", "#475569"]


def _indexed_pct(close: pd.Series) -> pd.Series:
    """Series as % change from the first non-null value of the window.
    Empty series in → empty series out; a zero/negative base is bad data
    → empty (never a plausible-wrong index)."""
    s = close.dropna()
    if s.empty:
        return pd.Series(dtype=float)
    base = s.iloc[0]
    if not isinstance(base, (int, float)) or base <= 0:
        return pd.Series(dtype=float)
    return (close / base - 1.0) * 100.0


def _period_stats(df: pd.DataFrame) -> dict:
    """{return_pct, high, low, avg_volume} over the window; keys None when
    the underlying column is missing/empty."""
    out = {"return_pct": None, "high": None, "low": None, "avg_volume": None}
    if df is None or df.empty or "close" not in df.columns:
        return out
    closes = df["close"].dropna()
    if len(closes) >= 2 and closes.iloc[0] > 0:
        out["return_pct"] = (closes.iloc[-1] / closes.iloc[0] - 1.0) * 100.0
    if not closes.empty:
        highs = df["high"].dropna() if "high" in df.columns else closes
        lows = df["low"].dropna() if "low" in df.columns else closes
        out["high"] = float(highs.max()) if not highs.empty else None
        out["low"] = float(lows.min()) if not lows.empty else None
    if "volume" in df.columns:
        vols = df["volume"].dropna()
        if not vols.empty:
            out["avg_volume"] = float(vols.mean())
    return out


def _nearest_size_order(cohort: list[dict], ticker: str) -> list[str]:
    """Universe tickers ordered by |total_assets − subject's|, subject
    excluded. Banks without assets sort last (alphabetical)."""
    me = next((m for m in cohort if m.get("ticker") == ticker), None)
    mine = (me or {}).get("total_assets")
    others = [m for m in cohort if m.get("ticker") and m.get("ticker") != ticker]
    if not isinstance(mine, (int, float)):
        return sorted(m["ticker"] for m in others)
    with_assets = [m for m in others if isinstance(m.get("total_assets"), (int, float))]
    without = sorted(m["ticker"] for m in others
                     if not isinstance(m.get("total_assets"), (int, float)))
    ordered = [m["ticker"] for m in
               sorted(with_assets, key=lambda m: abs(m["total_assets"] - mine))]
    return ordered + without


def render_stock_chart(ticker: str, peer_cohort: list[dict]):
    from data.fmp_client import get_history

    title_bar(f"{get_name(ticker) or ticker} ({ticker})", "Stock Chart")

    ctrl_l, ctrl_r = st.columns([3, 5])
    with ctrl_l:
        period = st.radio("Period", _PERIODS, index=4, horizontal=True,
                          key=f"sc_period_{ticker}")
    with ctrl_r:
        peers = st.multiselect(
            "Peer overlay (indexed % when selected)",
            _nearest_size_order(peer_cohort or [], ticker),
            default=[], max_selections=len(_PEER_COLORS),
            key=f"sc_peers_{ticker}",
            help="Nearest banks by asset size listed first.")

    hist = get_history(ticker, period)
    if hist is None or hist.empty:
        st.info("No price history is available for this ticker from the "
                "market-data provider.")
        return

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.75, 0.25], vertical_spacing=0.05)

    if peers:
        # Indexed comparison: subject bold navy, peers muted.
        fig.add_trace(go.Scatter(
            x=hist["date"], y=_indexed_pct(hist["close"]), name=ticker,
            line=dict(color=COLOR_PRIMARY, width=2.4), mode="lines"), row=1, col=1)
        missing = []
        for i, p in enumerate(peers):
            ph = get_history(p, period)
            if ph is None or ph.empty:
                missing.append(p)
                continue
            fig.add_trace(go.Scatter(
                x=ph["date"], y=_indexed_pct(ph["close"]), name=p,
                line=dict(color=_PEER_COLORS[i % len(_PEER_COLORS)], width=1.4),
                mode="lines"), row=1, col=1)
        fig.update_yaxes(ticksuffix="%", row=1, col=1)
        if missing:
            st.caption("No history for: " + ", ".join(missing))
    else:
        fig.add_trace(go.Scatter(
            x=hist["date"], y=hist["close"], name=ticker,
            line=dict(color=COLOR_PRIMARY, width=2), mode="lines",
            fill="tozeroy", fillcolor=COLOR_FILL_PRIMARY), row=1, col=1)
        closes = hist["close"].dropna()
        if not closes.empty:
            lo, hi = float(closes.min()), float(closes.max())
            pad = (hi - lo) * 0.08 or hi * 0.02
            fig.update_yaxes(range=[lo - pad, hi + pad], tickprefix="$", row=1, col=1)

    if "volume" in hist.columns:
        fig.add_trace(go.Bar(
            x=hist["date"], y=hist["volume"], name="Volume",
            marker_color=COLOR_GREY_LIGHT, showlegend=False), row=2, col=1)

    apply_standard_layout(fig, height=430)
    chart_col, stats_col = st.columns([7, 3])
    with chart_col:
        st.plotly_chart(fig, use_container_width=True,
                        key=f"sc_chart_{ticker}_{period}")
    with stats_col:
        s = _period_stats(hist)
        ledger(f"{period} Statistics", [
            ("Return", f"{s['return_pct']:+.1f}%" if s["return_pct"] is not None else "n/a"),
            ("High", f"${s['high']:,.2f}" if s["high"] is not None else "n/a"),
            ("Low", f"${s['low']:,.2f}" if s["low"] is not None else "n/a"),
            ("Avg volume", f"{s['avg_volume']:,.0f}" if s["avg_volume"] is not None else "n/a"),
        ])
        if peers:
            st.caption("Price pane is indexed to 0% at the window start; "
                       "volume is the subject bank only.")
