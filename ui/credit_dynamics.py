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


def _render_credit_headline(ticker, hist, summary, peer_median):
    """Credit headline cards — every number click-to-source. Reported FDIC
    ratios link to the Call Report facsimile; computed ratios (reserve
    coverage, past-due %) show their formula + the raw Call Report inputs."""
    from ui.source_trace import render_traceable_cards, fdic_calc, make_calc
    from ui.financial_highlights import _fdic_doc, _disp_date, _thou, _num

    cert = get_fdic_cert(ticker)
    entity = f"{get_name(ticker)} ({ticker})"
    rec = hist[0]
    latest = summary["latest"]
    cr_doc = _fdic_doc(cert, rec.get("REPDTE")) if cert else None
    asof = _disp_date(rec.get("REPDTE"))

    def pct(x):
        return f"{x:.2f}%" if x is not None else "—"

    def qoq(val, q, fmt, worse_up=True):
        if q is None:
            return val
        bad = (q >= 0) if worse_up else (q < 0)
        col = "#dc2626" if bad else "#059669"
        return f"{val} <span style='font-size:0.68rem; color:{col}; font-weight:600;'>{fmt(q)}</span>"

    npl = latest.get("npl_ratio"); nco = latest.get("nco_ratio")
    rc = latest.get("reserve_coverage"); pd89 = latest.get("past_due_30_89_pct")
    rtl = latest.get("reserve_to_loans")
    p3 = _num(rec.get("P3ASSET")); loans = _num(rec.get("LNLSNET"))
    cov_val = f"{rc:.0f}%" if rc is not None else "—"

    cards = [
        {"label": "NPL Ratio",
         "value": qoq(pct(npl), latest.get("npl_ratio_qoq"), lambda q: f"{q*100:+.0f}bps"),
         "calc": fdic_calc("NPL ratio", "NCLNLSR", rec, cert, unit="%", entity=entity,
                           value=pct(npl), reported=True,
                           definition="Non-current loans (90+ days past due or nonaccrual) "
                                       "as a percent of total loans.")},
        {"label": "NCO Ratio",
         "value": qoq(pct(nco), latest.get("nco_ratio_qoq"), lambda q: f"{q*100:+.0f}bps"),
         "calc": fdic_calc("NCO ratio", "NTLNLSR", rec, cert, unit="%", entity=entity,
                           value=pct(nco), reported=True,
                           definition="Annualized net charge-offs as a percent of total loans.")},
        {"label": "Reserve / NPL", "value": cov_val,
         "calc": make_calc("Reserve coverage (reserves / NPL)", cov_val, entity=entity,
                           source="FDIC Call Report", asof=asof, unit="%",
                           ref="Computed from Call Report",
                           definition="Loan-loss reserves as a multiple of non-current loans — "
                                       "how well reserves cover NPLs."
                                       + (f" Peer median {peer_median:.0f}%." if peer_median else ""),
                           terms=[{"label": "Reserves / loans (%)", "val": pct(rtl), "doc": cr_doc},
                                  {"label": "NPL ratio (%)", "val": pct(npl), "doc": cr_doc}],
                           op="Reserves/loans ÷ NPL ratio × 100", reported=False,
                           link=(cr_doc or {}).get("url"))},
        {"label": "Past Due 30-89",
         "value": qoq(pct(pd89), latest.get("past_due_30_89_pct_qoq"), lambda q: f"{q:+.2f}pp"),
         "calc": make_calc("Past due 30-89 days", pct(pd89), entity=entity,
                           source="FDIC Call Report", asof=asof, unit="%",
                           ref="Computed from Call Report",
                           definition="Loans 30-89 days past due as a percent of total loans "
                                       "(early-delinquency signal).",
                           terms=[{"label": "30-89 days past due ($000)", "val": _thou(p3), "doc": cr_doc},
                                  {"label": "Total loans ($000)", "val": _thou(loans), "doc": cr_doc}],
                           op="30-89 past due ÷ total loans × 100", reported=False,
                           link=(cr_doc or {}).get("url"))},
        {"label": "Reserves / Loans", "value": pct(rtl),
         "calc": fdic_calc("Reserves / loans", "LNATRESR", rec, cert, unit="%", entity=entity,
                           value=pct(rtl), reported=True,
                           definition="Allowance for credit losses as a percent of total loans.")},
    ]
    render_traceable_cards(cards, key=f"credit_{ticker}", columns=5)


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

    # ── Headline metrics (click any value for its calc + Call Report) ──
    latest = summary["latest"]
    _render_credit_headline(ticker, hist, summary, peer_median)

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
        from utils.chart_style import (apply_standard_layout, tighten_yaxis,
                                       CHART_HEIGHT_FULL, CHART_HEIGHT_COMPACT)
        apply_standard_layout(fig1, title="NPL by Loan Segment", height=CHART_HEIGHT_COMPACT,
                              yaxis_title="NPL %", show_legend=True)
        tighten_yaxis(fig1, floor_zero=True, ticksuffix="%")

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
        tighten_yaxis(fig3, floor_zero=True, ticksuffix="%")

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
        _rc_vals = [v for v in timeline["reserve_coverage"].tolist() if v is not None] + [100]
        if peer_median:
            _rc_vals.append(peer_median)
        tighten_yaxis(fig4, _rc_vals, floor_zero=True, ticksuffix="%")

        # Dense 2×2 grid — no full-width single charts.
        _g1 = st.columns(2)
        with _g1[0]:
            st.plotly_chart(fig1, use_container_width=True)
        with _g1[1]:
            st.plotly_chart(fig2, use_container_width=True)
        _g2 = st.columns(2)
        with _g2[0]:
            st.plotly_chart(fig3, use_container_width=True)
        with _g2[1]:
            st.plotly_chart(fig4, use_container_width=True)

    except ImportError:
        st.warning("Install plotly to view credit trend charts.")
