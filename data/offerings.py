"""
Detailed Offerings for one holdco (docs/SNL-BUILD-PLAN.md §14) — registered
securities offerings + 8-K private placements, per-document classified.

Rows come from three legs:

  424B* prospectuses — the primary document's front cover is fetched and
      classified (live-verified on Banner, 2026-07-13):
        Merger prospectus — 424B3s that are S-4 deal documents ("Agreement
            and Plan of Merger" / proxy-statement covers). Shown but
            EXCLUDED from offering totals — the deal itself lives on the
            M&A History tab; counting it here would double-count.
        Preliminary — "Subject to Completion" covers with blank amounts.
        ECM — common stock / depositary shares / preferred stock covers;
            gross from the stated aggregate or shares × price-to-public.
        DCM — notes/debentures covers ("We are offering $100,000,000
            aggregate principal amount of ... Subordinated Notes due
            2030"); gross from the stated aggregate principal.
      Unmatched covers -> kind "Unclassified", amounts n/a — never a guess.
  S-1/S-3 family — shelf/primary registration rows (no amounts; S-3ASR
      registers an unlimited amount by design).
  8-K Item 3.02 — unregistered (private-placement) sales. 8-Ks whose items
      also include 2.01 are SKIPPED: stock issued as acquisition
      consideration is deal consideration, not a capital raise. Amounts
      strict-extracted from the filing text where stated.

Classification is OUR OWN from filing type + cover text, labeled as such.
Cache: ``offerings:v1:{cik}`` for 7 days; any fetch failure returns None
(uncacheable). Sorted newest-first.
"""

from __future__ import annotations

import html as _html
import re
import time
from datetime import datetime

import requests

from data.ma_summary import _SHELF_FORMS, iter_submission_filings

CACHE_TTL_SECONDS = 7 * 86400
_PAUSE_S = 0.15
_COVER_CHARS = 8000             # the classification signals live on the cover

_MERGER_COVER_RE = re.compile(
    r"agreement\s+and\s+plan\s+of\s+merger|proxy\s+statement(?:/|\s+and\s+)"
    r"prospectus|merger\s+of|to\s+be\s+voted", re.IGNORECASE)
_PRELIM_RE = re.compile(r"subject\s+to\s+completion", re.IGNORECASE)
# Selling-holder resales (e.g. Treasury's TARP CPP preferred auctions):
# real prospectuses, but the COMPANY raises nothing — classified apart and
# excluded from raise totals (live-verified: Banner 424B5 2012-03-29).
_RESALE_RE = re.compile(
    r"(?:we\s+)?will\s+not\s+receive\s+any\s+(?:of\s+the\s+)?proceeds",
    re.IGNORECASE)
_DCM_SEC_RE = re.compile(
    r"((?:\d+\.\d+%\s+)?(?:fixed[-\s]to[-\s]floating(?:\s+rate)?\s+)?"
    r"(?:subordinated|senior)\s+(?:notes|debentures)(?:\s+due\s+\d{4})?)",
    re.IGNORECASE)
_ECM_SEC_RE = re.compile(
    r"(depositary\s+shares|(?:non[-\s]cumulative\s+)?(?:perpetual\s+)?"
    r"preferred\s+stock|common\s+stock)", re.IGNORECASE)
# "We are offering $100,000,000 aggregate principal amount"
_DCM_GROSS_RE = re.compile(
    r"\$\s?([\d][\d,]*(?:\.\d+)?)\s+(?:aggregate\s+)?principal\s+amount",
    re.IGNORECASE)
# The SEC-mandated cover price table: "Public offering price $ 2.00
# $ 150,000,000" (live-verified, Banner 424B5 June 2010) — per-share first,
# total second. Authoritative when present.
_ECM_TABLE_RE = re.compile(
    r"(?:public\s+offering\s+price|price\s+to\s+(?:the\s+)?public)\s*"
    r"\$\s?([\d][\d,]*(?:\.\d+)?)\s*\$\s?([\d][\d,]*(?:\.\d+)?)",
    re.IGNORECASE)
# Fallback: "We are offering 75,000,000 shares" x a lone per-share price.
_ECM_SHARES_RE = re.compile(
    r"offering\s+(?:of\s+)?([\d][\d,]{4,})\s+shares", re.IGNORECASE)
_ECM_PRICE_RE = re.compile(
    r"at\s+a\s+(?:public\s+offering\s+)?price\s+of\s+"
    r"\$\s?([\d][\d,]*(?:\.\d+)?)", re.IGNORECASE)
# 3.02 private placements: "sold $X aggregate principal amount" / "gross
# proceeds of $X"
_PP_GROSS_RE = re.compile(
    r"(?:gross\s+proceeds\s+of|sold|issued)[^.]{0,80}?"
    r"\$\s?([\d][\d,]*(?:\.\d+)?)(?:\s*(million))?", re.IGNORECASE)


def _headers() -> dict:
    from config import SEC_USER_AGENT
    return {"User-Agent": SEC_USER_AGENT}


def _is_fresh(cached: dict | None) -> bool:
    from data.freshness import is_fresh
    return is_fresh(cached, CACHE_TTL_SECONDS)


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _fetch_cover(cik: int, accession: str, doc: str) -> tuple[str | None, bool]:
    """(cover text, ok). A MISSING primary document (routine on old archived
    filings) is (None, True) — a data gap, not a fetch failure; only an HTTP
    error is (None, False)."""
    if not accession or not doc:
        return None, True
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{accession.replace('-', '')}/{doc}")
    try:
        resp = requests.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        txt = re.sub(r"<[^>]+>", " ", resp.text)
        return re.sub(r"\s+", " ", _html.unescape(txt))[:_COVER_CHARS], True
    except Exception as e:
        print(f"[offerings] doc {accession}/{doc}: {type(e).__name__}: {e}")
        return None, False


def classify_424b(cover: str) -> dict:
    """kind/security/gross/price from a 424B front cover. Strict — anything
    ambiguous stays n/a; an unmatched cover is 'Unclassified'."""
    if _MERGER_COVER_RE.search(cover):
        return {"kind": "Merger prospectus", "security": None,
                "gross_usd": None, "price_per_share": None}
    if _PRELIM_RE.search(cover):
        return {"kind": "Preliminary", "security": None,
                "gross_usd": None, "price_per_share": None}
    if _RESALE_RE.search(cover):
        sec = _ECM_SEC_RE.search(cover) or _DCM_SEC_RE.search(cover)
        return {"kind": "Resale (selling holder)",
                "security": " ".join(sec.group(1).split()).title() if sec else None,
                "gross_usd": None, "price_per_share": None}
    m = _DCM_SEC_RE.search(cover)
    if m:
        gross = None
        gm = _DCM_GROSS_RE.search(cover)
        if gm:
            v = _num(gm.group(1))
            gross = int(v) if v and v > 100_000 else None
        return {"kind": "DCM", "security": " ".join(m.group(1).split()),
                "gross_usd": gross, "price_per_share": None}
    m = _ECM_SEC_RE.search(cover)
    if m:
        price = gross = None
        tm = _ECM_TABLE_RE.search(cover)
        if tm:
            a, b = _num(tm.group(1)), _num(tm.group(2))
            # per-share first, total second; sanity: total >> per-share
            if a and b and b > a * 1000:
                price, gross = a, int(b)
        if gross is None:
            pm = _ECM_PRICE_RE.search(cover)
            sm = _ECM_SHARES_RE.search(cover)
            if pm and sm:
                pv, sv = _num(pm.group(1)), _num(sm.group(1))
                if pv and sv:
                    price, gross = pv, int(pv * sv)
        return {"kind": "ECM", "security": " ".join(m.group(1).split()).title(),
                "gross_usd": gross, "price_per_share": price}
    return {"kind": "Unclassified", "security": None,
            "gross_usd": None, "price_per_share": None}


def extract_pp_gross(text: str) -> int | None:
    """Private-placement gross from 8-K text; distinct candidates -> n/a."""
    vals = set()
    for num, million in _PP_GROSS_RE.findall(text):
        v = _num(num)
        if v is None:
            continue
        if million:
            v *= 1_000_000
        if v > 500_000:            # ignore per-share / trivial figures
            vals.add(int(v))
    return vals.pop() if len(vals) == 1 else None


def get_offerings(cik) -> list[dict] | None:
    """
    All classified offering rows for a holdco CIK, newest-first, or None on
    any fetch failure (uncacheable):

      [{date, form, kind, security, gross_usd, price_per_share, url,
        accession}]

    kind: ECM | DCM | Private placement | Shelf registration |
          Merger prospectus | Preliminary | Resale (selling holder) |
          Unclassified.
    Merger prospectuses, Preliminary and Resale rows are display rows only —
    the UI excludes them from every raise total (deal double-count /
    unpriced / no company proceeds).
    """
    if not cik:
        return None
    cik = int(cik)
    from data import cache

    key = f"offerings:v1:{cik}"
    cached = cache.get(key)
    if _is_fresh(cached) and isinstance(cached.get("rows"), list):
        return cached["rows"]

    filings, ok = iter_submission_filings(cik)
    if not ok:
        return None

    rows: list[dict] = []
    fetch_failed = False
    for f in filings:
        form, date_, acc, doc = f["form"], f["date"], f["accession"], f["doc"]
        url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/"
               f"{acc.replace('-', '')}/{doc}") if acc and doc else None
        if form in _SHELF_FORMS:
            rows.append({"date": date_, "form": form,
                         "kind": "Shelf registration", "security": None,
                         "gross_usd": None, "price_per_share": None,
                         "url": url, "accession": acc})
        elif form.startswith("424B"):
            time.sleep(_PAUSE_S)
            cover, ok = _fetch_cover(cik, acc, doc)
            if not ok:
                fetch_failed = True
                continue
            cls = (classify_424b(cover) if cover is not None else
                   {"kind": "Unclassified", "security": None,
                    "gross_usd": None, "price_per_share": None})
            rows.append({"date": date_, "form": form, **cls,
                         "url": url, "accession": acc})
        elif form == "8-K":
            items = {i.strip() for i in (f["items"] or "").split(",")}
            if "3.02" not in items or "2.01" in items:
                # 2.01+3.02 = stock issued as ACQUISITION consideration —
                # deal consideration, not a capital raise.
                continue
            time.sleep(_PAUSE_S)
            text, ok = _fetch_cover(cik, acc, doc)
            if not ok:
                fetch_failed = True
                continue
            rows.append({"date": date_, "form": "8-K (Item 3.02)",
                         "kind": "Private placement", "security": None,
                         "gross_usd": extract_pp_gross(text)
                                      if text is not None else None,
                         "price_per_share": None, "url": url,
                         "accession": acc})

    if fetch_failed:
        return None
    rows.sort(key=lambda r: r["date"], reverse=True)
    cache.put(key, {"rows": rows, "cached_at": datetime.now().isoformat()})
    return rows


if __name__ == "__main__":
    # LIVE smoke — Banner Corp (CIK 946673), classifications hand-verified
    # against the actual documents (2026-07-13 probe):
    #   2020-06-26 424B2 -> DCM, "5.00% Fixed-to-Floating Rate Subordinated
    #       Notes due 2030", $100,000,000 stated aggregate principal
    #   2020-06-25 424B3 -> Preliminary (blank amounts, subject to completion)
    #   2026-06-17 424B3 -> Merger prospectus (Banner / Pacific Financial)
    rows = get_offerings(946673)
    assert rows is not None, "fetch failed"
    print(f"Banner: {len(rows)} rows")
    for r in rows[:12]:
        print(f"  {r['date']}  {r['form']:<14} {r['kind']:<18}"
              f" {r['security'] or '—':<52} gross={r['gross_usd']}")
    d = next(r for r in rows if r["date"] == "2020-06-26")
    assert d["kind"] == "DCM" and d["gross_usd"] == 100_000_000, d
    assert "Subordinated Notes due 2030" in d["security"], d
    p = next(r for r in rows if r["date"] == "2020-06-25")
    assert p["kind"] == "Preliminary", p
    mrg = next(r for r in rows if r["date"] == "2026-06-17")
    assert mrg["kind"] == "Merger prospectus", mrg
    shelf = [r for r in rows if r["kind"] == "Shelf registration"]
    assert len(shelf) == 18, len(shelf)   # matches ma_summary's smoke count
    print("SMOKE OK")
