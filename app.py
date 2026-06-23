"""
Bank Valuation & Analysis Dashboard

A comprehensive, live-updating bank valuation screen using FDIC, SEC EDGAR,
and IBKR APIs. Built with Streamlit for company-wide sharing.
"""

import time
import streamlit as st
import pandas as pd

from config import (PRICE_REFRESH_SECONDS, TABS, METRICS, METRICS_BY_KEY,
                    TAB_META, THEME_ORDER)
from data.bank_mapping import get_fdic_cert, get_cik, get_name
from data.bank_universe import get_universe_tickers
from data import fdic_client, sec_client, cache
from data.ibkr_client import get_ibkr_client, get_empty_price
from analysis.metrics import build_all_bank_metrics
from ui.styles import CUSTOM_CSS
from ui.generic_table import render_generic_table
from ui.overview_table import render_data_freshness
from ui.earnings import render_earnings_overview
from ui.home import render_home

# ── Page config ──────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Bank Valuation Dashboard",
    page_icon=":material/account_balance:",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ── Session state initialization ─────────────────────────────────────────
if "ibkr_connected" not in st.session_state:
    st.session_state.ibkr_connected = False


# ── Level 1 Navigation: top nav bar (DESIGN-SYSTEM.md — sidebar retired) ──
SECTIONS = ["Home", "Market & Macro", "Screen & Compare", "Company", "Earnings", "News & Research", "Geographic"]
# Backward-compat for the old ?s= section key (renamed 2026-06-15). Old
# bookmarks/links to ?s=Activity should still land on the renamed section;
# the former top-level "Screening" and "Peers" are now sub-sections of
# "Screen & Compare", so their old deep-links resolve there too.
_SECTION_ALIASES = {"Activity": "News & Research",
                    "Screening": "Screen & Compare", "Peers": "Screen & Compare"}
# Company Analysis sub-tabs. Rendered horizontally at the TOP of the main
# content (under the bank picker), not in the sidebar. One shared template per
# bank — every bank gets the same sub-tabs.
# Two-level company navigation lives in ui/company_nav.py as data: the same
# registry drives the nav radios AND the dispatch, so a sub-tab cannot exist
# without a renderer (pinned by a test — the A17 bug class is impossible).
from ui.company_nav import (
    COMPANY_NAV, COMPANY_LEAVES, COMPANY_SECTION_OF, render_company_subtab,
    resolve_url_bank,
)
# A ?bank=X deep-link (e.g. a click on a Home movers/news row) should jump
# straight into Company Analysis. Set the section's session_state BEFORE the
# radio instantiates so it opens on the right page; the bank itself is picked
# up below from the same query param.
# Website-grade URL state: the address bar carries the view, so a browser
# refresh (or a shared link) restores exactly where you were instead of
# bouncing to Home. ?s=<section>; Company adds ?bank= and ?tab=.
_qs = st.query_params.get("s")
_qs = _SECTION_ALIASES.get(_qs, _qs)
if "nav_section" not in st.session_state and _qs in SECTIONS:
    st.session_state["nav_section"] = _qs
# A ?bank=X deep-link (Home movers/feed row, nav-search pick) jumps into Company
# by flipping nav_section before the radio instantiates. But it must fire ONLY on
# the rerun where the bank param actually ARRIVES — not on every rerun a bank is
# in the URL. Otherwise the bank param left over from a prior Company visit
# (it's stripped at L174, but only AFTER the radio renders) re-forces Company on
# the very rerun you click another tab, trapping you on the company page forever.
# Discriminator: a genuine deep-link is a bank param that CHANGED since last
# render (paired with ?s=Company, which every deep-link sets); a lingering one is
# unchanged → respect the radio's fresh section instead.
_bank_qs = st.query_params.get("bank")
if (_bank_qs and _bank_qs != st.session_state.get("_bank_qs_seen")
        and _qs in (None, "Company")
        and st.session_state.get("nav_section") != "Company"):
    st.session_state["nav_section"] = "Company"
st.session_state["_bank_qs_seen"] = _bank_qs
from utils.timing import timed

# Render the top nav FIRST — it depends only on SECTIONS, never on data, so
# it must ALWAYS paint immediately. (Regression 2026-06-13: get_universe_
# tickers() below can do a multi-minute cold-start build / per-ticker FDIC
# resolution; when it ran BEFORE the nav, the whole page — nav included —
# blocked behind it, leaving users staring at a blank "Stop" page for
# minutes with no way to navigate off Home. Streamlit streams widget deltas
# as they're produced, so creating the nav before any slow data call means
# the tabs appear within milliseconds regardless of load time below.)
from ui.chrome import top_nav as _top_nav
section, _nav_search, _nav_right = _top_nav(SECTIONS, key="nav_section")

# Universe scope AFTER the nav. Even when this is slow on a cold instance,
# the nav is already on screen. (The watchlist concept is retired; the
# variable keeps its name because ~30 downstream call sites take it as the
# scope parameter.)
@st.cache_data(ttl=1800, show_spinner=False)
def _universe_tickers_cached() -> list[str]:
    """get_universe_tickers() resolves every ticker against a Postgres-backed
    cert-active check — hundreds of DB round-trips, ~1.7s — yet the resolved
    list only changes when the nightly refresh-universe job rebuilds the
    snapshot. A Streamlit rerun re-executes this script top-to-bottom, so
    UNCACHED this ran on EVERY page navigation. Memoize it (30-min TTL) so the
    cost is paid once per instance, not per click. The snapshot is <26h fresh
    by design, so 30 min of staleness in membership is well within tolerance."""
    return sorted(get_universe_tickers())


with timed("app.universe_tickers"):
    watchlist = _universe_tickers_cached()

with _nav_right:
    _u1, _u2 = st.columns([3, 1.2], vertical_alignment="center")
    with _u1:
        st.markdown(
            f'<div style="text-align:right;font-size:var(--fs-2xs);color:var(--text-secondary);">'
            f'<span class="ksk-dot ok"></span>{len(watchlist)} banks · FDIC live</div>',
            unsafe_allow_html=True)
    with _u2:
        with st.popover("⋯", use_container_width=True):
            if st.button("Refresh this view", use_container_width=True, key="refresh_view"):
                # Targeted refresh: flag it; the handler below the cache
                # definitions clears just this page's data (no global nuke).
                st.session_state["_refresh_view_pending"] = section
                st.rerun()
            if st.button("Refresh all data", use_container_width=True, key="refresh_all"):
                st.cache_data.clear()
                cache.clear_all()
                st.rerun()
            auto_refresh = st.checkbox("Auto-refresh prices", value=False, key="auto_refresh")
            from data.ibkr_client import HAS_IBKR
            if HAS_IBKR:
                ibkr = get_ibkr_client()
                if not st.session_state.ibkr_connected:
                    if st.button("Connect to IBKR", key="ibkr_connect"):
                        with st.spinner("Connecting..."):
                            if ibkr.connect():
                                st.session_state.ibkr_connected = True
                                ibkr.start_event_loop()
                                st.success("Connected")
                            else:
                                st.error("Connection failed.")
                else:
                    st.markdown('<span class="ksk-dot ok"></span> IBKR connected', unsafe_allow_html=True)
                    if st.button("Disconnect", key="ibkr_disconnect"):
                        ibkr.disconnect()
                        st.session_state.ibkr_connected = False
                        st.rerun()
            else:
                st.session_state.ibkr_connected = False
auto_refresh = st.session_state.get("auto_refresh", False)

# ── Global bank search (nav bar) ─────────────────────────────────────────
# Jump to any covered bank's Company page by ticker OR name, from any section.
# Rendered into the nav's search column but AFTER `watchlist` loads, so the nav
# tabs still paint instantly on a cold instance. Reuses the Company page's
# company_pick mechanism (a distinct widget key avoids a collision with the
# in-page picker); on a pick it switches to Company and reruns. The
# _nav_search_last guard stops it re-navigating on every rerun once selected.
with _nav_search:
    _bank_labels = {}
    for _t in watchlist:
        _nm = get_name(_t)
        _bank_labels[_t] = f"{_t} — {_nm}" if (_nm and _nm != _t) else _t
    _picked = st.selectbox(
        "Find a bank", options=watchlist, index=None,
        format_func=lambda t: _bank_labels.get(t, t),
        placeholder="Search ticker or name…",
        label_visibility="collapsed", key="nav_bank_search")
    if _picked and _picked != st.session_state.get("_nav_search_last"):
        # Navigate via the URL (NOT st.session_state["nav_section"], which can't
        # be set after the nav radio is instantiated): the ?bank= handler near
        # the top of this script switches to Company and sets company_pick on the
        # rerun — the same path a Movers/feed row click uses.
        st.session_state["_nav_search_last"] = _picked
        st.query_params["s"] = "Company"
        st.query_params["bank"] = _picked
        st.rerun()

# Keep the URL in sync with the current section (no-op when unchanged).
if st.query_params.get("s") != section:
    st.query_params["s"] = section
if section != "Company":
    for _k in ("bank", "tab"):
        if _k in st.query_params:
            try:
                del st.query_params[_k]
            except Exception:
                pass

# ── Level 2 Navigation (contextual) ─────────────────────────────────────
screening_tab = None
company_ticker = None
company_subtab = None
sc_sub = None

# "Screen & Compare" hosts two sub-sections: Screen (the screening tables,
# formerly the top-level "Screening") and Compare (peer comparison, formerly
# "Peers"). A horizontal sub-nav picks between them; everything downstream keys
# off (section, sc_sub) so each page keeps its existing behavior.
if section == "Screen & Compare":
    # A "Compare these banks" click on a screen flags the switch; apply it here,
    # BEFORE the radio instantiates (a widget's session_state can't be written
    # once it exists).
    if st.session_state.pop("_goto_compare", False):
        st.session_state["sc_sub"] = "Compare"
    with st.container(key="sc_subnav"):
        sc_sub = st.radio("View", ["Screen", "Compare", "Trends"], key="sc_sub",
                          horizontal=True, label_visibility="collapsed")

    # Density pass for the whole Screen & Compare panel. Injected only on this
    # page's render, so it's effectively page-scoped (other sections never emit
    # it). Streamlit's default 1rem inter-element gap + full-width controls left
    # this view sprawling and hard to scan; tighten the vertical rhythm, thin the
    # collapsed expander "bars", and pull labels closer to their inputs so the
    # controls read as one compact toolbar instead of a tall stack of bands.
    st.markdown(
        """
        <style>
          div[data-testid="stVerticalBlock"]{gap:0.4rem;}
          /* Small, light, sentence-case control labels (NOT a grey header band).
             The element is a <label> with the text in a nested <p>. */
          [data-testid="stWidgetLabel"]{margin-bottom:2px;}
          [data-testid="stWidgetLabel"] p{font-size:0.7rem !important;
              letter-spacing:.01em;font-weight:500;color:var(--text-tertiary);
              line-height:1.1;}
          /* Compact selects — light bordered pills, smaller value text. */
          div[data-testid="stSelectbox"] div[data-baseweb="select"]>div{
              min-height:34px;font-size:0.8rem;}
          /* Sub-view nav (Screen · Compare · Trends) — navy underline tab bar. */
          .st-key-sc_subnav div[role="radiogroup"]{display:flex;gap:2px 6px;
              align-items:flex-end;border-bottom:1px solid var(--grid-head);
              margin-bottom:12px;}
          .st-key-sc_subnav div[role="radiogroup"]>label{margin:0 !important;
              padding:6px 16px;cursor:pointer;border-bottom:2px solid transparent;
              border-radius:6px 6px 0 0;transition:background .12s,border-color .12s;}
          .st-key-sc_subnav div[role="radiogroup"]>label:hover{background:var(--bg-hover);}
          .st-key-sc_subnav div[role="radiogroup"]>label>div:first-of-type{
              display:none !important;}
          .st-key-sc_subnav div[role="radiogroup"]>label p{font-size:0.95rem;
              color:var(--text-secondary);font-weight:500;}
          .st-key-sc_subnav div[role="radiogroup"]>label:has(input:checked){
              border-bottom-color:var(--brand-primary);}
          .st-key-sc_subnav div[role="radiogroup"]>label:has(input:checked) p{
              color:var(--brand-primary);font-weight:700;}
          /* Compact toolbar: selects sit left (a trailing spacer column keeps
             them content-width, not full-bleed); the small outline action buttons
             group under a thin divider. Single neutral tone — navy is reserved
             for the active tab and the primary CTA. */
          .st-key-screen_actbtns{border-top:0.5px solid var(--grid-head);
              margin-top:7px;padding-top:9px;}
          .st-key-screen_actbtns [data-testid="stHorizontalBlock"]{gap:6px !important;}
          .st-key-screen_scopesub div[data-baseweb="select"]{max-width:340px;}
          /* Compact buttons (toolbar, launcher, dialogs) — outline, navy hover. */
          [data-testid="stButton"] button, button[data-testid^="stBaseButton"]{
              min-height:30px;padding:0 12px;}
          [data-testid="stButton"] button:hover,
          button[data-testid^="stBaseButton"]:hover{
              border-color:var(--brand-border) !important;color:var(--brand-primary) !important;}
          [data-testid="stButton"] button p, button[data-testid^="stBaseButton"] p{
              font-size:0.72rem !important;}
          /* Primary CTA (Compare) — navy brand fill. */
          button[data-testid="stBaseButton-primary"]{
              background:var(--brand-primary) !important;
              border-color:var(--brand-primary) !important;}
          button[data-testid="stBaseButton-primary"]:hover{
              background:var(--brand-hover) !important;border-color:var(--brand-hover) !important;}
          button[data-testid="stBaseButton-primary"] p{color:var(--text-inverse) !important;}
          /* Compare's view tabs (st.tabs) — navy active to match the sub-nav. */
          [data-baseweb="tab-list"] button[aria-selected="true"],
          [data-baseweb="tab-list"] button[aria-selected="true"] p{
              color:var(--brand-primary) !important;}
          [data-baseweb="tab-highlight"]{background:var(--brand-primary) !important;}
          /* Thin expander headers (Compare sub-view still uses expanders). */
          div[data-testid="stExpander"] summary{padding-top:0.3rem;padding-bottom:0.3rem;}
          div[data-testid="stExpander"] details{border-radius:6px;}
        </style>
        """,
        unsafe_allow_html=True,
    )

# ── Screen launcher helpers (New / Open a saved screen) ─────────────────────
# A "screen" is a saved filter/sort/columns template. The Screen home is a
# launcher; a results table renders only INSIDE an open screen. These helpers
# write the toolbar's widget-backed session keys, so they MUST run before those
# widgets instantiate (i.e. in the resolution block below, never mid-render).
_SCREEN_FILTER_FMTS = ("pct", "ratio", "currency", "number", "millions",
                       "billions", "dollars_auto")


def _screen_filter_key_to_idx():
    """metric_key → filter-dropdown index. MIRRORS the toolbar's `filterable`
    derivation (kept identical so a restored filter lands on the right metric)."""
    fk = sorted([(m["key"], m["label"]) for m in METRICS
                 if m.get("format") in _SCREEN_FILTER_FMTS], key=lambda x: x[1])
    return {k: i + 1 for i, (k, _) in enumerate(fk)}


def _screen_clear_filters(ss, tk):
    """Reset tab `tk`'s filter + scope + sort widgets to a blank screen."""
    ss[f"num_filters_{tk}"] = 1
    for _fi in range(4):
        ss.pop(f"filt_metric_{tk}_{_fi}", None)
        ss.pop(f"filt_kind_{tk}_{_fi}", None)
    ss[f"screen_{tk}_scope_type"] = "All banks"
    ss[f"sort_{tk}"] = 0
    ss.pop(f"custom_cols_{tk}", None)


def _screen_switch_tab(ss, tk):
    """Point the single Table picker at the tab a saved screen was built from."""
    if any(t["key"] == tk for t in TABS):
        ss["screen_tab_key"] = tk


def _screen_restore_cfg(ss, cfg, tk):
    """Restore a saved screen `cfg` into tab `tk`'s widgets — filters by metric
    KEY (survives column/table changes; unknown keys are skipped, never
    mis-mapped onto the wrong metric)."""
    k2i = _screen_filter_key_to_idx()
    if cfg.get("sort_idx") is not None:
        ss[f"sort_{tk}"] = cfg["sort_idx"]
    if cfg.get("sort_order"):
        ss[f"order_{tk}"] = cfg["sort_order"]
    restored = 0
    for flt in cfg.get("filters", []):
        mk = flt.get("metric_key")
        if mk not in k2i:
            continue
        ss[f"filt_metric_{tk}_{restored}"] = k2i[mk]
        kind = flt.get("kind", "absolute")
        if kind == "peer_relative":
            ss[f"filt_kind_{tk}_{restored}"] = "Peer-relative"
            ss[f"filt_band_{tk}_{restored}"] = flt.get("band", "Top")
            ss[f"filt_pct_{tk}_{restored}"] = float(flt.get("pct", 25.0))
        elif kind == "change":
            ss[f"filt_kind_{tk}_{restored}"] = "Change"
            ss[f"filt_basis_{tk}_{restored}"] = flt.get("basis", "QoQ")
            ss[f"filt_chop_{tk}_{restored}"] = flt.get("op", ">")
            ss[f"filt_chval_{tk}_{restored}"] = flt.get("value", 0.0)
        elif kind == "trend":
            ss[f"filt_kind_{tk}_{restored}"] = "Trend"
            ss[f"filt_dir_{tk}_{restored}"] = (
                "Declining" if flt.get("direction") == "down" else "Rising")
            ss[f"filt_q_{tk}_{restored}"] = int(flt.get("quarters", 3))
        else:
            ss[f"filt_kind_{tk}_{restored}"] = "Absolute"
            ss[f"filt_op_{tk}_{restored}"] = flt.get("op", "<")
            ss[f"filt_val_{tk}_{restored}"] = flt.get("value", 0.0)
        restored += 1
    if restored:
        ss[f"num_filters_{tk}"] = min(restored, 4)
    if cfg.get("columns"):
        ss[f"custom_cols_{tk}"] = cfg["columns"]


if section == "Screen & Compare" and sc_sub == "Screen":
    # Single flat Table picker — Theme is folded in (only two themes), so tables
    # are listed grouped by theme order under one "Table" control. screen_tab_key
    # holds the chosen table key. Resolved up front (before any Screen widget) so
    # screening_tab — and the gated data load below — is known; the Table selectbox
    # in the control bar shares this exact key. First run falls back to the first
    # table.
    _ordered_keys = []
    for _th in THEME_ORDER:
        _ordered_keys += [t["key"] for t in TABS
                          if TAB_META.get(t["key"], ("", ""))[0] == _th]
    _ordered_keys += [t["key"] for t in TABS if t["key"] not in _ordered_keys]
    _cur_key = st.session_state.get("screen_tab_key")
    if _cur_key not in _ordered_keys:
        _cur_key = _ordered_keys[0]
    screening_tab = next(t for t in TABS if t["key"] == _cur_key)

    # Launcher actions (New / Open / Close) set by a button last run. Applied
    # HERE — before any Screen widget instantiates — so writing widget-backed
    # keys (table/sort/filters/scope) is legal this run. "Open" may switch the
    # active Table, so re-resolve screening_tab afterward.
    _sa = st.session_state.pop("_screen_action", None)
    if _sa:
        _act = _sa.get("act")
        if _act == "close":
            st.session_state["_screen_open"] = False
        elif _act == "new":
            _screen_clear_filters(st.session_state, screening_tab["key"])
            st.session_state["_screen_open"] = True
        elif _act == "open":
            from data.saved_screens import load_screen as _ls
            _cfg = _ls(_sa.get("name"), _sa.get("version"))
            if _cfg:
                _tk = _cfg.get("tab_key") or _sa.get("tab") or screening_tab["key"]
                _screen_switch_tab(st.session_state, _tk)
                _screen_clear_filters(st.session_state, _tk)
                _screen_restore_cfg(st.session_state, _cfg, _tk)
                st.session_state["_screen_open"] = True
        # Re-resolve the active table in case Open switched it.
        _cur_key = st.session_state.get("screen_tab_key")
        if _cur_key not in _ordered_keys:
            _cur_key = _ordered_keys[0]
        screening_tab = next(t for t in TABS if t["key"] == _cur_key)

    # Quick "add a bank by ticker" → accumulate the pick into the Manual scope so
    # the chosen banks populate the table immediately. Handled HERE (before the
    # scope/manual widgets render) so writing their session keys is legal; the
    # search box is then reset to empty for the next add.
    _addbank = st.session_state.get("screen_addbank")
    if _addbank:
        _mk = f"screen_{screening_tab['key']}_manual"
        _cur_manual = list(st.session_state.get(_mk, []))
        if _addbank not in _cur_manual:
            _cur_manual.append(_addbank)
        st.session_state[_mk] = _cur_manual
        st.session_state[f"screen_{screening_tab['key']}_scope_type"] = "Manual"
        st.session_state["screen_addbank"] = None   # reset the search box
        st.session_state["_screen_open"] = True

elif section == "Company":
    # Deep-link support: a metric card can link to ?bank=X&tab=<token> to jump
    # straight to the tab that shows that figure (carries the bank so the deep
    # link survives a full page navigation).
    _qp = st.query_params
    # The URL's ?bank= overrides the picker ONLY on external navigation (a
    # deep-link click, a shared link, a refresh) — detected as "URL names a
    # bank other than the one we last applied". On a plain widget-driven rerun
    # the URL is still the OLD bank (it's synced from the widget only after the
    # picker renders, below), so forcing the picker to it here would revert the
    # user's new pick every rerun and freeze the dropdown (2026-06-14 bug).
    _url_bank = (_qp.get("bank") or "").strip().upper() or None
    _force_bank = resolve_url_bank(_url_bank, st.session_state.get("_applied_url_bank"))
    if _force_bank:
        st.session_state["company_pick"] = _force_bank
        st.session_state["_applied_url_bank"] = _force_bank

    # A single search box (rendered in the main content) holds the selection.
    company_ticker = (st.session_state.get("company_pick") or "").strip().upper() or None

    if company_ticker:
        # Deep-link ?tab=<token> pre-selects the sub-tab. The sub-tab radio
        # itself renders in the MAIN content area (top of the page), so we only
        # pre-set its session_state value here; the widget reads it there.
        _TAB_TOKENS = {"financials": "Financial Highlights", "valuation": "Valuation Model",
                       "filings": "Filings & Reports", "peer": "Peer Rank",
                       "ownership": "Institutional (13F)", "earnings": "Earnings",
                       "deposits": "Deposit Trends"}
        _raw_tab = _qp.get("tab") or ""
        _goto = _TAB_TOKENS.get(_raw_tab.lower()) or (
            _raw_tab if _raw_tab in COMPANY_SECTION_OF else None)
        # Only pre-seed widget state on a fresh session (a reload/shared link);
        # once the widgets exist they own the state and we sync URL <- widgets.
        if _goto and "company_section" not in st.session_state:
            _sec = COMPANY_SECTION_OF[_goto]
            st.session_state["company_section"] = _sec
            _sec_nav = COMPANY_NAV[_sec]
            if isinstance(_sec_nav, dict):
                # Financials carries a basis layer. Honor an explicit ?basis= when
                # valid (shareable Company-Reported links); else pick the basis
                # that contains the target leaf, defaulting to the first.
                _url_basis = _qp.get("basis")
                _basis = (_url_basis if _url_basis in _sec_nav else
                          next((b for b, leaves in _sec_nav.items() if _goto in leaves),
                               next(iter(_sec_nav))))
                st.session_state[f"company_basis::{_sec}"] = _basis
                st.session_state[f"company_subtab::{_sec}::{_basis}"] = _goto
            else:
                st.session_state[f"company_subtab::{_sec}"] = _goto



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

    # Treat very stale warm rows as refresh candidates for watchlist-sized
    # sets, so the Home never shows day-old moves as if they were live. The
    # full-universe screen (~400 tickers) tolerates the warm cache to avoid a
    # huge live fan-out; its freshness is the refresh-prices job's job.
    if len(tickers) <= 120:
        stale_after = 6 * 3600
        stale = [t for t, q in out.items()
                 if (q or {}).get("age_seconds") is not None
                 and q["age_seconds"] > stale_after]
        missing = list({*missing, *stale})

    # 3. Live FMP for anything not warm-cached or gone stale. Only overwrite a
    #    warm row when the live call actually returns a price — otherwise keep
    #    the (stale-but-real) cached value rather than blanking it.
    if missing:
        try:
            from data.fmp_client import get_quote_batch, _has_key
            if _has_key():
                fresh = get_quote_batch(missing)
                out.update({t: q for t, q in fresh.items()
                            if q and q.get("price") is not None})
        except Exception as e:
            print(f"[prices] FMP fallback failed: {type(e).__name__}: {e}")

    # 4. Fill any remaining gaps with empty quotes.
    return {t: out.get(t) or get_empty_price() for t in tickers}


@st.cache_data(ttl=900, show_spinner=False)
def _load_all_data_cached(tickers: tuple) -> list[dict]:
    """Cached core — caches the price fetch + metric computation for the whole
    watchlist (the prior version re-fetched 62 banks' prices and recomputed every
    metric on every page load, ~15-20s). Keyed on the ticker tuple so it's a hit
    across reruns. 15-min TTL keeps the Home aggregates reasonably fresh."""
    fdic, hist = load_fdic_data(tickers)
    sec = load_sec_data(tickers)
    prices = load_prices(list(tickers))
    return build_all_bank_metrics(list(tickers), fdic, sec, prices, hist)


def load_all_data(tickers: list[str]) -> list[dict]:
    return _load_all_data_cached(tuple(tickers))


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
    prices = load_prices([ticker])
    metrics = build_all_bank_metrics([ticker], fdic, sec, prices, hist)
    return metrics[0] if metrics else {"ticker": ticker}


@st.cache_data(ttl=3600, show_spinner=False)
def _screen_metric_series(ticker: str, metric_keys: tuple, n_quarters: int) -> dict:
    """Cached per-quarter metric history for the change/trend screen primitives
    (one entry per bank). Recomputed through the real engine — see
    analysis/metric_history.py."""
    from analysis.metric_history import metric_series
    return metric_series(ticker, list(metric_keys), n_quarters)


def get_watchlist_cohort() -> list[dict]:
    """Peer cohort = watchlist metrics. Returns the cached set, loading it on
    first use so peer ranking (the Peer Rank tab) is reliably available even if
    the user hasn't visited Home / Screening / Peer Comparison this session.
    Centralizes what was an inline load in the Peer Rank dispatch."""
    cached = cache.get("watchlist_metrics_last")
    if cached:
        return cached
    metrics = load_all_data(watchlist)
    cache.put("watchlist_metrics_last", metrics)
    return metrics


# Cross-instance aggregate snapshot. The in-process @st.cache_data memo dies
# with every deploy's new instance, and rebuilding the full-universe metrics
# inline took 60s+ on each cold start — on a heavy deploy day that was nearly
# every page load. Cold instances now serve the persisted snapshot instantly
# and only rebuild when it's genuinely stale; per-section as-of badges (prices,
# rates) keep the freshness honest on screen.
_METRICS_SNAP_KEY = "watchlist_metrics_snap"
_METRICS_SNAP_TTL_S = 6 * 3600


def load_all_data_fast(tickers: list[str]) -> list[dict]:
    from data.freshness import is_fresh
    snap = None
    try:
        snap = cache.get(_METRICS_SNAP_KEY)
    except Exception:
        snap = None
    if (snap and is_fresh(snap, _METRICS_SNAP_TTL_S)
            and snap.get("n_tickers") == len(tickers)):
        return snap["metrics"]
    metrics = load_all_data(tickers)
    try:
        from datetime import datetime
        cache.put(_METRICS_SNAP_KEY, {
            "cached_at": datetime.now().isoformat(),
            "n_tickers": len(tickers),
            "metrics": metrics,
        })
    except Exception as e:
        print(f"[app] could not persist metrics snapshot: {type(e).__name__}")
    return metrics


# ── Lazy data loading ─────────────────────────────────────────────────
# Only load watchlist metrics if actually needed by the current view.
# This avoids a 5-15 second wait when switching to "All Banks" screening
# or opening the Company Analysis page for a single bank.

_NEEDS_WATCHLIST = (
    section == "Home"  # Home shows top opportunities from watchlist
    or (section == "Screen & Compare" and sc_sub == "Screen" and screening_tab is not None)
    or (section == "Screen & Compare" and sc_sub == "Compare")  # peer comparison needs all metrics
    or (section == "Screen & Compare" and sc_sub == "Trends")   # scope picker + metric list
)

# Deferred "Refresh this view" handler (set in the nav utilities popover,
# processed here where the cached loaders exist).
_rv = st.session_state.pop("_refresh_view_pending", None)
if _rv == "Company":
    _t = (st.session_state.get("company_pick") or "").strip().upper()
    load_single_bank_metrics_cached.clear()
    if _t:
        for _ck in (f"sec:{_t}", f"fdic:{_t}", f"fdic_hist:{_t}"):
            try:
                cache.invalidate(_ck)
            except Exception:
                pass
elif _rv:
    _load_all_data_cached.clear()
    try:
        cache.invalidate(_METRICS_SNAP_KEY)
    except Exception:
        pass

if _NEEDS_WATCHLIST:
    with timed("app.load_all_data_fast"):
        all_metrics = load_all_data_fast(watchlist)
    # Stash for cross-tab use (peer-relative valuation, home alerts, etc.)
    cache.put("watchlist_metrics_last", all_metrics)
else:
    all_metrics = []


# ═══════════════════════════════════════════════════════════════════════════
# MAIN CONTENT AREA
# ═══════════════════════════════════════════════════════════════════════════

if section == "Home":
    render_home(all_metrics, watchlist)

elif section == "Screen & Compare" and sc_sub == "Screen" and screening_tab:
    # ── SCREEN: Multi-bank comparison tables ────────────────────────────
    from data.saved_screens import (
        save_screen, load_screen, list_screens, delete_screen, screen_versions,
    )

    tab_key = screening_tab["key"]
    tab_columns = screening_tab["columns"]

    from ui.bank_scope import scope_type_options, render_scope_sub
    from data.bank_groups import save_group

    # Filterable metrics span the ENTIRE metric set (not just this table's
    # columns) so you can screen on, say, CET1 and P/TBV from any table. Built
    # once here because it no longer depends on the active tab.
    _FILT_FORMATS = ("pct", "ratio", "currency", "number", "millions",
                     "billions", "dollars_auto")
    filterable = sorted(
        [(m["key"], m["label"]) for m in METRICS if m.get("format") in _FILT_FORMATS],
        key=lambda x: x[1],
    )
    filter_labels = ["—"] + [lbl for _, lbl in filterable]
    filter_keys = [None] + [k for k, _ in filterable]
    filter_key_to_idx = {k: i + 1 for i, (k, _) in enumerate(filterable)}

    def _filter_specs_from_state():
        """Rebuild active filter specs from session_state every run, so filters
        apply even while the Metric Filters dialog is closed (a dialog body only
        executes while open). Mirrors the widgets in _filters_dialog — same keys.
        session_state is committed before each rerun, so this always reflects the
        latest edits made inside the dialog."""
        ss = st.session_state
        n = ss.get(f"num_filters_{tab_key}", 1)
        specs = []
        for fi in range(n):
            midx = ss.get(f"filt_metric_{tab_key}_{fi}", 0)
            mkey = (filter_keys[midx]
                    if isinstance(midx, int) and 0 < midx < len(filter_keys) else None)
            if mkey is None:
                continue
            kind = ss.get(f"filt_kind_{tab_key}_{fi}", "Absolute")
            if kind == "Peer-relative":
                specs.append({"kind": "peer_relative", "metric": mkey,
                              "band": ss.get(f"filt_band_{tab_key}_{fi}", "Top"),
                              "pct": ss.get(f"filt_pct_{tab_key}_{fi}", 25.0)})
            elif kind == "Change":
                specs.append({"kind": "change", "metric": mkey,
                              "basis": ss.get(f"filt_basis_{tab_key}_{fi}", "QoQ"),
                              "op": ss.get(f"filt_chop_{tab_key}_{fi}", "<"),
                              "value": ss.get(f"filt_chval_{tab_key}_{fi}", 0.0)})
            elif kind == "Trend":
                specs.append({"kind": "trend", "metric": mkey,
                              "direction": ("down" if ss.get(f"filt_dir_{tab_key}_{fi}",
                                            "Declining") == "Declining" else "up"),
                              "quarters": int(ss.get(f"filt_q_{tab_key}_{fi}", 3))})
            else:
                specs.append({"kind": "absolute", "metric": mkey,
                              "op": ss.get(f"filt_op_{tab_key}_{fi}", "<"),
                              "value": ss.get(f"filt_val_{tab_key}_{fi}", 0.0)})
        return specs

    # ── Saved screens dialog ───────────────────────────────────────────
    # Saved screens capture the *how* (filters / sort / columns); the *who*
    # (bank scope) is chosen live and persisted separately as a Bank Group.
    # Opened from a toolbar button below; load/save/delete each st.rerun() so the
    # toolbar controls (rendered above) pick up the restored state next run.
    @st.dialog("Saved screens", width="large")
    def _saved_dialog():
        saved = list_screens()
        saved_for_tab = [s for s in saved if s.get("tab") == tab_key]

        load_col, del_col, new_col = st.columns([2, 1, 2])

        with load_col:
            ver_by_name = {s["name"]: s.get("version", 1) for s in saved_for_tab}
            load_options = ["— select —"] + [s["name"] for s in saved_for_tab]
            load_choice = st.selectbox(
                f"Load saved screen ({len(saved_for_tab)} available for this tab)",
                load_options,
                format_func=lambda n: (f"{n}  (v{ver_by_name[n]})" if n in ver_by_name else n),
                key=f"load_screen_{tab_key}",
            )
            load_version = None
            if load_choice != "— select —":
                # Versioned templates: offer the current version + any prior
                # revisions to roll back to.
                versions = screen_versions(load_choice)
                if len(versions) > 1:
                    _vlabel = {v["version"]: (f"v{v['version']} · {v['saved_at'][:10]}"
                                              + (" (current)" if v["current"] else ""))
                               for v in versions}
                    load_version = st.selectbox(
                        "Version", [v["version"] for v in versions],
                        format_func=lambda x: _vlabel.get(x, f"v{x}"),
                        key=f"load_ver_{tab_key}",
                    )
                if st.button(f"Load '{load_choice}'", key=f"load_btn_{tab_key}"):
                    cfg = load_screen(load_choice, load_version)
                    if cfg:
                        ss = st.session_state
                        if cfg.get("sort_idx") is not None:
                            ss[f"sort_{tab_key}"] = cfg["sort_idx"]
                        if cfg.get("sort_order"):
                            ss[f"order_{tab_key}"] = cfg["sort_order"]
                        # Restore filters by metric KEY (stable across tables). An
                        # old index-only save, or a key not in the current metric
                        # set, is skipped — never restored to the wrong metric.
                        restored = 0
                        for flt in cfg.get("filters", []):
                            mk = flt.get("metric_key")
                            if mk not in filter_key_to_idx:
                                continue
                            ss[f"filt_metric_{tab_key}_{restored}"] = filter_key_to_idx[mk]
                            # kind defaults to absolute so pre-kind saves restore
                            # as threshold filters.
                            kind = flt.get("kind", "absolute")
                            if kind == "peer_relative":
                                ss[f"filt_kind_{tab_key}_{restored}"] = "Peer-relative"
                                ss[f"filt_band_{tab_key}_{restored}"] = flt.get("band", "Top")
                                ss[f"filt_pct_{tab_key}_{restored}"] = float(flt.get("pct", 25.0))
                            elif kind == "change":
                                ss[f"filt_kind_{tab_key}_{restored}"] = "Change"
                                ss[f"filt_basis_{tab_key}_{restored}"] = flt.get("basis", "QoQ")
                                ss[f"filt_chop_{tab_key}_{restored}"] = flt.get("op", ">")
                                ss[f"filt_chval_{tab_key}_{restored}"] = flt.get("value", 0.0)
                            elif kind == "trend":
                                ss[f"filt_kind_{tab_key}_{restored}"] = "Trend"
                                ss[f"filt_dir_{tab_key}_{restored}"] = (
                                    "Declining" if flt.get("direction") == "down" else "Rising")
                                ss[f"filt_q_{tab_key}_{restored}"] = int(flt.get("quarters", 3))
                            else:
                                ss[f"filt_kind_{tab_key}_{restored}"] = "Absolute"
                                ss[f"filt_op_{tab_key}_{restored}"] = flt.get("op", "<")
                                ss[f"filt_val_{tab_key}_{restored}"] = flt.get("value", 0.0)
                            restored += 1
                        if restored:
                            ss[f"num_filters_{tab_key}"] = min(restored, 4)
                        ss[f"custom_cols_{tab_key}"] = cfg.get("columns") or tab_columns
                        st.rerun()

        with del_col:
            if load_choice != "— select —":
                if st.button("Delete", key=f"del_btn_{tab_key}"):
                    delete_screen(load_choice)
                    st.success(f"Deleted '{load_choice}'")
                    st.rerun()

        with new_col:
            with st.form(f"save_form_{tab_key}", clear_on_submit=True):
                new_name = st.text_input("Save current as…",
                                          placeholder="e.g. Value CRE Overweight",
                                          key=f"new_screen_name_{tab_key}")
                if st.form_submit_button("Save Screen") and new_name:
                    ss = st.session_state
                    num_filt = ss.get(f"num_filters_{tab_key}", 1)
                    filters = []
                    for i in range(num_filt):
                        mi = ss.get(f"filt_metric_{tab_key}_{i}", 0)
                        mk = filter_keys[mi] if 0 < mi < len(filter_keys) else None
                        if mk is None:
                            continue
                        fkind_s = ss.get(f"filt_kind_{tab_key}_{i}", "Absolute")
                        if fkind_s == "Peer-relative":
                            filters.append({
                                "kind": "peer_relative", "metric_key": mk,
                                "band": ss.get(f"filt_band_{tab_key}_{i}", "Top"),
                                "pct": ss.get(f"filt_pct_{tab_key}_{i}", 25.0),
                            })
                        elif fkind_s == "Change":
                            filters.append({
                                "kind": "change", "metric_key": mk,
                                "basis": ss.get(f"filt_basis_{tab_key}_{i}", "QoQ"),
                                "op": ss.get(f"filt_chop_{tab_key}_{i}", ">"),
                                "value": ss.get(f"filt_chval_{tab_key}_{i}", 0.0),
                            })
                        elif fkind_s == "Trend":
                            filters.append({
                                "kind": "trend", "metric_key": mk,
                                "direction": ("down" if ss.get(f"filt_dir_{tab_key}_{i}") == "Declining"
                                              else "up"),
                                "quarters": ss.get(f"filt_q_{tab_key}_{i}", 3),
                            })
                        else:
                            filters.append({
                                "kind": "absolute", "metric_key": mk,
                                "op": ss.get(f"filt_op_{tab_key}_{i}", "<"),
                                "value": ss.get(f"filt_val_{tab_key}_{i}", 0.0),
                            })
                    cfg = {
                        "tab_key": tab_key,
                        "sort_idx": ss.get(f"sort_{tab_key}", 0),
                        "sort_order": ss.get(f"order_{tab_key}", "Desc"),
                        "num_filters": num_filt,
                        "filters": filters,
                        "columns": ss.get(f"custom_cols_{tab_key}") or tab_columns,
                    }
                    save_screen(new_name, cfg)
                    _vs = screen_versions(new_name)
                    _v = _vs[0]["version"] if _vs else 1
                    st.success(f"Saved '{new_name}' (v{_v})")
                    st.rerun()

        st.divider()
        with st.expander("Bank groups — save the result set or manage saved groups"):
            _render_groups_panel()

    # ── Controls row: As of · Scope · Sort · Order ─────────────────────
    # One dense control row (was a stacked As-of band ABOVE a separate
    # Scope/Sort/Order row). As-of reconstructs the universe as it FILED at a
    # past quarter-end (FDIC point-in-time: includes since-failed/acquired banks
    # for the quarters they filed, e.g. SVB in Q4-2022; market & SEC metrics are
    # n/a then, never guessed) and feeds the scope selector. The rest are
    # independent. Sort options are built before the row so c_sort can use them.
    from data.as_of_metrics import (recent_quarter_ends, quarter_label,
                                     as_of_quarter_metrics)
    from data.entity_graph import KNOWN_PUBLIC_FAILURES, lineage_predecessors
    _qs_list = recent_quarter_ends(20)   # ~5 years, enough to reach the 2023 failures
    _asof_opts = ["Latest (live)"] + [quarter_label(q) for q in _qs_list]

    sort_labels = ["Default"]
    sort_keys = [None]
    for col_key in tab_columns:
        m = METRICS_BY_KEY.get(col_key)
        if m:
            sort_labels.append(m["label"])
            sort_keys.append(col_key)

    # ── Screen HOME (launcher) ─────────────────────────────────────────
    # No screen open → show ONLY the launcher and STOP, BEFORE the toolbar renders.
    # Starting a screen is the required first step, so the Theme/Table/As-of/Scope/
    # Sort/Order controls don't appear until you're in one. New screen → a blank
    # screen; or open / delete a saved one.
    if not st.session_state.get("_screen_open"):
        st.markdown("")
        _lc1, _lc2 = st.columns([1.5, 4])
        with _lc1:
            if st.button("➕  New screen", type="primary", use_container_width=True,
                         key="screen_new_btn"):
                st.session_state["_screen_action"] = {"act": "new"}
                st.rerun()
            st.caption("Start a blank screen, then pick the table, filters and scope.")
        with _lc2:
            _saved = list_screens()
            if not _saved:
                st.caption("No saved screens yet. Start one with **New screen**, set "
                           "filters, then **Save** it from the action bar.")
            else:
                st.markdown("**Saved screens**")
                _lbl_of = {t["key"]: t["label"] for t in TABS}
                for _s in _saved:
                    _oc, _mc, _dc = st.columns([2.4, 3, 0.7])
                    with _oc:
                        if st.button(_s["name"], key=f"open_{_s['filename']}",
                                     use_container_width=True):
                            st.session_state["_screen_action"] = {
                                "act": "open", "name": _s["name"],
                                "tab": _s.get("tab")}
                            st.rerun()
                    with _mc:
                        _tablab = _lbl_of.get(_s.get("tab"), _s.get("tab") or "—")
                        _fc = _s.get("filter_count", 0)
                        st.caption(f"{_tablab} · {_fc} filter"
                                   f"{'' if _fc == 1 else 's'} · v{_s.get('version', 1)}")
                    with _dc:
                        if st.button("✕", key=f"del_{_s['filename']}",
                                     help=f"Delete '{_s['name']}'"):
                            delete_screen(_s["name"])
                            st.rerun()
        st.stop()

    # ── Compact toolbar — configurator selects (left-aligned, content-width) ──
    # Plain columns with a trailing SPACER column keep the selects content-width
    # instead of stretching edge-to-edge. Theme is folded into Table. The action
    # buttons render below under a thin divider (screen_actbtns).
    c_table, c_asof, c_scope, c_sort, c_order, c_add, _c_spacer = st.columns(
        [2.3, 1.3, 1.4, 1.2, 0.8, 2.3, 1.4])
    with c_table:
        # Theme folded in: one flat Table picker over all tables (ordered by
        # theme). screen_tab_key holds the chosen key (resolved up top).
        st.selectbox(
            "Table", options=_ordered_keys,
            format_func=lambda k: next(t["label"] for t in TABS if t["key"] == k),
            key="screen_tab_key")
    with c_asof:
        _asof_pick = st.selectbox(
            "As of", _asof_opts, key=f"asof_{tab_key}",
            help="Screen the universe as it filed at a past quarter-end (FDIC "
                 "point-in-time; market/SEC metrics are n/a in this mode).")
    is_asof = _asof_pick != "Latest (live)"
    asof_q_label = _asof_pick if is_asof else ""
    if is_asof:
        _q = _qs_list[_asof_opts.index(_asof_pick) - 1]
        _cand = {get_fdic_cert(t): t for t in watchlist if get_fdic_cert(t)}
        _company_certs = set(_cand)   # current public banks WITH a Company page
        for _c, _nm in KNOWN_PUBLIC_FAILURES.items():
            _cand.setdefault(_c, _nm)
        with st.spinner(f"Reconstructing the universe as of {_asof_pick}… "
                        "(first load fetches a few years of FDIC history; then cached)"):
            # Lineage: banks since absorbed by a current bank were separate at Q —
            # re-add them (the as-of builder still gates each on filing at Q).
            for _c, _info in lineage_predecessors(set(_cand), _q).items():
                _cand.setdefault(_c, _info.get("name") or f"CERT:{_c}")
            screen_metrics = as_of_quarter_metrics(_q, _cand)
        # Tag rows with no current Company page (failures / since-acquired) so the
        # table links them to FDIC BankFind instead of a dead ?bank= link.
        for _m in screen_metrics:
            _m["_defunct"] = _m.get("_fdic_cert") not in _company_certs
        if not screen_metrics:
            st.warning(f"No FDIC filings reconstructed for {_asof_pick}.")
    else:
        screen_metrics = all_metrics
    with c_scope:
        _scope_type = st.selectbox("Scope", scope_type_options(),
                                   key=f"screen_{tab_key}_scope_type")
    with c_sort:
        sort_idx = st.selectbox(
            "Sort", options=list(range(len(sort_labels))),
            format_func=lambda i: sort_labels[i], key=f"sort_{tab_key}")
    with c_order:
        sort_order = st.selectbox("Order", options=["Desc", "Asc"],
                                  key=f"order_{tab_key}")
    with c_add:
        # Quick add: search a ticker/name → it's appended to the Manual scope and
        # the table populates (handled in the resolution block above on rerun).
        st.selectbox(
            "Add bank", options=sorted(watchlist), index=None,
            placeholder="ticker or name…",
            format_func=lambda t: (f"{t} — {get_name(t)}"
                                   if get_name(t) and get_name(t) != t else t),
            key="screen_addbank")
    # Scope's secondary picker — only when the type needs one; narrow, below the
    # selects. For "All banks" render_scope_sub draws nothing (no empty band).
    if _scope_type == "All banks":
        display_metrics, display_tickers, scope_label = render_scope_sub(
            screen_metrics, _scope_type, key_prefix=f"screen_{tab_key}")
    else:
        with st.container(key="screen_scopesub"):
            _ssub, _ = st.columns([3, 6])
            with _ssub:
                display_metrics, display_tickers, scope_label = render_scope_sub(
                    screen_metrics, _scope_type, key_prefix=f"screen_{tab_key}")

    # ── Metric filters dialog (any metric; AND-combined) ───────────────
    from analysis.screen_engine import evaluate as _evaluate_screen

    @st.dialog("Metric filters", width="large")
    def _filters_dialog():
        # Local list is discarded; the specs actually applied are rebuilt from
        # session_state below (_filter_specs_from_state) so they hold when closed.
        filter_specs = []
        st.caption("Filter on any metric, AND-combined. **Absolute** = value vs a "
                   "threshold · **Peer-relative** = Top/Bottom % by value within the "
                   "current scope · **Change** = QoQ/YoY move · **Trend** = N consecutive "
                   "quarters one way. A bank with no value (or too little history) for a "
                   "filter is excluded as no-data, never counted as failing.")
        num_filters = st.selectbox(
            "Number of filters",
            options=[1, 2, 3, 4],
            key=f"num_filters_{tab_key}",
        )

        for fi in range(num_filters):
            kc, mc, c3, c4, c5 = st.columns([1.5, 2.6, 1.3, 1.3, 1.3])
            lblvis = "collapsed" if fi > 0 else "visible"
            with kc:
                fkind = st.selectbox(
                    "Type", ["Absolute", "Peer-relative", "Change", "Trend"],
                    key=f"filt_kind_{tab_key}_{fi}", label_visibility=lblvis,
                )
            with mc:
                filt_idx = st.selectbox(
                    "Metric",
                    options=list(range(len(filter_labels))),
                    format_func=lambda i, fl=filter_labels: fl[i],
                    key=f"filt_metric_{tab_key}_{fi}", label_visibility=lblvis,
                )
            filt_key = filter_keys[filt_idx] if filt_idx > 0 else None

            if fkind == "Peer-relative":
                with c3:
                    fband = st.selectbox("Band", ["Top", "Bottom"],
                        key=f"filt_band_{tab_key}_{fi}", label_visibility=lblvis)
                with c4:
                    fpct = st.number_input("Pct %", value=25.0, min_value=1.0,
                        max_value=99.0, step=5.0, format="%.0f",
                        key=f"filt_pct_{tab_key}_{fi}", label_visibility=lblvis)
                if filt_key is not None:
                    filter_specs.append({"kind": "peer_relative", "metric": filt_key,
                                         "band": fband, "pct": fpct})
            elif fkind == "Change":
                with c3:
                    fbasis = st.selectbox("Basis", ["QoQ", "YoY"],
                        key=f"filt_basis_{tab_key}_{fi}", label_visibility=lblvis)
                with c4:
                    fchop = st.selectbox("Op", ["<", "≤", ">", "≥", "="],
                        key=f"filt_chop_{tab_key}_{fi}", label_visibility=lblvis)
                with c5:
                    fchval = st.number_input("Δ", value=0.0, step=0.1, format="%.2f",
                        key=f"filt_chval_{tab_key}_{fi}", label_visibility=lblvis)
                if filt_key is not None:
                    filter_specs.append({"kind": "change", "metric": filt_key,
                                         "basis": fbasis, "op": fchop, "value": fchval})
            elif fkind == "Trend":
                with c3:
                    fdir = st.selectbox("Direction", ["Declining", "Rising"],
                        key=f"filt_dir_{tab_key}_{fi}", label_visibility=lblvis)
                with c4:
                    fq = st.selectbox("Quarters", [2, 3, 4],
                        key=f"filt_q_{tab_key}_{fi}", label_visibility=lblvis)
                if filt_key is not None:
                    filter_specs.append({"kind": "trend", "metric": filt_key,
                                         "direction": "down" if fdir == "Declining" else "up",
                                         "quarters": fq})
            else:  # Absolute
                with c3:
                    fop = st.selectbox("Op", ["<", "≤", ">", "≥", "="],
                        key=f"filt_op_{tab_key}_{fi}", label_visibility=lblvis)
                with c4:
                    fval = st.number_input("Value", value=0.0, step=0.1, format="%.2f",
                        key=f"filt_val_{tab_key}_{fi}", label_visibility=lblvis)
                if filt_key is not None:
                    filter_specs.append({"kind": "absolute", "metric": filt_key,
                                         "op": fop, "value": fval})

    # Live specs (independent of whether the dialog is open) drive the table.
    filter_specs = _filter_specs_from_state()

    # A screen is "engaged" once it has a filter or a narrowed scope. A brand-new
    # blank screen (no filter, All banks) shows a prompt instead of dumping the
    # full universe — results appear as soon as you add a filter or pick a scope.
    _engaged = bool(filter_specs) or (_scope_type != "All banks")

    # Apply via the screening engine — it excludes no-data banks (counted) rather
    # than silently scoring them as failures (cardinal rule). Change/trend specs
    # need per-quarter history, computed lazily per bank and cached.
    n_excluded_nodata = 0
    if filter_specs and display_metrics:
        ct_specs = [s for s in filter_specs if s["kind"] in ("change", "trend")]
        if ct_specs:
            ct_metrics = tuple(sorted({s["metric"] for s in ct_specs}))
            max_lb = max(
                [4 if s.get("basis") == "YoY" else 1 for s in ct_specs if s["kind"] == "change"]
                + [int(s.get("quarters", 3)) for s in ct_specs if s["kind"] == "trend"] + [1])

            def _hist_provider(tk, _m=ct_metrics, _n=max_lb):
                return _screen_metric_series(tk, _m, _n)

            with st.spinner("Computing quarterly history for change/trend filters…"):
                display_metrics, n_excluded_nodata = _evaluate_screen(
                    display_metrics, filter_specs, _hist_provider)
        else:
            display_metrics, n_excluded_nodata = _evaluate_screen(
                display_metrics, filter_specs)

    # Apply sorting
    sort_key = sort_keys[sort_idx] if sort_idx > 0 else None
    if sort_key and display_metrics:
        ascending = sort_order == "Asc"
        display_metrics = sorted(
            display_metrics,
            key=lambda m: (m.get(sort_key) is None, m.get(sort_key) or 0),
            reverse=not ascending,
        )

    # Customized columns (computed here so the header's column count matches
    # what actually renders below).
    display_cols_final = st.session_state.get(f"custom_cols_{tab_key}") or tab_columns
    if not display_cols_final:
        display_cols_final = tab_columns
    scope_slug = ("".join(c if c.isalnum() else "_"
                          for c in scope_label.lower())[:30].strip("_") or "scope")

    # The big SNL title bar is gone — the tab bar + Table name identify the view.
    # The status line (count / provenance) and the results table render BELOW the
    # control bar (after the action row); see the status block further down.

    # ── Columns dialog (picker only — export is its own button now) ────
    @st.dialog("Columns", width="large")
    def _columns_dialog():
        all_metric_keys = [m["key"] for m in METRICS if m.get("format") != "date"]
        default_cols = st.session_state.get(f"custom_cols_{tab_key}", tab_columns)
        default_cols = [c for c in default_cols if c in all_metric_keys]
        st.multiselect(
            "Columns to display (leave as-is for the tab's default view)",
            all_metric_keys, default=default_cols,
            format_func=lambda k: f"{METRICS_BY_KEY.get(k, {}).get('label', k)}  "
                                  f"({METRICS_BY_KEY.get(k, {}).get('category', '—')})",
            key=f"custom_cols_{tab_key}")

    # ── Export dialog — CSV / Excel of the current result set ──────────
    @st.dialog("Export results")
    def _export_dialog():
        st.caption(f"Export the current {len(display_metrics)} banks × "
                   f"{len(display_cols_final)} columns, exactly as shown "
                   "(scope, filters and sort applied).")
        if not display_metrics:
            st.warning("No banks to export.")
            return
        export_df = pd.DataFrame(display_metrics)
        export_cols = ["ticker"] + [c for c in display_cols_final
                                    if c in export_df.columns]
        export_df = export_df[export_cols].copy()
        rename = {"ticker": "Ticker"}
        for c in display_cols_final:
            m = METRICS_BY_KEY.get(c)
            if m:
                rename[c] = m["label"]
        export_df = export_df.rename(columns=rename)
        ec1, ec2 = st.columns(2)
        with ec1:
            st.download_button(
                "Download CSV", export_df.to_csv(index=False).encode("utf-8"),
                file_name=f"{tab_key}_{scope_slug}.csv", mime="text/csv",
                use_container_width=True, key=f"csv_{tab_key}")
        with ec2:
            try:
                import io
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                    export_df.to_excel(writer, index=False, sheet_name=tab_key[:31])
                    from openpyxl.utils import get_column_letter
                    ws = writer.sheets[tab_key[:31]]
                    ws.freeze_panes = "A2"
                    for col_idx, col_name in enumerate(export_df.columns, start=1):
                        # NaN-safe width: str() every value (Arrow-backed columns
                        # keep NaN as a float, so .astype(str).map(len) TypeErrors).
                        max_len = max([len(str(col_name))]
                                      + [len(str(v)) for v in export_df[col_name].tolist()])
                        ws.column_dimensions[get_column_letter(col_idx)].width = min(
                            28, max_len + 2)
                st.download_button(
                    "Download Excel", buf.getvalue(),
                    file_name=f"{tab_key}_{scope_slug}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True, key=f"xlsx_{tab_key}")
            except Exception as e:
                st.caption(f"Excel export unavailable: {type(e).__name__}")

    # ── Bank groups panel (folded into the Saved dialog) ───────────────
    def _render_groups_panel():
        """Firm-wide bank groups (data/bank_groups), shown inside the Saved dialog.
        Three tabs: save the current screen set, build from a pasted/CSV ticker
        list, or manage an existing group (members, description, tag, rename, del)."""
        from data.bank_groups import (list_groups, load_group, rename_group,
                                       delete_group, parse_tickers)

        _t_save, _t_paste, _t_manage = st.tabs(
            ["Save current", "Paste / import", "Manage"])

        with _t_save:
            st.caption("Save the current (filtered) screen set as a named group.")
            _gn = st.text_input("Group name", placeholder="e.g. CRE-heavy Southeast",
                                key=f"grp_new_{tab_key}")
            _gd = st.text_input("Description (optional)", key=f"grp_newdesc_{tab_key}")
            _gt = st.text_input("Tag / folder (optional)", placeholder="e.g. Watchlists",
                                key=f"grp_newtag_{tab_key}")
            if st.button(f"Save {len(display_metrics)} banks", key=f"grp_newbtn_{tab_key}",
                         use_container_width=True):
                _tk = [m["ticker"] for m in display_metrics if m.get("ticker")]
                if not _gn.strip():
                    st.warning("Enter a name first.")
                elif not _tk:
                    st.warning("No banks to save.")
                elif save_group(_gn, _tk, _gd, _gt):
                    st.success(f"Saved '{_gn.strip()}' ({len(_tk)} banks).")
                    st.rerun()
                else:
                    st.error("Could not save.")

        with _t_paste:
            st.caption("Build a group from a pasted ticker list or an uploaded "
                       "CSV/TXT of tickers (comma, space or newline separated).")
            _pn = st.text_input("Group name", key=f"grp_pn_{tab_key}")
            _pt = st.text_input("Tag / folder (optional)", key=f"grp_pt_{tab_key}")
            _paste = st.text_area("Tickers", placeholder="JPM, BAC, WFC …",
                                  key=f"grp_paste_{tab_key}")
            _csv = st.file_uploader("…or upload CSV / TXT", type=["csv", "txt"],
                                    key=f"grp_csv_{tab_key}")
            if st.button("Create group", key=f"grp_pbtn_{tab_key}",
                         use_container_width=True):
                _raw = _paste or ""
                if _csv is not None:
                    try:
                        _raw += "\n" + _csv.getvalue().decode("utf-8", "ignore")
                    except Exception:
                        pass
                _tk = parse_tickers(_raw)
                if not _pn.strip():
                    st.warning("Enter a name first.")
                elif not _tk:
                    st.warning("No tickers parsed.")
                elif save_group(_pn, _tk, "", _pt):
                    _miss = [t for t in _tk if t not in set(watchlist)]
                    _msg = f"Created '{_pn.strip()}' ({len(_tk)} tickers)."
                    if _miss:
                        _msg += (f" {len(_miss)} not in the current universe: "
                                 f"{', '.join(_miss[:8])}{'…' if len(_miss) > 8 else ''}")
                    st.success(_msg)
                    st.rerun()
                else:
                    st.error("Could not save.")

        with _t_manage:
            _groups = list_groups()
            if not _groups:
                st.caption("No saved groups yet — create one in the other tabs.")
                return
            _cnt = {g["name"]: g["count"] for g in _groups}
            _tagof = {g["name"]: g.get("tag", "") for g in _groups}
            _pick = st.selectbox(
                "Group", [g["name"] for g in _groups],
                format_func=lambda n: ((f"[{_tagof[n]}] " if _tagof.get(n) else "")
                                       + f"{n}  ({_cnt.get(n, 0)})"),
                key=f"grp_pick_{tab_key}")
            _g = load_group(_pick) or {}
            _cur = _g.get("tickers", [])
            _univ = sorted(set(watchlist) | set(_cur))
            _members = st.multiselect(
                "Members — add or remove banks", _univ, default=_cur,
                format_func=lambda t: (f"{t} — {get_name(t)}"
                                       if get_name(t) and get_name(t) != t else t),
                key=f"grp_members_{tab_key}_{_pick}")
            _ed_desc = st.text_input("Description", value=_g.get("description", ""),
                                     key=f"grp_eddesc_{tab_key}_{_pick}")
            _ed_tag = st.text_input("Tag / folder", value=_g.get("tag", ""),
                                    key=f"grp_edtag_{tab_key}_{_pick}")
            if st.button("Save changes", key=f"grp_savemem_{tab_key}",
                         use_container_width=True):
                if save_group(_pick, _members, _ed_desc, _ed_tag):
                    st.success(f"Updated '{_pick}' ({len(_members)} banks).")
                    st.rerun()
                else:
                    st.error("Could not save changes.")

            st.markdown("**Export**")
            st.download_button("Download CSV", ("ticker\n" + "\n".join(_cur)).encode("utf-8"),
                               file_name=f"{_pick}.csv", mime="text/csv",
                               key=f"grp_dl_{tab_key}", use_container_width=True)
            st.text_area("Copy tickers", value=", ".join(_cur), height=68,
                         key=f"grp_copy_{tab_key}_{_pick}")

            rc1, rc2 = st.columns([3, 1])
            with rc1:
                _rn = st.text_input("Rename to", placeholder="New name…",
                                    key=f"grp_rn_{tab_key}", label_visibility="collapsed")
            with rc2:
                if st.button("Rename", key=f"grp_rnbtn_{tab_key}", use_container_width=True):
                    if _rn.strip() and rename_group(_pick, _rn):
                        st.success(f"Renamed '{_pick}' → '{_rn.strip()}'.")
                        st.rerun()
                    else:
                        st.warning("Enter a new name.")
            # Delete gated behind an explicit confirm — one-way write to shared storage.
            _delok = st.checkbox("Confirm delete", key=f"grp_delok_{tab_key}")
            if st.button("Delete this group", key=f"grp_del_{tab_key}",
                         disabled=not _delok, use_container_width=True):
                if delete_group(_pick):
                    st.success(f"Deleted '{_pick}'.")
                    st.rerun()
                else:
                    st.error("Could not delete.")

    # ── Compact toolbar — action buttons (small, left, under a thin divider) ──
    # Groups is folded into Saved; Compare hands the set to the side-by-side
    # Compare view and shows only when small enough to compare (engaged, ≤30). A
    # trailing SPACER column keeps the buttons grouped left and content-sized.
    _flabel = f"Filters ({len(filter_specs)})" if filter_specs else "Filters"
    _show_compare = _engaged and 0 < len(display_metrics) <= 30
    with st.container(key="screen_actbtns"):
        _aw = [1.0, 1.0, 1.0, 1.0, 1.0] + ([1.5] if _show_compare else []) + [7.5]
        _ac = st.columns(_aw)
        with _ac[0]:
            if st.button("← Screens", key=f"btn_close_{tab_key}",
                         use_container_width=True, help="Back to the launcher"):
                st.session_state["_screen_action"] = {"act": "close"}
                st.rerun()
        with _ac[1]:
            if st.button(_flabel, key=f"btn_filters_{tab_key}", use_container_width=True):
                _filters_dialog()
        with _ac[2]:
            if st.button("Columns", key=f"btn_cols_{tab_key}", use_container_width=True):
                _columns_dialog()
        with _ac[3]:
            if st.button("Saved", key=f"btn_saved_{tab_key}", use_container_width=True):
                _saved_dialog()
        with _ac[4]:
            if st.button("Export", key=f"btn_export_{tab_key}", use_container_width=True):
                _export_dialog()
        if _show_compare:
            with _ac[5]:
                if st.button(f"Compare {len(display_metrics)} →", type="primary",
                             key=f"compare_handoff_{tab_key}", use_container_width=True):
                    st.session_state["_compare_handoff_tickers"] = [
                        m["ticker"] for m in display_metrics if m.get("ticker")]
                    # sc_sub already instantiated; flag the switch for next run.
                    st.session_state["_goto_compare"] = True
                    st.rerun()

    # ── Status line + results (below the control bar) ──────────────────
    from ui.chrome import status_dot
    if not _engaged:
        st.info("**Blank screen** — add a **Filter** or pick a **Scope** in the bar "
                "above to populate results, or use **Saved** to load one. "
                "**← Screens** goes back to the launcher.")
    else:
        filter_note = (f" · {len(filter_specs)} filter"
                       f"{'s' if len(filter_specs) != 1 else ''}") if filter_specs else ""
        nodata_note = (f" · {n_excluded_nodata} excluded (no data)"
                       if n_excluded_nodata else "")
        if is_asof:
            # Point-in-time: count banks no longer in today's coverage.
            _live = {t for t in watchlist}
            _exited = sum(1 for m in display_metrics if m.get("ticker") not in _live)
            _exit_note = f" · incl. {_exited} since-exited" if _exited else ""
            _meta = (status_dot("warn", f"As of {asof_q_label}")
                     + f" · {len(display_metrics)} banks · {screening_tab['title']}"
                     + f" · {scope_label}{filter_note}{nodata_note}{_exit_note}"
                     + " · FDIC point-in-time (market & SEC metrics n/a)")
        else:
            _meta = (status_dot("ok", f"{len(display_metrics)} banks")
                     + f" · {screening_tab['title']} · {scope_label}{filter_note}"
                     + f"{nodata_note} · FDIC + SEC fundamentals · "
                     + f"{len(display_cols_final)} columns")
        st.markdown(
            f'<div style="font-size:var(--fs-xs);color:var(--text-secondary);'
            f'margin:8px 0 7px;">{_meta}</div>',
            unsafe_allow_html=True,
        )
        if not is_asof:
            fdic_ages = {t: cache.fdic_age(t) for t in display_tickers[:10]}
            sec_ages = {t: cache.sec_age(t) for t in display_tickers[:10]}
            render_data_freshness(fdic_ages, sec_ages, st.session_state.ibkr_connected)
        render_generic_table(
            display_metrics, display_cols_final, table_key=tab_key, show_legend=True,
        )

elif section == "Company":
    # ── COMPANY ANALYSIS: Single-bank deep dive ─────────────────────────

    # Single search box over the ENTIRE universe — every covered US bank is
    # searchable by ticker or name (this box used to offer only the watchlist).
    # accept_new_options still allows tickers we haven't mapped yet; those
    # resolve dynamically via bank_mapping.
    _opts = list(watchlist)  # = the full universe (see Coverage scope above)
    _cur = st.session_state.get("company_pick")
    if _cur and _cur not in _opts:
        _opts.append(_cur)  # keep the current/deep-linked selection selectable

    def _fmt_pick(t):
        nm = get_name(t)
        return f"{t} — {nm}" if (nm and nm != t) else t

    # Narrow box (it doesn't need full width); a short visible label keeps the
    # selected value from rendering clipped (a known collapsed-label quirk).
    _pcol, _ = st.columns([2, 3])
    with _pcol:
        st.selectbox(
            "🔎 Search a bank",
            options=_opts,
            format_func=_fmt_pick,
            placeholder="Ticker or name… (e.g. BANR, JPM)",
            accept_new_options=True,
            key="company_pick",
        )
    company_ticker = (st.session_state.get("company_pick") or "").strip().upper() or None

    if not company_ticker:
        st.info("👆 Type a ticker above to begin (your watchlist autocompletes; any ticker works).")
    else:
        # Two-level navigation at the TOP of the page (under the picker):
        # a top row of sections, then that section's sub-tabs. Both are radios
        # wrapped in keyed containers so the CSS in ui/styles.py styles them as
        # tab bars (.st-key-company_section_nav = primary, .st-key-company_subtab_nav
        # = lighter secondary). The sub-tab radio uses a per-section key so each
        # section remembers its own active sub-tab.
        with st.container(key="company_section_nav"):
            company_section = st.radio(
                "Section", list(COMPANY_NAV.keys()), key="company_section",
                horizontal=True, label_visibility="collapsed",
            )
        # Financials carries a basis layer (Company Reported | Templated)
        # between the section and its sub-tabs; other sections are a flat list.
        _nav = COMPANY_NAV[company_section]
        company_basis = None
        if isinstance(_nav, dict):
            with st.container(key="company_basis_nav"):
                company_basis = st.radio(
                    "Basis", list(_nav.keys()),
                    key=f"company_basis::{company_section}",
                    horizontal=True, label_visibility="collapsed",
                )
            _subs = _nav[company_basis]
        else:
            _subs = _nav
        if len(_subs) > 1:
            with st.container(key="company_subtab_nav"):
                company_subtab = st.radio(
                    "View", _subs,
                    key=f"company_subtab::{company_section}::{company_basis}",
                    horizontal=True, label_visibility="collapsed",
                )
        else:
            company_subtab = _subs[0]
        st.markdown("<div style='margin-bottom:4px;'></div>", unsafe_allow_html=True)

        # URL <- widgets: the address bar always names the exact view, so a
        # browser refresh or shared link lands right back here.
        _url_pairs = [("s", "Company"), ("bank", company_ticker),
                      ("tab", company_subtab)]
        if company_basis:
            _url_pairs.append(("basis", company_basis))
        for _k, _v in _url_pairs:
            if st.query_params.get(_k) != _v:
                st.query_params[_k] = _v
        # The widget owns the selection now; remember it so the early-rerun
        # guard treats the (about-to-be-synced) URL as "already applied" and
        # never reverts a fresh pick back to the previous bank.
        st.session_state["_applied_url_bank"] = company_ticker

    if company_ticker:
        rendered = render_company_subtab(company_subtab, company_ticker, {
            "watchlist": watchlist,
            "load_metrics": load_single_bank_metrics_cached,
            "peer_cohort": lambda: all_metrics or get_watchlist_cohort(),
        }, basis=company_basis)
        if not rendered:
            st.error(
                f"No renderer wired for sub-tab “{company_subtab}” — "
                "COMPANY_NAV and the renderer registry in ui/company_nav.py "
                "are out of sync."
            )

elif section == "Market & Macro":
    from ui.macro import render_macro_dashboard
    with timed("macro.render"):
        render_macro_dashboard()

elif section == "Screen & Compare" and sc_sub == "Compare":
    # ── COMPARE: Side-by-side bank comparison (peers) ───────────────────
    from ui.peer_comparison import render_peer_comparison
    if not all_metrics:
        all_metrics = load_all_data(watchlist)
        cache.put("watchlist_metrics_last", all_metrics)
    render_peer_comparison(all_metrics)

elif section == "Screen & Compare" and sc_sub == "Trends":
    # ── TRENDS: one metric across N quarters, banks × quarters (FDIC) ────
    # The "As of" picker shows ONE past quarter; Trends shows the SAME metric across
    # the last N quarters, one row per bank. FDIC fundamentals (bank-subsidiary) +
    # SEC per-share (TBV/share, book value/share — HoldCo, forward-filled goodwill).
    from ui.chrome import title_bar
    from ui.bank_scope import scope_type_options, render_scope_sub
    from ui.trends_table import render_trends_table
    from data.as_of_metrics import metric_grid, TREND_METRICS
    from data.sec_per_share import (sec_metric_grid, SEC_TREND_METRICS,
                                    SEC_TREND_KEYS, SEC_TREND_FMT)

    _ALL_TM = TREND_METRICS + SEC_TREND_METRICS
    _mlabels = dict(_ALL_TM)
    _sec_keys = set(SEC_TREND_KEYS)

    title_bar("KSK Investors", "Quarterly Trends")
    st.caption("One metric across recent quarters, one row per bank. FDIC "
               "fundamentals (bank-subsidiary) plus SEC per-share (TBV/share, book "
               "value/share — holding-company basis).")

    tc1, tc2, tc3 = st.columns([2, 1, 3])
    with tc1:
        _metric = st.selectbox("Metric", [k for k, _ in _ALL_TM],
                               format_func=lambda k: _mlabels[k], key="trend_metric")
    with tc2:
        _nq = st.selectbox("Quarters", [4, 8, 12, 16, 20], index=4, key="trend_nq")
    with tc3:
        _scope_t = st.selectbox("Scope", scope_type_options(), key="trend_scope_type")

    _td, _ttick, _tlabel = render_scope_sub(all_metrics, _scope_t, key_prefix="trend")

    _is_sec = _metric in _sec_keys
    _is_all = _scope_t == "All banks"
    _src = "SEC companyfacts (HoldCo)" if _is_sec else "FDIC point-in-time"
    _fmt, _dec = SEC_TREND_FMT.get(_metric, (None, None)) if _is_sec else (None, None)

    # Per source: SEC keyed by CIK, FDIC by cert.
    _idmap = {}
    for _t in _ttick:
        _k = get_cik(_t) if _is_sec else get_fdic_cert(_t)
        if _k:
            _idmap[int(_k)] = _t

    def _grid(build_it):
        _sid = "ALLBANKS" if _is_all else None
        _gn = 20 if _is_all else _nq   # all-banks served from the pre-warmed 20q grid
        if _is_sec:
            return sec_metric_grid(_metric, _idmap, _gn, build_if_missing=build_it,
                                   scope_id=_sid)
        return metric_grid(_metric, _idmap, _gn, build_if_missing=build_it, scope_id=_sid)

    # All-banks (~300s for FDIC; ~40s+ SEC fetches) exceeds the live request timeout,
    # so it is NEVER built in-request — pre-warmed nightly (jobs/refresh_trends) and
    # read cache-only here. Scoped cohorts build live.
    if not _idmap:
        st.warning("No banks with an FDIC cert / SEC CIK in this scope.")
    elif _is_all:
        _labels, _rows = _grid(False)
        if _labels is None:
            st.info("The **all-banks** trend grid is prepared by a nightly job and "
                    "isn't cached yet. Pick a **scope** (a saved group, asset band, "
                    "state, …) for an instant live view, or check back after the next "
                    "refresh.")
        else:
            _labels = _labels[:_nq]
            st.markdown(
                f'<div style="font-size:var(--fs-xs);color:var(--text-secondary);'
                f'margin:1px 0 7px;">{_mlabels[_metric]} · {len(_rows)} banks · '
                f'all banks · {_nq} quarters · {_src} (pre-warmed)</div>',
                unsafe_allow_html=True)
            render_trends_table(_rows, _labels, _metric, _fmt, _dec)
    else:
        with st.spinner(f"Building {_mlabels[_metric]} across {_nq} quarters for "
                        f"{len(_idmap)} banks…"):
            _labels, _rows = _grid(True)
        st.markdown(
            f'<div style="font-size:var(--fs-xs);color:var(--text-secondary);'
            f'margin:1px 0 7px;">{_mlabels[_metric]} · {len(_rows)} banks · '
            f'{_tlabel} · {_nq} quarters · {_src}</div>',
            unsafe_allow_html=True)
        render_trends_table(_rows, _labels, _metric, _fmt, _dec)

elif section == "Earnings":
    # ── EARNINGS ANALYSIS: Aggregate tracking ───────────────────────────
    if not all_metrics:
        all_metrics = load_all_data(watchlist)
        cache.put("watchlist_metrics_last", all_metrics)
    render_earnings_overview(watchlist, all_metrics)

elif section == "News & Research":
    # ── NEWS & RESEARCH: Universe-wide event feed ───────────────────────
    from ui.recent_activity import render_activity_overview
    render_activity_overview()

elif section == "Geographic":
    # ── GEOGRAPHIC: Multi-bank branch map + state/MSA deposit lookup ────
    from ui.geo_view import render_geo_view
    render_geo_view()


# ── Auto-refresh ─────────────────────────────────────────────────────────
if auto_refresh and st.session_state.ibkr_connected:
    time.sleep(PRICE_REFRESH_SECONDS)
    st.rerun()
