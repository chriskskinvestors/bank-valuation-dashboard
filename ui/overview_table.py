"""
Data-provenance freshness badges shown above the screening table.

(The legacy screening-table renderer that used to live here —
render_overview_table / render_column_selector — was dead code with zero
callers and was removed; app.py builds the screener itself and imports only
render_data_freshness from this module.)
"""

import streamlit as st


def render_data_freshness(fdic_ages: dict, sec_ages: dict, ibkr_connected: bool):
    """One compact, left-aligned row of data-provenance freshness badges.

    Was ``st.columns(3)``, which pinned each badge to the left edge of its own
    third of the page — three tiny pills flung across the full width with big
    empty gaps. A single flex row keeps them tight together, which is also how
    the design system treats pill groups.
    """
    badges: list[tuple[str, str]] = []

    # Price source: IBKR when connected (local), else FMP (cloud) — show real status.
    if ibkr_connected:
        badges.append(("IBKR LIVE", "freshness-live"))
    else:
        try:
            from data.fmp_client import _has_key
            fmp_ok = _has_key()
        except Exception:
            fmp_ok = False
        badges.append(("FMP LIVE", "freshness-live") if fmp_ok
                      else ("PRICES OFFLINE", "freshness-stale"))

    # FDIC + SEC fundamentals: average age across the sampled tickers.
    for src, ages_map in (("FDIC", fdic_ages), ("SEC", sec_ages)):
        ages = [a for a in ages_map.values() if a is not None]
        if ages:
            avg_hours = sum(ages) / len(ages) / 3600
            cls = "freshness-live" if avg_hours < 24 else "freshness-cached"
            badges.append((f"{src} {avg_hours:.0f}h ago", cls))
        else:
            badges.append((f"{src} NO DATA", "freshness-stale"))

    spans = "".join(
        f'<span class="freshness-badge {cls}">{label}</span>'
        for label, cls in badges)
    st.markdown(
        f'<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;'
        f'margin:0 0 8px;">{spans}</div>',
        unsafe_allow_html=True,
    )
