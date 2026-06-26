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
from datetime import date

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


# ── Announced earnings-RELEASE date (from the PR headline) ────────────────
# Universal across IR platforms: every bank issues a "… Will Announce Q2 2026
# Results on July 14, 2026" headline ~2 weeks out, and we never truncate
# headlines — so parsing the date there confirms the release date even for banks
# whose IR site we don't scrape for structured call events.
_MONTHS = {m: i for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"), 1)}
_ON_DATE_RE = re.compile(
    r"\b(?:on|for)\s+(?:or\s+about\s+)?([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", re.I)
_ANNOUNCE_CUES = ("announce", "will report", "to report", "will release",
                  "to release", "schedule", "set date", "sets date",
                  "will host", "to host")


def _parse_on_date(text: str) -> str | None:
    """First '… on <Month> <Day>, <Year>' date in `text`, as ISO; None if none."""
    for m in _ON_DATE_RE.finditer(text or ""):
        mon = _MONTHS.get(m.group(1).lower())
        if not mon:
            continue
        try:
            return date(int(m.group(3)), mon, int(m.group(2))).isoformat()
        except ValueError:
            continue
    return None


def _announced_release_date(headline: str, today_iso: str) -> str | None:
    """The earnings-RELEASE date a bank states in an announcement headline ('…
    Will Announce Q2 2026 Results on July 14, 2026') — ISO, only when it's an
    announcement headline AND the date is in the future. A call/webcast-only
    headline is NOT read as the release date. None otherwise — never guessed."""
    hl = (headline or "").lower()
    if not any(c in hl for c in _ANNOUNCE_CUES):
        return None
    if ("conference call" in hl or "webcast" in hl) and not (
            "result" in hl or "earnings" in hl):
        return None
    d = _parse_on_date(headline)
    return d if (d and d >= today_iso) else None


def call_info_map() -> dict:
    """{ticker: {call_time, webcast_url, dial_in, release_date}} parsed from each
    bank's earnings-announcement press release.

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
        today_iso = date.today().isoformat()
        out: dict = {}
        for r in rows:                               # newest-first
            tk = r.get("ticker")
            if not tk:
                continue
            cur = out.get(tk) or {}
            # Call logistics from the PR body (best-effort, when not already found
            # from a newer PR for this ticker).
            if not any(cur.get(k) for k in ("call_time", "webcast_url", "dial_in")):
                ci = parse_call_info(r.get("summary") or "")
                url = ci.get("webcast_url")
                if url and not is_safe_news_url(url):
                    ci["webcast_url"] = None
                for k in ("call_time", "webcast_url", "dial_in"):
                    if ci.get(k):
                        cur[k] = ci[k]
            # Announced release date from the HEADLINE — captured even when the
            # body has no parseable call logistics (the universe-wide signal).
            if not cur.get("release_date"):
                rd = _announced_release_date(r.get("headline") or "", today_iso)
                if rd:
                    cur["release_date"] = rd
            if any(cur.values()):
                out[tk] = cur
        return out

    try:
        return _build()
    except Exception:
        return {}


def merged_call_info() -> dict:
    """{ticker: {call_time, webcast_url, dial_in}} combining BOTH call-detail
    sources: the structured Q4 IR events snapshot (date/time/webcast link, the
    reliable source) layered over the press-release parser (which still supplies
    the dial-in number Q4 events don't expose). Q4's call_time / webcast_url win
    when present. Used by the Calls & Webcasts agenda and the Home calendar."""
    base = {tk: dict(info) for tk, info in (call_info_map() or {}).items()}
    try:
        from data.events.ir_site import get_q4_call_details
        q4 = get_q4_call_details() or {}
    except Exception:
        q4 = {}
    for tk, info in q4.items():
        cur = dict(base.get(tk) or {})
        if info.get("call_time"):
            cur["call_time"] = info["call_time"]
        if info.get("webcast_url"):
            cur["webcast_url"] = info["webcast_url"]
        if info.get("call_date"):
            cur["call_date"] = info["call_date"]   # the company's announced date
        base[tk] = cur
    return base


# FMP's report-time code → human label. bmo = before market open, amc = after
# market close, dmh = during market hours.
_WHEN_LABEL = {"bmo": "Before open", "amc": "After close", "dmh": "Midday"}


def _week_monday(d):
    """The Monday that starts d's calendar week."""
    from datetime import timedelta
    return d - timedelta(days=d.weekday())


def _iso_date(s):
    """Parse an ISO 'YYYY-MM-DD' string to a date; None on anything unparseable."""
    from datetime import date
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _index_soonest(rows, ticker_key, date_key, uni):
    """{ticker: {"d": date|None, "row": row}} keeping the soonest-dated row per
    universe ticker. Rows with no parseable date are kept only if the ticker has
    no dated row (so a date-less estimate still surfaces the bank)."""
    out: dict = {}
    for r in (rows or []):
        tk = (r.get(ticker_key) or "").upper()
        if not tk or tk not in uni:
            continue
        d = _iso_date(r.get(date_key))
        prev = out.get(tk)
        if prev is None or (d is not None and (prev["d"] is None or d < prev["d"])):
            out[tk] = {"d": d, "row": r}
    return out


def build_calls_agenda(yf_rows, fmp_rows, universe, call_info, today,
                       horizon_days: int = 75):
    """Merge the two upcoming-earnings sources into the Calls & Webcasts agenda.

    The yfinance snapshot (data.estimates.fetch_earnings_calendar) carries the
    accurate near-term report dates universe-wide; FMP's calendar adds before/
    after-open timing, the confirmed flag and the revenue estimate (and extends
    coverage to any bank yfinance is missing). Per universe ticker we take the
    soonest real date (yfinance preferred, FMP fallback) and overlay FMP's
    timing/confirmed/revenue and the parsed call info. Banks reporting in
    [today, today+horizon_days] are grouped into Monday-started weekly buckets.

    Pure / unit-tested. `universe` is any container of upper-case tickers;
    `call_info` is {ticker: {call_time, webcast_url, dial_in}} (may be empty).
    Every date / timing / estimate is a raw source value or None — the date is
    "confirmed" only when FMP says so (yfinance carries no confirmed flag), so
    callers mark unconfirmed dates as projected. Nothing is fabricated. Returns
    [] when nothing qualifies.

    Returns: [{"label": str, "week_start": "YYYY-MM-DD", "rows": [row, ...]}],
    week buckets ordered soonest-first; rows within a bucket ordered by date
    then ticker. Each row:
        {ticker, date, days_until, when, confirmed, eps_est, rev_est,
         period_ending, call_time, webcast_url, dial_in}
    """
    from datetime import timedelta

    uni = set(universe or ())
    ci_map = call_info or {}
    horizon = today + timedelta(days=horizon_days)

    yf_by_tk = _index_soonest(yf_rows, "ticker", "next_earnings_date", uni)
    fmp_by_tk = _index_soonest(fmp_rows, "symbol", "date", uni)

    seen: dict = {}
    for tk in set(yf_by_tk) | set(fmp_by_tk):
        yf = yf_by_tk.get(tk)
        fmp = fmp_by_tk.get(tk)
        yrow = (yf or {}).get("row", {})
        frow = (fmp or {}).get("row", {})
        ci = ci_map.get(tk) or {}
        # Release date: the bank's OWN announced date (parsed from its earnings PR
        # headline) is authoritative and confirmed; else the yfinance/FMP estimate.
        # The CALL is carried separately as call_date (often a different day, e.g.
        # report after close / call next morning) — never folded into the release.
        rel_d = _iso_date(ci.get("release_date")) if ci.get("release_date") else None
        if rel_d is not None and not (today <= rel_d <= horizon):
            rel_d = None
        d = rel_d or (yf or {}).get("d") or (fmp or {}).get("d")
        if d is None or d < today or d > horizon:
            continue
        eps = yrow.get("eps_estimate")
        if eps is None:
            eps = frow.get("epsEstimated")
        # Confirmed when the company announced the release date (rel_d), FMP
        # confirms it, or a published call event is consistent with it (same day,
        # or a few days before a next-morning call).
        call_d = _iso_date(ci.get("call_date")) if ci.get("call_date") else None
        confirmed = bool(rel_d) or bool(frow.get("confirmed")) or (
            call_d is not None and 0 <= (call_d - d).days <= 4)
        # Report timing: FMP's before/after-open code, else inferred — a call the
        # NEXT morning means the release went out after close the day before.
        when = _WHEN_LABEL.get((frow.get("time") or "").lower())
        if when is None and call_d is not None and (call_d - d).days == 1:
            when = "After close"
        seen[tk] = {
            "_date": d,
            "ticker": tk,
            "date": d.isoformat(),
            "days_until": (d - today).days,
            "when": when,
            "confirmed": confirmed,
            "eps_est": eps,
            "rev_est": frow.get("revenueEstimated"),
            "period_ending": frow.get("periodEnding"),
            "call_time": ci.get("call_time"),
            "call_date": ci.get("call_date"),   # announced call day (may differ)
            "webcast_url": ci.get("webcast_url"),
            "dial_in": ci.get("dial_in"),
        }

    rows = sorted(seen.values(), key=lambda x: (x["_date"], x["ticker"]))
    this_monday = _week_monday(today)
    buckets: dict = {}
    for row in rows:
        buckets.setdefault(_week_monday(row.pop("_date")), []).append(row)

    out = []
    for wk in sorted(buckets):
        if wk == this_monday:
            label = "This week"
        elif wk == this_monday + timedelta(days=7):
            label = "Next week"
        else:
            label = f"Week of {wk.isoformat()}"
        out.append({"label": label, "week_start": wk.isoformat(),
                    "rows": buckets[wk]})
    return out


def earnings_timing_map() -> dict:
    """{ticker: {"when": label, "confirmed": bool}} from FMP's earnings calendar
    for the next ~75 days — reliable, universe-wide report timing (before/after
    open) that the yfinance estimate lacks. ONE FMP call, CROSS-INSTANCE cached
    6h (cache.served_snapshot) so a cold Cloud Run instance — the common case
    under active deploys — reads the shared value instead of re-calling FMP (that
    re-call was a chunk of the Earnings/Home calendar's cold-load time). The Home
    calendar shows this when no precise PR/IR call time is available. Empty on
    failure (a genuine FMP failure raises out of build() so it is NOT cached)."""
    from datetime import date, timedelta
    from data import cache as _cache
    from data import fmp_client

    def _build() -> dict:
        today = date.today()
        rows = fmp_client.get_earnings_calendar(
            today.isoformat(), (today + timedelta(days=75)).isoformat())
        if rows is None:                            # FMP failure → don't cache
            raise RuntimeError("FMP earnings calendar unavailable")
        out: dict = {}
        for r in rows:
            tk = (r.get("symbol") or "").upper()
            if not tk or tk in out:                 # first (soonest) per symbol
                continue
            label = _WHEN_LABEL.get((r.get("time") or "").lower())
            if label:
                out[tk] = {"when": label, "confirmed": bool(r.get("confirmed"))}
        return out

    try:
        return _cache.served_snapshot("earnings_timing_map_v1", 21600, _build) or {}
    except Exception:
        return {}
