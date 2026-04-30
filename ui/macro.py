"""
Macro Dashboard — Fed funds, yield curve, credit spreads, unemployment.

Standalone top-level section. Also provides helpers used by Home and NIM pages.
"""

import streamlit as st
import pandas as pd

from data.fred_client import (
    fetch_series, latest_value, get_macro_snapshot, recession_probability, SERIES,
)
from utils.chart_style import apply_standard_layout, CHART_HEIGHT_FULL, CHART_HEIGHT_COMPACT, ALERT_STYLE


def _trend_arrow(df: pd.DataFrame, lookback_days: int = 30) -> str:
    """Return ↑/↓/→ based on trailing trend."""
    if df.empty or len(df) < 2:
        return "→"
    recent = df["value"].tail(lookback_days).dropna()
    if len(recent) < 2:
        return "→"
    change = recent.iloc[-1] - recent.iloc[0]
    if change > 0.05:
        return "↑"
    elif change < -0.05:
        return "↓"
    return "→"


def render_macro_dashboard():
    """Render the standalone Macro section."""
    st.markdown(
        '<div class="dashboard-header">'
        "<h1>🌐 Macro Dashboard</h1>"
        "<p>Fed funds, yield curve, credit spreads, unemployment</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    # ── Recession indicator ────────────────────────────────────────────
    rec = recession_probability()
    level = rec["level"]
    score = rec["score"]

    if level == "high":
        style = ALERT_STYLE["high"]
        icon = "🚨"
        label = f"Elevated recession risk ({score}/100)"
    elif level == "medium":
        style = ALERT_STYLE["medium"]
        icon = "⚠️"
        label = f"Mixed recession signals ({score}/100)"
    else:
        style = ALERT_STYLE["ok"]
        icon = "✅"
        label = f"Low recession signal ({score}/100)"

    factors_html = ""
    if rec["factors"]:
        factors_html = "<br>".join([f"• {f}" for f in rec["factors"]])
    else:
        factors_html = "No recession signals triggered"

    st.markdown(
        f'<div style="{style}">{icon} <strong>{label}</strong><br>'
        f'<span style="font-weight:normal; font-size:0.82rem;">{factors_html}</span></div>',
        unsafe_allow_html=True,
    )

    st.markdown("")

    # ── Key Macro KPIs ─────────────────────────────────────────────────
    snap = get_macro_snapshot()
    ff = snap.get("FEDFUNDS", {}).get("value")
    t2 = snap.get("DGS2", {}).get("value")
    t10 = snap.get("DGS10", {}).get("value")
    t30 = snap.get("DGS30", {}).get("value")
    spread_2y = snap.get("T10Y2Y", {}).get("value")
    spread_3m = snap.get("T10Y3M", {}).get("value")
    mortgage = snap.get("MORTGAGE30US", {}).get("value")
    unemp = snap.get("UNRATE", {}).get("value")
    hy_spread = snap.get("BAMLH0A0HYM2", {}).get("value")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Fed Funds", f"{ff:.2f}%" if ff is not None else "—",
                  delta=snap.get("FEDFUNDS", {}).get("date"))
    with c2:
        st.metric("10Y Treasury", f"{t10:.2f}%" if t10 is not None else "—",
                  delta=f"2Y: {t2:.2f}%" if t2 is not None else None, delta_color="off")
    with c3:
        st.metric("10Y - 2Y Spread", f"{spread_2y:+.2f}pp" if spread_2y is not None else "—",
                  delta="Inverted" if (spread_2y is not None and spread_2y < 0) else "Normal",
                  delta_color="inverse")
    with c4:
        st.metric("30Y Mortgage", f"{mortgage:.2f}%" if mortgage is not None else "—")

    c5, c6, c7, c8 = st.columns(4)
    with c5:
        st.metric("Unemployment", f"{unemp:.1f}%" if unemp is not None else "—")
    with c6:
        st.metric("HY Spread", f"{hy_spread:.2f}%" if hy_spread is not None else "—")
    with c7:
        st.metric("10Y - 3M Spread", f"{spread_3m:+.2f}pp" if spread_3m is not None else "—",
                  delta="Inverted" if (spread_3m is not None and spread_3m < 0) else "Normal",
                  delta_color="inverse")
    with c8:
        st.metric("30Y Treasury", f"{t30:.2f}%" if t30 is not None else "—")

    st.markdown("---")

    # ── Charts ─────────────────────────────────────────────────────────
    try:
        import plotly.graph_objects as go

        # Chart 1: Yield curve (main)
        fig1 = go.Figure()
        rates_series = {
            "FEDFUNDS": ("Fed Funds", "#1a73e8"),
            "DGS2": ("2Y Treasury", "#1b5e20"),
            "DGS10": ("10Y Treasury", "#e65100"),
            "DGS30": ("30Y Treasury", "#6a1b9a"),
            "MORTGAGE30US": ("30Y Mortgage", "#b71c1c"),
        }
        for sid, (label, color) in rates_series.items():
            df = fetch_series(sid, years=3)
            if not df.empty:
                fig1.add_trace(go.Scatter(
                    x=df["date"], y=df["value"],
                    name=label, mode="lines",
                    line=dict(color=color, width=2),
                ))
        apply_standard_layout(fig1, title="Rate History (3Y)", height=CHART_HEIGHT_FULL,
                              yaxis_title="Rate")
        fig1.update_yaxes(ticksuffix="%")
        st.plotly_chart(fig1, use_container_width=True)

        # Charts 2 & 3 side-by-side
        cc1, cc2 = st.columns(2)

        # Chart 2: Yield curve spreads
        fig2 = go.Figure()
        for sid, (label, color) in [
            ("T10Y2Y", ("10Y-2Y", "#1a73e8")),
            ("T10Y3M", ("10Y-3M", "#b71c1c")),
        ]:
            df = fetch_series(sid, years=5)
            if not df.empty:
                fig2.add_trace(go.Scatter(
                    x=df["date"], y=df["value"],
                    name=label, mode="lines",
                    line=dict(color=color, width=2),
                    fill="tozeroy" if sid == "T10Y2Y" else None,
                    fillcolor="rgba(26,115,232,0.08)" if sid == "T10Y2Y" else None,
                ))
        fig2.add_hline(y=0, line_color="#666", line_width=1, line_dash="dash")
        apply_standard_layout(fig2, title="Yield Curve Spreads (5Y)", height=CHART_HEIGHT_COMPACT,
                              yaxis_title="Spread")
        fig2.update_yaxes(ticksuffix="pp")
        with cc1:
            st.plotly_chart(fig2, use_container_width=True)

        # Chart 3: Unemployment + HY spread
        fig3 = go.Figure()
        unemp_df = fetch_series("UNRATE", years=5)
        hy_df = fetch_series("BAMLH0A0HYM2", years=5)
        if not unemp_df.empty:
            fig3.add_trace(go.Scatter(
                x=unemp_df["date"], y=unemp_df["value"],
                name="Unemployment", mode="lines",
                line=dict(color="#e65100", width=2),
                yaxis="y",
            ))
        if not hy_df.empty:
            fig3.add_trace(go.Scatter(
                x=hy_df["date"], y=hy_df["value"],
                name="HY Credit Spread", mode="lines",
                line=dict(color="#b71c1c", width=2, dash="dot"),
                yaxis="y2",
            ))
        apply_standard_layout(fig3, title="Labor & Credit (5Y)", height=CHART_HEIGHT_COMPACT,
                              yaxis_title="Unemployment %")
        fig3.update_layout(
            yaxis=dict(ticksuffix="%"),
            yaxis2=dict(title="HY Spread %", overlaying="y", side="right", ticksuffix="%"),
        )
        with cc2:
            st.plotly_chart(fig3, use_container_width=True)

    except ImportError:
        st.warning("Install plotly to view macro charts.")

    st.markdown("---")
    st.caption(
        "Data from FRED (Federal Reserve Economic Data). Refreshed daily. "
        "Recession score combines 10Y-2Y spread, 10Y-3M spread (NY Fed indicator), "
        "and Sahm Rule proxy on unemployment."
    )
