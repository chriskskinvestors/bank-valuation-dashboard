"""
Bank Valuation & Analysis Dashboard

A comprehensive, live-updating bank valuation screen using FDIC, SEC EDGAR,
and IBKR APIs. Built with Streamlit for company-wide sharing.
"""

import time
import streamlit as st
import pandas as pd

from config import PRICE_REFRESH_SECONDS, TABS, TAB_LABELS, METRICS_BY_KEY
from data.bank_mapping import get_fdic_cert, get_cik, get_name
from data.bank_universe import get_universe_tickers
from data import fdic_client, sec_client, cache
from data.ibkr_client import get_ibkr_client, get_empty_price
from analysis.metrics import build_all_bank_metrics
from ui.styles import CUSTOM_CSS
from ui.generic_table import render_generic_table
from ui.overview_table import render_data_freshness
from ui.bank_detail import render_bank_detail
from ui.watchlist import render_watchlist_sidebar, load_watchlist, load_portfolio
from ui.deposit_lookup import render_deposit_lookup, render_deposits_for_ticker
from ui.filings import render_filings, render_filings_for_ticker
from ui.earnings import render_earnings_consensus, render_earnings_overview
from ui.home import render_home

# ── Page config ──────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Bank Valuation Dashboard",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ── Session state initialization ─────────────────────────────────────────
if "ibkr_connected" not in st.session_state:
    st.session_state.ibkr_connected = False
if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = 0


# ── Sidebar ──────────────────────────────────────────────────────────────
st.sidebar.markdown(
    '<div class="dashboard-header">'
    "<h1>KSK Investors</h1>"
    "<p>Bank Analysis Platform</p>"
    "</div>",
    unsafe_allow_html=True,
)

# ── Level 1 Navigation ──────────────────────────────────────────────────
SECTIONS = ["🏠 Home", "📊 Screening", "🏦 Company Analysis", "📈 Earnings Analysis"]
section = st.sidebar.radio("Navigate", SECTIONS, key="nav_section", label_visibility="collapsed")

st.sidebar.markdown("---")

# ── Level 2 Navigation (contextual) ─────────────────────────────────────
screening_tab = None
company_ticker = None
company_subtab = None

if section == "📊 Screening":
    screening_tab_idx = st.sidebar.selectbox(
        "Table",
        options=list(range(len(TABS))),
        format_func=lambda i: TAB_LABELS[i],
        key="screening_tab",
    )
    screening_tab = TABS[screening_tab_idx]

elif section == "🏦 Company Analysis":
    # Bank selector — watchlist first, then type any ticker
    wl = load_watchlist()
    company_ticker = st.sidebar.selectbox(
        "Bank",
        options=[""] + sorted(wl),
        format_func=lambda t: f"{t} — {get_name(t)}" if t else "Select a bank...",
        key="company_bank",
    )
    ticker_override = st.sidebar.text_input(
        "Or enter ticker",
        placeholder="Any ticker...",
        key="company_ticker_input",
    )
    if ticker_override:
        company_ticker = ticker_override.strip().upper()

    if company_ticker:
        COMPANY_TABS = ["Overview", "Filings", "Deposits", "Earnings"]
        company_subtab = st.sidebar.radio(
            "View",
            COMPANY_TABS,
            key="company_subtab",
            horizontal=True,
        )

st.sidebar.markdown("---")

# ── Refresh button ───────────────────────────────────────────────────────
if st.sidebar.button("🔄 Refresh All Data", use_container_width=True):
    st.cache_data.clear()
    cache.clear_all()
    st.rerun()

# ── IBKR connection ──────────────────────────────────────────────────────
with st.sidebar.expander("IBKR Connection"):
    ibkr = get_ibkr_client()
    if not st.session_state.ibkr_connected:
        if st.button("Connect to IBKR", key="ibkr_connect"):
            with st.spinner("Connecting..."):
                success = ibkr.connect()
                if success:
                    st.session_state.ibkr_connected = True
                    ibkr.start_event_loop()
                    st.success("Connected!")
                else:
                    st.error("Connection failed.")
    else:
        st.markdown('<span class="status-dot status-connected"></span> Connected', unsafe_allow_html=True)
        if st.button("Disconnect", key="ibkr_disconnect"):
            ibkr.disconnect()
            st.session_state.ibkr_connected = False
            st.rerun()

# ── Watchlist + Portfolio management ─────────────────────────────────────
watchlist, portfolio = render_watchlist_sidebar(st)

# ── Auto-refresh toggle ──────────────────────────────────────────────────
auto_refresh = st.sidebar.checkbox("Auto-refresh prices", value=False, key="auto_refresh")


# ── Data loading ─────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner="Loading FDIC data...")
def load_fdic_data(tickers: tuple) -> tuple[dict, dict]:
    latest_results = {}
    hist_results = {}
    for ticker in tickers:
        cert = get_fdic_cert(ticker)
        if not cert:
            continue
        cached = cache.get_fdic(ticker)
        if cached:
            latest_results[ticker] = cached
            hist_cached = cache.get(f"fdic_hist:{ticker}")
            if hist_cached:
                hist_results[ticker] = hist_cached
                continue
        hist_df = fdic_client.fetch_financials(cert, limit=4)
        if not hist_df.empty:
            records = hist_df.to_dict("records")
            hist_results[ticker] = records
            cache.put(f"fdic_hist:{ticker}", records)
            if ticker not in latest_results:
                latest = hist_df.iloc[0].to_dict()
                latest = {k: (None if pd.isna(v) else v) for k, v in latest.items()}
                cache.put_fdic(ticker, latest)
                latest_results[ticker] = latest
    return latest_results, hist_results


@st.cache_data(ttl=3600, show_spinner="Loading SEC data...")
def load_sec_data(tickers: tuple) -> dict:
    results = {}
    for ticker in tickers:
        cached = cache.get_sec(ticker)
        if cached:
            results[ticker] = cached
            continue
        cik = get_cik(ticker)
        if cik:
            data = sec_client.get_latest_fundamentals(cik)
            if data:
                cache.put_sec(ticker, data)
                results[ticker] = data
    return results


def load_ibkr_prices(tickers: list) -> dict:
    if not st.session_state.ibkr_connected:
        return {t: get_empty_price() for t in tickers}
    ibkr = get_ibkr_client()
    ibkr.subscribe(tickers)
    if not ibkr.get_all_prices():
        time.sleep(2)
    prices = {}
    for t in tickers:
        p = ibkr.get_price(t)
        prices[t] = p if p else get_empty_price()
    return prices


def load_all_data(tickers: list[str]) -> tuple[list[dict], pd.DataFrame]:
    fdic, hist = load_fdic_data(tuple(tickers))
    sec = load_sec_data(tuple(tickers))
    prices = load_ibkr_prices(tickers)
    metrics = build_all_bank_metrics(tickers, fdic, sec, prices, hist)
    return metrics, pd.DataFrame(metrics)


# Load watchlist data (always needed)
all_metrics, metrics_df = load_all_data(watchlist)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN CONTENT AREA
# ═══════════════════════════════════════════════════════════════════════════

if section == "🏠 Home":
    render_home(all_metrics, watchlist)

elif section == "📊 Screening" and screening_tab:
    # ── SCREENING: Multi-bank comparison tables ─────────────────────────

    # Filter & sort controls
    f_col1, f_col2, f_col3 = st.columns([2, 2, 1])

    with f_col1:
        bank_filter = st.selectbox(
            "Banks",
            options=["Watchlist", "Portfolio", "All Banks"],
            key=f"filter_{screening_tab['key']}",
        )

    tab_columns = screening_tab["columns"]
    sort_labels = ["Default"]
    sort_keys = [None]
    for col_key in tab_columns:
        m = METRICS_BY_KEY.get(col_key)
        if m:
            sort_labels.append(m["label"])
            sort_keys.append(col_key)

    with f_col2:
        sort_idx = st.selectbox(
            "Sort by",
            options=list(range(len(sort_labels))),
            format_func=lambda i: sort_labels[i],
            key=f"sort_{screening_tab['key']}",
        )

    with f_col3:
        sort_order = st.selectbox(
            "Order",
            options=["Desc", "Asc"],
            key=f"order_{screening_tab['key']}",
        )

    # ── Metric filters ──────────────────────────────────────────────────
    # Build filterable metrics from this tab's columns (only numeric ones)
    filterable = []
    for col_key in tab_columns:
        m = METRICS_BY_KEY.get(col_key)
        if m and m.get("format") in ("pct", "ratio", "currency", "number", "millions", "billions"):
            filterable.append((col_key, m["label"], m.get("format", "")))

    active_filters = []
    with st.expander("🔍 Metric Filters", expanded=False):
        # Up to 4 filters in a row
        num_filters = st.selectbox(
            "Number of filters",
            options=[1, 2, 3, 4],
            key=f"num_filters_{screening_tab['key']}",
        )

        filter_labels = ["—"] + [f[1] for f in filterable]
        filter_keys = [None] + [f[0] for f in filterable]

        for fi in range(num_filters):
            fc1, fc2, fc3 = st.columns([3, 1, 2])
            with fc1:
                filt_idx = st.selectbox(
                    "Metric",
                    options=list(range(len(filter_labels))),
                    format_func=lambda i, fl=filter_labels: fl[i],
                    key=f"filt_metric_{screening_tab['key']}_{fi}",
                    label_visibility="collapsed" if fi > 0 else "visible",
                )
            with fc2:
                filt_op = st.selectbox(
                    "Op",
                    options=["<", "≤", ">", "≥", "="],
                    key=f"filt_op_{screening_tab['key']}_{fi}",
                    label_visibility="collapsed" if fi > 0 else "visible",
                )
            with fc3:
                filt_val = st.number_input(
                    "Value",
                    value=0.0,
                    step=0.1,
                    format="%.2f",
                    key=f"filt_val_{screening_tab['key']}_{fi}",
                    label_visibility="collapsed" if fi > 0 else "visible",
                )

            filt_key = filter_keys[filt_idx] if filt_idx > 0 else None
            if filt_key is not None:
                active_filters.append((filt_key, filt_op, filt_val))

    # Resolve which banks to show
    if bank_filter == "Portfolio":
        display_tickers = portfolio
        display_metrics = None
    elif bank_filter == "All Banks":
        display_tickers = get_universe_tickers()
        display_metrics = None
    else:
        display_tickers = watchlist
        display_metrics = all_metrics

    if display_metrics is None:
        if display_tickers:
            display_metrics, _ = load_all_data(display_tickers)
        else:
            display_metrics = []

    # Apply metric filters
    if active_filters and display_metrics:
        filtered = []
        for m in display_metrics:
            passes = True
            for fk, fop, fv in active_filters:
                val = m.get(fk)
                if val is None:
                    passes = False
                    break
                try:
                    val = float(val)
                except (TypeError, ValueError):
                    passes = False
                    break
                if fop == "<" and not (val < fv):
                    passes = False
                elif fop == "≤" and not (val <= fv):
                    passes = False
                elif fop == ">" and not (val > fv):
                    passes = False
                elif fop == "≥" and not (val >= fv):
                    passes = False
                elif fop == "=" and not (abs(val - fv) < 0.005):
                    passes = False
                if not passes:
                    break
            if passes:
                filtered.append(m)
        display_metrics = filtered

    # Apply sorting
    sort_key = sort_keys[sort_idx] if sort_idx > 0 else None
    if sort_key and display_metrics:
        ascending = sort_order == "Asc"
        display_metrics = sorted(
            display_metrics,
            key=lambda m: (m.get(sort_key) is None, m.get(sort_key) or 0),
            reverse=not ascending,
        )

    # Header
    filter_note = f" · {len(active_filters)} filter{'s' if len(active_filters) != 1 else ''}" if active_filters else ""
    total_before = len(display_tickers) if not active_filters else "filtered"
    st.markdown(
        '<div class="dashboard-header">'
        f"<h1>{screening_tab['title']}</h1>"
        f"<p>{len(display_metrics)} banks ({bank_filter}{filter_note}) | "
        f"{'IBKR Live' if st.session_state.ibkr_connected else 'IBKR Offline'} | "
        f"{len(screening_tab['columns'])} columns</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    fdic_ages = {t: cache.fdic_age(t) for t in display_tickers[:10]}
    sec_ages = {t: cache.sec_age(t) for t in display_tickers[:10]}
    render_data_freshness(fdic_ages, sec_ages, st.session_state.ibkr_connected)

    st.markdown("")

    render_generic_table(
        display_metrics, screening_tab["columns"], table_key=screening_tab["key"]
    )

elif section == "🏦 Company Analysis":
    # ── COMPANY ANALYSIS: Single-bank deep dive ─────────────────────────

    if not company_ticker:
        st.markdown(
            '<div class="dashboard-header">'
            "<h1>Company Analysis</h1>"
            "<p>Select a bank from the sidebar to begin</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.info("👈 Choose a bank from the sidebar dropdown or type a ticker.")

    elif company_subtab == "Overview":
        render_bank_detail(company_ticker, metrics_df)

    elif company_subtab == "Filings":
        render_filings_for_ticker(company_ticker)

    elif company_subtab == "Deposits":
        render_deposits_for_ticker(company_ticker)

    elif company_subtab == "Earnings":
        # Build actual metrics dict for this ticker
        ticker_metrics = {}
        for m in all_metrics:
            if m.get("ticker") == company_ticker:
                ticker_metrics = m
                break
        if not ticker_metrics:
            # Load on demand if not in watchlist
            single_metrics, _ = load_all_data([company_ticker])
            ticker_metrics = single_metrics[0] if single_metrics else {}

        render_earnings_consensus(company_ticker, ticker_metrics)

elif section == "📈 Earnings Analysis":
    # ── EARNINGS ANALYSIS: Aggregate tracking ───────────────────────────
    render_earnings_overview(watchlist, all_metrics)


# ── Auto-refresh ─────────────────────────────────────────────────────────
if auto_refresh and st.session_state.ibkr_connected:
    time.sleep(PRICE_REFRESH_SECONDS)
    st.rerun()
