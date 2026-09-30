"""
Credit Dynamics UI — renders the institutional-grade credit quality panel
in the Company Analysis > Credit tab.
"""

import streamlit as st
import pandas as pd

from data.bank_mapping import get_name
from ui.chrome import title_bar
from data.cache import get as cache_get, put as cache_put
from data import fdic_client
from analysis.credit_dynamics import (
    summarize_bank_credit,
    compute_peer_reserve_median,
)


from utils.chart_style import (ALERT_STYLE as _SEVERITY_STYLE,
                               COLOR_SUCCESS, COLOR_DANGER, COLOR_PRIMARY,
                               COLOR_WARNING)


# Shared loader (data/loaders) — was a verbatim copy in five tab modules.
from data.loaders import load_fdic_hist as _load_hist
from ui.history_range import range_picker, chart_timeline
from analysis.credit_dynamics import build_credit_timeline


# Memoized for an hour (UX review P1-05, 2026-09-30): the loop reads ~600
# cached FDIC histories (tens of MB of JSON) on EVERY Asset Quality render for
# one median of nightly-refreshed data — most of the page's ~13 s. The median
# can only move when the nightly job rewrites fdic_hist:*, so an hour-old
# value is the same number.
@st.cache_data(ttl=3600, show_spinner=False)
def _load_peer_median_reserve_coverage(watchlist: list[str]) -> float | None:
    """Compute reserve-coverage peer median from cached watchlist histories."""
    covs = []
    for t in watchlist:
        hist = cache_get(f"fdic_hist:{t}")
        if not hist:
            continue
        latest = hist[0]
        rtl = latest.get("LNATRESR")
        npl = latest.get("NCLNLSR")
        if rtl is not None and npl is not None and npl > 0:
            covs.append(rtl / npl * 100)
    if not covs:
        return None
    return float(pd.Series(covs).median())


# @st.fragment for the same reason as the statement pages: the Annual/
# Quarterly radio inside the statement table would otherwise rerun the WHOLE
# Company page per toggle (see ui/financials_statements.py fragment note).
@st.fragment
def render_credit_dynamics(ticker: str, watchlist: list[str] | None = None,
                           view: str = "detail"):
    """Render the Credit Quality analysis panel for a bank.

    view="detail"        — bank-level: alerts, headline cards, NCO / past-due /
                           reserve-coverage trends (the Asset Quality Detail tab).
    view="by_loan_type"  — segment-level: hotspots table + NPL by loan segment
                           (the Asset Quality by Loan Type tab). Previously both
                           nav tabs rendered the identical page.
    """
    hist = _load_hist(ticker)
    if not hist:
        from ui.states import empty_state
        empty_state('No FDIC history available for credit analysis')
        return

    peer_median = _load_peer_median_reserve_coverage(watchlist or [])
    summary = summarize_bank_credit(hist, peer_reserve_median=peer_median)
    timeline = summary["timeline"]

    if timeline.empty:
        st.info("Insufficient data for credit analysis.")
        return

    _page = "Asset Quality by Loan Type" if view == "by_loan_type" else "Asset Quality Detail"
    title_bar(f"{get_name(ticker)} ({ticker})", _page)

    if view == "by_loan_type":
        _render_by_loan_type(ticker, summary, timeline)
        return

    st.markdown('<div class="ksk-sec">Credit Quality Dynamics</div>',
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
            f'<div style="{_SEVERITY_STYLE["ok"]}"><strong>No credit alerts — trends stable</strong></div>',
            unsafe_allow_html=True,
        )

    latest = summary["latest"]

    # Absolute + peer context
    if peer_median:
        rc = latest.get("reserve_coverage")
        if rc is not None:
            gap = rc - peer_median
            if rc < 100:
                benchmark_msg = f"**Under-reserved** — below 100% minimum (peer median {peer_median:.0f}%)"
            elif gap < 0:
                benchmark_msg = f"Below peer median by {abs(gap):.0f}pp"
            else:
                benchmark_msg = f"Above peer median by {gap:.0f}pp"
            st.caption(f"Reserve coverage: {benchmark_msg}")

    st.markdown("---")

    # ── Charts (bank-level) ────────────────────────────────────────────
    import plotly.graph_objects as go
    from utils.chart_style import (apply_standard_layout, tighten_yaxis,
                                   CHART_HEIGHT_COMPACT, COLOR_FILL_DANGER)

    # Owner layout (2026-07-13): statement table LEFT, charts RIGHT. The
    # containers are created here so the chart-range picker (deep history,
    # ui/history_range) sits above the charts it drives. `ctl` is what the
    # charts plot: the page's own 20-quarter timeline on the default
    # range, a deeper rebuild otherwise — alerts and headline keep 5Y.
    _left, _right = st.columns([1, 1])
    with _right:
        rng = range_picker(f"aq_rng_{ticker}")
        ctl, _depth_cap = chart_timeline(
            ticker, rng, timeline, build_credit_timeline,
            [("nco_ratio", "NCO"), ("past_due_30_89_pct", "30-89 past due"),
             ("past_due_90_pct", "90+ past due"),
             ("reserve_coverage", "Reserve coverage")])
        if _depth_cap:
            st.caption(_depth_cap)

    # Chart 2: NCO trend
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(
        x=ctl["date"], y=ctl["nco_ratio"],
        name="NCO Rate", mode="lines+markers",
        line=dict(color=COLOR_DANGER, width=2.5),
        marker=dict(size=6), fill="tozeroy",
        fillcolor=COLOR_FILL_DANGER,
    ))
    apply_standard_layout(fig2, title="Net Charge-Off Rate", height=CHART_HEIGHT_COMPACT,
                          yaxis_title="NCO %", show_legend=False, hovermode="x")
    fig2.update_yaxes(ticksuffix="%")

    # Chart 3: Past due migration
    fig3 = go.Figure()
    if "past_due_30_89_pct" in ctl.columns:
        fig3.add_trace(go.Scatter(
            x=ctl["date"], y=ctl["past_due_30_89_pct"],
            name="30-89 Past Due", mode="lines+markers",
            line=dict(color=COLOR_WARNING, width=2),
        ))
    if "past_due_90_pct" in ctl.columns:
        fig3.add_trace(go.Scatter(
            x=ctl["date"], y=ctl["past_due_90_pct"],
            name="90+ Past Due", mode="lines+markers",
            line=dict(color=COLOR_DANGER, width=2),
        ))
    apply_standard_layout(fig3, title="Past Due Migration", height=CHART_HEIGHT_COMPACT,
                          yaxis_title="% of Loans", show_legend=True)
    tighten_yaxis(fig3, floor_zero=True, ticksuffix="%")

    # Chart 4: Reserve coverage trend with peer median line
    fig4 = go.Figure()
    fig4.add_trace(go.Scatter(
        x=ctl["date"], y=ctl["reserve_coverage"],
        name="Reserve / NPL", mode="lines+markers",
        line=dict(color=COLOR_SUCCESS, width=2.5),
        marker=dict(size=6),
    ))
    fig4.add_hline(y=100, line_color=COLOR_DANGER, line_width=1, line_dash="dash",
                    annotation_text="100% floor", annotation_position="bottom right")
    if peer_median:
        fig4.add_hline(y=peer_median, line_color=COLOR_PRIMARY, line_width=1, line_dash="dot",
                        annotation_text=f"Peer median {peer_median:.0f}%", annotation_position="top right")
    apply_standard_layout(fig4, title="Reserve Coverage vs NPL", height=CHART_HEIGHT_COMPACT,
                          yaxis_title="Reserve / NPL", show_legend=False, hovermode="x")
    _rc_vals = [v for v in ctl["reserve_coverage"].tolist() if v is not None] + [100]
    if peer_median:
        _rc_vals.append(peer_median)
    tighten_yaxis(fig4, _rc_vals, floor_zero=True, ticksuffix="%")

    # Owner layout (2026-07-13): the full SNL/CapIQ-depth statement table on
    # the LEFT (statement engine — Annual/Quarterly toggle, click-to-source),
    # the credit trend charts stacked on the RIGHT.
    from ui.financials_statements import render_asset_quality
    with _left:
        render_asset_quality(ticker)
    with _right:
        _g1 = st.columns(2)
        with _g1[0]:
            st.plotly_chart(fig2, use_container_width=True, key=f"aq_nco_{ticker}")
        with _g1[1]:
            st.plotly_chart(fig3, use_container_width=True, key=f"aq_pd_{ticker}")
        _g2 = st.columns(2)
        with _g2[0]:
            st.plotly_chart(fig4, use_container_width=True, key=f"aq_rc_{ticker}")


def _render_by_loan_type(ticker: str, summary: dict, timeline):
    """Asset Quality by Loan Type — every loan segment's latest NPL ratio (and
    its multiple vs the bank-wide ratio) on the left, the per-segment NPL trend
    on the right. Shows ALL segments (the prior 'hotspots only' table was empty
    whenever no segment ran above the bank average)."""
    from utils.chart_style import CATEGORICAL_PALETTE
    segments = [
        ("npl_ratio", "Total", "#0f172a", 3),
        ("npl_cre", "All RE", COLOR_DANGER, 2),
        ("npl_resi", "Residential", COLOR_PRIMARY, 2),
        ("npl_multifam", "Multifamily", COLOR_WARNING, 2),
        ("npl_nres_re", "Non-Res RE", CATEGORICAL_PALETTE[4], 2),
        ("npl_ci", "C&I", COLOR_SUCCESS, 2),
        ("npl_consumer", "Consumer", CATEGORICAL_PALETTE[6], 2),
    ]
    # Owner layout (2026-07-13): full per-category delinquency matrix
    # (statement engine, Annual/Quarterly toggle) on the LEFT — the table
    # SNL shows as NA; the per-segment NPL trend chart stays on the RIGHT.
    _tbl, _chart = st.columns([1, 1])
    with _tbl:
        from ui.financials_statements import render_aq_by_loan_type
        render_aq_by_loan_type(ticker)

    with _chart:
        import plotly.graph_objects as go
        from utils.chart_style import (apply_standard_layout, tighten_yaxis,
                                       CHART_HEIGHT_COMPACT)
        # Deep-history range for the segment chart (ui/history_range).
        rng = range_picker(f"aqlt_rng_{ticker}")
        ctl, _depth_cap = chart_timeline(
            ticker, rng, timeline, build_credit_timeline,
            [(k, lb) for k, lb, _c, _w in segments])
        if _depth_cap:
            st.caption(_depth_cap)
        fig = go.Figure()
        for key, label, color, width in segments:
            if key in ctl.columns and ctl[key].notna().any():
                fig.add_trace(go.Scatter(
                    x=ctl["date"], y=ctl[key],
                    name=label, mode="lines+markers",
                    line=dict(color=color, width=width),
                    marker=dict(size=5 if width < 3 else 7),
                ))
        apply_standard_layout(fig, title="NPL by Loan Segment",
                              height=CHART_HEIGHT_COMPACT,
                              yaxis_title="NPL %", show_legend=True)
        tighten_yaxis(fig, floor_zero=True, ticksuffix="%")
        st.plotly_chart(fig, use_container_width=True)
