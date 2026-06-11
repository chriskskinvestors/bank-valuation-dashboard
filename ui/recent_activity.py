"""
Recent Activity panel — renders events from the unified store.

Two entry points:
  • render_recent_activity(ticker) — single-bank feed (Company Analysis)
  • render_activity_overview()    — universe-wide feed (Home / Activity tab)
"""

from __future__ import annotations
import html as _html
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
    "google_news": "Google News",
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


_FEED_CSS = """
<style>
.ev-feed { margin-top: 2px; }
.ev-row { padding: 6px 0 7px; border-bottom: 1px solid rgba(148,163,184,0.14); }
.ev-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; line-height: 1.2; }
.ev-tk { font-weight: 700; color: #0f172a; font-size: 0.82rem; }
.ev-badge { font-size: 0.64rem; font-weight: 600; padding: 1px 7px; border-radius: 9px;
            background: rgba(107,114,128,0.12); color: #6b7280; white-space: nowrap; }
.ev-ago { color: #94a3b8; font-size: 0.72rem; margin-left: auto; white-space: nowrap; }
.ev-head { font-size: 0.88rem; font-weight: 600; color: #0f172a; line-height: 1.3; margin-top: 1px; }
.ev-head a.ev-src { color: #2563eb; text-decoration: none; font-weight: 400; }
.ev-sum { font-size: 0.79rem; color: #475569; line-height: 1.38; margin-top: 1px;
          display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
</style>
"""


def _summary_text(ev: dict) -> str:
    """The best one/two-line summary for an event, trimmed; never just echo the
    headline back."""
    s = (ev.get("summary") or "").strip()
    head = (ev.get("headline") or "").strip()
    if not s or s.lower() == head.lower():
        return ""
    # Collapse whitespace; clamp length (the CSS also line-clamps to 2 lines).
    s = " ".join(s.split())
    return s[:260].rstrip() + ("…" if len(s) > 260 else "")


def _event_row(ev: dict, show_ticker: bool) -> str:
    type_label = EVENT_TYPE_LABELS.get(ev["event_type"], ev["event_type"])
    color = EVENT_TYPE_COLORS.get(ev["event_type"], "#6b7280")
    src_label = SOURCE_LABELS.get(ev["source"], ev["source"])
    ago = _fmt_ago(ev.get("published_at"))
    url = ev.get("url")
    link = f'<a class="ev-src" href="{_html.escape(str(url))}" target="_blank">↗</a>' if url else ""
    tk = (f'<span class="ev-tk">{_html.escape(str(ev.get("ticker") or ""))}</span>'
          if (show_ticker and ev.get("ticker")) else "")
    headline = _html.escape(ev.get("headline") or "(no headline)")
    summ = _summary_text(ev)
    sum_html = f'<div class="ev-sum">{_html.escape(summ)}</div>' if summ else ""
    return (
        f'<div class="ev-row"><div class="ev-meta">{tk}'
        f'<span class="ev-badge" style="color:{color};background:{color}14;">{_html.escape(type_label)}</span>'
        f'<span class="ev-badge">{_html.escape(src_label)}</span>'
        f'<span class="ev-ago">{ago}</span></div>'
        f'<div class="ev-head">{headline} {link}</div>{sum_html}</div>'
    )


def _render_feed(events: list[dict], show_ticker: bool = False):
    """Render the whole feed as a single dense HTML block (no inter-element gaps)."""
    from data.events.wire_base import is_safe_news_url
    # Defensive: never render an event whose link is a messaging/social/spam URL
    # (e.g. a content-farm "earnings" article linking to a WhatsApp group).
    events = [e for e in events if is_safe_news_url(e.get("url"))]
    body = "".join(_event_row(e, show_ticker) for e in events)
    st.markdown(_FEED_CSS + f'<div class="ev-feed">{body}</div>', unsafe_allow_html=True)


def render_recent_activity(ticker: str, limit: int = 20):
    """Single-bank event feed."""
    if not ticker:
        st.info("Pick a bank from the sidebar to see its recent activity.")
        return

    st.subheader(f"📰 Recent Activity — {ticker}")
    st.caption(
        "SEC 8-K filings plus Business Wire, PR Newswire, GlobeNewswire and Yahoo "
        "News feeds. Refreshed automatically every 30 minutes during market hours."
    )

    events = get_recent_events(ticker, limit=limit)
    if not events:
        st.info(
            f"No events ingested yet for **{ticker}**. "
            "The dashboard polls SEC EDGAR every 30 minutes. "
            "If this bank has filed an 8-K in the last 7 days it should appear shortly."
        )
        return

    _render_feed(events, show_ticker=False)


def render_activity_overview(limit: int = 50):
    """Universe-wide event feed (Home page or Activity tab)."""
    st.subheader("📰 Recent Activity (Universe)")
    st.caption(
        f"Most recent {limit} events across all banks in the universe — SEC 8-K "
        "filings plus Business Wire, PR Newswire, GlobeNewswire and Yahoo News."
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

    _render_feed(events, show_ticker=True)
