"""
Plotly charts for the bank detail page.
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import METRICS_BY_KEY


CHART_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#e0e0e0", size=12),
    margin=dict(l=50, r=20, t=40, b=40),
    xaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
    yaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
)


def price_chart(df: pd.DataFrame, ticker: str) -> go.Figure:
    """Create a candlestick/line price chart from IBKR historical data."""
    if df.empty:
        fig = go.Figure()
        fig.update_layout(
            title=f"{ticker} — No price data available",
            **CHART_LAYOUT,
        )
        return fig

    if all(col in df.columns for col in ["open", "high", "low", "close"]):
        fig = go.Figure(data=[
            go.Candlestick(
                x=df["date"],
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                increasing_line_color="#00c853",
                decreasing_line_color="#ff1744",
                name=ticker,
            )
        ])
    else:
        fig = go.Figure(data=[
            go.Scatter(
                x=df["date"],
                y=df["close"],
                mode="lines",
                line=dict(color="#64b5f6", width=2),
                name=ticker,
            )
        ])

    fig.update_layout(
        title=f"{ticker} Price",
        xaxis_title="Date",
        yaxis_title="Price ($)",
        xaxis_rangeslider_visible=False,
        **CHART_LAYOUT,
    )
    return fig


def metrics_trend_chart(
    fdic_df: pd.DataFrame,
    metric_keys: list[str],
    title: str = "Key Metrics Over Time",
) -> go.Figure:
    """Plot FDIC metrics over time (quarterly)."""
    if fdic_df.empty:
        fig = go.Figure()
        fig.update_layout(title=title, **CHART_LAYOUT)
        return fig

    fig = go.Figure()

    colors = ["#64b5f6", "#00c853", "#ffd600", "#ff1744", "#ce93d8", "#4dd0e1"]

    for i, key in enumerate(metric_keys):
        m = METRICS_BY_KEY.get(key)
        if not m:
            continue
        field = m.get("fdic_field")
        if field and field in fdic_df.columns:
            fig.add_trace(go.Scatter(
                x=fdic_df["REPDTE"],
                y=fdic_df[field],
                mode="lines+markers",
                name=m["label"],
                line=dict(color=colors[i % len(colors)], width=2),
                marker=dict(size=5),
            ))

    fig.update_layout(
        title=title,
        xaxis_title="Quarter",
        yaxis_title="Value (%)",
        legend=dict(orientation="h", y=-0.2),
        **CHART_LAYOUT,
    )
    return fig


def peer_radar_chart(radar_data: dict) -> go.Figure:
    """Create a radar/spider chart comparing banks across metrics."""
    categories = radar_data.get("categories", [])
    series = radar_data.get("series", [])

    if not categories or not series:
        fig = go.Figure()
        fig.update_layout(title="Peer Comparison — No data", **CHART_LAYOUT)
        return fig

    fig = go.Figure()
    colors = ["#64b5f6", "#00c853", "#ffd600", "#ff1744", "#ce93d8", "#4dd0e1"]

    for i, s in enumerate(series):
        values = s["values"] + [s["values"][0]]  # close the polygon
        cats = categories + [categories[0]]
        fig.add_trace(go.Scatterpolar(
            r=values,
            theta=cats,
            fill="toself",
            name=s["name"],
            line=dict(color=colors[i % len(colors)]),
            opacity=0.6,
        ))

    fig.update_layout(
        title="Peer Comparison (Percentile Rank)",
        polar=dict(
            bgcolor="rgba(0,0,0,0)",
            radialaxis=dict(visible=True, range=[0, 100], gridcolor="rgba(255,255,255,0.1)"),
            angularaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
        ),
        **CHART_LAYOUT,
    )
    return fig


def balance_sheet_chart(fdic_df: pd.DataFrame) -> go.Figure:
    """Stacked bar chart of assets, loans, deposits over time."""
    if fdic_df.empty:
        fig = go.Figure()
        fig.update_layout(title="Balance Sheet", **CHART_LAYOUT)
        return fig

    fig = go.Figure()

    if "ASSET" in fdic_df.columns:
        fig.add_trace(go.Bar(
            x=fdic_df["REPDTE"],
            y=fdic_df["ASSET"] * 1000 / 1e9,  # thousands to billions
            name="Total Assets",
            marker_color="#64b5f6",
        ))
    if "LNLSNET" in fdic_df.columns:
        fig.add_trace(go.Bar(
            x=fdic_df["REPDTE"],
            y=fdic_df["LNLSNET"] * 1000 / 1e9,
            name="Net Loans",
            marker_color="#00c853",
        ))
    if "DEP" in fdic_df.columns:
        fig.add_trace(go.Bar(
            x=fdic_df["REPDTE"],
            y=fdic_df["DEP"] * 1000 / 1e9,
            name="Total Deposits",
            marker_color="#ffd600",
        ))

    fig.update_layout(
        title="Balance Sheet Trend ($B)",
        xaxis_title="Quarter",
        yaxis_title="$ Billions",
        barmode="group",
        legend=dict(orientation="h", y=-0.2),
        **CHART_LAYOUT,
    )
    return fig
