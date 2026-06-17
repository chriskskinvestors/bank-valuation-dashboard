"""
One-shot mapping resolver — discovers correct CIK + CERT for every bank
in the universe and writes the result to data/bank_map_resolved.json.

For each universe ticker:
  1. Get the official SEC CIK from tickers.json (cached)
  2. Pull the SEC submissions endpoint to get the canonical company name
  3. Search FDIC by NAMEHCR with that name (and variations) — pick best match
  4. Verify the chosen CERT actually returns financials
  5. Record the resolved (cik, cert, name) tuple

The output JSON gets loaded by data/bank_mapping.py at startup as a
high-priority lookup, ahead of the heuristic dynamic resolver. The
dynamic resolver stays as a fallback for tickers we don't see during
the build pass.

Run:   python tools/resolve_all_mappings.py
Output: data/bank_map_resolved.json
"""

from __future__ import annotations
import json
import re
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import requests

UA = {"User-Agent": "BankValuationDashboard chris@kskinvestors.com"}


# ──────────────────────────────────────────────────────────────────────────
# SEC lookups
# ──────────────────────────────────────────────────────────────────────────

_SEC_TICKERS: dict[str, dict] = {}


def _load_sec_tickers():
    global _SEC_TICKERS
    if _SEC_TICKERS:
        return _SEC_TICKERS
    r = requests.get("https://www.sec.gov/files/company_tickers.json",
                     headers=UA, timeout=20)
    r.raise_for_status()
    data = r.json()
    _SEC_TICKERS = {v["ticker"]: v for v in data.values()}
    print(f"[SEC] loaded {len(_SEC_TICKERS)} tickers from official map")
    return _SEC_TICKERS


def get_sec_info(ticker: str) -> dict | None:
    """Returns {'cik': int, 'name': str} or None."""
    info = _load_sec_tickers().get(ticker.upper())
    if not info:
        return None
    return {"cik": info["cik_str"], "name": info["title"]}


def sec_has_xbrl(cik: int, max_age_years: int = 2) -> bool:
    """
    Does SEC's companyfacts endpoint return *recent* data for this CIK?

    HTTP 200 is not enough — companyfacts also returns 200 for deregistered
    companies whose XBRL data is years old (e.g. CCNB last filed XBRL in
    2013). Verifies that at least one of a few key concepts has a value
    dated within `max_age_years`.
    """
    try:
        r = requests.get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json",
            headers=UA, timeout=10,
        )
        if r.status_code != 200:
            return False
        data = r.json()
    except Exception:
        return False

    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=365 * max_age_years)).strftime("%Y-%m-%d")
    us_gaap = (data.get("facts") or {}).get("us-gaap", {})
    # Check a few concepts that any active filer would update
    for concept in ("NetIncomeLoss", "StockholdersEquity", "Assets", "Revenues",
                     "EarningsPerShareBasic"):
        units = (us_gaap.get(concept) or {}).get("units", {})
        for vals in units.values():
            for v in vals:
                if (v.get("end") or "") >= cutoff:
                    return True
    return False


# ──────────────────────────────────────────────────────────────────────────
# FDIC search
# ──────────────────────────────────────────────────────────────────────────

_GENERIC_TOKENS = {"BANK", "BANC", "BANCORP", "BANCSHARES", "FINANCIAL",
                   "BANKSHARES", "TRUST", "NATIONAL", "FEDERAL", "FIRST",
                   "THE", "OF", "AND", "&", "INC", "CORP", "CO", "LTD",
                   "GROUP", "HOLDINGS", "COMPANY", "BHC"}


def _clean_for_search(name: str) -> str:
    """Strip suffixes + punctuation, leave content words."""
    if not name:
        return ""
    n = name.upper()
    n = re.sub(r"\s*[,]?\s*(INC\.?|CORP\.?|CORPORATION|CO\.?|LTD\.?|"
               r"LLC|HOLDINGS|GROUP|COMPANY|/[A-Z]{2,3}/?)\.?\s*$",
               "", n, flags=re.IGNORECASE)
    n = re.sub(r"[^\w\s&]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def _content_tokens(name: str) -> list[str]:
    """Words from a name that are NOT generic banking suffixes."""
    return [t for t in _clean_for_search(name).split() if t not in _GENERIC_TOKENS]


def fdic_search_by_name(name: str) -> list[dict]:
    """
    Try a few NAMEHCR variations and return matched institutions.
    Returns active institutions with positive assets, sorted by asset desc.
    """
    if not name:
        return []
    cleaned = _clean_for_search(name)
    if not cleaned:
        return []

    tokens = _content_tokens(name)
    queries = []
    # Try most specific to least specific
    if len(tokens) >= 3:
        queries.append(" ".join(tokens[:3]))
    if len(tokens) >= 2:
        queries.append(" ".join(tokens[:2]))
    queries.append(cleaned)
    if tokens:
        queries.append(tokens[0])

    seen_certs: set[int] = set()
    matches: list[dict] = []

    for q in queries:
        # Quoted phrase search in NAMEHCR (holding company name)
        q_quoted = f'"{q}"'.replace(" ", "%20").replace('"', "%22")
        url = "https://banks.data.fdic.gov/api/institutions"
        try:
            r = requests.get(url, params={
                "filters": f"NAMEHCR:{q_quoted} AND ACTIVE:1",
                "fields": "CERT,NAME,NAMEHCR,ASSET,STALP",
                "sort_by": "ASSET", "sort_order": "DESC",
                "limit": 5,
            }, headers=UA, timeout=10)
            if r.status_code != 200:
                continue
            for entry in r.json().get("data", []):
                d = entry.get("data", {})
                cert = d.get("CERT")
                if cert is None or cert in seen_certs:
                    continue
                seen_certs.add(cert)
                matches.append({
                    "cert": cert,
                    "name": d.get("NAME", ""),
                    "namehcr": d.get("NAMEHCR", ""),
                    "asset": d.get("ASSET", 0),
                    "state": d.get("STALP", ""),
                })
        except Exception:
            continue

    return matches


def best_fdic_match(sec_name: str, matches: list[dict]) -> dict | None:
    """Score matches by token overlap with the SEC name. Highest wins."""
    if not matches:
        return None
    sec_tokens = set(_content_tokens(sec_name))
    if not sec_tokens:
        return matches[0]  # take asset-sorted top if we have no content tokens

    def score(m: dict) -> float:
        # Score = Jaccard on content tokens of NAMEHCR vs SEC name
        hcr_tokens = set(_content_tokens(m["namehcr"]))
        if not hcr_tokens:
            return 0
        inter = sec_tokens & hcr_tokens
        union = sec_tokens | hcr_tokens
        return len(inter) / len(union)

    scored = [(score(m), m) for m in matches]
    scored.sort(key=lambda x: (-x[0], -m["asset"] if (m := x[1]) else 0))
    top_score, top = scored[0]
    if top_score < 0.3:
        return None  # not confident enough
    return top


def fdic_verify_cert(cert: int) -> bool:
    """Confirm this cert returns financials (not just static institution data)."""
    try:
        r = requests.get(
            "https://banks.data.fdic.gov/api/financials",
            params={"filters": f"CERT:{cert}", "fields": "CERT,REPDTE", "limit": 1},
            headers=UA, timeout=10,
        )
        return r.status_code == 200 and bool(r.json().get("data"))
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────
# Per-ticker resolver
# ──────────────────────────────────────────────────────────────────────────

def resolve(ticker: str) -> dict:
    """Return {ticker, cik, cert, name, source} for one ticker."""
    out = {"ticker": ticker, "cik": None, "cert": None, "name": ticker,
           "sec_xbrl": False, "fdic_score": 0.0}

    sec = get_sec_info(ticker)
    if sec:
        out["name"] = sec["name"]
        # Only set CIK if SEC actually has XBRL for it; otherwise FDIC-only path
        if sec_has_xbrl(sec["cik"]):
            out["cik"] = sec["cik"]
            out["sec_xbrl"] = True

    # FDIC search — use SEC-canonical name if available, else ticker
    search_name = out["name"]
    matches = fdic_search_by_name(search_name)
    pick = best_fdic_match(search_name, matches)
    if pick and fdic_verify_cert(pick["cert"]):
        out["cert"] = pick["cert"]
        # Compute the score we used for selection (for reporting)
        sec_tokens = set(_content_tokens(search_name))
        hcr_tokens = set(_content_tokens(pick["namehcr"]))
        if hcr_tokens and sec_tokens:
            inter = sec_tokens & hcr_tokens
            union = sec_tokens | hcr_tokens
            out["fdic_score"] = round(len(inter) / len(union), 2)

    return out


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────

def main():
    import warnings; warnings.filterwarnings("ignore")
    from data.bank_universe import get_universe_tickers
    from data.bank_mapping import BANK_MAP

    tickers = sorted(set(get_universe_tickers()) | set(BANK_MAP.keys()))
    print(f"Resolving {len(tickers)} tickers...")
    _load_sec_tickers()  # eager load so we don't race

    results: list[dict] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(resolve, t): t for t in tickers}
        done = 0
        for fut in as_completed(futures):
            results.append(fut.result())
            done += 1
            if done % 50 == 0 or done == len(tickers):
                print(f"  {done}/{len(tickers)} ({time.time()-t0:.0f}s)")

    # Stats
    has_cik = sum(1 for r in results if r["cik"])
    has_cert = sum(1 for r in results if r["cert"])
    has_both = sum(1 for r in results if r["cik"] and r["cert"])
    has_either = sum(1 for r in results if r["cik"] or r["cert"])
    has_neither = sum(1 for r in results if not r["cik"] and not r["cert"])
    print()
    print(f"Resolved CIK:   {has_cik}/{len(results)} ({has_cik/len(results)*100:.1f}%)")
    print(f"Resolved CERT:  {has_cert}/{len(results)} ({has_cert/len(results)*100:.1f}%)")
    print(f"Both:           {has_both}/{len(results)}")
    print(f"At least one:   {has_either}/{len(results)}")
    print(f"Neither:        {has_neither}/{len(results)}")

    # Sort results by ticker, write JSON. Names go through the shared display
    # formatter so the stored data is already normalized (Title Case, EDGAR
    # /XX/ suffixes + corporate-form tokens dropped, ticker acronyms kept).
    from utils.formatting import format_bank_name
    results.sort(key=lambda r: r["ticker"])
    out_path = REPO_ROOT / "data" / "bank_map_resolved.json"
    payload = {r["ticker"]: {"cik": r["cik"], "fdic_cert": r["cert"],
                              "name": format_bank_name(r["name"], r["ticker"]),
                              "fdic_score": r["fdic_score"]}
               for r in results}
    out_path.write_text(json.dumps(payload, indent=1, sort_keys=True))
    print(f"\nWrote {out_path}")

    # Print residual no-data tickers (will help curate manual overrides)
    no_data = [r["ticker"] for r in results if not r["cik"] and not r["cert"]]
    if no_data:
        print(f"\nNo CIK and no CERT ({len(no_data)}):")
        for t in no_data:
            print(f"  {t}")


if __name__ == "__main__":
    main()
