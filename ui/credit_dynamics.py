"""
Credit Dynamics UI — renders the institutional-grade credit quality panel
in the Company Analysis > Credit tab.
"""

import streamlit as st
import pandas as pd

from data.bank_mapping import get_fdic_cert, get_name
from data.cache import get as cache_get, put as cache_put
from data import fdic_client
from analysis.credit_dynamics import (
    summarize_bank_credit,
    compute_peer_reserve_median,
)


from utils.chart_style import ALERT_STYLE as _SEVERITY_STYLE


def _load_hist(ticker: str) -> list[dict]:
    """Load 20 qtrs of FDIC history, fetching if not cached."""
    hist = cache_get(f"fdic_hist:{ticker}")
    if hist and len(hist) >= 8:
        return hist
    cert = get_fdic_cert(ticker)
    if not cert:
        return hist or []
    df = fdic_client.fetch_financials(cert, limit=20)
    if df.empty:
        return hist or []
    records = df.to_dict("records")
    cache_put(f"fdic_hist:{ticker}", records)
    return records


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


def _coverage_color(pct: float | None, peer_med: float | None = None) -> str:
    """Green if well-reserved, yellow if adequate, red if thin."""
    if pct is None:
        return "#999"
    if pct >= 200:
        return "#1b5e20"
    if pct >= 100:
        return "#e65100"
    return "#b71c1c"


def render_credit_dynamics(ticker: str, watchlist: list[str] | None = None):
    """Render the Credit Quality analysis panel for a bank."""
    hist = _load_hist(ticker)
    if not hist:
        st.info("No FDIC history available for credit analysis.")
        return

    peer_median = _load_peer_median_reserve_coverage(watchlist or [])
    summary = summarize_bank_credit(hist, peer_reserve_median=peer_median)
    timeline = summary["timeline"]

    if timeline.empty:
        st.info("Insufficient data for credit analysis.")
        return

    st.subheader("🏦 Credit Quality Dynamics")

    # ── Alerts ─────────────────────────────────────────────────────────
    alerts = summary["alerts"]
    if alerts:
        for a in alerts:
            style = _SEVERITY_STYLE.get(a["severity"], _SEVERITY_STYLE["medium"])
            icon = "🚨" if a["severity"] == "high" else "⚠️"
            st.markdown(
                f'<div style="{style}">{icon} <strong>{a["message"]}</strong></div>',
                unsafe_allow_html=True,
            )
        st.markdown("")
    else:
        st.markdown(
            f'<div style="{_SEVERITY_STYLE["ok"]}">✅ <strong>No credit alerts — trends stable</strong></div>',
            unsafe_allow_html=True,
        )

    # ── Headline metrics ───────────────────────────────────────────────
    latest = summary["latest"]
    c1, c2, c3, c4, c5 = st.columns(5)

    with c1:
        npl = latest.get("npl_ratio")
        npl_qoq = latest.get("npl_ratio_qoq")
        st.metric(
            "NPL Ratio",
            f"{npl:.2f}%" if npl is not None else "—",
            delta=f"{npl_qoq*100:+.0f}bps QoQ" if npl_qoq is not None else None,
            delta_color="inverse",
        )

    with c2:
        nco = latest.get("nco_ratio")
        nco_qoq = latest.get("nco_ratio_qoq")
        st.metric(
            "NCO Ratio",
            f"{nco:.2f}%" if nco is not None else "—",
            delta=f"{nco_qoq*100:+.0f}bps QoQ" if nco_qoq is not None else None,
            delta_color="inverse",
        )

    with c3:
        rc = latest.get("reserve_coverage")
        color = _coverage_color(rc, peer_median)
        benchmark_text = (
            f"Peer median: {peer_median:.0f}%" if peer_median else "No peer data"
        )
        st.markdown(
            f"""
            <div style="padding:4px 0;">
                <div style="font-size:0.85rem; color:#666;">Reserve/NPL</div>
                <div style="font-size:1.75rem; font-weight:600; color:{color};">
                    {f"{rc:.0f}%" if rc is not None else "—"}
                </div>
                <div style="font-size:0.75rem; color:#999;">{benchmark_text}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with c4:
        pd_30_89 = latest.get("past_due_30_89_pct")
        pd_qoq = latest.get("past_due_30_89_pct_qoq")
        st.metric(
            "Past Due 30-89",
            f"{pd_30_89:.2f}%" if pd_30_89 is not None else "—",
            delta=f"{pd_qoq:+.2f}pp QoQ" if pd_qoq is not None else None,
            delta_color="inverse",
        )

    with c5:
        rtl = latest.get("reserve_to_loans")
        st.metric(
            "Reserves / Loans",
            f"{rtl:.2f}%" if rtl is not None else "—",
        )

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

    # ── Segment Hotspots Table ─────────────────────────────────────────
    hotspots = summary["hotspots"]
    if hotspots:
        st.subheader("🎯 Segment Hotspots")
        hs_rows = []
        for h in hotspots:
            hs_rows.append({
                "Segment": h["segment"],
                "NPL %": f"{h['npl_pct']:.2f}%",
                "vs Bank Total": f"{h['vs_total_multiple']:.1f}x",
            })
        hs_df = pd.DataFrame(hs_rows)
        st.dataframe(hs_df, use_container_width=True, hide_index=True)
        st.markdown("")

    # ── Charts ─────────────────────────────────────────────────────────
    try:
        import plotly.graph_objects as go

        # Chart 1: NPL by segment over time (stacked/line)
        fig1 = go.Figure()
        segments = [
            ("npl_ratio", "Total", "#1a1a1a", 3),
            ("npl_cre", "CRE", "#b71c1c", 2),
            ("npl_resi", "Residential", "#1a73e8", 2),
            ("npl_multifam", "Multifamily", "#e65100", 2),
            ("npl_nres_re", "Non-Res RE", "#6a1b9a", 2),
            ("npl_ci", "C&I", "#1b5e20", 2),
            ("npl_consumer", "Consumer", "#ff6f00", 2),
        ]
        for key, label, color, width in segments:
            if key in timeline.columns and timeline[key].notna().any():
                fig1.add_trace(go.Scatter(
                    x=timeline["date"], y=timeline[key],
                    name=label, mode="lines+markers",
                    line=dict(color=color, width=width),
                    marker=dict(size=5 if width < 3 else 7),
                ))
        from utils.chart_style import apply_standard_layout, CHART_HEIGHT_FULL, CHART_HEIGHT_COMPACT
        apply_standard_layout(fig1, title="NPL by Loan Segment", height=CHART_HEIGHT_FULL,
                              yaxis_title="NPL %", show_legend=True)
        fig1.update_yaxes(ticksuffix="%")
        st.plotly_chart(fig1, use_container_width=True)

        # Charts 2 & 3 side-by-side (NCO + Past Due) for density
        cc1, cc2 = st.columns(2)

        # Chart 2: NCO trend
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=timeline["date"], y=timeline["nco_ratio"],
            name="NCO Rate", mode="lines+markers",
            line=dict(color="#b71c1c", width=2.5),
            marker=dict(size=6), fill="tozeroy",
            fillcolor="rgba(183,28,28,0.10)",
        ))
        apply_standard_layout(fig2, title="Net Charge-Off Rate", height=CHART_HEIGHT_COMPACT,
                              yaxis_title="NCO %", show_legend=False, hovermode="x")
        fig2.update_yaxes(ticksuffix="%")
        with cc1:
            st.plotly_chart(fig2, use_container_width=True)

        # Chart 3: Past due migration
        fig3 = go.Figure()
        if "past_due_30_89_pct" in timeline.columns:
            fig3.add_trace(go.Scatter(
                x=timeline["date"], y=timeline["past_due_30_89_pct"],
                name="30-89 Past Due", mode="lines+markers",
                line=dict(color="#e65100", width=2),
            ))
        if "past_due_90_pct" in timeline.columns:
            fig3.add_trace(go.Scatter(
                x=timeline["date"], y=timeline["past_due_90_pct"],
                name="90+ Past Due", mode="lines+markers",
                line=dict(color="#b71c1c", width=2),
            ))
        apply_standard_layout(fig3, title="Past Due Migration", height=CHART_HEIGHT_COMPACT,
                              yaxis_title="% of Loans", show_legend=True)
        fig3.update_yaxes(ticksuffix="%")
        with cc2:
            st.plotly_chart(fig3, use_container_width=True)

        # Chart 4: Reserve coverage trend with peer median line
        fig4 = go.Figure()
        fig4.add_trace(go.Scatter(
            x=timeline["date"], y=timeline["reserve_coverage"],
            name="Reserve / NPL", mode="lines+markers",
            line=dict(color="#1b5e20", width=2.5),
            marker=dict(size=6),
        ))
        fig4.add_hline(y=100, line_color="#b71c1c", line_width=1, line_dash="dash",
                        annotation_text="100% floor", annotation_position="bottom right")
        if peer_median:
            fig4.add_hline(y=peer_median, line_color="#1a73e8", line_width=1, line_dash="dot",
                            annotation_text=f"Peer median {peer_median:.0f}%", annotation_position="top right")
        apply_standard_layout(fig4, title="Reserve Coverage vs NPL", height=CHART_HEIGHT_COMPACT,
                              yaxis_title="Reserve / NPL", show_legend=False, hovermode="x")
        fig4.update_yaxes(ticksuffix="%")
        st.plotly_chart(fig4, use_container_width=True)

    except ImportError:
        st.warning("Install plotly to view credit trend charts.")
