"""Earnings-release metrics for non-SEC-filer (OTC) banks.

~20 universe banks (PBAM, BKSC, OZK, …) publish no EDGAR filings — their
quarterly earnings release IS their primary public disclosure, distributed
via the wire services (GlobeNewswire / Business Wire / ACCESSWIRE). This
module locates the latest earnings release through FMP's press-release feed
(FMP is only the TRANSPORT — the content is the bank's own release; owner
provenance decision 2026-07-16), fetches the FULL story from the wire URL
(FMP's `text` field is a ~300-char summary blurb), and runs the exact same
guarded extractors as the EDGAR path (data/release_metrics — bands,
adjusted-variant exclusion, cross-candidate agreement, period-headed table
columns only). Anything not confidently found is None — never guessed.

Returned shape mirrors data.release_metrics.release_metrics so the boards,
exhibit and valuation layers consume either source identically; `source`
distinguishes them for labeling ("per company release").
"""
from __future__ import annotations

from datetime import datetime

_UA = {"User-Agent": "Mozilla/5.0 (compatible; KSK-dashboard "
                     "research@kskinvestors.com)"}


def _latest_earnings_pr(ticker: str) -> dict | None:
    """The newest press release whose TITLE passes the earnings-headline
    gate (shared with the 9.01-fallback finder) AND whose title+blurb name
    the bank as SUBJECT: {title, url, published_at} or None.

    The subject guard is non-negotiable: FMP's symbol index is polluted for
    short tickers (the news adapter's founding bug — symbols=CMA returned
    "CMA Fest" stories), and here a wrong story doesn't just mis-file news,
    it puts ANOTHER COMPANY'S numbers on this bank's valuation. Both gates
    run BEFORE any fetch — appointments, product news and other issuers'
    releases never cost a page load."""
    from data.events.fmp_news import _is_subject
    from data.fmp_client import get_press_releases
    from data.ir_provider import _is_earnings_headline
    try:
        prs = get_press_releases(ticker, limit=25) or []
    except Exception:
        return None
    hits = [p for p in prs
            if _is_earnings_headline(p.get("title") or "")
            and p.get("url") and p.get("published_at")
            and _is_subject(ticker, f"{p.get('title') or ''} "
                                    f"{p.get('text') or ''}")]
    if not hits:
        return None
    return max(hits, key=lambda p: p["published_at"])


def _release_qend(title: str, filed_date: str) -> str | None:
    """The release's quarter-end. The TITLE's own period statement
    ("… Second Quarter 2026 …") governs — tiny OTC banks publish late, and
    a date-derived quarter would mislabel every value in a late release.
    A title period more than ~100 days before (or after) the publish date
    is a garbage signal → None, never a guess. Titles without a period fall
    back to the standard published-just-after-quarter-end assumption."""
    from datetime import date
    from data.ir_provider import _quarter_end_before
    from data.release_metrics import _period_qend
    title_qend = _period_qend(title or "")
    if title_qend:
        try:
            gap = (date.fromisoformat(filed_date)
                   - date.fromisoformat(title_qend)).days
        except ValueError:
            return None
        return title_qend if 0 <= gap <= 100 else None
    return _quarter_end_before(filed_date)


def _fetch_story(url: str) -> str | None:
    """Full wire-story HTML, or None. Wire pages (GlobeNewswire etc.) render
    the release body incl. real <table> markup, so table extraction works."""
    from data.http import get_with_retry
    try:
        resp = get_with_retry(url, headers=_UA, timeout=30)
    except Exception:
        return None
    return resp.text if resp is not None else None


def otc_release_metrics(ticker: str) -> dict | None:
    """Extracted metrics for a non-SEC bank's latest earnings release:
    same shape as release_metrics() plus {source: "company_release",
    title}. Cached per ticker; an extraction is immutable per story URL, so
    a fresh-within-15-min cache serves directly and past that only the
    (cheap) press-release index is re-checked — a new story URL triggers
    re-extraction, anything else re-stamps."""
    if not ticker:
        return None
    from data import cache as _cache
    from data.freshness import is_fresh

    # v5 (2026-07-16): earnings-conference-call notice refusal (FRBA).
    # v4 + subject guard (FMP index pollution must never put
    # another company's numbers on this bank) + title-governed qend (late
    # OTC releases would mislabel every value under the date-derived
    # assumption). v3 prose-EPS connector (release_metrics v12). COUPLING:
    # any release_metrics extraction-spec bump must bump THIS version too
    # (extractions here are immutable per story URL). v2 refused "Announces
    # Date for … Earnings Release" scheduling notices.
    key = f"otc_release:v5:{ticker.upper()}"
    try:
        cached = _cache.get(key)
    except Exception:
        cached = None
    if cached is not None and is_fresh(cached, 900):
        return cached.get("value")

    def _stamp(value):
        try:
            _cache.put(key, {"cached_at": datetime.now().isoformat(),
                             "value": value})
        except Exception:
            pass
        return value

    prev = (cached or {}).get("value")
    pr = _latest_earnings_pr(ticker)
    if pr is None:
        # PR index unreachable or no earnings release found: keep serving
        # what we had (re-stamped); nothing if we had nothing.
        return _stamp(prev) if prev else None
    if prev and prev.get("url") == pr["url"]:
        return _stamp(prev)                     # same story — nothing new

    html = _fetch_story(pr["url"])
    if not html:
        return _stamp(prev) if prev else None

    from data.ir_provider import extract_capital_ratios
    from data.release_metrics import (_prior_quarter_end, _year_ago_qend,
                                      extract_release_metrics,
                                      extract_table_metrics)
    filed_date = (pr["published_at"] or "")[:10]
    qend = _release_qend(pr.get("title") or "", filed_date)
    if qend is None:
        # Title names a period that can't be reconciled with the publish
        # date — extracting would mislabel every value. Serve what we had.
        return _stamp(prev) if prev else None
    prior_qend = _prior_quarter_end(qend)
    val = {
        "qend": qend,
        "metrics": extract_release_metrics(html, expected_qend=qend),
        "prior_metrics": (extract_table_metrics(html, prior_qend)
                          if prior_qend else {}),
        "prior_qend": prior_qend,
        "yoy_metrics": (extract_table_metrics(html, _year_ago_qend(qend))
                        if _year_ago_qend(qend) else {}),
        "yoy_qend": _year_ago_qend(qend),
        "capital": extract_capital_ratios(html),
        "url": pr["url"],
        "title": pr.get("title"),
        "filed_date": filed_date,
        "source": "company_release",
    }
    return _stamp(val)
