"""
Recent Activity panel — renders events from the unified store.

Two entry points:
  • render_recent_activity(ticker) — single-bank feed (Company Analysis)
  • render_activity_overview()    — universe-wide feed (Home / Activity tab)
"""

from __future__ import annotations
from datetime import datetime, timezone

import streamlit as st

from data.events import get_recent_events, get_universe_recent


SOURCE_LABELS = {
    "sec_8k": "SEC 8-K",
    "businesswire": "Business Wire",
    "prnewswire": "PR Newswire",
    "globenewswire": "GlobeNewswire",
    "yfinance_news": "Yahoo News",
    "ir_site": "IR Site",
}

EVENT_TYPE_LABELS = {
    "earnings": "Earnings",
    "press_release": "Press Release",
    "m_and_a": "M&A",
    "executive_change": "Officer Change",
    "shareholder_vote": "Shareholder Vote",
    "regulatory": "Regulatory",
    "news": "News",
}

EVENT_TYPE_COLORS = {
    "earnings": "#2563eb",       # blue
    "press_release": "#059669",  # green
    "m_and_a": "#7c3aed",        # purple
    "executive_change": "#d97706",
    "shareholder_vote": "#6b7280",
    "regulatory": "#dc2626",     # red
    "news": "#0891b2",           # cyan
}


def _fmt_ago(ts: datetime | None) -> str:
    if ts is None:
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    secs = int(delta.total_seconds())
    if secs < 60: return f"{secs}s ago"
    if secs < 3600: return f"{secs // 60}m ago"
    if secs < 86400: return f"{secs // 3600}h ago"
    days = secs // 86400
    if days < 30: return f"{days}d ago"
    months = days // 30
    if months < 12: return f"{months}mo ago"
    return f"{days // 365}y ago"


def _badge(label: str, color: str) -> str:
    return (
        f'<span style="background:{color}1a;color:{color};'
        f'padding:2px 8px;border-radius:10px;font-size:0.75rem;'
        f'font-weight:600;margin-right:6px;">{label}</span>'
    )


def _render_event_card(ev: dict, show_ticker: bool = False):
    src_label = SOURCE_LABELS.get(ev["source"], ev["source"])
    type_label = EVENT_TYPE_LABELS.get(ev["event_type"], ev["event_type"])
    color = EVENT_TYPE_COLORS.get(ev["event_type"], "#6b7280")
    ago = _fmt_ago(ev.get("published_at"))

    header_html = (
        _badge(type_label, color)
        + _badge(src_label, "#6b7280")
        + f'<span style="color:#6b7280;font-size:0.85rem;">{ago}</span>'
    )
    if show_ticker:
        header_html = (
            f'<strong style="font-size:1rem;margin-right:8px;">{ev["ticker"]}</strong>'
            + header_html
        )

    st.markdown(header_html, unsafe_allow_html=True)
    st.markdown(f"**{ev.get('headline', '(no headline)')}**")
    if ev.get("summary"):
        st.markdown(
            f'<div style="color:#374151;margin-top:4px;font-size:0.9rem;">'
            f'{ev["summary"]}</div>',
            unsafe_allow_html=True,
        )
    if ev.get("url"):
        st.markdown(f"[View source →]({ev['url']})")
    st.markdown("---")


def render_recent_activity(ticker: str, limit: int = 20):
    """Single-bank event feed."""
    if not ticker:
        st.info("Pick a bank from the sidebar to see its recent activity.")
        return

    st.subheader(f"📰 Recent Activity — {ticker}")
    st.caption(
        "SEC 8-K filings and (coming soon) press-release wire feeds. "
        "Refreshed automatically every 30 minutes during market hours."
    )

    events = get_recent_events(ticker, limit=limit)
    if not events:
        st.info(
            f"No events ingested yet for **{ticker}**. "
            "The dashboard polls SEC EDGAR every 30 minutes. "
            "If this bank has filed an 8-K in the last 7 days it should appear shortly."
        )
        return

    for ev in events:
        _render_event_card(ev, show_ticker=False)


def render_activity_overview(limit: int = 50):
    """Universe-wide event feed (Home page or Activity tab)."""
    st.subheader("📰 Recent Activity (Universe)")
    st.caption(
        f"Most recent {limit} events across all banks in the universe. "
        "8-K filings are pulled from SEC EDGAR; wire press releases coming next."
    )

    # Filter widgets
    col1, col2 = st.columns([1, 1])
    with col1:
        type_filter = st.multiselect(
            "Filter by type",
            options=list(EVENT_TYPE_LABELS.keys()),
            default=[],
            format_func=lambda x: EVENT_TYPE_LABELS.get(x, x),
        )
    with col2:
        source_filter = st.multiselect(
            "Filter by source",
            options=list(SOURCE_LABELS.keys()),
            default=[],
            format_func=lambda x: SOURCE_LABELS.get(x, x),
        )

    events = get_universe_recent(limit=limit * 2)  # over-fetch for filtering
    if type_filter:
        events = [e for e in events if e["event_type"] in type_filter]
    if source_filter:
        events = [e for e in events if e["source"] in source_filter]
    events = events[:limit]

    if not events:
        st.info(
            "No events match the current filters. "
            "If the events table is empty, the poll job hasn't run yet — "
            "give it 30 minutes after deploy."
        )
        return

    for ev in events:
        _render_event_card(ev, show_ticker=True)
