"""
Plotly charts for the bank detail page.
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import METRICS_BY_KEY


from utils.chart_style import CHART_LAYOUT  # re-export for any old callers


def price_chart(df: pd.DataFrame, ticker: str) -> go.Figure:
    """A proper price chart: candlesticks (when OHLC is available) + a volume
    panel, taller aspect ratio, gridlines. Falls back to a clean line if only
    close prices exist."""
    from utils.chart_style import apply_standard_layout
    if df is None or df.empty or "close" not in df.columns:
        fig = go.Figure()
        apply_standard_layout(fig, title=f"{ticker} — no price data",
                              height=300, show_legend=False)
        return fig

    d = df.sort_values("date")
    y = d["close"].astype(float)
    first, last = float(y.iloc[0]), float(y.iloc[-1])
    up = last >= first
    color = "#059669" if up else "#dc2626"
    pct = ((last - first) / first * 100) if first else 0.0
    title = (f"{ticker}  ${last:,.2f}  "
             f"<span style='color:{color}'>{pct:+.2f}% over period</span>")

    has_vol = "volume" in d.columns and d["volume"].notna().any()

    if has_vol:
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            vertical_spacing=0.04, row_heights=[0.74, 0.26])
    else:
        fig = make_subplots(rows=1, cols=1)

    # Price: clean area line (green if up over the window, red if down).
    fig.add_trace(go.Scatter(
        x=d["date"], y=y, mode="lines", name=ticker, showlegend=False,
        line=dict(color=color, width=2), fill="tozeroy",
        fillcolor=("rgba(5,150,105,0.07)" if up else "rgba(220,38,38,0.07)"),
        hovertemplate="%{x|%b %d, %Y}<br>$%{y:.2f}<extra></extra>",
    ), row=1, col=1)

    if has_vol:
        ref = d["close"].shift(1).fillna(d["close"])
        vcolors = ["rgba(5,150,105,0.45)" if c >= o else "rgba(220,38,38,0.45)"
                   for o, c in zip(ref, d["close"])]
        fig.add_trace(go.Bar(x=d["date"], y=d["volume"], marker_color=vcolors,
                             name="Volume", showlegend=False), row=2, col=1)
        fig.update_yaxes(showgrid=False, row=2, col=1)

    apply_standard_layout(fig, title=title, height=420, show_legend=False,
                          hovermode="x unified")
    fig.update_xaxes(showgrid=True, gridcolor="rgba(148,163,184,0.12)")
    # Zoom the price y-axis to the data range so the move reads clearly.
    ymin, ymax = float(y.min()), float(y.max())
    pad = (ymax - ymin) * 0.08 or max(ymax * 0.01, 0.5)
    fig.update_yaxes(range=[ymin - pad, ymax + pad], tickprefix="$", showgrid=True,
                     gridcolor="rgba(148,163,184,0.12)", row=1, col=1)
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

    # Determine scale (B or T) from max asset value
    max_usd = 0
    if "ASSET" in fdic_df.columns:
        assets_series = fdic_df["ASSET"] * 1000  # thousands → dollars
        max_usd = assets_series.abs().max() if not assets_series.empty else 0
    scale, unit = (1e12, "T") if max_usd >= 1e12 else (1e9, "B")

    if "ASSET" in fdic_df.columns:
        fig.add_trace(go.Bar(
            x=fdic_df["REPDTE"],
            y=fdic_df["ASSET"] * 1000 / scale,
            name="Total Assets",
            marker_color="#64b5f6",
        ))
    if "LNLSNET" in fdic_df.columns:
        fig.add_trace(go.Bar(
            x=fdic_df["REPDTE"],
            y=fdic_df["LNLSNET"] * 1000 / scale,
            name="Net Loans",
            marker_color="#00c853",
        ))
    if "DEP" in fdic_df.columns:
        fig.add_trace(go.Bar(
            x=fdic_df["REPDTE"],
            y=fdic_df["DEP"] * 1000 / scale,
            name="Total Deposits",
            marker_color="#ffd600",
        ))

    fig.update_layout(
        title=f"Balance Sheet Trend (${unit})",
        xaxis_title="Quarter",
        yaxis_title=f"$ {'Trillions' if unit == 'T' else 'Billions'}",
        barmode="group",
        legend=dict(orientation="h", y=-0.2),
        **CHART_LAYOUT,
    )
    return fig
