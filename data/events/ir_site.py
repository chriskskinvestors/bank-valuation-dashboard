"""
Investor Relations site scraper (watchlist-only, generic).

This is a best-effort source: IR pages have no standard format, so we
can't reliably extract structured press releases from arbitrary sites.
The strategy:

  1. Use the IR_URLS map from bank_mapping.py (already populated for
     watchlist banks).
  2. Try common URL patterns: append /news, /press-releases, /news/press-releases.
  3. Parse HTML, find <a> tags whose text looks like a headline + has a
     plausible date nearby (in URL or nearby text).
  4. Emit as low-confidence Event objects. Dedupe by URL.

When a bank uses a known IR vendor (Q4 Inc, Investorroom, etc.) the same
selectors often work. For one-offs we accept misses — wire feeds + 8-K
catch ~98% of material releases anyway.

This adapter is intentionally conservative: better to return zero
events than to flood the Activity tab with non-press-release links
(navigation, footer, etc.).
"""

from __future__ import annotations
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse

import requests

from data.bank_mapping import BANK_MAP, IR_URLS
from data.events.base import Event, SourceAdapter
from data.events.wire_base import classify_press_release


# URL paths to try off of the IR home page (in order of preference). Covers the
# common IR-platform conventions (Q4, Issuer Direct, EQS, etc.) — e.g. Capital
# Bank lists under /news-releases, which the original short list missed.
_PRESS_PAGE_CANDIDATES = [
    "/news-releases",
    "/news/press-releases",
    "/news/news-releases",
    "/press-releases",
    "/investor-news",
    "/news-events/press-releases",
    "/news-and-events/news",
    "/investors/news",
    "/investor-relations/news",
    "/news",
    "/newsroom",
    "/press",
    "/news-events",
    "/news-and-events",
    "",  # IR home itself sometimes lists releases
]

# Heuristics for "is this a press release link?"
_PR_URL_PATTERNS = re.compile(
    r"(press[-_]?release|news[-_]?release|news[-_]?details|news/[0-9]{4}|"
    r"news/\d+|story|article)",
    re.IGNORECASE,
)

# Lower-case substrings that mark a link as navigational. If any of these
# appears in the link text, reject it. Case-insensitive substring beats
# exact match because IR sites have many variants of "Skip to main content".
_NAV_REJECT_SUBSTRINGS = {
    "skip to", "skip nav", "back to top", "view all", "see all", "read more",
    "learn more", "click here", "sign up", "sign in", "log in", "login",
    "subscribe", "contact us", "about us", "menu", "search", "newsletter",
    "privacy policy", "terms of", "cookie", "sitemap", "site map",
    "investor relations", "press releases", "all news", "all press releases",
    "next page", "previous page", "main content", "site navigation",
    "press kit", "media kit", "media inquir", "investor contact",
    "rss feed", "email alerts", "social media",
}

# Plausible date patterns inside link text or URL
_DATE_PATTERN = re.compile(
    r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b|"
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(0?[1-9]|[12]\d|3[01]),?\s+(20\d{2})\b",
    re.IGNORECASE,
)


def _parse_date_in_text(text: str) -> datetime | None:
    """Try to extract a date from a string. Returns UTC datetime or None."""
    m = _DATE_PATTERN.search(text or "")
    if not m:
        return None
    if m.group(1):  # YYYY-MM-DD form
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            tzinfo=timezone.utc)
        except ValueError:
            return None
    # Month-name form
    months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
              "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
    try:
        mon = months.get(m.group(4)[:3].lower())
        if mon:
            return datetime(int(m.group(6)), mon, int(m.group(5)),
                            tzinfo=timezone.utc)
    except (ValueError, IndexError):
        pass
    return None


def _is_plausible_press_link(href: str, text: str) -> bool:
    """Quick filter — does this <a> look like a press release link?"""
    if not href or not text:
        return False
    text = text.strip()
    if len(text) < 25 or len(text) > 300:
        # Real press release headlines are usually 25+ chars
        return False
    if href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return False
    text_lc = text.lower()
    if any(nav in text_lc for nav in _NAV_REJECT_SUBSTRINGS):
        return False
    # Either the URL has a press-release pattern (high confidence) OR the
    # link text + URL together look like a press release (medium confidence).
    has_pr_url = bool(_PR_URL_PATTERNS.search(href))
    if has_pr_url:
        return True
    # Without a press-release-pattern URL, require headline-like text AND
    # a plausible date somewhere in the URL or text — eliminates navigation.
    has_date = bool(_DATE_PATTERN.search(href) or _DATE_PATTERN.search(text))
    if not has_date:
        return False
    words = text.split()
    return (len(words) >= 5
            and text[0].isupper()
            and not text_lc.startswith(("view ", "read ", "more ", "see ", "go ")))


def _extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """
    Extract (absolute_url, text) tuples from <a> tags using regex only —
    avoids adding a BeautifulSoup dependency just for this. Quick and dirty
    but works for typical IR page structures.
    """
    out: list[tuple[str, str]] = []
    # <a ... href="..." ...>text</a>
    for m in re.finditer(
        r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        html, re.IGNORECASE | re.DOTALL,
    ):
        href = m.group(1)
        # Strip nested HTML inside link text
        text = re.sub(r"<[^>]+>", " ", m.group(2))
        text = re.sub(r"\s+", " ", text).strip()
        absolute = urljoin(base_url, href)
        out.append((absolute, text))
    return out


def _fetch(url: str, timeout: int = 12) -> str | None:
    try:
        resp = requests.get(
            url, timeout=timeout,
            headers={"User-Agent": "BankValuationDashboard (chris@kskinvestors.com)"},
            allow_redirects=True,
        )
        if resp.status_code == 200 and "text/html" in resp.headers.get("Content-Type", ""):
            return resp.text
    except Exception:
        return None
    return None


# ── Q4 Inc IR platform (JSON API) ────────────────────────────────────────
# A large share of bank IR sites run on Q4 Inc, which renders press releases
# CLIENT-SIDE from a JSON API — so the HTML scraper above finds nothing (the
# page ships no release links). But the same endpoint the page's JS calls is a
# plain GET we can hit directly: <host>/feed/PressRelease.svc/GetPressReleaseList
# with the site's apiKey (embedded in the page). This is first-party, fresh, and
# unblocked — the BEST source — and the apiKey/endpoint pattern is identical
# across every Q4-hosted site, so one path covers them all.
_Q4_APIKEY_RE = re.compile(r"apiKey['\"\s:=]+([0-9A-Fa-f]{24,40})")
_Q4_MARKER_RE = re.compile(r"q4cdn|q4inc|q4app|q4web", re.IGNORECASE)


def _parse_q4_date(s: str) -> datetime | None:
    """Q4 dates read 'MM/DD/YYYY HH:MM:SS' (naive ET-ish). Treat as UTC — the
    multi-day lookback makes a few hours' drift immaterial."""
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime((s or "").strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _q4_site(ir_home: str) -> tuple[bool, str | None]:
    """(is_q4, apiKey) for an IR home. is_q4 ← the homepage carries a Q4 marker
    (q4cdn/q4inc/…); apiKey is extracted ONLY when inlined. Modern Q4 sites omit
    the key from the HTML — their JSON feeds serve keyless — so a missing key must
    NOT be read as 'not a Q4 site'. That conflation was the bug that hid every
    keyless Q4 bank (e.g. EQBK) from discovery and from press-release polling.

    Cached per host 7d; (False, None) records a known non-Q4 site so we don't
    refetch its homepage every cycle."""
    host = urlparse(ir_home).netloc
    ck = f"q4_site:{host}"
    cache = None
    try:
        from data import cache as _c
        from data.freshness import is_fresh
        cache = _c
        hit = _c.get(ck)
        if hit and is_fresh(hit, 7 * 24 * 3600):
            return bool(hit.get("is_q4")), (hit.get("apiKey") or None)
    except Exception:
        pass
    html = _fetch(ir_home, timeout=5)
    is_q4 = bool(html and _Q4_MARKER_RE.search(html))
    key = ""
    if is_q4:
        m = _Q4_APIKEY_RE.search(html)
        if m:
            key = m.group(1)
    try:
        if cache is not None:
            cache.put(ck, {"is_q4": is_q4, "apiKey": key,
                           "cached_at": datetime.now().isoformat()})
    except Exception:
        pass
    return is_q4, (key or None)


def _q4_apikey(ir_home: str) -> str | None:
    """Back-compat shim: the Q4 site's apiKey (or None). Prefer _q4_site() when you
    also need the is-Q4 signal — a keyless Q4 site returns (True, None) there."""
    return _q4_site(ir_home)[1]


def _q4_press_releases(ir_home: str, cutoff: datetime) -> list[tuple[str, str, datetime]] | None:
    """If `ir_home` is a Q4 site, return [(url, headline, published)] from its
    PressRelease JSON API. Returns None when it's NOT a Q4 site or the API can't
    be reached — the caller then falls back to HTML scraping. An empty list means
    'Q4 site, nothing recent' (no fallback needed)."""
    is_q4, key = _q4_site(ir_home)
    if not is_q4:
        return None
    parsed = urlparse(ir_home)
    host = f"{parsed.scheme}://{parsed.netloc}"
    params = {
        "apiKey": key or "", "LanguageId": 1, "bodyType": 0,
        "pressReleaseDateFilter": 3, "categoryId": "", "tagList": "",
        "includeTags": "true", "year": 0, "excludeSelection": 1,
        "pageSize": 20, "pageNumber": 0,
    }
    try:
        resp = requests.get(
            host + "/feed/PressRelease.svc/GetPressReleaseList",
            params=params, timeout=10,
            headers={"User-Agent": "BankValuationDashboard (chris@kskinvestors.com)",
                     "Accept": "application/json"},
        )
        resp.raise_for_status()
        items = resp.json().get("GetPressReleaseListResult") or []
    except Exception:
        return None
    out: list[tuple[str, str, datetime]] = []
    for it in items:
        headline = (it.get("Headline") or "").strip()
        link = it.get("LinkToDetailPage") or it.get("LinkToUrl") or ""
        if not headline or not link:
            continue
        pub = _parse_q4_date(it.get("PressReleaseDate")) or datetime.now(timezone.utc)
        if pub < cutoff:
            continue
        out.append((urljoin(host, link), headline, pub))
    return out


# ── Universe-wide Q4 IR discovery ────────────────────────────────────────
# Most banks aren't in the curated IR_URLS map, but ~a third run a Q4 IR site
# reachable at a standard subdomain off their FDIC website. Discover those
# nightly and cache {ticker: q4_ir_url} so the poll adapter covers them too.
_IR_SUBDOMAINS = ("investorrelations", "ir", "investors", "investor")
_IR_ENDPOINTS_CACHE_KEY = "ir_q4_endpoints"


def _domain_root(webaddr: str) -> str:
    """Bare registrable domain from an FDIC WEBADDR ("www.provident.bank/" ->
    "provident.bank"); "" if it doesn't look like a domain."""
    d = re.sub(r"^\s*https?://", "", (webaddr or "").strip().lower())
    d = d.split("/")[0].strip().strip(".")
    if d.startswith("www."):
        d = d[4:]
    return d if ("." in d and " " not in d) else ""


def discover_q4_ir_url(webaddr: str) -> str | None:
    """Find a bank's Q4 IR site from its website. Two methods: (1) probe standard
    IR subdomains (investorrelations./ir./investors./investor.<domain>); (2) if
    none hit, pull the "Investor Relations" link off the bank's main page and
    check it (catches Q4 sites at non-standard URLs, e.g. Zions). Returns the Q4
    home URL (apiKey cache warmed) or None. DNS misses fail fast."""
    root = _domain_root(webaddr)
    if not root:
        return None
    # Method 1 — subdomain probe (cheap; DNS miss is instant).
    for sub in _IR_SUBDOMAINS:
        url = f"https://{sub}.{root}/"
        try:
            if _q4_site(url)[0]:
                return url
        except Exception:
            continue
    # Method 2 — follow the main site's investor-relations link.
    try:
        html = _fetch(f"https://{root}/", timeout=6)
        if html:
            seen = set()
            for href in re.findall(r'href="([^"]+)"', html):
                if not re.search(r"investor", href, re.IGNORECASE):
                    continue
                url = urljoin(f"https://{root}/", href)
                if url in seen:
                    continue
                seen.add(url)
                if _q4_site(url)[0]:
                    return url
                if len(seen) >= 4:   # bound: don't chase every investor-ish link
                    break
    except Exception:
        pass
    return None


def refresh_q4_ir_endpoints(universe: dict | None = None,
                            max_workers: int = 16) -> dict[str, str]:
    """Discover each universe bank's Q4 IR endpoint and cache {ticker: url}.
    Run nightly (from refresh-universe). Curated IR_URLS are kept as-is (and
    their Q4 key warmed); other banks are probed off their FDIC webaddr.
    Pass the freshly-built `universe` dict to avoid any snapshot-cache staleness."""
    from concurrent.futures import ThreadPoolExecutor
    if universe is None:
        from data.bank_universe import get_universe
        universe = get_universe()
    uni = universe

    def _disc(item):
        tk, info = item
        curated = IR_URLS.get(tk)
        if curated:
            try:
                _q4_site(curated)   # warm the Q4 detection/key cache
            except Exception:
                pass
            return tk, curated
        try:
            return tk, discover_q4_ir_url(info.get("webaddr") or "")
        except Exception:
            return tk, None

    found: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for tk, url in ex.map(_disc, list(uni.items())):
            if url:
                found[tk] = url
    try:
        from data import cache
        cache.put(_IR_ENDPOINTS_CACHE_KEY,
                  {"endpoints": found, "cached_at": datetime.now().isoformat()})
    except Exception as e:
        print(f"[ir] could not cache endpoints: {type(e).__name__}: {e}")
    n_probed = sum(1 for t in found if t not in IR_URLS)
    print(f"[ir] discovered {len(found)} IR endpoints ({n_probed} via webaddr probe)",
          flush=True)
    return found


def get_ir_endpoints() -> dict[str, str]:
    """Curated IR_URLS merged with the nightly-discovered Q4 endpoints — the full
    set the adapter polls. Falls back to just IR_URLS before discovery has run."""
    endpoints = dict(IR_URLS)
    try:
        from data import cache
        cached = (cache.get(_IR_ENDPOINTS_CACHE_KEY) or {}).get("endpoints") or {}
        endpoints.update(cached)
    except Exception:
        pass
    return endpoints


# ── Q4 events-and-presentations: upcoming earnings-CALL details ──────────────
# Press releases rarely carry the webcast link / call time in a parseable form;
# the structured details live on the IR site's events page (the Q4 Event API).
# We already discover each bank's Q4 IR home, so we can pull its next earnings
# call's date, time and webcast link directly.
_Q4_CALLS_SNAP_KEY = "q4_calls_snap"


def _q4_call_time(start_str: str, tz: str | None) -> str | None:
    """Format a Q4 StartDate ('MM/DD/YYYY HH:MM:SS', local) + TimeZone into a
    compact label like '9:00a ET'. None when there's no real time-of-day (a
    midnight stamp means the event carries only a date)."""
    dt = _parse_q4_date(start_str)
    if dt is None or (dt.hour == 0 and dt.minute == 0):
        return None
    h = dt.hour % 12 or 12
    label = f"{h}:{dt.minute:02d}{'a' if dt.hour < 12 else 'p'}"
    return f"{label} {tz}".strip() if tz else label


def _q4_events(ir_home: str) -> list[dict] | None:
    """Upcoming EARNINGS-CALL events from a Q4 IR site's Event API, or None when
    it isn't a Q4 site / the API can't be reached. Each:
    {start: datetime, call_time: str|None, webcast_url: str|None, detail_url}."""
    parsed = urlparse(ir_home)
    host = f"{parsed.scheme}://{parsed.netloc}"
    if not host.startswith("http"):
        return None
    is_q4, key = _q4_site(ir_home)     # the Event feed also serves keyless
    if not is_q4:
        return None
    params = {
        "apiKey": key or "", "LanguageId": 1, "eventDateFilter": 1,  # 1 = upcoming
        "pageSize": 20, "pageNumber": 0, "tagList": "", "includeTags": "true",
    }
    try:
        resp = requests.get(
            host + "/feed/Event.svc/GetEventList", params=params, timeout=10,
            headers={"User-Agent": "BankValuationDashboard (chris@kskinvestors.com)",
                     "Accept": "application/json"})
        resp.raise_for_status()
        items = resp.json().get("GetEventListResult") or []
    except Exception:
        return None
    out: list[dict] = []
    for it in items:
        # Title is often blank on these rows; SeoName carries the event name.
        name = (((it.get("SeoName") or "").replace("-", " ")) + " "
                + (it.get("Title") or "")).strip().lower()
        if "earnings" not in name and "conference call" not in name:
            continue                    # presentations / investor days etc. — skip
        start = _parse_q4_date(it.get("StartDate"))
        if start is None:
            continue
        web = (it.get("WebCastLink") or "").strip()
        if web and not web.lower().startswith("http"):
            web = ""
        link = it.get("LinkToDetailPage") or ""
        out.append({
            "start": start,
            "call_time": _q4_call_time(it.get("StartDate"), it.get("TimeZone")),
            "webcast_url": web or None,
            "detail_url": urljoin(host, link) if link else None,
        })
    return out


def refresh_q4_calls_snapshot(universe: dict | None = None, max_workers: int = 12,
                              horizon_days: int = 120) -> dict[str, dict]:
    """Pull each Q4-hosted bank's NEXT earnings call (date, time, webcast link)
    and persist {ticker: {call_date, call_time, webcast_url, detail_url}} as the
    cross-instance snapshot the Calls & Webcasts agenda overlays. Background-only
    (nightly refresh-universe) — it's one HTTP call per Q4 bank (~a third of the
    universe), far too slow for the interactive path."""
    from concurrent.futures import ThreadPoolExecutor
    from data.events.wire_base import is_safe_news_url

    ir_map = get_ir_endpoints()
    if universe:
        keep = set(universe)
        ir_map = {t: u for t, u in ir_map.items() if t in keep} or ir_map
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=horizon_days)
    grace = now - timedelta(days=1)     # tolerate a call stamped earlier today

    def _one(item):
        tk, url = item
        try:
            evs = _q4_events(url)
        except Exception:
            return tk, None
        upcoming = sorted((e for e in (evs or []) if grace <= e["start"] <= horizon),
                          key=lambda e: e["start"])
        if not upcoming:
            return tk, None
        e = upcoming[0]
        web = e["webcast_url"] if (e["webcast_url"] and is_safe_news_url(e["webcast_url"])) else None
        if not (e["call_time"] or web):
            return tk, None             # nothing worth overlaying
        return tk, {"call_date": e["start"].date().isoformat(),
                    "call_time": e["call_time"], "webcast_url": web,
                    "detail_url": e["detail_url"]}

    found: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for tk, info in ex.map(_one, list(ir_map.items())):
            if info:
                found[tk] = info
    try:
        from data import cache
        cache.put(_Q4_CALLS_SNAP_KEY,
                  {"value": found, "cached_at": datetime.now().isoformat()})
    except Exception as e:
        print(f"[ir] could not cache q4 call details: {type(e).__name__}: {e}")
    print(f"[ir] q4 call details for {len(found)} banks", flush=True)
    return found


def get_q4_call_details() -> dict[str, dict]:
    """{ticker: {call_date, call_time, webcast_url, detail_url}} from the nightly
    Q4 events snapshot. {} before the job has run (degrades to no call details,
    never blocks)."""
    try:
        from data import cache
        snap = cache.get(_Q4_CALLS_SNAP_KEY)
    except Exception:
        snap = None
    if snap and isinstance(snap.get("value"), dict):
        return snap["value"]
    return {}


class IRSiteAdapter(SourceAdapter):
    """Generic IR-page scraper. Watchlist-only."""

    name = "ir_site"
    LOOKBACK_DAYS = 30  # Looser since dates on IR pages aren't always reliable
    MAX_POLL_SECONDS = 150  # internal budget; return partial well before the 240s cap

    def poll(self, tickers: list[str], since: datetime | None = None) -> list[Event]:
        import time as _t
        from data.events.wire_base import is_junk_news, is_safe_news_url
        cutoff = since or (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS))
        out: list[Event] = []
        seen_urls: set[str] = set()
        deadline = _t.monotonic() + self.MAX_POLL_SECONDS

        def _emit(ticker, url, headline, pub, raw):
            if not url or url in seen_urls or not is_safe_news_url(url):
                return
            if is_junk_news(headline, ticker):
                return
            seen_urls.add(url)
            out.append(Event(
                ticker=ticker, source=self.name,
                event_type=classify_press_release(headline),
                headline=headline[:300], published_at=pub, url=url,
                summary="", external_id=url, raw=raw))

        # Cover EVERY bank with an IR site — the curated IR_URLS PLUS the
        # nightly-discovered Q4 endpoints (so ~130 banks, not just the 54
        # curated). Passed watchlist first.
        ir_map = get_ir_endpoints()
        passed = [t for t in tickers if t in ir_map]
        order = passed + sorted(set(ir_map) - set(passed))

        # PHASE 1 — the cheap Q4 JSON API for every site FIRST. This is where the
        # first-party releases the wires/EDGAR miss live (e.g. PFS). Run it in a
        # thread pool: these are 54 independent I/O-bound fetches to DIFFERENT
        # hosts, so concurrency collapses cold-cache time from minutes to ~30s and
        # GUARANTEES every Q4 bank (incl. PFS) is reached well within budget —
        # before any slow HTML scrape (the bug that left PFS at 0 fetched, then
        # timed the whole adapter out and discarded everything).
        from concurrent.futures import (ThreadPoolExecutor, as_completed,
                                         TimeoutError as _FTimeout)
        non_q4: list[tuple[str, str]] = []

        def _q4(t):
            return _q4_press_releases(ir_map[t], cutoff)

        # HARD-bounded: collect whatever finishes within the budget and ABANDON
        # stragglers (cancel_futures) — a slow/hung IR host (or cache call) can no
        # longer make the whole adapter overrun poll-events' 240s cap, which would
        # discard every event incl. PFS. PFS is a fast Q4 fetch, so its future
        # resolves early regardless of slow siblings.
        ex = ThreadPoolExecutor(max_workers=8)
        futs = {ex.submit(_q4, t): t for t in order}
        done_tickers: set[str] = set()
        try:
            for fut in as_completed(futs, timeout=self.MAX_POLL_SECONDS):
                ticker = futs[fut]
                done_tickers.add(ticker)
                ir_home = ir_map[ticker]
                try:
                    q4 = fut.result()
                except Exception as e:
                    print(f"[ir_site] {ticker} q4 error: {type(e).__name__}: {e}")
                    q4 = None
                if q4 is None:
                    non_q4.append((ticker, ir_home))   # defer to the HTML phase
                    continue
                for url, headline, pub in q4:
                    _emit(ticker, url, headline, pub, {"ir_home": ir_home, "platform": "q4"})
        except _FTimeout:
            print(f"[ir_site] Q4 phase budget hit — {len(done_tickers)}/{len(order)} "
                  "sites checked; rest catch up next cycle")
        ex.shutdown(wait=False, cancel_futures=True)

        # PHASE 2 — best-effort HTML scrape for non-Q4 sites with leftover budget.
        # Each scrape is deadline-aware (a single slow site can't run its full
        # 14-path × per-fetch budget and blow poll-events' 240s cap, which would
        # discard EVERY event including Phase 1's PFS release). Whatever doesn't
        # fit catches up next cycle.
        for ticker, ir_home in non_q4:
            # Need comfortable headroom for at least one fetch; else stop here.
            if _t.monotonic() > deadline - 10:
                print(f"[ir_site] budget reached — {len(out)} events; "
                      f"{len(non_q4)} HTML sites deferred to next cycle")
                break
            try:
                links = self._find_press_links(ir_home, deadline)
            except Exception as e:
                print(f"[ir_site] {ticker} error: {type(e).__name__}: {e}")
                continue
            for url, text in links:
                pub = _parse_date_in_text(url) or _parse_date_in_text(text)
                if pub and pub < cutoff:
                    continue
                _emit(ticker, url, text, pub or datetime.now(timezone.utc),
                      {"ir_home": ir_home, "date_inferred": pub is None})
        return out

    def _find_press_links(self, ir_home: str,
                          deadline: float | None = None) -> list[tuple[str, str]]:
        """Try the IR home + common press-release subpaths, return plausible links.
        Stops early when `deadline` (time.monotonic seconds) is reached so a slow
        site can't run all 14 paths and overrun the poll's per-adapter cap."""
        import time as _t
        # Get the host root for path-joining (handle https://ir.example.com/sub/)
        parsed = urlparse(ir_home)
        host = f"{parsed.scheme}://{parsed.netloc}"

        results: list[tuple[str, str]] = []
        seen_hrefs: set[str] = set()

        for path in _PRESS_PAGE_CANDIDATES:
            if deadline is not None and _t.monotonic() > deadline:
                break
            candidate = ir_home if path == "" else (ir_home.rstrip("/") + path)
            html = _fetch(candidate, timeout=6)
            if not html:
                continue
            for href, text in _extract_links(html, candidate):
                if href in seen_hrefs:
                    continue
                if not _is_plausible_press_link(href, text):
                    continue
                # Only keep same-host links; off-site links from IR pages
                # are usually nav (legal, careers, etc.).
                if urlparse(href).netloc and urlparse(href).netloc != parsed.netloc:
                    continue
                seen_hrefs.add(href)
                results.append((href, text))

            # If we found stuff at this path, stop trying others — they
            # usually lead to the same content with different framing.
            if results:
                break

        return results
