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

    # ── Key stats grid ───────────────────────────────────────────────
    bank_row = all_metrics_df[all_metrics_df["ticker"] == ticker]
    if not bank_row.empty:
        row = bank_row.iloc[0]
        # (key, label) — 12 cards across two rows: market/valuation then fundamentals.
        cards = [
            ("price", "Price"), ("change_pct", "Chg %"), ("market_cap", "Mkt Cap"),
            ("pe_ratio", "P/E"), ("ptbv_ratio", "P/TBV"), ("dividend_yield", "Div Yield"),
            ("roatce_blended", "ROATCE"), ("roaa", "ROAA"), ("nim", "NIM"),
            ("efficiency_ratio", "Efficiency"), ("cet1_ratio", "CET1"), ("npl_ratio", "NPL"),
        ]

        def _stat_card(key, label):
            m = METRICS_BY_KEY.get(key, {})
            val = row.get(key)
            disp = (format_value(val, m.get("format", "number"), m.get("decimals", 2))
                    if val is not None and not pd.isna(val) else "—")
            accent = "inherit"
            if key == "change_pct" and val is not None and not pd.isna(val):
                accent = "#059669" if val >= 0 else "#dc2626"
            return (
                '<div style="background:rgba(148,163,184,0.06); border:1px solid '
                'rgba(148,163,184,0.18); border-radius:10px; padding:9px 13px;">'
                f'<div style="font-size:0.62rem; color:#64748b; font-weight:600; '
                'text-transform:uppercase; letter-spacing:0.03em;">' + label + '</div>'
                f'<div style="font-size:1.12rem; font-weight:700; color:{accent}; '
                f'line-height:1.35;">{disp}</div></div>'
            )

        st.markdown(
            '<div style="display:grid; grid-template-columns:repeat(6, 1fr); gap:8px;">'
            + "".join(_stat_card(k, lbl) for k, lbl in cards) + "</div>",
            unsafe_allow_html=True,
        )

        # Click-through to the primary data sources for this bank.
        cik = info.get("cik") if info else None
        cert = info.get("fdic_cert") if info else None
        links = []
        if cik:
            links.append(
                f'<a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany'
                f'&CIK={cik}&type=10-K&dateb=&owner=include&count=40" target="_blank" '
                'style="text-decoration:none;">📄 SEC filings (EDGAR)</a>')
        if cert:
            links.append(
                f'<a href="https://banks.data.fdic.gov/bankfind-suite/bankfind/details/'
                f'{cert}" target="_blank" style="text-decoration:none;">🏦 FDIC BankFind</a>')
        if links:
            st.markdown(
                '<div style="margin-top:7px; font-size:0.8rem; color:#64748b;">'
                'Sources: ' + " &nbsp;·&nbsp; ".join(links) + "</div>",
                unsafe_allow_html=True,
            )

    st.markdown("---")

    # ── Price chart ──────────────────────────────────────────────────
    st.subheader("Price History")
    duration_options = {"1W": "1 W", "1M": "1 M", "3M": "3 M", "1Y": "1 Y", "5Y": "5 Y"}
    selected_duration = st.radio(
        "Period", list(duration_options.keys()), horizontal=True, key="price_period"
    )

    # Try IBKR first (when running locally with TWS); fall back to FMP
    # (works in cloud + offline IBKR).
    ibkr = get_ibkr_client()
    hist_df = pd.DataFrame()
    if ibkr.connected:
        duration_str = duration_options[selected_duration]
        bar_size = "1 day" if selected_duration in ("3M", "1Y", "5Y") else "1 hour" if selected_duration == "1M" else "15 mins"
        hist_df = ibkr.get_historical_data(ticker, duration_str, bar_size)
    if hist_df is None or hist_df.empty:
        try:
            from data.fmp_client import get_history
            hist_df = get_history(ticker, selected_duration)
        except Exception as e:
            print(f"[bank_detail] FMP history fallback failed: {e}")
            hist_df = pd.DataFrame()

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
