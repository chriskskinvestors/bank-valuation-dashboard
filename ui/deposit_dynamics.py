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

from data.bank_mapping import get_name
from data.cache import get as cache_get
from ui.chrome import title_bar
from data import fdic_client
from analysis.deposit_dynamics import summarize_bank_deposits, build_deposit_timeline


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
from ui.history_range import range_picker, chart_timeline


# @st.fragment: the statement table's Annual/Quarterly radio must not rerun
# the whole Company page (same pattern as the other Financials tabs).
@st.fragment
def render_deposit_dynamics(ticker: str, show_title: bool = True):
    """Render the Deposit Dynamics analysis panel for a specific bank.

    show_title=False when embedded under another page (e.g. the Deposit/Loan
    Composition tab) so the SNL title bar is not repeated mid-page.
    """
    hist = _load_hist(ticker)
    if not hist:
        from ui.states import empty_state
        empty_state('No FDIC history available for deposit dynamics analysis')
        return

    summary = summarize_bank_deposits(hist)
    timeline = summary["timeline"]

    if timeline.empty:
        st.info("Insufficient data for deposit dynamics.")
        return

    # ── Header ─────────────────────────────────────────────────────────
    if show_title:
        title_bar(f"{get_name(ticker)} ({ticker})", "Deposit Trends")
    st.markdown('<div class="ksk-sec">Deposit &amp; Loan Composition</div>',
                unsafe_allow_html=True)

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

    cycle_beta = summary["cycle_beta"]

    # ── Cycle Beta Explainer ───────────────────────────────────────────
    if cycle_beta:
        start = cycle_beta.get("start_date")
        end = cycle_beta.get("end_date")
        ff_change = cycle_beta.get("ff_change")
        cod_change = cycle_beta.get("cod_change")
        direction = cycle_beta.get("cycle_direction", "")
        moved = "rose" if direction == "up" else "fell"
        start_str = _fmt_quarter(start)
        end_str = _fmt_quarter(end)
        caption = (
            f"**Cycle beta** tracks {direction} cycle from {start_str} to {end_str}. "
            f"Fed funds {moved} {abs(ff_change):.2f}pp → Cost of deposits {cod_change:+.2f}pp. "
            f"Beta <0.30 = sticky (green), >0.50 = rate-sensitive (red)."
        )
        st.caption(caption)

    st.markdown("---")

    # ── Charts ─────────────────────────────────────────────────────────
    import plotly.graph_objects as go
    from utils.chart_style import (apply_standard_layout, tighten_yaxis,
                                   CHART_HEIGHT_FULL, CHART_HEIGHT_COMPACT)

    # Owner layout (2026-07-13): statement table LEFT, charts RIGHT. The
    # containers are created here so the chart-range picker (deep history,
    # ui/history_range) sits above the charts it drives. `ctl` is what the
    # charts plot: the page's own 20-quarter timeline on the default
    # range, a deeper rebuild otherwise — alerts and cycle beta keep 5Y.
    _left, _right = st.columns([1, 1])
    with _right:
        rng = range_picker(f"dep_rng_{ticker}")
        ctl, _depth_cap = chart_timeline(
            ticker, rng, timeline, build_deposit_timeline,
            [("cost_of_deposits", "Cost of deposits"), ("fed_funds", "Fed funds"),
             ("nonint_dep_pct", "Non-int bearing"), ("brokered_pct", "Brokered"),
             ("uninsured_pct", "Uninsured")])
        if _depth_cap:
            st.caption(_depth_cap)

    # Chart 1: Cost of Deposits vs Fed Funds (main)
    fig1 = go.Figure()
    fig1.add_trace(go.Scatter(
        x=ctl["date"], y=ctl["fed_funds"],
        name="Fed Funds", mode="lines+markers",
        line=dict(color=COLOR_PRIMARY, width=2, dash="dot"),
        marker=dict(size=6),
    ))
    fig1.add_trace(go.Scatter(
        x=ctl["date"], y=ctl["cost_of_deposits"],
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
    if "nonint_dep_pct" in ctl.columns:
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=ctl["date"], y=ctl["nonint_dep_pct"],
            name="Non-Int Bearing", mode="lines+markers",
            line=dict(color=COLOR_SUCCESS, width=2.5),
        ))
        if "brokered_pct" in ctl.columns and ctl["brokered_pct"].notna().any():
            fig2.add_trace(go.Scatter(
                x=ctl["date"], y=ctl["brokered_pct"],
                name="Brokered", mode="lines+markers",
                line=dict(color=COLOR_WARNING, width=2),
            ))
        if "uninsured_pct" in ctl.columns and ctl["uninsured_pct"].notna().any():
            fig2.add_trace(go.Scatter(
                x=ctl["date"], y=ctl["uninsured_pct"],
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
        for g in ctl["dep_qoq_growth"]
    ]
    fig3.add_trace(go.Bar(
        x=ctl["date"], y=ctl["dep_qoq_growth"],
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

    # Owner layout (2026-07-13): the deposit-side statement table (Annual/
    # Quarterly toggle, click-to-source) on the LEFT — the full loan+deposit
    # mix lives on Deposit/Loan Composition; this page keeps the deposit
    # sections + growth next to its cost/beta charts on the RIGHT.
    from ui.financials_statements import render_deposit_trends_table
    with _left:
        render_deposit_trends_table(ticker)
    with _right:
        _g = st.columns(2)
        with _g[0]:
            st.plotly_chart(fig1, use_container_width=True, key=f"dep_cost_{ticker}")
        if fig2 is not None:
            with _g[1]:
                st.plotly_chart(fig2, use_container_width=True, key=f"dep_comp_{ticker}")
        _g2 = st.columns(2)
        with _g2[0]:
            st.plotly_chart(fig3, use_container_width=True, key=f"dep_qoq_{ticker}")

