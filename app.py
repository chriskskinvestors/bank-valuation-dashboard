"""
Bank Valuation & Analysis Dashboard

A comprehensive, live-updating bank valuation screen using FDIC, SEC EDGAR,
and IBKR APIs. Built with Streamlit for company-wide sharing.
"""

import time
import streamlit as st
import pandas as pd

from config import PRICE_REFRESH_SECONDS, TABS, TAB_LABELS
from data.bank_mapping import get_fdic_cert, get_cik, get_name
from data import fdic_client, sec_client, cache
from data.ibkr_client import get_ibkr_client, get_empty_price
from analysis.metrics import build_all_bank_metrics
from ui.styles import CUSTOM_CSS
from ui.generic_table import render_generic_table
from ui.overview_table import render_data_freshness
from ui.bank_detail import render_bank_detail
from ui.watchlist import render_watchlist_sidebar
from ui.deposit_lookup import render_deposit_lookup

# ── Page config ──────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Bank Valuation Dashboard",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ── Session state initialization ────────────────────────────────────────
if "ibkr_connected" not in st.session_state:
    st.session_state.ibkr_connected = False
if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = 0


# ── Sidebar ─────────────────────────────────────────────────────────────
st.sidebar.markdown(
    '<div class="dashboard-header">'
    "<h1>Bank Valuation</h1>"
    "<p>Live analysis dashboard</p>"
    "</div>",
    unsafe_allow_html=True,
)

# ── Tab navigation ──────────────────────────────────────────────────────
ALL_VIEWS = TAB_LABELS + ["─── Tools ───", "🔍 Deposit Lookup"]
view_index = st.sidebar.selectbox(
    "📊 View",
    options=list(range(len(ALL_VIEWS))),
    format_func=lambda i: ALL_VIEWS[i],
    key="tab_nav",
)

# Determine if this is a table tab or a special page
is_deposit_lookup = ALL_VIEWS[view_index] == "🔍 Deposit Lookup"
is_separator = ALL_VIEWS[view_index].startswith("───")
current_tab = TABS[view_index] if view_index < len(TABS) else None

st.sidebar.markdown("---")

# IBKR connection control
st.sidebar.subheader("IBKR Connection")
ibkr = get_ibkr_client()

if not st.session_state.ibkr_connected:
    if st.sidebar.button("Connect to IBKR"):
        with st.spinner("Connecting to TWS/Gateway..."):
            success = ibkr.connect()
            if success:
                st.session_state.ibkr_connected = True
                ibkr.start_event_loop()
                st.sidebar.success("Connected!")
            else:
                st.sidebar.error("Connection failed. Ensure TWS/Gateway is running.")
else:
    st.sidebar.markdown(
        '<span class="status-dot status-connected"></span> Connected',
        unsafe_allow_html=True,
    )
    if st.sidebar.button("Disconnect"):
        ibkr.disconnect()
        st.session_state.ibkr_connected = False
        st.rerun()

# Watchlist management
watchlist = render_watchlist_sidebar(st)

# Refresh controls
st.sidebar.markdown("---")
st.sidebar.subheader("Refresh")
auto_refresh = st.sidebar.checkbox("Auto-refresh prices", value=True)
if st.sidebar.button("Force refresh all data"):
    cache.clear_all()
    st.session_state.last_refresh = 0
    st.rerun()


# ── Data loading ────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner="Loading FDIC data...")
def load_fdic_data(tickers: tuple) -> tuple[dict, dict]:
    """Load FDIC data for all tickers. Returns (latest, hist_4q)."""
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
    """Load SEC data for all tickers, using cache."""
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
    """Get current prices from IBKR (or empty if not connected)."""
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


# Load all data
fdic_data, fdic_hist = load_fdic_data(tuple(watchlist))
sec_data = load_sec_data(tuple(watchlist))
price_data = load_ibkr_prices(watchlist)

# Build unified metrics
all_metrics = build_all_bank_metrics(watchlist, fdic_data, sec_data, price_data, fdic_hist)
metrics_df = pd.DataFrame(all_metrics)


# ── Main content area ───────────────────────────────────────────────────
if "detail_ticker" in st.session_state and st.session_state.detail_ticker:
    render_bank_detail(st.session_state.detail_ticker, metrics_df)

elif is_deposit_lookup:
    # ── DEPOSIT LOOKUP PAGE ──────────────────────────────────────────
    render_deposit_lookup()

elif is_separator:
    st.info("Select a view from the dropdown.")

elif current_tab:
    # ── TABLE TABS ───────────────────────────────────────────────────
    st.markdown(
        '<div class="dashboard-header">'
        f"<h1>{current_tab['title']}</h1>"
        f"<p>{len(watchlist)} banks | "
        f"{'IBKR Live' if st.session_state.ibkr_connected else 'IBKR Offline'} | "
        f"{len(current_tab['columns'])} columns</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    fdic_ages = {t: cache.fdic_age(t) for t in watchlist}
    sec_ages = {t: cache.sec_age(t) for t in watchlist}
    render_data_freshness(fdic_ages, sec_ages, st.session_state.ibkr_connected)

    st.markdown("")

    result_df = render_generic_table(
        all_metrics, current_tab["columns"], table_key=current_tab["key"]
    )

    if result_df is not None and not result_df.empty:
        st.markdown("---")
        selected_bank = st.selectbox(
            "Select a bank for detailed analysis",
            options=[""] + watchlist,
            format_func=lambda t: f"{t} — {get_name(t)}" if t else "Choose a bank...",
            key=f"bank_sel_{current_tab['key']}",
        )
        if selected_bank:
            st.session_state.detail_ticker = selected_bank
            st.rerun()


# ── Auto-refresh ────────────────────────────────────────────────────────
if auto_refresh and st.session_state.ibkr_connected:
    time.sleep(PRICE_REFRESH_SECONDS)
    st.rerun()
