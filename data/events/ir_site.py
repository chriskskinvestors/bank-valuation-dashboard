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


# URL paths to try off of the IR home page (in order of preference)
_PRESS_PAGE_CANDIDATES = [
    "/news/press-releases",
    "/news/news-releases",
    "/press-releases",
    "/news",
    "/newsroom",
    "/press",
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


class IRSiteAdapter(SourceAdapter):
    """Generic IR-page scraper. Watchlist-only."""

    name = "ir_site"
    LOOKBACK_DAYS = 30  # Looser since dates on IR pages aren't always reliable

    def poll(self, tickers: list[str], since: datetime | None = None) -> list[Event]:
        cutoff = since or (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS))
        out: list[Event] = []
        seen_urls: set[str] = set()

        for ticker in tickers:
            ir_home = IR_URLS.get(ticker)
            if not ir_home:
                continue
            try:
                links = self._find_press_links(ir_home)
            except Exception as e:
                print(f"[ir_site] {ticker} error: {type(e).__name__}: {e}")
                continue

            for url, text in links:
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # Try to find a date in URL or text
                pub = _parse_date_in_text(url) or _parse_date_in_text(text)
                if pub and pub < cutoff:
                    continue
                # If no date, assume "recent" (now) but mark in raw so we
                # can de-prioritize undated items in the UI later.
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
                    raw={"ir_home": ir_home, "date_inferred": pub is None},
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
