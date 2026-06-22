"""
Earnings conference-call details for the Home calendar's earnings rows.

There is no structured feed for upcoming bank earnings-call logistics (FMP /
yfinance carry the date and an EPS estimate, never the webcast URL or dial-in).
Those details only appear in the earnings-DATE-announcement press release each
bank issues ~2 weeks out ("… will report Q2 results on July 24 and host a
conference call at 10:00 a.m. ET; webcast at …; dial-in 1-800-…").

We already ingest those PRs (data/events) and keep up to ~2000 chars of body
text. This module parses the call time / webcast URL / dial-in out of that body
— BEST-EFFORT and partial by nature: not every release states a dial-in, the
body is truncated, and formats vary. Every field is None when not confidently
found; nothing is ever fabricated.

Pure parser (parse_call_info) is unit-tested; call_info_map() does ONE events
query, 1h-cached, so the per-render path stays cheap.
"""

from __future__ import annotations

import re

# Time-of-day with an explicit US time zone — banks always state the zone for
# the call ("10:00 a.m. ET", "8:30 AM Eastern Time", "9 a.m. Central", and the
# very common "9:00 am (ET)" with the zone in parentheses).
_TIME_RE = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?\s*[(\[]?\s*"
    r"(eastern|central|mountain|pacific|e[ds]?t|c[ds]?t|m[ds]?t|p[ds]?t)\b",
    re.I,
)
_TZ_ABBR = {"eastern": "ET", "central": "CT", "mountain": "MT", "pacific": "PT"}

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")
_WEBCAST_CUES = ("webcast", "listen", "live audio", "audio of the call",
                 "investor", "ir.", "/investor")

_PHONE_RE = re.compile(
    r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
_DIALIN_CUE_RE = re.compile(
    r"(?:dial[-\s]?in|toll[-\s]?free|by (?:tele)?phone|telephone|"
    r"to access the call|participants? (?:may|can|should) dial)", re.I)
_ID_RE = re.compile(
    r"(?:conference id|passcode|access code|pass\s?code|conference number)"
    r"[:#\s]*([0-9]{4,})", re.I)


def _parse_call_time(text: str) -> str | None:
    """First time-with-zone in the release (the call time), normalized to a
    compact label like '10:00a ET' / '9a CT'. None if absent."""
    m = _TIME_RE.search(text)
    if not m:
        return None
    hour, minute, ap, tz = m.group(1), m.group(2), m.group(3).lower(), m.group(4).lower()
    tzab = _TZ_ABBR.get(tz)
    if tzab is None:
        tzab = tz[0].upper() + "T"          # e/c/m/p + DT/ST -> ET/CT/MT/PT
    clock = f"{hour}:{minute}" if minute else hour
    return f"{clock}{ap} {tzab}"


def _parse_webcast_url(text: str) -> str | None:
    """A URL whose surrounding text marks it as the call's webcast/listen link.
    None if no contextually-webcast URL is present."""
    low = text.lower()
    for m in _URL_RE.finditer(text):
        ctx = low[max(0, m.start() - 90): m.end() + 10]
        if any(cue in ctx for cue in _WEBCAST_CUES):
            return m.group(0).rstrip(".,;)]\"'")
    return None


def _parse_dial_in(text: str) -> str | None:
    """A dial-in phone number (+ conference ID/passcode when stated). Prefers a
    number next to a dial-in cue; nothing speculative otherwise. None if absent."""
    num = None
    cue = _DIALIN_CUE_RE.search(text)
    if cue:
        # Releases often put boilerplate ("To ask a question on the call,
        # individuals may call in by dialing …") between the cue and the number.
        m = _PHONE_RE.search(text, cue.end(), cue.end() + 140)
        if m:
            num = m.group(0).strip()
    if not num:
        # No explicit cue → only accept a clearly North-American toll number
        # (leading 1/+1) to avoid grabbing an unrelated figure.
        m = re.search(r"\+?1[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", text)
        if not m:
            return None
        num = m.group(0).strip()
    pid = _ID_RE.search(text)
    return f"{num} (ID {pid.group(1)})" if pid else num


def parse_call_info(text: str) -> dict:
    """Extract {call_time, webcast_url, dial_in} from an earnings-PR body.
    Each value is None when not confidently found; returns {} when nothing is
    parseable. Pure / unit-tested — never fabricates."""
    if not text:
        return {}
    info = {
        "call_time": _parse_call_time(text),
        "webcast_url": _parse_webcast_url(text),
        "dial_in": _parse_dial_in(text),
    }
    return info if any(info.values()) else {}


def mid_label(ci: dict | None) -> str:
    """Compact calendar-cell label for parsed call info: time + a webcast
    indicator (the row itself links to the webcast). '' when nothing parsed."""
    if not ci:
        return ""
    parts = []
    if ci.get("call_time"):
        parts.append(ci["call_time"])
    if ci.get("webcast_url"):
        parts.append("webcast ↗")          # ↗ — row href points to it
    elif ci.get("dial_in"):
        parts.append("call")
    return " · ".join(parts)


def call_info_map() -> dict:
    """{ticker: {call_time, webcast_url, dial_in}} parsed from each bank's most
    recent earnings-announcement press release.

    ONE events query, 1h-cached (st.cache_data) so the calendar render never
    re-queries/re-parses. Queries 'earnings'-typed events directly (not a flat
    recency window): the call-details PR is often weeks old by the report date,
    so it would otherwise fall outside the most-recent rows. A parsed webcast URL
    is dropped unless it passes the news feed's safety filter. Empty on failure."""
    import streamlit as st

    @st.cache_data(ttl=3600, show_spinner=False)
    def _build() -> dict:
        try:
            from data.events.store import get_events_by_type
            from data.events.wire_base import is_safe_news_url
        except Exception:
            return {}
        try:
            rows = get_events_by_type("earnings", limit=800)
        except Exception:
            return {}
        out: dict = {}
        for r in rows:                               # newest-first
            tk = r.get("ticker")
            if not tk or tk in out:
                continue
            info = parse_call_info(r.get("summary") or "")
            if not info:
                continue
            url = info.get("webcast_url")
            if url and not is_safe_news_url(url):
                info["webcast_url"] = None
            if any(info.values()):
                out[tk] = info
        return out

    try:
        return _build()
    except Exception:
        return {}


# FMP's report-time code → human label. bmo = before market open, amc = after
# market close, dmh = during market hours.
_WHEN_LABEL = {"bmo": "Before open", "amc": "After close", "dmh": "Midday"}


def earnings_timing_map() -> dict:
    """{ticker: {"when": label, "confirmed": bool}} from FMP's earnings calendar
    for the next ~75 days — reliable, universe-wide report timing (before/after
    open) that the yfinance estimate lacks. ONE FMP call, 6h-cached. The Home
    calendar shows this when no precise PR/IR call time is available. Empty on
    failure."""
    import streamlit as st

    @st.cache_data(ttl=21600, show_spinner=False)
    def _build() -> dict:
        from datetime import date, timedelta
        from data import fmp_client
        try:
            today = date.today()
            rows = fmp_client.get_earnings_calendar(
                today.isoformat(), (today + timedelta(days=75)).isoformat())
        except Exception:
            return {}
        out: dict = {}
        for r in (rows or []):
            tk = (r.get("symbol") or "").upper()
            if not tk or tk in out:                 # first (soonest) per symbol
                continue
            label = _WHEN_LABEL.get((r.get("time") or "").lower())
            if label:
                out[tk] = {"when": label, "confirmed": bool(r.get("confirmed"))}
        return out

    try:
        return _build()
    except Exception:
        return {}
