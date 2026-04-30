"""
Capital Dynamics UI — renders the capital adequacy & buyback capacity panel
in the Company Analysis > Capital tab.
"""

import streamlit as st
import pandas as pd

from data.bank_mapping import get_fdic_cert, get_cik
from data.cache import get as cache_get, put as cache_put
from data import fdic_client, sec_client
from analysis.capital_dynamics import (
    summarize_bank_capital,
    CET1_REG_MIN, CET1_BUFFER_FLOOR,
)
from utils.formatting import fmt_dollars_from_thousands


from utils.chart_style import ALERT_STYLE as _SEVERITY_STYLE


def _pick_scale(max_abs_dollars: float) -> tuple[float, str]:
    """
    Pick an appropriate scale for a chart axis given the max absolute $ value.

    Returns (divisor, unit_suffix) — e.g., (1e9, "B"), (1e6, "M"), (1e3, "K").
    """
    if max_abs_dollars is None:
        return 1.0, ""
    abs_val = abs(max_abs_dollars)
    if abs_val >= 1e9:
        return 1e9, "B"
    elif abs_val >= 1e6:
        return 1e6, "M"
    elif abs_val >= 1e3:
        return 1e3, "K"
    return 1.0, ""


def _cet1_color(cet1: float | None) -> str:
    if cet1 is None:
        return "#999"
    if cet1 >= CET1_BUFFER_FLOOR + 2:  # well capitalized
        return "#1b5e20"
    if cet1 >= CET1_BUFFER_FLOOR:
        return "#e65100"
    return "#b71c1c"


def _load_hist(ticker: str) -> list[dict]:
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


def _load_shares(ticker: str) -> float | None:
    cik = get_cik(ticker)
    if not cik:
        return None
    cached = cache_get(f"sec:{ticker}")
    if cached and cached.get("shares_outstanding"):
        return cached["shares_outstanding"]
    sec = sec_client.get_latest_fundamentals(cik)
    if sec:
        cache_put(f"sec:{ticker}", sec)
        return sec.get("shares_outstanding")
    return None


def _load_peer_cet1_median(watchlist: list[str]) -> float | None:
    cet1s = []
    for t in watchlist:
        hist = cache_get(f"fdic_hist:{t}")
        if not hist:
            continue
        c = hist[0].get("IDT1CER")
        if c is not None and c > 0:
            cet1s.append(c)
    if not cet1s:
        return None
    return float(pd.Series(cet1s).median())


def _fmt_usd(amount_k: float | None) -> str:
    """Format thousands of dollars with auto T/B/M/K scaling."""
    return fmt_dollars_from_thousands(amount_k)


def render_capital_dynamics(ticker: str, watchlist: list[str] | None = None):
    """Render the Capital Adequacy & Buyback Capacity panel."""
    hist = _load_hist(ticker)
    if not hist:
        st.info("No FDIC history available for capital analysis.")
        return

    shares = _load_shares(ticker)
    peer_cet1 = _load_peer_cet1_median(watchlist or [])
    summary = summarize_bank_capital(hist, shares_outstanding=shares, peer_cet1_median=peer_cet1)
    timeline = summary["timeline"]

    if timeline.empty:
        st.info("Insufficient data for capital analysis.")
        return

    st.subheader("💰 Capital Adequacy & Buyback Capacity")

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
            f'<div style="{_SEVERITY_STYLE["ok"]}">✅ <strong>Capital position healthy — no alerts</strong></div>',
            unsafe_allow_html=True,
        )

    # ── Headline metrics ───────────────────────────────────────────────
    latest = summary["latest"]
    c1, c2, c3, c4, c5 = st.columns(5)

    with c1:
        cet1 = latest.get("cet1_pct")
        cet1_qoq = latest.get("cet1_qoq_pp")
        color = _cet1_color(cet1)
        benchmark = f"Peer: {peer_cet1:.2f}%" if peer_cet1 else ""
        st.markdown(
            f"""
            <div style="padding:4px 0;">
                <div style="font-size:0.85rem; color:#666;">CET1 Ratio</div>
                <div style="font-size:1.75rem; font-weight:600; color:{color};">
                    {f"{cet1:.2f}%" if cet1 is not None else "—"}
                </div>
                <div style="font-size:0.75rem; color:#999;">
                    {f"{cet1_qoq:+.2f}pp QoQ · {benchmark}" if cet1_qoq is not None else benchmark}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with c2:
        tbv_cagr = summary["tbv_cagr_1y"]
        tbv_cagr_2y = summary["tbv_cagr_2y"]
        st.metric(
            "TBV/Share CAGR (1Y)",
            f"{tbv_cagr:.1f}%" if tbv_cagr is not None else "—",
            delta=f"2Y: {tbv_cagr_2y:.1f}%" if tbv_cagr_2y is not None else None,
            delta_color="off",
        )

    with c3:
        bb = summary["buyback_capacity"]
        free = bb.get("free_capital")
        organic = bb.get("organic_need")
        st.metric(
            "Free Capital (Q)",
            _fmt_usd(free),
            delta=f"after {_fmt_usd(organic)} loan-growth need",
            delta_color="off",
        )

    with c4:
        # Payout ratio (4Q avg)
        retention_4q = timeline["retention_ratio"].tail(4).dropna()
        if len(retention_4q) > 0:
            avg_retention = retention_4q.mean()
            payout = max(0, (1 - avg_retention) * 100)
            color = "#b71c1c" if payout > 80 else ("#e65100" if payout > 60 else "#1b5e20")
            st.markdown(
                f"""
                <div style="padding:4px 0;">
                    <div style="font-size:0.85rem; color:#666;">Payout Ratio (4Q)</div>
                    <div style="font-size:1.75rem; font-weight:600; color:{color};">
                        {payout:.0f}%
                    </div>
                    <div style="font-size:0.75rem; color:#999;">Capital returned / NI</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.metric("Payout Ratio (4Q)", "—")

    with c5:
        total_cap = latest.get("total_cap_pct")
        leverage = latest.get("leverage_pct")
        st.metric(
            "Total Cap / Leverage",
            f"{total_cap:.2f}%" if total_cap is not None else "—",
            delta=f"Leverage: {leverage:.2f}%" if leverage is not None else None,
            delta_color="off",
        )

    # Buyback capacity explainer
    bb = summary["buyback_capacity"]
    if bb.get("free_capital") is not None:
        ni = latest.get("net_income_k_qtr")
        returned = latest.get("capital_returned_k")
        if ni is not None and returned is not None:
            explainer = (
                f"**Buyback capacity:** Quarterly NI {_fmt_usd(ni)} − "
                f"capital returned {_fmt_usd(returned)} − "
                f"loan-growth capital {_fmt_usd(bb.get('organic_need'))} "
                f"= **{_fmt_usd(bb.get('free_capital'))}** free for incremental buybacks."
            )
            if bb.get("free_capital") < 0:
                explainer += " *(Negative = already returning more than earnings support at current loan-growth pace.)*"
            st.caption(explainer)

    st.markdown("---")

    # ── Charts ─────────────────────────────────────────────────────────
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        # Chart 1: CET1 with regulatory floor lines
        fig1 = go.Figure()
        fig1.add_trace(go.Scatter(
            x=timeline["date"], y=timeline["cet1_pct"],
            name="CET1", mode="lines+markers",
            line=dict(color="#1a73e8", width=2.5),
            marker=dict(size=7), fill="tozeroy",
            fillcolor="rgba(26,115,232,0.10)",
        ))
        fig1.add_hline(y=CET1_REG_MIN, line_color="#b71c1c", line_width=1, line_dash="dash",
                        annotation_text=f"{CET1_REG_MIN}% reg min + buffer",
                        annotation_position="bottom right")
        fig1.add_hline(y=CET1_BUFFER_FLOOR, line_color="#e65100", line_width=1, line_dash="dot",
                        annotation_text=f"{CET1_BUFFER_FLOOR}% comfort floor",
                        annotation_position="top right")
        if peer_cet1:
            fig1.add_hline(y=peer_cet1, line_color="#1b5e20", line_width=1, line_dash="dashdot",
                            annotation_text=f"Peer median {peer_cet1:.2f}%",
                            annotation_position="top left")
        from utils.chart_style import apply_standard_layout, CHART_HEIGHT_FULL, CHART_HEIGHT_COMPACT

        apply_standard_layout(
            fig1, title="CET1 Ratio Trend",
            height=CHART_HEIGHT_FULL, yaxis_title="CET1",
            show_legend=False, hovermode="x",
        )
        fig1.update_yaxes(ticksuffix="%")
        st.plotly_chart(fig1, use_container_width=True)

        # Charts 2 & 3 side-by-side
        cc1, cc2 = st.columns(2)

        # Chart 2: TBV/share trend
        if "tbv_per_share" in timeline.columns and timeline["tbv_per_share"].notna().any():
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(
                x=timeline["date"], y=timeline["tbv_per_share"],
                name="TBV / Share", mode="lines+markers",
                line=dict(color="#1b5e20", width=2.5),
                marker=dict(size=6),
            ))
            apply_standard_layout(
                fig2, title="Tangible Book Value Per Share",
                height=CHART_HEIGHT_COMPACT, yaxis_title="TBV/Share",
                show_legend=False, hovermode="x",
                wide_left_margin=True,
            )
            fig2.update_yaxes(tickprefix="$")
            with cc1:
                st.plotly_chart(fig2, use_container_width=True)

        # Chart 3: Capital return mix — auto-scaled
        # Coerce to numeric first: columns may contain None from stale/missing FDIC
        # rows (e.g., banks right after their cert becomes active).
        _ni = pd.to_numeric(timeline["net_income_k_qtr"], errors="coerce")
        _cr = pd.to_numeric(timeline["capital_returned_k"], errors="coerce")
        max_val = max(_ni.abs().max() or 0, _cr.abs().max() or 0)
        scale, unit = _pick_scale(max_val * 1000)
        ni_scaled = timeline["net_income_k_qtr"] * 1000 / scale
        cr_scaled = timeline["capital_returned_k"] * 1000 / scale

        fig3 = go.Figure()
        fig3.add_trace(go.Bar(
            x=timeline["date"], y=ni_scaled,
            name="Net Income", marker_color="#1a73e8", opacity=0.85,
        ))
        fig3.add_trace(go.Bar(
            x=timeline["date"], y=cr_scaled,
            name="Capital Returned", marker_color="#b71c1c", opacity=0.85,
        ))
        apply_standard_layout(
            fig3, title="Net Income vs Capital Returned",
            height=CHART_HEIGHT_COMPACT, yaxis_title=f"$ {unit}",
            show_legend=True, wide_left_margin=True,
        )
        fig3.update_layout(barmode="group")
        with cc2:
            st.plotly_chart(fig3, use_container_width=True)

        # Chart 4: Capital Generation Waterfall (last quarter)
        #
        # Note: "Capital Returned" is DERIVED as NI − ΔEquity, so it captures
        # dividends + buybacks + AOCI + any other equity adjustments together.
        # We can't separate them without pulling from SEC 10-Q AOCI components.
        # The waterfall shows Starting Equity + NI − (NI − ΔEquity) = Ending Equity,
        # which by construction sums exactly — no residual.
        prior_eq = timeline["equity_k"].iloc[-2] if len(timeline) >= 2 else None
        curr_eq = latest.get("equity_k")
        ni = latest.get("net_income_k_qtr")
        if (prior_eq is not None and curr_eq is not None and ni is not None):
            cap_returned = latest.get("capital_returned_k") or 0

            scale, unit = _pick_scale(curr_eq * 1000)
            wf_scaled = [
                prior_eq * 1000 / scale,
                ni * 1000 / scale,
                -cap_returned * 1000 / scale,
                curr_eq * 1000 / scale,
            ]

            waterfall_labels = [
                "Starting<br>Equity",
                "+ Net<br>Income",
                "- Capital Returned<br>(Divs + Buybacks + AOCI)",
                "Ending<br>Equity",
            ]

            fig4 = go.Figure()
            fig4.add_trace(go.Waterfall(
                x=waterfall_labels,
                measure=["absolute", "relative", "relative", "total"],
                y=wf_scaled,
                text=[f"${v:,.1f}{unit}" for v in wf_scaled],
                textposition="outside",
                connector={"line": {"color": "rgb(150,150,150)"}},
                increasing={"marker": {"color": "#1b5e20"}},
                decreasing={"marker": {"color": "#b71c1c"}},
                totals={"marker": {"color": "#1a73e8"}},
            ))
            latest_ts = latest.get("date")
            if latest_ts is not None and hasattr(latest_ts, "month"):
                q = (latest_ts.month - 1) // 3 + 1
                period_label = f"{latest_ts.year}-Q{q}"
            else:
                period_label = ""
            apply_standard_layout(
                fig4, title=f"Capital Generation — {period_label}",
                height=CHART_HEIGHT_FULL, yaxis_title=f"$ {unit}",
                show_legend=False, wide_left_margin=True,
            )
            st.plotly_chart(fig4, use_container_width=True)

    except ImportError:
        st.warning("Install plotly to view capital charts.")

    # ── Capital Return Attribution (SEC-sourced) ────────────────────────
    st.markdown("---")
    _render_capital_return_attribution(ticker)


def _render_capital_return_attribution(ticker: str):
    """
    Show SEC-sourced dividend and buyback breakdown + total shareholder yield.
    """
    from data.bank_mapping import get_cik
    from analysis.capital_return import summarize_capital_return
    from utils.formatting import fmt_dollars

    cik = get_cik(ticker)
    if not cik:
        return

    # Try to get market cap from cached metrics
    market_cap = None
    try:
        from data.cache import get as cache_get
        metrics = cache_get("watchlist_metrics_last")
        if metrics:
            for m in metrics:
                if m.get("ticker") == ticker:
                    market_cap = m.get("market_cap")
                    break
    except Exception:
        pass

    with st.spinner("Loading SEC capital return data..."):
        result = summarize_capital_return(cik, market_cap=market_cap, lookback_quarters=20)

    timeline = result.get("timeline")
    if timeline is None or timeline.empty:
        return

    ttm = result.get("ttm", {})
    growth = result.get("growth", {})
    yld = result.get("yield", {})

    st.subheader("💸 Capital Return Attribution")

    div_source = result.get("dividend_source", "unknown")
    source_note = {
        "common-specific": "Common dividends (pure, excludes preferred).",
        "total minus preferred": "Common dividends (derived = total − preferred).",
        "total (includes preferred)": "⚠️ Total dividends only (includes preferred; may overstate common by ~3-8% for banks with meaningful preferred stock).",
        "unavailable": "⚠️ Dividend data not available in SEC filings.",
    }.get(div_source, "")

    st.caption(
        "Data from SEC XBRL (holding-company 10-K / 10-Q cash flow statement). "
        f"{source_note} "
        "Buybacks are common-stock only. Total shareholder yield = (TTM dividends + buybacks) / current market cap."
    )

    # Headline row: Total Return Ratio, Payout Ratio, Buyback Ratio, Share Reduction, Shareholder Yield
    c1, c2, c3, c4, c5 = st.columns(5)

    with c1:
        tr_ratio = (ttm.get("total_return_ratio_ttm") or 0) * 100
        st.metric(
            "Total Return Ratio",
            f"{tr_ratio:.0f}%" if ttm.get("total_return_ratio_ttm") is not None else "—",
            delta="of TTM net income", delta_color="off",
        )
    with c2:
        p_ratio = (ttm.get("payout_ratio_ttm") or 0) * 100
        st.metric(
            "Dividend Payout",
            f"{p_ratio:.0f}%" if ttm.get("payout_ratio_ttm") is not None else "—",
            delta=fmt_dollars(ttm.get("dividends_ttm"), 2) + " TTM" if ttm.get("dividends_ttm") else None,
            delta_color="off",
        )
    with c3:
        bb_ratio = (ttm.get("buyback_ratio_ttm") or 0) * 100
        st.metric(
            "Buyback Ratio",
            f"{bb_ratio:.0f}%" if ttm.get("buyback_ratio_ttm") is not None else "—",
            delta=fmt_dollars(ttm.get("buybacks_ttm"), 2) + " TTM" if ttm.get("buybacks_ttm") else None,
            delta_color="off",
        )
    with c4:
        sc_change = ttm.get("share_change_pct_ttm")
        st.metric(
            "Share Reduction",
            f"{sc_change:+.2f}%" if sc_change is not None else "—",
            delta="TTM" if sc_change is not None else None,
            delta_color="off",
        )
    with c5:
        sy = yld.get("total_shareholder_yield_pct")
        st.metric(
            "Shareholder Yield",
            f"{sy:.2f}%" if sy is not None else "—",
            delta=(
                f"{yld.get('dividend_yield_pct',0):.1f}% div + {yld.get('buyback_yield_pct',0):.1f}% bb"
                if sy is not None else None
            ),
            delta_color="off",
        )

    # ── Growth row ─────────────────────────────────────────────────────
    if any(growth.get(k) is not None for k in ["dividends_yoy_pct", "buybacks_yoy_pct", "dps_yoy_pct"]):
        st.markdown("")
        g1, g2, g3, g4 = st.columns(4)
        with g1:
            dps_g = growth.get("dps_yoy_pct")
            st.metric("DPS YoY Growth",
                      f"{dps_g:+.1f}%" if dps_g is not None else "—",
                      delta="$/share declared", delta_color="off")
        with g2:
            dg = growth.get("dividends_yoy_pct")
            st.metric("Dividends YoY",
                      f"{dg:+.1f}%" if dg is not None else "—",
                      delta="$ paid", delta_color="off")
        with g3:
            bg = growth.get("buybacks_yoy_pct")
            st.metric("Buybacks YoY",
                      f"{bg:+.1f}%" if bg is not None else "—",
                      delta="$ paid", delta_color="off")
        with g4:
            tg = growth.get("total_return_yoy_pct")
            st.metric("Total Return YoY",
                      f"{tg:+.1f}%" if tg is not None else "—",
                      delta="combined", delta_color="off")

    # ── Quarterly trend chart ──────────────────────────────────────────
    try:
        import plotly.graph_objects as go
        from utils.chart_style import apply_standard_layout, CHART_HEIGHT_COMPACT

        # Only show quarters with actual data
        df = timeline.dropna(subset=["dividends_q", "buybacks_q"], how="all")
        if not df.empty:
            # Coerce columns to numeric — any may contain None from sparse quarters
            _div = pd.to_numeric(df["dividends_q"], errors="coerce")
            _bb = pd.to_numeric(df["buybacks_q"], errors="coerce")
            _ni = pd.to_numeric(df["net_income_q"], errors="coerce")
            # Pick scale
            max_abs = max(
                _div.abs().max() or 0,
                _bb.abs().max() or 0,
                _ni.abs().max() or 0,
            )
            if max_abs >= 1e9:
                scale, unit = 1e9, "B"
            elif max_abs >= 1e6:
                scale, unit = 1e6, "M"
            else:
                scale, unit = 1e3, "K"

            cc1, cc2 = st.columns(2)

            # Chart 1: Stacked bar NI vs Div+BB
            fig1 = go.Figure()
            fig1.add_trace(go.Bar(
                x=df["date"], y=df["net_income_q"] / scale,
                name="Net Income", marker_color="#2563eb",
                opacity=0.45,
            ))
            fig1.add_trace(go.Bar(
                x=df["date"], y=df["dividends_q"].fillna(0) / scale,
                name="Dividends", marker_color="#059669",
            ))
            fig1.add_trace(go.Bar(
                x=df["date"], y=df["buybacks_q"].fillna(0) / scale,
                name="Buybacks", marker_color="#d97706",
            ))
            apply_standard_layout(
                fig1, title="Net Income vs Capital Returned (Quarterly)",
                height=CHART_HEIGHT_COMPACT,
                yaxis_title=f"$ {unit}",
                show_legend=True, wide_left_margin=True,
            )
            fig1.update_layout(barmode="group")
            with cc1:
                st.plotly_chart(fig1, use_container_width=True)

            # Chart 2: Total return ratio % trend
            fig2 = go.Figure()
            ratio_pct = df["total_return_ratio_q"] * 100
            fig2.add_trace(go.Scatter(
                x=df["date"], y=ratio_pct,
                mode="lines+markers",
                line=dict(color="#2563eb", width=2.5),
                marker=dict(size=6),
                name="Total Return Ratio",
            ))
            fig2.add_hline(y=100, line_color="#dc2626", line_width=1, line_dash="dash",
                           annotation_text="100% (returning all NI)",
                           annotation_position="top right", annotation_font_size=10)
            apply_standard_layout(
                fig2, title="Total Return Ratio (Divs+BB / NI)",
                height=CHART_HEIGHT_COMPACT,
                yaxis_title="%", show_legend=False,
            )
            fig2.update_yaxes(ticksuffix="%")
            with cc2:
                st.plotly_chart(fig2, use_container_width=True)

            # Chart 3: Share count trend
            if df["shares_outstanding"].notna().any():
                fig3 = go.Figure()
                fig3.add_trace(go.Scatter(
                    x=df["date"], y=df["shares_outstanding"] / 1e6,
                    mode="lines+markers",
                    line=dict(color="#9333ea", width=2.5),
                    marker=dict(size=6),
                    fill="tozeroy",
                    fillcolor="rgba(147, 51, 234, 0.08)",
                    name="Shares Outstanding",
                ))
                apply_standard_layout(
                    fig3, title="Shares Outstanding (M) — declining = buybacks working",
                    height=CHART_HEIGHT_COMPACT,
                    yaxis_title="Shares (M)", show_legend=False,
                )
                st.plotly_chart(fig3, use_container_width=True)

    except ImportError:
        pass

    # ── Quarterly detail table ─────────────────────────────────────────
    with st.expander("📋 Quarterly detail (last 8 quarters)"):
        df_disp = timeline.tail(8).copy()

        def _fmt_d(v):
            if pd.isna(v) or v is None:
                return "—"
            return fmt_dollars(v, 2)

        def _fmt_pct(v):
            if pd.isna(v) or v is None:
                return "—"
            return f"{v*100:.1f}%"

        rows = []
        for _, r in df_disp.iterrows():
            rows.append({
                "Quarter": (
                    f"{int(r.get('year', 0))}Q{int(r.get('quarter', 0))}"
                    if pd.notna(r.get('year')) and pd.notna(r.get('quarter'))
                    else str(r['date'].date()) if r.get('date') is not None else "—"
                ),
                "Net Income": _fmt_d(r.get("net_income_q")),
                "Dividends": _fmt_d(r.get("dividends_q")),
                "Buybacks": _fmt_d(r.get("buybacks_q")),
                "Total Returned": _fmt_d(r.get("total_returned_q")),
                "Payout": _fmt_pct(r.get("payout_ratio_q")),
                "Buyback %": _fmt_pct(r.get("buyback_ratio_q")),
                "Total Ret %": _fmt_pct(r.get("total_return_ratio_q")),
                "Share Chg": (
                    f"{r.get('share_change_pct'):+.2f}%"
                    if r.get("share_change_pct") is not None and not pd.isna(r.get("share_change_pct"))
                    else "—"
                ),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
