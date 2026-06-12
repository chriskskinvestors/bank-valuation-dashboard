"""
Master bank universe — all publicly traded and OTC-traded US banks.

Two-phase discovery:
  Phase 1: Cross-reference SEC company names with FDIC holding company names
  Phase 2: For remaining candidates with bank-like names, verify SIC code
           via EDGAR submissions API (6020-6036 = commercial banks / savings)

The universe is cached for 24 hours via Streamlit's @st.cache_data.
"""

import time
import requests
import streamlit as st

SEC_HEADERS = {"User-Agent": "BankValuationDashboard admin@company.com"}
BANK_SIC_CODES = {"6020", "6021", "6022", "6035", "6036", "6710", "6712"}
BANK_NAME_KEYWORDS = {
    "BANK", "BANCORP", "BANCSHARES", "BANC", "SAVINGS", "THRIFT",
    "FINANCIAL", "BK ", "NATIONAL ASSN",
}

# ETF/ETP tickers from bank issuers (e.g. Deutsche Bank issues ~30 ETNs
# that share CIK 1159508 with DB itself, polluting the bank universe).
_SKIP_TICKERS = {
    "BERZ", "BNKD", "BNKU", "BULZ", "CARD", "CARU", "CONL",
    "FLBL", "FLRT", "HERD", "NRGD", "NRGU", "OILK", "TSLZ",
    "FNGG", "FNGO", "HIBL", "HIBS", "WEBL", "WEBS", "ZSL",
    "BACRP",
    # Deutsche Bank-issued PowerShares/X-trackers ETNs
    "DGP", "DGZ", "DZZ", "DEE", "DEENF", "ADZCF", "OLOXF",
    "DBA", "DBB", "DBC", "DBE", "DBEU", "DBJP", "DBMV",
    "DBO", "DBP", "DBS", "DBV", "DJCI", "DJCB", "UDN",
    "UUP", "USDU", "BNO", "PPLT", "PALL",

    # Foreign-bank ADRs — our pipeline depends on SEC XBRL + FDIC Call
    # Reports, neither of which covers foreign banks. They legitimately
    # have no US-regulator data, so the universe filter would drop them
    # anyway — listing here makes the intent explicit.
    "BBD", "BBDO",  # Banco Bradesco (Brazil)
    "BNS",          # Bank of Nova Scotia (Canada)
    "ITUB",         # Itaú Unibanco (Brazil)
    "DB",           # Deutsche Bank (Germany) — covered above too via ETN dedup
    "BAWAY", "BWAGF",  # BAWAG Group AG (Austria) — ADR + F-share, CIK 1968385
    # Foreign parents of US bank subsidiaries — matched FDIC holding-company
    # names in phase 1 (HSBC Bank USA, City National/RBC, Santander Bank NA…)
    # but are foreign-domiciled filers (6-K/20-F, no US-GAAP facts). Also
    # excluded structurally by _is_us_domestic_filer; listed for intent.
    "HSBC",  # HSBC Holdings (UK)
    "RY",    # Royal Bank of Canada
    "BMO",   # Bank of Montreal (Canada)
    "SAN",   # Banco Santander (Spain)
    "BCS",   # Barclays (UK)
    "UBS",   # UBS Group (Switzerland)
    "MFG",   # Mizuho Financial Group (Japan)
    "WF",    # Woori Financial Group (Korea)
    "SHG",   # Shinhan Financial Group (Korea)
}

# US-domiciled only (explicit scope: US banks traded on exchanges or OTC).
# SEC submissions mark incorporation with state codes; foreign issuers carry
# country codes (BAWAG: "C4" = Austria). US states + DC + territories:
_US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY", "PR", "GU", "VI", "AS", "MP",
}

# Foreign-private-issuer forms. A US domestic issuer MUST file 10-K/10-Q;
# foreign issuers file these instead (and some, like HSBC, carry an EMPTY
# stateOfIncorporation — so the state check alone is not sufficient).
_FOREIGN_ISSUER_FORMS = {"20-F", "40-F", "6-K"}
_DOMESTIC_FORMS = {"10-K", "10-Q"}


def _is_us_domestic_filer(sub: dict) -> bool:
    """US-domicile check from a SEC submissions JSON, using two signals:
    (1) stateOfIncorporation is a US state/territory code when present;
    (2) the recent filing forms include 10-K/10-Q (domestic) rather than
    only 20-F/40-F/6-K (foreign private issuer). Empty state + domestic
    forms passes (some US filers omit the state)."""
    state = (sub.get("stateOfIncorporation") or "").strip().upper()
    if state and state not in _US_STATE_CODES:
        return False
    forms = set(sub.get("filings", {}).get("recent", {}).get("form", []))
    if forms & _FOREIGN_ISSUER_FORMS and not forms & _DOMESTIC_FORMS:
        return False
    return True


def _clean(n: str) -> str:
    """Normalize a company name for matching."""
    n = n.upper()
    for s in [", INC.", ", INC", " INC.", " INC", " CORP.", " CORP",
              " CO.", " CO", " LTD.", " LTD", "/DE", "/MD", "/NJ",
              "/RI", "/PA", "/OH", "/NC", "/NY", "/VA", "/CA",
              "/WI", "/MI", "/MN", "/TX", "/FL", "/GA", "/IL",
              " N.A.", " NA", ".", ",", "&"]:
        n = n.replace(s, "")
    return n.strip()


def _fetch_fdic_banks() -> dict[str, dict]:
    """Fetch all ACTIVE FDIC institutions and build the HC lookup.

    Filter is ACTIVE:1 — institution records are current-state, so no report
    vintage is needed. (The original REPDTE:YYYYMMDD filter matched ZERO rows
    from the day it was written: the institutions endpoint formats REPDTE as
    MM/DD/YYYY. Combined with the old `except: break`, that meant this
    function silently returned an empty lookup forever and the universe was
    the curated map alone.)

    Raises on fetch failure or an empty result — a partial list must never be
    silently used and cached for 24h."""
    fdic_banks: list[dict] = []
    offset = 0
    while True:
        params = {
            "filters": "ACTIVE:1",
            "fields": "CERT,NAME,NAMEHCR,ASSET",
            "sort_by": "ASSET",
            "sort_order": "DESC",
            "limit": 1000,
            "offset": offset,
        }
        resp = requests.get(
            "https://banks.data.fdic.gov/api/institutions",
            params=params, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data", [])
        if not rows:
            break
        for r in rows:
            d = r["data"]
            fdic_banks.append({
                "cert": int(d["CERT"]),
                "namehcr": d.get("NAMEHCR", ""),
                "asset": d.get("ASSET") or 0,
            })
        offset += len(rows)
        if offset >= data.get("totals", {}).get("count", 0):
            break
        time.sleep(0.05)
    if not fdic_banks:
        raise RuntimeError("FDIC institutions endpoint returned no active institutions")

    # Deduplicate: HC name -> largest bank cert
    hc_lookup = {}
    for b in fdic_banks:
        hc = b["namehcr"].upper().strip()
        if not hc or len(hc) < 3:
            continue
        if hc not in hc_lookup or b["asset"] > hc_lookup[hc]["asset"]:
            hc_lookup[hc] = b
    return hc_lookup


def _fetch_sec_companies() -> list[list]:
    """Fetch all SEC public companies with tickers. Raises on failure — an
    empty list here would silently collapse the universe to the curated map."""
    resp = requests.get(
        "https://www.sec.gov/files/company_tickers_exchange.json",
        headers=SEC_HEADERS, timeout=15,
    )
    resp.raise_for_status()
    rows = resp.json().get("data", [])
    if not rows:
        raise RuntimeError("SEC company_tickers_exchange.json returned no rows")
    return rows


def _load_lastgood() -> tuple[dict | None, bool]:
    """(universe, is_fresh) from the persisted snapshot. Handles both the
    stamped format ({cached_at, universe}) and the legacy bare-dict format
    (treated as present-but-stale)."""
    from data import cache as _cache
    from data.freshness import is_fresh
    snap = _cache.get("bank_universe_lastgood")
    if not snap:
        return None, False
    if isinstance(snap, dict) and "universe" in snap:
        return snap["universe"], is_fresh(snap, 26 * 3600)
    return snap, False  # legacy format: usable as fallback, never "fresh"


@st.cache_data(ttl=3600, show_spinner="Loading bank universe...")
def build_universe() -> dict[str, dict]:
    """
    The full universe of publicly traded US banks (~470).

    Returns dict: ticker -> {name, cik, fdic_cert, exchange}

    Serving strategy: the LIVE build (full FDIC pagination + per-candidate SEC
    SIC verification) takes ~6-7 minutes — far too slow for a user request —
    so interactive processes serve the snapshot persisted by the nightly
    jobs/refresh_universe run when it's fresh (<26h). A live build happens
    here only when the snapshot is missing/stale, and a source failure falls
    back to a stale snapshot rather than raising (a shrunken universe is never
    silently built — _fetch_* raise on partial data).
    """
    lastgood, fresh = _load_lastgood()
    if lastgood and fresh:
        return lastgood
    try:
        return refresh_universe_snapshot()
    except Exception as e:
        if lastgood:
            print(f"[universe] live build failed ({type(e).__name__}: {e}); "
                  f"serving stale last-good universe ({len(lastgood)} banks)")
            return lastgood
        raise


def refresh_universe_snapshot() -> dict[str, dict]:
    """Run the live build and persist it as the snapshot interactive
    processes serve. Called nightly by jobs/refresh_universe."""
    from datetime import datetime
    universe = _build_universe_live()
    try:
        from data import cache as _cache
        _cache.put("bank_universe_lastgood",
                   {"cached_at": datetime.now().isoformat(), "universe": universe})
    except Exception as e:
        print(f"[universe] could not persist snapshot: {type(e).__name__}: {e}")
    return universe


def _build_universe_live() -> dict[str, dict]:
    """The actual SEC×FDIC fetch + match. ~6-7 minutes; never call from an
    interactive request path — use build_universe()."""
    sec_rows = _fetch_sec_companies()
    hc_lookup = _fetch_fdic_banks()

    universe = {}

    # ── Phase 1: Strong name matching ────────────────────────────────────
    for row in sec_rows:
        cik, name, ticker, exchange = row
        if not ticker or not name:
            continue
        ticker = ticker.upper()
        if any(c in ticker for c in ["-", "+"]) or len(ticker) > 5:
            continue
        if ticker in _SKIP_TICKERS:
            continue

        sec_clean = _clean(name)

        for hc_raw, bank_info in hc_lookup.items():
            hc_clean = _clean(hc_raw)

            # Exact match
            if sec_clean == hc_clean:
                universe[ticker] = {
                    "name": name.title() if name.isupper() else name,
                    "cik": int(cik),
                    "fdic_cert": bank_info["cert"],
                    "exchange": exchange or "OTC",
                }
                break

            # Strong prefix match (both names >= 8 chars, >= 65% overlap)
            if len(sec_clean) >= 8 and len(hc_clean) >= 8:
                if hc_clean.startswith(sec_clean) or sec_clean.startswith(hc_clean):
                    overlap = min(len(sec_clean), len(hc_clean))
                    ratio = overlap / max(len(sec_clean), len(hc_clean))
                    if ratio >= 0.65:
                        universe[ticker] = {
                            "name": name.title() if name.isupper() else name,
                            "cik": int(cik),
                            "fdic_cert": bank_info["cert"],
                            "exchange": exchange or "OTC",
                        }
                        break

    # ── Phase 1.5: US-domicile enforcement on name matches ──────────────
    # Phase-1 matches on FDIC holding-company NAMES, which foreign parents
    # of US bank subsidiaries also carry (HSBC Holdings ↔ HSBC Bank USA,
    # Royal Bank of Canada ↔ City National, Santander ↔ Santander Bank NA).
    # Those parents are foreign-domiciled filers (6-K/20-F, no US-GAAP
    # facts) — out of scope ("US-domiciled banks on exchanges or OTC").
    # ~1 fetch per phase-1 match (+~50s nightly), same source as phase 2.
    for t in sorted(universe):
        cik = universe[t].get("cik")
        if not cik:
            continue
        try:
            time.sleep(0.12)  # SEC rate limit: 10 req/sec
            resp = requests.get(
                f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json",
                headers=SEC_HEADERS, timeout=8,
            )
            if resp.status_code != 200:
                continue  # transient — keep; nightly rebuild retries tomorrow
            if not _is_us_domestic_filer(resp.json()):
                print(f"[universe] dropping {t}: foreign-domiciled filer")
                del universe[t]
        except requests.RequestException:
            continue

    # ── Phase 2: SIC code verification for bank-named candidates ─────────
    candidates = []
    for row in sec_rows:
        cik, name, ticker, exchange = row
        if not ticker or not name:
            continue
        ticker = ticker.upper()
        if ticker in universe:
            continue
        if any(c in ticker for c in ["-", "+"]) or len(ticker) > 5:
            continue
        if ticker in _SKIP_TICKERS:
            continue

        name_upper = name.upper()
        if any(kw in name_upper for kw in BANK_NAME_KEYWORDS):
            candidates.append({
                "cik": int(cik), "name": name,
                "ticker": ticker, "exchange": exchange,
            })

    for c in candidates:
        try:
            time.sleep(0.12)  # SEC rate limit: 10 req/sec
            cik_str = str(c["cik"]).zfill(10)
            resp = requests.get(
                f"https://data.sec.gov/submissions/CIK{cik_str}.json",
                headers=SEC_HEADERS, timeout=8,
            )
            if resp.status_code != 200:
                continue
            sub = resp.json()
            sic = sub.get("sic", "")
            if sic not in BANK_SIC_CODES:
                continue
            if not _is_us_domestic_filer(sub):
                continue

            # Confirmed bank — try to find FDIC cert
            fdic_cert = None
            name_clean = _clean(c["name"])
            first_word = name_clean.split()[0] if name_clean else ""
            if first_word and len(first_word) >= 4:
                for hc_raw, bank_info in hc_lookup.items():
                    hc_clean = _clean(hc_raw)
                    if hc_clean.startswith(first_word) or first_word in hc_clean.split():
                        fdic_cert = bank_info["cert"]
                        break

            universe[c["ticker"]] = {
                "name": c["name"].title() if c["name"].isupper() else c["name"],
                "cik": c["cik"],
                "fdic_cert": fdic_cert,
                "exchange": c["exchange"] or "OTC",
            }
        except Exception:
            continue

    # ── Reconcile with curated mappings ──────────────────────────────────
    # The live name-matching above silently drops banks whose SEC and FDIC
    # names don't align — including mega-caps (JPM, C, WFC, GS, MS) and many
    # mid-caps. We have curated, verified CIK+CERT mappings for those, so fold
    # every one in. Skip ETNs (_SKIP_TICKERS) and the handful of foreign ADRs /
    # acquired banks our SEC-XBRL + FDIC-Call-Report pipeline genuinely can't
    # cover (no US-regulator data).
    _NON_COVERABLE = {"DB", "BBD", "BBDO", "BNS", "UBS", "ITUB", "WF", "MCBI"}
    from data.bank_mapping import BANK_MAP
    curated = dict(BANK_MAP)
    try:
        from data.bank_mapping import _RESOLVED_FROM_JSON
        for t, info in _RESOLVED_FROM_JSON.items():
            curated.setdefault(t, info)
    except Exception:
        pass
    for ticker, info in curated.items():
        t = ticker.upper()
        if t in _SKIP_TICKERS or t in _NON_COVERABLE:
            continue
        cik = info.get("cik")
        cert = info.get("fdic_cert")
        if not cik and not cert:
            continue
        # Curated mappings are verified CIK+CERT pairs — they OVERWRITE any
        # phase-1 fuzzy name-match for the same ticker (verified beats fuzzy;
        # previously the fuzzy match would have shadowed the curated one).
        # Keep the SEC-derived exchange when the curated entry lacks one.
        existing = universe.get(t) or {}
        universe[t] = {
            "name": info.get("name") or existing.get("name") or t,
            "cik": int(cik) if cik else None,
            "fdic_cert": int(cert) if cert else None,
            "exchange": info.get("exchange") or existing.get("exchange") or "OTC",
        }

    return universe


# Module-level cache to avoid re-deserializing the universe dict on every call
_UNIVERSE_CACHE: dict | None = None


def get_universe() -> dict[str, dict]:
    """Get the universe dict, cached at module level for maximum speed."""
    global _UNIVERSE_CACHE
    if _UNIVERSE_CACHE is None:
        _UNIVERSE_CACHE = build_universe()
    return _UNIVERSE_CACHE


@st.cache_data(ttl=86400, show_spinner=False)
def get_universe_tickers() -> list[str]:
    """
    Return sorted list of all bank tickers in the universe — FILTERED to
    only those that resolve to at least one ACTIVE data source:
      • SEC CIK with XBRL data (validated by the resolver), OR
      • FDIC cert pointing to an ACTIVE institution (not acquired/closed).

    Tickers that can't resolve, or whose only data source is an inactive
    FDIC cert (acquired bank like MCBI), are dropped here so they never
    appear empty or stale in the UI.
    """
    from data.bank_mapping import get_cik, get_fdic_cert
    from data.fdic_client import cert_is_active

    all_tickers = sorted(get_universe().keys())

    def _resolves(t: str) -> bool:
        cik = get_cik(t)
        if cik is not None:
            return True  # SEC path; resolver already validated XBRL recency
        cert = get_fdic_cert(t)
        if cert is None:
            return False
        return cert_is_active(cert)  # Drop acquired/closed institutions

    resolved = [t for t in all_tickers if _resolves(t)]
    dropped = set(all_tickers) - set(resolved)
    if dropped:
        print(f"[universe] Dropped {len(dropped)} unresolvable/inactive tickers: "
              f"{', '.join(sorted(dropped)[:10])}"
              f"{'...' if len(dropped) > 10 else ''}")
    return resolved


def get_universe_count() -> int:
    """Return the number of banks in the universe."""
    return len(get_universe())


def get_universe_count_fast() -> str:
    """
    Return the universe size for the header WITHOUT forcing an expensive build.

    Prefers the real built count when the universe is already cached this
    process; otherwise falls back to the curated mapping count (cheap, static).
    Never returns a hardcoded guess.
    """
    if _UNIVERSE_CACHE is not None:
        return str(len(_UNIVERSE_CACHE))
    try:
        from data.bank_mapping import BANK_MAP
        certs = set(BANK_MAP)
        try:
            from data.bank_mapping import _RESOLVED_FROM_JSON
            certs |= set(_RESOLVED_FROM_JSON)
        except Exception:
            pass
        return str(len(certs))
    except Exception:
        return "—"


def search_universe(query: str, limit: int = 25) -> list[dict]:
    """
    Search the bank universe by ticker or company name.
    Returns list of {ticker, name, cik, fdic_cert, exchange}.
    """
    universe = get_universe()
    query_upper = query.upper().strip()

    if not query_upper:
        return []

    # Exact ticker match
    if query_upper in universe:
        return [{"ticker": query_upper, **universe[query_upper]}]

    results = []
    seen_tickers = set()

    # Ticker prefix match first
    for ticker, info in universe.items():
        if ticker.startswith(query_upper):
            results.append({"ticker": ticker, **info})
            seen_tickers.add(ticker)

    # Then name match (O(1) lookup via set instead of O(n) list scan)
    for ticker, info in universe.items():
        if ticker in seen_tickers:
            continue
        if query_upper in info["name"].upper():
            results.append({"ticker": ticker, **info})

    return results[:limit]


def get_universe_bank(ticker: str) -> dict | None:
    """
    Look up a single bank in the universe.
    Falls back to dynamic resolution if not in prebuilt universe.
    """
    ticker = ticker.upper()
    universe = get_universe()
    info = universe.get(ticker)
    if info:
        return {"ticker": ticker, **info}

    # Fallback to dynamic resolution
    from data.bank_mapping import resolve_ticker
    resolved = resolve_ticker(ticker)
    if resolved.get("cik") or resolved.get("fdic_cert"):
        return resolved
    return None
