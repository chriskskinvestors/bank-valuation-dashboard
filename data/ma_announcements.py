"""
Merger ANNOUNCEMENT resolution for the Detailed M&A History table
(docs/SNL-BUILD-PLAN.md §14) — announce date + stated deal value.

FDIC structure history (data/ma_history.py) knows only COMPLETIONS. This
module finds the deal's announcement 8-K via EDGAR full-text search (EFTS,
coverage 2001+): quoted target-bank-name query over 8-Ks in the 18 months
up to completion, then classifies each candidate's press-release text as
announcement vs completion. Guards — all must pass, else n/a:

  • the target's name appears in the document text
  • the acquirer's distinctive brand token appears (names are taken from
    the FDIC structure row, i.e. the names AT DEAL TIME — survives
    renamings like South Umpqua Bank -> Umpqua Bank -> Columbia Bank)
  • announcement markers present ("definitive agreement", "agreement and
    plan of merger", "agreed to acquire", ...)
  • completed-tense markers ABSENT ("has completed", "announced the
    completion", ...) — a completion PR cites the "previously announced
    definitive agreement", so completion rejection takes precedence.
    Prospective phrases ("upon/following completion of") don't trip it.

Deal value: STRICT stated-value patterns only ("... valued at
approximately $191.1 million") on the accepted announcement text; several
DISTINCT candidate values -> n/a, never a guess. All-stock MOEs whose PR
quotes only an exchange ratio return value None here — the computed
ratio × price × shares leg is a separate increment (owner-approved plan,
2026-07-13). Whole-company deals only: branch-package announcement
linkage (both parties keep operating, so name queries are hopelessly
noisy) is deferred, honest n/a.

resolve_announcement returns (result | None, ok): ok=False means a FETCH
failure (caller must not cache); ok=True with None means genuinely not
found (cacheable n/a — e.g. pre-2001 deals, private targets with no
EDGAR-filed PR).
"""

from __future__ import annotations

import html as _html
import re
import time
from datetime import date, timedelta

import requests

EDGAR_FTS = "https://efts.sec.gov/LATEST/search-index"
_EFTS_FLOOR = "2001-04-01"      # EDGAR full-text coverage starts 2001; a
                                # startdt before this 500s — always clamp
_WINDOW_DAYS = 540              # announce → completion span searched
_MAX_CANDIDATES = 16            # accession groups fetched per deal — big
                                # public targets (Sterling, Pacific Premier)
                                # file many eligible 8-Ks before the
                                # announcement; oldest-first stays the safe
                                # order (the first doc IN TIME passing the
                                # announce gates is the announcement)
_PAUSE_S = 0.15                 # stay far under EDGAR's 10 req/s

# 8-K items an announcement can carry: 1.01 material agreement, 8.01 other
# events, 7.01 Reg FD. A candidate whose items EXCLUDE all three (earnings
# 2.02, completion 2.01, officer 5.02, ...) is skipped without fetching —
# big public targets file routine 8-Ks constantly and would otherwise burn
# the candidate budget. Hits with no item codes (pre-2004 numbering) stay
# eligible.
_ANNOUNCE_ITEMS = {"1.01", "7.01", "8.01"}


def _headers() -> dict:
    from config import SEC_USER_AGENT
    return {"User-Agent": SEC_USER_AGENT}


# Corporate suffixes stripped for the quoted EFTS phrase — the PR says
# "Pacific Premier Bank", not "Pacific Premier Bank, National Association".
_NAME_SUFFIX_RE = re.compile(
    r"[,\s]+(?:national\s+association|n\.?\s?a\.?|f\.?s\.?b\.?|fsb|ssb|"
    r"national\s+banking\s+association)\s*$", re.IGNORECASE)

# Generic words that never identify a bank brand (subset of the events-store
# stopword idea, local so this module stays dependency-light).
_GENERIC = frozenset({
    "bank", "banks", "bancorp", "bancshares", "banc", "banco", "financial",
    "holdings", "holding", "group", "corporation", "corp", "company", "co",
    "incorporated", "inc", "trust", "savings", "loan", "association",
    "national", "federal", "state", "first", "community", "citizens",
    "united", "american", "pacific", "valley", "the", "of", "and", "new",
})


def query_name(name: str) -> str:
    """FDIC institution name -> the quoted phrase searched in EFTS."""
    return _NAME_SUFFIX_RE.sub("", (name or "").strip()).strip(" ,")


def brand_token(name: str) -> str | None:
    """The first distinctive brand token of a bank name ('Umpqua Bank' ->
    'umpqua'), or None when every token is generic ('First National Bank')."""
    for w in re.findall(r"[a-z0-9&']+", (name or "").lower()):
        if w not in _GENERIC and len(w) >= 3:
            return w
    return None


_ANNOUNCE_RE = re.compile(
    r"definitive\s+(?:merger\s+)?agreement|agreement\s+and\s+plan\s+of\s+"
    r"(?:merger|reorganization)|agree(?:d|ment)\s+to\s+(?:acquire|merge|be\s+"
    r"acquired)|have\s+agreed\s+to\s+combine|signed\s+a\s+definitive|"
    r"will\s+acquire|to\s+be\s+acquired\s+by", re.IGNORECASE)

# Completed-tense only. "upon/following/after completion of" (announcement
# boilerplate about the future close) must NOT match.
_COMPLETED_RE = re.compile(
    r"\b(?:has|have|had|today|successfully)\s+completed\b|"
    r"\bannounce[ds]?\s+the\s+completion\b|"
    r"\bcompleted\s+(?:its|the)\s+(?:previously\s+announced|acquisition|"
    r"merger|purchase|combination)", re.IGNORECASE)

# Stated deal value, tightly anchored to transaction-value phrasings so a
# termination fee / capital figure can never match.
_VALUE_RE = re.compile(
    r"(?:transaction\s+valued\s+at|valued\s+at|deal\s+valued\s+at|"
    r"aggregate\s+(?:transaction\s+)?value\s+of|total\s+(?:transaction|deal)\s+"
    r"value\s+of|purchase\s+price\s+of|aggregate\s+consideration\s+of)\s+"
    r"(?:approximately\s+|about\s+)?\$\s?([\d][\d,]*(?:\.\d+)?)\s*"
    r"(billion|million)", re.IGNORECASE)


def _strip_html(raw: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", _html.unescape(txt))


def extract_stated_value(text: str) -> int | None:
    """Deal value in RAW DOLLARS from strict stated-value phrasings, or None.
    Distinct candidate amounts -> None (ambiguous, never a guess)."""
    vals = set()
    for num, unit in _VALUE_RE.findall(text):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        vals.add(int(v * (1_000_000_000 if unit.lower() == "billion"
                          else 1_000_000)))
    if len(vals) != 1:
        return None
    return vals.pop()


def _efts_hits(target_query: str, startdt: str, enddt: str) -> list[dict] | None:
    """EFTS hits for a quoted phrase over 8-Ks in a window; None on failure."""
    try:
        resp = requests.get(EDGAR_FTS, params={
            "q": f'"{target_query}"', "forms": "8-K",
            "dateRange": "custom", "startdt": startdt, "enddt": enddt,
        }, headers=_headers(), timeout=30)
        resp.raise_for_status()
        return resp.json().get("hits", {}).get("hits", [])
    except Exception as e:
        print(f"[ma_announce] EFTS '{target_query}' error: {type(e).__name__}: {e}")
        return None


def _candidates(hits: list[dict]) -> list[dict]:
    """Group EFTS document hits by accession, oldest first (the announcement
    precedes every later mention). Keeps the best document per accession —
    the press-release exhibit (EX-99.*) over the 8-K body. Accessions whose
    8-K items exclude every announcement item are dropped here, before any
    document fetch (see _ANNOUNCE_ITEMS)."""
    by_adsh: dict[str, dict] = {}
    for h in hits:
        src = h.get("_source", {})
        adsh = src.get("adsh") or (h.get("_id", "").split(":")[0])
        doc = h.get("_id", "").split(":")[-1]
        if not adsh or not src.get("file_date"):
            continue
        # Gate on modern dotted item codes only — pre-2004 8-Ks carry legacy
        # single-digit items ("5", "7") and must stay eligible.
        modern = {str(i) for i in (src.get("items") or []) if "." in str(i)}
        if modern and not (_ANNOUNCE_ITEMS & modern):
            continue
        is_ex99 = str(src.get("file_type") or "").upper().startswith("EX-99")
        cur = by_adsh.get(adsh)
        if cur is None or (is_ex99 and not cur["is_ex99"]):
            by_adsh[adsh] = {"adsh": adsh, "doc": doc,
                             "file_date": src.get("file_date"),
                             "cik": (src.get("ciks") or [None])[0],
                             "is_ex99": is_ex99}
    return sorted(by_adsh.values(), key=lambda c: c["file_date"])


def _fetch_doc_text(cik, adsh: str, doc: str) -> str | None:
    """One EDGAR archive document as flattened text; None on failure."""
    if not cik or not adsh or not doc:
        return None
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{adsh.replace('-', '')}/{doc}")
    try:
        resp = requests.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        return _strip_html(resp.text)
    except Exception as e:
        print(f"[ma_announce] doc {adsh}/{doc} error: {type(e).__name__}: {e}")
        return None


def resolve_announcement(target_name: str, acquirer_name: str,
                         completion_date: str) -> tuple[dict | None, bool]:
    """
    The announcement 8-K for a completed whole-company deal.

    Returns (result, ok):
      result — {announce_date 'YYYY-MM-DD', value_usd int RAW DOLLARS | None,
                value_basis 'stated' | None, url, accession} or None
      ok     — False only on a FETCH failure (EFTS or a candidate document);
               the caller must then skip its cache put. ok=True with a None
               result is a genuine, cacheable n/a.
    """
    tq = query_name(target_name)
    acq_tok = brand_token(acquirer_name)
    if not tq or not completion_date or completion_date < _EFTS_FLOOR:
        return None, True

    try:
        comp = date.fromisoformat(completion_date)
    except ValueError:
        return None, True
    # Clamp to the index floor — EFTS 500s on a startdt before its coverage.
    startdt = max((comp - timedelta(days=_WINDOW_DAYS)).isoformat(),
                  _EFTS_FLOOR)

    hits = _efts_hits(tq, startdt, completion_date)
    if hits is None:
        return None, False

    fetch_failed = False
    for cand in _candidates(hits)[:_MAX_CANDIDATES]:
        time.sleep(_PAUSE_S)
        text = _fetch_doc_text(cand["cik"], cand["adsh"], cand["doc"])
        if text is None:
            fetch_failed = True
            continue
        low = text.lower()
        if tq.lower() not in low:
            continue
        if acq_tok and acq_tok not in low:
            continue
        if _COMPLETED_RE.search(text):        # completion PR — not the announce
            continue
        if not _ANNOUNCE_RE.search(text):
            continue
        return {
            "announce_date": cand["file_date"],
            "value_usd": extract_stated_value(text),
            "value_basis": "stated" if extract_stated_value(text) else None,
            "url": (f"https://www.sec.gov/Archives/edgar/data/"
                    f"{int(cand['cik'])}/{cand['adsh'].replace('-', '')}/"
                    f"{cand['doc']}"),
            "accession": cand["adsh"],
        }, True
    # Nothing classified as the announcement. Only claim a cacheable n/a if
    # every candidate was actually readable.
    return None, not fetch_failed


if __name__ == "__main__":
    # LIVE smoke — known ground truth:
    #   Banner/Skagit: announced 2018-07-26, stated value $191.1M
    #   Columbia/Umpqua (bank-level target Columbia State Bank, acquirer name
    #   at deal time "Umpqua Bank"): announced 2021-10-12; all-stock MOE PR
    #   states no dollar value -> value None (computed leg is separate)
    #   Banner/AmericanWest: announced 2014-11-05 (Starbuck/AmericanWest)
    r, ok = resolve_announcement("Skagit Bank", "Banner Bank", "2018-11-01")
    print("Skagit:", r, ok)
    assert ok and r and r["announce_date"] == "2018-07-26", r
    assert r["value_usd"] == 191_100_000 and r["value_basis"] == "stated", r

    r, ok = resolve_announcement("Columbia State Bank", "Umpqua Bank",
                                 "2023-03-01")
    print("Columbia State Bank:", r, ok)
    assert ok and r and r["announce_date"] == "2021-10-12", r

    r, ok = resolve_announcement("AmericanWest Bank", "Banner Bank",
                                 "2015-10-02")
    print("AmericanWest:", r, ok)
    assert ok and r and r["announce_date"] == "2014-11-05", r

    # Pre-EFTS deal -> honest, cacheable n/a
    r, ok = resolve_announcement("Whatcom State Bank", "Banner Bank",
                                 "1999-01-04")
    assert r is None and ok
    print("\nSMOKE OK: announcement resolution verified on known deals.")
