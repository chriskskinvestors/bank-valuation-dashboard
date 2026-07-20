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
from datetime import date, datetime

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
# PR-distribution wires: a release's own syndication URL sits near "webcast" text
# but is NOT the webcast (S&T's body links to prnewswire, not the event) — never
# surface one as the webcast link.
_WIRE_HOSTS = ("prnewswire.com", "businesswire.com", "globenewswire.com",
               "accesswire.com", "prweb.com", "newswire.com", "einpresswire.com",
               "einnews.com")

_PHONE_RE = re.compile(
    r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
_DIALIN_CUE_RE = re.compile(
    r"(?:dial[-\s]?in|toll[-\s]?free|by (?:tele)?phone|telephone|"
    r"to access the call|participants? (?:may|can|should) dial|"
    # bare imperative "dial (833) 461-5787" (SMBK 2026-07-20 — the cue only
    # scopes where the number is looked for, so this stays safe)
    r"\bdial(?:ing)?\b)", re.I)
_ID_RE = re.compile(
    # "Meeting ID: 208 155 555" — label variants + digit groups that may be
    # SPACE-SEPARATED (SMBK); inner whitespace normalized by the caller.
    r"(?:conference id|meeting id|passcode|access code|pass\s?code|"
    r"conference number|entry number)"
    r"[:#\s]*(\d(?:[\d ]{2,})\d)", re.I)


def _parse_call_time(text: str) -> str | None:
    """The conference-call time, normalized to a compact label like '10:00a ET'.
    Prefer a time-with-zone right after a call cue ("…conference call at 9:00 a.m.
    ET…") over the first time in the body — a scraped PR often leads with its
    publication timestamp ("June 30, 2026 7:29am EDT"), which is NOT the call time.
    Falls back to the first time-with-zone. None if absent."""
    m = None
    for cue in _CALL_CUE_RE.finditer(text or ""):
        m = _TIME_RE.search(text, cue.start(), cue.start() + 160)
        if m:
            break
    if not m:
        m = _TIME_RE.search(text or "")
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
        url = m.group(0).rstrip(".,;)]\"'")
        if any(w in url.lower() for w in _WIRE_HOSTS):
            continue                        # the PR's own wire link, not the webcast
        ctx = low[max(0, m.start() - 90): m.end() + 10]
        if any(cue in ctx for cue in _WEBCAST_CUES):
            return url
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
    if pid:
        code = re.sub(r"\s+", " ", pid.group(1).strip())
        return f"{num} (ID {code})"
    return num


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
        "when": _parse_release_timing(text),
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
_WD = r"(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+"   # "Thursday, " / "Fri "
_ON_DATE_RE = re.compile(
    r"\b(?:"
    r"(?:on|for)\s+(?:or\s+about\s+)?(?:" + _WD + r")?"    # "on [Thursday,] July 23, 2026"
    r"|" + _WD +                                           # or weekday-led: ", Thursday, July 23, 2026"
    r")"
    r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", re.I)
_ANNOUNCE_CUES = ("announce", "will report", "to report", "will release",
                  "to release", "schedule", "set date", "sets date",
                  "will host", "to host",
                  # HBCP 2026-07-20: "TO ISSUE 2026 SECOND QUARTER EARNINGS
                  # AND HOST CONFERENCE CALL" — "issue" is a release verb too.
                  "will issue", "to issue",
                  # SMBK-style "Sets Dates for..." already covered; the
                  # date-in-title form "Earnings Release Date" is its own cue.
                  "release date")
_EARNINGS_KW = ("results", "earnings")


def _is_earnings_announcement(headline: str) -> bool:
    """True for an UPCOMING-earnings announcement headline — an announcement cue
    plus an earnings/results keyword — whether or not it states the date inline
    ('… Announces Schedule for Second Quarter 2026 Results', '… Announces Earnings
    Release Date and Conference Call'). Used to pick which PRs to body-fetch; the
    date itself is then parsed from the body."""
    hl = (headline or "").lower()
    return (any(c in hl for c in _ANNOUNCE_CUES)
            and any(k in hl for k in _EARNINGS_KW))


_RELEASE_CUE_RE = re.compile(r"\b(?:release|report|announce|issue|publish)\b", re.I)


def _parse_release_date(text: str, today_iso: str) -> str | None:
    """Earnings-RELEASE date from a PR body — a future '… on <date>' shortly after
    a release/report cue (e.g. 'release its Q2 results on Thursday, July 23').
    None if not found."""
    if not text:
        return None
    for m in _RELEASE_CUE_RE.finditer(text):
        d = _parse_on_date(text[m.start():m.start() + 130])
        if d and d >= today_iso:
            return d
    return None


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


_CALL_CUE_RE = re.compile(
    r"(conference call|webcast|host[^.]{0,30}call|call to discuss)", re.I)


def _parse_call_date(text: str, today_iso: str) -> str | None:
    """The conference-CALL date from a PR body — a future '… on <Month> <Day>,
    <Year>' shortly after a call/webcast cue (e.g. 'host a conference call on
    July 15, 2026'). None if not found — never guessed."""
    if not text:
        return None
    for cue in _CALL_CUE_RE.finditer(text):
        d = _parse_on_date(text[cue.start():cue.start() + 170])
        if d and d >= today_iso:
            return d
    return None


# Report timing stated in the announcement itself ("… will report results after
# the market closes …" / "… before the market opens …"). This is the same
# Before-open/After-close signal as FMP's flag, but read straight from the bank's
# own PR — so it fills the calendar's "When" column for banks FMP doesn't cover.
_BEFORE_OPEN_RE = re.compile(
    r"before\s+(?:the\s+)?(?:u\.?s\.?\s+)?(?:market|markets?)?\s*open", re.I)
_AFTER_CLOSE_RE = re.compile(
    r"after\s+(?:the\s+)?(?:u\.?s\.?\s+)?(?:market|markets?)?\s*clos", re.I)


def _parse_release_timing(text: str) -> str | None:
    """'Before open' / 'After close' if the PR states when the release drops
    ('after the market closes', 'before the market opens'). None otherwise — the
    label matches _WHEN_LABEL's values so it slots straight into the When column."""
    if not text:
        return None
    if _BEFORE_OPEN_RE.search(text):
        return "Before open"
    if _AFTER_CLOSE_RE.search(text):
        return "After close"
    return None


def call_info_map() -> dict:
    """{ticker: {call_time, webcast_url, dial_in, call_date, release_date}} parsed
    from each bank's earnings-announcement press release.

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
                for k in ("call_time", "webcast_url", "dial_in", "when"):
                    if ci.get(k):
                        cur[k] = ci[k]
            # Conference-call date from the body (when stated up front).
            if not cur.get("call_date"):
                cd = _parse_call_date(r.get("summary") or "", today_iso)
                if cd:
                    cur["call_date"] = cd
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


def _fetch_pr_body(url: str) -> str:
    """Fetch a press-release DETAIL PAGE and return its readable text. <a> hrefs
    are inlined right after the link text so a real webcast link survives the
    tag-strip — but NON-content blocks (script/style/head/nav/header/footer/svg)
    are removed FIRST, since they carry the junk that polluted extraction: JSON-LD
    'schema.org' URLs in <script>, and menu links like /corporate-profile in nav.
    A generous length bound keeps the actual PR body (often deep below the page
    chrome) from being truncated away. '' on any failure."""
    try:
        from data.events.ir_site import _fetch
        html = _fetch(url, timeout=8)
    except Exception:
        html = None
    if not html:
        return ""
    html = re.sub(r"(?is)<(script|style|head|nav|header|footer|svg)\b.*?</\1>",
                  " ", html)
    text = re.sub(r'(?is)<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                  r"\2 \1 ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)[:40000]


def refresh_pr_call_snapshot(max_fetch: int = 200, max_workers: int = 8) -> dict:
    """Universe-wide, platform-agnostic conference-call details from each bank's
    earnings-ANNOUNCEMENT press release. Unlike the snippet parser, this fetches
    the PR's full DETAIL PAGE (its URL — from whatever wire/IR source ingested it)
    and parses the conference-call section: webcast link, dial-in, call time/date,
    plus the confirmed release date. Persists {ticker: {...}} as the cross-instance
    'pr_call_snap'. Background-only — the body fetches are far too many for the
    interactive path. {} on failure."""
    try:
        from data.events.store import get_events_by_type
        from data.events.wire_base import is_safe_news_url
    except Exception:
        return {}
    from concurrent.futures import ThreadPoolExecutor
    try:
        rows = get_events_by_type("earnings", limit=800)
    except Exception:
        return {}
    # Q4-hosted banks are handled cleanly by the Q4 snapshot (structured API body);
    # skip them here so the noisy HTML detail-page scrape (wrong times, junk
    # webcast URLs on multi-item pages) only runs for NON-Q4 banks.
    try:
        from data.events.ir_site import get_ir_endpoints
        q4_tickers = set(get_ir_endpoints())
    except Exception:
        q4_tickers = set()
    today_iso = date.today().isoformat()
    # One announcement PR per ticker — the newest earnings-announcement headline
    # (cue + results/earnings), even when the date isn't in the headline. Many
    # banks title it "Announces Schedule for Q2 Results" / "Announces Earnings
    # Release Date and Conference Call" and put the dates only in the body — which
    # is exactly why we fetch the body below.
    picked: dict = {}
    for r in rows:                                    # newest-first
        tk, url = r.get("ticker"), r.get("url")
        if not tk or not url or tk in picked or tk in q4_tickers:
            continue
        if (_is_earnings_announcement(r.get("headline") or "")
                or _parse_call_date(r.get("summary") or "", today_iso)):
            picked[tk] = r

    def _one(item):
        tk, r = item
        body = _fetch_pr_body(r.get("url") or "")
        if not body:
            return tk, None
        # Release date from the headline if stated there, else from the body.
        rd = (_announced_release_date(r.get("headline") or "", today_iso)
              or _parse_release_date(body, today_iso))
        cd = _parse_call_date(body, today_iso)
        # Stale-leak guard: no FUTURE date → this is a past-quarter results release,
        # not an upcoming-call announcement; don't surface its stale call logistics.
        if not (rd or cd):
            return tk, None
        ci = parse_call_info(body)
        wc = ci.get("webcast_url")
        if wc and not is_safe_news_url(wc):
            ci["webcast_url"] = None
        info = {k: v for k, v in ci.items() if v}
        info["release_date"] = rd
        info["call_date"] = cd
        info = {k: v for k, v in info.items() if v}
        return tk, (info or None)

    out: dict = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for tk, info in ex.map(_one, list(picked.items())[:max_fetch]):
            if info:
                out[tk] = info
    try:
        from data import cache
        cache.put("pr_call_snap",
                  {"value": out, "cached_at": datetime.now().isoformat()})
    except Exception as e:
        print(f"[earnings_call] could not cache PR call details: "
              f"{type(e).__name__}: {e}")
    print(f"[earnings_call] PR call details for {len(out)} banks", flush=True)
    return out


def get_pr_call_details() -> dict:
    """{ticker: {...}} from the nightly full-body PR call-detail snapshot. {}
    before it has run (degrades to the snippet parser)."""
    try:
        from data import cache
        snap = cache.get("pr_call_snap")
    except Exception:
        snap = None
    if snap and isinstance(snap.get("value"), dict):
        return snap["value"]
    return {}


def _fmp_announcement_infos(tickers, fetch_prs, is_subject, today_iso,
                            max_fetch: int = 120) -> dict:
    """Pure core of fmp_announcement_call_info: for upcoming-reporter
    tickers (soonest first), the newest ANNOUNCEMENT-shaped first-party PR
    (≤60 days old, subject-confirmed — FMP's symbol index is polluted for
    short tickers) parsed for call logistics and the announced release
    date. Wire feeds miss many of these announcements; FMP's press-release
    index aggregates them (discovery-only, links stay first-party)."""
    out: dict = {}
    fetched = 0
    today_d = _iso_date(today_iso)
    for tk in tickers:
        if fetched >= max_fetch:
            break
        fetched += 1
        try:
            prs = fetch_prs(tk) or []
        except Exception:
            continue
        for pr in prs:                                # newest-first
            title = pr.get("title") or ""
            if not _is_earnings_announcement(title):
                continue
            d = _iso_date(str(pr.get("published_at") or "")[:10])
            if d is None or today_d is None or (today_d - d).days > 60:
                continue
            blob = title + "\n" + (pr.get("text") or "")
            try:
                if not is_subject(tk, blob):
                    continue
            except Exception:
                continue
            ci = parse_call_info(blob)
            rd = (_announced_release_date(title, today_iso)
                  or _parse_release_date(blob, today_iso))
            if rd:
                ci["release_date"] = rd
            cd = _parse_call_date(blob, today_iso)
            if cd:
                ci["call_date"] = cd
            ci = {k: v for k, v in ci.items() if v}
            if ci:
                out[tk] = ci
            break                                     # newest announcement only
    return out


def fmp_announcement_call_info() -> dict:
    """{ticker: call info} for banks reporting in the next 14 days, mined
    from FMP's press-release index (full announcement-PR text — the wires'
    RSS coverage misses many). Per-ticker fetches ride fmp_client's own
    cache; the assembled map is 6h-cached. Empty on any failure."""
    import streamlit as st

    @st.cache_data(ttl=6 * 3600, show_spinner=False)
    def _build() -> dict:
        from concurrent.futures import ThreadPoolExecutor
        from datetime import timedelta
        try:
            from data import fmp_client
            from data.bank_universe import get_universe
            from data.events.fmp_news import _is_subject
            from data.events.wire_base import is_safe_news_url
        except Exception:
            return {}
        today = date.today()
        try:
            cal = fmp_client.get_earnings_calendar(
                today.isoformat(), (today + timedelta(days=14)).isoformat()) or []
            uni = {tk for tk, v in get_universe().items()
                   if (v or {}).get("share_class", "common") == "common"}
        except Exception:
            return {}
        # soonest report first, deduped
        seen, targets = set(), []
        for r in sorted(cal, key=lambda x: str(x.get("date") or "9999")):
            tk = (r.get("symbol") or "").upper()
            if tk in uni and tk not in seen:
                seen.add(tk)
                targets.append(tk)

        prs_by_tk: dict = {}

        def _fetch(tk):
            try:
                prs_by_tk[tk] = fmp_client.get_press_releases(tk, limit=15)
            except Exception:
                prs_by_tk[tk] = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(_fetch, targets[:120]))

        out = _fmp_announcement_infos(
            targets, lambda tk: prs_by_tk.get(tk), _is_subject,
            today.isoformat())
        for tk, ci in out.items():
            url = ci.get("webcast_url")
            if url and not is_safe_news_url(url):
                ci.pop("webcast_url", None)
        return out

    try:
        return _build()
    except Exception:
        return {}


def _release_call_infos(board_rows, today, fetch_release, is_safe,
                        cache_get, cache_put) -> dict:
    """Pure core of release_call_info_map: {ticker: info} for board rows that
    REPORTED within the last 2 days (never awaiting rows — their releases
    don't exist; never older rows — a prior quarter's call logistics are
    stale). Every returned info carries release_date = the board's report
    date, which the agenda treats as an announced-by-the-bank date →
    the calendar's ✓ instead of "(proj.)" for banks that have factually
    reported. Call logistics parse from the release body when present
    (banks state the report-day call, webcast and dial-in there — coverage
    the wires miss). Parsed once per (ticker, report date) via the cache
    callables; a fetch failure caches {} for the cycle but release_date
    still confirms."""
    out: dict = {}
    for r in board_rows or []:
        if r.get("awaiting"):
            continue
        d = _iso_date(r.get("date"))
        if d is None or (today - d).days > 2:
            continue
        tk = r.get("ticker")
        if not tk:
            continue
        ck = f"release_callinfo:v1:{tk}:{r['date']}"
        hit = cache_get(ck)
        if hit is not None:
            ci = dict(hit or {})
        else:
            ci = {}
            try:
                rel = fetch_release(tk)
                fd = _iso_date((rel or {}).get("filed_date"))
                if rel and fd is not None and 0 <= (fd - d).days <= 5:
                    from data.release_metrics import _flat_text
                    ci = parse_call_info(_flat_text(rel.get("html") or ""))
                    url = ci.get("webcast_url")
                    if url and not is_safe(url):
                        ci["webcast_url"] = None
                    ci = {k: v for k, v in ci.items() if v}
            except Exception:
                ci = {}
            cache_put(ck, ci)
        ci["release_date"] = r["date"]
        out[tk] = ci
    return out


def release_call_info_map() -> dict:
    """{ticker: {release_date, call_time?, webcast_url?, dial_in?, when?}}
    from the earnings RELEASES of banks that reported in the last two days
    (the Results board). 1h assembled-map cache; each release parsed once
    per report via the shared data cache. Empty on any failure."""
    import streamlit as st

    @st.cache_data(ttl=3600, show_spinner=False)
    def _build() -> dict:
        from datetime import datetime
        try:
            from data import cache as _cache
            from data.bank_mapping import get_cik
            from data.earnings_results import results_board
            from data.events.wire_base import is_safe_news_url
            from data.ir_provider import latest_earnings_release
        except Exception:
            return {}
        try:
            rows = results_board()
        except Exception:
            rows = []

        def _fetch(tk):
            cik = get_cik(tk)
            return latest_earnings_release(cik) if cik else None

        def _get(ck):
            try:
                v = _cache.get(ck)
                return (v or {}).get("value") if v is not None else None
            except Exception:
                return None

        def _put(ck, val):
            try:
                _cache.put(ck, {"cached_at": datetime.now().isoformat(),
                                "value": val})
            except Exception:
                pass

        return _release_call_infos(rows, date.today(), _fetch,
                                   is_safe_news_url, _get, _put)

    try:
        return _build()
    except Exception:
        return {}


def merged_call_info() -> dict:
    """{ticker: {call_time, webcast_url, dial_in, call_date, release_date}} from
    ALL call-detail sources, layered weakest→strongest:
      1. the snippet parser (call_info_map — the RSS description),
      2. the full-body PR snapshot (get_pr_call_details — the PR detail page),
      3. the Q4 IR snapshot (get_q4_call_details — clean PR-API body + event:
         dates/time/webcast/dial-in, the most reliable for Q4-hosted banks),
      4. the curated megabank webcast map (get_curated_call_info — webcast link
         ONLY, for the bespoke non-Q4 megabanks whose link the scrape can't reach
         reliably; these tickers aren't on Q4, so it never collides with layer 3).
    Later layers overwrite earlier ones field-by-field. Used by the Calendar
    agenda and the Home calendar."""
    base = {tk: dict(info) for tk, info in (call_info_map() or {}).items()}

    def _overlay(src, keys):
        for tk, info in (src or {}).items():
            cur = dict(base.get(tk) or {})
            for k in keys:
                if info.get(k):
                    cur[k] = info[k]
            base[tk] = cur

    # FMP's press-release index (upcoming reporters): full announcement-PR
    # text the wires' RSS coverage misses — discovery-only, links first-party.
    _overlay(fmp_announcement_call_info(),
             ("call_time", "webcast_url", "dial_in", "call_date",
              "release_date", "when"))
    _overlay(get_pr_call_details(),
             ("call_time", "webcast_url", "dial_in", "call_date", "release_date",
              "when"))
    # The bank's own earnings RELEASE (banks that reported ≤2 days ago): its
    # existence makes the date a fact — ✓, never "(proj.)" — and its body
    # states the report-day call/webcast/dial-in the wires often miss.
    _overlay(release_call_info_map(),
             ("call_time", "webcast_url", "dial_in", "release_date", "when"))
    try:
        from data.events.ir_site import get_q4_call_details
        q4 = get_q4_call_details()
    except Exception:
        q4 = {}
    _overlay(q4, ("call_time", "webcast_url", "call_date", "dial_in",
                  "release_date", "when"))
    # Curated megabank webcast links last: only the webcast_url field, only for
    # the non-Q4 megabanks (so it overrides the junk webcast the HTML scrape
    # produces for them, and never touches a date/time or a Q4 bank).
    try:
        from data.events.ir_site import get_curated_call_info
        _overlay(get_curated_call_info(), ("webcast_url",))
    except Exception:
        pass
    return base


# FMP's report-time code → human label. bmo = before market open, amc = after
# market close, dmh = during market hours.
_WHEN_LABEL = {"bmo": "Before open", "amc": "After close", "dmh": "Midday"}


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
    [today, today+horizon_days] are grouped into per-DAY buckets.

    Pure / unit-tested. `universe` is any container of upper-case tickers;
    `call_info` is {ticker: {call_time, webcast_url, dial_in}} (may be empty).
    Every date / timing / estimate is a raw source value or None — the date is
    "confirmed" only when FMP says so (yfinance carries no confirmed flag), so
    callers mark unconfirmed dates as projected. Nothing is fabricated. Returns
    [] when nothing qualifies.

    Returns: [{"label": str, "date": "YYYY-MM-DD", "rows": [row, ...]}],
    one bucket per report DAY ordered soonest-first (label "Today"/"Tomorrow"/
    "Thu, Jul 16"); rows within a day ordered by ticker. Each row:
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
        call_time = ci.get("call_time")
        dial_in = ci.get("dial_in")
        # A call date wildly inconsistent with the report date is stale/mis-parsed
        # (e.g. next year's Q1 call scraped onto this quarter — PNC showed "Apr 15"
        # on a Jul 15 row). A real earnings call sits within a few days of the
        # release, so drop the call date/time/dial-in when it doesn't fit.
        if call_d is not None and not (-2 <= (call_d - d).days <= 7):
            call_d = call_time = dial_in = None
        confirmed = bool(rel_d) or bool(frow.get("confirmed")) or (
            call_d is not None and 0 <= (call_d - d).days <= 4)
        # Report timing: FMP's before/after-open code; else the timing the bank
        # stated in its own announcement ("after the market closes"); else inferred
        # — a call the NEXT morning means the release went out after close.
        when = _WHEN_LABEL.get((frow.get("time") or "").lower())
        if when is None:
            when = ci.get("when")
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
            "call_time": call_time,
            "call_date": call_d.isoformat() if call_d else None,  # gated: stale dropped
            "webcast_url": ci.get("webcast_url"),
            "dial_in": dial_in,
        }

    rows = sorted(seen.values(), key=lambda x: (x["_date"], x["ticker"]))
    buckets: dict = {}
    for row in rows:
        buckets.setdefault(row.pop("_date"), []).append(row)

    out = []
    for day in sorted(buckets):
        delta = (day - today).days
        if delta == 0:
            label = "Today"
        elif delta == 1:
            label = "Tomorrow"
        else:
            label = day.strftime("%a, %b ") + str(day.day)   # "Thu, Jul 16"
        out.append({"label": label, "date": day.isoformat(),
                    "rows": buckets[day]})
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
        # v2: v1 snapshots were built from field-less rows (no includeReportTimes).
        return _cache.served_snapshot("earnings_timing_map_v2", 21600, _build) or {}
    except Exception:
        return {}
