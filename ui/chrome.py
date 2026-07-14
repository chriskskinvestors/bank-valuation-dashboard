"""
Chrome components for the design system (docs/DESIGN-SYSTEM.md).

One implementation each for the page furniture every screen shares:
top_nav, title_bar, ledger (boxless KPI rows), status_dot, table_export.
st.metric is banned by the spec — use ledger(). All colors/sizes come
from the styles.py tokens; no raw hexes or ad-hoc font sizes here.
"""
from __future__ import annotations

import html as _html
import io

import streamlit as st


def top_nav(sections: list[str], key: str = "nav_section",
            wordmark: str = "KSK INVESTORS"):
    """The horizontal top navigation bar (replaces the sidebar):
    wordmark | section tabs | bank search | utilities. Returns
    (section, search_column, right_column) — the caller renders the global
    bank-search box into search_column and its status chip / refresh / settings
    into right_column so both stay in the bar."""
    wm, nav, search, right = st.columns([0.85, 4.0, 1.55, 1.1],
                                        vertical_alignment="center")
    with wm:
        st.markdown(
            f'<div style="font-size:var(--fs-sm);font-weight:600;'
            f'letter-spacing:0.08em;color:var(--brand-primary);">'
            f'{_html.escape(wordmark)}</div>', unsafe_allow_html=True)
    with nav:
        with st.container(key="topnav"):
            section = st.radio("Navigate", sections, key=key, horizontal=True,
                               label_visibility="collapsed")
    return section, search, right


def lazy_tabs(labels: list[str], key: str, default: int = 0) -> str:
    """A tab bar that renders ONLY the selected pane.

    ``st.tabs`` runs *every* pane's body on every rerun (hidden ones included),
    which is pure wasted work for heavy panes (charts, DB pulls). This renders a
    pill bar styled like the Company sub-tabs and returns the active label so the
    caller dispatches just one pane::

        sel = lazy_tabs(["Calendar", "Heat-Map", ...], key="earnings")
        if sel == "Calendar": _render_calendar()
        elif sel == "Heat-Map": _render_heatmap()

    The container key drives the pill styling (see styles.py ``st-key-lazytabs_``)."""
    with st.container(key=f"lazytabs_{key}"):
        return st.radio(key, labels, index=default, horizontal=True,
                        key=f"_lazytab_{key}", label_visibility="collapsed")


def title_bar(entity: str, page: str, ids_html: str = "") -> None:
    """SNL-style title bar: `Entity | PAGE NAME` + identifier link row.
    ids_html is caller-built (links already escaped/trusted)."""
    ids = f'<div class="tb-ids">{ids_html}</div>' if ids_html else ""
    st.markdown(
        f'<div class="ksk-titlebar"><span class="tb-main">{_html.escape(entity)}</span>'
        f'<span class="tb-sep">|</span>'
        f'<span class="tb-page">{_html.escape(page).upper()}</span>{ids}</div>',
        unsafe_allow_html=True)


def ledger(title: str, rows: list[tuple[str, str]]) -> None:
    """Boxless KPI block: small-caps title + label/value hairline rows.
    Values are caller-formatted HTML (may contain colored spans/links);
    labels are escaped here."""
    body = "".join(
        f'<div class="lg-row"><span class="lg-label">{_html.escape(label)}</span>'
        f'<span class="lg-val">{val}</span></div>'
        for label, val in rows
    )
    st.markdown(
        f'<div class="ksk-ledger"><div class="lg-title">{_html.escape(title)}</div>{body}</div>',
        unsafe_allow_html=True)


def status_dot(kind: str, label: str) -> str:
    """Colored dot + plain label (replaces emoji severity icons).
    kind: ok | warn | bad. Returns inline HTML."""
    return f'<span class="ksk-dot {kind}"></span>{_html.escape(label)}'


def table_export(df, filename: str, key: str) -> None:
    """Small right-aligned Export action for a data table (spec: every
    data table gets one). CSV via download_button; Excel callers can pass
    their own bytes through st.download_button directly."""
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    st.download_button("Export", buf.getvalue(), file_name=f"{filename}.csv",
                       mime="text/csv", key=key)


def ticker_company_url(ticker) -> str | None:
    """Root-relative Company-page URL for st.dataframe LinkColumn ticker cells
    (universal linking rule). Root-relative on purpose: the canvas grid renders
    in a component context, so a bare '?bank=' would resolve against the wrong
    base. None (blank cell) for missing/NaN/em-dash tickers. HTML surfaces use
    plain '?bank=' anchors instead — this helper is the dataframe path."""
    if ticker is None:
        return None
    t = str(ticker).strip()
    if not t or t == "—" or t.lower() == "nan":
        return None
    return f"/?s=Company&bank={t}"


def ticker_linkcol(col: str = "Ticker") -> dict:
    """column_config rendering `col` (URLs built by ticker_company_url) as the
    plain ticker text, deep-linking to the Company page. Grid link cells open
    a new tab — the canvas grid can't navigate in-app."""
    return {col: st.column_config.LinkColumn(
        col, display_text=r"bank=(.+)$", width="small",
        help="Open the bank's Company page")}
