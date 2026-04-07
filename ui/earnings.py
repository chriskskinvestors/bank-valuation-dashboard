"""
Earnings vs Consensus — per-bank comparison and aggregate tracking.
"""

import streamlit as st
import pandas as pd

from data.bank_mapping import get_name, get_fdic_cert, get_cik
from data.consensus import (
    parse_consensus_pdf,
    parse_consensus_excel,
    save_consensus,
    load_consensus,
    list_consensus,
    list_all_consensus,
    compare_consensus_to_actual,
    METRIC_DISPLAY,
    METRIC_UNITS,
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
    """Format a value with its unit."""
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
    """Format a delta value."""
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


# ── Per-Bank Earnings View ───────────────────────────────────────────────

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

    # ── Upload consensus document ────────────────────────────────────────
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
            placeholder="e.g. 2025Q4, 2026Q1",
            key=f"consensus_period_{ticker}",
        )

    # Process upload
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

    # ── Show existing consensus data ─────────────────────────────────────
    periods = list_consensus(ticker)

    if not periods:
        st.info(
            f"No consensus data for {ticker} yet. "
            "Upload a consensus PDF or Excel file above to get started."
        )
        _render_key_metrics(ticker, actual_metrics)
        return

    # Period selector
    period_labels = [f"{p['period']} ({p['source']}, {p['metric_count']} metrics)" for p in periods]
    selected_idx = st.selectbox(
        "Select period",
        options=list(range(len(periods))),
        format_func=lambda i: period_labels[i],
        key=f"consensus_period_select_{ticker}",
    )

    selected_period = periods[selected_idx]["period"]
    consensus = load_consensus(ticker, selected_period)

    if not consensus:
        st.warning("Could not load consensus data.")
        return

    # ── Comparison table ─────────────────────────────────────────────────
    st.subheader("Consensus vs Actual")

    comparison = compare_consensus_to_actual(consensus, actual_metrics)

    if not comparison:
        st.info("No comparable metrics found.")
        return

    # Build display table
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
            "Result": label,
        })

    df = pd.DataFrame(rows)

    # Apply row coloring
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
        **{"font-size": "0.8rem", "padding": "4px 8px"}
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

    st.markdown("---")
    _render_key_metrics(ticker, actual_metrics)


def _render_key_metrics(ticker: str, actual_metrics: dict):
    """Show key reported metrics for context."""
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


# ── Aggregate Earnings View ──────────────────────────────────────────────

def render_earnings_overview(watchlist: list[str], all_metrics: list[dict]):
    """Render aggregate earnings tracking across all banks."""

    st.markdown(
        '<div class="dashboard-header">'
        "<h1>Earnings Analysis</h1>"
        "<p>Consensus tracking across all banks</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    # Get all consensus data
    all_consensus = list_all_consensus()

    if not all_consensus:
        st.info(
            "No consensus data uploaded yet. Go to **Company Analysis → Earnings** "
            "to upload consensus estimates for individual banks."
        )
        return

    # Build summary table
    metrics_by_ticker = {m["ticker"]: m for m in all_metrics}

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

            # Find EPS beat/miss specifically
            eps_result = next((c for c in comparison if c["key"] == "eps"), None)

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
                "Score": f"{beats}/{total}" if total > 0 else "—",
            })

    if rows:
        df = pd.DataFrame(rows)

        def _color_score(row):
            beats = row.get("Beats", 0)
            misses = row.get("Misses", 0)
            if beats > misses:
                return [_BEAT_STYLE] * len(row)
            elif misses > beats:
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
