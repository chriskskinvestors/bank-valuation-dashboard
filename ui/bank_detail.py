"""
Bank detail page — deep dive on a single bank.
"""

import pandas as pd
import streamlit as st

from config import METRICS, METRICS_BY_KEY, METRIC_CATEGORIES
from data.bank_mapping import get_name, get_bank_info
from data import fdic_client, sec_client
from data.ibkr_client import get_ibkr_client
from analysis.peer_comparison import build_radar_data, get_peer_group_by_asset_size
from utils.formatting import format_value
from ui.charts import price_chart, metrics_trend_chart, peer_radar_chart, balance_sheet_chart


def render_bank_detail(ticker: str, all_metrics_df: pd.DataFrame):
    """Render the full detail page for a single bank."""
    info = get_bank_info(ticker)
    name = info["name"] if info else ticker

    st.markdown(f"### {name} ({ticker})")

    # Back button
    if st.button("< Back to Overview"):
        st.session_state.pop("detail_ticker", None)
        st.rerun()

    # ── Key stats row ────────────────────────────────────────────────
    bank_row = all_metrics_df[all_metrics_df["ticker"] == ticker]
    if not bank_row.empty:
        row = bank_row.iloc[0]
        stat_cols = st.columns(6)
        key_stats = ["price", "change_pct", "pe_ratio", "ptbv_ratio", "roatce", "cet1_ratio"]
        for i, key in enumerate(key_stats):
            m = METRICS_BY_KEY.get(key)
            if m:
                val = row.get(key)
                with stat_cols[i]:
                    st.metric(
                        label=m["label"],
                        value=format_value(val, m["format"], m.get("decimals", 2)),
                    )

    st.markdown("---")

    # ── Price chart ──────────────────────────────────────────────────
    st.subheader("Price History")
    duration_options = {"1W": "1 W", "1M": "1 M", "3M": "3 M", "1Y": "1 Y", "5Y": "5 Y"}
    selected_duration = st.radio(
        "Period", list(duration_options.keys()), horizontal=True, key="price_period"
    )

    ibkr = get_ibkr_client()
    hist_df = pd.DataFrame()
    if ibkr.connected:
        duration_str = duration_options[selected_duration]
        bar_size = "1 day" if selected_duration in ("3M", "1Y", "5Y") else "1 hour" if selected_duration == "1M" else "15 mins"
        hist_df = ibkr.get_historical_data(ticker, duration_str, bar_size)

    st.plotly_chart(price_chart(hist_df, ticker), use_container_width=True)

    # ── FDIC metrics trend ──────────────────────────────────────────
    st.subheader("Key Metrics Trend")
    cert = info["fdic_cert"] if info else None
    fdic_hist = pd.DataFrame()
    if cert:
        fdic_hist = fdic_client.get_historical_financials(cert, quarters=20)

    col1, col2 = st.columns(2)
    with col1:
        st.plotly_chart(
            metrics_trend_chart(fdic_hist, ["roaa", "roatce", "nim"], "Profitability"),
            use_container_width=True,
        )
    with col2:
        st.plotly_chart(
            metrics_trend_chart(fdic_hist, ["npl_ratio", "nco_ratio"], "Credit Quality"),
            use_container_width=True,
        )

    # ── Balance sheet trend ─────────────────────────────────────────
    st.subheader("Balance Sheet")
    st.plotly_chart(balance_sheet_chart(fdic_hist), use_container_width=True)

    # ── All metrics table ───────────────────────────────────────────
    st.subheader("All Metrics")
    if not bank_row.empty:
        row = bank_row.iloc[0]
        for category in METRIC_CATEGORIES:
            cat_metrics = [m for m in METRICS if m["category"] == category]
            if not cat_metrics:
                continue
            st.markdown(f"**{category}**")
            metric_cols = st.columns(min(4, len(cat_metrics)))
            for i, m in enumerate(cat_metrics):
                val = row.get(m["key"])
                with metric_cols[i % len(metric_cols)]:
                    st.metric(
                        label=m["label"],
                        value=format_value(val, m["format"], m.get("decimals", 2)),
                    )

    # ── Peer comparison radar ───────────────────────────────────────
    st.subheader("Peer Comparison")
    peer_metrics = ["roatce", "nim", "cet1_ratio", "efficiency_ratio", "npl_ratio", "pe_ratio"]
    peers = get_peer_group_by_asset_size(all_metrics_df, ticker, n=4)
    compare_tickers = [ticker] + peers

    radar = build_radar_data(all_metrics_df, compare_tickers, peer_metrics)
    st.plotly_chart(peer_radar_chart(radar), use_container_width=True)

    # ── SEC filings ─────────────────────────────────────────────────
    st.subheader("Recent SEC Filings")
    cik = info["cik"] if info else None
    if cik:
        filing_info = sec_client.get_filing_info(cik)
        if filing_info and filing_info.get("recent_filings"):
            for f in filing_info["recent_filings"]:
                accession_clean = f["accession"].replace("-", "")
                url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_clean}"
                st.markdown(f"- **{f['form']}** — {f['date']} — [{f.get('description', 'View')}]({url})")
        else:
            st.info("No recent filings found.")
    else:
        st.info("SEC CIK not mapped for this bank.")
