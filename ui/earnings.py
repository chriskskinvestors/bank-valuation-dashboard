"""
Earnings vs Consensus — comprehensive earnings analysis platform.

Features:
1. Manual consensus input form
2. Historical earnings tracking with trend charts
3. Earnings calendar with next report dates
4. Surprise magnitude rankings
5. Sector aggregate beat/miss stats
6. Auto-populated consensus estimates (via yfinance)
"""

import streamlit as st
import pandas as pd

from data.bank_mapping import get_name, get_fdic_cert, get_cik
from data.consensus import (
    parse_consensus_pdf,
    parse_consensus_excel,
    parse_bulk_consensus,
    parse_bulk_consensus_pdf,
    save_consensus,
    load_consensus,
    list_consensus,
    list_all_consensus,
    compare_consensus_to_actual,
    save_manual_consensus,
    METRIC_DISPLAY,
    METRIC_UNITS,
)
from data.estimates import (
    fetch_estimates_cached,
    fetch_earnings_calendar,
    fetch_all_estimates,
)


# ── Beat/miss styling ───────────────────────────────────────────────────
_BEAT_STYLE = "background-color: #e8f5e9; color: #1b5e20; font-weight: 600;"
_MISS_STYLE = "background-color: #ffebee; color: #b71c1c; font-weight: 600;"
_INLINE_STYLE = "background-color: #fff8e1; color: #e65100;"
_NA_STYLE = "color: #999;"

_BEAT_LABEL = "✅ Beat"
_MISS_LABEL = "❌ Miss"
_INLINE_LABEL = "➖ Inline"
_NA_LABEL = "—"


def _format_val(val, unit: str) -> str:
    if val is None:
        return "—"
    if unit == "$":
        return f"${val:,.2f}"
    elif unit == "%":
        return f"{val:.2f}%"
    elif unit in ("$M", "$m"):
        return f"${val:,.1f}M"
    elif unit in ("$B", "$b"):
        return f"${val:,.2f}B"
    elif unit == "bps":
        return f"{val:.0f} bps"
    elif unit == "x":
        return f"{val:.2f}x"
    else:
        return f"{val:,.2f}"


def _format_delta(delta, unit: str) -> str:
    if delta is None:
        return "—"
    sign = "+" if delta > 0 else ""
    if unit == "$":
        return f"{sign}${delta:,.2f}"
    elif unit == "%":
        return f"{sign}{delta:.2f}%"
    elif unit in ("$M", "$m"):
        return f"{sign}${delta:,.1f}M"
    elif unit in ("$B", "$b"):
        return f"{sign}${delta:,.2f}B"
    elif unit == "bps":
        return f"{sign}{delta:.0f} bps"
    else:
        return f"{sign}{delta:,.2f}"


# ═══════════════════════════════════════════════════════════════════════════
# PER-BANK EARNINGS VIEW (Company Analysis → Earnings tab)
# ═══════════════════════════════════════════════════════════════════════════

def render_earnings_consensus(ticker: str, actual_metrics: dict):
    """Render the earnings vs consensus comparison for a single bank."""

    bank_name = get_name(ticker)
    st.markdown(
        f'<div class="dashboard-header">'
        f"<h1>{ticker} — Earnings vs Consensus</h1>"
        f"<p>{bank_name}</p>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # ── Auto-populated estimates from yfinance ──────────────────────────
    with st.spinner("Loading analyst estimates..."):
        estimates = fetch_estimates_cached(ticker)

    if estimates and not estimates.get("error"):
        _render_auto_estimates(ticker, estimates)

    st.markdown("---")

    # ── Tabs for input methods ──────────────────────────────────────────
    input_tab1, input_tab2 = st.tabs(["📝 Manual Input", "📄 Upload File"])

    with input_tab1:
        _render_manual_input(ticker)

    with input_tab2:
        _render_file_upload(ticker)

    st.markdown("---")

    # ── Historical earnings surprises ───────────────────────────────────
    if estimates and estimates.get("earnings_history"):
        _render_earnings_history_chart(ticker, estimates)
        st.markdown("---")

    # ── Consensus comparison table ──────────────────────────────────────
    periods = list_consensus(ticker)

    if periods:
        st.subheader("Consensus vs Actual")

        period_labels = [f"{p['period']} ({p['source']}, {p['metric_count']} metrics)" for p in periods]
        selected_idx = st.selectbox(
            "Select period",
            options=list(range(len(periods))),
            format_func=lambda i: period_labels[i],
            key=f"consensus_period_select_{ticker}",
        )

        selected_period = periods[selected_idx]["period"]
        consensus = load_consensus(ticker, selected_period)

        if consensus:
            comparison = compare_consensus_to_actual(consensus, actual_metrics)
            if comparison:
                _render_comparison_table(comparison)
            else:
                st.info("No comparable metrics found.")
    else:
        st.info(
            f"No consensus data for {ticker} yet. "
            "Enter estimates manually or upload a consensus file above."
        )

    _render_key_metrics(ticker, actual_metrics)


def _render_auto_estimates(ticker: str, estimates: dict):
    """Show auto-populated analyst estimates from yfinance."""
    st.subheader("Analyst Estimates")

    cols = st.columns(5)

    with cols[0]:
        ned = estimates.get("next_earnings_date")
        st.metric("Next Earnings", ned if ned else "Unknown")

    with cols[1]:
        eps_est = estimates.get("eps_estimate")
        st.metric("EPS Est (Next Qtr)", f"${eps_est:.2f}" if eps_est else "—")

    with cols[2]:
        eps_fwd = estimates.get("eps_fwd_annual")
        st.metric("EPS Est (Annual)", f"${eps_fwd:.2f}" if eps_fwd else "—")

    with cols[3]:
        target = estimates.get("target_price")
        st.metric("Avg Price Target", f"${target:.2f}" if target else "—")

    with cols[4]:
        analysts = estimates.get("analyst_count")
        st.metric("Analyst Coverage", str(analysts) if analysts else "—")

    # Price target range
    t_low = estimates.get("target_low")
    t_high = estimates.get("target_high")
    rec = estimates.get("recommendation")
    if t_low and t_high:
        st.caption(
            f"Price target range: ${t_low:.2f} – ${t_high:.2f}"
            + (f" · Consensus: {rec.replace('_', ' ').title()}" if rec else "")
        )

    # Offer to auto-populate consensus from estimates
    if estimates.get("earnings_history"):
        past = [e for e in estimates["earnings_history"] if e.get("eps_estimate") is not None]
        if past:
            with st.expander("📊 Past Earnings Surprises (from Yahoo Finance)"):
                hist_rows = []
                for e in past[:8]:
                    surprise = e.get("surprise_pct")
                    if surprise is not None:
                        if surprise > 1:
                            result = _BEAT_LABEL
                        elif surprise < -1:
                            result = _MISS_LABEL
                        else:
                            result = _INLINE_LABEL
                    else:
                        result = _NA_LABEL

                    hist_rows.append({
                        "Date": e.get("date", "—"),
                        "EPS Estimate": f"${e['eps_estimate']:.2f}" if e.get("eps_estimate") is not None else "—",
                        "EPS Actual": f"${e['eps_actual']:.2f}" if e.get("eps_actual") is not None else "—",
                        "Surprise %": f"{surprise:+.1f}%" if surprise is not None else "—",
                        "Result": result,
                    })

                if hist_rows:
                    df = pd.DataFrame(hist_rows)
                    st.dataframe(df, use_container_width=True, hide_index=True)


def _render_manual_input(ticker: str):
    """Manual consensus input form."""
    st.markdown("**Enter consensus estimates manually:**")

    period = st.text_input(
        "Period",
        placeholder="e.g. 2026Q1",
        key=f"manual_period_{ticker}",
    )

    # Core metrics in organized groups
    st.markdown("##### Earnings & Profitability")
    mc1, mc2, mc3, mc4 = st.columns(4)

    with mc1:
        eps = st.number_input("EPS ($)", value=None, format="%.2f", key=f"m_eps_{ticker}", step=0.01)
    with mc2:
        nim = st.number_input("NIM (%)", value=None, format="%.2f", key=f"m_nim_{ticker}", step=0.01)
    with mc3:
        efficiency = st.number_input("Efficiency (%)", value=None, format="%.1f", key=f"m_eff_{ticker}", step=0.1)
    with mc4:
        roatce = st.number_input("ROATCE (%)", value=None, format="%.2f", key=f"m_roatce_{ticker}", step=0.01)

    st.markdown("##### Income & Revenue")
    mc5, mc6, mc7, mc8 = st.columns(4)

    with mc5:
        roaa = st.number_input("ROAA (%)", value=None, format="%.2f", key=f"m_roaa_{ticker}", step=0.01)
    with mc6:
        nii = st.number_input("Net Int Income ($M)", value=None, format="%.1f", key=f"m_nii_{ticker}", step=0.1)
    with mc7:
        revenue = st.number_input("Revenue ($M)", value=None, format="%.1f", key=f"m_rev_{ticker}", step=0.1)
    with mc8:
        netinc = st.number_input("Net Income ($M)", value=None, format="%.1f", key=f"m_netinc_{ticker}", step=0.1)

    st.markdown("##### Balance Sheet & Credit")
    mc9, mc10, mc11, mc12 = st.columns(4)

    with mc9:
        tbvps = st.number_input("TBV/Share ($)", value=None, format="%.2f", key=f"m_tbvps_{ticker}", step=0.01)
    with mc10:
        npl = st.number_input("NPL Ratio (%)", value=None, format="%.2f", key=f"m_npl_{ticker}", step=0.01)
    with mc11:
        cet1 = st.number_input("CET1 (%)", value=None, format="%.2f", key=f"m_cet1_{ticker}", step=0.01)
    with mc12:
        provision = st.number_input("Provision ($M)", value=None, format="%.1f", key=f"m_prov_{ticker}", step=0.1)

    st.markdown("##### Other")
    mc13, mc14, mc15, mc16 = st.columns(4)

    with mc13:
        nonii = st.number_input("Nonint Income ($M)", value=None, format="%.1f", key=f"m_nonii_{ticker}", step=0.1)
    with mc14:
        nonix = st.number_input("Nonint Expense ($M)", value=None, format="%.1f", key=f"m_nonix_{ticker}", step=0.1)
    with mc15:
        nco = st.number_input("NCO Ratio (%)", value=None, format="%.2f", key=f"m_nco_{ticker}", step=0.01)
    with mc16:
        dps = st.number_input("Dividend/Share ($)", value=None, format="%.2f", key=f"m_dps_{ticker}", step=0.01)

    if st.button("💾 Save Consensus", key=f"save_manual_{ticker}", type="primary"):
        if not period:
            st.error("Please enter a period (e.g. 2026Q1)")
        else:
            metrics = {}
            if eps is not None: metrics["eps"] = eps
            if nim is not None: metrics["nim"] = nim
            if efficiency is not None: metrics["efficiency_ratio"] = efficiency
            if roatce is not None: metrics["roatce"] = roatce
            if roaa is not None: metrics["roaa"] = roaa
            if nii is not None: metrics["nii"] = nii
            if revenue is not None: metrics["revenue"] = revenue
            if netinc is not None: metrics["netinc"] = netinc
            if tbvps is not None: metrics["tbvps"] = tbvps
            if npl is not None: metrics["npl_ratio"] = npl
            if cet1 is not None: metrics["cet1_ratio"] = cet1
            if provision is not None: metrics["provision"] = provision
            if nonii is not None: metrics["nonii"] = nonii
            if nonix is not None: metrics["nonix"] = nonix
            if nco is not None: metrics["nco_ratio"] = nco
            if dps is not None: metrics["dps"] = dps

            if not metrics:
                st.error("Please enter at least one metric.")
            else:
                save_manual_consensus(ticker, period, metrics)
                st.success(f"Saved {len(metrics)} consensus estimates for {ticker} {period}")
                st.rerun()


def _render_file_upload(ticker: str):
    """File upload for consensus estimates."""
    col1, col2 = st.columns([2, 1])
    with col1:
        uploaded = st.file_uploader(
            "Upload consensus estimate",
            type=["pdf", "xlsx", "xls", "csv"],
            key=f"consensus_upload_{ticker}",
            help="Upload a PDF or Excel file with consensus estimates",
        )
    with col2:
        period = st.text_input(
            "Period",
            placeholder="e.g. 2026Q1",
            key=f"consensus_period_{ticker}",
        )

    if uploaded and period:
        with st.spinner("Parsing consensus document..."):
            file_bytes = uploaded.read()
            filename = uploaded.name.lower()

            if filename.endswith(".pdf"):
                parsed = parse_consensus_pdf(file_bytes, ticker, period)
            else:
                parsed = parse_consensus_excel(file_bytes, ticker, period, filename)

            if parsed.get("error"):
                st.error(f"Error parsing: {parsed['error']}")
            elif not parsed.get("metrics"):
                st.warning("No metrics found in the document.")
            else:
                save_consensus(parsed)
                st.success(f"Parsed {len(parsed['metrics'])} metrics for {ticker} {period}")
                st.rerun()


def _render_earnings_history_chart(ticker: str, estimates: dict):
    """Render earnings surprise trend chart."""
    history = estimates.get("earnings_history", [])
    past = [e for e in history if e.get("eps_actual") is not None and e.get("eps_estimate") is not None]

    if not past:
        return

    st.subheader("Earnings Surprise History")

    try:
        import plotly.graph_objects as go

        past_reversed = list(reversed(past[:8]))

        dates = [e["date"] for e in past_reversed]
        actuals = [e["eps_actual"] for e in past_reversed]
        estimates_vals = [e["eps_estimate"] for e in past_reversed]
        surprises = [e.get("surprise_pct", 0) or 0 for e in past_reversed]

        fig = go.Figure()

        fig.add_trace(go.Bar(
            x=dates, y=actuals,
            name="Actual EPS",
            marker_color=["#1b5e20" if s >= 0 else "#b71c1c" for s in surprises],
            opacity=0.7,
        ))

        fig.add_trace(go.Scatter(
            x=dates, y=estimates_vals,
            name="Consensus EPS",
            mode="lines+markers",
            line=dict(color="#1a73e8", width=2, dash="dash"),
            marker=dict(size=8),
        ))

        # Add surprise % as text above bars
        for i, (d, a, s) in enumerate(zip(dates, actuals, surprises)):
            fig.add_annotation(
                x=d, y=a,
                text=f"{s:+.1f}%",
                showarrow=False,
                yshift=15,
                font=dict(size=10, color="#1b5e20" if s >= 0 else "#b71c1c"),
            )

        fig.update_layout(
            height=300,
            margin=dict(l=40, r=20, t=30, b=40),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            yaxis_title="EPS ($)",
            plot_bgcolor="white",
            paper_bgcolor="white",
        )

        st.plotly_chart(fig, use_container_width=True)

    except ImportError:
        # Fallback without plotly
        df = pd.DataFrame(past[:8])
        st.dataframe(df, use_container_width=True, hide_index=True)


def _render_comparison_table(comparison: list[dict]):
    """Render the beat/miss comparison table."""
    rows = []
    for c in comparison:
        beat_miss = c["beat_miss"]
        if beat_miss == "beat":
            label = _BEAT_LABEL
        elif beat_miss == "miss":
            label = _MISS_LABEL
        elif beat_miss == "inline":
            label = _INLINE_LABEL
        else:
            label = _NA_LABEL

        rows.append({
            "Metric": c["metric_name"],
            "Consensus": _format_val(c["consensus"], c["unit"]),
            "Actual": _format_val(c["actual"], c["unit"]),
            "Δ": _format_delta(c["delta"], c["unit"]),
            "Δ %": f"{c['delta_pct']:+.1f}%" if c.get("delta_pct") is not None else "—",
            "Result": label,
        })

    df = pd.DataFrame(rows)

    def _color_row(row):
        result = row["Result"]
        if _BEAT_LABEL in result:
            return [_BEAT_STYLE] * len(row)
        elif _MISS_LABEL in result:
            return [_MISS_STYLE] * len(row)
        elif _INLINE_LABEL in result:
            return [_INLINE_STYLE] * len(row)
        return [_NA_STYLE] * len(row)

    styled = df.style.apply(_color_row, axis=1).set_properties(
        **{"font-size": "0.75rem", "padding": "3px 6px"}
    )

    st.dataframe(
        styled,
        use_container_width=True,
        hide_index=True,
        height=min(600, 40 + 35 * len(df)),
    )

    # Summary stats
    beats = sum(1 for c in comparison if c["beat_miss"] == "beat")
    misses = sum(1 for c in comparison if c["beat_miss"] == "miss")
    inlines = sum(1 for c in comparison if c["beat_miss"] == "inline")
    total = beats + misses + inlines

    if total > 0:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Metrics", total)
        c2.metric("Beats", beats, delta=f"{beats/total*100:.0f}%")
        c3.metric("Misses", misses, delta=f"-{misses/total*100:.0f}%")
        c4.metric("Inline", inlines)


def _render_key_metrics(ticker: str, actual_metrics: dict):
    """Show key reported metrics for context."""
    st.markdown("---")
    st.subheader("Key Reported Metrics")

    key_metrics = [
        ("eps", "EPS"), ("nim", "NIM"), ("efficiency_ratio", "Efficiency"),
        ("roaa", "ROAA"), ("roatce", "ROATCE"), ("cet1_ratio", "CET1"),
        ("npl_ratio", "NPL Ratio"), ("tbvps", "TBV/Share"),
    ]

    cols = st.columns(4)
    for i, (key, label) in enumerate(key_metrics):
        val = actual_metrics.get(key)
        unit = METRIC_UNITS.get(key, "")
        with cols[i % 4]:
            st.metric(label, _format_val(val, unit))


# ═══════════════════════════════════════════════════════════════════════════
# AGGREGATE EARNINGS VIEW (Earnings Analysis section)
# ═══════════════════════════════════════════════════════════════════════════

def render_earnings_overview(watchlist: list[str], all_metrics: list[dict]):
    """Render the full earnings analysis section with all features."""

    st.markdown(
        '<div class="dashboard-header">'
        "<h1>Earnings Analysis</h1>"
        "<p>Consensus tracking, earnings calendar & surprise history</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    metrics_by_ticker = {m["ticker"]: m for m in all_metrics}
    all_consensus = list_all_consensus()

    # ── Top KPI bar ─────────────────────────────────────────────────────
    _render_earnings_kpi_bar(watchlist, all_consensus, metrics_by_ticker)

    st.markdown("---")

    # ── Main tabs (reordered by usage priority) ─────────────────────────
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📅 Calendar",
        "🌡 Surprise Heat-Map",
        "📊 Beat / Miss",
        "🎯 Biggest Surprises",
        "📈 Sector Aggregates",
        "📄 Upload / Input",
    ])

    with tab1:
        _render_earnings_calendar(watchlist)
    with tab2:
        _render_surprise_heatmap(watchlist)
    with tab3:
        _render_beat_miss_summary(all_consensus, metrics_by_ticker)
    with tab4:
        _render_surprise_rankings(all_consensus, metrics_by_ticker, watchlist)
    with tab5:
        _render_sector_aggregates(all_consensus, metrics_by_ticker, watchlist)
    with tab6:
        _render_upload_section(watchlist)


def _render_earnings_kpi_bar(watchlist: list[str], all_consensus: dict, metrics_by_ticker: dict):
    """Top summary KPIs across the whole watchlist."""
    from datetime import datetime, date

    # Reports in next 14 days
    try:
        from data.estimates import fetch_earnings_calendar
        cal = fetch_earnings_calendar(tuple(watchlist))
    except Exception:
        cal = []

    today = date.today()
    upcoming_14 = 0
    upcoming_7 = 0
    for entry in cal:
        try:
            ed = datetime.strptime(entry.get("next_earnings_date", ""), "%Y-%m-%d").date()
            days = (ed - today).days
            if 0 <= days <= 7:
                upcoming_7 += 1
            if 0 <= days <= 14:
                upcoming_14 += 1
        except (ValueError, TypeError):
            continue

    # Beat/miss stats across all consensus
    total_beats = 0
    total_misses = 0
    total_inlines = 0
    banks_with_consensus = len(all_consensus)
    for ticker, periods in all_consensus.items():
        if not periods:
            continue
        latest = periods[0]
        consensus = load_consensus(ticker, latest["period"])
        actual = metrics_by_ticker.get(ticker, {})
        if consensus:
            comparison = compare_consensus_to_actual(consensus, actual)
            for c in comparison:
                if c["beat_miss"] == "beat":
                    total_beats += 1
                elif c["beat_miss"] == "miss":
                    total_misses += 1
                elif c["beat_miss"] == "inline":
                    total_inlines += 1

    total_cmp = total_beats + total_misses + total_inlines
    beat_pct = (total_beats / total_cmp * 100) if total_cmp else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric(
            "Reporting This Week",
            upcoming_7,
            delta=f"{upcoming_14} in 14d",
            delta_color="off",
        )
    with c2:
        st.metric("Banks w/ Consensus", banks_with_consensus)
    with c3:
        st.metric(
            "Total Metrics Compared", total_cmp,
            delta=f"{banks_with_consensus} banks" if banks_with_consensus else None,
            delta_color="off",
        )
    with c4:
        st.metric(
            "Beat Rate",
            f"{beat_pct:.0f}%" if total_cmp else "—",
            delta=f"{total_beats}B / {total_misses}M / {total_inlines}I" if total_cmp else None,
            delta_color="off",
        )
    with c5:
        avg_surprise = None
        try:
            # Compute avg EPS surprise from yfinance history
            from data.estimates import fetch_all_estimates
            estimates = fetch_all_estimates(tuple(watchlist[:30]))
            surprises = []
            for t, est in estimates.items():
                for e in est.get("earnings_history", [])[:1]:
                    sp = e.get("surprise_pct")
                    if sp is not None:
                        surprises.append(sp)
            if surprises:
                avg_surprise = sum(surprises) / len(surprises)
        except Exception:
            pass
        st.metric(
            "Last Qtr Avg Surprise",
            f"{avg_surprise:+.1f}%" if avg_surprise is not None else "—",
            delta="EPS vs consensus", delta_color="off",
        )


# ── Surprise Heat-Map ──────────────────────────────────────────────────

def _render_surprise_heatmap(watchlist: list[str]):
    """
    Heat-map: rows = banks, columns = last 8 quarters, cells = EPS surprise %.
    Green = beat, red = miss. Data comes from yfinance earnings history.
    """
    from data.estimates import fetch_all_estimates
    from data.bank_mapping import get_name

    with st.spinner("Loading earnings history..."):
        estimates = fetch_all_estimates(tuple(watchlist))

    # Build rows (banks) × columns (quarters) matrix of surprise %
    # Collect all quarters across all banks, then take most recent 8
    all_quarters = set()
    bank_data = {}
    for ticker, est in estimates.items():
        history = est.get("earnings_history", []) or []
        history = [e for e in history if e.get("surprise_pct") is not None
                   and e.get("eps_actual") is not None]
        if not history:
            continue
        bank_data[ticker] = history
        for e in history:
            all_quarters.add(e.get("date"))

    if not bank_data:
        st.info(
            "No earnings history available yet. Visit a few banks to populate "
            "the cache, or upload consensus files."
        )
        return

    # Sort quarters, keep last 8
    sorted_quarters = sorted(all_quarters, reverse=True)[:8]
    sorted_quarters.reverse()  # chronological left→right

    # Short labels: "2026Q1" style
    def _quarter_label(date_str: str) -> str:
        if not date_str:
            return "—"
        try:
            y, m, _ = date_str.split("-")[:3]
            q = (int(m) - 1) // 3 + 1
            return f"{y}Q{q}"
        except Exception:
            return date_str[:7] if date_str else "—"

    col_labels = [_quarter_label(d) for d in sorted_quarters]
    quarter_to_idx = {d: i for i, d in enumerate(sorted_quarters)}

    # Sort banks by most recent surprise (descending)
    def _sort_key(item):
        ticker, history = item
        most_recent = history[0] if history else {}
        return most_recent.get("surprise_pct") or 0

    bank_data = dict(sorted(bank_data.items(), key=_sort_key, reverse=True))

    # Build matrix
    tickers_list = list(bank_data.keys())
    n_banks = len(tickers_list)
    n_qtrs = len(sorted_quarters)
    matrix = [[None] * n_qtrs for _ in range(n_banks)]
    actual_matrix = [[None] * n_qtrs for _ in range(n_banks)]
    est_matrix = [[None] * n_qtrs for _ in range(n_banks)]

    for i, ticker in enumerate(tickers_list):
        history = bank_data[ticker]
        for e in history:
            d = e.get("date")
            if d in quarter_to_idx:
                j = quarter_to_idx[d]
                matrix[i][j] = e.get("surprise_pct")
                actual_matrix[i][j] = e.get("eps_actual")
                est_matrix[i][j] = e.get("eps_estimate")

    # Bank labels with name
    row_labels = [f"{t}" for t in tickers_list]

    try:
        import plotly.graph_objects as go

        # Build text (surprise %) and hover text
        text_matrix = [
            [
                (f"{v:+.0f}%" if v is not None else "")
                for v in row
            ]
            for row in matrix
        ]
        hover_matrix = [
            [
                (
                    f"{tickers_list[i]} · {col_labels[j]}<br>"
                    f"Consensus: ${est_matrix[i][j]:.2f}<br>"
                    f"Actual: ${actual_matrix[i][j]:.2f}<br>"
                    f"Surprise: {matrix[i][j]:+.1f}%"
                    if matrix[i][j] is not None else ""
                )
                for j in range(n_qtrs)
            ]
            for i in range(n_banks)
        ]

        fig = go.Figure(data=go.Heatmap(
            z=matrix,
            x=col_labels,
            y=row_labels,
            text=text_matrix,
            texttemplate="%{text}",
            textfont={"size": 10},
            customdata=hover_matrix,
            hovertemplate="%{customdata}<extra></extra>",
            colorscale=[
                [0, "#b71c1c"], [0.3, "#ef9a9a"], [0.5, "#fafafa"],
                [0.7, "#a5d6a7"], [1, "#1b5e20"],
            ],
            zmid=0,
            zmin=-20, zmax=20,
            colorbar=dict(
                title=dict(text="Surprise %", side="right"),
                tickvals=[-20, -10, 0, 10, 20],
                ticktext=["-20%", "-10%", "0", "+10%", "+20%"],
                len=0.8,
            ),
        ))
        fig.update_layout(
            title="EPS Surprise History — Heat-Map (last 8 quarters)",
            height=max(300, 30 + 22 * n_banks),
            margin=dict(l=60, r=20, t=50, b=50),
            xaxis=dict(tickangle=0, side="top"),
            yaxis=dict(autorange="reversed"),  # keep sort order top→bottom
            plot_bgcolor="white", paper_bgcolor="white",
            font=dict(size=11),
        )
        st.plotly_chart(fig, use_container_width=True)

        st.caption(
            "Each cell = EPS surprise % for that bank's quarter. "
            "Dark green = big beat, dark red = big miss. "
            "Banks sorted by most recent surprise (best on top)."
        )

        # Summary stats — consistency metrics
        st.markdown("##### Consistency Metrics")
        stats_rows = []
        for ticker in tickers_list:
            history = bank_data[ticker]
            surprises = [e.get("surprise_pct") for e in history
                         if e.get("surprise_pct") is not None][:8]
            if not surprises:
                continue
            beat_count = sum(1 for s in surprises if s > 1)
            miss_count = sum(1 for s in surprises if s < -1)
            inline_count = len(surprises) - beat_count - miss_count
            avg_surprise = sum(surprises) / len(surprises)
            # Std dev as volatility
            if len(surprises) >= 2:
                mean = avg_surprise
                vol = (sum((s - mean) ** 2 for s in surprises) / (len(surprises) - 1)) ** 0.5
            else:
                vol = None
            stats_rows.append({
                "Ticker": ticker,
                "Bank": get_name(ticker)[:30],
                "Beat Rate": f"{beat_count}/{len(surprises)} ({beat_count/len(surprises)*100:.0f}%)",
                "Avg Surprise": f"{avg_surprise:+.1f}%",
                "Volatility": f"{vol:.1f}pp" if vol else "—",
                "Last Qtr": f"{surprises[0]:+.1f}%",
            })
        if stats_rows:
            stats_df = pd.DataFrame(stats_rows)

            def _stats_color(row):
                last_str = row["Last Qtr"]
                try:
                    v = float(last_str.replace("%", "").replace("+", ""))
                except Exception:
                    return [""] * len(row)
                if v > 5: return ["background-color: #e8f5e9;"] * len(row)
                if v > 1: return ["background-color: #f1f8e9;"] * len(row)
                if v < -5: return ["background-color: #ffebee; color: #b71c1c;"] * len(row)
                if v < -1: return ["background-color: #fff3e0;"] * len(row)
                return [""] * len(row)

            styled = stats_df.style.apply(_stats_color, axis=1).set_properties(
                **{"font-size": "0.82rem", "padding": "3px 8px"}
            )
            st.dataframe(styled, use_container_width=True, hide_index=True,
                          height=min(500, 50 + 32 * len(stats_df)))

    except ImportError:
        st.warning("Install plotly to view the heat-map.")


# ── Earnings Calendar ──────────────────────────────────────────────────

def _render_earnings_calendar(watchlist: list[str]):
    """Show upcoming earnings dates for all tracked banks."""
    st.subheader("Upcoming Earnings Dates")

    with st.spinner("Loading earnings calendar..."):
        calendar = fetch_earnings_calendar(tuple(watchlist))

    if not calendar:
        st.info("No earnings dates found. Earnings calendar data may not be available for all banks.")
        return

    # Split into upcoming and past
    from datetime import date
    today = date.today().isoformat()

    upcoming = [c for c in calendar if c.get("next_earnings_date", "9999") >= today]
    past = [c for c in calendar if c.get("next_earnings_date", "") < today]

    if upcoming:
        st.markdown("##### Upcoming Reports")
        rows = []
        for c in upcoming:
            # Calculate days until
            try:
                from datetime import datetime
                ed = datetime.strptime(c["next_earnings_date"], "%Y-%m-%d").date()
                days_until = (ed - date.today()).days
                days_str = f"{days_until}d" if days_until > 0 else "Today"
            except Exception:
                days_str = "—"

            rec = c.get("recommendation", "")
            rec_display = rec.replace("_", " ").title() if rec else "—"

            rows.append({
                "Ticker": c["ticker"],
                "Bank": get_name(c["ticker"]),
                "Report Date": c["next_earnings_date"],
                "Days": days_str,
                "EPS Est (Qtr)": f"${c['eps_estimate']:.2f}" if c.get("eps_estimate") else "—",
                "EPS Est (Ann)": f"${c['eps_fwd_annual']:.2f}" if c.get("eps_fwd_annual") else "—",
                "Price Target": f"${c['target_price']:.2f}" if c.get("target_price") else "—",
                "Rating": rec_display,
                "Analysts": str(c.get("analyst_count", "—")),
            })

        df = pd.DataFrame(rows)

        def _highlight_soon(row):
            days = row.get("Days", "")
            if days == "Today":
                return [_BEAT_STYLE] * len(row)
            try:
                d = int(days.replace("d", ""))
                if d <= 7:
                    return ["background-color: #fff8e1; color: #e65100;"] * len(row)
                elif d <= 14:
                    return ["background-color: #f3f4f6;"] * len(row)
            except (ValueError, AttributeError):
                pass
            return [""] * len(row)

        styled = df.style.apply(_highlight_soon, axis=1).set_properties(
            **{"font-size": "0.75rem", "padding": "3px 6px"}
        )
        st.dataframe(styled, use_container_width=True, hide_index=True,
                      height=min(500, 40 + 35 * len(df)))

        # Summary metrics
        within_7 = sum(1 for c in upcoming if _days_until(c.get("next_earnings_date")) <= 7)
        within_30 = sum(1 for c in upcoming if _days_until(c.get("next_earnings_date")) <= 30)
        cols = st.columns(3)
        cols[0].metric("Total Upcoming", len(upcoming))
        cols[1].metric("This Week", within_7)
        cols[2].metric("This Month", within_30)
    else:
        st.info("No upcoming earnings dates found.")


def _days_until(date_str: str) -> int:
    """Calculate days until a date string."""
    try:
        from datetime import datetime, date
        ed = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (ed - date.today()).days
    except Exception:
        return 999


# ── Beat / Miss Summary ───────────────────────────────────────────────

def _render_beat_miss_summary(all_consensus: dict, metrics_by_ticker: dict):
    """Show beat/miss summary across all banks with consensus data."""
    st.subheader("Beat / Miss Summary")

    if not all_consensus:
        st.info("No consensus data uploaded yet. Go to the Upload / Input tab to add data.")
        return

    rows = []
    for ticker, periods in sorted(all_consensus.items()):
        latest = periods[0] if periods else None
        if not latest:
            continue

        consensus = load_consensus(ticker, latest["period"])
        actual = metrics_by_ticker.get(ticker, {})

        if consensus:
            comparison = compare_consensus_to_actual(consensus, actual)
            beats = sum(1 for c in comparison if c["beat_miss"] == "beat")
            misses = sum(1 for c in comparison if c["beat_miss"] == "miss")
            inlines = sum(1 for c in comparison if c["beat_miss"] == "inline")
            total = beats + misses + inlines

            eps_result = next((c for c in comparison if c["key"] == "eps"), None)
            nim_result = next((c for c in comparison if c["key"] == "nim"), None)

            rows.append({
                "Ticker": ticker,
                "Bank": get_name(ticker),
                "Period": latest["period"],
                "Metrics": total,
                "Beats": beats,
                "Misses": misses,
                "Inline": inlines,
                "EPS Δ": _format_delta(
                    eps_result["delta"], eps_result["unit"]
                ) if eps_result and eps_result["delta"] is not None else "—",
                "NIM Δ": _format_delta(
                    nim_result["delta"], nim_result["unit"]
                ) if nim_result and nim_result["delta"] is not None else "—",
                "Score": f"{beats}/{total}" if total > 0 else "—",
                "_beats": beats,
                "_misses": misses,
            })

    if rows:
        df_full = pd.DataFrame(rows)
        display_cols = [c for c in df_full.columns if not c.startswith("_")]
        df = df_full[display_cols].copy()

        # Build a lookup for beat/miss coloring
        beat_counts = df_full["_beats"].tolist()
        miss_counts = df_full["_misses"].tolist()

        def _color_score(row):
            idx = row.name
            b = beat_counts[idx] if idx < len(beat_counts) else 0
            m = miss_counts[idx] if idx < len(miss_counts) else 0
            if b > m:
                return [_BEAT_STYLE] * len(row)
            elif m > b:
                return [_MISS_STYLE] * len(row)
            return [""] * len(row)

        styled = df.style.apply(_color_score, axis=1).set_properties(
            **{"font-size": "0.75rem", "padding": "3px 6px"}
        )

        st.dataframe(
            styled,
            use_container_width=True,
            hide_index=True,
            height=min(600, 40 + 35 * len(df)),
        )
    else:
        st.info("No consensus comparisons available yet.")


# ── Surprise Rankings ──────────────────────────────────────────────────

def _render_surprise_rankings(all_consensus: dict, metrics_by_ticker: dict, watchlist: list[str]):
    """Rank banks by biggest beats and misses."""
    st.subheader("Surprise Magnitude Rankings")

    if not all_consensus:
        st.info("No consensus data available. Upload estimates to see surprise rankings.")
        return

    # Also include yfinance earnings history
    all_surprises = []

    # From uploaded consensus data
    for ticker, periods in all_consensus.items():
        latest = periods[0] if periods else None
        if not latest:
            continue

        consensus = load_consensus(ticker, latest["period"])
        actual = metrics_by_ticker.get(ticker, {})

        if consensus:
            comparison = compare_consensus_to_actual(consensus, actual)
            for c in comparison:
                if c.get("delta_pct") is not None and c["beat_miss"] != "n/a":
                    all_surprises.append({
                        "Ticker": ticker,
                        "Bank": get_name(ticker),
                        "Metric": c["metric_name"],
                        "Period": latest["period"],
                        "Consensus": _format_val(c["consensus"], c["unit"]),
                        "Actual": _format_val(c["actual"], c["unit"]),
                        "Surprise %": c["delta_pct"],
                        "Result": c["beat_miss"],
                        "Source": "Uploaded",
                    })

    # From yfinance earnings history
    with st.spinner("Loading historical surprises..."):
        estimates = fetch_all_estimates(tuple(watchlist[:30]))

    for ticker, est in estimates.items():
        for e in est.get("earnings_history", [])[:4]:
            if e.get("surprise_pct") is not None and e.get("eps_actual") is not None:
                all_surprises.append({
                    "Ticker": ticker,
                    "Bank": get_name(ticker),
                    "Metric": "EPS",
                    "Period": e.get("date", ""),
                    "Consensus": f"${e['eps_estimate']:.2f}" if e.get("eps_estimate") else "—",
                    "Actual": f"${e['eps_actual']:.2f}",
                    "Surprise %": e["surprise_pct"],
                    "Result": "beat" if e["surprise_pct"] > 1 else ("miss" if e["surprise_pct"] < -1 else "inline"),
                    "Source": "Yahoo Finance",
                })

    if not all_surprises:
        st.info("No surprise data available yet.")
        return

    # Sort by absolute surprise
    all_surprises.sort(key=lambda x: abs(x.get("Surprise %", 0)), reverse=True)

    # Filter controls
    fc1, fc2 = st.columns(2)
    with fc1:
        filter_type = st.selectbox(
            "Show",
            ["All", "Beats Only", "Misses Only"],
            key="surprise_filter",
        )
    with fc2:
        metric_filter = st.selectbox(
            "Metric",
            ["All Metrics", "EPS Only", "NIM Only", "Efficiency Only"],
            key="surprise_metric_filter",
        )

    filtered = all_surprises
    if filter_type == "Beats Only":
        filtered = [s for s in filtered if s["Result"] == "beat"]
    elif filter_type == "Misses Only":
        filtered = [s for s in filtered if s["Result"] == "miss"]

    if metric_filter == "EPS Only":
        filtered = [s for s in filtered if "EPS" in s["Metric"] or "Earnings" in s["Metric"]]
    elif metric_filter == "NIM Only":
        filtered = [s for s in filtered if "NIM" in s["Metric"] or "Interest Margin" in s["Metric"]]
    elif metric_filter == "Efficiency Only":
        filtered = [s for s in filtered if "Efficiency" in s["Metric"]]

    if filtered:
        df = pd.DataFrame(filtered[:50])
        df["Surprise %"] = df["Surprise %"].apply(lambda x: f"{x:+.2f}%")

        # Color by result
        def _color_surprise(row):
            result = row.get("Result", "")
            if result == "beat":
                return [_BEAT_STYLE] * len(row)
            elif result == "miss":
                return [_MISS_STYLE] * len(row)
            elif result == "inline":
                return [_INLINE_STYLE] * len(row)
            return [""] * len(row)

        display_cols = ["Ticker", "Bank", "Metric", "Period", "Consensus", "Actual", "Surprise %", "Source"]
        styled = df[display_cols].style.apply(_color_surprise, axis=1).set_properties(
            **{"font-size": "0.75rem", "padding": "3px 6px"}
        )

        st.dataframe(styled, use_container_width=True, hide_index=True,
                      height=min(600, 40 + 35 * len(df)))

        # Top beats / top misses summary
        top_beats = [s for s in all_surprises if s["Result"] == "beat"][:5]
        top_misses = [s for s in all_surprises if s["Result"] == "miss"][:5]

        bc1, bc2 = st.columns(2)
        with bc1:
            st.markdown("##### Biggest Beats")
            for s in top_beats:
                st.markdown(
                    f"**{s['Ticker']}** {s['Metric']}: {s['Surprise %']:+.1f}% "
                    f"({s['Consensus']} → {s['Actual']})"
                )
        with bc2:
            st.markdown("##### Biggest Misses")
            for s in top_misses:
                st.markdown(
                    f"**{s['Ticker']}** {s['Metric']}: {s['Surprise %']:+.1f}% "
                    f"({s['Consensus']} → {s['Actual']})"
                )
    else:
        st.info("No surprises match the current filter.")


# ── Sector Aggregates ──────────────────────────────────────────────────

def _render_sector_aggregates(all_consensus: dict, metrics_by_ticker: dict, watchlist: list[str]):
    """Show aggregate beat/miss statistics across all banks."""
    st.subheader("Sector Aggregate Statistics")

    # Collect per-metric stats from uploaded consensus
    metric_stats = {}  # key -> {beats, misses, inlines, total, avg_surprise}

    for ticker, periods in all_consensus.items():
        latest = periods[0] if periods else None
        if not latest:
            continue

        consensus = load_consensus(ticker, latest["period"])
        actual = metrics_by_ticker.get(ticker, {})

        if consensus:
            comparison = compare_consensus_to_actual(consensus, actual)
            for c in comparison:
                if c["beat_miss"] == "n/a":
                    continue

                key = c["key"] or c["metric_name"]
                if key not in metric_stats:
                    metric_stats[key] = {
                        "name": c["metric_name"],
                        "beats": 0, "misses": 0, "inlines": 0,
                        "total": 0, "surprises": [],
                    }

                metric_stats[key]["total"] += 1
                if c["beat_miss"] == "beat":
                    metric_stats[key]["beats"] += 1
                elif c["beat_miss"] == "miss":
                    metric_stats[key]["misses"] += 1
                elif c["beat_miss"] == "inline":
                    metric_stats[key]["inlines"] += 1

                if c.get("delta_pct") is not None:
                    metric_stats[key]["surprises"].append(c["delta_pct"])

    # Also collect EPS surprises from yfinance
    with st.spinner("Loading sector data..."):
        estimates = fetch_all_estimates(tuple(watchlist[:30]))

    yf_eps_stats = {"beats": 0, "misses": 0, "inlines": 0, "total": 0, "surprises": []}
    for ticker, est in estimates.items():
        for e in est.get("earnings_history", [])[:1]:  # Most recent quarter only
            if e.get("surprise_pct") is not None:
                yf_eps_stats["total"] += 1
                s = e["surprise_pct"]
                yf_eps_stats["surprises"].append(s)
                if s > 1:
                    yf_eps_stats["beats"] += 1
                elif s < -1:
                    yf_eps_stats["misses"] += 1
                else:
                    yf_eps_stats["inlines"] += 1

    # Display overall sector stats from yfinance
    if yf_eps_stats["total"] > 0:
        st.markdown("##### EPS Surprises Across Universe (Latest Quarter)")
        total = yf_eps_stats["total"]
        beats = yf_eps_stats["beats"]
        misses = yf_eps_stats["misses"]
        inlines = yf_eps_stats["inlines"]

        mc1, mc2, mc3, mc4, mc5 = st.columns(5)
        mc1.metric("Banks Reporting", total)
        mc2.metric("Beat %", f"{beats/total*100:.0f}%" if total else "—")
        mc3.metric("Miss %", f"{misses/total*100:.0f}%" if total else "—")
        mc4.metric("Inline %", f"{inlines/total*100:.0f}%" if total else "—")
        avg_s = sum(yf_eps_stats["surprises"]) / len(yf_eps_stats["surprises"]) if yf_eps_stats["surprises"] else 0
        mc5.metric("Avg Surprise", f"{avg_s:+.1f}%")

        # Beat/miss bar chart
        try:
            import plotly.graph_objects as go
            fig = go.Figure(data=[
                go.Bar(name="Beat", x=["EPS"], y=[beats], marker_color="#1b5e20"),
                go.Bar(name="Inline", x=["EPS"], y=[inlines], marker_color="#e65100"),
                go.Bar(name="Miss", x=["EPS"], y=[misses], marker_color="#b71c1c"),
            ])
            fig.update_layout(
                barmode="stack", height=200,
                margin=dict(l=40, r=20, t=20, b=30),
                plot_bgcolor="white", paper_bgcolor="white",
                legend=dict(orientation="h"),
            )
            st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            pass

        st.markdown("---")

    # Per-metric breakdown from uploaded consensus
    if metric_stats:
        st.markdown("##### Per-Metric Breakdown (Uploaded Consensus)")

        rows = []
        for key, stats in sorted(metric_stats.items(), key=lambda x: x[1]["total"], reverse=True):
            total = stats["total"]
            avg_surprise = sum(stats["surprises"]) / len(stats["surprises"]) if stats["surprises"] else 0

            rows.append({
                "Metric": stats["name"],
                "Banks": total,
                "Beat": stats["beats"],
                "Miss": stats["misses"],
                "Inline": stats["inlines"],
                "Beat %": f"{stats['beats']/total*100:.0f}%" if total else "—",
                "Avg Surprise": f"{avg_surprise:+.1f}%",
            })

        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
    elif not yf_eps_stats["total"]:
        st.info("No aggregate data available yet. Upload consensus estimates to see sector-level statistics.")


# ── Upload / Input Section ─────────────────────────────────────────────

def _render_upload_section(watchlist: list[str]):
    """Upload and manual input for the aggregate view."""
    st.subheader("Add Consensus Estimates")

    input_method = st.radio(
        "Input method",
        ["📄 Bulk Upload (Multi-Bank)", "📝 Manual Entry (Single Bank)", "📄 Single File Upload"],
        key="overview_input_method",
        horizontal=True,
    )

    if input_method == "📄 Bulk Upload (Multi-Bank)":
        _render_bulk_upload()

    elif input_method == "📝 Manual Entry (Single Bank)":
        uc1, uc2 = st.columns([1, 1])

        with uc1:
            upload_ticker = st.selectbox(
                "Bank",
                options=[""] + sorted(watchlist),
                format_func=lambda t: f"{t} — {get_name(t)}" if t else "Select a bank...",
                key="manual_overview_ticker",
            )
            custom_ticker = st.text_input(
                "Or type ticker",
                placeholder="Any ticker...",
                key="manual_overview_custom_ticker",
            )
            if custom_ticker:
                upload_ticker = custom_ticker.strip().upper()

        with uc2:
            upload_period = st.text_input(
                "Period",
                placeholder="e.g. 2026Q1",
                key="manual_overview_period",
            )

        if upload_ticker:
            _render_manual_input(upload_ticker)

    else:
        u_col1, u_col2, u_col3 = st.columns([2, 1, 1])
        with u_col1:
            uploaded = st.file_uploader(
                "Consensus file (PDF or Excel)",
                type=["pdf", "xlsx", "xls", "csv"],
                key="earnings_overview_upload",
            )
        with u_col2:
            upload_ticker = st.text_input(
                "Ticker",
                placeholder="e.g. SFST",
                key="earnings_overview_ticker",
            )
        with u_col3:
            upload_period = st.text_input(
                "Period",
                placeholder="e.g. 2026Q1",
                key="earnings_overview_period",
            )

        if uploaded and upload_ticker and upload_period:
            with st.spinner("Parsing consensus document..."):
                file_bytes = uploaded.read()
                filename = uploaded.name.lower()
                ticker_clean = upload_ticker.strip().upper()

                if filename.endswith(".pdf"):
                    parsed = parse_consensus_pdf(file_bytes, ticker_clean, upload_period)
                else:
                    parsed = parse_consensus_excel(file_bytes, ticker_clean, upload_period, filename)

                if parsed.get("error"):
                    st.error(f"Error parsing: {parsed['error']}")
                elif not parsed.get("metrics"):
                    st.warning("No metrics found in the document.")
                else:
                    save_consensus(parsed)
                    st.success(f"Parsed {len(parsed['metrics'])} metrics for {ticker_clean} {upload_period}")
                    st.rerun()


def _render_bulk_upload():
    """Render the bulk multi-bank consensus upload section."""

    st.markdown("""
    Upload a single file with consensus estimates for **multiple banks**. Supported formats:

    **Excel/CSV — Wide format** (one row per bank):
    | Ticker | EPS | NIM | Efficiency | ROATCE | Net Income | TBV |
    |--------|-----|-----|-----------|--------|-----------|-----|
    | JPM | 5.44 | 2.75 | 55.2 | 18.5 | 14500 | 72.50 |
    | BAC | 0.82 | 1.95 | 62.1 | 12.3 | 7200 | 25.80 |

    **Excel/CSV — Long format** (one metric per row):
    | Ticker | Metric | Value |
    |--------|--------|-------|
    | JPM | EPS | 5.44 |
    | JPM | NIM | 2.75 |

    **PDF** — Broker research reports, sector summaries, or any PDF with consensus estimates for multiple banks. AI will extract tickers and metrics automatically.
    """)

    bc1, bc2 = st.columns([3, 1])

    with bc1:
        bulk_file = st.file_uploader(
            "Upload multi-bank consensus file",
            type=["xlsx", "xls", "csv", "pdf"],
            key="bulk_consensus_upload",
            help="Excel, CSV, or PDF with consensus estimates for multiple banks",
        )

    with bc2:
        bulk_period = st.text_input(
            "Period (applies to all)",
            placeholder="e.g. 2026Q1",
            key="bulk_consensus_period",
        )

    if bulk_file and bulk_period:
        if st.button("📥 Process Bulk Upload", type="primary", key="bulk_process"):
            file_bytes = bulk_file.read()
            filename = bulk_file.name.lower()

            if filename.endswith(".pdf"):
                with st.spinner("AI is reading PDF and extracting consensus estimates for all banks..."):
                    result = parse_bulk_consensus_pdf(file_bytes, bulk_period.strip())
            else:
                with st.spinner("Parsing multi-bank consensus file..."):
                    result = parse_bulk_consensus(file_bytes, bulk_period.strip(), filename)

            # Show results
            if result["errors"]:
                for err in result["errors"]:
                    st.error(err)

            if result["results"]:
                st.success(
                    f"✅ Loaded consensus for **{result['total_banks']} banks** "
                    f"({result['total_metrics']} total metrics) for period {bulk_period}"
                )

                # Show detail table
                df = pd.DataFrame(result["results"])
                df = df.rename(columns={
                    "ticker": "Ticker",
                    "period": "Period",
                    "metrics_count": "Metrics",
                    "status": "Status",
                })

                def _color_status(row):
                    if row.get("Status") == "saved":
                        return [_BEAT_STYLE] * len(row)
                    return [_MISS_STYLE] * len(row)

                styled = df.style.apply(_color_status, axis=1).set_properties(
                    **{"font-size": "0.75rem", "padding": "3px 6px"}
                )
                st.dataframe(styled, use_container_width=True, hide_index=True)

                st.rerun()
            elif not result["errors"]:
                st.warning("No banks or metrics found in the file. Check the format above.")
