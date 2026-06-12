"""
N-PORT (mutual-fund holdings) client — SNL-BUILD-PLAN §13, Ownership tabs.

SEC Form NPORT-P is filed per fund SERIES (monthly portfolio, made public
for the third month of each fiscal quarter, ~60 days after quarter end).
Finding the funds that hold a given STOCK is therefore a reverse lookup,
exactly like 13F: run EDGAR full-text search over recent NPORT-P filings,
fetch each hit's primary XML, extract the <invstOrSec> entries matching
the target issuer.

EDGAR full-text search endpoint (probed live 2026-06-12):

    GET https://efts.sec.gov/LATEST/search-index
        ?q=<query>&forms=NPORT-P&dateRange=custom&startdt=...&enddt=...

  - response shape: {"hits": {"total": ..., "hits": [{"_id":
    "<accession>:<primary file>", "_source": {"ciks": [...],
    "display_names": [...], "file_date": "YYYY-MM-DD"}}]}} — the same
    shape form13f_client consumes; max 100 hits per page.
  - phrase queries must be double-quoted ('"EAST WEST BANCORP"').
  - CUSIP queries are sent UNQUOTED — quoted CUSIPs have returned HTTP 500
    in live probes; unquoted works (814 hits / 130 days, 2026-06-12).
  - the endpoint also throws TRANSIENT 500s on any query (observed live:
    identical query 500 then 200 seconds apart); _search_nport_filings
    retries 5xx a few times, then returns [] so the caller's next query
    attempt can proceed.
  - ticker symbols are unreliable query terms: in NPORT XML the ticker is
    an attribute value (<ticker value="EWBC"/>), which full-text search
    mostly does not index ('"EWBC"' → 2 hits vs 3,617 for the name).

Matching precision (per-entry, precedence order):
  1. CUSIP equality — exact, when the caller supplies one (most precise).
     When both sides have a CUSIP it decides outright (mismatch = no match).
  2. <identifiers><ticker value=.../> equality — exact, when present.
     Also decides outright: an entry whose ticker identifier mismatches
     must NOT fall through to name matching (First Bancorp NC vs PR).
  3. Normalized issuer-name equality — full-string match after stripping
     punctuation and trailing legal suffixes; NO substring matching.
     Honest caveat: distinct issuers can share a normalized name (e.g.
     First Bancorp NC vs First Bancorp PR). Most NPORT entries carry no
     ticker identifier, so for ambiguous names pass the CUSIP.

Values: NPORT <valUSD> is raw dollars (no 13F thousands-vs-dollars scale
ambiguity); <balance> with <units>NS</units> is number of shares. Entries
whose units are not NS (bonds, notional contracts) are skipped.

Cached 7 days per ticker under nport_cache/{TICKER}.json via cloud_storage
+ data/freshness — the house pattern for client-specific TTLs (form4,
form13f, fred, estimates). data/cache.py is NOT used because it enforces
the global 24h fundamentals TTL and cannot hold a 7-day entry.
"""

import re
import time
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

import requests

from data.cloud_storage import save_json, load_json
from data.http import get_with_retry
from config import SEC_USER_AGENT

NPORT_CACHE_PREFIX = "nport_cache"
CACHE_TTL_SECONDS = 7 * 86400
FETCH_PACE_SECONDS = 0.12   # respectful EDGAR pacing between document fetches
SEARCH_WINDOW_DAYS = 130    # NPORT-P public ~60d after fiscal quarter end

HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}
EDGAR_FTS = "https://efts.sec.gov/LATEST/search-index"


def _is_fresh(cached: dict | None) -> bool:
    from data.freshness import is_fresh
    return is_fresh(cached, CACHE_TTL_SECONDS)


# ── EDGAR discovery ───────────────────────────────────────────────────────

def _search_nport_filings(query: str, limit: int) -> list[dict]:
    """
    EDGAR full-text search for recent NPORT-P filings matching ``query``
    (already quoted/unquoted by the caller — see module docstring).
    Returns [{cik, accession, primary_doc, registrant, date_filed}].
    """
    since = (datetime.now() - timedelta(days=SEARCH_WINDOW_DAYS)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    params = {
        "q": query,
        "forms": "NPORT-P",
        "dateRange": "custom",
        "startdt": since,
        "enddt": today,
    }
    # EDGAR FTS throws transient 500s (identical query 500 then 200 seconds
    # apart, observed live 2026-06-12). get_with_retry only retries 429, so
    # 5xx is handled here; after the retries give up, return [] so the
    # caller's next query attempt still runs. Non-5xx errors propagate.
    resp = None
    for attempt in range(3):
        try:
            resp = get_with_retry(EDGAR_FTS, params=params, headers=HEADERS,
                                  timeout=20)
            break
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", 0) or 0
            if status < 500:
                raise
            time.sleep(1.0 + attempt)
    if resp is None:
        return []
    hits = resp.json().get("hits", {}).get("hits", [])

    results = []
    for hit in hits[:limit]:
        _id = hit.get("_id", "")
        if ":" not in _id:
            continue
        accession, primary_doc = _id.split(":", 1)
        src = hit.get("_source", {})
        ciks = src.get("ciks", [])
        names = src.get("display_names", [])
        registrant = re.sub(r"\s*\(CIK \d+\)\s*$", "", names[0]) if names else "—"
        if ciks:
            results.append({
                "cik": ciks[0],
                "accession": accession,
                "primary_doc": primary_doc,
                "registrant": registrant.strip(),
                "date_filed": src.get("file_date"),
            })
    return results


def filing_index_url(cik: str | int, accession: str) -> str:
    """Human-readable EDGAR filing-index URL for an NPORT-P accession."""
    acc_no_hyphens = str(accession).replace("-", "")
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{acc_no_hyphens}/{accession}-index.htm")


def _fetch_nport_xml(cik: str, accession: str, primary_doc: str) -> ET.Element | None:
    """Fetch and parse one NPORT-P primary XML. None on any failure."""
    acc_no_hyphens = accession.replace("-", "")
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{acc_no_hyphens}/{primary_doc}")
    try:
        resp = get_with_retry(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=30)
        if resp is None:
            return None
        return ET.fromstring(resp.text)
    except Exception:
        return None


# ── Entry matching ────────────────────────────────────────────────────────

# Trailing legal-form tokens dropped before whole-name comparison.
_NAME_SUFFIXES = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO",
                  "COMPANY", "LTD", "PLC", "SA", "NV", "AG"}

# CUSIP placeholders some funds file instead of omitting the element.
_CUSIP_PLACEHOLDERS = {"", "N/A", "NA", "NONE", "000000000", "0"}


def _normalize_name(name: str) -> str:
    """Uppercase, strip punctuation, drop trailing legal suffixes."""
    s = re.sub(r"[^A-Z0-9 ]+", " ", (name or "").upper())
    tokens = s.split()
    while tokens and tokens[-1] in _NAME_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _entry_matches(entry: dict, ticker: str, cusip: str | None,
                   company_name: str) -> str | None:
    """
    Decide whether one parsed <invstOrSec> entry is the target issuer.
    Returns the match basis ("cusip" | "ticker" | "name") or None.
    Precedence and precision notes are in the module docstring.
    """
    e_cusip = (entry.get("cusip") or "").strip().upper()
    have_real_cusip = e_cusip not in _CUSIP_PLACEHOLDERS
    if cusip:
        if have_real_cusip:
            # Caller gave a CUSIP and the entry has one: it decides outright.
            return "cusip" if e_cusip == cusip.strip().upper() else None
        # Entry has no usable CUSIP — fall through to ticker/name.

    e_ticker = (entry.get("ticker") or "").strip().upper()
    if e_ticker and ticker:
        # Entry carries an explicit ticker identifier: like CUSIP, it
        # decides outright. A mismatch must not fall through to name
        # matching — distinct issuers can share a normalized name
        # (First Bancorp NC vs First Bancorp PR).
        return "ticker" if e_ticker == ticker.strip().upper() else None

    if company_name:
        target = _normalize_name(company_name)
        if target and _normalize_name(entry.get("name") or "") == target:
            return "name"
    return None


def _parse_filing_entries(root: ET.Element, ticker: str, cusip: str | None,
                          company_name: str) -> dict | None:
    """
    Extract the target issuer's common-stock position from one parsed
    NPORT-P XML. Returns {fund_name, series_id, report_date, shares,
    value_usd, pct_of_fund, matched_by} or None when the fund doesn't
    hold the target. Multiple matching lots are summed.
    """
    # genInfo: series identity + the report period the holdings cover.
    fund_name = series_id = report_date = None
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "seriesName":
            fund_name = (el.text or "").strip() or fund_name
        elif tag == "seriesId":
            series_id = (el.text or "").strip() or series_id
        elif tag == "repPdDate":
            report_date = (el.text or "").strip() or report_date

    shares = 0.0
    value_usd = 0.0
    pct_of_fund = 0.0
    matched_by = None
    for sec in root.iter():
        if not sec.tag.endswith("invstOrSec"):
            continue
        entry: dict = {}
        for child in sec:
            tag = child.tag.split("}")[-1]
            if tag in ("name", "title", "cusip", "balance", "units",
                       "valUSD", "pctVal", "assetCat"):
                entry[tag] = (child.text or "").strip()
            elif tag == "identifiers":
                for ident in child:
                    if ident.tag.split("}")[-1] == "ticker":
                        entry["ticker"] = ident.get("value", "")

        # Common shares only: balance must be a share count (units NS) and,
        # when the asset category is stated, equity ("EC"). Bonds/derivatives
        # of the same issuer must not be aggregated as ownership.
        if entry.get("units") != "NS":
            continue
        if entry.get("assetCat") and entry["assetCat"] != "EC":
            continue

        basis = _entry_matches(entry, ticker, cusip, company_name)
        if not basis:
            continue
        try:
            shares += float(entry.get("balance") or 0)
            value_usd += float(entry.get("valUSD") or 0)
            pct_of_fund += float(entry.get("pctVal") or 0)
        except (TypeError, ValueError):
            continue
        matched_by = matched_by or basis

    if not matched_by or shares <= 0:
        return None
    return {
        "fund_name": fund_name or "—",
        "series_id": series_id,
        "report_date": report_date,
        "shares": shares,
        "value_usd": value_usd,
        "pct_of_fund": pct_of_fund,
        "matched_by": matched_by,
    }


# ── Public API ────────────────────────────────────────────────────────────

def get_fund_holders(ticker: str, cusip: str | None = None,
                     max_filings: int = 40,
                     company_name: str = "") -> list[dict]:
    """
    Mutual funds (NPORT-P filers) holding ``ticker``, newest report per
    fund series, sorted by position value desc. Cached 7 days per ticker.

    Each holder: {fund_name, series_id, registrant, registrant_cik,
    shares, value_usd, pct_of_fund, report_date, date_filed, accession,
    filing_url, matched_by}.

    ``cusip`` makes matching exact (recommended for ambiguous issuer
    names). Without it, matching is ticker-identifier or exact normalized
    issuer-name equality — see module docstring for the precision caveat.
    ``max_filings`` caps EDGAR document fetches (search returns at most
    100 hits per page, so coverage beyond ~100 funds needs pagination —
    deliberately not built yet).

    Failures return [] after one [nport] log line — never a partial guess.
    """
    if not ticker:
        return []
    t = ticker.upper()

    cached = load_json(NPORT_CACHE_PREFIX, f"{t}.json")
    if _is_fresh(cached) and "holders" in cached:
        return cached["holders"]

    try:
        if not company_name and not cusip:
            # Name is the workhorse FTS query term (see module docstring).
            from data.bank_mapping import search_sec_by_ticker
            _, resolved = search_sec_by_ticker(t)
            company_name = resolved or ""

        # Query attempts, most precise first; quoting rules per live probe.
        attempts = []
        if cusip:
            attempts.append(cusip.strip())            # unquoted — quoted CUSIP → HTTP 500
        if company_name:
            attempts.append(f'"{company_name.strip()}"')
        attempts.append(f'"{t}"')                     # last resort, unreliable

        candidates = []
        for q in attempts:
            candidates = _search_nport_filings(q, limit=max_filings)
            if candidates:
                break

        holders: dict[tuple, dict] = {}   # one row per fund series, latest report wins
        for c in candidates[:max_filings]:
            if FETCH_PACE_SECONDS > 0:
                time.sleep(FETCH_PACE_SECONDS)
            root = _fetch_nport_xml(c["cik"], c["accession"], c["primary_doc"])
            if root is None:
                continue
            pos = _parse_filing_entries(root, t, cusip, company_name)
            if pos is None:
                continue
            pos.update({
                "registrant": c["registrant"],
                "registrant_cik": c["cik"],
                "date_filed": c["date_filed"],
                "accession": c["accession"],
                "filing_url": filing_index_url(c["cik"], c["accession"]),
            })
            key = (c["cik"], pos["series_id"] or pos["fund_name"])
            prev = holders.get(key)
            if prev is None or (pos["report_date"] or "") > (prev["report_date"] or ""):
                holders[key] = pos

        out = sorted(holders.values(),
                     key=lambda h: h.get("value_usd") or 0, reverse=True)
    except Exception as e:
        print(f"[nport] fund-holder fetch failed for {t}: {type(e).__name__}: {e}")
        return []

    try:
        save_json(NPORT_CACHE_PREFIX, f"{t}.json", {
            "ticker": t,
            "cached_at": datetime.now().isoformat(),
            "holders": out,
        })
    except Exception:
        pass

    return out
