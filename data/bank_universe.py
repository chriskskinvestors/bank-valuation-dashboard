"""
Master bank universe — all publicly traded and OTC-traded US banks.

Builds the universe by cross-referencing:
  - SEC EDGAR company_tickers_exchange.json (all public companies with tickers)
  - FDIC institutions API (all FDIC-insured banks with holding company names)

The universe is cached for 24 hours via Streamlit's @st.cache_data.
Individual lookups for banks not in the prebuilt universe fall back to
the dynamic resolve_ticker() function in bank_mapping.py.
"""

import time
import requests
import streamlit as st

SEC_HEADERS = {"User-Agent": "BankValuationDashboard admin@company.com"}

# ETF/ETP tickers from bank issuers that aren't actual bank stocks
_ETF_TICKERS = {
    "BERZ", "BNKD", "BNKU", "BULZ", "CARD", "CARU", "CONL",
    "FLBL", "FLRT", "HERD", "NRGD", "NRGU", "OILK", "TSLZ",
    "FNGG", "FNGO", "HIBL", "HIBS", "WEBL", "WEBS", "ZSL",
    "BACRP",  # ETN
}


def _clean_name(n: str) -> str:
    """Normalize a company name for matching."""
    n = n.upper()
    for s in [", INC.", ", INC", " INC.", " INC", " CORP.", " CORP",
              " CO.", " CO", " LTD.", " LTD", "/DE", "/MD", "/NJ",
              "/RI", "/PA", "/OH", "/NC", "/NY", "/VA", "/CA",
              "/WI", "/MI", "/MN", "/TX", "/FL", "/GA", "/IL",
              " N.A.", " NA", ".", ","]:
        n = n.replace(s, "")
    return n.strip()


@st.cache_data(ttl=86400, show_spinner="Building bank universe...")
def build_universe() -> dict[str, dict]:
    """
    Build the full universe of publicly traded US banks.

    Returns dict: ticker -> {name, cik, fdic_cert, exchange}
    """
    # ── Step 1: All SEC public companies ─────────────────────────────────
    try:
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers_exchange.json",
            headers=SEC_HEADERS, timeout=15,
        )
        resp.raise_for_status()
        sec_rows = resp.json().get("data", [])
    except Exception as e:
        print(f"[Universe] SEC fetch error: {e}")
        sec_rows = []

    # ── Step 2: All FDIC-insured institutions ────────────────────────────
    fdic_banks = []
    offset = 0
    while True:
        try:
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
        except Exception:
            break

    # ── Step 3: Build HC name -> largest cert lookup ─────────────────────
    hc_lookup = {}  # cleaned HC name -> {cert, asset}
    for b in fdic_banks:
        hc = b["namehcr"].upper().strip()
        if not hc or len(hc) < 3:
            continue
        hc_clean = _clean_name(hc)
        if hc_clean not in hc_lookup or b["asset"] > hc_lookup[hc_clean]["asset"]:
            hc_lookup[hc_clean] = b

    # ── Step 4: Cross-reference SEC tickers with FDIC holding companies ──
    universe = {}
    for row in sec_rows:
        cik, name, ticker, exchange = row
        if not ticker:
            continue
        ticker = ticker.upper()

        # Skip preferred shares, warrants, ETFs/ETNs
        if any(c in ticker for c in ["-", "+"]):
            continue
        if ticker in _ETF_TICKERS:
            continue
        if len(ticker) > 5:  # Most bank tickers are 1-5 chars
            continue

        sec_clean = _clean_name(name)

        # Find best FDIC match
        best_cert = None
        best_score = 0

        for hc_clean, bank_info in hc_lookup.items():
            if sec_clean == hc_clean:
                best_cert = bank_info["cert"]
                best_score = 100
                break

            # Require both names to be long enough for prefix matching
            min_len = min(len(sec_clean), len(hc_clean))
            if min_len < 8:
                continue

            if hc_clean.startswith(sec_clean):
                score = len(sec_clean) / len(hc_clean) * 90
            elif sec_clean.startswith(hc_clean):
                score = len(hc_clean) / len(sec_clean) * 90
            else:
                continue

            if score > best_score:
                best_cert = bank_info["cert"]
                best_score = score

        if best_cert and best_score >= 65:
            universe[ticker] = {
                "name": name.title() if name.isupper() else name,
                "cik": int(cik),
                "fdic_cert": best_cert,
                "exchange": exchange or "OTC",
            }

    return universe


@st.cache_data(ttl=86400, show_spinner=False)
def get_universe_tickers() -> list[str]:
    """Return sorted list of all bank tickers in the universe."""
    return sorted(build_universe().keys())


def get_universe_count() -> int:
    """Return the number of banks in the universe."""
    return len(build_universe())


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
