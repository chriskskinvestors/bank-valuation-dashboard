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
    parse_consensus_excel,
    detect_and_parse_pdf,
    parse_bulk_consensus,
    parse_bulk_consensus_pdf,
    save_consensus,
    compile_consensus,
    consensus_detail,
    list_consensus,
    list_all_consensus,
    compare_consensus_to_actual,
    save_manual_consensus,
    METRIC_DISPLAY,
    METRIC_UNITS,
)
from analysis.period_actuals import period_actuals
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

# Design-system styling for this view (boxless KPI strip + full-grid colours).
# Injected once per render; matches the Corporate Profile / Financials look
# (ksk-ledger + ksk-grid) rather than boxed cards and tinted st.dataframes.
_EC_CSS = (
    "<style>"
    ".ec-sec{font-size:var(--fs-2xs);letter-spacing:0.08em;text-transform:uppercase;"
    "font-weight:600;color:var(--text-secondary);border-bottom:1px solid var(--grid-head);"
    "padding-bottom:2px;margin:14px 0 6px;}"
    # Hairline KPI grid — frame from the container (top/left), internal + right/
    # bottom lines from the cells, so it tiles cleanly at any column count / rows.
    ".ec-kpi{display:grid;border-top:1px solid var(--grid-head);"
    "border-left:0.5px solid var(--grid-line);}"
    ".ec-kpi-cell{padding:5px 12px;border-right:0.5px solid var(--grid-line);"
    "border-bottom:0.5px solid var(--grid-line);}"
    ".ec-kpi-l{font-size:var(--fs-2xs);letter-spacing:0.05em;text-transform:uppercase;"
    "color:var(--text-secondary);white-space:nowrap;}"
    ".ec-kpi-l a.src{color:var(--brand-primary);text-decoration:none;margin-left:4px;}"
    ".ec-kpi-v{font-size:var(--fs-md);font-weight:600;color:var(--text-primary);"
    "font-variant-numeric:tabular-nums;}"
    ".ksk-grid .beat{color:var(--success);font-weight:600;}"
    ".ksk-grid .miss{color:var(--danger);font-weight:600;}"
    ".ec-grid table{width:100%;}"
    "</style>")


def _ec_cell(s):
    """HTML-safe cell text that also neutralises '$' so Streamlit's markdown
    doesn't interpret two dollar amounts on a line as LaTeX."""
    return _html.escape(str(s)).replace("$", "&#36;")


def _kpi_strip(items, cols):
    """Boxless hairline KPI grid (design system: no boxed cards / shadows).

    items: list of (label, value, tooltip, link). A non-empty ``link`` adds a
    small ↗ to the source document; ``tooltip`` becomes the cell's title. Used
    for both the analyst-estimate strip and the reported-metrics strip so they
    read identically."""
    cells = []
    for label, value, tip, link in items:
        src = (f'<a class="src" href="{_html.escape(str(link), quote=True)}" '
               f'target="_blank" title="View source document">↗</a>') if link else ""
        ttl = f' title="{_html.escape(str(tip), quote=True)}"' if tip else ""
        cells.append(
            f'<div class="ec-kpi-cell"{ttl}>'
            f'<div class="ec-kpi-l">{_html.escape(str(label))}{src}</div>'
            f'<div class="ec-kpi-v">{_ec_cell(value)}</div></div>')
    st.markdown(
        f'<div class="ec-kpi" style="grid-template-columns:repeat({cols},1fr)">'
        + "".join(cells) + "</div>", unsafe_allow_html=True)


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
    st.markdown(_EC_CSS, unsafe_allow_html=True)

    # ── Auto-populated estimates from yfinance ──────────────────────────
    with st.spinner("Loading analyst estimates..."):
        estimates = fetch_estimates_cached(ticker)

    if estimates and not estimates.get("error"):
        _render_auto_estimates(ticker, estimates)

    st.markdown("---")

    # ── Add estimates (shared firm-level component, ticker pre-filled) ───
    # Lazy segmented_control (st.tabs renders both bodies eagerly); the upload
    # path auto-detects firm + every forecast period, manual tags one firm.
    st.markdown("**Add estimates for this bank**")
    add_mode = st.segmented_control(
        "Add estimates", ["Upload research file", "Enter manually"],
        default="Upload research file", key=f"pb_addmode_{ticker}",
        label_visibility="collapsed") or "Upload research file"
    if add_mode == "Upload research file":
        _render_firm_upload(default_ticker=ticker, kp=f"pb_{ticker}")
    else:
        _render_manual_input(ticker)

    st.markdown("---")

    # ── Historical earnings surprises ───────────────────────────────────
    if estimates and estimates.get("earnings_history"):
        _render_earnings_history_chart(ticker, estimates)
        st.markdown("---")

    # ── Consensus comparison table ──────────────────────────────────────
    periods = list_consensus(ticker)

    if periods:
        st.markdown('<div class="ec-sec">Consensus vs Actual</div>',
                    unsafe_allow_html=True)

        period_labels = [f"{p['period']} ({p['source']}, {p['metric_count']} metrics)" for p in periods]
        selected_idx = st.selectbox(
            "Select period",
            options=list(range(len(periods))),
            format_func=lambda i: period_labels[i],
            key=f"consensus_period_select_{ticker}",
        )

        selected_period = periods[selected_idx]["period"]
        consensus = compile_consensus(ticker, selected_period)

        if consensus:
            firms = consensus.get("firms") or []
            st.caption(f"Consensus = mean of **{consensus.get('n_firms', len(firms))} "
                       f"firm(s)**: {', '.join(firms) if firms else '—'}")
            # Compare against the actuals for THIS period — not the bank's latest
            # trailing snapshot (which made a forward quarter show actuals at all,
            # and pitted a single-quarter estimate against a TTM figure). None ⇒
            # the period has not been reported yet, so we show the estimate only.
            period_actual = period_actuals(ticker, selected_period)
            if period_actual is None:
                st.info(f"**{selected_period} has not been reported yet** — showing "
                        "the consensus estimate only. Actuals will populate once "
                        "the bank files for this period.")
            comparison = compare_consensus_to_actual(consensus, period_actual or {})
            if comparison:
                _render_comparison_table(comparison)
                if period_actual is not None:
                    st.caption("Actuals are the company's as-reported figures for "
                               "this period (SEC companyfacts — the same holding-"
                               "company basis as **Company Reported**, refreshed as "
                               "filings are processed). ROAA is implied (annualized "
                               "income ÷ average assets). NIM and ROATCE show n/a — "
                               "filings don't carry a clean average-earning-assets "
                               "or common-tangible-equity basis to compare on.")
            else:
                st.info("No comparable metrics found.")

            # Per-firm breakdown — what each firm estimated (reuses the same
            # matrix as the Estimates browser).
            detail = consensus_detail(ticker, selected_period)
            if detail and detail.get("metrics"):
                with st.expander(
                        f"Per-firm breakdown — {len(detail['firms'])} firm(s): "
                        f"{', '.join(detail['firms'])}"):
                    _render_firm_matrix(detail, f"{ticker}_{selected_period}")
    else:
        st.info(
            f"No consensus data for {ticker} yet. "
            "Enter estimates manually or upload a consensus file above."
        )

    _render_key_metrics(ticker, actual_metrics)


def _render_auto_estimates(ticker: str, estimates: dict):
    """Show auto-populated analyst estimates from yfinance."""
    ned = estimates.get("next_earnings_date")
    eps_est = estimates.get("eps_estimate"); eps_fwd = estimates.get("eps_fwd_annual")
    target = estimates.get("target_price"); analysts = estimates.get("analyst_count")

    # Boxless KPI strip (design system: no boxed cards) — small-caps label over
    # the value, hairline-separated; each cell keeps its definition as a tooltip.
    kpis = [
        ("Next Earnings", (ned if ned else "Unknown"),
         "Estimated date of the next quarterly earnings release."),
        ("EPS Est (Next Qtr)", (f"${eps_est:.2f}" if eps_est else "—"),
         "Consensus analyst estimate for next-quarter diluted EPS."),
        ("EPS Est (Annual)", (f"${eps_fwd:.2f}" if eps_fwd else "—"),
         "Consensus analyst estimate for forward annual diluted EPS."),
        ("Avg Price Target", (f"${target:.2f}" if target else "—"),
         "Average of analysts' 12-month price targets."),
        ("Analyst Coverage", (str(analysts) if analysts else "—"),
         "Number of sell-side analysts contributing estimates."),
    ]
    st.markdown('<div class="ec-sec">Analyst Estimates</div>', unsafe_allow_html=True)
    _kpi_strip([(l, v, d, None) for l, v, d in kpis], cols=5)

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
    """Manual consensus input form — stored as ONE firm's view (a broker, or the
    user's own model) so it aggregates with other firms into the consensus."""
    st.markdown("**Enter estimates manually (one firm / your own model):**")

    pcol, fcol = st.columns(2)
    with pcol:
        period = st.text_input(
            "Period", placeholder="e.g. 2026Q1", key=f"manual_period_{ticker}")
    with fcol:
        firm = st.text_input(
            "Firm / Broker (or your model)", value="My model",
            key=f"m_firm_{ticker}").strip()

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
                save_manual_consensus(ticker, period, metrics, firm or "My model")
                st.success(f"Saved {len(metrics)} estimates for {ticker} {period} "
                           f"({firm or 'My model'})")
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

        # Consensus = MEAN across firms; show the low–high range when >1 firm.
        n = c.get("n_firms")
        if c.get("low") is not None and c.get("high") is not None and (n or 0) > 1:
            rng = (f'{_format_val(c["low"], c["unit"])}–'
                   f'{_format_val(c["high"], c["unit"])} ({n})')
        else:
            rng = "—"
        rows.append({
            "Metric": c["metric_name"],
            "Consensus": _format_val(c["consensus"], c["unit"]),
            "Range (firms)": rng,
            "Actual": _format_val(c["actual"], c["unit"]),
            "Δ": _format_delta(c["delta"], c["unit"]),
            "Δ %": f"{c['delta_pct']:+.1f}%" if c.get("delta_pct") is not None else "—",
            "Result": label,
        })

    # Full-grid table (design system .ksk-grid) — semantic colour on Δ / Δ % and
    # the Result column, not a heavy whole-row tint.
    head = ("<tr><th>Metric</th><th>Consensus</th><th>Range (firms)</th>"
            "<th>Actual</th><th>Δ</th><th>Δ %</th><th>Result</th></tr>")
    body = ""
    for r in rows:
        res = r["Result"]
        rcls = "beat" if res == _BEAT_LABEL else ("miss" if res == _MISS_LABEL else "")
        # Financial convention: negatives red, positives plain (note _format_delta
        # writes "$-1.51", so test for "-" anywhere, not just the leading char).
        dcls = "neg" if "-" in r["Δ"] else ""
        pcls = "neg" if "-" in r["Δ %"] else ""
        body += (
            "<tr>"
            f"<td>{_ec_cell(r['Metric'])}</td>"
            f"<td>{_ec_cell(r['Consensus'])}</td>"
            f"<td>{_ec_cell(r['Range (firms)'])}</td>"
            f"<td>{_ec_cell(r['Actual'])}</td>"
            f"<td class='{dcls}'>{_ec_cell(r['Δ'])}</td>"
            f"<td class='{pcls}'>{_ec_cell(r['Δ %'])}</td>"
            f"<td class='{rcls}'>{_ec_cell(res)}</td></tr>")
    st.markdown(f'<div class="ksk-grid ec-grid"><table><thead>{head}</thead>'
                f'<tbody>{body}</tbody></table></div>', unsafe_allow_html=True)
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
    st.markdown('<div class="ec-sec">Key Reported Metrics</div>',
                unsafe_allow_html=True)

    from ui.source_trace import _calc_tooltip, fdic_calc, make_calc, sec_doc_for
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
    _kpi_strip([(c["label"], c["value"], _calc_tooltip(c.get("calc")),
                 (c.get("calc") or {}).get("link")) for c in cards], cols=4)


# ═══════════════════════════════════════════════════════════════════════════
# AGGREGATE EARNINGS VIEW (Earnings Analysis section)
# ═══════════════════════════════════════════════════════════════════════════

def render_earnings_overview(watchlist: list[str]):
    """Render the full earnings analysis section with all features."""

    title_bar("KSK Investors", "Earnings Analysis")

    all_consensus = list_all_consensus()

    # ── Top KPI bar ─────────────────────────────────────────────────────
    _render_earnings_kpi_bar(watchlist, all_consensus)

    st.markdown("---")

    # ── Sections (LAZY) ─────────────────────────────────────────────────
    # st.tabs renders EVERY tab body on every run, so opening Earnings used to
    # fire all seven tabs' data fetches at once — including the Heat-Map's
    # universe-wide (~all banks) yfinance pull — which made every load crawl. A
    # segmented_control renders only the selected section, so each tab's fetches
    # run only when you're on it.
    SECTIONS = [
        "Calendar", "Results", "Surprise Heat-Map", "Beat / Miss",
        "Estimates", "Biggest Surprises", "Sector Aggregates", "Upload / Input",
    ]
    active = st.segmented_control(
        "Earnings view", SECTIONS, default="Calendar",
        key="earnings_section", label_visibility="collapsed") or "Calendar"

    if active == "Calendar":
        _render_earnings_calendar(watchlist)
    elif active == "Results":
        _render_results_board()
    elif active == "Surprise Heat-Map":
        _render_surprise_heatmap(watchlist)
    elif active == "Beat / Miss":
        _render_beat_miss_summary(all_consensus)
    elif active == "Estimates":
        _render_estimates_browser(all_consensus)
    elif active == "Biggest Surprises":
        _render_surprise_rankings(all_consensus, watchlist)
    elif active == "Sector Aggregates":
        _render_sector_aggregates(all_consensus, watchlist)
    elif active == "Upload / Input":
        _render_upload_section(watchlist)


def _avg_eps_surprise_cached(tickers: tuple) -> float | None:
    """Average last-quarter EPS surprise across (up to ~30) banks, CROSS-INSTANCE
    cached 6h. The underlying ~30 yfinance/GCS reads ran on EVERY Earnings load
    (the KPI bar is always rendered) and re-ran on each cold Cloud Run instance,
    which was a chunk of the tab's slowness. served_snapshot shares one computed
    value across instances; a genuine fetch failure raises out of build() so it
    is NOT cached (next render retries), while a real 'no surprises' caches None."""
    from data import cache as _cache

    def _build():
        from data.estimates import fetch_all_estimates
        estimates = fetch_all_estimates(tickers)
        surprises = [e.get("surprise_pct")
                     for est in estimates.values()
                     for e in (est.get("earnings_history") or [])[:1]
                     if e.get("surprise_pct") is not None]
        return (sum(surprises) / len(surprises)) if surprises else None

    try:
        return _cache.served_snapshot("earnings_avg_eps_surprise_v1", 21600, _build)
    except Exception as e:
        print(f"[earnings] surprise fetch failed: {type(e).__name__}: {e}")
        return None


def _render_earnings_kpi_bar(watchlist: list[str], all_consensus: dict):
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
        consensus = compile_consensus(ticker, latest["period"])
        # Period-matched reported actuals (None ⇒ this period isn't reported yet,
        # so the bank drops out of the aggregate rather than scoring a forward
        # estimate against trailing actuals — which used to inflate beat rates).
        actual = period_actuals(ticker, latest["period"]) or {}
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

    avg_surprise = _avg_eps_surprise_cached(tuple(watchlist[:30]))

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


def _heatmap_columns(bank_data: dict) -> tuple[list[str], dict]:
    """
    Bucket each bank's surprise rows by FISCAL quarter and keep the 8 most
    recent quarter buckets. Returns (chronological quarter labels, per-ticker
    {quarter label → latest row announced in that bucket}).

    Joining on the exact announcement date scattered one earnings season
    across ~8 single-day columns: banks announce on different days, so most
    rows populated one cell and several date columns collapsed onto the
    same quarter label.
    """
    all_quarters = set()
    for history in bank_data.values():
        for e in history:
            q = _quarter_label(e.get("date"))
            if q != "—":   # undated rows can't be bucketed — skip, don't guess
                all_quarters.add(q)

    # YYYYQn labels sort lexicographically in time order; keep last 8.
    col_labels = sorted(all_quarters, reverse=True)[:8]
    col_labels.reverse()  # chronological left→right
    keep = set(col_labels)

    placed = {}
    for ticker, history in bank_data.items():
        chosen = {}  # quarter label → latest announcement in that bucket
        for e in history:
            q = _quarter_label(e.get("date"))
            if q in keep and (q not in chosen
                              or e.get("date") > chosen[q].get("date")):
                chosen[q] = e
        placed[ticker] = chosen
    return col_labels, placed


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
    # Bucket rows by FISCAL quarter, then take the most recent 8 buckets
    bank_data = {}
    for ticker, est in estimates.items():
        history = est.get("earnings_history", []) or []
        history = [e for e in history if e.get("surprise_pct") is not None
                   and e.get("eps_actual") is not None]
        if not history:
            continue
        bank_data[ticker] = history

    col_labels, placed = _heatmap_columns(bank_data)
    if not bank_data or not col_labels:
        st.info(
            "No earnings history available yet. Visit a few banks to populate "
            "the cache, or upload consensus files."
        )
        return

    quarter_to_idx = {q: i for i, q in enumerate(col_labels)}

    # Sort banks by most recent surprise (descending)
    def _sort_key(item):
        ticker, history = item
        most_recent = history[0] if history else {}
        return most_recent.get("surprise_pct") or 0

    bank_data = dict(sorted(bank_data.items(), key=_sort_key, reverse=True))

    # Build matrix
    tickers_list = list(bank_data.keys())
    n_banks = len(tickers_list)
    n_qtrs = len(col_labels)
    matrix = [[None] * n_qtrs for _ in range(n_banks)]
    actual_matrix = [[None] * n_qtrs for _ in range(n_banks)]
    est_matrix = [[None] * n_qtrs for _ in range(n_banks)]

    for i, ticker in enumerate(tickers_list):
        for q, e in placed[ticker].items():
            j = quarter_to_idx[q]
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


def _render_earnings_grid(headers, body_rows, height: int | None = None,
                          col_widths: list[str] | None = None):
    """Render an SNL-style `ksk-grid` HTML table (design-system look used across
    the site) — hairline grid, small-caps headers, tabular right-aligned cells.
    Replaces st.dataframe here, which can't carry per-row links and renders a
    literal "None" for empty cells. `headers` is a list of (label, cls) where cls
    is "" (right-aligned, default) or "nm" (left-aligned text); `body_rows` is a
    list of pre-built <tr>…</tr> strings.

    `col_widths`, when given, is a per-column width list (e.g. "11%") emitted as a
    <colgroup>. With it the table is locked to `table-layout:fixed; width:100%`, so
    it fills its container EXACTLY and never overruns — the fix for two side-by-side
    week tables overflowing their halves and colliding at the seam. Wide cells
    (Bank, Dial-in) then ellipsis-truncate instead of pushing the table wider.

    `height` None (default) → NATURAL height: the table grows to fit every row and
    the PAGE scrolls — no inner, separately-scrolling box (the row-height estimate
    that fed a fixed box always under/over-shot and left a scrollbar). An explicit
    px height gives the legacy fixed scroll box with a sticky header."""
    head = "<tr>" + "".join(
        f'<th class="{cls}">{_html.escape(lbl)}</th>' for lbl, cls in headers) + "</tr>"
    colgroup = ("<colgroup>" + "".join(f'<col style="width:{w}">'
                for w in col_widths) + "</colgroup>") if col_widths else ""
    css = (
        ".ern-wrap{border:0.5px solid var(--grid-head);}"
        ".ern-wrap.scroll{overflow:auto;}"
        ".ern-wrap.scroll thead th{position:sticky;top:0;z-index:2;}"
        # Fixed layout + full width so each table is clamped to its column (the two
        # side-by-side week tables can no longer overrun and collide); overflowing
        # text clips with an ellipsis rather than widening the table.
        ".ern-grid{width:100%;table-layout:fixed;}"
        ".ern-grid td,.ern-grid th{overflow:hidden;text-overflow:ellipsis;}"
        ".ern-grid td.nm,.ern-grid th.nm{text-align:left;color:var(--text-secondary);}"
        ".ern-grid a.tk{font-weight:700;text-decoration:none;color:var(--brand-primary);}"
        ".ern-grid a.lnk{text-decoration:none;color:var(--brand-primary);font-weight:600;}"
        ".ern-grid td.mut{color:var(--text-muted);}"
        ".ern-grid tr.soon td{background:rgba(217,119,6,0.07);}"
    )
    if height is None:
        wrap = '<div class="ern-wrap">'
    else:
        wrap = f'<div class="ern-wrap scroll" style="height:{height}px">'
    st.markdown(
        f"<style>{css}</style>{wrap}"
        f'<table class="ksk-grid ern-grid">'
        f'{colgroup}<thead>{head}</thead><tbody>{"".join(body_rows)}</tbody></table></div>',
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

def _cal_tr(r: dict, soon: bool) -> str:
    """One <tr> for the combined earnings-calendar grid (report date/timing + the
    conference call + the estimates)."""
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
        _cell("✓" if r["confirmed"] else None),
        _cell(days_str),
        _cell(r.get("when")),
        _cell(_call_label(r.get("call_date"), r.get("call_time"))),
        webcast,
        _cell(r.get("dial_in")),
        _cell(f"${r['eps_est']:.2f}" if r.get("eps_est") else None),
        _cell(_fmt_rev_est(r.get("rev_est"))),
    ]
    tr_cls = ' class="soon"' if soon else ""
    return f"<tr{tr_cls}>" + "".join(cells) + "</tr>"


def _render_earnings_calendar(watchlist: list[str]):
    """Universe-wide upcoming earnings calendar, grouped by week — report date &
    timing, the conference call (time / webcast / dial-in), the estimates and the
    analyst context together, one row per bank. (Merges the old Calendar and Calls
    & Webcasts tabs.) Dates/timing/confirmed from FMP + yfinance; call details from
    the IR/PR pipeline; annual EPS / target / rating / coverage from yfinance.
    '—' wherever a value isn't available yet (never fabricated — see CLAUDE.md)."""
    from datetime import date, timedelta

    st.subheader("Earnings Calendar")

    horizon_days = 75            # full upcoming-season window
    today = date.today()
    with st.spinner("Loading earnings calendar..."):
        try:
            from data.bank_universe import get_universe
            universe = set(get_universe().keys())
        except Exception:
            universe = set()
        # Date spine: the universe-wide yfinance snapshot (real near-term dates,
        # nightly-cached) carrying the analyst estimates; FMP overlays timing, the
        # confirmed flag and revenue; the IR/PR pipeline adds call time + webcast.
        try:
            yf_cal = fetch_earnings_calendar(tuple(sorted(universe)))
        except Exception:
            yf_cal = []
        try:
            fmp_cal = _fmp_earnings_window(
                today.isoformat(), (today + timedelta(days=horizon_days)).isoformat())
        except Exception:
            fmp_cal = None
        try:
            from data import earnings_call as _ecall
            calls = _ecall.merged_call_info()
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
    n_week = sum(1 for r in all_rows if r["days_until"] <= 7)
    n_confirmed = sum(1 for r in all_rows if r["confirmed"])
    n_webcast = sum(1 for r in all_rows if r.get("webcast_url"))
    n_time = sum(1 for r in all_rows if r.get("call_time"))
    ledger("Upcoming Earnings", [
        ("Banks Reporting", str(len(all_rows))),
        ("This Week", str(n_week)),
        ("Confirmed Dates", str(n_confirmed)),
        ("Webcast Links", str(n_webcast)),
        ("Precise Call Times", str(n_time)),
    ])
    st.caption(
        "Full bank universe, by week. Two dates per bank: **Release** = the "
        "earnings release date (FMP/yfinance estimate; **When** is its before/"
        "after-open timing, a **✓** marks a confirmed date — FMP, or the company "
        "has published its earnings call — others are **(proj.)**), and **Call** = "
        "the conference-call date + time (often a "
        "different day — e.g. release after close, call next morning), with "
        "**Webcast / Dial-in**, all from the bank's own IR announcement, plus the "
        "**EPS / Rev** estimates. '—' wherever a value isn't available yet.")

    headers = [("Ticker", ""), ("Bank", "nm"), ("Release", ""), ("✓", ""),
               ("In", ""), ("When", ""), ("Call", ""), ("Webcast", ""),
               ("Dial-in", ""), ("EPS Est", ""), ("Rev Est", "")]
    # Per-column widths (sum ≈ 100%) so each table fills its container exactly and
    # the two side-by-side halves line up — text columns (Bank, Dial-in) absorb the
    # slack and ellipsis-truncate rather than widening the table past its half.
    col_widths = ["7%", "18%", "11%", "4%", "5%", "7%", "11%", "8%",
                  "20%", "5%", "4%"]

    # Tables render at NATURAL height (no inner scrollbox — the page scrolls). A
    # heavy day is split into two balanced tables side by side so it fills the
    # horizontal space instead of running down one tall column.
    for bucket in agenda:
        rows = bucket["rows"]
        # Highlight today's / tomorrow's reports.
        soon = (date.fromisoformat(bucket["date"]) - today).days <= 1
        header = f"{bucket['label']} · {len(rows)} report{'s' if len(rows) != 1 else ''}"
        st.markdown(f"##### {'🔴 ' if soon else ''}{header}")

        trs = [_cal_tr(r, soon) for r in rows]
        if len(trs) > 20:
            mid = (len(trs) + 1) // 2
            left, right = trs[:mid], trs[mid:]
            c1, c2 = st.columns(2, gap="medium")     # gutter between the two tables
            with c1:
                _render_earnings_grid(headers, left, col_widths=col_widths)
            with c2:
                _render_earnings_grid(headers, right, col_widths=col_widths)
        else:
            _render_earnings_grid(headers, trs, col_widths=col_widths)

    table_export(pd.DataFrame(all_rows), "earnings_calendar",
                 key="exp_earnings_calendar")


# ── Earnings call helpers ─────────────────────────────────────────────

def _iso_to_short(iso) -> str | None:
    """'2026-07-15' → 'Jul 15'; None on anything unparseable."""
    try:
        from datetime import date as _date
        d = _date.fromisoformat(str(iso))
        return f"{d.strftime('%b')} {d.day}"
    except (TypeError, ValueError):
        return None


def _call_label(call_date, call_time) -> str | None:
    """The 'Call' cell — the conference-call DATE and time, its own column distinct
    from the release date (e.g. 'Jul 15 · 9:00a ET'). Shows whichever parts are
    known; None when there's no call detail at all."""
    bits = []
    if call_date:
        short = _iso_to_short(call_date)
        if short:
            bits.append(short)
    if call_time:
        bits.append(call_time)
    return " · ".join(bits) or None


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


@st.cache_data(ttl=21600, show_spinner=False)
def _fmp_earnings_window(from_iso: str, to_iso: str):
    """FMP earnings calendar for the window, cached 6h. fmp_client.get_earnings_
    calendar is a raw ~15s network call with no cache of its own; calling it on
    every render made the Calendar tab slow. Raises on failure so a
    transient error is NOT cached (house pattern: never cache failures)."""
    from data import fmp_client
    rows = fmp_client.get_earnings_calendar(from_iso, to_iso)
    if rows is None:
        raise RuntimeError("FMP earnings calendar unavailable")
    return rows


# ── Results board (reported this season) ──────────────────────────────

def _signed_pct_cell(v, live: bool = False) -> str:
    """Signed percent cell, green/red by sign; '· live' marks an intraday
    stand-in (session close not posted yet). Muted '—' when None."""
    if v is None:
        return '<td class="mut">—</td>'
    cls = "pos" if v >= 0 else "neg"
    suffix = " · live" if live else ""
    return f'<td class="{cls}">{v:+.1f}%{suffix}</td>'


# Release-metric keys → compact labels, in display order. Percent keys render
# "x.xx%"; per-share keys ($) render "$x.xx". Values come from the bank's own
# release via data/release_metrics (prose-confirmed or None — never guessed).
_REL_METRICS = [
    ("nim", "NIM", "%"), ("efficiency", "Efficiency", "%"),
    ("roa", "ROA", "%"), ("roe", "ROE", "%"), ("rotce", "ROTCE", "%"),
    ("cet1_ratio", "CET1", "%"), ("t1_ratio", "Tier 1", "%"),
    ("total_ratio", "Total Cap", "%"), ("lev_ratio", "Leverage", "%"),
    ("tbv_ps", "TBV/sh", "$"), ("div_ps", "Div/sh", "$"),
    ("nco_ratio", "NCOs", "%"), ("npa_assets", "NPAs/Assets", "%"),
    ("acl_loans", "ACL/Loans", "%"),
]


def _rel_detail_tr(r: dict, ncols: int) -> str:
    """The hidden expansion <tr> under a bank's row: every release-extracted
    metric as a dense label:value strip, '—' where the release didn't
    confirmably state it."""
    rel = r.get("rel") or {}
    vals = {**(rel.get("metrics") or {}), **(rel.get("capital") or {})}
    parts = []
    for key, label, unit in _REL_METRICS:
        v = vals.get(key)
        if v is None:
            sval = '<span class="mut">—</span>'
        elif unit == "$":
            sval = f"${v:,.2f}"
        else:
            sval = f"{v:.2f}%"
        parts.append(f'<span class="rl">{label}</span> {sval}')
    src = rel.get("url")
    if src:
        parts.append(f'<a class="lnk" href="{_html.escape(src, quote=True)}" '
                     f'target="_blank" rel="noopener">release ↗</a>')
    return (f'<tr class="det"><td class="dt" colspan="{ncols}">'
            + " · ".join(parts) + "</td></tr>")


def _results_tr(r: dict, ncols: int) -> str:
    """A bank's Results rows: the main <tr> (with the ▸ expander toggle when
    release metrics are attached) immediately followed by its hidden detail
    <tr> — the CSS `tr:has(:checked) + tr.det` pair contract."""
    try:
        from datetime import date as _date
        rep = _date.fromisoformat(r["date"]).strftime("%b %d")
    except (KeyError, ValueError):
        rep = r.get("date")
    def _usd(v):
        return None if v is None else (f"-${-v:.2f}" if v < 0 else f"${v:.2f}")
    eps_act, eps_est = _usd(r.get("eps_act")), _usd(r.get("eps_est"))
    # Release link: the wire/IR press release when the events feed has it;
    # else the SEC 8-K EX-99.1 already located for the metrics expansion
    # (micro-caps often file with EDGAR without ever hitting a wire).
    url = r.get("pr_url") or (r.get("rel") or {}).get("url")
    if url:
        title = _html.escape(r.get("pr_headline") or "SEC 8-K earnings release",
                             quote=True)
        release = (f'<td><a class="lnk" href="{_html.escape(url, quote=True)}" '
                   f'title="{title}" target="_blank" rel="noopener">Release ↗</a></td>')
    else:
        release = '<td class="mut">—</td>'
    has_rel = bool(r.get("rel"))
    toggle = ('<td class="tg"><label><input type="checkbox">'
              '<span class="xa"></span></label></td>'
              if has_rel else '<td class="mut"></td>')
    main = "<tr>" + "".join([
        toggle,
        _tk_cell(r["ticker"]),
        _cell(get_name(r["ticker"]), "nm"),
        _cell(rep),
        _cell(r.get("when")),
        _cell(r.get("period_ending")),
        _cell(eps_act),
        _cell(eps_est),
        _signed_pct_cell(r.get("eps_surprise")),
        _cell(_fmt_rev_est(r.get("rev_act"))),
        _cell(_fmt_rev_est(r.get("rev_est"))),
        _signed_pct_cell(r.get("rev_surprise")),
        _signed_pct_cell(r.get("px_react"), live=bool(r.get("px_react_live"))),
        release,
    ]) + "</tr>"
    return main + (_rel_detail_tr(r, ncols) if has_rel else "")


def _render_results_board():
    """Reported results this season, one row per bank: actual vs estimated
    EPS/revenue with surprise %, the release-session price reaction, and the
    results press release — compiled from FMP actuals (same-day), the events
    feed and EOD history. Fills as banks report; 15-min refresh."""
    from data.earnings_results import results_board

    st.subheader("Reported Results")
    with st.spinner("Loading reported results..."):
        rows = results_board()

    if not rows:
        st.info("No universe bank has reported in the trailing 30 days yet — "
                "this board fills as results land.")
        return

    eps_rows = [r for r in rows if r.get("eps_surprise") is not None]
    beats = sum(1 for r in eps_rows if r["eps_surprise"] >= 0)
    reacts = [r["px_react"] for r in rows if r.get("px_react") is not None]
    ledger("Results Season", [
        ("Reported", str(len(rows))),
        ("EPS Beats", str(beats)),
        ("EPS Misses", str(len(eps_rows) - beats)),
        ("Beat Rate", f"{beats / len(eps_rows) * 100:.0f}%" if eps_rows else "—"),
        ("Avg EPS Surprise", f"{sum(r['eps_surprise'] for r in eps_rows) / len(eps_rows):+.1f}%"
         if eps_rows else "—"),
        ("Avg Px Reaction", f"{sum(reacts) / len(reacts):+.1f}%" if reacts else "—"),
    ])
    st.caption(
        "Every universe bank that has **reported** in the trailing 30 days, "
        "newest first — actual vs estimated **EPS / Revenue** with the surprise "
        "(FMP, filled the day results land), **Px React** = the release "
        "session's close-over-prior-close move (after-close reports react the "
        "NEXT session; *live* marks today's in-progress session), and the "
        "results press **Release** from the news feed. A **▸** expands the "
        "release's own metrics (NIM, efficiency, returns, capital, TBV, "
        "credit) — each parsed from the bank's release prose and shown only "
        "when confidently confirmed; '—' otherwise, never a guess. "
        "Refreshes ~15 min.")

    headers = [("", ""), ("Ticker", ""), ("Bank", "nm"), ("Reported", ""),
               ("When", ""), ("Period", ""), ("EPS Act", ""), ("EPS Est", ""),
               ("EPS Δ", ""), ("Rev Act", ""), ("Rev Est", ""), ("Rev Δ", ""),
               ("Px React", ""), ("Release", "")]
    col_widths = ["3%", "6%", "14%", "7%", "8%", "8%", "6%", "6%", "7%",
                  "7%", "7%", "6%", "8%", "7%"]
    # Expansion CSS: the detail row directly follows its main row; checking the
    # toggle shows it (Chrome-first `:has()`, the house pattern). The checkbox
    # itself is hidden — the ▸/▾ arrow is the visible affordance.
    st.markdown(
        "<style>"
        ".ern-grid tr.det{display:none;}"
        ".ern-grid tr:has(td.tg input:checked)+tr.det{display:table-row;}"
        ".ern-grid td.tg{cursor:pointer;text-align:center;}"
        ".ern-grid td.tg label{cursor:pointer;display:block;}"
        ".ern-grid td.tg input{display:none;}"
        ".ern-grid td.tg .xa::after{content:'▸';color:var(--brand-primary);}"
        ".ern-grid tr:has(td.tg input:checked) .xa::after{content:'▾';}"
        ".ern-grid td.dt{text-align:left;background:var(--surface-raised,rgba(0,0,0,0.03));"
        "white-space:normal;}"
        ".ern-grid td.dt .rl{color:var(--text-muted);font-size:0.85em;"
        "text-transform:uppercase;letter-spacing:0.03em;}"
        "</style>", unsafe_allow_html=True)
    _render_earnings_grid(headers, [_results_tr(r, len(headers)) for r in rows],
                          col_widths=col_widths)
    export_rows = [{**{k: v for k, v in r.items() if k != "rel"},
                    **{f"rel_{key}": ((r.get("rel") or {}).get("metrics", {}) |
                                      (r.get("rel") or {}).get("capital", {})
                                      ).get(key)
                       for key, _, _ in _REL_METRICS}}
                   for r in rows]
    table_export(pd.DataFrame(export_rows), "earnings_results",
                 key="exp_earnings_results")


# ── Beat / Miss Summary ───────────────────────────────────────────────

def _render_firm_matrix(detail: dict, key_suffix: str):
    """Per-firm estimate matrix for one (ticker, period): a row per metric, a
    column per firm (in that metric's canonical unit), plus Mean and low–high
    Range. Shared by the Estimates browser and the per-bank Earnings tab so both
    show the SAME 'what each firm estimated' view. `detail` is consensus_detail()."""
    firms = detail["firms"]
    rows = []
    for m in sorted(detail["metrics"], key=lambda x: x["name"]):
        row = {"Metric": m["name"]}
        for f in firms:
            v = m["by_firm"].get(f)
            row[f] = _format_val(v, m["unit"]) if v is not None else "—"
        row["Mean"] = _format_val(m["mean"], m["unit"])
        row["Range"] = (f'{_format_val(m["low"], m["unit"])}–'
                        f'{_format_val(m["high"], m["unit"])}') if m["n"] > 1 else "—"
        rows.append(row)

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True,
                 height=min(720, 45 + 32 * len(df)))
    table_export(df, f"estimates_{key_suffix}",
                 key=f"exp_estimates_{key_suffix}")


def _render_estimates_browser(all_consensus: dict):
    """Browse the saved consensus estimates by company → period, showing each
    firm's number per metric plus the mean and low–high range. This is the
    'what did the upload actually pull' view."""
    st.subheader("Consensus Estimates by Company")

    if not all_consensus:
        st.info("No estimates uploaded yet. Add a firm's research note on the "
                "**Upload / Input** tab — it's stored per firm and combined here.")
        return

    tickers = sorted(all_consensus.keys())
    c1, c2 = st.columns([1, 1])
    with c1:
        ticker = st.selectbox(
            "Company", tickers,
            format_func=lambda t: f"{t} — {get_name(t)}", key="estbrowse_tkr")
    periods_info = all_consensus.get(ticker, [])
    period_labels = [p["period"] for p in periods_info]
    with c2:
        period = st.selectbox("Period", period_labels, key="estbrowse_per")

    if periods_info:
        st.caption("Periods on file: " + " · ".join(
            f"**{p['period']}** ({p['n_firms']} firm{'s' if p['n_firms'] != 1 else ''}, "
            f"{p['metric_count']} metrics)" for p in periods_info))

    detail = consensus_detail(ticker, period) if period else None
    if not detail or not detail.get("metrics"):
        st.info("No estimates for this selection.")
        return

    firms = detail["firms"]
    st.markdown(f"##### {ticker} · {period} — {len(firms)} firm(s): {', '.join(firms)}")
    _render_firm_matrix(detail, f"{ticker}_{period}")
    st.caption("Values shown in each metric's standard unit; **Mean** is the "
               "consensus across firms, **Range** is low–high. Add more firms' "
               "notes to broaden the consensus.")


def _render_beat_miss_summary(all_consensus: dict):
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

        consensus = compile_consensus(ticker, latest["period"])
        # Period-matched reported actuals (None ⇒ this period isn't reported yet,
        # so the bank drops out of the aggregate rather than scoring a forward
        # estimate against trailing actuals — which used to inflate beat rates).
        actual = period_actuals(ticker, latest["period"]) or {}

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

def _surprise_line(s: dict) -> str:
    """One Biggest Beats/Misses markdown line. Consensus/Actual arrive
    pre-formatted ("$3.10"), so the pair must be \\$-escaped or Streamlit
    renders the $…$ span as LaTeX."""
    return (
        f"**{s['Ticker']}** {s['Metric']}: {s['Surprise %']:+.1f}% "
        f"({s['Consensus']} → {s['Actual']})"
    ).replace("$", "\\$")


def _render_surprise_rankings(all_consensus: dict, watchlist: list[str]):
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

        consensus = compile_consensus(ticker, latest["period"])
        # Period-matched reported actuals (None ⇒ this period isn't reported yet,
        # so the bank drops out of the aggregate rather than scoring a forward
        # estimate against trailing actuals — which used to inflate beat rates).
        actual = period_actuals(ticker, latest["period"]) or {}

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
                st.markdown(_surprise_line(s))
        with bc2:
            st.markdown("##### Biggest Misses")
            for s in top_misses:
                st.markdown(_surprise_line(s))
    else:
        st.info("No surprises match the current filter.")


# ── Sector Aggregates ──────────────────────────────────────────────────

def _render_sector_aggregates(all_consensus: dict, watchlist: list[str]):
    """Show aggregate beat/miss statistics across all banks."""
    st.subheader("Sector Aggregate Statistics")

    # Collect per-metric stats from uploaded consensus
    metric_stats = {}  # key -> {beats, misses, inlines, total, avg_surprise}

    for ticker, periods in all_consensus.items():
        latest = periods[0] if periods else None
        if not latest:
            continue

        consensus = compile_consensus(ticker, latest["period"])
        # Period-matched reported actuals (None ⇒ this period isn't reported yet,
        # so the bank drops out of the aggregate rather than scoring a forward
        # estimate against trailing actuals — which used to inflate beat rates).
        actual = period_actuals(ticker, latest["period"]) or {}

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

# Cap uploaded consensus files — a large PDF is base64-encoded straight to the
# LLM (cost + truncation risk); Excel/CSV this big is never a consensus sheet.
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _render_upload_section(watchlist: list[str]):
    """Add estimates for the aggregate view — every path is firm-level (the same
    shared component the per-bank tab uses), so a single note, a hand model, and
    a multi-bank sector note all land per (ticker, period, firm)."""
    st.subheader("Add Consensus Estimates")

    input_method = st.radio(
        "Input method",
        ["Single research file (one firm)", "Manual entry (one firm)",
         "Bulk multi-bank (one firm)"],
        key="overview_input_method",
        horizontal=True,
    )

    if input_method == "Single research file (one firm)":
        _render_firm_upload(kp="ovr")

    elif input_method == "Manual entry (one firm)":
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

        # (No Period field here — _render_manual_input has its own period + firm.)
        if upload_ticker:
            _render_manual_input(upload_ticker)

    else:
        _render_bulk_upload()


def _render_firm_upload(default_ticker: str = "", kp: str = "ovr"):
    """THE shared upload+confirm component (both the per-bank Estimates/Earnings
    tab and the aggregate Upload/Input section use this). One firm's research file
    → consensus: a PDF model auto-detects the ticker, the firm, and the firm's
    estimates for EVERY forecast period (multi-period grid) — each saved as its
    own (ticker, period, firm) record. Excel/CSV is a single period entered by
    hand.

    `default_ticker` pre-fills the ticker (the per-bank tab knows it; the
    aggregate tab passes "" and detects). `kp` namespaces every widget/session
    key so multiple instances (e.g. per ticker) never collide."""
    ss = st.session_state
    k_det, k_sig = f"{kp}_detect", f"{kp}_detect_sig"
    field_keys = (f"{kp}_tkr", f"{kp}_per", f"{kp}_firm", f"{kp}_periods")
    uploaded = st.file_uploader(
        "Research file (PDF or Excel) — a PDF model auto-detects the ticker, firm "
        "and every forecast period",
        type=["pdf", "xlsx", "xls", "csv"],
        key=f"{kp}_upload",
    )
    if not uploaded:
        for k in (k_det, k_sig):
            ss.pop(k, None)                    # reset when the file is cleared
        return

    file_bytes = uploaded.read()
    if len(file_bytes) > _MAX_UPLOAD_BYTES:
        st.error(f"File is too large ({len(file_bytes)/1e6:.1f} MB). "
                 "Please upload a file under 10 MB.")
        return

    filename = uploaded.name.lower()
    is_pdf = filename.endswith(".pdf")
    sig = f"{uploaded.name}:{len(file_bytes)}"

    # Parse ONCE per file (cached by signature) — the AI/parse call never re-runs
    # on later reruns (typing in the fields, etc.).
    if ss.get(k_sig) != sig:
        with st.spinner("Reading the model — detecting ticker, firm & per-period "
                        "estimates…" if is_pdf else "Parsing file…"):
            if is_pdf:
                ss[k_det] = detect_and_parse_pdf(file_bytes, filename)
            else:
                ex = parse_consensus_excel(file_bytes, "", "", filename)
                ss[k_det] = {"detected_ticker": "", "detected_firm": "",
                             "excel_metrics": ex.get("metrics", []),
                             "error": ex.get("error")}
        ss[k_sig] = sig
        for k in field_keys:
            ss.pop(k, None)                    # re-seed fields from the new file

    det = ss.get(k_det, {})
    if det.get("error"):
        st.error(f"Error parsing: {det['error']}")
        return

    # Per-bank tab pre-fills the known ticker; aggregate uses the detected one.
    det_tkr = default_ticker or det.get("detected_ticker", "")
    det_firm = det.get("detected_firm", "")

    st.markdown("This is **one firm's** estimates — stored under the firm and "
                "combined with other firms into the consensus.")
    c1, c2 = st.columns(2)
    with c1:
        ticker = st.text_input("Ticker", value=det_tkr, placeholder="e.g. SFST",
                               key=f"{kp}_tkr").strip().upper()
    with c2:
        firm = st.text_input("Firm / Broker", value=det_firm,
                             placeholder="e.g. Brean Capital",
                             key=f"{kp}_firm").strip()

    # ── PDF: many forecast periods; Excel: one period entered by hand ──
    if is_pdf:
        periods = det.get("periods", [])
        if not periods:
            st.warning("No forecast-period estimates found in the document.")
            return
        by_period = {p["period"]: p["metrics"] for p in periods}
        labels = list(by_period)
        total = sum(len(m) for m in by_period.values())
        st.caption(
            (f"Auto-detected firm **{det_firm or '—'}** · **{len(labels)}** forecast "
             f"period(s) · **{total}** estimates. Pick which periods to save:"))
        chosen = st.multiselect("Periods to save", labels, default=labels,
                                key=f"{kp}_periods")
        to_save = [(p, by_period[p]) for p in chosen]
    else:
        period = st.text_input("Period", placeholder="e.g. 2026Q2",
                               key=f"{kp}_per").strip()
        em = det.get("excel_metrics", [])
        if not em:
            st.warning("No consensus metrics found in the file.")
            return
        st.caption(f"{len(em)} metric(s) found.")
        to_save = [(period, em)] if period else []

    if st.button("Save Estimates", type="primary", key=f"{kp}_save"):
        if not ticker or not firm:
            st.error("Enter a ticker and firm before saving.")
        elif not to_save:
            st.error("Select at least one period (and enter it for Excel).")
        else:
            saved, errs = 0, []
            for per, mets in to_save:
                try:
                    save_consensus({"ticker": ticker, "period": per, "firm": firm,
                                    "source": "pdf" if is_pdf else "excel",
                                    "metrics": mets})
                    saved += 1
                except IOError as e:
                    errs.append(f"{per}: {e}")
            if errs:
                for e in errs:
                    st.error(e)
            if saved:
                st.success(f"Saved {firm}'s estimates for {ticker} across "
                           f"{saved} period(s).")
                for k in (k_det, k_sig, *field_keys):
                    ss.pop(k, None)
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

    st.caption("A sector note is from ONE firm — tag the firm so each bank's row "
               "groups with that firm's other estimates.")
    bc1, bc2, bc3 = st.columns([3, 1, 1])

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

    with bc3:
        bulk_firm = st.text_input(
            "Firm / Broker",
            placeholder="e.g. KBW",
            key="bulk_consensus_firm",
        )

    if bulk_file and bulk_period and bulk_firm.strip():
        if st.button("Process Bulk Upload", type="primary", key="bulk_process"):
            file_bytes = bulk_file.read()
            filename = bulk_file.name.lower()
            firm = bulk_firm.strip()

            if len(file_bytes) > _MAX_UPLOAD_BYTES:
                st.error(f"File is too large ({len(file_bytes)/1e6:.1f} MB). "
                         "Please upload a file under 10 MB.")
                st.stop()

            if filename.endswith(".pdf"):
                with st.spinner("AI is reading PDF and extracting consensus estimates for all banks..."):
                    result = parse_bulk_consensus_pdf(file_bytes, bulk_period.strip(), firm)
            else:
                with st.spinner("Parsing multi-bank consensus file..."):
                    result = parse_bulk_consensus(file_bytes, bulk_period.strip(), filename, firm)

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
