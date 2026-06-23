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


def _q4_press_releases(ir_home: str, cutoff: datetime) -> list[tuple[str, str, datetime]] | None:
    """If `ir_home` is a Q4 site, return [(url, headline, published)] from its
    PressRelease JSON API. Returns None when it's NOT a Q4 site or the API can't
    be reached — the caller then falls back to HTML scraping. An empty list means
    'Q4 site, nothing recent' (no fallback needed)."""
    html = _fetch(ir_home)
    if not html:
        return None
    key_m = _Q4_APIKEY_RE.search(html)
    if not key_m or not _Q4_MARKER_RE.search(html):
        return None  # not a Q4 site
    parsed = urlparse(ir_home)
    host = f"{parsed.scheme}://{parsed.netloc}"
    params = {
        "apiKey": key_m.group(1), "LanguageId": 1, "bodyType": 0,
        "pressReleaseDateFilter": 3, "categoryId": "", "tagList": "",
        "includeTags": "true", "year": 0, "excludeSelection": 1,
        "pageSize": 20, "pageNumber": 0,
    }
    try:
        resp = requests.get(
            host + "/feed/PressRelease.svc/GetPressReleaseList",
            params=params, timeout=15,
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


class IRSiteAdapter(SourceAdapter):
    """Generic IR-page scraper. Watchlist-only."""

    name = "ir_site"
    LOOKBACK_DAYS = 30  # Looser since dates on IR pages aren't always reliable
    MAX_POLL_SECONDS = 200  # internal budget; return partial before poll-events' cap

    def poll(self, tickers: list[str], since: datetime | None = None) -> list[Event]:
        import time as _t
        from data.events.wire_base import is_junk_news, is_safe_news_url
        cutoff = since or (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS))
        out: list[Event] = []
        seen_urls: set[str] = set()

        # Cover EVERY bank with a mapped IR site, not just the passed scope — the
        # Q4 API is per-bank first-party news the wires/EDGAR often miss, and many
        # mapped banks (e.g. PFS) aren't in the watchlist this adapter is handed.
        # Passed tickers first so the active watchlist is always covered; the rest
        # fill in until the internal budget, then continue next cycle.
        passed = [t for t in tickers if t in IR_URLS]
        order = passed + sorted(set(IR_URLS) - set(passed))
        # Bounded so a slow HTML site can't blow the poll-events per-adapter cap —
        # which would abandon the WHOLE adapter and commit nothing (the prior
        # ir_site behaviour). Returning partial means every run makes progress.
        deadline = _t.monotonic() + self.MAX_POLL_SECONDS
        for ticker in order:
            if _t.monotonic() > deadline:
                print(f"[ir_site] budget reached after {len(out)} events — "
                      "rest catches up next cycle")
                break
            ir_home = IR_URLS.get(ticker)
            if not ir_home:
                continue

            # Q4 JSON API first (real data on JS-rendered IR sites); fall back to
            # HTML link-scraping for non-Q4 platforms.
            q4 = None
            try:
                q4 = _q4_press_releases(ir_home, cutoff)
            except Exception as e:
                print(f"[ir_site] {ticker} q4 error: {type(e).__name__}: {e}")
            if q4 is not None:
                for url, headline, pub in q4:
                    if url in seen_urls or not is_safe_news_url(url):
                        continue
                    if is_junk_news(headline, ticker):
                        continue
                    seen_urls.add(url)
                    out.append(Event(
                        ticker=ticker, source=self.name,
                        event_type=classify_press_release(headline),
                        headline=headline[:300], published_at=pub, url=url,
                        summary="", external_id=url,
                        raw={"ir_home": ir_home, "platform": "q4"},
                    ))
                continue  # Q4 handled this bank — don't also HTML-scrape it

            try:
                links = self._find_press_links(ir_home)
            except Exception as e:
                print(f"[ir_site] {ticker} error: {type(e).__name__}: {e}")
                continue

            for url, text in links:
                if url in seen_urls or not is_safe_news_url(url):
                    continue
                if is_junk_news(text, ticker):
                    continue
                seen_urls.add(url)

                # Try to find a date in URL or text
                pub = _parse_date_in_text(url) or _parse_date_in_text(text)
                if pub and pub < cutoff:
                    continue
                inferred = pub is None
                if not pub:
                    pub = datetime.now(timezone.utc)

                out.append(Event(
                    ticker=ticker,
                    source=self.name,
                    event_type=classify_press_release(text),
                    headline=text[:300],
                    published_at=pub,
                    url=url,
                    summary="",  # IR pages don't always have summaries
                    external_id=url,  # URL is the natural dedup key
                    raw={"ir_home": ir_home, "date_inferred": inferred},
                ))
        return out

    def _find_press_links(self, ir_home: str) -> list[tuple[str, str]]:
        """Try the IR home + common press-release subpaths, return plausible links."""
        # Get the host root for path-joining (handle https://ir.example.com/sub/)
        parsed = urlparse(ir_home)
        host = f"{parsed.scheme}://{parsed.netloc}"

        results: list[tuple[str, str]] = []
        seen_hrefs: set[str] = set()

        for path in _PRESS_PAGE_CANDIDATES:
            candidate = ir_home if path == "" else (ir_home.rstrip("/") + path)
            html = _fetch(candidate)
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
