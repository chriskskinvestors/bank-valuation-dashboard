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

Deal value, two strict bases (n/a over guess, always labeled):
  stated   — "... valued at approximately $191.1 million" phrasings on the
             accepted announcement text; several DISTINCT candidate
             values -> n/a.
  computed — all-stock deals whose PR quotes only an exchange ratio
             ("receive 0.5958 of a share of Columbia stock for each
             Umpqua share"): value = target shares outstanding (SEC
             companyfacts dei cover count nearest ≤ announce) × ratio ×
             acquirer's last close BEFORE announce (FMP EOD — the press
             convention; verified: Columbia/Umpqua computes $5.19B vs the
             press-reported ~$5.2B). Party -> ticker via the PR's
             "(NASDAQ: XXXX)" mentions; ticker -> CIK via the EFTS hits'
             display names (works for delisted targets like UMPQ) with the
             live bank mapping as fallback. Every leg strict: ambiguous
             ratio, unmapped party, stale share count (>200d) or stale
             price (>10d) -> value n/a; the announce DATE is kept either
             way. ``value_note`` records the computed formula verbatim.

Whole-company deals only: branch-package announcement linkage (both
parties keep operating, so name queries are hopelessly noisy) is
deferred, honest n/a.

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


# ── Computed all-stock value (ratio × acquirer price × target shares) ─────

# "receive 0.5958 of a share of Columbia stock for each Umpqua share"
_RATIO_RECEIVE_RE = re.compile(
    r"receive\s+(\d?\.\d{2,4})\s+(?:of\s+a\s+share|shares?)\s+of\s+"
    r"([A-Z][\w.&'\- ]{1,50}?)\s+(?:common\s+)?stock\s+for\s+each(?:\s+share\s+"
    r"of)?\s+([A-Z][\w.&'\- ]{1,50}?)\s+(?:common\s+stock|shares?|stock)")
# "each share of Umpqua common stock will be converted into ... 0.5958
#  shares of Columbia common stock"
_RATIO_CONVERT_RE = re.compile(
    r"each\s+share\s+of\s+([A-Z][\w.&'\- ]{1,50}?)\s+(?:common\s+)?stock\s+"
    r"(?:will\s+be|shall\s+be|is)\s+converted\s+into\s+(?:the\s+right\s+to\s+"
    r"receive\s+)?(\d?\.\d{2,4})\s+(?:of\s+a\s+share|shares?)\s+of\s+"
    r"([A-Z][\w.&'\- ]{1,50}?)\s+(?:common\s+)?stock")
# "Columbia Banking System, Inc. (NASDAQ: COLB)" -> name/ticker pairs
_PR_TICKER_RE = re.compile(
    r"([A-Z][\w.,&'\- ]{2,60}?)\s*\(\s*(?:NYSE(?:\s+American)?|NASDAQ|Nasdaq)"
    r"\s*:\s*([A-Z]{1,5})\s*\)")
# EFTS display_names: "UMPQUA HOLDINGS CORP  (UMPQ)  (CIK 0001077771)".
# DELISTED registrants lose the "(UMPQ)" part (live-verified), so the CIK
# fallback below also matches on the display NAME's brand token.
_DISPLAY_NAME_RE = re.compile(r"\(([A-Z]{1,5})\)\s+\(CIK\s+(\d+)\)")
_DISPLAY_CIK_RE = re.compile(r"^(.*?)\s*(?:\([A-Z]{1,5}\)\s*)?\(CIK\s+(\d+)\)")

_SHARES_MAX_AGE_DAYS = 200      # cover count must be within 2 quarters
_PRICE_MAX_AGE_DAYS = 10        # last close must be a normal trading gap


def extract_exchange_ratio(text: str) -> tuple[float, str, str] | None:
    """(ratio, acquirer-side phrase, target-side phrase) from the PR's
    exchange-ratio sentence, or None. Several DISTINCT ratios -> None."""
    found = []
    for m in _RATIO_RECEIVE_RE.finditer(text):
        found.append((float(m.group(1)), m.group(2).strip(), m.group(3).strip()))
    for m in _RATIO_CONVERT_RE.finditer(text):
        found.append((float(m.group(2)), m.group(3).strip(), m.group(1).strip()))
    if not found or len({r for r, _, _ in found}) != 1:
        return None
    return found[0]


def _pr_ticker_pairs(text: str) -> list[tuple[str, str]]:
    """[(company name phrase, ticker)] from '(NASDAQ: XXXX)' mentions."""
    return [(m.group(1).strip(), m.group(2)) for m in _PR_TICKER_RE.finditer(text)]


def _ticker_for_side(side_phrase: str, pairs: list[tuple[str, str]]) -> str | None:
    """The PR ticker whose company name shares the side phrase's brand token."""
    tok = brand_token(side_phrase)
    if not tok:
        return None
    hits = {tick for name, tick in pairs if tok in name.lower()}
    return hits.pop() if len(hits) == 1 else None


def _shares_outstanding_asof(cik, asof: str) -> tuple[int | None, str | None, bool]:
    """Target cover-page share count nearest ≤ ``asof`` from SEC companyfacts.
    Returns (shares, as-of end date, ok) — ok=False on fetch failure."""
    from data.sec_client import fetch_company_facts

    facts = fetch_company_facts(int(cik))
    if not facts:
        return None, None, False        # fetch failed (helper logs + returns {})
    dei = facts.get("facts", {}).get("dei", {}).get(
        "EntityCommonStockSharesOutstanding", {})
    rows = [r for u in dei.get("units", {}).values() for r in u
            if (r.get("end") or "") <= asof and r.get("val")]
    if not rows:
        return None, None, True
    best = max(rows, key=lambda r: (r.get("end", ""), r.get("filed", "")))
    floor = (date.fromisoformat(asof)
             - timedelta(days=_SHARES_MAX_AGE_DAYS)).isoformat()
    if (best.get("end") or "") < floor:
        return None, None, True         # too stale to price a deal — n/a
    return int(best["val"]), best.get("end"), True


def _close_before(ticker: str, asof: str) -> tuple[float | None, str | None, bool]:
    """Acquirer's last close STRICTLY before ``asof`` (the press convention).
    Returns (close, date, ok) — ok=False when the environment has no FMP key
    (retry later); with a key, no data is a genuine, cacheable n/a."""
    from data import fmp_client

    if not fmp_client._has_key():
        return None, None, False
    try:
        df = fmp_client.get_history(ticker, "ALL")
    except Exception as e:
        print(f"[ma_announce] price {ticker} error: {type(e).__name__}: {e}")
        return None, None, False
    if df is None or df.empty or "date" not in df or "close" not in df:
        return None, None, True
    rows = df[df["date"].astype(str).str[:10] < asof]
    if rows.empty:
        return None, None, True
    last = rows.iloc[-1]
    pdate = str(last["date"])[:10]
    floor = (date.fromisoformat(asof)
             - timedelta(days=_PRICE_MAX_AGE_DAYS)).isoformat()
    if pdate < floor:
        return None, None, True         # halted/stale tape — n/a
    return float(last["close"]), pdate, True


def compute_stock_value(text: str, announce_date: str,
                        cik_by_ticker: dict[str, int],
                        name_ciks: list[tuple[str, int]] = ()) -> tuple[dict | None, bool]:
    """
    Computed all-stock deal value from the announcement text, or None.

    ``name_ciks``: [(EFTS display name lower-cased, cik)] — the fallback CIK
    source for DELISTED targets whose display names carry no ticker.

    Returns ({value_usd, value_note}, ok). Every leg is strict: ambiguous or
    absent ratio, unmapped side, stale shares or price -> (None, True) — a
    cacheable n/a. ok=False only when a lookup FAILED (no FMP key, SEC fetch
    error) so the caller retries instead of freezing the miss.
    """
    ratio_hit = extract_exchange_ratio(text)
    if not ratio_hit:
        return None, True
    ratio, acq_side, tgt_side = ratio_hit
    pairs = _pr_ticker_pairs(text)
    acq_tick = _ticker_for_side(acq_side, pairs)
    tgt_tick = _ticker_for_side(tgt_side, pairs)
    if not acq_tick or not tgt_tick or acq_tick == tgt_tick:
        return None, True

    tgt_cik = cik_by_ticker.get(tgt_tick)
    if not tgt_cik:
        tok = brand_token(tgt_side)
        cands = {c for n, c in (name_ciks or []) if tok and tok in n}
        if len(cands) == 1:
            tgt_cik = cands.pop()
    if not tgt_cik:
        from data.bank_mapping import get_cik
        tgt_cik = get_cik(tgt_tick)
    if not tgt_cik:
        return None, True

    shares, shares_asof, ok = _shares_outstanding_asof(tgt_cik, announce_date)
    if not ok:
        return None, False
    if not shares:
        return None, True
    price, price_date, ok = _close_before(acq_tick, announce_date)
    if not ok:
        return None, False
    if not price:
        return None, True

    return {
        "value_usd": int(round(shares * ratio * price)),
        "value_note": (f"computed: {ratio} × {acq_tick} ${price:.2f} "
                       f"({price_date}) × {shares:,} {tgt_tick} shares "
                       f"({shares_asof})"),
    }, True


def _cik_by_ticker(hits: list[dict]) -> dict[str, int]:
    """ticker -> CIK from the EFTS hits' display names (listed filers)."""
    out: dict[str, int] = {}
    for h in hits:
        for dn in (h.get("_source", {}).get("display_names") or []):
            m = _DISPLAY_NAME_RE.search(dn or "")
            if m:
                out.setdefault(m.group(1), int(m.group(2)))
    return out


def _name_ciks(hits: list[dict]) -> list[tuple[str, int]]:
    """[(display name lower, cik)] from the EFTS hits — the CIK source for
    DELISTED filers, whose display names carry no ticker (e.g. UMPQ)."""
    out: dict[int, str] = {}
    for h in hits:
        for dn in (h.get("_source", {}).get("display_names") or []):
            m = _DISPLAY_CIK_RE.match((dn or "").strip())
            if m:
                out.setdefault(int(m.group(2)), m.group(1).lower())
    return [(n, c) for c, n in out.items()]


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
        result = {
            "announce_date": cand["file_date"],
            "value_usd": extract_stated_value(text),
            "value_basis": None,
            "value_note": None,
            "url": (f"https://www.sec.gov/Archives/edgar/data/"
                    f"{int(cand['cik'])}/{cand['adsh'].replace('-', '')}/"
                    f"{cand['doc']}"),
            "accession": cand["adsh"],
        }
        ok = True
        if result["value_usd"] is not None:
            result["value_basis"] = "stated"
        else:
            comp, ok = compute_stock_value(text, cand["file_date"],
                                           _cik_by_ticker(hits),
                                           _name_ciks(hits))
            if comp:
                result["value_usd"] = comp["value_usd"]
                result["value_basis"] = "computed"
                result["value_note"] = comp["value_note"]
        return result, ok
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
    # All-stock MOE -> computed value (requires FMP_API_KEY in env):
    # 0.5958 × COLB $39.57 prior close × 220,133,236 UMPQ cover shares
    # ≈ $5.19B vs the press-reported ~$5.2B. Range-asserted (a data-vendor
    # close restatement shouldn't fail the smoke); unit tests pin the math.
    from data.fmp_client import _has_key
    if _has_key():
        assert r["value_basis"] == "computed", r
        assert 5_000_000_000 < r["value_usd"] < 5_400_000_000, r
        assert "0.5958" in (r["value_note"] or ""), r
    else:
        print("  (no FMP_API_KEY — computed-value leg not exercised)")

    r, ok = resolve_announcement("AmericanWest Bank", "Banner Bank",
                                 "2015-10-02")
    print("AmericanWest:", r, ok)
    assert ok and r and r["announce_date"] == "2014-11-05", r

    # Pre-EFTS deal -> honest, cacheable n/a
    r, ok = resolve_announcement("Whatcom State Bank", "Banner Bank",
                                 "1999-01-04")
    assert r is None and ok
    print("\nSMOKE OK: announcement resolution verified on known deals.")
