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
EDGAR-filed PR). An HTTP 404 on an archive document counts as NOT FOUND,
not as a fetch failure: EDGAR archives are immutable, so a missing document
(routine on 2001-vintage accessions) 404s identically on every retry —
treating it as transient kept those deals perpetually uncached and
re-fetched by every nightly refresh-deal-comps run.
"""

from __future__ import annotations

import html as _html
import re
import time
from datetime import date, datetime, timedelta

import requests

from data.http import is_http_404

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
_DOC_404_TTL_S = 90 * 86400     # a 404 on an immutable EDGAR archive document
                                # is permanent; remember it so the nightly job
                                # never re-fetches the same dead 2001-vintage
                                # docs. 90d self-heals against any freak miss.

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
    'umpqua', 'TD Bank Group' -> 'td'), or None when every token is generic
    ('First National Bank'). Match tokens with token_in (word-boundary) —
    short brands like 'td' must never substring-match ('ltd')."""
    for w in re.findall(r"[a-z0-9&']+", (name or "").lower()):
        if w not in _GENERIC and len(w) >= 2:
            return w
    return None


def token_in(tok: str | None, text: str) -> bool:
    """Word-boundary presence of a brand token in already-lowered text."""
    if not tok:
        return False
    return re.search("\\b" + re.escape(tok) + "\\b", text) is not None


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
    r"(?:approximately\s+|about\s+)?(?:US)?\$\s?([\d][\d,]*(?:\.\d+)?)\s*"
    r"(billion|million)", re.IGNORECASE)


def _strip_html(raw: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", _html.unescape(txt))


# ── Computed all-stock value (ratio × acquirer price × target shares) ─────

# Ratio forms — side phrases are COMMA-TOLERANT ("First Hawaiian, Inc.")
# and the bare form "2.095 First Hawaiian shares for each TriCo share" is
# covered (both live-verified on FHB/TriCo 2026-07-14; upstreamed from
# data/ma_pending).
_RATIO_RECEIVE_RE = re.compile(
    r"receive\s+(\d{1,2}(?:\.\d{1,4})?)\s+(?:of\s+a\s+share|shares?)\s+of\s+"
    r"([A-Z][\w.,&'\- ]{1,60}?)\s+(?:common\s+)?stock\s+for\s+each(?:\s+share\s+"
    r"of)?\s+([A-Z][\w.,&'\- ]{1,60}?)\s+(?:common\s+stock|shares?|stock)")
_RATIO_BARE_RE = re.compile(
    r"receive\s+(\d{1,2}(?:\.\d{1,4})?)\s+([A-Z][\w.,&'\- ]{1,60}?)\s+"
    r"shares?\s+for\s+each\s+([A-Z][\w.,&'\- ]{1,60}?)\s+shares?")
# "each share of Umpqua common stock will be converted into ... 0.5958
#  shares of Columbia common stock"
_RATIO_CONVERT_RE = re.compile(
    r"each\s+share\s+of\s+([A-Z][\w.,&'\- ]{1,60}?)\s+(?:common\s+)?stock\s+"
    r"(?:will\s+be|shall\s+be|is)\s+converted\s+into\s+(?:the\s+right\s+to\s+"
    r"receive\s+)?(\d{1,2}(?:\.\d{1,4})?)\s+(?:of\s+a\s+share|shares?)\s+of\s+"
    r"([A-Z][\w.,&'\- ]{1,60}?)\s+(?:common\s+)?stock")
# "Columbia Banking System, Inc. (NASDAQ: COLB)" -> name/ticker pairs
_PR_TICKER_RE = re.compile(
    r"([A-Z][\w.,&'\- ]{2,60}?)\s*\(\s*(?:[A-Z]{2,8}\s+and\s+)?"
    r"(?:NYSE(?:\s+American)?|NASDAQ|Nasdaq)\s*:\s*([A-Z]{1,6})\s*\)")
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
    for m in _RATIO_BARE_RE.finditer(text):
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
    hits = {tick for name, tick in pairs if token_in(tok, name.lower())}
    return hits.pop() if len(hits) == 1 else None


def _shares_outstanding_asof(cik, asof: str) -> tuple[int | None, str | None, bool]:
    """Target cover-page share count nearest ≤ ``asof`` from SEC companyfacts.
    Returns (shares, as-of end date, ok) — ok=False only on a TRANSIENT fetch
    failure. A permanent companyfacts 404 (non-reporting target CIK) yields no
    facts with ok=True: a cacheable honest gap, not an eternal retry."""
    from data.sec_client import fetch_company_facts_ok

    facts, facts_ok = fetch_company_facts_ok(int(cik))
    if not facts:
        return None, None, facts_ok     # 404 -> ok=True (gap); 5xx -> ok=False
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


def _side_cik(side_phrase: str, tick: str | None,
              cik_by_ticker: dict[str, int],
              name_ciks: list[tuple[str, int]] = ()) -> int | None:
    """CIK for a ratio side: ticker map first, then the display-NAME brand
    token (delisted filers), then the live bank mapping."""
    cik = cik_by_ticker.get(tick) if tick else None
    if not cik:
        tok = brand_token(side_phrase)
        cands = {c for n, c in (name_ciks or []) if token_in(tok, n)}
        if len(cands) == 1:
            cik = cands.pop()
    if not cik and tick:
        from data.bank_mapping import get_cik
        cik = get_cik(tick)
    return cik


def ratio_target_cik(text: str, cik_by_ticker: dict[str, int],
                     name_ciks: list[tuple[str, int]] = ()) -> int | None:
    """The exchange-ratio's per-share (TARGET) side resolved to a holdco CIK,
    or None. Deal comps pair the deal value with THIS entity's financials —
    in an MOE the FDIC bank-level survivor can be the opposite side of the
    holdco-level target (Columbia/Umpqua, live-verified), so the value's own
    ratio is the only safe source of the priced entity."""
    hit = extract_exchange_ratio(text)
    if not hit:
        return None
    _ratio, _acq_side, tgt_side = hit
    tgt_tick = _ticker_for_side(tgt_side, _pr_ticker_pairs(text))
    return _side_cik(tgt_side, tgt_tick, cik_by_ticker, name_ciks)


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

    tgt_cik = _side_cik(tgt_side, tgt_tick, cik_by_ticker, name_ciks)
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


# Trailing form: "$41.1 million in aggregate[, subject to adjustment]"
# (live-verified: Catalyst/Lakeside all-cash PR, 2026-04-08).
_VALUE_TRAIL_RE = re.compile(
    r"\$\s?([\d][\d,]*(?:\.\d+)?)\s*(million|billion)\s+in\s+"
    r"(?:the\s+)?aggregate", re.IGNORECASE)


def extract_stated_value(text: str) -> int | None:
    """Deal value in RAW DOLLARS from strict stated-value phrasings, or None.
    Distinct candidate amounts -> None (ambiguous, never a guess)."""
    vals = set()
    for num, unit in _VALUE_RE.findall(text) + _VALUE_TRAIL_RE.findall(text):
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


def _doc_404_key(cik, adsh: str, doc: str) -> str:
    return f"edgar_doc_404:v1:{int(cik)}:{adsh}:{doc}"


def _fetch_doc_text(cik, adsh: str, doc: str) -> tuple[str | None, bool]:
    """One EDGAR archive document as flattened text. Returns (text, ok):
    ok=False only on a TRANSIENT failure (timeout, 5xx — caller must not
    cache the miss). An HTTP 404 is (None, True): archives are immutable,
    the document will never appear, a cacheable honest gap. Unresolvable
    coordinates (no cik/adsh/doc) are equally permanent -> (None, True).

    A 404 is also remembered in a document-level negative cache
    (_DOC_404_TTL_S): a bank that legitimately fails to cache its ma_history
    that night (e.g. a transient EFTS 500 elsewhere in its build) would
    otherwise re-fetch the same dead 2001-vintage docs on every run. On a
    negative-cache hit we skip the network AND the log line entirely."""
    if not cik or not adsh or not doc:
        return None, True
    from data import cache
    from data.freshness import is_fresh

    key = _doc_404_key(cik, adsh, doc)
    if is_fresh(cache.get(key), _DOC_404_TTL_S):
        return None, True           # known-permanent 404 — no network, no log
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{adsh.replace('-', '')}/{doc}")
    try:
        resp = requests.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        return _strip_html(resp.text), True
    except Exception as e:
        print(f"[ma_announce] doc {adsh}/{doc} error: {type(e).__name__}: {e}")
        if is_http_404(e):
            try:
                cache.put(key, {"cached_at": datetime.now().isoformat()})
            except Exception as ce:
                print(f"[ma_announce] doc-404 cache put {adsh}/{doc}: {ce}")
            return None, True
        return None, False


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
        text, t_ok = _fetch_doc_text(cand["cik"], cand["adsh"], cand["doc"])
        if text is None:
            fetch_failed = fetch_failed or not t_ok
            continue
        low = text.lower()
        if tq.lower() not in low:
            continue
        if acq_tok and not token_in(acq_tok, low):
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
            "target_cik": ratio_target_cik(text, _cik_by_ticker(hits),
                                           _name_ciks(hits)),
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


# ── Terminated / withdrawn deals (EFTS sweep, owner-approved 2026-07-13) ──

# Announced-but-never-completed deals have no FDIC anchor. Sweep the subject
# HOLDCO's own 8-Ks (EFTS ciks filter) for "Agreement and Plan of Merger"
# mentions: groups whose 8-K items include 1.02 (Termination of Material
# Definitive Agreement) are termination candidates; each is back-linked to
# the latest PRIOR announcement-classified 8-K by the same filer, which
# supplies the counterparty (the PR ticker pair that isn't the subject),
# announce date, and deal value via the increment-A/B machinery.

_MERGER_PHRASE = '"Agreement and Plan of Merger"'
_TERM_TEXT_RE = re.compile(r"\bterminat(?:e|ed|ion|ing)\b", re.IGNORECASE)
_EX99_NAME_RE = re.compile(r"ex[-_.]?99|press", re.IGNORECASE)


def _split_merger_groups(hits: list[dict]) -> tuple[list[dict], list[dict]]:
    """Group a per-filer merger-phrase EFTS result by accession and split:
    1.02 groups = termination candidates; announce-item groups (1.01/7.01/
    8.01 — FHN/TD was Reg-FD-only, live-verified) = announcements.
    (_candidates isn't reusable here — it drops 1.02-only groups by design.)
    Terminations newest-first, announcements oldest-first."""
    terminations, announcements = [], []
    by_adsh: dict[str, dict] = {}
    for h in hits:
        src = h.get("_source", {})
        adsh = src.get("adsh") or (h.get("_id", "").split(":")[0])
        if not adsh or not src.get("file_date"):
            continue
        items = {str(i) for i in (src.get("items") or [])}
        is_ex99 = str(src.get("file_type") or "").upper().startswith("EX-99")
        cur = by_adsh.setdefault(adsh, {
            "adsh": adsh, "doc": h.get("_id", "").split(":")[-1],
            "file_date": src.get("file_date"),
            "cik": (src.get("ciks") or [None])[0],
            "items": set(), "is_ex99": is_ex99})
        cur["items"] |= items
        if is_ex99 and not cur["is_ex99"]:
            cur.update(doc=h.get("_id", "").split(":")[-1], is_ex99=True)
    for g in by_adsh.values():
        if "1.02" in g["items"]:
            terminations.append(g)
        elif g["items"] & _ANNOUNCE_ITEMS:
            announcements.append(g)
    terminations.sort(key=lambda g: g["file_date"], reverse=True)
    announcements.sort(key=lambda g: g["file_date"])
    return terminations, announcements


# Private-counterparty extraction for CASH deals (no ticker parens on a
# private target): the acquire-verb object, e.g. "Agreement to Acquire
# Lakeside Bancshares, Inc." (live-verified CLST PR, 2026-04-08). A leading
# "About "/"the " (section-header run-on) is stripped by the caller.
_ACQUIRE_OBJ_RE = re.compile(
    r"(?:agreement\s+to\s+acquire|will\s+acquire|to\s+acquire|"
    r"acquisition\s+of|acquire\s+100%\s+of\s+the\s+stock\s+of)\s+"
    r"([A-Z][\w.,&'\- ]{2,60}?)(?:\s*\(|\s+in\s+an?\s|,\s+the\s|\.\s|\s+and\s)")

# A captured company phrase is often a run-on across an "About X. X" PR
# footer (live: HOPE's TBNK pair captured "About Territorial Bancorp Inc.
# Territorial Bancorp Inc."). The real name is the LAST sentence piece —
# split on a lowercase-terminated sentence boundary (the [a-z] lookbehind
# keeps "U.S. Bancorp" intact) and keep the final multi-word piece.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[a-z])\.\s+(?=[A-Z])")


def _clean_company_name(phrase: str) -> str:
    """Normalize a captured company phrase to a single clean name: take the
    last sentence piece of an 'About X. X' footer run-on (only when that
    piece is itself multi-word), then strip a leading 'About '/'the '."""
    p = (phrase or "").strip(" .,")
    pieces = _SENTENCE_SPLIT_RE.split(p)
    if len(pieces) > 1 and len(pieces[-1].split()) >= 2:
        p = pieces[-1]
    p = re.sub(r"^\s*(?:about|the)\s+", "", p.strip(), flags=re.IGNORECASE)
    return p.strip(" .,")


def find_open_announcements(cik, subject_name: str) -> tuple[list[dict], bool]:
    """
    Recent announcement 8-Ks with NO completion/termination anchor IN THE
    ANNOUNCEMENT ITSELF — the CASH-deal pending candidate leg (stock deals
    come from data/ma_pending's Rule 425 episodes; a pure-cash deal files no
    425 at all). These are CANDIDATES only: the caller
    (ma_pending.find_pending_deals) confirms each is still open against the
    filer's later Item 2.01/1.02 8-Ks — presence of an announcement 8-K in
    the window is NOT proof the deal is unclosed (it persists after close).

    Returns ([{announce_date, direction, counterparty_name, counterparty_cik,
    value_usd, value_basis, value_note, target_cik, announce_url,
    accession}], ok). Strict: no counterparty cleanly extractable -> no row,
    never a guess.
    """
    if not cik:
        return [], True
    cik10 = f"{int(cik):010d}"
    subj_tok = brand_token(subject_name or "")
    try:
        resp = requests.get(EDGAR_FTS, params={
            "q": _MERGER_PHRASE, "forms": "8-K", "ciks": cik10,
            "dateRange": "custom", "startdt": _EFTS_FLOOR,
            "enddt": date.today().isoformat(),
        }, headers=_headers(), timeout=30)
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
    except Exception as e:
        print(f"[ma_announce] open sweep cik {cik10}: {type(e).__name__}: {e}")
        return [], False

    _terms, announcements = _split_merger_groups(hits)
    floor = (date.today() - timedelta(days=_WINDOW_DAYS)).isoformat()
    # A completion 8-K routinely carries 8.01/7.01 ALONGSIDE its 2.01 and
    # would otherwise classify as an announcement (live: UMB's Heartland
    # completion filing latched as the "announcement", making a closed deal
    # look pending). Any 2.01 in the group disqualifies it here.
    recent = [a for a in announcements
              if a["file_date"] >= floor and "2.01" not in a["items"]]

    # SELF tokens: the caller-supplied name may be empty (a bank with no FDIC
    # structure rows derives none) — the filer's own EFTS display names
    # always identify self (live bug: Catalyst picked ITSELF as counterparty
    # when subject_name came through empty).
    self_toks = {subj_tok} if subj_tok else set()
    for h in hits:
        for dn in (h.get("_source", {}).get("display_names") or []):
            m = _DISPLAY_CIK_RE.match((dn or "").strip())
            if m and int(m.group(2)) == int(cik):
                t = brand_token(m.group(1))
                if t:
                    self_toks.add(t)

    def _is_self(name: str) -> bool:
        low = (name or "").lower()
        return any(token_in(t, low) for t in self_toks)

    rows, fetch_failed, seen_toks = [], False, set()
    for ann in sorted(recent, key=lambda g: g["file_date"], reverse=True)[:3]:
        time.sleep(_PAUSE_S)
        text, t_ok = _accession_text(ann["cik"], ann["adsh"], ann["doc"])
        fetch_failed = fetch_failed or not t_ok
        if not text:
            continue
        if _COMPLETED_RE.search(text) or not _ANNOUNCE_RE.search(text):
            continue
        # Counterparty: a non-self ticker pair (name cleaned of the "About
        # X. X" footer run-on), else the acquire-verb object for a private
        # target — cleaned, self-excluded, recurring, prefer a fuller name.
        direction = "acquisition"
        counterparty = None
        pair_names = [_clean_company_name(n) for n, _t in _pr_ticker_pairs(text)]
        pair_names = [n for n in pair_names if n and not _is_self(n)]
        if pair_names:
            counterparty = pair_names[0]
        else:
            best = {}
            for m in _ACQUIRE_OBJ_RE.finditer(text):
                cand = _clean_company_name(m.group(1))
                t = brand_token(cand)
                if not t or _is_self(cand):
                    continue
                if len(re.findall("\\b" + re.escape(t) + "\\b",
                                  text.lower())) < 2:
                    continue
                # The headline carries the full name ("Lakeside Bancshares,
                # Inc."), the body the short one ("Lakeside") — keep the
                # longest cleaned capture per brand token.
                if len(cand) > len(best.get(t, "")):
                    best[t] = cand
            if len(best) == 1:
                counterparty = next(iter(best.values()))
        if not counterparty or _is_self(counterparty):
            continue
        ct = brand_token(counterparty)
        if not ct or ct in seen_toks:
            continue
        # Self as the acquire object -> we are the target (seller side).
        for m in _ACQUIRE_OBJ_RE.finditer(text):
            if _is_self(_clean_company_name(m.group(1))):
                direction = "sale"
                break
        seen_toks.add(ct)
        value = extract_stated_value(text)
        basis = "stated" if value else None
        note = None
        if value is None:
            comp, c_ok = compute_stock_value(text, ann["file_date"], {}, [])
            fetch_failed = fetch_failed or not c_ok
            if comp:
                value, basis = comp["value_usd"], "computed"
                note = comp["value_note"]
        rows.append({
            "announce_date": ann["file_date"],
            "direction": direction,
            "counterparty_name": counterparty,
            "counterparty_cik": None,
            "value_usd": value, "value_basis": basis, "value_note": note,
            "target_cik": None,
            "announce_url": (f"https://www.sec.gov/Archives/edgar/data/"
                             f"{int(ann['cik'])}/{ann['adsh'].replace('-', '')}/"
                             f"{ann['doc']}"),
            "accession": ann["adsh"],
        })
    return rows, not fetch_failed


def _accession_text(cik, adsh: str, primary_doc: str) -> tuple[str | None, bool]:
    """Primary document text PLUS the accession's EX-99 press-release
    exhibits, fetched via the filing index — the PR usually lacks the exact
    EFTS query phrase, so it is not among the phrase-matched documents, yet
    it is where the "(NYSE: XXX)" party pairs and deal values live
    (live-verified on FHN/TD). Returns (text, ok); ok=False on any TRANSIENT
    fetch failure so a partial read is never cached as a miss — an HTTP 404
    (document permanently absent from the immutable archive) keeps ok=True."""
    base, ok = _fetch_doc_text(cik, adsh, primary_doc)
    if base is None:
        return None, ok
    extra: list[str] = []
    try:
        resp = requests.get(
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{adsh.replace('-', '')}/", headers=_headers(), timeout=30)
        resp.raise_for_status()
        names = re.findall(r'href="[^"]*?([^"/]+\.htm)"', resp.text)
    except Exception as e:
        print(f"[ma_announce] index {adsh}: {type(e).__name__}: {e}")
        return base, is_http_404(e)
    for n in dict.fromkeys(names):
        if _EX99_NAME_RE.search(n) and n != primary_doc:
            time.sleep(_PAUSE_S)
            t, t_ok = _fetch_doc_text(cik, adsh, n)
            if t is None:
                ok = ok and t_ok
                continue
            extra.append(t)
            if len(extra) >= 2:
                break
    return " ".join([base] + extra), ok


def find_terminated_deals(subject_cik, subject_name: str) -> tuple[list[dict], bool]:
    """
    Terminated M&A deals for a holdco CIK, newest-first.

    Returns ([{termination_date, announce_date, counterparty_name,
               value_usd, value_basis, value_note, direction | None,
               announce_url, termination_url}], ok) — ok=False on any fetch
    failure (caller must not cache). Strict: a termination 8-K with no
    back-linkable announcement is DROPPED (never a counterparty guess).
    """
    if not subject_cik:
        return [], True
    cik10 = f"{int(subject_cik):010d}"
    subj_tok = brand_token(subject_name)

    try:
        resp = requests.get(EDGAR_FTS, params={
            "q": _MERGER_PHRASE, "forms": "8-K", "ciks": cik10,
            "dateRange": "custom", "startdt": _EFTS_FLOOR,
            "enddt": date.today().isoformat(),
        }, headers=_headers(), timeout=30)
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
    except Exception as e:
        print(f"[ma_announce] term sweep cik {cik10}: {type(e).__name__}: {e}")
        return [], False

    terminations, announcements = _split_merger_groups(hits)

    deals, fetch_failed = [], False
    for term in terminations:
        time.sleep(_PAUSE_S)
        term_text, t_ok = _accession_text(term["cik"], term["adsh"], term["doc"])
        fetch_failed = fetch_failed or not t_ok
        if term_text is None:
            continue
        if not _TERM_TEXT_RE.search(term_text):
            continue
        # Back-link: EARLIEST in-window prior announcement — the original
        # announcement precedes the deal's extension/amendment 8-Ks, which
        # also cite the merger agreement and would otherwise steal the date.
        prior = [a for a in announcements
                 if _EFTS_FLOOR <= a["file_date"] < term["file_date"]
                 and (date.fromisoformat(term["file_date"])
                      - date.fromisoformat(a["file_date"])).days <= _WINDOW_DAYS]
        linked = None
        for ann in prior:
            time.sleep(_PAUSE_S)
            ann_text, a_ok = _accession_text(ann["cik"], ann["adsh"], ann["doc"])
            fetch_failed = fetch_failed or not a_ok
            if ann_text is None:
                continue
            if _COMPLETED_RE.search(ann_text) or not _ANNOUNCE_RE.search(ann_text):
                continue
            # Counterparty = a non-subject PR ticker pair, drawn from BOTH
            # documents' pairs — the announcement PR may render the
            # counterparty in a form the pair regex can't read (TD's
            # quoted-abbrev style) while the termination PR has it clean,
            # and vice versa. Either way the counterparty's brand token
            # must appear in BOTH documents.
            pairs = (_pr_ticker_pairs(ann_text)
                     + _pr_ticker_pairs(term_text))
            others = [
                (n, t) for n, t in pairs
                if subj_tok and subj_tok not in n.lower()
                and (brand_token(n) or "") != ""
                and brand_token(n) in term_text.lower()
                and brand_token(n) in ann_text.lower()
            ]
            if not others:
                continue
            linked = (ann, ann_text, others)
            break
        if not linked:
            continue  # no guarded announcement — drop, never guess
        ann, ann_text, others = linked
        value = extract_stated_value(ann_text)
        basis = "stated" if value else None
        note = None
        if value is None:
            comp, ok = compute_stock_value(ann_text, ann["file_date"],
                                           _cik_by_ticker(hits), _name_ciks(hits))
            if not ok:
                fetch_failed = True
            if comp:
                value, basis = comp["value_usd"], "computed"
                note = comp["value_note"]
        # Direction only when the ratio names the subject as the per-share
        # (target) side; otherwise honest None.
        direction = None
        ratio_hit = extract_exchange_ratio(ann_text)
        if ratio_hit and subj_tok:
            if subj_tok in ratio_hit[2].lower():
                direction = "sale"
            elif subj_tok in ratio_hit[1].lower():
                direction = "acquisition"
        deals.append({
            "termination_date": term["file_date"],
            "announce_date": ann["file_date"],
            "counterparty_name": others[0][0],
            "value_usd": value,
            "value_basis": basis,
            "value_note": note,
            "direction": direction,
            "announce_url": (f"https://www.sec.gov/Archives/edgar/data/"
                             f"{int(ann['cik'])}/{ann['adsh'].replace('-', '')}/"
                             f"{ann['doc']}"),
            "termination_url": (f"https://www.sec.gov/Archives/edgar/data/"
                                f"{int(term['cik'])}/"
                                f"{term['adsh'].replace('-', '')}/{term['doc']}"),
        })
    return deals, not fetch_failed


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
    assert r and r["announce_date"] == "2021-10-12", r
    # All-stock MOE -> computed value (requires FMP_API_KEY in env):
    # 0.5958 × COLB $39.57 prior close × 220,133,236 UMPQ cover shares
    # ≈ $5.19B vs the press-reported ~$5.2B. Range-asserted (a data-vendor
    # close restatement shouldn't fail the smoke); unit tests pin the math.
    # Without the key this leg reports ok=False BY DESIGN (price lookup
    # unavailable — retry later), so ok is only asserted key-in-hand.
    from data.fmp_client import _has_key
    if _has_key():
        assert ok, r
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

    # Terminated-deal sweep — FHN/TD ground truth: announced 2022-02-28
    # (US$13.4B all-cash stated), mutually terminated 2023-05-04.
    terms, ok = find_terminated_deals(36966, "First Horizon Bank")
    print("FHN terminations:", terms, ok)
    assert ok and len(terms) == 1, terms
    assert terms[0]["termination_date"] == "2023-05-04", terms
    assert terms[0]["announce_date"] == "2022-02-28", terms
    assert terms[0]["counterparty_name"] == "TD Bank Group", terms
    assert terms[0]["value_usd"] == 13_400_000_000, terms
    # Control: Banner Corp (CIK 946673) has no terminated deals.
    terms, ok = find_terminated_deals(946673, "Banner Bank")
    assert ok and terms == [], terms
    print("\nSMOKE OK: announcement resolution verified on known deals.")
