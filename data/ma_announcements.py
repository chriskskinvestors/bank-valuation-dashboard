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
import random
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
_PAUSE_S = 0.3                  # EDGAR allows 10 req/s; the whole-accession
                                # reads (index + up to 3 documents) made 0.15s
                                # pacing burst past it (429s through the
                                # 2026-10-06 walk, then the 4h task timeout)
_MAX_REGATES = 4                # whole-accession re-gates per deal
_429_WAITS = (3, 6, 12)         # seconds before retrying a 429
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
# "receive either (1) 2.4589 shares of HBT Financial’s common stock for each
# share of Tri-County stock" (HBT/Tri-County 2026-08-10): an election list
# marker and a possessive on the acquirer side.
_RATIO_RECEIVE_RE = re.compile(
    r"receive\s+(?:either\s+)?(?:\(\d\)\s+)?(\d{1,2}(?:\.\d{1,4})?)\s+"
    r"(?:of\s+a\s+share|shares?)\s+of\s+"
    r"([A-Z][\w.,&'\- ]{1,60}?)(?:[’']s)?\s+(?:common\s+)?stock\s+for\s+each"
    r"(?:\s+share\s+of)?\s+([A-Z][\w.,&'\- ]{1,60}?)\s+(?:common\s+stock|shares?|stock)")
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
# Mixed consideration: "receive 2.5814 shares of Old Second common stock and
# $15.93 in cash for each share of Bancorp Financial's common stock"
# (live-verified OSBC PR 2025-02-25).
# Also "Prosperity will issue 0.3803 shares of Prosperity common stock and
# $11.36 in cash for each outstanding share of Stellar common stock"
# (live-verified PB PR 2026-01-28 — the deck states the same $39.08/share
# this form prices to: 0.3803 × $72.90 + $11.36).
_RATIO_MIXED_RE = re.compile(
    r"(?:receive|issue)\s+(\d{1,2}(?:\.\d{1,4})?)\s+(?:of\s+a\s+share|shares?)"
    r"\s+of\s+([A-Z][\w.,&'\- ]{1,60}?)\s+(?:common\s+)?stock\s+and\s+\$\s?"
    r"[\d,]+(?:\.\d+)?\s+in\s+cash\s+for\s+each\s+(?:outstanding\s+)?"
    r"(?:share\s+of\s+)?([A-Z][\w.,&'\- ]{1,60}?)(?:['’]s)?\s+"
    r"(?:common\s+stock|shares?|stock)")
# Merger-agreement summary with par values: "each share of common stock, par
# value $0.01 per share, of CrossFirst ("CrossFirst Common Stock") ... will be
# converted into the right to receive 0.6675 of a share (the "Exchange
# Ratio") of common stock, par value $0.001 per share, of Busey" (live-
# verified Busey 8-K 2024-08-27).
_RATIO_PARVALUE_RE = re.compile(
    r"each\s+share\s+of\s+common\s+stock,\s+par\s+value\s+\$[\d.]+\s+per\s+share,"
    r"\s+of\s+([A-Z][\w.&'\-]{1,30})\b.{0,320}?converted\s+into\s+the\s+right\s+"
    r"to\s+receive\s+(\d{1,2}(?:\.\d{1,4})?)\s+(?:of\s+a\s+share|shares?)\s*"
    r"(?:\(the\s+[\"“”']?Exchange\s+Ratio[\"“”']?\)\s*)?of\s+common\s+stock"
    r"(?:,\s+par\s+value\s+\$[\d.]+\s+per\s+share,?)?\s+of\s+([A-Z][\w.&'\-]{1,30})\b")
# "will receive a fixed exchange ratio of 2.63 shares of Esquire common stock
# for each share of Signature common stock" (Esquire/Signature wire release
# 2026-03-12).
_RATIO_FIXED_OF_RE = re.compile(
    r"exchange\s+ratio\s+of\s+(\d{1,2}(?:\.\d{1,4})?)\s+(?:of\s+a\s+share|shares?)"
    r"\s+of\s+([A-Z][\w.,&'\- ]{1,60}?)\s+(?:common\s+)?stock\s+for\s+each\s+share"
    r"\s+of\s+([A-Za-z][\w.,&'\- ]{1,60}?)\s+(?:common\s+stock|shares?|stock)")
# "based on the exchange ratio of 3.40x" (Richmond Mutual/Farmers Bancorp
# wire release 2025-11-12) — the sides are not named in the sentence.
_RATIO_OF_BARE_RE = re.compile(
    r"exchange\s+ratio\s+of\s+(\d{1,2}(?:\.\d{1,4})?)(?![\d.])x?\b"
    r"(?!\s+(?:of\s+a\s+share|shares?))")
# "$12.70 in cash and 1.0534 shares of TowneBank common stock for each share
# of blueharbor outstanding common stock" (TowneBank/blueharbor wire release
# 2026-10-06; lowercase brand on the target side).
_RATIO_CASH_AND_SHARES_RE = re.compile(
    r"\$\s?[\d,]+(?:\.\d+)?\s+in\s+cash\s+and\s+(\d{1,2}(?:\.\d{1,4})?)\s+shares?"
    r"\s+of\s+([A-Z][\w.&'\-]{1,30})\s+(?:common\s+)?stock\s+for\s+each\s+share"
    r"\s+of\s+([A-Za-z][\w.&'\- ]{1,40}?)\s+(?:outstanding\s+)?(?:common\s+)?"
    r"(?:stock|shares?)")
# Cash-first mixed form with a comma share count: "converted into the right
# to receive $69,850 in cash and approximately 2,869 Civista common shares"
# (live-verified CIVB 8-K 2025-07-11; the target is a 500-share bank). The
# target side is not named in the sentence -> "".
_RATIO_CASH_FIRST_RE = re.compile(
    r"right\s+to\s+receive\s+\$\s?[\d,]+(?:\.\d+)?\s+in\s+cash\s+and\s+"
    r"(?:approximately\s+)?(\d{1,3}(?:,\d{3})*(?:\.\d{1,4})?)\s+"
    r"([A-Z][\w.&'\-]{1,30})\s+(?:common\s+)?shares?\b")
# Election: "each share of VBI common stock will be converted ... into the
# right to receive (i) $1,000.00 in cash, (ii) 38.5000 shares of Seacoast
# common stock" (live-verified SBCF 8-K 2025-05-29).
_RATIO_ELECTION_RE = re.compile(
    r"each\s+share\s+of\s+([A-Z][\w.,&'\- ]{1,60}?)\s+(?:common\s+)?stock\s+"
    r"(?:will\s+be|shall\s+be|is)\s+converted[^.;]{0,60}?right\s+to\s+receive"
    r".{0,80}?\(ii\)\s+(\d{1,3}(?:\.\d{1,4})?)\s+shares?\s+of\s+"
    r"([A-Z][\w.,&'\- ]{1,60}?)\s+(?:common\s+)?stock")
# Merger-agreement summary form: "into a right to receive 0.5500 (the
# "Exchange Ratio") shares of common stock, $0.625 par value, of QNB ("QNB
# Common Stock" ...)" (live-verified QNBC 8-K Item 1.01, 2025-09-23). The
# per-share (target) side is not named in the sentence -> "" (callers treat
# an empty side as unresolvable; the ratio itself still prices the offer).
_RATIO_PAREN_RE = re.compile(
    r"receive\s+(\d{1,2}(?:\.\d{1,4})?)\s+\(the\s+[\"“”']?Exchange\s+Ratio"
    r"[\"“”']?\)\s+shares?\s+of\s+common\s+stock,?\s+(?:\$[\d.]+\s+par\s+"
    r"value(?:\s+per\s+share)?,?\s+)?of\s+([A-Z][\w.,&'\- ]{1,40}?)\s*\(")
# "Columbia Banking System, Inc. (NASDAQ: COLB)" -> name/ticker pairs
# A defined-term parenthetical may sit between the name and the ticker
# ('Capital Bancorp, Inc. ("Capital") (NASDAQ: CBNK)', Peoples 2026-09-30);
# OTC sellers list as "(OTC: TYFG)" (HBT/Tri-County 2026-08-10).
_PR_TICKER_RE = re.compile(
    r"([A-Z][\w.,&'\- ]{2,60}?)\s*(?:\(\s*[“\"][^”\"]{1,40}[”\"]\s*\)\s*)?"
    r"\(\s*(?:[A-Z]{2,8}\s+and\s+)?"
    r"(?:NYSE(?:\s+American)?|NASDAQ|Nasdaq|OTCQX|OTCQB|OTC\s+Pink|OTC)\s*:\s*"
    r"([A-Z]{1,6})\s*\)")
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
    for m in _RATIO_MIXED_RE.finditer(text):
        found.append((float(m.group(1)), m.group(2).strip(), m.group(3).strip()))
    for m in _RATIO_ELECTION_RE.finditer(text):
        found.append((float(m.group(2)), m.group(3).strip(), m.group(1).strip()))
    for m in _RATIO_PAREN_RE.finditer(text):
        found.append((float(m.group(1)), m.group(2).strip(), ""))
    for m in _RATIO_CASH_FIRST_RE.finditer(text):
        found.append((float(m.group(1).replace(",", "")), m.group(2).strip(), ""))
    for m in _RATIO_PARVALUE_RE.finditer(text):
        found.append((float(m.group(2)), m.group(3).strip(), m.group(1).strip()))
    for m in _RATIO_FIXED_OF_RE.finditer(text):
        found.append((float(m.group(1)), m.group(2).strip(), m.group(3).strip()))
    for m in _RATIO_CASH_AND_SHARES_RE.finditer(text):
        found.append((float(m.group(1)), m.group(2).strip(), m.group(3).strip()))
    for m in _RATIO_OF_BARE_RE.finditer(text):
        found.append((float(m.group(1)), "", ""))
    if not found or len({r for r, _, _ in found}) != 1:
        return None
    # Several forms agreeing on one ratio: prefer the one that names sides.
    return max(found, key=lambda f: (bool(f[1]), bool(f[2])))


_NAME_CONNECTORS = {"of", "and", "&", "the", "de", "la", "du", "for"}


def _trailing_name(phrase: str) -> str:
    """The trailing run of capitalized words (connectors allowed) of a
    captured name phrase: the pair regex starts at the earliest uppercase
    letter within its window, so "FOR the proposed merger with Middlefield
    Banc Corp" (Farmers 2026-01-13) is Middlefield Banc Corp. A phrase with
    no capitalized tail (a lowercase brand) is returned as captured."""
    words = phrase.split()
    keep: list[str] = []
    for w in reversed(words):
        if w[:1].isupper() or w.lower() in _NAME_CONNECTORS or w == "&":
            keep.append(w)
        else:
            break
    while keep and keep[-1].lower() in _NAME_CONNECTORS:
        keep.pop()               # a leading connector is not a name start
    return " ".join(reversed(keep)) if keep else phrase


def _pr_ticker_pairs(text: str) -> list[tuple[str, str]]:
    """[(company name phrase, ticker)] from '(NASDAQ: XXXX)' mentions."""
    return [(_trailing_name(m.group(1).strip()), m.group(2))
            for m in _PR_TICKER_RE.finditer(text)]


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


# "the aggregate value of merger consideration to be paid by Seacoast would
# be approximately $110 million" (live-verified SBCF/Heartland PR 2025-02-27).
_VALUE_CONSID_RE = re.compile(
    r"aggregate\s+value\s+of\s+(?:the\s+)?merger\s+consideration[^.$]{0,80}?"
    r"\$\s?([\d][\d,]*(?:\.\d+)?)\s*(billion|million)", re.IGNORECASE)


def extract_stated_value(text: str) -> int | None:
    """Deal value in RAW DOLLARS from strict stated-value phrasings, or None.
    Distinct candidate amounts -> None (ambiguous, never a guess)."""
    vals = set()
    for num, unit in (_VALUE_RE.findall(text) + _VALUE_TRAIL_RE.findall(text)
                      + _VALUE_CONSID_RE.findall(text)):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        # round, not truncate: int(2.002 × 1e9) is 2,001,999,999 — off by a
        # dollar AND distinct from the deck's "$2,002 million" (ambiguity ->
        # n/a for a value both documents state).
        vals.add(int(round(v * (1_000_000_000 if unit.lower() == "billion"
                                else 1_000_000))))
    if len(vals) != 1:
        return None
    return vals.pop()


# ── Deal terms (Recent Deals tab — owner directive 2026-10-05) ────────────
#
# Structured merger terms read off the SAME announcement text the legs above
# already hold (PR + 425 legend + merger-agreement 8-K where the corpus has
# it). Every extractor is STRICT in the house style: several DISTINCT
# candidates -> None; nothing inferred; n/a over a plausible-wrong number.
# Ground truth hand-read from the live filings (2026-10-05):
#   FHB/TriCo 2026-07-13 (all-stock): 2.095 ratio; "representing $63.12 per
#     share" stated; "expect to close the transaction by the end of 2026";
#     "termination fee of $80,000,000" (merger-agreement 8-K Item 1.01); no
#     premium stated. The PR's "cash in lieu of fractional shares" must NOT
#     read as cash consideration, and the deck's "Premium deposit franchise"
#     / "Core deposit premium" must NOT read as a price premium.
#   Catalyst/Lakeside 2026-04-08 (all-cash): "$19.58 in cash for each
#     outstanding share" / "$19.58 per share in cash"; "expected to close in
#     the third quarter of 2026".
#   Old Second/Bancorp Financial 2025-02-25 (mixed, fixed): "2.5814 shares of
#     Old Second common stock and $15.93 in cash for each share"; "approximately
#     75% stock and 25% cash"; close "third quarter of 2025"; fee $8,500,000.
#   Seacoast/Villages 2025-05-29 (election): "(i) $1,000.00 in cash, (ii)
#     38.5000 shares of Seacoast common stock or (iii) a 25%-75% combination
#     ... at the shareholder's election"; proration 25% cash / 75% stock;
#     fee "$31.4 million".
#   QNB/Victory 2025-09-23: "termination fee to QNB of $1,575,000"; close
#     "fourth quarter of 2025 or first quarter of 2026" (a stated range -> its
#     later bound, phrase kept verbatim).

_NUM = r"(\d{1,4}(?:,\d{3})*(?:\.\d{1,4})?)"

# Per-share cash needs PER-SHARE context — "$19.58 in cash for each
# (outstanding) share", "$19.58 per share in cash", "$15.93 in cash (the
# "Cash Consideration") for each share", "each share ... converted into the
# right to receive $69,850 in cash" (Civista/Farmers Savings: 500-share
# target, live-verified), "right to receive (i) $1,000.00 in cash, (ii)"
# (election list). A bare "$32,500,000 in cash" / "$16,832,742 in cash" is
# the AGGREGATE cash leg (Equity/Frontier, FS Bancorp/Pacific West,
# MetroCity/First IC — all rendered as per-share on the first universe
# run, the plausible-wrong class) and must not match.
_CASH_PER_SHARE_RE = re.compile(
    r"\$\s?" + _NUM + r"\s+per\s+share\s+in\s+cash\b"
    r"|\$\s?" + _NUM + r"\s+in\s+cash(?:\s*\([^)]{0,40}\))?\s+(?:for\s+each|per)\s+"
    r"(?:\w+\s+){0,3}?share\b"
    r"|each\s+share[^.]{0,160}?converted\s+(?:at\s+closing\s+)?into\s+the\s+"
    r"right\s+to\s+receive\s+(?:\(i\)\s+)?\$\s?" + _NUM + r"\s+in\s+cash\b"
    # "$11.36 in cash and 0.3803 PB common shares for each STEL common share"
    r"|\$\s?" + _NUM + r"\s+in\s+cash\s+and\s+[^$;]{0,80}?for\s+each\s+"
    r"(?:\w+\s+){0,4}?share\b", re.IGNORECASE)
_CASH_CONSID_RE = re.compile(
    r"cash\s+consideration\s+of\s+\$\s?" + _NUM + r"\s+per\s+share",
    re.IGNORECASE)
_NUM_SHARES_NEAR_RE = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?\s+(?:\w+\s+){0,3}?shares\b", re.IGNORECASE)

# Stated per-share value of the offer: "representing $63.12 per share",
# "the implied purchase price is $62.60 per Bancorp Financial common
# share", "implied value of $25.00 per share", "per share deal value of
# $19.58".
_IMPLIED_PRICE_RE = re.compile(
    r"(?:representing|represents|implied\s+(?:purchase\s+)?(?:value|price)\s+(?:of|is)|implies\s+"
    r"a\s+value\s+of|valued\s+at|a\s+(?:total\s+)?value\s+of|equates\s+to)\s+"
    r"(?:approximately\s+|about\s+)?\$\s?" + _NUM +
    r"\s+per\s+(?:\w+\s+){0,3}?share\b", re.IGNORECASE)
_IMPLIED_PRICE_DECK_RE = re.compile(
    r"per\s+share\s+(?:deal\s+|transaction\s+)?value\s+(?:of|equates\s+to|is)\s+"
    r"\$\s?" + _NUM, re.IGNORECASE)
# "the aggregate transaction value is approximately $728.1 million, or
# $43.75 per share" (Peoples/Capital 2026-09-30).
_IMPLIED_PRICE_OR_RE = re.compile(
    r"(?:million|billion),?\s+or\s+(?:approximately\s+|about\s+)?\$\s?" + _NUM
    + r"\s+per\s+(?:\w+\s+){0,3}?share\b", re.IGNORECASE)

# Premium as STATED: "a premium of approximately 28% to ..." / "a 28%
# premium to the closing price". Window-gated to PRICE context (closing /
# unaffected / trading / VWAP / market price) and rejected near "deposit" or
# "book" (core-deposit premium, P/TBV premium are different animals).
_PREMIUM_A_RE = re.compile(
    r"premium\s+of\s+(?:approximately\s+|about\s+)?(\d{1,3}(?:\.\d+)?)\s?%",
    re.IGNORECASE)
_PREMIUM_B_RE = re.compile(
    r"(\d{1,3}(?:\.\d+)?)\s?%\s+premium\b", re.IGNORECASE)
_PRICE_CTX = ("price", "closing", "close", "trading", "vwap", "market",
              "unaffected")
_NOT_PRICE_CTX = ("deposit", "book", "tangible")

# Termination fee: "termination fee of $80,000,000", "termination fee to QNB
# of $1,575,000", "termination fee of $31.4 million", "a $10 million
# termination fee". Dollar amounts only — a "4% of deal value" fee is n/a.
# The gap between "termination fee" and the amount admits only a payee
# clause ("to QNB", "payable by VBI to Seacoast") — a loose gap once reached
# across "... total assets of $5.3 billion". A unit word after a comma-
# grouped number ("$5,300,000 million", Bank First/Centre 1.01 — a filer
# typo) is contradictory -> that candidate is dropped, never multiplied.
_TERM_FEE_A_RE = re.compile(
    r"termination\s+fee(?:\s+(?:payable\s+)?(?:to|by)\s+[A-Z][\w.&'\- ]{0,40}?)?"
    r"\s+(?:of|equal\s+to|in\s+the\s+amount\s+of)\s+(?:up\s+to\s+)?"
    r"(?:approximately\s+)?\$\s?" + _NUM + r"\s*(million|billion)?",
    re.IGNORECASE)
_TERM_FEE_B_RE = re.compile(
    r"\$\s?" + _NUM + r"\s*(million|billion)?\s+termination\s+fee",
    re.IGNORECASE)

# Expected close, as stated. The time phrase is captured up to the sentence
# or the "subject to" clause and kept VERBATIM; the date is derived only
# from quarter / half / month / year-end wording (a range -> its later bound).
_EXPECTED_CLOSE_RE = re.compile(
    r"(?:(?:expected|anticipated|expects?|anticipates?|expect)\s+to\s+"
    r"(?:close|be\s+completed|be\s+consummated|complete|consummate)"
    r"(?:\s+(?:the|this)\s+(?:transaction|merger|acquisition|mergers))?"
    r"|(?:closing|completion)\s+(?:of\s+the\s+(?:transaction|merger|"
    r"acquisition|mergers)\s+)?is\s+(?:expected|anticipated))\s+"
    r"((?:in|during|by|on\s+or\s+before|before|prior\s+to|late\s+in|early"
    r"\s+in|around)\s+[^.;]{3,90})", re.IGNORECASE)
_CLOSE_CUT_RE = re.compile(
    r",?\s+(?:subject\s+to|pending|assuming|contingent|following|and\s+is|"
    r"which|with\s+the)\b", re.IGNORECASE)
_ORD = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
        "fourth": 4, "4th": 4}
_Q_RE = re.compile(
    r"\b(first|second|third|fourth|1\s?st|2\s?nd|3\s?rd|4\s?th)\s+(?:calendar\s+|"
    r"fiscal\s+)?quarter\s+(?:of\s+)?(20\d\d)\b|\bq([1-4])\s*(20\d\d)\b",
    re.IGNORECASE)
_HALF_RE = re.compile(
    r"\b(first|second)\s+half\s+of\s+(20\d\d)\b", re.IGNORECASE)
_YEAR_END_RE = re.compile(
    r"\b(?:end\s+of|year[-\s]end)\s+(20\d\d)\b", re.IGNORECASE)
_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")
_MONTH_RE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\s+(?:(\d{1,2}),\s+)?(20\d\d)\b",
    re.IGNORECASE)

# Consideration-mix phrasings and the stated split.
_ALL_STOCK_RE = re.compile(
    r"all[-\s]stock|100\s?%\s+(?:common\s+)?stock|stock[-\s]for[-\s]stock",
    re.IGNORECASE)
_ALL_CASH_RE = re.compile(r"all[-\s]cash|100\s?%\s+cash", re.IGNORECASE)
_ELECTION_RE = re.compile(
    r"(?:shareholder|stockholder|holder)s?['’]?s?\s+election|elect(?:ion)?\s+"
    r"to\s+receive|may\s+elect", re.IGNORECASE)
# A stock leg the ratio regexes could not parse: "exchange ratio" wording or
# "0.3803 ... shares" near the cash. Cash + an unparsed stock leg must NOT
# classify as all-cash (the Prosperity/Stellar $11.36 would have shown as
# the whole per-share price — exactly the plausible-wrong class).
_STOCK_LEG_RE = re.compile(
    r"exchange\s+ratio|(?<!\$)\b\d{1,2}\.\d{2,4}\s+(?:\w+\s+){0,3}?shares\b",
    re.IGNORECASE)
# Fixed share count for the whole target ("will issue 4,062,520 shares of
# Prosperity common stock for all outstanding shares of Southwest") — stock
# consideration with no per-share ratio (live-verified PB/Texas Partners).
_FIXED_SHARES_RE = re.compile(
    r"issue\s+[\d,]{5,}\s+shares\s+of\s+[A-Z][\w.,&'\- ]{1,60}?\s+(?:common\s+)?"
    r"stock\s+for\s+all\s+(?:of\s+the\s+)?outstanding\s+shares", re.IGNORECASE)
_MIX_STOCK_CASH_RE = re.compile(
    r"(\d{1,3})\s?%\s+(?:common\s+)?stock\s*(?:and|/)\s*(\d{1,3})\s?%\s+cash",
    re.IGNORECASE)
_MIX_CASH_STOCK_RE = re.compile(
    r"(\d{1,3})\s?%\s+cash\s*(?:and|/)\s*(\d{1,3})\s?%\s+(?:common\s+)?stock",
    re.IGNORECASE)
_PRORATION_RE = re.compile(
    r"(\d{1,3})\s?%\s+of\s+[^.]{0,80}?receive\s+the\s+cash\s+consideration"
    r"\s+and\s+(\d{1,3})\s?%\s+[^.]{0,80}?receive\s+the\s+stock\s+"
    r"consideration", re.IGNORECASE)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _single(values) -> float | int | None:
    """The one distinct value, else None (ambiguous -> never a guess)."""
    vals = set(values)
    return vals.pop() if len(vals) == 1 else None


def extract_cash_per_share(text: str) -> float | None:
    """Cash consideration per TARGET share, or None. "cash in lieu of
    fractional shares" carries no dollar figure and never matches."""
    found = []
    for rx in (_CASH_PER_SHARE_RE, _CASH_CONSID_RE):
        for m in rx.finditer(text):
            found.append(round(_num(next(g for g in m.groups() if g)), 4))
    return _single(found)


def _cash_with_unparsed_stock_leg(text: str) -> bool:
    """True when a per-share cash mention shares its sentence with an "N
    shares" stock leg the ratio forms did not parse ("$69,850 in cash and
    approximately 2,869 Civista common shares") — all-cash would then be a
    plausible-wrong classification."""
    for rx in (_CASH_PER_SHARE_RE, _CASH_CONSID_RE):
        for m in rx.finditer(text):
            lo = text.rfind(". ", 0, m.start()) + 1
            hi = text.find(". ", m.end())
            sent = text[lo:hi if hi > 0 else m.end() + 200]
            if _NUM_SHARES_NEAR_RE.search(sent):
                return True
    return False


def extract_implied_price(text: str) -> float | None:
    """Per-share offer value as STATED in the text, or None."""
    found = [round(_num(m.group(1)), 4)
             for rx in (_IMPLIED_PRICE_RE, _IMPLIED_PRICE_DECK_RE, _IMPLIED_PRICE_OR_RE)
             for m in rx.finditer(text)]
    return _single(found)


def extract_premium_pct(text: str) -> float | None:
    """Premium to the target's market price, as STATED (percent), or None.
    Only price-context premiums count; deposit/book premiums are skipped."""
    found = []
    for rx in (_PREMIUM_A_RE, _PREMIUM_B_RE):
        for m in rx.finditer(text):
            before = text[max(0, m.start() - 60):m.start()].lower()
            after = text[m.end():m.end() + 160].lower()
            if any(w in before for w in _NOT_PRICE_CTX) \
                    or any(w in after[:60] for w in _NOT_PRICE_CTX):
                continue
            if not any(w in after for w in _PRICE_CTX):
                continue
            found.append(round(_num(m.group(1)), 2))
    return _single(found)


def extract_termination_fee(text: str) -> int | None:
    """Termination fee in RAW DOLLARS, or None (absent, non-dollar, or
    several distinct amounts — e.g. a deal with two different fees)."""
    found = []
    for rx in (_TERM_FEE_A_RE, _TERM_FEE_B_RE):
        for m in rx.finditer(text):
            v = _num(m.group(1))
            unit = (m.group(2) or "").lower()
            if unit and v >= 10_000:
                continue            # "$5,300,000 million" — contradictory
            if unit == "billion":
                v *= 1_000_000_000
            elif unit == "million":
                v *= 1_000_000
            elif v < 1000:
                continue            # "$4 termination fee" — not a fee amount
            found.append(int(round(v)))
    return _single(found)


def _quarter_end(q: int, year: int) -> str:
    m = q * 3
    d = {3: 31, 6: 30, 9: 30, 12: 31}[m]
    return f"{year:04d}-{m:02d}-{d:02d}"


def _month_end(month: int, year: int) -> str:
    import calendar
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def close_phrase_to_date(phrase: str) -> str | None:
    """ISO period-end for an expected-close phrase, or None when the wording
    does not pin a period (e.g. "early 2027", "mid-2027", "later this year").
    quarter -> quarter end; half -> Jun 30 / Dec 31; "end of 2026" ->
    Dec 31; "March 2027" -> month end; "March 31, 2027" -> that day; a
    range ("Q4 2025 or Q1 2026") -> the later bound."""
    dates = []
    for m in _Q_RE.finditer(phrase):
        if m.group(1):
            dates.append(_quarter_end(_ORD[m.group(1).lower().replace(" ", "")],
                                      int(m.group(2))))
        else:
            dates.append(_quarter_end(int(m.group(3)), int(m.group(4))))
    for m in _HALF_RE.finditer(phrase):
        dates.append(f"{int(m.group(2))}-06-30" if m.group(1).lower() == "first"
                     else f"{int(m.group(2))}-12-31")
    for m in _YEAR_END_RE.finditer(phrase):
        dates.append(f"{int(m.group(1))}-12-31")
    for m in _MONTH_RE.finditer(phrase):
        month = _MONTHS.index(m.group(1).lower()) + 1
        year = int(m.group(3))
        if m.group(2):
            dates.append(f"{year:04d}-{month:02d}-{int(m.group(2)):02d}")
        else:
            dates.append(_month_end(month, year))
    return max(dates) if dates else None


def extract_expected_close(text: str) -> tuple[str | None, str | None]:
    """(phrase, date) — the stated expected-close wording (verbatim, cut at
    the "subject to" clause) and its period-end date. Several statements
    that resolve to the SAME date are fine (PR + 8-K); distinct dates ->
    phrase kept, date None. The phrase reported is the first one that
    pins a period (a 425 legend's "by the end of the year" must not mask
    the PR's "by the end of 2026")."""
    phrases, dated = [], []
    for m in _EXPECTED_CLOSE_RE.finditer(text):
        raw = _CLOSE_CUT_RE.split(m.group(1), maxsplit=1)[0]
        raw = " ".join(raw.split()).strip(" ,")
        if not raw:
            continue
        phrases.append(raw)
        d = close_phrase_to_date(raw)
        if d:
            dated.append((raw, d))
    if not phrases:
        return None, None
    if not dated:
        return phrases[0], None
    if len({d for _p, d in dated}) != 1:
        return dated[0][0], None
    return dated[0]


def extract_mix_pcts(text: str) -> tuple[float, float] | None:
    """(stock %, cash %) as stated ("75% stock and 25% cash"; a proration
    "25% ... cash consideration and 75% ... stock consideration"), or None."""
    found = set()
    for m in _MIX_STOCK_CASH_RE.finditer(text):
        found.add((float(m.group(1)), float(m.group(2))))
    for m in _MIX_CASH_STOCK_RE.finditer(text):
        found.add((float(m.group(2)), float(m.group(1))))
    for m in _PRORATION_RE.finditer(text):
        found.add((float(m.group(2)), float(m.group(1))))
    found = {p for p in found if abs(p[0] + p[1] - 100) < 0.01}
    return found.pop() if len(found) == 1 else None


def classify_consideration(text: str, ratio, cash) -> str | None:
    """'stock' | 'cash' | 'mixed' | 'election' | None from the extracted
    components first, the PR's own wording second."""
    if ratio and cash:
        return "election" if _ELECTION_RE.search(text) else "mixed"
    if ratio:
        return "stock"
    if cash:
        if _STOCK_LEG_RE.search(text) or _cash_with_unparsed_stock_leg(text):
            return None
        return "cash"
    if _FIXED_SHARES_RE.search(text) and not _ALL_CASH_RE.search(text):
        return "stock"
    if _ALL_STOCK_RE.search(text) and not _ALL_CASH_RE.search(text):
        return "stock"
    if _ALL_CASH_RE.search(text) and not _ALL_STOCK_RE.search(text):
        return "cash"
    return None


def implied_offer(terms: dict, acq_price, *, basis_label: str) -> tuple[float | None, str | None]:
    """Per-share offer value at an acquirer price, from the terms'
    consideration structure. (value, note) or (None, None) when the structure
    cannot be priced honestly. Shared by the at-announce leg (prior close)
    and the live merger-arb leg (current price)."""
    mix = terms.get("consideration")
    ratio, cash = terms.get("exchange_ratio"), terms.get("cash_per_share")
    if mix == "cash" and cash:
        return float(cash), f"${cash:,.2f} cash per share"
    if acq_price is None:
        return None, None
    if mix == "stock" and ratio:
        return (round(ratio * acq_price, 4),
                f"{ratio} × {basis_label} ${acq_price:,.2f}")
    if mix == "mixed" and ratio and cash:
        return (round(ratio * acq_price + cash, 4),
                f"{ratio} × {basis_label} ${acq_price:,.2f} + ${cash:,.2f} cash")
    if mix == "election" and ratio and cash:
        pcts = terms.get("stock_pct"), terms.get("cash_pct")
        if pcts[0] is None or pcts[1] is None:
            return None, None
        v = ratio * acq_price * pcts[0] / 100 + cash * pcts[1] / 100
        return (round(v, 4),
                f"blended at the stated {pcts[0]:g}% stock / {pcts[1]:g}% cash "
                f"proration: {ratio} × {basis_label} ${acq_price:,.2f} and "
                f"${cash:,.2f} cash")
    return None, None


def extract_terms(text: str) -> dict:
    """Pure (no network) term extraction from announcement text."""
    ratio_hit = extract_exchange_ratio(text)
    ratio = ratio_hit[0] if ratio_hit else None
    cash = extract_cash_per_share(text)
    pcts = extract_mix_pcts(text)
    phrase, close_date = extract_expected_close(text)
    return {
        "consideration": classify_consideration(text, ratio, cash),
        "exchange_ratio": ratio,
        "acq_side": ratio_hit[1] if ratio_hit else None,
        "tgt_side": ratio_hit[2] if ratio_hit else None,
        "cash_per_share": cash,
        "stock_pct": pcts[0] if pcts else None,
        "cash_pct": pcts[1] if pcts else None,
        "implied_price_stated": extract_implied_price(text),
        "premium_pct": extract_premium_pct(text),
        "expected_close_phrase": phrase,
        "expected_close_date": close_date,
        "termination_fee_usd": extract_termination_fee(text),
    }


def build_terms(text: str, announce_date: str, acq_tick: str | None = None,
                tgt_tick: str | None = None, close_lookup=None) -> tuple[dict, bool]:
    """
    Deal terms for one announcement: extract_terms plus the acquirer's close
    before announce (press convention, _close_before) and the implied
    per-share price at announce — stated when the PR states it, else
    computed from the consideration structure and labeled so.

    ``acq_tick``/``tgt_tick`` override the PR-pair resolution of the ratio's
    sides (ma_pending resolves them through the live universe);
    ``close_lookup`` is the price function (default _close_before — callers
    that bind their own name pass it so one seam serves both). Returns
    (terms, ok): ok=False only when the price lookup FAILED (no FMP key /
    fetch error) — the caller must not cache; every absent field is an
    honest None.
    """
    t = extract_terms(text)
    pairs = _pr_ticker_pairs(text)
    if t["exchange_ratio"]:
        acq_tick = acq_tick or _ticker_for_side(t["acq_side"], pairs)
        tgt_tick = tgt_tick or _ticker_for_side(t["tgt_side"], pairs)
        if not acq_tick or not tgt_tick:
            # No "(NASDAQ: XXXX)" pair for a side (OTC acquirers such as QNB
            # Corp., merger-agreement summaries that name parties by their
            # defined terms): resolve through the live universe by brand
            # token — unique hit only, as ma_pending does (n/a over a wrong
            # link). Lazy import: ma_pending imports this module.
            from data.ma_pending import _universe_match
            if not acq_tick and t["acq_side"]:
                acq_tick = _universe_match(t["acq_side"])[0]
            if not tgt_tick and t["tgt_side"]:
                tgt_tick = _universe_match(t["tgt_side"])[0]
    ok = fill_implied_price(t, announce_date, acq_tick=acq_tick, tgt_tick=tgt_tick,
                            close_lookup=close_lookup)
    return t, ok


def fill_implied_price(t: dict, announce_date: str, acq_tick: str | None = None,
                       tgt_tick: str | None = None, close_lookup=None) -> bool:
    """Complete the tickers, the acquirer's close before announce and the
    implied per-share price on a terms dict — the tail of build_terms as
    its own seam, so a leg that built terms WITHOUT the acquirer's ticker
    (the 425 and announcement-8-K legs know only the filer's CIK; live
    2026-10-06 the board showed Peoples/Capital's 1.11 ratio with no
    implied price) can finish the row once the filer's ticker is known.
    Returns ok — False only when the price lookup FAILED."""
    acq_tick = acq_tick or t.get("acq_ticker")
    tgt_tick = tgt_tick or t.get("tgt_ticker")
    t["acq_ticker"] = acq_tick
    t["tgt_ticker"] = tgt_tick
    close, close_date, ok = None, None, True
    if t.get("exchange_ratio") and acq_tick and announce_date:
        close, close_date, ok = (close_lookup or _close_before)(acq_tick, announce_date)
    t["acq_close_at_announce"] = close
    t["acq_close_date"] = close_date
    if t.get("implied_price_stated") is not None:
        t["implied_price"] = t["implied_price_stated"]
        t["implied_price_basis"] = "stated"
        t["implied_price_note"] = "per-share value as stated in the announcement"
    else:
        v, note = implied_offer(t, close, basis_label=f"{acq_tick} close {close_date}")
        t["implied_price"] = v
        t["implied_price_basis"] = "computed" if v is not None else None
        t["implied_price_note"] = f"computed: {note}" if note else None
    return ok


def _get_429_aware(url: str, params: dict | None = None):
    """requests.get with a 429 backoff (EDGAR's "Too Many Requests"). Kept on
    plain requests.get — not data.http.get_with_retry — so the suites' wire
    mocks on this module's requests.get still intercept every fetch."""
    resp = None
    for i, wait in enumerate((0,) + _429_WAITS):
        if wait:
            time.sleep(wait + random.uniform(0, 1))
        resp = requests.get(url, params=params, headers=_headers(), timeout=30)
        if getattr(resp, "status_code", None) != 429:
            break
    return resp


def _efts_hits(target_query: str, startdt: str, enddt: str) -> list[dict] | None:
    """EFTS hits for a quoted phrase over 8-Ks in a window; None on failure."""
    try:
        resp = _get_429_aware(EDGAR_FTS, params={
            "q": f'"{target_query}"', "forms": "8-K",
            "dateRange": "custom", "startdt": startdt, "enddt": enddt,
        })
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
                             "is_ex99": is_ex99,
                             "filers": " ".join(src.get("display_names") or []).lower()}
    return sorted(by_adsh.values(), key=lambda c: c["file_date"])


def _filed_by_a_party(cand: dict, acq_tok: str | None, tgt_tok: str | None) -> bool:
    """Is this EFTS candidate filed by the acquirer or the target? The quoted
    bank-name query also returns every unrelated registrant naming the bank
    as a LENDER in a credit agreement (live 2026-10-06: "Bremer Bank" ->
    Core Scientific x7, "Synovus Bank" -> Tupperware/AdaptHealth/..., "Comerica
    Bank" -> Credit Acceptance), which spent the candidate budget and, since
    tokens like "old" and "fifth" occur in any document, even passed the text
    gates — Old National/Bremer, Pinnacle/Synovus and Fifth Third/Comerica
    all anchored on a third party's filing. With no usable token on either
    side the gate stays open (never drop a deal on an all-generic name)."""
    toks = [t for t in (acq_tok, tgt_tok) if t]
    if not toks or not cand.get("filers"):
        return True
    return any(token_in(t, cand["filers"]) for t in toks)


# The target must be named in DEAL context, not as a peer in a deck or a
# lender in a covenant: within a sentence of acquire / merge / agreement /
# combination wording.
_DEAL_CTX = r"(?:acqui\w+|merg\w+|combin\w+|agreement)"


# Completed-tense, split by strength. The explicit forms always mark a
# completion filing; the generic "has/successfully completed" form only
# counts when the TARGET is named in that sentence — an investor deck's
# "management has successfully completed and integrated 9 bank M&A
# transactions" (Old National/Bremer, live) is history, not this deal.
_COMPLETED_STRONG_RE = re.compile(
    r"\bannounce[ds]?\s+the\s+completion\b|"
    r"\bcompleted\s+(?:its|the)\s+(?:previously\s+announced|acquisition|"
    r"merger|purchase|combination)", re.IGNORECASE)
_COMPLETED_WEAK_RE = re.compile(
    r"\b(?:has|have|had|today|successfully)\s+completed\b", re.IGNORECASE)


def _completed_for(text: str, tgt_tok: str | None) -> bool:
    """Does ``text`` announce THIS deal's completion? (see the split above;
    with no usable target token the generic form counts everywhere)."""
    if _COMPLETED_STRONG_RE.search(text):
        return True
    for m in _COMPLETED_WEAK_RE.finditer(text):
        if not tgt_tok:
            return True
        # A tight window (flattened deck slides carry no sentence breaks, so
        # a deck page about the target would otherwise put its name "in the
        # sentence" of the boilerplate). Real completion releases hit the
        # STRONG forms above regardless of this window.
        win = text[max(0, m.start() - 120):m.end() + 120]
        if token_in(tgt_tok, win.lower()):
            return True
    return False


def _named_in_deal_context(text: str, target_query: str,
                           tgt_tok: str | None = None) -> bool:
    tq = re.escape(target_query)
    if tgt_tok and target_query.lower() not in text.lower():
        # The release names the holdco ("The Farmers Bancorp"), the FDIC row
        # the charter ("Farmers Bank, Frankfort, Indiana"): the brand token
        # is the common ground.
        tq = r"\b" + re.escape(tgt_tok) + r"\b"
    # A 250-char window that does not cross a sentence boundary (". " + a
    # capital), so a peer list in one sentence cannot borrow the "definitive
    # agreement" of the next — while "Inc." mid-name does not end the window
    # unless a new sentence actually starts.
    win = r"(?:(?!\.\s+[A-Z]).){0,250}?"
    return re.search(rf"{_DEAL_CTX}{win}{tq}|{tq}{win}{_DEAL_CTX}",
                     text, re.IGNORECASE) is not None


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
    # is_fresh judges the 90d design TTL — the default 24h read ceiling would
    # void the negative cache and re-fetch known-dead docs nightly.
    if is_fresh(cache.get(key, max_age_s=None), _DOC_404_TTL_S):
        return None, True           # known-permanent 404 — no network, no log
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{adsh.replace('-', '')}/{doc}")
    try:
        resp = _get_429_aware(url)
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
                         completion_date: str,
                         acquirer_ticker: str | None = None) -> tuple[dict | None, bool]:
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
    tgt_tok = brand_token(target_name)
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
    regates = 0
    party = [c for c in _candidates(hits) if _filed_by_a_party(c, acq_tok, tgt_tok)]
    for cand in party[:_MAX_CANDIDATES]:
        time.sleep(_PAUSE_S)
        text, t_ok = _fetch_doc_text(cand["cik"], cand["adsh"], cand["doc"])
        if text is None:
            fetch_failed = fetch_failed or not t_ok
            continue
        if _completed_for(text, tgt_tok):     # completion PR — not the announce
            continue

        def _gates(txt: str) -> bool:
            low = txt.lower()
            return (tq.lower() in low
                    and (not acq_tok or token_in(acq_tok, low))
                    and not _completed_for(txt, tgt_tok)
                    and _ANNOUNCE_RE.search(txt) is not None
                    and _named_in_deal_context(txt, tq, tgt_tok))

        full = None
        if not _gates(text):
            # Only a document that at least NAMES the target earns the
            # whole-accession read (index + up to three documents): the
            # 2026-10-06 walk re-read every party filing and burst EDGAR's
            # rate limit. Capped per deal as well.
            low = text.lower()
            names_target = (tq.lower() in low
                            or bool(tgt_tok and token_in(tgt_tok, low)))
            if not names_target or regates >= _MAX_REGATES:
                continue
            regates += 1
            # The single EFTS-matched document is often the wrong one to
            # judge: the press release says "Bremer Financial" while the
            # exact charter phrase "Bremer Bank" sits in the 8-K body (Old
            # National anchored four months late on an approval filing), or
            # the match is the investor DECK with no agreement wording
            # (Seacoast/Villages anchored on a later release). Re-gate every
            # text test on the whole accession before skipping a candidate.
            full, f_ok = _accession_text(cand["cik"], cand["adsh"], cand["doc"])
            fetch_failed = fetch_failed or not f_ok
            if not full or not _gates(full):
                continue
            text = full
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
        # Terms come off the WHOLE accession (8-K body + press release +
        # investor deck): the merger-agreement summary in the 8-K body carries
        # the termination fee and the deck the stated per-share value, neither
        # of which the single gating document has (live: OSBC $8.5M fee,
        # PB/Stellar "$39.08 per common share" both live in sibling documents).
        f_ok = True
        if full is None:
            full, f_ok = _accession_text(cand["cik"], cand["adsh"], cand["doc"])
        terms, t_ok = build_terms(full or text, cand["file_date"],
                                  acq_tick=acquirer_ticker)
        result["terms"] = terms
        return result, ok and t_ok and f_ok
    # Nothing classified as the announcement. Only claim a cacheable n/a if
    # every candidate was actually readable.
    return None, not fetch_failed


# ── Wire-feed announcement (acquirers EDGAR cannot reach) ─────────────────
#
# TowneBank (FDIC-registered, no SEC filings), Merchants & Marine, Ballston
# Spa and other OTC acquirers announce deals only on the wire and their own
# site; Richmond Mutual and Esquire ARE filers but EDGAR's quoted-name search
# could not reach their announcements. Owner 2026-10-06: "no SEC filings is
# not an excuse to not have data." FMP's press-release index is the
# TRANSPORT only (the content is the bank's own release, same provenance
# decision as data/otc_release); the subject guard (data/events/fmp_news
# ._is_subject) rejects the polluted-symbol stories; the full story is
# fetched from the wire URL and run through the SAME gates and extractors
# as the EDGAR path.

_WIRE_LIMIT = 250


def _wire_releases(ticker: str) -> list[dict] | None:
    """The acquirer's press-release index, newest first; None when the
    feed is unavailable (no key / transport error — caller must not cache)."""
    from data.fmp_client import _has_key, get_press_releases
    if not ticker or not _has_key():
        return None
    try:
        return get_press_releases(ticker, limit=_WIRE_LIMIT) or []
    except Exception as e:
        print(f"[ma_announce] wire {ticker}: {type(e).__name__}: {e}")
        return None


def _wire_releases_since(ticker: str, since: str) -> list[dict] | None:
    """The acquirer's press releases from ``since`` (YYYY-MM-DD) to today —
    the deal-resolution read (a completion release can be months old; the
    newest-N index missed U.S. Bancorp's 2026-06-01 BTIG completion)."""
    from datetime import date as _date
    from data.fmp_client import _has_key, get_press_releases
    if not ticker or not _has_key():
        return None
    try:
        return get_press_releases(ticker, limit=_WIRE_LIMIT, since=since,
                                  until=_date.today().isoformat()) or []
    except Exception as e:
        print(f"[ma_announce] wire-since {ticker}: {type(e).__name__}: {e}")
        return None


def _wire_story_text(url: str) -> str | None:
    from data.otc_release import _fetch_story
    html = _fetch_story(url)
    return _strip_html(html) if html else None


def resolve_announcement_wire(acquirer_ticker: str, target_name: str,
                              acquirer_name: str,
                              completion_date: str) -> tuple[dict | None, bool]:
    """The deal's announcement from the ACQUIRER's own wire releases, same
    result shape as resolve_announcement (plus source='wire'). Strict:
    the release must name the target in deal context, name the acquirer,
    carry announcement wording and no completed tense. (result, ok): ok=False
    when the feed or a story fetch failed."""
    if not acquirer_ticker or not target_name or not completion_date:
        return None, True
    prs = _wire_releases(acquirer_ticker)
    if prs is None:
        return None, False
    try:
        comp = date.fromisoformat(completion_date)
    except ValueError:
        return None, True
    floor = (comp - timedelta(days=_WINDOW_DAYS)).isoformat()
    tq, tgt_tok, acq_tok = (query_name(target_name), brand_token(target_name),
                            brand_token(acquirer_name))
    from data.events.fmp_news import _is_subject

    def _names_target(low: str) -> bool:
        return tq.lower() in low or bool(tgt_tok and token_in(tgt_tok, low))

    cands = []
    for p in prs:
        d = (p.get("published_at") or "")[:10]
        if not (floor <= d <= completion_date):
            continue
        blob = f"{p.get('title') or ''} {p.get('text') or ''}"
        if not _names_target(blob.lower()) or not _is_subject(acquirer_ticker, blob):
            continue
        cands.append((d, p))
    fetch_failed = False
    for d, p in sorted(cands, key=lambda x: x[0]):
        time.sleep(_PAUSE_S)
        text = _wire_story_text(p.get("url") or "")
        if not text:
            fetch_failed = True
            continue
        low = text.lower()
        if not _names_target(low) or (acq_tok and not token_in(acq_tok, low)):
            continue
        if _completed_for(text, tgt_tok) or not _ANNOUNCE_RE.search(text):
            continue
        if not _named_in_deal_context(text, tq, tgt_tok):
            continue
        terms, t_ok = build_terms(text, d, acq_tick=acquirer_ticker)
        value = extract_stated_value(text)
        return {
            "announce_date": d,
            "value_usd": value,
            "value_basis": "stated" if value else None,
            "value_note": None,
            "target_cik": None,
            "url": p.get("url"),
            "accession": None,
            "terms": terms,
            "source": "wire",
        }, (not fetch_failed) and t_ok
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
_ANN_SCAN_CAP = 8               # announcement-candidate documents read per
                                # filer (newest first); the oldest per
                                # counterparty is the announcement
_TERM_TEXT_RE = re.compile(r"\bterminat(?:e|ed|ion|ing)\b", re.IGNORECASE)
_EX99_NAME_RE = re.compile(r"ex[-_.]?99|press", re.IGNORECASE)
_EXHIBIT_NAME_RE = re.compile(r"ex(?:hibit)?[-_.]?\d", re.IGNORECASE)


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
    r"((?:\d{1,2}(?:st|nd|rd|th)\s+)?[A-Z][\w.,&'\- ]{2,60}?)"
    r"(?:\s*\(|\s+in\s+an?\s|,\s+the\s|\.\s|\s+and\s|"
    r"\s+to\s+(?:expand|create|form|enter|strengthen|bolster|grow|become|"
    r"build|extend|add|accelerate)\b)", re.IGNORECASE)
# An ordinal-led bank name ("1st Colonial Bancorp", "1st Source") is a
# name; any other digit in a capture is a dateline / amount run-on.
_ORDINAL_NAME_RE = re.compile(r"^\d{1,2}(?:st|nd|rd|th)\s+[A-Z]")
# Past-tense closing wording near a capture = a prior deal ("our recently
# closed William Penn transaction"; "closed in April 2025"). "expected to be
# completed in the fourth quarter" is THIS deal (Isabella, pass 4).
# A year bound to deal wording ("NXT Bank acquisition 2021", "2019
# merger") — never a balance-sheet date ("as of December 31, 2025" beside
# "acquire Grand River", Isabella's June release).
_HIST_DEAL_YEAR_RE = re.compile(
    r"\b(?:acquisition|acquired|merger|transaction)\s+(?:in\s+|of\s+)?(20[0-4]\d)\b|"
    r"\b(20[0-4]\d)\s+(?:acquisition|merger)\b", re.IGNORECASE)
_PAST_DEAL_RE = re.compile(
    r"\b(?:recently|previously)\s+(?:closed|completed)\b|\bwas\s+(?:closed|completed)\b|"
    r"(?<!to\sbe\s)(?<!will\sbe\s)\b(?:closed|completed)\s+(?:on|in)\s+"
    r"(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December|\d{4})\b", re.IGNORECASE)


def _digits_in_name(name: str) -> bool:
    body = _ORDINAL_NAME_RE.sub("", name or "", count=1) if _ORDINAL_NAME_RE.match(name or "") else (name or "")
    return bool(re.search(r"\d", body))

# The agreement sentence names THIS filing's counterparty — an acquirer with
# two live deals cites both targets ("TC Bancshares" and "First Reliance" in
# Colony's 2026-06-24 8-K, live), and the single-candidate rule then yielded
# nothing. "entered into an Agreement and Plan of Merger (the "Merger
# Agreement") with First Reliance Bancshares, Inc." settles it.
_MERGER_WITH_RE = re.compile(
    r"Agreement\s+and\s+Plan\s+of\s+(?:Merger|Reorganization)[^.]{0,160}?\bwith\s+"
    r"((?:\d{1,2}(?:st|nd|rd|th)\s+)?[A-Z][\w.,&'\- ]{2,60}?)(?:\s*\(|,\s+(?:a|an|the)\s|\.\s|\s+and\s|\s+pursuant|"
    r"\s+under)")

# A captured company phrase is often a run-on across an "About X. X" PR
# footer (live: HOPE's TBNK pair captured "About Territorial Bancorp Inc.
# Territorial Bancorp Inc."). The real name is the LAST sentence piece —
# split on a lowercase-terminated sentence boundary (the [a-z] lookbehind
# keeps "U.S. Bancorp" intact) and keep the final multi-word piece.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[a-z])\.\s+(?=[A-Z])")


_STATE_TAIL_RE = re.compile(
    r",?\s+an?\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?\s+(?:corporation|company|"
    r"bank(?:ing\s+corporation)?|association|bancorp|limited\s+liability\s+company)\b.*$")
_DEFINED_TERM_HEAD = r"([A-Z][\w.,&'\- ]{3,60}?)\s*\(\s*(?:the\s+)?[“\"]"
_DEFINED_TERM_TAIL = (r"[”\"](?:,\s*[“\"][^”\"]{1,30}[”\"])*\s*"
                      r"(?:or\s+[“\"][^”\"]{1,30}[”\"]\s*)?\)")


def _clean_company_name(phrase: str) -> str:
    """Normalize a captured company phrase to a single clean name: take the
    last sentence piece of an 'About X. X' footer run-on (only when that
    piece is itself multi-word), strip a leading 'About '/'the ' and a
    state-of-incorporation tail ("First Savings Financial Group, Inc., an
    Indiana corporation", First Merchants 2026-02-02)."""
    p = (phrase or "").strip(" .,")
    pieces = _SENTENCE_SPLIT_RE.split(p)
    if len(pieces) > 1 and len(pieces[-1].split()) >= 2:
        p = pieces[-1]
    p = re.sub(r"^\s*(?:about|the)\s+", "", p.strip(), flags=re.IGNORECASE)
    p = _STATE_TAIL_RE.sub("", p)
    # A joint-announcement list ("Isabella Bank, and Grand River Commerce,
    # Inc." — Isabella's 2026-06-12 release) is the LAST party; "Bank of
    # Commerce and Trust Company" (no comma) is one name.
    p = re.split(r",\s+and\s+", p)[-1]
    return p.strip(" .,")


def expand_defined_term(short: str, text: str) -> str:
    """A short defined term captured as a counterparty ("HCB", "BOH") is the
    full name the text defines it with ('HCB Financial Corp. ("HCB")'),
    else the capture unchanged. One or two words, no digits."""
    s = (short or "").strip()
    if not s or len(s.split()) > 2 or re.search(r"\d", s) or len(s) > 20:
        return short
    rx = re.compile(_DEFINED_TERM_HEAD + re.escape(s) + _DEFINED_TERM_TAIL)
    names = {_clean_company_name(_trailing_name(m.group(1).strip()))
             for m in rx.finditer(text)}
    names = {n for n in names if n and n.lower() != s.lower() and len(n) > len(s)}
    return next(iter(names)) if len(names) == 1 else short


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
    # An all-generic name ("First Financial Bancorp") has NO brand token:
    # self is then its own ticker(s) and its normalized display name (the
    # dateline-prefixed "Cincinnati, Ohio - July 21, 2026. First Financial
    # Bancorp. (NASDAQ: FFBC)" pair was not self, and FFBC's Finward deal
    # died on the dateline rejection, live 2026-10-06).
    self_tickers: set[str] = set()
    self_names: set[str] = set()

    def _norm(s: str) -> str:
        return " ".join(re.findall(r"[a-z0-9&]+", (s or "").lower()))

    for h in hits:
        for dn in (h.get("_source", {}).get("display_names") or []):
            m = _DISPLAY_CIK_RE.match((dn or "").strip())
            if m and int(m.group(2)) == int(cik):
                t = brand_token(m.group(1))
                if t:
                    self_toks.add(t)
                self_names.add(_norm(re.sub(r"/[A-Z]{2}/", " ", m.group(1))))
            m2 = _DISPLAY_NAME_RE.search(dn or "")
            if m2 and int(m2.group(2)) == int(cik):
                self_tickers.add(m2.group(1))
    if subject_name:
        self_names.add(_norm(subject_name))

    def _is_self(name: str) -> bool:
        low = (name or "").lower()
        if any(token_in(t, low) for t in self_toks):
            return True
        n = _norm(_clean_company_name(name))
        return bool(n) and any(n == s or s.startswith(n + " ") for s in self_names)

    fetch_failed = False
    # Newest first within a bounded scan, then the OLDEST 8-K per
    # counterparty wins (the announcement; approval / vote update 8-Ks
    # carry the merger phrase and name the same counterparty — Isabella
    # re-anchored to its 2026-10-06 approval 8-K). An oldest-first cap cut
    # Peoples' 2026-09-30 Capital announcement off behind two earnings
    # 8-Ks, an earlier deal and an approvals 8-K (pass 4, 2026-10-07).
    by_tok: dict[str, dict] = {}
    for ann in sorted(recent, key=lambda g: g["file_date"], reverse=True)[:_ANN_SCAN_CAP]:
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
        cp_ticker = None
        pairs = [(_clean_company_name(n), t) for n, t in _pr_ticker_pairs(text)
                 if t not in self_tickers]
        pairs = [(n, t) for n, t in pairs if n and not _is_self(n)]
        # The counterparty is the non-self pair the text names MOST (at
        # least twice): a peer table in an investor exhibit lists other
        # banks' pairs once (Farmers' 2026-01-13 8-K put Hingham, a peer,
        # ahead of Middlefield, the actual target — live on the board).
        low_text = text.lower()
        ranked = []
        for n, t in pairs:
            tok = brand_token(n)
            cnt = len(re.findall(r"\b" + re.escape(tok) + r"\b", low_text)) if tok else 0
            if cnt >= 2:
                ranked.append((cnt, n, t))
        if ranked:
            ranked.sort(key=lambda x: -x[0])
            _cnt, counterparty, cp_ticker = ranked[0]
        else:
            best = {}
            for m in _ACQUIRE_OBJ_RE.finditer(text):
                cand = expand_defined_term(_clean_company_name(m.group(1)), text)
                t = brand_token(cand)
                if not t or _is_self(cand):
                    continue
                # "our recently closed William Penn transaction" in Mid
                # Penn's 1st Colonial deck (2025-09-24) is a PAST deal.
                around = text[max(0, m.start() - 120):m.end() + 120]
                if _PAST_DEAL_RE.search(around):
                    continue
                # A year before the filing year next to the capture is a
                # historical deal ("Entry into Iowa with NXT Bank
                # acquisition 2021" in HBT's 2025-10-20 deck; the deal was
                # CNB Bank Shares, pass 4).
                filing_year = int(ann["file_date"][:4])
                if any(int(y) < filing_year
                       for pair in _HIST_DEAL_YEAR_RE.findall(around)
                       for y in pair if y):
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
            elif len(best) > 1:
                with_toks = {brand_token(_clean_company_name(m.group(1)))
                             for m in _MERGER_WITH_RE.finditer(text)}
                pick = [t for t in best if t in with_toks]
                if len(pick) == 1:
                    counterparty = best[pick[0]]
        if not counterparty or _is_self(counterparty):
            continue
        # A capture that is not a company name — a dateline run-on
        # ("Cincinnati, Ohio - July 21, 2026. First Financial Bancorp", live
        # on the first universe board as a self-deal) — is unreadable: no
        # row, never a guess.
        if _digits_in_name(counterparty) or len(counterparty.split()) > 8:
            continue
        ct = brand_token(counterparty)
        if not ct:
            continue
        if ct in by_tok and by_tok[ct]["announce_date"] <= ann["file_date"]:
            continue              # an older 8-K already carries this deal
        # Self as the acquire object -> we are the target (seller side).
        for m in _ACQUIRE_OBJ_RE.finditer(text):
            if _is_self(_clean_company_name(m.group(1))):
                direction = "sale"
                break
        # The ratio sentence's per-share side is the target: a seller's own
        # 8-K carrying the joint release lists the buyer's ticker pair first
        # (Tri-County's 8-K, 2026-08-10) and would otherwise read as an
        # acquisition.
        ratio_hit = extract_exchange_ratio(text)
        if ratio_hit:
            _r, acq_side, tgt_side = ratio_hit
            if _is_self(tgt_side) and not _is_self(acq_side):
                direction = "sale"
            elif _is_self(acq_side) and not _is_self(tgt_side):
                direction = "acquisition"
        value = extract_stated_value(text)
        basis = "stated" if value else None
        note = None
        if value is None:
            comp, c_ok = compute_stock_value(text, ann["file_date"], {}, [])
            fetch_failed = fetch_failed or not c_ok
            if comp:
                value, basis = comp["value_usd"], "computed"
                note = comp["value_note"]
        terms, t_ok = build_terms(text, ann["file_date"])
        fetch_failed = fetch_failed or not t_ok
        by_tok[ct] = {
            "announce_date": ann["file_date"],
            "direction": direction,
            "counterparty_name": counterparty,
            "counterparty_ticker": cp_ticker,
            "terms": terms,
            "counterparty_cik": None,
            "value_usd": value, "value_basis": basis, "value_note": note,
            "target_cik": None,
            "announce_url": (f"https://www.sec.gov/Archives/edgar/data/"
                             f"{int(ann['cik'])}/{ann['adsh'].replace('-', '')}/"
                             f"{ann['doc']}"),
            "accession": ann["adsh"],
        }
    rows = sorted(by_tok.values(), key=lambda r: r["announce_date"], reverse=True)
    return rows, not fetch_failed


def _accession_text(cik, adsh: str, primary_doc: str) -> tuple[str | None, bool]:
    """Primary document text PLUS the accession's EX-99 press-release
    exhibits, fetched via the filing index — the PR usually lacks the exact
    EFTS query phrase, so it is not among the phrase-matched documents, yet
    it is where the "(NYSE: XXX)" party pairs and deal values live
    (live-verified on FHN/TD). When ``primary_doc`` is itself an exhibit
    (EFTS matched the investor deck), the listing's first non-exhibit
    document — the 8-K body, whose Item 1.01 summary carries the exchange
    ratio and termination fee — is read too (live: Seacoast/Villages
    2025-05-29, Old Second's $8.5M fee). Returns (text, ok); ok=False on any
    TRANSIENT fetch failure so a partial read is never cached as a miss — an
    HTTP 404 (document permanently absent from the immutable archive) keeps
    ok=True."""
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
    names = list(dict.fromkeys(names))
    wanted = [n for n in names if _EX99_NAME_RE.search(n) and n != primary_doc][:2]
    # The directory listing also carries EDGAR's site-nav links (index.htm,
    # search.htm, R1.htm ...): the filing's own documents share the primary
    # document's filename stem (tmb-20250224x8k.htm / tmb-20250224xex99d1.htm,
    # d103745d8k.htm / d103745dex991.htm — live-verified), so the body is the
    # first non-exhibit name with a ≥5-char common prefix.
    def _stem_match(n: str) -> bool:
        p = primary_doc or ""
        k = 0
        while k < min(len(n), len(p)) and n[k] == p[k]:
            k += 1
        return k >= 5
    body = next((n for n in names
                 if n != primary_doc and not n.endswith("-index.htm")
                 and not _EXHIBIT_NAME_RE.search(n) and _stem_match(n)), None)
    if body and _EXHIBIT_NAME_RE.search(primary_doc or ""):
        wanted.insert(0, body)
    for n in wanted:
        time.sleep(_PAUSE_S)
        t, t_ok = _fetch_doc_text(cik, adsh, n)
        if t is None:
            ok = ok and t_ok
            continue
        extra.append(t)
    return " ".join([base] + extra), ok


def find_terminated_deals(subject_cik, subject_name: str) -> tuple[list[dict], bool]:
    """
    Terminated M&A deals for a holdco CIK, newest-first.

    Returns ([{termination_date, announce_date, counterparty_name,
               value_usd, value_basis, value_note, direction | None,
               terms (build_terms), announce_url, termination_url}], ok) — ok=False on any fetch
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
        terms, t_ok = build_terms(ann_text, ann["file_date"])
        fetch_failed = fetch_failed or not t_ok
        deals.append({
            "termination_date": term["file_date"],
            "announce_date": ann["file_date"],
            "counterparty_name": others[0][0],
            "terms": terms,
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
