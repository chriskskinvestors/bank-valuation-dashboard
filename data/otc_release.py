"""Earnings-release metrics for non-SEC-filer (OTC) banks.

~100 universe banks (PBAM, BKSC, the 2026-07-16 admission sweep, …)
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


# IR-site crawls are quarterly-cadence discovery, not freshness: once per day
# per bank is generous (a release posted mid-day is picked up within 24h, the
# same tolerance the Form-4 insider half documents). The 15-min envelope serve
# and the wire-PR path are NOT throttled by this.
_IR_CRAWL_TTL_S = 24 * 3600


def _ir_checked_within(cached: dict | None, ttl_s: float) -> bool:
    """True when the envelope records an IR-crawl attempt newer than ttl_s.
    Absent/garbled marker = False (crawl) — first discovery must never be
    blocked, and a corrupt timestamp must not wedge a bank into never
    crawling again."""
    from datetime import datetime as _dt
    marker = (cached or {}).get("ir_checked_at")
    if not marker:
        return False
    try:
        return (_dt.now() - _dt.fromisoformat(str(marker))).total_seconds() < ttl_s
    except (TypeError, ValueError):
        return False


def _earnings_prs(ticker: str) -> list[dict]:
    """Every press release in the wire index whose TITLE passes the
    earnings-headline gate (shared with the 9.01-fallback finder) AND whose
    title+blurb name the bank as SUBJECT — newest first, [] on failure.
    `_latest_earnings_pr` serves the head; the EPS-history backfill walks
    the tail (prior quarters' releases, same two gates).

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
        return []
    hits = [p for p in prs
            if _is_earnings_headline(p.get("title") or "")
            and p.get("url") and p.get("published_at")
            and _is_subject(ticker, f"{p.get('title') or ''} "
                                    f"{p.get('text') or ''}")]
    return sorted(hits, key=lambda p: p["published_at"], reverse=True)


def _latest_earnings_pr(ticker: str) -> dict | None:
    """The newest gated earnings release: {title, url, published_at} or
    None (see _earnings_prs for the gates)."""
    hits = _earnings_prs(ticker)
    return hits[0] if hits else None


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


_STORY_KEY = "wire_story_html:v1:"
_DENIED_RE = re.compile(r"(?i)<title>\s*access denied\s*</title>")
# Business Wire refuses server fetches for good (403 since 2026-10-07): an
# unreachable story there is skipped, never retried as a transient failure.
_PERMANENT_BLOCK_HOST_RE = re.compile(r"(?i)://(?:www\.)?businesswire\.com/")
_HOST_BACKOFF_S = 1800
_host_down: dict[str, float] = {}     # host → time of its last refusal


def _story_key(url: str) -> str:
    # Hashed: the cache key column is VARCHAR(255) and wire URLs carry the
    # whole headline as a slug.
    import hashlib
    return _STORY_KEY + hashlib.sha1(url.encode("utf-8")).hexdigest()


def _fetch_story(url: str) -> str | None:
    """Full wire-story HTML, or None. Wire pages (GlobeNewswire etc.) render
    the release body incl. real <table> markup, so table extraction works.

    A published story never changes, so a successful fetch is kept for good
    and served at any age: GlobeNewswire began refusing server fetches on
    2026-10-08 (Akamai "Access Denied" 403), and every re-extraction (an
    envelope version bump, the EPS-history backfill's retry) would otherwise
    need a page the wire no longer serves."""
    if not url:
        return None
    from data import cache
    try:
        hit = cache.get(_story_key(url), max_age_s=None)
    except Exception:
        hit = None
    if isinstance(hit, dict) and hit.get("html"):
        return hit["html"]
    import time
    from urllib.parse import urlparse
    from data.http import get_with_retry, is_http_404
    host = (urlparse(url).netloc or "").lower()
    # From Cloud Run the block is a read timeout, not a fast 403: three
    # 30s attempts per story. One refusal backs the whole host off for a
    # while, so a warm pass pays it once, not once per bank.
    if time.time() - _host_down.get(host, 0.0) < _HOST_BACKOFF_S:
        return None
    try:
        resp = get_with_retry(url, headers=_UA, timeout=30)
    except Exception as e:
        if not is_http_404(e):
            _host_down[host] = time.time()
        return None
    html = resp.text if resp is not None else None
    if html and _DENIED_RE.search(html[:2000]):
        _host_down[host] = time.time()
        return None          # a bot wall served as 200 is a failed fetch
    if html and len(html) >= 2000:
        try:
            cache.put(_story_key(url), {"url": url, "html": html})
        except Exception:
            pass
    return html


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
            "https://api.fdic.gov/banks/institutions",
            params={"filters": f"CERT:{int(cert)}", "fields": "WEBADDR",
                    "format": "json"},
            headers=_UA, timeout=20)
        data = (resp.json() or {}).get("data") if resp is not None else None
        return (data[0]["data"].get("WEBADDR") or None) if data else None
    except Exception:
        return None


# Link-text period forms the shared header parser (_period_qend) doesn't
# know: "Q2 2026" / "Q2'26" (it only reads "2Q26"), and an ordinal quarter
# with NO year ("… Announces Second Quarter Results"). Bank news lists
# prefix each link with its PUBLISH date, which the parser's bare-date
# fallback returned as the period — live 2026-10-06: CCNB qend 2026-07-28,
# OAKV 2026-07-30, FOTB 2026-07-22 ("July 22, 2026 - Press Release"), every
# extracted value mis-dated and the staleness gates anchored to a non-quarter.
_LINK_QTOK = re.compile(r"\bQ([1-4])\s?['’]?(\d{2}|\d{4})\b", re.I)
_LINK_QORD = re.compile(r"\b(first|1st|second|2nd|third|3rd|fourth|4th)\s+"
                        r"(?:quarter|qtr\.?)\b", re.I)
_LINK_PUB_GAP_DAYS = 100  # publish date at most this far past the quarter-end


def _link_qend(text: str) -> str | None:
    """A link text's stated period as a calendar QUARTER-END, or None.
    The shared parser's quarter tokens win by pattern order ("July 28, 2026
    - … Second Quarter 2026 Results" → 2026-06-30); a full date it returns
    that is NOT a quarter-end is the publish date, never the period. Then
    the link-only forms: "Q2 2026"; and a yearless ordinal quarter, whose
    year comes from that publish date — the latest such quarter-end on or
    before it, accepted only within _LINK_PUB_GAP_DAYS (the wire path's
    _release_qend bound). No quarter token, or no date to anchor a yearless
    one → None: a period that can't be proven is never a candidate."""
    from datetime import date
    from data.release_metrics import _Q_END, _QNUM, _period_qend
    found = _period_qend(text)
    if found and _is_quarter_end(found):
        return found
    m = _LINK_QTOK.search(text)
    if m:
        q, y = int(m.group(1)), int(m.group(2))
        y += 2000 if y < 100 else 0
        mo, dy = _Q_END[q]
        return date(y, mo, dy).isoformat()
    m = _LINK_QORD.search(text)
    if not (m and found):
        return None
    pub = date.fromisoformat(found)
    mo, dy = _Q_END[_QNUM[m.group(1).lower()]]
    qe = date(pub.year, mo, dy)
    if qe > pub:
        qe = date(pub.year - 1, mo, dy)
    return qe.isoformat() if (pub - qe).days <= _LINK_PUB_GAP_DAYS else None


def _ir_release_candidates(ticker: str) -> list[dict]:
    """Every earnings release linked from the bank's own site, newest stated
    period first: [{url, title, qend, kind}]. Two-hop crawl (static news
    paths + homepage nav links that hint investor/news pages — banks bury
    the list one click deep); the FIRST page carrying any candidate is the
    list (same stop rule as the single-pick locator always had); candidate
    links must pass the IR link gate AND state a quarter-end period
    (_link_qend). The head is
    the bank's latest release (_latest_ir_release); the tail is the prior
    quarters' releases the EPS-history backfill reads."""
    from urllib.parse import urljoin, urlparse

    from data.events.ir_site import _domain_root, _extract_links, _fetch

    webaddr = _bank_webaddr(ticker)
    if not webaddr:
        return []
    root = _domain_root(webaddr)      # bare host, no scheme ("hamlinbank.com")
    if not root:
        return []

    def _scan(html: str, page: str, best: list) -> list:
        for href, text in _extract_links(html, page):
            if not text or not _is_ir_release_link(text):
                continue
            qend = _link_qend(text)
            if not qend:
                continue
            kind = "pdf" if href.lower().split("?")[0].endswith(".pdf") \
                else "html"
            best.append({"url": href, "title": text, "qend": qend,
                         "kind": kind})
        return best

    hosts = [f"https://{root}", f"https://www.{root}"]
    best, home_html, home_url = [], None, None
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
    if not best and home_html:
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
    # Stable sort: among equal stated periods the first link found wins —
    # exactly the single-pick locator's "strictly newer replaces" rule.
    return sorted(best, key=lambda c: c["qend"], reverse=True)


def _latest_ir_release(ticker: str) -> dict | None:
    """The newest earnings release posted on the bank's own site:
    {url, title, qend, kind} or None — the head of _ir_release_candidates;
    a newest stated period older than ~200 days means the bank doesn't
    keep current releases here → None."""
    from datetime import date, timedelta
    cands = _ir_release_candidates(ticker)
    if not cands:
        return None
    floor = (date.today() - timedelta(days=_IR_STALE_DAYS)).isoformat()
    return cands[0] if cands[0]["qend"] >= floor else None


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


def _env_record(ticker: str) -> tuple[str, dict | None]:
    """(cache key, raw envelope record) — the ONE place the per-ticker key
    (version = extraction spec) is spelled, read at any age: freshness is
    judged by the caller (15-min is_fresh + URL-match re-stamp), and the
    default 24h read ceiling would drop the record after any >24h gap and
    force a full re-crawl + re-extraction per bank."""
    from data import cache as _cache
    key = f"otc_release:v17:{ticker.upper()}"
    try:
        return key, _cache.get(key, max_age_s=None)
    except Exception:
        return key, None


def _read_envelope(ticker: str) -> dict | None:
    """SERVE-ONLY envelope read (the allow_fetch=False contract, without the
    wrapper's re-stamp paths): the value dict or None."""
    _, cached = _env_record(ticker)
    v = (cached or {}).get("value")
    return None if not v or v.get("empty") else v


def otc_release_metrics(ticker: str, *, allow_fetch: bool = True,
                        ir_crawl: bool = True) -> dict | None:
    """Extracted metrics for a non-SEC bank's latest earnings release:
    same shape as release_metrics() plus {source: "company_release",
    title}. Cached per ticker; an extraction is immutable per story URL, so
    a fresh-within-15-min cache serves directly and past that only the
    (cheap) press-release index is re-checked — a new story URL triggers
    re-extraction, anything else re-stamps.

    allow_fetch=False is the SERVE-ONLY contract (2026-08-31): return the
    cached envelope at WHATEVER age — no index check, no story fetch, no
    IR-site crawl, and no re-stamp write. The metric-resolution paths
    (analysis/valuation) pass False, because they run on BOTH the snapshot
    job's build loop and single-bank Company-page renders: the IR crawl
    costs 30-100s per no-wire bank, and with the 24h crawl throttle those
    all lapse together — one refresh-home-snapshot run per day was spending
    14-24 min inside build_metrics paying them serially (measured 818s /
    1438s spikes, 2026-08-31), and an unlucky Company-page load could pay
    one inline. jobs/refresh_home_snapshot warms this AFTER the snapshot
    write instead; renders and metric builds only read."""
    if not ticker:
        return None
    from data import cache as _cache
    from data.freshness import is_fresh

    # v16 (2026-10-06): release_metrics v23 — revenue-basis facts (SEC
    # filers' envelope field; the OTC extraction itself is unchanged, bumped
    # under the unconditional coupling rule).
    # v15 (2026-10-06): release_metrics v22 — adjusted / ex-items EPS forms.
    # v13 (2026-10-06): OTC P/E + market cap — the envelope now carries the
    # release's discrete-quarter diluted-EPS SERIES (release_metrics
    # .extract_table_series) and its tied-out common share count
    # (release_shares.extract_shares_outstanding), and every extraction
    # appends to the per-ticker quarter history (otc_eps_history). Cached
    # envelopes lack those keys until re-extracted → bump.
    # v12 (2026-10-01): release_metrics v21.
    # v11 (2026-10-01): release_metrics v20 — bv_ps preferred guard (NPB).
    # v10 (2026-10-01): release_metrics v19 — deterministic bv_ps (GLBZ).
    # v9 (2026-08-20): release_metrics v18 — EPS tie-out input rows (NI
    # applicable to common + weighted average diluted shares). OTC banks
    # never reach the composite-EPS tie-out (no CIK), but the coupling rule
    # below is unconditional: the shared extractor's spec moved, so cached
    # OTC extractions re-run once and stay shape-identical to SEC banks'.
    # v8 (2026-08-02): release_metrics v17 — TBV/share dollar-change-then-level
    # form ("increased $1.67, or 15.6%, to $12.38", FSRL 2Q26). OTC banks are
    # exactly the population that narrates figures in prose rather than tagging
    # them, so a TBV pattern gap hits them hardest; without this bump their
    # cached extractions keep the pre-fix n/a until their next release.
    # v7 (2026-07-27): catch up to release_metrics v14-v16 — v6 was cut when
    # the shared extractor was at v13, so OTC releases extracted 07-16..07-21
    # were pinned with the respectively-pair bug (ROE took the FIRST value of
    # an "X and Y were 1.70% and 14.65%, respectively" pair — CCFN 1.70 vs
    # real 14.65) and the v14 point-first-decimal misses. Immutable per URL,
    # so only a version bump re-extracts them; without it the wrong figure
    # renders as "per company release" until each bank's next release (~Oct).
    # v6 (2026-07-16 pm): + IR-site fallback (owner: "The PDFs posted need
    # to be part of it") — banks without a wire release get their own site
    # crawled (domain from the FDIC record = structural identity), PDF text
    # via pypdf; located-nothing is cached as a sentinel so the ~80 no-wire
    # banks don't re-crawl every render. v5 conference-call notice refusal;
    # v4 subject guard + title-governed qend; v3 prose-EPS connector
    # (release_metrics v12). COUPLING: any release_metrics extraction-spec
    # bump must bump THIS version too (extractions immutable per URL).
    key, cached = _env_record(ticker)
    if not allow_fetch:
        v = (cached or {}).get("value")
        return None if not v or v.get("empty") else v

    if cached is not None and is_fresh(cached, 900):
        v = cached.get("value")
        return None if (v or {}).get("empty") else v

    def _stamp(value, ir_checked: str | None = None):
        # ir_checked_at rides the envelope and must SURVIVE re-stamps: it
        # marks the last IR-site crawl ATTEMPT (cached_at is bumped on every
        # serve, so it can't be the throttle clock).
        try:
            _cache.put(key, {"cached_at": datetime.now().isoformat(),
                             "value": value,
                             "ir_checked_at":
                                 ir_checked or (cached or {}).get("ir_checked_at")})
        except Exception:
            pass
        return value

    prev = (cached or {}).get("value")
    if prev and prev.get("empty"):
        prev = None
    pr = _latest_earnings_pr(ticker)
    transport = "wire"
    if pr is None and not ir_crawl:
        # Wire-only warm (SEC filers without an Item 2.02 8-K — the PBAM
        # class): no 30-100s site crawl; serve what we had, write nothing.
        return prev
    if pr is None:
        # No wire release — the bank's own site is the disclosure channel.
        # THROTTLE (2026-08-17): the two-hop site crawl below costs 30-84s
        # for the slow banks, and the metrics build re-entered it every 30
        # minutes for ~80 no-wire banks once the 15-min serve lapsed — the
        # slowest 10 banks alone were 29% of a 1741s build (measured
        # 2026-08-03; NARA 84s, TCNB 64s). Releases are quarterly: crawl at
        # most once per _IR_CRAWL_TTL_S per bank and serve the envelope in
        # between. First-ever discovery (no marker) still crawls
        # immediately, and the wire path above is untouched.
        if _ir_checked_within(cached, _IR_CRAWL_TTL_S):
            if prev:
                return _stamp(prev)
            _stamp({"empty": True})
            return None

        _ir_now = datetime.now().isoformat()

        def _stamp_ir(value):
            return _stamp(value, ir_checked=_ir_now)

        ir = _latest_ir_release(ticker)
        if ir is None:
            # Nothing anywhere: cache the negative so the site isn't
            # re-crawled every render; serve what we had if anything.
            if prev:
                return _stamp_ir(prev)
            _stamp_ir({"empty": True})
            return None
        if prev and prev.get("url") == ir["url"]:
            return _stamp_ir(prev)              # same document — nothing new
        text = _fetch_document(ir["url"], ir["kind"])
        if not text:
            if prev:
                return _stamp_ir(prev)
            _stamp_ir({"empty": True})
            return None
        return _extract_and_stamp(
            _stamp_ir, prev, ticker, html=text, url=ir["url"],
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
                                      extract_table_metrics,
                                      extract_table_series)
    from data.release_shares import extract_shares_outstanding
    prior_qend = _prior_quarter_end(qend)
    metrics = extract_release_metrics(html, expected_qend=qend)
    # OTC P/E + market cap inputs (owner spec 2026-10-06). eps_series: the
    # release's own discrete-quarter diluted EPS by quarter-end (five-quarter
    # tables), period-proven column by column — the composite TTM's
    # components. shares_outstanding: common shares at qend, served only
    # when BV/share × shares reproduces the release's own equity (±1%);
    # shares_tie_out records the proof or the refusal.
    series = extract_table_series(html, "eps_diluted")
    shares = extract_shares_outstanding(html, qend, metrics)
    _append_eps_history(ticker, url=url, qend=qend,
                        eps=metrics.get("eps_diluted"), series=series)
    val = {
        "qend": qend,
        "metrics": metrics,
        "eps_series": series,
        "shares_outstanding": shares["shares"],
        "shares_tie_out": shares["tie"],
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


# ── Per-ticker quarter history (OTC composite TTM EPS, owner spec 2026-10-06)
# The envelope above is ONE release per ticker (latest URL). A TTM needs
# four discrete quarters, so every extraction also APPENDS its quarter(s) to
# a small per-ticker history: {qend: {eps_diluted, url, extracted_at, via}}
# — the release's own quarter ("release") and every column of its
# five-quarter table ("table"). Entries are write-once: a later value that
# agrees re-affirms, a later value that DISAGREES marks the quarter
# `conflict` (the composite refuses it — one of the two is wrong, and a
# restatement is indistinguishable from a mis-read here) — never overwrites.
# After a year every OTC bank has four quarters even without a table; the
# bounded one-time backfill below (≤4 prior releases, wire index then IR
# crawl) fills the first year.

_HIST_V = 1
_BACKFILL_MAX_RELEASES = 4
_HIST_AGREE = 0.011      # cent agreement, as release_metrics


def _hist_key(ticker: str) -> str:
    key = f"otc_eps_history:v{_HIST_V}:{ticker.upper()}"
    return key


def get_eps_history(ticker: str) -> dict:
    """SERVE-ONLY read of the ticker's quarter history at any age:
    {"quarters": {qend: {...}}, "backfill": {...} | None}; {} when none."""
    from data import cache as _cache
    key = _hist_key(ticker)
    try:
        cached = _cache.get(key, max_age_s=None)
    except Exception:
        return {}
    return (cached or {}).get("value") or {}


def _is_quarter_end(qend: str | None) -> bool:
    try:
        y, m, d = (int(x) for x in str(qend).split("-"))
    except (TypeError, ValueError):
        return False
    return {3: 31, 6: 30, 9: 30, 12: 31}.get(m) == d and 2000 <= y <= 2100


def _append_eps_history(ticker: str, *, url: str, qend: str | None,
                        eps: float | None, series: dict | None) -> dict:
    """Merge one release's quarter(s) into the ticker's history (write-once
    per quarter; disagreement → conflict flag, never an overwrite). The
    release's own quarter is recorded via "release" and OUTRANKS a "table"
    entry for the same quarter when they agree (provenance upgrade only).
    Returns the stored history."""
    from data import cache as _cache
    hist = get_eps_history(ticker)
    quarters = dict(hist.get("quarters") or {})
    now = datetime.now().isoformat(timespec="seconds")
    incoming: list[tuple[str, float, str]] = []
    if qend and eps is not None and _is_quarter_end(qend):
        incoming.append((qend, float(eps), "release"))
    for q, v in (series or {}).items():
        if v is not None and _is_quarter_end(q):
            incoming.append((q, float(v), "table"))
    for q, v, via in incoming:
        cur = quarters.get(q)
        if cur is None:
            quarters[q] = {"eps_diluted": v, "url": url, "extracted_at": now,
                           "via": via}
            continue
        if cur.get("eps_diluted") is None:
            continue
        if abs(cur["eps_diluted"] - v) <= _HIST_AGREE:
            if via == "release" and cur.get("via") == "table":
                quarters[q] = {**cur, "url": url, "via": via,
                               "reaffirmed_at": now}
            continue
        if not cur.get("conflict"):
            quarters[q] = {**cur, "conflict": True,
                           "alt": {"eps_diluted": v, "url": url, "via": via,
                                   "seen_at": now}}
    hist = {**hist, "quarters": quarters}
    try:
        _cache.put(_hist_key(ticker), {"cached_at": now, "value": hist})
    except Exception:
        pass
    return hist


def backfill_eps_history(ticker: str,
                         max_releases: int = _BACKFILL_MAX_RELEASES) -> bool:
    """ONE-TIME bounded backfill of a ticker's quarter history from its
    prior releases: up to `max_releases` older releases (the wire index
    first — cheap, already fetched by the warm; the bank's own site only
    when the wire has nothing), each run through the same guarded
    extractors and appended. Runs only for a ticker whose envelope exists
    (its latest release was already extracted) and only once — the
    `backfill` marker is written even when nothing was found, so a bank
    with no prior releases costs one attempt, ever. Returns True when a
    backfill RAN (the caller budgets runs), False when skipped.

    Called from jobs/refresh_home_snapshot's warm pass — never from a
    render or the snapshot build (the serve-only contract above)."""
    from data import cache as _cache
    from data.release_metrics import (extract_release_metrics,
                                      extract_table_series)
    env = _read_envelope(ticker)
    if not env or not env.get("url"):
        return False
    hist = get_eps_history(ticker)
    if hist.get("backfill"):
        return False
    if _ir_checked_within({"ir_checked_at": hist.get("backfill_blocked_at")},
                          _IR_CRAWL_TTL_S):
        return False                  # a blocked attempt retries daily
    latest_url = env["url"]
    docs: list[tuple[str, str | None, str | None, str, bool]] = []
    for pr in _earnings_prs(ticker):
        if pr["url"] == latest_url:
            continue
        qend = _release_qend(pr.get("title") or "",
                             (pr["published_at"] or "")[:10])
        docs.append((pr["url"], pr.get("title"), qend, "html", True))
    if not docs:
        for c in _ir_release_candidates(ticker):
            if c["url"] == latest_url:
                continue
            docs.append((c["url"], c.get("title"), c["qend"], c["kind"],
                         False))
    tried: list[str] = []
    blocked = False
    for url, title, qend, kind, wire in docs[:max_releases]:
        tried.append(url)
        if not qend or not _is_quarter_end(qend):
            continue                  # period unprovable → never extracted
        text = _fetch_story(url) if wire else _fetch_document(url, kind)
        if not text:
            # An unreachable wire story is a failure, not an absent quarter:
            # writing the once-only marker over it (GlobeNewswire, blocked
            # 2026-10-08) would lose that quarter for good. Business Wire
            # refuses server fetches permanently — that one is skipped.
            if wire and not _PERMANENT_BLOCK_HOST_RE.search(url):
                blocked = True
            continue
        metrics = extract_release_metrics(text, expected_qend=qend)
        series = extract_table_series(text, "eps_diluted")
        _append_eps_history(ticker, url=url, qend=qend,
                            eps=metrics.get("eps_diluted"), series=series)
    hist = get_eps_history(ticker)
    now = datetime.now().isoformat(timespec="seconds")
    if blocked:
        hist["backfill_blocked_at"] = now
    else:
        hist["backfill"] = {"done_at": now, "urls": tried}
    try:
        _cache.put(_hist_key(ticker),
                   {"cached_at": datetime.now().isoformat(), "value": hist})
    except Exception:
        pass
    return True
