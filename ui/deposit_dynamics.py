"""
Deposit Dynamics UI — renders the institutional-grade deposit analysis
panel at the top of the Company Analysis > Deposits tab.

Shows:
  - Deposit beta (cycle + rolling)
  - Alerts (4 conditions)
  - Composition trend chart
  - Cost of deposits vs Fed funds chart
"""

import streamlit as st
import pandas as pd

from data.bank_mapping import get_fdic_cert, get_name
from data.cache import get as cache_get
from data import fdic_client
from analysis.deposit_dynamics import summarize_bank_deposits
from utils.formatting import fmt_dollars_from_thousands


from utils.chart_style import (ALERT_STYLE as _SEVERITY_STYLE,
                               COLOR_SUCCESS, COLOR_DANGER, COLOR_PRIMARY,
                               COLOR_WARNING)


def _fmt_quarter(ts) -> str:
    """Format a timestamp as 'YYYY-QN' (e.g., 2026-Q1)."""
    if ts is None:
        return "—"
    if hasattr(ts, "year") and hasattr(ts, "month"):
        q = (ts.month - 1) // 3 + 1
        return f"{ts.year}-Q{q}"
    return str(ts)


# Shared loader (data/loaders) — was a verbatim copy in five tab modules.
from data.loaders import load_fdic_hist as _load_hist


def _render_deposit_headline(ticker, hist, summary, timeline):
    """Deposit headline cards — click-to-source. Reported FDIC fields (total
    deposits, funding cost) link to the Call Report; computed shares and the
    model betas show their formula + the FDIC/FRED inputs."""
    from ui.source_trace import render_traceable_cards, fdic_calc, make_calc
    from ui.financial_highlights import _fdic_doc, _disp_date, _thou, _num

    cert = get_fdic_cert(ticker)
    entity = f"{get_name(ticker)} ({ticker})"
    rec = hist[0]
    latest = summary["latest"]
    cycle_beta = summary.get("cycle_beta") or {}
    rolling_beta = summary.get("rolling_beta") or {}
    cr_doc = _fdic_doc(cert, rec.get("REPDTE")) if cert else None
    asof = _disp_date(rec.get("REPDTE"))

    total = _num(latest.get("total_dep"))
    qoq = latest.get("dep_qoq_growth")
    cod = latest.get("cost_of_deposits")
    nonint_pct = latest.get("nonint_dep_pct"); unins = latest.get("uninsured_pct")
    depni = _num(rec.get("DEPNIDOM")); dep = _num(rec.get("DEP"))

    tot_disp = fmt_dollars_from_thousands(total)
    if qoq is not None:
        col = "#059669" if qoq >= 0 else "#dc2626"
        tot_disp += (f" <span style='font-size:0.68rem; color:{col}; "
                     f"font-weight:600;'>{qoq:+.1f}%</span>")
    cod_disp = f"{cod:.2f}%" if cod is not None else "—"
    if len(timeline) >= 2 and cod is not None:
        prev = timeline["cost_of_deposits"].iloc[-2]
        if prev is not None and not pd.isna(prev):
            chg = (cod - prev) * 100
            col = "#dc2626" if chg >= 0 else "#059669"  # rising cost = bad
            cod_disp += (f" <span style='font-size:0.68rem; color:{col}; "
                         f"font-weight:600;'>{chg:+.0f}bps</span>")

    cb = cycle_beta.get("beta"); rb = rolling_beta.get("beta")
    r2 = rolling_beta.get("r_squared")
    ff_chg = cycle_beta.get("ff_change"); cod_chg = cycle_beta.get("cod_change")

    cards = [
        {"label": "Total Deposits", "value": tot_disp,
         "calc": fdic_calc("Total deposits", "DEP", rec, cert, unit="$ in thousands",
                           entity=entity, value=fmt_dollars_from_thousands(total),
                           reported=True, definition="Total domestic and foreign deposits.")},
        {"label": "Cost of Deposits", "value": cod_disp,
         "calc": fdic_calc("Cost of deposits (funding)", "INTEXPY", rec, cert, unit="%",
                           entity=entity, value=(f"{cod:.2f}%" if cod is not None else "—"),
                           reported=True,
                           definition="Annualized cost of interest-bearing liabilities (FDIC "
                                       "INTEXPY — deposits plus other borrowings; a funding-cost "
                                       "proxy for pure-deposit banks).")},
        {"label": f"Cycle Beta", "value": (f"{cb:.2f}" if cb is not None else "—"),
         "calc": make_calc("Deposit cycle beta", (f"{cb:.2f}" if cb is not None else "—"),
                           entity=entity, source="Model — rate-cycle regression", asof=asof,
                           unit="beta", ref="Δ cost of deposits ÷ Δ fed funds (cycle)",
                           definition="How much of a Fed-funds move passes through to deposit "
                                       "cost over a full rate cycle. <0.30 = sticky deposits, "
                                       ">0.50 = rate-sensitive.",
                           terms=[{"label": "Δ Fed funds over cycle (pp)",
                                   "val": (f"{ff_chg:+.2f}" if ff_chg is not None else "—"),
                                   "sub": "FRED — effective federal funds rate"},
                                  {"label": "Δ Cost of deposits over cycle (pp)",
                                   "val": (f"{cod_chg:+.2f}" if cod_chg is not None else "—"),
                                   "doc": cr_doc, "sub": "FDIC INTEXPY"}],
                           op="Δ cost of deposits ÷ Δ fed funds")},
        {"label": "Rolling Beta (4Q)", "value": (f"{rb:.2f}" if rb is not None else "—"),
         "calc": make_calc("Rolling deposit beta (4-quarter)",
                           (f"{rb:.2f}" if rb is not None else "—"), entity=entity,
                           source="Model — 4Q rolling regression", asof=asof, unit="beta",
                           ref="regression slope, trailing 4 quarters",
                           definition="Recent deposit-cost sensitivity to Fed funds, fit over the "
                                       "last four quarters (more responsive than the cycle beta).",
                           terms=[{"label": "Regression R²",
                                   "val": (f"{r2:.2f}" if r2 is not None else "—"),
                                   "sub": "goodness of fit"}],
                           op="slope of Δ cost-of-deposits vs Δ fed funds (4Q)")},
        {"label": "Non-Int Dep %", "value": (f"{nonint_pct:.1f}%" if nonint_pct is not None else "—"),
         "calc": make_calc("Non-interest-bearing deposit share",
                           (f"{nonint_pct:.1f}%" if nonint_pct is not None else "—"), entity=entity,
                           source="FDIC Call Report", asof=asof, unit="%",
                           ref="Computed from Call Report",
                           definition="Non-interest-bearing (checking) deposits as a share of total "
                                       "— a stickiness/low-cost-funding gauge."
                                       + (f" Uninsured deposits {unins:.0f}% of total."
                                          if unins is not None else ""),
                           terms=[{"label": "Non-interest-bearing deposits ($000)",
                                   "val": _thou(depni), "doc": cr_doc},
                                  {"label": "Total deposits ($000)", "val": _thou(dep), "doc": cr_doc}],
                           op="Non-interest deposits ÷ total deposits × 100", reported=False,
                           link=(cr_doc or {}).get("url"))},
    ]
    render_traceable_cards(cards, key=f"deposits_{ticker}", columns=5)


def render_deposit_dynamics(ticker: str):
    """Render the Deposit Dynamics analysis panel for a specific bank."""
    hist = _load_hist(ticker)
    if not hist:
        st.info("No FDIC history available for deposit dynamics analysis.")
        return

    summary = summarize_bank_deposits(hist)
    timeline = summary["timeline"]

    if timeline.empty:
        st.info("Insufficient data for deposit dynamics.")
        return

    # ── Header ─────────────────────────────────────────────────────────
    st.subheader("Deposit Dynamics")

    # ── Alerts ─────────────────────────────────────────────────────────
    alerts = summary["alerts"]
    if alerts:
        for a in alerts:
            style = _SEVERITY_STYLE.get(a["severity"], _SEVERITY_STYLE["medium"])
            st.markdown(
                f'<div style="{style}"><strong>{a["message"]}</strong></div>',
                unsafe_allow_html=True,
            )
        st.markdown("")
    else:
        st.markdown(
            f'<div style="{_SEVERITY_STYLE["ok"]}"><strong>Deposit profile stable — no alerts</strong></div>',
            unsafe_allow_html=True,
        )

    # ── Key Metrics Row (click any value for its calc + sources) ──
    latest = summary["latest"]
    cycle_beta = summary["cycle_beta"]
    rolling_beta = summary["rolling_beta"]
    _render_deposit_headline(ticker, hist, summary, timeline)

    # ── Cycle Beta Explainer ───────────────────────────────────────────
    if cycle_beta:
        start = cycle_beta.get("start_date")
        end = cycle_beta.get("end_date")
        ff_change = cycle_beta.get("ff_change")
        cod_change = cycle_beta.get("cod_change")
        direction = cycle_beta.get("cycle_direction", "")
        arrow = "↑" if direction == "up" else "↓"
        start_str = _fmt_quarter(start)
        end_str = _fmt_quarter(end)
        caption = (
            f"**Cycle beta** tracks {direction} cycle from {start_str} to {end_str}. "
            f"Fed funds {arrow} {abs(ff_change):.2f}pp → Cost of deposits {cod_change:+.2f}pp. "
            f"Beta <0.30 = sticky (green), >0.50 = rate-sensitive (red)."
        )
        st.caption(caption)

    st.markdown("---")

    # ── Charts ─────────────────────────────────────────────────────────
    import plotly.graph_objects as go
    from utils.chart_style import (apply_standard_layout, tighten_yaxis,
                                   CHART_HEIGHT_FULL, CHART_HEIGHT_COMPACT)

    # Chart 1: Cost of Deposits vs Fed Funds (main)
    fig1 = go.Figure()
    fig1.add_trace(go.Scatter(
        x=timeline["date"], y=timeline["fed_funds"],
        name="Fed Funds", mode="lines+markers",
        line=dict(color=COLOR_PRIMARY, width=2, dash="dot"),
        marker=dict(size=6),
    ))
    fig1.add_trace(go.Scatter(
        x=timeline["date"], y=timeline["cost_of_deposits"],
        name="Cost of Deposits", mode="lines+markers",
        line=dict(color=COLOR_DANGER, width=2.5),
        marker=dict(size=7),
    ))
    apply_standard_layout(
        fig1, title="Cost of Deposits vs Fed Funds",
        height=CHART_HEIGHT_COMPACT, yaxis_title="Rate",
    )
    tighten_yaxis(fig1, floor_zero=True, ticksuffix="%")

    # Chart 2: Deposit Composition Trend
    fig2 = None
    if "nonint_dep_pct" in timeline.columns:
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=timeline["date"], y=timeline["nonint_dep_pct"],
            name="Non-Int Bearing", mode="lines+markers",
            line=dict(color=COLOR_SUCCESS, width=2.5),
        ))
        if "brokered_pct" in timeline.columns and timeline["brokered_pct"].notna().any():
            fig2.add_trace(go.Scatter(
                x=timeline["date"], y=timeline["brokered_pct"],
                name="Brokered", mode="lines+markers",
                line=dict(color=COLOR_WARNING, width=2),
            ))
        if "uninsured_pct" in timeline.columns and timeline["uninsured_pct"].notna().any():
            fig2.add_trace(go.Scatter(
                x=timeline["date"], y=timeline["uninsured_pct"],
                name="Uninsured", mode="lines+markers",
                line=dict(color=COLOR_DANGER, width=2, dash="dash"),
            ))
        apply_standard_layout(
            fig2, title="Deposit Composition",
            height=CHART_HEIGHT_COMPACT, yaxis_title="% of Total Deposits",
        )
        tighten_yaxis(fig2, floor_zero=True, ticksuffix="%")

    # Chart 3: QoQ Deposit Growth
    fig3 = go.Figure()
    colors = [
        COLOR_SUCCESS if (g is not None and g >= 0) else COLOR_DANGER
        for g in timeline["dep_qoq_growth"]
    ]
    fig3.add_trace(go.Bar(
        x=timeline["date"], y=timeline["dep_qoq_growth"],
        marker_color=colors, name="QoQ Growth",
    ))
    fig3.add_hline(y=0, line_color="#666", line_width=1)
    fig3.add_hline(y=-2, line_color=COLOR_DANGER, line_width=1, line_dash="dash",
                   annotation_text="Alert", annotation_position="bottom right",
                   annotation_font_size=10)
    apply_standard_layout(
        fig3, title="QoQ Deposit Growth",
        height=CHART_HEIGHT_COMPACT, yaxis_title="% QoQ",
        show_legend=False, hovermode="x",
    )
    fig3.update_yaxes(ticksuffix="%")

    # Row 1: cost trend | composition (2-up). Row 2: QoQ growth bars (wide).
    _g1 = st.columns(2)
    with _g1[0]:
        st.plotly_chart(fig1, use_container_width=True)
    if fig2 is not None:
        with _g1[1]:
            st.plotly_chart(fig2, use_container_width=True)
    st.plotly_chart(fig3, use_container_width=True)

