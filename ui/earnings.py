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

import html as _html

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
from utils.chart_style import (
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_DANGER,
    apply_standard_layout,
    CHART_HEIGHT_FULL,
    CHART_HEIGHT_COMPACT,
)
from ui.chrome import table_export, title_bar, ledger


# ── Beat/miss styling ───────────────────────────────────────────────────
_BEAT_STYLE = "background-color: rgba(5, 150, 105, 0.08); color: #059669; font-weight: 600;"
_MISS_STYLE = "background-color: rgba(220, 38, 38, 0.08); color: #dc2626; font-weight: 600;"
_INLINE_STYLE = "background-color: rgba(217, 119, 6, 0.08); color: #d97706;"
_NA_STYLE = "color: #999;"

_BEAT_LABEL = "Beat"
_MISS_LABEL = "Miss"
_INLINE_LABEL = "Inline"
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
    title_bar(f"{bank_name} ({ticker})", "Earnings vs Consensus", ids_html="")

    # ── Auto-populated estimates from yfinance ──────────────────────────
    with st.spinner("Loading analyst estimates..."):
        estimates = fetch_estimates_cached(ticker)

    if estimates and not estimates.get("error"):
        _render_auto_estimates(ticker, estimates)

    st.markdown("---")

    # ── Tabs for input methods ──────────────────────────────────────────
    input_tab1, input_tab2 = st.tabs(["Manual Input", "Upload File"])

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

    from ui.source_trace import render_traceable_cards, make_calc
    from data.bank_mapping import get_name
    entity = f"{get_name(ticker)} ({ticker})"

    ned = estimates.get("next_earnings_date")
    eps_est = estimates.get("eps_estimate"); eps_fwd = estimates.get("eps_fwd_annual")
    target = estimates.get("target_price"); analysts = estimates.get("analyst_count")
    SRC = "Analyst consensus (market data)"

    def analyst_card(label, value, definition):
        # Forward-looking analyst consensus — sourced from market data, NOT a
        # filing (clearly distinguished from the reported/computed figures).
        return {"label": label, "value": value,
                "calc": make_calc(label, value, entity=entity, source=SRC, asof="latest consensus",
                                  unit="estimate", ref="forward analyst estimate",
                                  definition=definition,
                                  terms=[{"label": label, "val": value}], reported=True)}

    cards = [
        analyst_card("Next Earnings", (ned if ned else "Unknown"),
                     "Estimated date of the next quarterly earnings release."),
        analyst_card("EPS Est (Next Qtr)", (f"${eps_est:.2f}" if eps_est else "—"),
                     "Consensus analyst estimate for next-quarter diluted EPS."),
        analyst_card("EPS Est (Annual)", (f"${eps_fwd:.2f}" if eps_fwd else "—"),
                     "Consensus analyst estimate for forward annual diluted EPS."),
        analyst_card("Avg Price Target", (f"${target:.2f}" if target else "—"),
                     "Average of analysts' 12-month price targets."),
        analyst_card("Analyst Coverage", (str(analysts) if analysts else "—"),
                     "Number of sell-side analysts contributing estimates."),
    ]
    render_traceable_cards(cards, key=f"earn_estimates_{ticker}", columns=5)

    # Price target range
    t_low = estimates.get("target_low")
    t_high = estimates.get("target_high")
    rec = estimates.get("recommendation")
    if t_low and t_high:
        st.caption(
            (f"Price target range: ${t_low:.2f} – ${t_high:.2f}"
             + (f" · Consensus: {rec.replace('_', ' ').title()}" if rec else "")
             ).replace("$", "\\$")  # avoid $X – $Y rendering as LaTeX
        )

    # Offer to auto-populate consensus from estimates
    if estimates.get("earnings_history"):
        past = [e for e in estimates["earnings_history"] if e.get("eps_estimate") is not None]
        if past:
            with st.expander("Past Earnings Surprises (from Yahoo Finance)"):
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
                    # Underlying numeric history (unformatted EPS / surprise)
                    table_export(pd.DataFrame(past[:8]),
                                 f"earnings_surprises_{ticker}",
                                 key=f"exp_earnings_surprises_{ticker}")


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

    if st.button("Save Consensus", key=f"save_manual_{ticker}", type="primary"):
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
                try:
                    save_consensus(parsed)
                except IOError as e:
                    st.error(str(e))
                else:
                    st.success(f"Parsed {len(parsed['metrics'])} metrics for {ticker} {period}")
                    st.rerun()


def _render_earnings_history_chart(ticker: str, estimates: dict):
    """Render earnings surprise trend chart."""
    history = estimates.get("earnings_history", [])
    past = [e for e in history if e.get("eps_actual") is not None and e.get("eps_estimate") is not None]

    if not past:
        return

    st.subheader("Earnings Surprise History")

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
        marker_color=[COLOR_SUCCESS if s >= 0 else COLOR_DANGER for s in surprises],
        opacity=0.7,
    ))

    fig.add_trace(go.Scatter(
        x=dates, y=estimates_vals,
        name="Consensus EPS",
        mode="lines+markers",
        line=dict(color=COLOR_PRIMARY, width=2, dash="dash"),
        marker=dict(size=8),
    ))

    # Add surprise % as text above bars
    for i, (d, a, s) in enumerate(zip(dates, actuals, surprises)):
        fig.add_annotation(
            x=d, y=a,
            text=f"{s:+.1f}%",
            showarrow=False,
            yshift=15,
            font=dict(size=10, color=COLOR_SUCCESS if s >= 0 else COLOR_DANGER),
        )

    apply_standard_layout(fig, height=CHART_HEIGHT_FULL, yaxis_title="EPS ($)")

    st.plotly_chart(fig, use_container_width=True)



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
    # Underlying numeric comparison (unformatted consensus/actual/deltas)
    table_export(pd.DataFrame(comparison), "consensus_vs_actual",
                 key="exp_consensus_vs_actual")

    # Summary stats
    beats = sum(1 for c in comparison if c["beat_miss"] == "beat")
    misses = sum(1 for c in comparison if c["beat_miss"] == "miss")
    inlines = sum(1 for c in comparison if c["beat_miss"] == "inline")
    total = beats + misses + inlines

    if total > 0:
        ledger("Consensus Summary", [
            ("Total Metrics", str(total)),
            ("Beats", f'{beats} <span style="color:var(--success);font-size:var(--fs-xs)">{beats/total*100:.0f}%</span>'),
            ("Misses", f'{misses} <span style="color:var(--danger);font-size:var(--fs-xs)">{misses/total*100:.0f}%</span>'),
            ("Inline", str(inlines)),
        ])


def _render_key_metrics(ticker: str, actual_metrics: dict):
    """Show key reported metrics — every value click-to-source (same provenance
    as the Overview cards): FDIC ratios → Call Report, SEC per-share → 10-K/10-Q,
    ROATCE → formula + inputs, with the one-time-item flag preserved."""
    st.markdown("---")
    st.subheader("Key Reported Metrics")

    from ui.source_trace import render_traceable_cards, fdic_calc, make_calc, sec_doc_for
    from ui.financial_highlights import _fdic_doc, _disp_date, _num, _thou
    from data.bank_mapping import get_fdic_cert, get_cik, get_name
    from data import fdic_client, sec_client

    cert = get_fdic_cert(ticker); cik = get_cik(ticker)
    entity = f"{get_name(ticker)} ({ticker})"
    rec = (fdic_client.get_latest_financials(cert) or {}) if cert else {}
    facts = sec_client.fetch_company_facts(cik) if cik else {}
    fund = (sec_client.get_latest_fundamentals(cik) or {}) if cik else {}
    cr_doc = _fdic_doc(cert, rec.get("REPDTE")) if (cert and rec.get("REPDTE")) else None
    asof = _disp_date(rec.get("REPDTE")) if rec.get("REPDTE") else "latest"
    eps_doc = sec_doc_for(cik, facts, "EarningsPerShareDiluted", instant=False) if facts else None
    eq_doc = sec_doc_for(cik, facts, "StockholdersEquity", instant=True) if facts else None
    sh_doc = ((sec_doc_for(cik, facts, "EntityCommonStockSharesOutstanding", instant=True, ns="dei")
               or sec_doc_for(cik, facts, "CommonStockSharesOutstanding", instant=True)) if facts else None)

    def fmt(key):
        return _format_val(actual_metrics.get(key), METRIC_UNITS.get(key, "%" if "roatce" in key else ""))

    distorted = bool(actual_metrics.get("earnings_distorted"))
    rn = actual_metrics.get("roatce_normalized")
    shares = _num(fund.get("shares_outstanding")); equity = _num(fund.get("book_value_total"))
    tbvps = _num(actual_metrics.get("tbvps"))
    tce = (tbvps * shares) if (tbvps is not None and shares) else None
    adj = (equity - tce) if (equity is not None and tce is not None) else None
    ni = _num(rec.get("NETINC")); eqf = _num(rec.get("EQTOT")); intanf = _num(rec.get("INTAN")) or 0
    tcef = (eqf - intanf) if eqf is not None else None

    def fdic_card(label, field, key, defi):
        return {"label": label, "value": fmt(key),
                "calc": fdic_calc(label, field, rec, cert, unit="%", entity=entity,
                                  value=fmt(key), reported=True, definition=defi)}

    cards = [
        {"label": "EPS", "value": fmt("eps"),
         "calc": make_calc("Diluted EPS (TTM)", fmt("eps"), entity=entity,
                           source="SEC filing (10-K/10-Q)", asof=(eps_doc or {}).get("label", "latest filing"),
                           unit="$ / share", ref="XBRL EarningsPerShareDiluted",
                           definition="Trailing-twelve-month diluted EPS from the holding company's filings.",
                           terms=[{"label": "Diluted EPS (TTM, reported)", "val": fmt("eps"), "doc": eps_doc}],
                           reported=True, link=(eps_doc or {}).get("url"))},
        fdic_card("NIM", "NIMY", "nim", "Net interest income as a percent of average earning assets."),
        fdic_card("Efficiency", "EEFFR", "efficiency_ratio",
                  "Non-interest expense ÷ (net interest income + non-interest income)."),
        fdic_card("ROAA", "ROA", "roaa", "Annualized net income as a percent of average assets."),
        {"label": ("ROATCE (one-time item)" if (distorted and rn is not None) else "ROATCE"), "value": fmt("roatce_blended"),
         "calc": make_calc("ROATCE", fmt("roatce_blended"), entity=entity, source="FDIC Call Report",
                           asof=asof, unit="%", ref="Computed from Call Report",
                           definition=("Blended trailing net income ÷ tangible common equity."
                                       + (f" One-time item inflated earnings — sustainable ≈ {rn:.1f}%."
                                          if (distorted and rn is not None) else "")),
                           terms=[{"label": "Tangible common equity",
                                   "val": (_thou(tcef) + " ($000)" if tcef is not None else "—"), "doc": cr_doc,
                                   "sub": (f"Equity {_thou(eqf)} − Intangibles {_thou(intanf)}" if eqf is not None else None)},
                                  {"label": "Net income", "val": "trailing-twelve-months",
                                   "sub": "blended across recent quarters — see Financials tab"}],
                           op="Net income ÷ tangible common equity × 100", reported=False,
                           link=(cr_doc or {}).get("url"))},
        fdic_card("CET1", "IDT1CER", "cet1_ratio",
                  "Common equity tier 1 capital ÷ risk-weighted assets (bank-level)."),
        fdic_card("NPL Ratio", "NCLNLSR", "npl_ratio", "Non-current loans as a percent of total loans."),
        {"label": "TBV/Share", "value": fmt("tbvps"),
         "calc": make_calc("Tangible BV / share", fmt("tbvps"), entity=entity,
                           source="SEC filing (10-K/10-Q)", asof=(eq_doc or {}).get("label", "latest filing"),
                           unit="$ / share", ref="(equity − intangibles) ÷ shares",
                           definition="Tangible common equity (equity − intangibles) ÷ shares outstanding.",
                           terms=[{"label": "Tangible common equity", "val": (_thou((tce or 0) / 1000) + " ($000)"),
                                   "doc": eq_doc,
                                   "sub": (f"Equity {_thou((equity or 0)/1000)} − intangibles "
                                           f"{_thou((adj or 0)/1000)} ($000)")},
                                  {"label": "Shares outstanding",
                                   "val": (f"{shares:,.0f}" if shares else "—"), "doc": sh_doc}],
                           op="Tangible common equity ÷ shares")},
    ]
    render_traceable_cards(cards, key=f"earn_keymetrics_{ticker}", columns=4)


# ═══════════════════════════════════════════════════════════════════════════
# AGGREGATE EARNINGS VIEW (Earnings Analysis section)
# ═══════════════════════════════════════════════════════════════════════════

def render_earnings_overview(watchlist: list[str], all_metrics: list[dict]):
    """Render the full earnings analysis section with all features."""

    title_bar("KSK Investors", "Earnings Analysis")

    metrics_by_ticker = {m["ticker"]: m for m in all_metrics}
    all_consensus = list_all_consensus()

    # ── Top KPI bar ─────────────────────────────────────────────────────
    _render_earnings_kpi_bar(watchlist, all_consensus, metrics_by_ticker)

    st.markdown("---")

    # ── Main tabs (reordered by usage priority) ─────────────────────────
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "Calendar",
        "Calls & Webcasts",
        "Surprise Heat-Map",
        "Beat / Miss",
        "Biggest Surprises",
        "Sector Aggregates",
        "Upload / Input",
    ])

    with tab1:
        _render_earnings_calendar(watchlist)
    with tab2:
        _render_calls_webcasts()
    with tab3:
        _render_surprise_heatmap(watchlist)
    with tab4:
        _render_beat_miss_summary(all_consensus, metrics_by_ticker)
    with tab5:
        _render_surprise_rankings(all_consensus, metrics_by_ticker, watchlist)
    with tab6:
        _render_sector_aggregates(all_consensus, metrics_by_ticker, watchlist)
    with tab7:
        _render_upload_section(watchlist)


def _render_earnings_kpi_bar(watchlist: list[str], all_consensus: dict, metrics_by_ticker: dict):
    """Top summary KPIs across the whole watchlist."""
    from datetime import datetime, date

    # Reports in next 14 days. A feed failure must NOT display as "0 reporting"
    # — that's a confident wrong number; show unavailable instead.
    cal_failed = False
    try:
        from data.estimates import fetch_earnings_calendar
        cal = fetch_earnings_calendar(tuple(watchlist))
    except Exception as e:
        print(f"[earnings] calendar fetch failed: {type(e).__name__}: {e}")
        cal = []
        cal_failed = True

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
    except Exception as e:
        # Renders "—" below, which is honest; log so the failure is visible.
        print(f"[earnings] surprise fetch failed: {type(e).__name__}: {e}")

    _m = "color:var(--text-muted);font-size:var(--fs-xs)"
    ledger("Earnings Summary", [
        ("Reporting This Week",
         (f'n/a <span style="{_m}">calendar feed unavailable</span>' if cal_failed
          else f'{upcoming_7} <span style="{_m}">{upcoming_14} in 14d</span>')),
        ("Banks w/ Consensus", str(banks_with_consensus)),
        ("Total Metrics Compared",
         f'{total_cmp}' + (f' <span style="{_m}">{banks_with_consensus} banks</span>'
                           if banks_with_consensus else "")),
        ("Beat Rate",
         (f'{beat_pct:.0f}% <span style="{_m}">{total_beats}B / {total_misses}M / {total_inlines}I</span>'
          if total_cmp else "—")),
        ("Last Qtr Avg Surprise",
         (f'{avg_surprise:+.1f}% <span style="{_m}">EPS vs consensus</span>'
          if avg_surprise is not None else "—")),
    ])


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

    # Labels show the FISCAL quarter the results cover — the most recent
    # completed calendar quarter before the announcement date. (Binning the
    # raw announcement date shifted every column one quarter late: Q4-2025
    # results announced 2026-01 were labeled "2026Q1".)
    def _quarter_label(date_str: str) -> str:
        if not date_str:
            return "—"
        try:
            y, m = int(date_str[:4]), int(date_str[5:7])
            ann_q = (m - 1) // 3 + 1   # calendar quarter of the announcement
            fy, fq = (y, ann_q - 1) if ann_q > 1 else (y - 1, 4)
            return f"{fy}Q{fq}"
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
            [0, COLOR_DANGER], [0.3, "#ef9a9a"], [0.5, "#fafafa"],
            [0.7, "#a5d6a7"], [1, COLOR_SUCCESS],
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
        "Each cell = EPS surprise % for that bank's FISCAL quarter (the "
        "completed quarter the announcement covered). "
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
            if v > 5: return ["background-color: rgba(5, 150, 105, 0.08);"] * len(row)
            if v > 1: return ["background-color: #f1f8e9;"] * len(row)
            if v < -5: return ["background-color: rgba(220, 38, 38, 0.08); color: #dc2626;"] * len(row)
            if v < -1: return ["background-color: #fff3e0;"] * len(row)
            return [""] * len(row)

        styled = stats_df.style.apply(_stats_color, axis=1).set_properties(
            **{"font-size": "0.82rem", "padding": "3px 8px"}
        )
        st.dataframe(styled, use_container_width=True, hide_index=True,
                      height=min(500, 50 + 32 * len(stats_df)))
        # Display frame (formatted) — stats are built as strings here
        table_export(stats_df, "earnings_consistency_metrics",
                     key="exp_earnings_consistency_metrics")



# ── SNL-style grid for the earnings tables ────────────────────────────

def _tk_cell(ticker: str) -> str:
    """Ticker cell deep-linking to the Company page (matches generic_table)."""
    tk = _html.escape(str(ticker or ""))
    if not tk:
        return '<td></td>'
    return (f'<td><a class="tk" href="?bank={_html.escape(str(ticker), quote=True)}" '
            f'target="_self">{tk}</a></td>')


def _render_earnings_grid(headers, body_rows, max_height: int = 560):
    """Render an SNL-style `ksk-grid` HTML table (design-system look used across
    the site) — hairline grid, small-caps headers, tabular right-aligned cells.
    Replaces st.dataframe here, which can't carry per-row links and renders a
    literal "None" for empty cells. `headers` is a list of (label, cls) where cls
    is "" (right-aligned, default) or "nm" (left-aligned text); `body_rows` is a
    list of pre-built <tr>…</tr> strings."""
    head = "<tr>" + "".join(
        f'<th class="{cls}">{_html.escape(lbl)}</th>' for lbl, cls in headers) + "</tr>"
    css = (
        ".ern-wrap{max-height:" + str(max_height) + "px;overflow:auto;"
        "border:0.5px solid var(--grid-head);}"
        ".ern-wrap thead th{position:sticky;top:0;z-index:2;}"
        ".ern-grid td.nm,.ern-grid th.nm{text-align:left;color:var(--text-secondary);"
        "max-width:230px;overflow:hidden;text-overflow:ellipsis;}"
        ".ern-grid a.tk{font-weight:700;text-decoration:none;color:var(--brand-primary);}"
        ".ern-grid a.lnk{text-decoration:none;color:var(--brand-primary);font-weight:600;}"
        ".ern-grid td.mut{color:var(--text-muted);}"
        ".ern-grid tr.soon td{background:rgba(217,119,6,0.07);}"
    )
    st.markdown(
        f"<style>{css}</style>"
        f'<div class="ern-wrap"><table class="ksk-grid ern-grid">'
        f'<thead>{head}</thead><tbody>{"".join(body_rows)}</tbody></table></div>',
        unsafe_allow_html=True)


def _cell(value, cls: str = "") -> str:
    """A right-aligned grid cell; empty/None → muted '—'. `value` is plain text
    (escaped here)."""
    text = "" if value is None else str(value)
    if text in ("", "—", "None"):
        return '<td class="mut">—</td>'
    c = f' class="{cls}"' if cls else ""
    return f'<td{c}>{_html.escape(text)}</td>'


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
        # Report timing (before/after market open) from FMP — reliable and
        # universe-wide. Precise call times + webcast links live on the dedicated
        # Calls & Webcasts tab; this view stays focused on dates + estimates.
        try:
            from data import earnings_call as _ecall
            _timing = _ecall.earnings_timing_map()
        except Exception:
            _timing = {}
        headers = [("Ticker", ""), ("Bank", "nm"), ("Report Date", ""),
                   ("Days", ""), ("When", ""), ("EPS Est (Qtr)", ""),
                   ("EPS Est (Ann)", ""), ("Price Target", ""), ("Rating", ""),
                   ("Analysts", "")]
        body = []
        for c in upcoming:
            try:
                from datetime import datetime
                ed = datetime.strptime(c["next_earnings_date"], "%Y-%m-%d").date()
                days_until = (ed - date.today()).days
                days_str = "Today" if days_until == 0 else f"{days_until}d"
            except Exception:
                days_until, days_str = 999, "—"

            # yfinance returns the string "none" for unrated names — render "—",
            # never the literal "None".
            rec = (c.get("recommendation") or "").strip()
            rec_display = (rec.replace("_", " ").title()
                           if rec and rec.lower() != "none" else "—")
            ac = c.get("analyst_count")          # None / 0 → "—", not "None"/"0"
            tm = _timing.get(c["ticker"]) or {}

            tr_cls = ' class="soon"' if days_until <= 7 else ""
            cells = [
                _tk_cell(c["ticker"]),
                _cell(get_name(c["ticker"]), "nm"),
                _cell(c["next_earnings_date"]),
                _cell(days_str),
                _cell(tm.get("when")),
                _cell(f"${c['eps_estimate']:.2f}" if c.get("eps_estimate") else None),
                _cell(f"${c['eps_fwd_annual']:.2f}" if c.get("eps_fwd_annual") else None),
                _cell(f"${c['target_price']:.2f}" if c.get("target_price") else None),
                _cell(rec_display),
                _cell(str(ac) if ac else None),
            ]
            body.append(f"<tr{tr_cls}>" + "".join(cells) + "</tr>")

        _render_earnings_grid(headers, body, max_height=min(560, 60 + 30 * len(body)))
        st.caption("When = report timing (FMP, before/after market open). "
                   "Call times & webcast links live on the Calls & Webcasts tab.")
        # Underlying numeric calendar records (unformatted estimates)
        table_export(pd.DataFrame(upcoming), "earnings_calendar_upcoming",
                     key="exp_earnings_calendar_upcoming")

        # Summary metrics
        within_7 = sum(1 for c in upcoming if _days_until(c.get("next_earnings_date")) <= 7)
        within_30 = sum(1 for c in upcoming if _days_until(c.get("next_earnings_date")) <= 30)
        ledger("Upcoming Reports", [
            ("Total Upcoming", str(len(upcoming))),
            ("This Week", str(within_7)),
            ("This Month", str(within_30)),
        ])
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


# ── Earnings Calls & Webcasts ─────────────────────────────────────────

def _fmt_rev_est(v) -> str:
    """Revenue estimate (absolute $) → compact $B / $M label; '—' if unknown."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if v == 0:
        return "—"
    if abs(v) >= 1e9:
        return f"${v / 1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def _render_calls_webcasts():
    """Universe-wide upcoming earnings calls & webcasts, grouped by week.

    Coverage spans the FULL bank universe (FMP earnings calendar), unlike the
    watchlist-scoped Calendar tab. Report timing (before/after open) and the
    confirmed-date flag come from FMP; precise call time / webcast link / dial-in
    are best-effort from each bank's earnings press release where published, and
    render '—' / blank when not yet available (never fabricated — see CLAUDE.md).
    """
    from datetime import date, timedelta

    st.subheader("Earnings Calls & Webcasts")

    horizon_days = 75            # full upcoming-season window
    today = date.today()
    with st.spinner("Loading earnings calendar..."):
        try:
            from data.bank_universe import get_universe
            universe = set(get_universe().keys())
        except Exception:
            universe = set()
        # Date spine: the universe-wide yfinance snapshot (real near-term dates,
        # nightly-cached). FMP's calendar overlays before/after-open timing, the
        # confirmed flag and the revenue estimate (and extends coverage).
        try:
            yf_cal = fetch_earnings_calendar(tuple(sorted(universe)))
        except Exception:
            yf_cal = []
        try:
            from data import fmp_client
            fmp_cal = fmp_client.get_earnings_calendar(
                today.isoformat(), (today + timedelta(days=horizon_days)).isoformat())
        except Exception:
            fmp_cal = None
        try:
            from data import earnings_call as _ecall
            calls = _ecall.call_info_map()
            agenda = _ecall.build_calls_agenda(
                yf_cal, fmp_cal, universe, calls, today, horizon_days=horizon_days)
        except Exception:
            agenda = []

    if not universe:
        st.info("Earnings calendar is temporarily unavailable. Please try again "
                "shortly.")
        return
    if not agenda:
        st.info("No upcoming bank earnings found in the next 75 days.")
        return

    all_rows = [r for b in agenda for r in b["rows"]]
    n_confirmed = sum(1 for r in all_rows if r["confirmed"])
    n_webcast = sum(1 for r in all_rows if r.get("webcast_url"))
    n_time = sum(1 for r in all_rows if r.get("call_time"))
    ledger("Upcoming Calls", [
        ("Banks Reporting", str(len(all_rows))),
        ("Confirmed Dates", str(n_confirmed)),
        ("Webcast Links", str(n_webcast)),
        ("Precise Call Times", str(n_time)),
    ])
    st.caption(
        "Full bank universe. **When** = report timing (FMP, before/after market "
        "open) and a **✓** marks an FMP-confirmed date — unconfirmed dates are "
        "FMP projections, marked **(proj.)**. **Call Time**, **Webcast** and "
        "**Dial-in** are parsed from each bank's earnings press release where "
        "published — these ramp as banks announce call details (~2 weeks out), "
        "so many rows show — until then.")

    # Quiet-state: nothing in the near term (the gap between earnings seasons).
    # Set expectations with the real soonest date rather than an empty-looking
    # table — all_rows is soonest-first.
    if not any(r["days_until"] <= 14 for r in all_rows):
        soonest = all_rows[0]
        proj = "" if soonest["confirmed"] else " (projected)"
        st.info(
            f"No bank earnings calls in the next two weeks — the earliest "
            f"upcoming report is **{soonest['ticker']}** on "
            f"**{soonest['date']}**{proj}. Call times and webcasts appear as "
            f"banks publish call details (~2 weeks ahead of each report).")

    for bucket in agenda:
        rows = bucket["rows"]
        soon = bucket["label"] == "This week"
        header = f"{bucket['label']} · {len(rows)} report{'s' if len(rows) != 1 else ''}"
        st.markdown(f"##### {'🔴 ' if soon else ''}{header}")

        headers = [("Ticker", ""), ("Bank", "nm"), ("Date", ""), ("In", ""),
                   ("✓", ""), ("When", ""), ("Call Time", ""), ("EPS Est", ""),
                   ("Rev Est", ""), ("Webcast", ""), ("Dial-in", "")]
        body = []
        for r in rows:
            days = r["days_until"]
            days_str = "Today" if days == 0 else f"{days}d"
            date_str = r["date"] if r["confirmed"] else f"{r['date']} (proj.)"
            url = r.get("webcast_url")
            if url:
                webcast = (f'<td><a class="lnk" href="{_html.escape(url, quote=True)}" '
                           f'target="_blank" rel="noopener">▶ Listen</a></td>')
            else:
                webcast = '<td class="mut">—</td>'
            cells = [
                _tk_cell(r["ticker"]),
                _cell(get_name(r["ticker"]), "nm"),
                _cell(date_str),
                _cell(days_str),
                _cell("✓" if r["confirmed"] else None),
                _cell(r.get("when")),
                _cell(r.get("call_time")),
                _cell(f"${r['eps_est']:.2f}" if r.get("eps_est") else None),
                _cell(_fmt_rev_est(r.get("rev_est"))),
                webcast,
                _cell(r.get("dial_in")),
            ]
            tr_cls = ' class="soon"' if soon else ""
            body.append(f"<tr{tr_cls}>" + "".join(cells) + "</tr>")

        _render_earnings_grid(headers, body, max_height=min(560, 60 + 30 * len(body)))

    table_export(pd.DataFrame(all_rows), "earnings_calls_webcasts",
                 key="exp_earnings_calls_webcasts")


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
        table_export(df, "beat_miss_summary", key="exp_beat_miss_summary")
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
        # Underlying numeric rows (unformatted Surprise %)
        table_export(pd.DataFrame(filtered[:50]), "surprise_rankings",
                     key="exp_surprise_rankings")

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

        avg_s = sum(yf_eps_stats["surprises"]) / len(yf_eps_stats["surprises"]) if yf_eps_stats["surprises"] else 0
        ledger("EPS Surprises — Latest Quarter", [
            ("Banks Reporting", str(total)),
            ("Beat %", f"{beats/total*100:.0f}%" if total else "—"),
            ("Miss %", f"{misses/total*100:.0f}%" if total else "—"),
            ("Inline %", f"{inlines/total*100:.0f}%" if total else "—"),
            ("Avg Surprise", f"{avg_s:+.1f}%"),
        ])

        # Beat/miss bar chart
        import plotly.graph_objects as go
        fig = go.Figure(data=[
            go.Bar(name="Beat", x=["EPS"], y=[beats], marker_color=COLOR_SUCCESS),
            go.Bar(name="Inline", x=["EPS"], y=[inlines], marker_color=COLOR_WARNING),
            go.Bar(name="Miss", x=["EPS"], y=[misses], marker_color=COLOR_DANGER),
        ])
        apply_standard_layout(fig, height=CHART_HEIGHT_COMPACT, show_legend=True)
        fig.update_layout(barmode="stack")
        st.plotly_chart(fig, use_container_width=True)

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
            table_export(df, "sector_metric_breakdown",
                         key="exp_sector_metric_breakdown")
    elif not yf_eps_stats["total"]:
        st.info("No aggregate data available yet. Upload consensus estimates to see sector-level statistics.")


# ── Upload / Input Section ─────────────────────────────────────────────

def _render_upload_section(watchlist: list[str]):
    """Upload and manual input for the aggregate view."""
    st.subheader("Add Consensus Estimates")

    input_method = st.radio(
        "Input method",
        ["Bulk Upload (Multi-Bank)", "Manual Entry (Single Bank)", "Single File Upload"],
        key="overview_input_method",
        horizontal=True,
    )

    if input_method == "Bulk Upload (Multi-Bank)":
        _render_bulk_upload()

    elif input_method == "Manual Entry (Single Bank)":
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

        # (No Period field here — _render_manual_input has its own; the
        # duplicate box this branch used to show was never read.)
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
                    try:
                        save_consensus(parsed)
                    except IOError as e:
                        st.error(str(e))
                    else:
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
        if st.button("Process Bulk Upload", type="primary", key="bulk_process"):
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
                    f"Loaded consensus for **{result['total_banks']} banks** "
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
