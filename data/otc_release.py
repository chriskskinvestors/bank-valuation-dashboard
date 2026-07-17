"""Earnings-release metrics for non-SEC-filer (OTC) banks.

~100 universe banks (PBAM, BKSC, OZK, the 2026-07-16 admission sweep, …)
publish no EDGAR filings — their quarterly earnings release IS their primary
public disclosure. Two transports, one guarded extraction:

  1. WIRE: FMP's press-release feed locates the story (FMP is only the
     TRANSPORT — the content is the bank's own release; owner provenance
     decision 2026-07-16); the FULL story is fetched from the wire URL
     (FMP's `text` field is a ~300-char summary blurb).
  2. IR SITE (owner: "The PDFs posted need to be part of it"): banks that
     never wire their releases get their OWN site crawled — domain from the
     FDIC record (structural identity), two hops (news paths + homepage nav
     hints), PDF text via pypdf. Located-nothing is cached as a sentinel so
     no-coverage banks aren't re-crawled every render.

Both feed the exact same guarded extractors as the EDGAR path
(data/release_metrics — bands, adjusted-variant exclusion, cross-candidate
agreement, period-headed table columns only). Anything not confidently
found is None — never guessed.

Returned shape mirrors data.release_metrics.release_metrics so the boards,
exhibit and valuation layers consume either source identically; `source`
distinguishes them for labeling ("per company release").
"""
from __future__ import annotations

import re
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


# ── IR-site fallback (owner directive 2026-07-16: "The PDFs posted need to
# be part of it") ──────────────────────────────────────────────────────────
# Most tiny banks never wire their releases — they post a PDF (or an HTML
# news page) on their OWN site. Identity here is structural: the domain
# comes from the bank's FDIC institution record, so unlike the wire path
# (polluted FMP index → subject guard) the document's issuer is proven by
# where it lives. The link's STATED PERIOD is the only period proof (PDFs
# carry no publish date): no period in the link text → never a candidate.

_NEWS_PATHS = ("news", "press-releases", "news-releases", "press-room",
               "about/news", "about-us/news", "investors", "investor-relations",
               "")

_IR_STALE_DAYS = 200      # newest stated period older than this → nothing

# IR link-text gate. Wire TITLES are full sentences ("X Reports Second
# Quarter 2026 Results") but bank-site links read "Q2 2026 Earnings
# Release" — no report verb, so the shared headline gate can't apply.
# Looser is safe HERE ONLY because identity is structural (the bank's own
# domain), the stated period is still mandatory, and the extraction guards
# bound what a mis-picked document can produce.
_IR_LINK_POSITIVE = re.compile(
    r"(?i)\b(?:earnings|results|press release|financial highlights|"
    r"quarterly report)\b")
_IR_LINK_NEGATIVE = re.compile(
    r"(?i)\b(?:annual meeting|proxy|webcast|conference call|newsletter|"
    r"promotion|career|holiday)\b|\bannounces?\s+(?:the\s+)?date\b")

# Homepage nav links worth one hop — where banks hide the news page.
_IR_NAV_HINT = re.compile(
    r"(?i)investor|shareholder|news|press|about")


def _is_ir_release_link(text: str) -> bool:
    return (bool(_IR_LINK_POSITIVE.search(text))
            and not _IR_LINK_NEGATIVE.search(text))


def _bank_webaddr(ticker: str) -> str | None:
    """The bank's own domain: universe snapshot webaddr, else a live FDIC
    institutions lookup by cert (new admissions predate the snapshot)."""
    try:
        from data.bank_universe import get_universe
        info = (get_universe() or {}).get(ticker.upper()) or {}
        if info.get("webaddr"):
            return info["webaddr"]
    except Exception:
        pass
    try:
        from data.bank_mapping import get_fdic_cert
        from data.http import get_with_retry
        cert = get_fdic_cert(ticker)
        if not cert:
            return None
        resp = get_with_retry(
            "https://banks.data.fdic.gov/api/institutions",
            params={"filters": f"CERT:{int(cert)}", "fields": "WEBADDR",
                    "format": "json"},
            headers=_UA, timeout=20)
        data = (resp.json() or {}).get("data") if resp is not None else None
        return (data[0]["data"].get("WEBADDR") or None) if data else None
    except Exception:
        return None


def _latest_ir_release(ticker: str) -> dict | None:
    """The newest earnings release posted on the bank's own site:
    {url, title, qend, kind} or None. Two-hop crawl (static news paths +
    homepage nav links that hint investor/news pages — banks bury the list
    one click deep); candidate links must pass the IR link gate AND state
    their period; the newest stated period wins, and one older than ~200
    days means the bank doesn't keep current releases here → None."""
    from datetime import date, timedelta
    from urllib.parse import urljoin, urlparse

    from data.events.ir_site import _domain_root, _extract_links, _fetch
    from data.release_metrics import _period_qend

    webaddr = _bank_webaddr(ticker)
    if not webaddr:
        return None
    root = _domain_root(webaddr)      # bare host, no scheme ("hamlinbank.com")
    if not root:
        return None

    def _scan(html: str, page: str, best: dict | None) -> dict | None:
        for href, text in _extract_links(html, page):
            if not text or not _is_ir_release_link(text):
                continue
            qend = _period_qend(text)
            if not qend:
                continue
            if best is None or qend > best["qend"]:
                kind = "pdf" if href.lower().split("?")[0].endswith(".pdf") \
                    else "html"
                best = {"url": href, "title": text, "qend": qend, "kind": kind}
        return best

    hosts = [f"https://{root}", f"https://www.{root}"]
    best, home_html, home_url = None, None, None
    for path in _NEWS_PATHS:
        html, page = None, None
        for host in hosts:
            page = urljoin(host + "/", path)
            html = _fetch(page)
            if html:
                if host != hosts[0]:
                    hosts = [host]    # site wants www — stop retrying bare
                break
        if not html:
            continue
        if path == "":
            home_html, home_url = html, page
        best = _scan(html, page, best)
        if best:
            break                     # first page with candidates wins
    # Second hop: homepage nav links hinting at investor/news sections.
    if best is None and home_html:
        seen, hops = set(), 0
        for href, text in _extract_links(home_html, home_url):
            if hops >= 6:
                break
            if not text or not _IR_NAV_HINT.search(text):
                continue
            if urlparse(href).netloc.split(":")[0].removeprefix("www.") != root:
                continue              # same-site only
            if href in seen:
                continue
            seen.add(href)
            hops += 1
            sub = _fetch(href)
            if sub:
                best = _scan(sub, href, best)
                if best:
                    break
    if best is None:
        return None
    floor = (date.today() - timedelta(days=_IR_STALE_DAYS)).isoformat()
    return best if best["qend"] >= floor else None


def _fetch_document(url: str, kind: str) -> str | None:
    """The document's TEXT: pypdf extraction for PDFs (first ~12 pages —
    tables collapse to text soup, so only the prose extractors apply, which
    is exactly the guarded degradation we want), raw HTML otherwise."""
    from data.http import get_with_retry
    try:
        resp = get_with_retry(url, headers=_UA, timeout=45)
    except Exception:
        return None
    if resp is None:
        return None
    is_pdf = (kind == "pdf"
              or "pdf" in (resp.headers.get("Content-Type") or "").lower())
    if not is_pdf:
        return resp.text
    try:
        import io as _io

        from pypdf import PdfReader
        reader = PdfReader(_io.BytesIO(resp.content))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:12])
    except Exception:
        return None


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

    # v6 (2026-07-16 pm): + IR-site fallback (owner: "The PDFs posted need
    # to be part of it") — banks without a wire release get their own site
    # crawled (domain from the FDIC record = structural identity), PDF text
    # via pypdf; located-nothing is cached as a sentinel so the ~80 no-wire
    # banks don't re-crawl every render. v5 conference-call notice refusal;
    # v4 subject guard + title-governed qend; v3 prose-EPS connector
    # (release_metrics v12). COUPLING: any release_metrics extraction-spec
    # bump must bump THIS version too (extractions immutable per URL).
    key = f"otc_release:v6:{ticker.upper()}"
    try:
        cached = _cache.get(key)
    except Exception:
        cached = None
    if cached is not None and is_fresh(cached, 900):
        v = cached.get("value")
        return None if (v or {}).get("empty") else v

    def _stamp(value):
        try:
            _cache.put(key, {"cached_at": datetime.now().isoformat(),
                             "value": value})
        except Exception:
            pass
        return value

    prev = (cached or {}).get("value")
    if prev and prev.get("empty"):
        prev = None
    pr = _latest_earnings_pr(ticker)
    transport = "wire"
    if pr is None:
        # No wire release — the bank's own site is the disclosure channel.
        ir = _latest_ir_release(ticker)
        if ir is None:
            # Nothing anywhere: cache the negative so the site isn't
            # re-crawled every render; serve what we had if anything.
            if prev:
                return _stamp(prev)
            _stamp({"empty": True})
            return None
        if prev and prev.get("url") == ir["url"]:
            return _stamp(prev)                 # same document — nothing new
        text = _fetch_document(ir["url"], ir["kind"])
        if not text:
            if prev:
                return _stamp(prev)
            _stamp({"empty": True})
            return None
        return _extract_and_stamp(
            _stamp, prev, ticker, html=text, url=ir["url"],
            title=ir.get("title"), qend=ir["qend"], filed_date=None,
            transport="ir_site")
    if prev and prev.get("url") == pr["url"]:
        return _stamp(prev)                     # same story — nothing new

    html = _fetch_story(pr["url"])
    if not html:
        return _stamp(prev) if prev else None

    filed_date = (pr["published_at"] or "")[:10]
    qend = _release_qend(pr.get("title") or "", filed_date)
    if qend is None:
        # Title names a period that can't be reconciled with the publish
        # date — extracting would mislabel every value. Serve what we had.
        return _stamp(prev) if prev else None
    return _extract_and_stamp(
        _stamp, prev, ticker, html=html, url=pr["url"],
        title=pr.get("title"), qend=qend, filed_date=filed_date,
        transport=transport)


def _extract_and_stamp(_stamp, prev, ticker, *, html, url, title, qend,
                       filed_date, transport):
    """Shared extraction tail for both transports — the SAME guarded
    extractors regardless of where the document came from."""
    from data.ir_provider import extract_capital_ratios
    from data.release_metrics import (_prior_quarter_end, _year_ago_qend,
                                      extract_release_metrics,
                                      extract_table_metrics)
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
        "url": url,
        "title": title,
        "filed_date": filed_date,
        "source": "company_release",
        "transport": transport,
    }
    return _stamp(val)
