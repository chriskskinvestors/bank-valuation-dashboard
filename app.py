"""
Bank Valuation & Analysis Dashboard

A comprehensive, live-updating bank valuation screen using FDIC, SEC EDGAR,
and IBKR APIs. Built with Streamlit for company-wide sharing.
"""

import time
import streamlit as st
import pandas as pd

from config import PRICE_REFRESH_SECONDS, TABS, TAB_LABELS, METRICS, METRICS_BY_KEY
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
from ui.historicals import render_historicals
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
SECTIONS = ["🏠 Home", "🌐 Macro", "📊 Screening", "🏦 Company Analysis", "🆚 Peer Comparison", "📈 Earnings Analysis", "📰 Activity", "🗺️ Geographic"]
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
    # Deep-link support: a metric card can link to ?bank=X&tab=<token> to jump
    # straight to the tab that shows that figure (carries the bank so the deep
    # link survives a full page navigation).
    _qp = st.query_params
    if _qp.get("bank"):
        st.session_state["company_bank"] = _qp.get("bank").upper()

    # The bank is picked in the MAIN content area (not the sidebar). Read the
    # current selection from session_state so the sub-tab nav can render here.
    _ov = (st.session_state.get("company_ticker_input") or "").strip().upper()
    company_ticker = _ov or (st.session_state.get("company_bank") or None)

    if company_ticker:
        COMPANY_TABS = ["Overview", "Activity", "Financials", "Filings", "Deposits", "Credit", "Capital", "NIM Sensitivity", "Valuation", "Ownership", "Earnings", "🔍 Data Quality"]
        _TAB_TOKENS = {"financials": "Financials", "valuation": "Valuation",
                       "dataquality": "🔍 Data Quality", "filings": "Filings"}
        _goto = _TAB_TOKENS.get((_qp.get("tab") or "").lower())
        if _goto and _goto in COMPANY_TABS:
            st.session_state["company_subtab"] = _goto
        company_subtab = st.sidebar.radio(
            "View",
            COMPANY_TABS,
            key="company_subtab",
            horizontal=True,
        )
        # Consume deep-link params so they don't persist on the next interaction.
        for _k in ("bank", "tab"):
            if _k in _qp:
                try:
                    del st.query_params[_k]
                except Exception:
                    pass

st.sidebar.markdown("---")

# ── Refresh button ───────────────────────────────────────────────────────
if st.sidebar.button("🔄 Refresh All Data", use_container_width=True):
    st.cache_data.clear()
    cache.clear_all()
    st.rerun()

# ── IBKR connection ──────────────────────────────────────────────────────
# In cloud deployment ib_insync isn't installed (HAS_IBKR=False), so the
# Connect button can't succeed. Hide the whole expander unless the local
# install actually has IBKR support — keeps the sidebar uncluttered and
# avoids "Connected" UI artifacts that confuse users.
from data.ibkr_client import HAS_IBKR
if HAS_IBKR:
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
else:
    # Force-reset the flag so the rest of the app behaves as offline,
    # regardless of any state inherited from another browser tab.
    st.session_state.ibkr_connected = False

# ── Watchlist + Portfolio management ─────────────────────────────────────
watchlist, portfolio = render_watchlist_sidebar(st)

# ── Auto-refresh toggle ──────────────────────────────────────────────────
auto_refresh = st.sidebar.checkbox("Auto-refresh prices", value=False, key="auto_refresh")


# ── Data loading ─────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner="Loading FDIC data...")
def load_fdic_data(tickers: tuple) -> tuple[dict, dict]:
    """Load FDIC data — batched cache lookup + parallel fetch for misses."""
    latest_results = {}
    hist_results = {}

    # First pass: ONE batched SELECT for all watchlist tickers' cache entries
    # Old code did N individual SELECTs (2N round-trips); for a 50-bank
    # watchlist over a Cloud SQL Unix socket this dominated load time.
    fdic_keys = [f"fdic:{t}" for t in tickers if get_fdic_cert(t)]
    hist_keys = [f"fdic_hist:{t}" for t in tickers if get_fdic_cert(t)]
    all_keys = fdic_keys + hist_keys
    batch = cache.get_multi(all_keys)

    uncached_certs = {}  # ticker -> cert
    for ticker in tickers:
        cert = get_fdic_cert(ticker)
        if not cert:
            continue
        cached = batch.get(f"fdic:{ticker}")
        hist_cached = batch.get(f"fdic_hist:{ticker}")
        if cached and hist_cached:
            latest_results[ticker] = cached
            hist_results[ticker] = hist_cached
        else:
            uncached_certs[ticker] = cert

    # Second pass: fetch uncached tickers IN PARALLEL
    if uncached_certs:
        parallel_results = fdic_client.fetch_multiple_banks_parallel(uncached_certs, limit=4)
        for ticker, hist_df in parallel_results.items():
            if hist_df is None or hist_df.empty:
                continue
            records = hist_df.to_dict("records")
            hist_results[ticker] = records
            cache.put(f"fdic_hist:{ticker}", records)

            latest = hist_df.iloc[0].to_dict()
            latest = {k: (None if pd.isna(v) else v) for k, v in latest.items()}
            cache.put_fdic(ticker, latest)
            latest_results[ticker] = latest

    return latest_results, hist_results


@st.cache_data(ttl=3600, show_spinner="Loading SEC data...")
def load_sec_data(tickers: tuple) -> dict:
    """Load SEC data — batched cache lookup + parallel fetch for misses."""
    results = {}

    # Same batch optimization as FDIC: one SELECT for all keys
    sec_keys = [f"sec:{t}" for t in tickers]
    batch = cache.get_multi(sec_keys)

    uncached_ciks = {}  # ticker -> cik
    for ticker in tickers:
        cached = batch.get(f"sec:{ticker}")
        if cached:
            results[ticker] = cached
            continue
        cik = get_cik(ticker)
        if cik:
            uncached_ciks[ticker] = cik

    # Second pass: fetch uncached tickers IN PARALLEL (rate-limited to SEC 10 req/sec)
    if uncached_ciks:
        parallel_results = sec_client.fetch_multiple_banks_parallel(uncached_ciks)
        for ticker, data in parallel_results.items():
            if data:
                cache.put_sec(ticker, data)
                results[ticker] = data

    return results


def load_prices(tickers: list, max_wait: float = 3.0) -> dict:
    """
    Load real-time prices for a list of tickers.

    Priority:
      1. IBKR live data — when the user has TWS/Gateway connected locally.
      2. FMP API        — used in cloud (no IBKR), or as fallback when IBKR
                          is disconnected. ~60s cache.
      3. Empty dict     — when neither source is available.

    Returns {ticker: {price, change, change_pct, ...}} compatible with the
    existing IBKR shape so downstream metric builders stay unchanged.
    """
    # 1. IBKR if live
    if st.session_state.ibkr_connected:
        ibkr = get_ibkr_client()
        ibkr.subscribe(tickers)
        waited = 0.0
        while waited < max_wait:
            if all(ibkr.get_price(t) for t in tickers):
                break
            time.sleep(0.1)
            waited += 0.1
        prices = {}
        for t in tickers:
            p = ibkr.get_price(t)
            prices[t] = p if p else get_empty_price()
        # If IBKR returned mostly empty prices (e.g. subscription issues),
        # fall through to FMP as a backup
        non_empty = sum(1 for p in prices.values() if p.get("price"))
        if non_empty >= len(tickers) * 0.5:
            return prices

    # 2. Warm price cache (Postgres) — the fast path in cloud. A market-hours
    #    job (jobs/refresh_prices.py) keeps every universe price fresh, so a
    #    full screen reads instantly instead of fanning out ~355 FMP calls
    #    (~70s cold against FMP's ~300/min cap). Stale/missing tickers fall
    #    through to live FMP below.
    out: dict = {}
    try:
        from data.price_cache_store import get_prices as get_warm_prices
        out = get_warm_prices(tickers)  # {ticker: quote} for cached rows
    except Exception as e:
        print(f"[prices] warm cache read failed: {type(e).__name__}: {e}")

    missing = [t for t in tickers if t not in out]

    # 3. Live FMP for anything not warm-cached (local/dev, brand-new tickers,
    #    or a cache that hasn't been warmed yet).
    if missing:
        try:
            from data.fmp_client import get_quote_batch, _has_key
            if _has_key():
                out.update(get_quote_batch(missing))
        except Exception as e:
            print(f"[prices] FMP fallback failed: {type(e).__name__}: {e}")

    # 4. Fill any remaining gaps with empty quotes.
    return {t: out.get(t) or get_empty_price() for t in tickers}


# Backwards-compat alias for any callers still using the old name
load_ibkr_prices = load_prices


def load_all_data(tickers: list[str]) -> tuple[list[dict], pd.DataFrame]:
    fdic, hist = load_fdic_data(tuple(tickers))
    sec = load_sec_data(tuple(tickers))
    prices = load_prices(tickers)
    metrics = build_all_bank_metrics(tickers, fdic, sec, prices, hist)
    return metrics, pd.DataFrame(metrics)


@st.cache_data(ttl=3600, show_spinner=False)
def load_single_bank_metrics_cached(ticker: str) -> dict:
    """
    Load metrics for a SINGLE bank with aggressive caching.

    Used when switching between banks in Company Analysis — reuses cached
    data instead of rebuilding the entire watchlist metrics.

    Returns a single metrics dict for the ticker.
    """
    fdic, hist = load_fdic_data((ticker,))
    sec = load_sec_data((ticker,))
    prices = load_ibkr_prices([ticker])
    metrics = build_all_bank_metrics([ticker], fdic, sec, prices, hist)
    return metrics[0] if metrics else {"ticker": ticker}


# ── Lazy data loading ─────────────────────────────────────────────────
# Only load watchlist metrics if actually needed by the current view.
# This avoids a 5-15 second wait when switching to "All Banks" screening
# or opening the Company Analysis page for a single bank.

_NEEDS_WATCHLIST = (
    section == "🏠 Home"  # Home shows top opportunities from watchlist
    or (section == "📊 Screening" and screening_tab is not None)  # Screening default filter
    or section == "🆚 Peer Comparison"  # Peer comparison needs all watchlist metrics
)

if _NEEDS_WATCHLIST:
    all_metrics, metrics_df = load_all_data(watchlist)
    # Stash for cross-tab use (peer-relative valuation, home alerts, etc.)
    cache.put("watchlist_metrics_last", all_metrics)
else:
    all_metrics = []
    metrics_df = pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════════════
# MAIN CONTENT AREA
# ═══════════════════════════════════════════════════════════════════════════

if section == "🏠 Home":
    render_home(all_metrics, watchlist)

elif section == "📊 Screening" and screening_tab:
    # ── SCREENING: Multi-bank comparison tables ─────────────────────────
    from data.saved_screens import (
        save_screen, load_screen, list_screens, delete_screen,
    )

    tab_key = screening_tab["key"]
    tab_columns = screening_tab["columns"]

    # ── Saved Screens bar ──────────────────────────────────────────────
    with st.expander("💾 Saved Screens", expanded=False):
        saved = list_screens()
        saved_for_tab = [s for s in saved if s.get("tab") == tab_key]

        load_col, del_col, new_col = st.columns([2, 1, 2])

        with load_col:
            load_options = ["— select —"] + [s["name"] for s in saved_for_tab]
            load_choice = st.selectbox(
                f"Load saved screen ({len(saved_for_tab)} available for this tab)",
                load_options,
                key=f"load_screen_{tab_key}",
            )
            if load_choice != "— select —":
                if st.button(f"📂 Load '{load_choice}'", key=f"load_btn_{tab_key}"):
                    cfg = load_screen(load_choice)
                    if cfg:
                        # Apply saved config to session_state
                        ss = st.session_state
                        if cfg.get("bank_filter"):
                            ss[f"filter_{tab_key}"] = cfg["bank_filter"]
                        if cfg.get("sort_idx") is not None:
                            ss[f"sort_{tab_key}"] = cfg["sort_idx"]
                        if cfg.get("sort_order"):
                            ss[f"order_{tab_key}"] = cfg["sort_order"]
                        if cfg.get("num_filters"):
                            ss[f"num_filters_{tab_key}"] = cfg["num_filters"]
                        for i, flt in enumerate(cfg.get("filters", [])):
                            ss[f"filt_metric_{tab_key}_{i}"] = flt["metric_idx"]
                            ss[f"filt_op_{tab_key}_{i}"] = flt["op"]
                            ss[f"filt_val_{tab_key}_{i}"] = flt["value"]
                        ss[f"custom_cols_{tab_key}"] = cfg.get("columns") or tab_columns
                        st.rerun()

        with del_col:
            if load_choice != "— select —":
                if st.button(f"🗑 Delete", key=f"del_btn_{tab_key}"):
                    delete_screen(load_choice)
                    st.success(f"Deleted '{load_choice}'")
                    st.rerun()

        with new_col:
            with st.form(f"save_form_{tab_key}", clear_on_submit=True):
                new_name = st.text_input("Save current as…",
                                          placeholder="e.g. Value CRE Overweight",
                                          key=f"new_screen_name_{tab_key}")
                if st.form_submit_button("💾 Save Screen") and new_name:
                    # Collect current state
                    ss = st.session_state
                    num_filt = ss.get(f"num_filters_{tab_key}", 1)
                    filters = []
                    for i in range(num_filt):
                        mi = ss.get(f"filt_metric_{tab_key}_{i}", 0)
                        op = ss.get(f"filt_op_{tab_key}_{i}", "<")
                        val = ss.get(f"filt_val_{tab_key}_{i}", 0.0)
                        filters.append({"metric_idx": mi, "op": op, "value": val})
                    cfg = {
                        "tab_key": tab_key,
                        "bank_filter": ss.get(f"filter_{tab_key}", "Watchlist"),
                        "sort_idx": ss.get(f"sort_{tab_key}", 0),
                        "sort_order": ss.get(f"order_{tab_key}", "Desc"),
                        "num_filters": num_filt,
                        "filters": filters,
                        "columns": ss.get(f"custom_cols_{tab_key}") or tab_columns,
                    }
                    save_screen(new_name, cfg)
                    st.success(f"Saved '{new_name}'")
                    st.rerun()

    # Filter & sort controls
    f_col1, f_col2, f_col3 = st.columns([2, 2, 1])

    with f_col1:
        bank_filter = st.selectbox(
            "Banks",
            options=["Watchlist", "Portfolio", "All Banks"],
            key=f"filter_{tab_key}",
        )

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
            key=f"sort_{tab_key}",
        )

    with f_col3:
        sort_order = st.selectbox(
            "Order",
            options=["Desc", "Asc"],
            key=f"order_{tab_key}",
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
        f"{'IBKR Live' if st.session_state.ibkr_connected else 'FMP'} | "
        f"{len(screening_tab['columns'])} columns</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    fdic_ages = {t: cache.fdic_age(t) for t in display_tickers[:10]}
    sec_ages = {t: cache.sec_age(t) for t in display_tickers[:10]}
    render_data_freshness(fdic_ages, sec_ages, st.session_state.ibkr_connected)

    # ── Column picker + Excel export ──────────────────────────────────
    with st.expander("🧰 Customize columns & export", expanded=False):
        cc_col, ex_col = st.columns([3, 1])

        with cc_col:
            # All possible metric keys (sorted by category)
            all_metric_keys = [m["key"] for m in METRICS if m.get("format") != "date"]
            # Default to this tab's columns if user hasn't customized
            default_cols = st.session_state.get(f"custom_cols_{tab_key}", tab_columns)
            # Ensure defaults are valid
            default_cols = [c for c in default_cols if c in all_metric_keys]

            selected_cols = st.multiselect(
                "Columns to display (leave as-is for the tab's default view)",
                all_metric_keys,
                default=default_cols,
                format_func=lambda k: f"{METRICS_BY_KEY.get(k, {}).get('label', k)}  ({METRICS_BY_KEY.get(k, {}).get('category', '—')})",
                key=f"custom_cols_{tab_key}",
            )

        with ex_col:
            st.markdown("**Export**")
            # Prepare DataFrame for export
            if display_metrics:
                export_df = pd.DataFrame(display_metrics)
                # Friendly column labels
                display_cols_export = selected_cols or tab_columns
                export_cols = ["ticker"] + [c for c in display_cols_export if c in export_df.columns]
                export_df = export_df[export_cols].copy()
                # Rename to labels
                rename = {"ticker": "Ticker"}
                for c in display_cols_export:
                    m = METRICS_BY_KEY.get(c)
                    if m:
                        rename[c] = m["label"]
                export_df = export_df.rename(columns=rename)

                # CSV export (no extra deps required)
                csv_bytes = export_df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    "📄 CSV",
                    csv_bytes,
                    file_name=f"{tab_key}_{bank_filter.lower().replace(' ','_')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key=f"csv_{tab_key}",
                )

                # Excel export (needs openpyxl, which is in requirements.txt already)
                try:
                    import io
                    buf = io.BytesIO()
                    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                        export_df.to_excel(writer, index=False, sheet_name=tab_key[:31])
                        # Freeze header row, set column widths
                        ws = writer.sheets[tab_key[:31]]
                        ws.freeze_panes = "A2"
                        for col_idx, col_name in enumerate(export_df.columns, start=1):
                            max_len = max(
                                len(str(col_name)),
                                export_df[col_name].astype(str).map(len).max() if len(export_df) else 10,
                            )
                            ws.column_dimensions[chr(64 + col_idx) if col_idx <= 26 else "A"].width = min(28, max_len + 2)
                    st.download_button(
                        "📊 Excel",
                        buf.getvalue(),
                        file_name=f"{tab_key}_{bank_filter.lower().replace(' ','_')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                        key=f"xlsx_{tab_key}",
                    )
                except Exception as e:
                    st.caption(f"Excel export unavailable: {type(e).__name__}")

    st.markdown("")

    # Use customized columns if set, else tab defaults
    display_cols_final = st.session_state.get(f"custom_cols_{tab_key}") or tab_columns
    if not display_cols_final:
        display_cols_final = tab_columns

    render_generic_table(
        display_metrics, display_cols_final, table_key=tab_key
    )

elif section == "🏦 Company Analysis":
    # ── COMPANY ANALYSIS: Single-bank deep dive ─────────────────────────

    # In-page bank picker (no need to use the sidebar). The widgets live here;
    # the sidebar sub-tab nav reads the selection from session_state above.
    _wl = load_watchlist()
    _pc1, _pc2 = st.columns([3, 2])
    with _pc1:
        st.selectbox(
            "Bank (watchlist)",
            options=[""] + sorted(_wl),
            format_func=lambda t: f"{t} — {get_name(t)}" if t else "Select a bank…",
            key="company_bank",
        )
    with _pc2:
        st.text_input("Or enter any ticker", placeholder="e.g. JPM",
                      key="company_ticker_input")
    _ov2 = (st.session_state.get("company_ticker_input") or "").strip().upper()
    company_ticker = _ov2 or (st.session_state.get("company_bank") or None)

    if not company_ticker:
        st.info("👆 Pick a bank above (watchlist dropdown) or type any ticker to begin.")

    elif company_subtab == "Overview":
        # Use cached single-bank metrics for fast switching
        single = load_single_bank_metrics_cached(company_ticker)
        single_df = pd.DataFrame([single])
        render_bank_detail(company_ticker, single_df)

    elif company_subtab == "Activity":
        from ui.recent_activity import render_recent_activity
        render_recent_activity(company_ticker)

    elif company_subtab == "Financials":
        render_historicals(company_ticker)

    elif company_subtab == "Filings":
        render_filings_for_ticker(company_ticker)

    elif company_subtab == "Deposits":
        render_deposits_for_ticker(company_ticker)

    elif company_subtab == "Credit":
        from ui.credit_dynamics import render_credit_dynamics
        render_credit_dynamics(company_ticker, watchlist)

    elif company_subtab == "Capital":
        from ui.capital_dynamics import render_capital_dynamics
        render_capital_dynamics(company_ticker, watchlist)

    elif company_subtab == "NIM Sensitivity":
        from ui.rate_sensitivity import render_rate_sensitivity
        render_rate_sensitivity(company_ticker)

    elif company_subtab == "Valuation":
        from ui.valuation_model import render_valuation_model
        render_valuation_model(company_ticker)

    elif company_subtab == "Ownership":
        from ui.ownership import render_ownership
        render_ownership(company_ticker)

    elif company_subtab == "🔍 Data Quality":
        from ui.data_quality import render_data_quality
        render_data_quality(company_ticker)

    elif company_subtab == "Earnings":
        # Use cached single-bank metrics — fast switching between banks
        ticker_metrics = load_single_bank_metrics_cached(company_ticker)
        render_earnings_consensus(company_ticker, ticker_metrics)

elif section == "🌐 Macro":
    from ui.macro import render_macro_dashboard
    render_macro_dashboard()

elif section == "🆚 Peer Comparison":
    # ── PEER COMPARISON: Side-by-side bank comparison ───────────────────
    from ui.peer_comparison import render_peer_comparison
    if not all_metrics:
        all_metrics, metrics_df = load_all_data(watchlist)
        cache.put("watchlist_metrics_last", all_metrics)
    render_peer_comparison(all_metrics, watchlist, portfolio)

elif section == "📈 Earnings Analysis":
    # ── EARNINGS ANALYSIS: Aggregate tracking ───────────────────────────
    if not all_metrics:
        all_metrics, metrics_df = load_all_data(watchlist)
        cache.put("watchlist_metrics_last", all_metrics)
    render_earnings_overview(watchlist, all_metrics)

elif section == "📰 Activity":
    # ── ACTIVITY: Universe-wide event feed ──────────────────────────────
    from ui.recent_activity import render_activity_overview
    render_activity_overview()

elif section == "🗺️ Geographic":
    # ── GEOGRAPHIC: Multi-bank branch map + state/MSA deposit lookup ────
    from ui.geo_view import render_geo_view
    render_geo_view()


# ── Auto-refresh ─────────────────────────────────────────────────────────
if auto_refresh and st.session_state.ibkr_connected:
    time.sleep(PRICE_REFRESH_SECONDS)
    st.rerun()
