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


# ── "Market & Macro" sections (docs/HOME-MACRO-PLAN.md, user-approved) ──
# Sections-as-data, same principle as ui/company_nav.py: the list drives the
# radio AND the dispatch. Sections marked pending render an honest note —
# their contents are built part-by-part with the user (never placeholders
# pretending to be data).
MACRO_SECTIONS = [
    "Rates & Curve",
    "Bank Sector",
    "Funding & Deposits",
    "Credit & Spreads",
    "Economy & Calendar",
    "Regime",
]


def render_macro_dashboard():
    """Render the standalone Market & Macro section."""
    st.markdown(
        '<div class="dashboard-header">'
        "<h1>Market & Macro</h1>"
        "<p>Rates, curve, bank sector, funding, credit, economy & regime</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    with st.container(key="macro_section_nav"):
        section = st.radio("Section", MACRO_SECTIONS, key="macro_section",
                           horizontal=True, label_visibility="collapsed")

    {
        "Rates & Curve": _render_rates_curve,
        "Bank Sector": _render_bank_sector,
        "Funding & Deposits": _render_funding_deposits,
        "Credit & Spreads": _render_credit_spreads,
        "Economy & Calendar": _render_economy_calendar,
        "Regime": _render_regime,
    }[section]()


def _pending(what: str, lands_with: str):
    """Honest under-construction note — approved section, content pending the
    user's part-by-part talk-through (HOME-MACRO-PLAN.md process)."""
    st.info(f"**{what}** — section approved; contents being built out "
            f"part-by-part. Data layer: {lands_with}.")


def _render_bank_sector():
    _pending("Bank Sector — KRE/KBE vs 2s10s, fed funds and HY OAS overlays",
             "FMP ETF prices (live) + FRED series (live)")


def _render_funding_deposits():
    _pending("Funding & Deposits — FDIC weekly national deposit rates vs fed funds",
             "data/national_rates.py (in build)")


def _render_economy_calendar():
    _pending("Economy & Calendar — FRED prints with upcoming-release calendar",
             "data/macro_calendar.py (in build)")


def _render_regime():
    # ── Recession indicator (re-homed from the old single-page layout) ──
    rec = recession_probability()
    level = rec["level"]
    score = rec["score"]

    if level == "high":
        style = ALERT_STYLE["high"]
        icon = '<span class="ksk-dot bad"></span>'
        label = f"Elevated recession risk ({score}/100)"
    elif level == "medium":
        style = ALERT_STYLE["medium"]
        icon = '<span class="ksk-dot warn"></span>'
        label = f"Mixed recession signals ({score}/100)"
    else:
        style = ALERT_STYLE["ok"]
        icon = '<span class="ksk-dot ok"></span>'
        label = f"Low recession signal ({score}/100)"

    factors_html = ""
    if rec["factors"]:
        factors_html = "<br>".join([f"• {f}" for f in rec["factors"]])
    else:
        factors_html = "No recession signals triggered"

    st.markdown(
        f'<div style="{style}">{icon} <strong>{label}</strong><br>'
        f'<span style="font-weight:normal; font-size:var(--fs-sm);">{factors_html}</span></div>',
        unsafe_allow_html=True,
    )

    # Curve-shape state — labeled regime, not just a chart
    snap = get_macro_snapshot()
    s2 = snap.get("T10Y2Y", {}).get("value")
    s3m = snap.get("T10Y3M", {}).get("value")
    if s2 is not None and s3m is not None:
        if s2 < 0 and s3m < 0:
            shape = "Inverted (both 10Y−2Y and 10Y−3M below zero)"
        elif s2 < 0 or s3m < 0:
            shape = "Partially inverted"
        elif s2 > 0.5:
            shape = "Steep"
        else:
            shape = "Flat-to-normal"
        st.markdown(f"**Curve shape:** {shape} · 10Y−2Y {s2:+.2f}pp · 10Y−3M {s3m:+.2f}pp")
    _pending("Credit and Fed-path regime states",
             "FRED series (live) — definitions per talk-through")


def _render_rates_curve():
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
    # Cohesive treasury palette: short→long maturities run light→dark blue so
    # the family reads as one curve; Fed funds (policy) is slate, non-treasury
    # series get a distinct accent.
    CURVE = {
        "DGS3MO": ("3M", "#93c5fd"),
        "DGS2":   ("2Y", "#60a5fa"),
        "DGS5":   ("5Y", "#3b82f6"),
        "DGS10":  ("10Y", "#2563eb"),
        "DGS30":  ("30Y", "#1e3a8a"),
    }
    import plotly.graph_objects as go

    # ── Chart 1: the actual yield curve — today / 3M ago / 1Y ago ──────
    def _at(df, days_ago):
        if df is None or df.empty:
            return None
        d = df.dropna(subset=["value"]).sort_values("date")
        if d.empty:
            return None
        if days_ago == 0:
            return float(d["value"].iloc[-1])
        cutoff = d["date"].iloc[-1] - pd.Timedelta(days=days_ago)
        prior = d[d["date"] <= cutoff]
        return float(prior["value"].iloc[-1]) if not prior.empty else None

    tenors = [("3M", "DGS3MO"), ("2Y", "DGS2"), ("5Y", "DGS5"),
              ("10Y", "DGS10"), ("30Y", "DGS30")]
    labels, cur_y, m3_y, y1_y = [], [], [], []
    for lbl, sid in tenors:
        d = fetch_series(sid, years=2)
        labels.append(lbl)
        cur_y.append(_at(d, 0)); m3_y.append(_at(d, 90)); y1_y.append(_at(d, 365))

    figc = go.Figure()
    figc.add_trace(go.Scatter(
        x=labels, y=y1_y, name="1Y ago", mode="lines+markers",
        line=dict(color="#cbd5e1", width=2, dash="dot"),
        marker=dict(size=5, color="#cbd5e1"),
    ))
    figc.add_trace(go.Scatter(
        x=labels, y=m3_y, name="3M ago", mode="lines+markers",
        line=dict(color="#93c5fd", width=2, dash="dash"),
        marker=dict(size=5, color="#93c5fd"),
    ))
    figc.add_trace(go.Scatter(
        x=labels, y=cur_y, name="Today", mode="lines+markers+text",
        line=dict(color="#2563eb", width=3),
        marker=dict(size=9, color="#2563eb"),
        text=[f"{v:.2f}%" if v is not None else "" for v in cur_y],
        textposition="top center", textfont=dict(size=11, color="#1e3a8a"),
    ))
    apply_standard_layout(figc, title="Treasury Yield Curve — today vs 3M & 1Y ago",
                          height=CHART_HEIGHT_FULL, yaxis_title="Yield",
                          xaxis_title="Maturity", hovermode="x unified")
    figc.update_yaxes(ticksuffix="%")
    st.plotly_chart(figc, use_container_width=True)

    # ── Chart 2: rate history (3Y), cohesive palette ───────────────────
    fig1 = go.Figure()
    ffdf = fetch_series("FEDFUNDS", years=3)
    if not ffdf.empty:
        fig1.add_trace(go.Scatter(
            x=ffdf["date"], y=ffdf["value"], name="Fed Funds",
            mode="lines", line=dict(color="#64748b", width=2, dash="dot"),
        ))
    for sid, (label, color) in CURVE.items():
        df = fetch_series(sid, years=3)
        if not df.empty:
            fig1.add_trace(go.Scatter(
                x=df["date"], y=df["value"], name=label, mode="lines",
                line=dict(color=color, width=2),
            ))
    apply_standard_layout(fig1, title="Rate History (3Y)", height=CHART_HEIGHT_FULL,
                          yaxis_title="Rate")
    fig1.update_yaxes(ticksuffix="%")
    st.plotly_chart(fig1, use_container_width=True)

    # Chart 3: curve spreads — 10Y-3M (the NY Fed recession indicator) is
    # emphasized, with the inverted (<0) zone shaded red and a live callout.
    fig2 = go.Figure()
    fig2.add_hrect(y0=-4, y1=0, fillcolor="rgba(220,38,38,0.07)",
                   line_width=0, layer="below")
    last_3m, last_3m_date = None, None
    for sid, label, color, width in [
        ("T10Y2Y", "10Y − 2Y", "#93c5fd", 1.6),
        ("T10Y3M", "10Y − 3M (recession signal)", "#dc2626", 2.8),
    ]:
        df = fetch_series(sid, years=5)
        if not df.empty:
            fig2.add_trace(go.Scatter(
                x=df["date"], y=df["value"], name=label, mode="lines",
                line=dict(color=color, width=width),
            ))
            if sid == "T10Y3M":
                dd = df.dropna(subset=["value"]).sort_values("date")
                if not dd.empty:
                    last_3m = float(dd["value"].iloc[-1])
                    last_3m_date = dd["date"].iloc[-1]
    fig2.add_hline(y=0, line_color="#94a3b8", line_width=1, line_dash="dash")
    if last_3m is not None:
        inv = last_3m < 0
        fig2.add_annotation(
            x=last_3m_date, y=last_3m,
            text=f"10Y−3M {last_3m:+.2f}pp · {'inverted' if inv else 'normal'}",
            showarrow=True, arrowhead=0, ax=-70, ay=-26,
            font=dict(size=10, color="#dc2626" if inv else "#059669"),
            bgcolor="#ffffff", bordercolor="#e5e7eb", borderpad=3,
        )
    apply_standard_layout(fig2, title="Curve Spreads (5Y) — 10Y−3M is the recession signal",
                          height=CHART_HEIGHT_COMPACT, yaxis_title="Spread")
    fig2.update_yaxes(ticksuffix="pp")
    st.plotly_chart(fig2, use_container_width=True)

    st.markdown("---")
    st.caption(
        "Data from FRED (Federal Reserve Economic Data). Refreshed daily. "
        "Recession score combines 10Y-2Y spread, 10Y-3M spread (NY Fed indicator), "
        "and Sahm Rule proxy on unemployment."
    )


def _render_credit_spreads():
    import plotly.graph_objects as go

    snap = get_macro_snapshot()
    hy_spread = snap.get("BAMLH0A0HYM2", {}).get("value")
    unemp = snap.get("UNRATE", {}).get("value")
    c1, c2, _ = st.columns(3)
    with c1:
        st.metric("HY Spread", f"{hy_spread:.2f}%" if hy_spread is not None else "—")
    with c2:
        st.metric("Unemployment", f"{unemp:.1f}%" if unemp is not None else "—")

    # Unemployment + HY credit spread (dual axis) — re-homed from the old
    # single-page layout.
    fig3 = go.Figure()
    unemp_df = fetch_series("UNRATE", years=5)
    hy_df = fetch_series("BAMLH0A0HYM2", years=5)
    if not unemp_df.empty:
        fig3.add_trace(go.Scatter(
            x=unemp_df["date"], y=unemp_df["value"], name="Unemployment",
            mode="lines", line=dict(color="#0891b2", width=2), yaxis="y",
        ))
    if not hy_df.empty:
        fig3.add_trace(go.Scatter(
            x=hy_df["date"], y=hy_df["value"], name="HY Credit Spread",
            mode="lines", line=dict(color="#d97706", width=2), yaxis="y2",
        ))
    apply_standard_layout(fig3, title="Labor & Credit (5Y)", height=CHART_HEIGHT_COMPACT,
                          yaxis_title="Unemployment %")
    fig3.update_layout(
        yaxis=dict(ticksuffix="%"),
        yaxis2=dict(title="HY Spread %", overlaying="y", side="right",
                    ticksuffix="%", showgrid=False),
    )
    st.plotly_chart(fig3, use_container_width=True)
    _pending("Spread regime bands + IG/HY history",
             "FRED series (live) — definitions per talk-through")
