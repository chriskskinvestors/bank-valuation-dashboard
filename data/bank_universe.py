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

# ETF/ETP tickers from bank issuers
_SKIP_TICKERS = {
    "BERZ", "BNKD", "BNKU", "BULZ", "CARD", "CARU", "CONL",
    "FLBL", "FLRT", "HERD", "NRGD", "NRGU", "OILK", "TSLZ",
    "FNGG", "FNGO", "HIBL", "HIBS", "WEBL", "WEBS", "ZSL",
    "BACRP",
}


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
    """Fetch all FDIC institutions for the latest quarter and build HC lookup."""
    fdic_banks = []
    offset = 0
    while True:
        try:
            params = {
                "filters": "REPDTE:20251231",
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
        except Exception:
            break

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
    """Fetch all SEC public companies with tickers."""
    try:
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers_exchange.json",
            headers=SEC_HEADERS, timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception:
        return []


@st.cache_data(ttl=86400, show_spinner="Building bank universe...")
def build_universe() -> dict[str, dict]:
    """
    Build the full universe of publicly traded US banks (~450-500 banks).

    Returns dict: ticker -> {name, cik, fdic_cert, exchange}
    """
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
            sic = resp.json().get("sic", "")
            if sic not in BANK_SIC_CODES:
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

    return universe


@st.cache_data(ttl=86400, show_spinner=False)
def get_universe_tickers() -> list[str]:
    """Return sorted list of all bank tickers in the universe."""
    return sorted(build_universe().keys())


def get_universe_count() -> int:
    """Return the number of banks in the universe."""
    return len(build_universe())


def get_universe_count_fast() -> str:
    """Return estimated universe count without triggering a build."""
    return "~480"


def search_universe(query: str, limit: int = 25) -> list[dict]:
    """
    Search the bank universe by ticker or company name.
    Returns list of {ticker, name, cik, fdic_cert, exchange}.
    """
    universe = build_universe()
    query_upper = query.upper().strip()

    if not query_upper:
        return []

    # Exact ticker match
    if query_upper in universe:
        return [{"ticker": query_upper, **universe[query_upper]}]

    results = []
    # Ticker prefix match first
    for ticker, info in universe.items():
        if ticker.startswith(query_upper):
            results.append({"ticker": ticker, **info})

    # Then name match
    for ticker, info in universe.items():
        if ticker in [r["ticker"] for r in results]:
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
    universe = build_universe()
    info = universe.get(ticker)
    if info:
        return {"ticker": ticker, **info}

    # Fallback to dynamic resolution
    from data.bank_mapping import resolve_ticker
    resolved = resolve_ticker(ticker)
    if resolved.get("cik") or resolved.get("fdic_cert"):
        return resolved
    return None
