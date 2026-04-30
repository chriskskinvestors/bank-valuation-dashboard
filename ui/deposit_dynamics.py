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


from utils.chart_style import ALERT_STYLE as _SEVERITY_STYLE


def _fmt_quarter(ts) -> str:
    """Format a timestamp as 'YYYY-QN' (e.g., 2026-Q1)."""
    if ts is None:
        return "—"
    if hasattr(ts, "year") and hasattr(ts, "month"):
        q = (ts.month - 1) // 3 + 1
        return f"{ts.year}-Q{q}"
    return str(ts)


def _beta_color(beta: float | None) -> str:
    """Green for low (sticky), yellow for medium, red for high (sensitive)."""
    if beta is None:
        return "#999"
    if beta < 0.30:
        return "#1b5e20"  # green — sticky
    if beta < 0.50:
        return "#e65100"  # amber
    return "#b71c1c"  # red — rate-sensitive


def _load_hist(ticker: str) -> list[dict]:
    """Load 20 qtrs of FDIC history, fetching if not cached."""
    hist = cache_get(f"fdic_hist:{ticker}")
    if hist and len(hist) >= 8:
        return hist

    # Fetch fresh — we need 20 quarters for a proper cycle view
    cert = get_fdic_cert(ticker)
    if not cert:
        return hist or []

    df = fdic_client.fetch_financials(cert, limit=20)
    if df.empty:
        return hist or []

    from data.cache import put as cache_put
    records = df.to_dict("records")
    cache_put(f"fdic_hist:{ticker}", records)
    return records


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
    st.subheader("📊 Deposit Dynamics")

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
            f'<div style="{_SEVERITY_STYLE["ok"]}">✅ <strong>Deposit profile stable — no alerts</strong></div>',
            unsafe_allow_html=True,
        )

    # ── Key Metrics Row ────────────────────────────────────────────────
    latest = summary["latest"]
    cycle_beta = summary["cycle_beta"]
    rolling_beta = summary["rolling_beta"]

    c1, c2, c3, c4, c5 = st.columns(5)

    with c1:
        total = latest.get("total_dep")
        qoq = latest.get("dep_qoq_growth")
        val = fmt_dollars_from_thousands(total)
        delta = f"{qoq:+.1f}% QoQ" if qoq is not None else None
        st.metric("Total Deposits", val, delta=delta, delta_color="normal")

    with c2:
        cod = latest.get("cost_of_deposits")
        # Compute QoQ change
        if len(timeline) >= 2:
            prev_cod = timeline["cost_of_deposits"].iloc[-2]
            cod_chg = (cod - prev_cod) * 100 if (cod is not None and prev_cod is not None) else None
        else:
            cod_chg = None
        st.metric(
            "Cost of Deposits",
            f"{cod:.2f}%" if cod is not None else "—",
            delta=f"{cod_chg:+.0f} bps QoQ" if cod_chg is not None else None,
            delta_color="inverse",  # rising cost = bad
        )

    with c3:
        cb = cycle_beta.get("beta")
        direction = cycle_beta.get("cycle_direction", "").title()
        color = _beta_color(cb)
        st.markdown(
            f"""
            <div style="padding:4px 0;">
                <div style="font-size:0.85rem; color:#666;">Cycle Beta ({direction})</div>
                <div style="font-size:1.75rem; font-weight:600; color:{color};">
                    {f"{cb:.2f}" if cb is not None else "—"}
                </div>
                <div style="font-size:0.75rem; color:#999;">
                    {f"n={cycle_beta.get('n_quarters', '—')}Q" if cb is not None else ""}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with c4:
        rb = rolling_beta.get("beta")
        r2 = rolling_beta.get("r_squared")
        color = _beta_color(rb)
        st.markdown(
            f"""
            <div style="padding:4px 0;">
                <div style="font-size:0.85rem; color:#666;">Rolling Beta (4Q)</div>
                <div style="font-size:1.75rem; font-weight:600; color:{color};">
                    {f"{rb:.2f}" if rb is not None else "—"}
                </div>
                <div style="font-size:0.75rem; color:#999;">
                    {f"R²={r2:.2f}" if r2 is not None else ""}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with c5:
        nii_pct = latest.get("nonint_dep_pct")
        unins = latest.get("uninsured_pct")
        # Show both — non-int % tells you stickiness, uninsured tells you run risk
        st.metric(
            "Non-Int Dep %",
            f"{nii_pct:.1f}%" if nii_pct is not None else "—",
            delta=f"Uninsured: {unins:.0f}%" if unins is not None else None,
            delta_color="off",
        )

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
    try:
        import plotly.graph_objects as go
        from utils.chart_style import apply_standard_layout, CHART_HEIGHT_FULL, CHART_HEIGHT_COMPACT

        # Chart 1: Cost of Deposits vs Fed Funds (main)
        fig1 = go.Figure()
        fig1.add_trace(go.Scatter(
            x=timeline["date"], y=timeline["fed_funds"],
            name="Fed Funds", mode="lines+markers",
            line=dict(color="#1a73e8", width=2, dash="dot"),
            marker=dict(size=6),
        ))
        fig1.add_trace(go.Scatter(
            x=timeline["date"], y=timeline["cost_of_deposits"],
            name="Cost of Deposits", mode="lines+markers",
            line=dict(color="#b71c1c", width=2.5),
            marker=dict(size=7),
        ))
        apply_standard_layout(
            fig1, title="Cost of Deposits vs Fed Funds",
            height=CHART_HEIGHT_FULL, yaxis_title="Rate",
        )
        fig1.update_yaxes(ticksuffix="%")
        st.plotly_chart(fig1, use_container_width=True)

        # Charts 2 & 3 side-by-side for density
        cc1, cc2 = st.columns(2)

        # Chart 2: Deposit Composition Trend
        if "nonint_dep_pct" in timeline.columns:
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(
                x=timeline["date"], y=timeline["nonint_dep_pct"],
                name="Non-Int Bearing", mode="lines+markers",
                line=dict(color="#1b5e20", width=2.5),
            ))
            if "brokered_pct" in timeline.columns and timeline["brokered_pct"].notna().any():
                fig2.add_trace(go.Scatter(
                    x=timeline["date"], y=timeline["brokered_pct"],
                    name="Brokered", mode="lines+markers",
                    line=dict(color="#e65100", width=2),
                ))
            if "uninsured_pct" in timeline.columns and timeline["uninsured_pct"].notna().any():
                fig2.add_trace(go.Scatter(
                    x=timeline["date"], y=timeline["uninsured_pct"],
                    name="Uninsured", mode="lines+markers",
                    line=dict(color="#b71c1c", width=2, dash="dash"),
                ))
            apply_standard_layout(
                fig2, title="Deposit Composition",
                height=CHART_HEIGHT_COMPACT, yaxis_title="% of Total Deposits",
            )
            fig2.update_yaxes(ticksuffix="%")
            with cc1:
                st.plotly_chart(fig2, use_container_width=True)

        # Chart 3: QoQ Deposit Growth
        fig3 = go.Figure()
        colors = [
            "#1b5e20" if (g is not None and g >= 0) else "#b71c1c"
            for g in timeline["dep_qoq_growth"]
        ]
        fig3.add_trace(go.Bar(
            x=timeline["date"], y=timeline["dep_qoq_growth"],
            marker_color=colors, name="QoQ Growth",
        ))
        fig3.add_hline(y=0, line_color="#666", line_width=1)
        fig3.add_hline(y=-2, line_color="#b71c1c", line_width=1, line_dash="dash",
                       annotation_text="Alert", annotation_position="bottom right",
                       annotation_font_size=10)
        apply_standard_layout(
            fig3, title="QoQ Deposit Growth",
            height=CHART_HEIGHT_COMPACT, yaxis_title="% QoQ",
            show_legend=False, hovermode="x",
        )
        fig3.update_yaxes(ticksuffix="%")
        with cc2:
            st.plotly_chart(fig3, use_container_width=True)

    except ImportError:
        st.warning("Install plotly to view deposit trend charts.")
