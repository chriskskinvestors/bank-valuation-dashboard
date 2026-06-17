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
