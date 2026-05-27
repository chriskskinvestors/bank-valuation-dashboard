"""
OTC bank ticker discovery.

For every active FDIC institution, queries SEC EDGAR full-text search by
holding-company name to find OTC-registered tickers that aren't in SEC's
exchange-listed `company_tickers.json`. Verifies each match by:
  1. Confirming the EDGAR display name contains a ticker symbol
  2. Pulling the CIK's submissions endpoint to get the canonical tickers list
  3. Token-overlap match against the FDIC NAMEHCR (Jaccard ≥ 0.5)
  4. Verifying the SEC entity has recent XBRL data OR at least recent filings

Output: merges newly-discovered OTC tickers into data/bank_map_resolved.json.

Run:   python tools/discover_otc_tickers.py
Expected runtime: ~10-20 min for ~4,000 FDIC banks (rate-limited).
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
EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"
EDGAR_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# Generic tokens that don't help with matching
_GENERIC = {"BANK", "BANC", "BANCORP", "BANCSHARES", "FINANCIAL", "BANKSHARES",
            "TRUST", "NATIONAL", "FEDERAL", "FIRST", "THE", "OF", "AND",
            "INC", "CORP", "CO", "LTD", "GROUP", "HOLDINGS", "COMPANY",
            "&", "BHC", "HOLDING", "SAVINGS", "THRIFT"}


def _norm(name: str) -> set[str]:
    """Tokenize and strip generic words for fuzzy name matching."""
    if not name:
        return set()
    s = re.sub(r"[^\w\s&]", " ", name.upper())
    s = re.sub(r"/[A-Z]{2,3}/?", "", s)
    return {t for t in s.split() if t and t not in _GENERIC}


def _name_match_score(a: str, b: str) -> float:
    ta, tb = _norm(a), _norm(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# Match a ticker symbol embedded in EDGAR's display_names format:
#   "Community Heritage Financial, Inc.  (CMHF)  (CIK 0001792597)"
_TICKER_IN_DISPLAY = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,8})\)\s*\(CIK")


def _search_edgar(query: str, max_attempts: int = 3) -> list[dict]:
    """Search EDGAR full-text. Returns the top hits."""
    for attempt in range(max_attempts):
        try:
            r = requests.get(EDGAR_SEARCH, params={"q": f'"{query}"'},
                              headers=UA, timeout=15)
            if r.status_code == 429:
                time.sleep((2 ** attempt) + 1)
                continue
            r.raise_for_status()
            return r.json().get("hits", {}).get("hits", [])
        except Exception:
            if attempt == max_attempts - 1:
                return []
            time.sleep((2 ** attempt) + 1)
    return []


def _get_tickers_from_submissions(cik: int) -> list[str]:
    """Pull the canonical tickers list from SEC submissions endpoint."""
    try:
        r = requests.get(EDGAR_SUBMISSIONS.format(cik=int(cik)), headers=UA, timeout=10)
        if r.status_code != 200:
            return []
        return r.json().get("tickers", []) or []
    except Exception:
        return []


def _entity_has_recent_filings(cik: int, max_age_days: int = 730) -> bool:
    """Active SEC filer in the last 2 years?"""
    try:
        r = requests.get(EDGAR_SUBMISSIONS.format(cik=int(cik)), headers=UA, timeout=10)
        if r.status_code != 200:
            return False
        recent = r.json().get("filings", {}).get("recent", {})
        dates = recent.get("filingDate", [])
        if not dates:
            return False
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
        return max(dates) >= cutoff
    except Exception:
        return False


def discover_for_institution(inst: dict, existing_cert_to_ticker: dict) -> dict | None:
    """
    Discover the best ticker for one FDIC institution. Returns the proposed
    mapping or None.
    """
    cert = inst.get("cert")
    if not cert:
        return None
    # Skip if we already have a ticker for this cert
    if int(cert) in existing_cert_to_ticker:
        return None
    # Use holding-company name preferentially (it's the public entity)
    name = inst.get("namehcr") or inst.get("name") or ""
    if not name:
        return None

    hits = _search_edgar(name)
    if not hits:
        return None

    for h in hits[:5]:
        src = h.get("_source", {})
        display = src.get("display_names", [""])[0]
        # Extract ticker from display string
        m = _TICKER_IN_DISPLAY.search(display)
        if not m:
            continue
        ticker = m.group(1)
        ciks = src.get("ciks", [])
        if not ciks:
            continue
        cik = int(ciks[0])

        # Quality gate 1: name overlap
        # display has format "Company Name (TICKER) (CIK NNN)"
        # Strip the parenthetical parts for clean name match
        clean_display = re.sub(r"\s*\([^)]+\)\s*", "", display).strip()
        score = _name_match_score(name, clean_display)
        if score < 0.5:
            continue

        # Quality gate 2: ticker confirmed by submissions endpoint
        listed_tickers = _get_tickers_from_submissions(cik)
        if ticker not in listed_tickers:
            continue

        # Quality gate 3: actually filing recently
        if not _entity_has_recent_filings(cik):
            continue

        return {
            "ticker": ticker,
            "cik": cik,
            "fdic_cert": int(cert),
            "name": clean_display or name,
            "match_score": round(score, 2),
            "namehcr": name,
        }
    return None


def main():
    import warnings; warnings.filterwarnings("ignore")
    from data.fdic_client import list_all_active_institutions

    # Load existing resolved JSON + BANK_MAP to skip already-known certs
    resolved_path = REPO_ROOT / "data" / "bank_map_resolved.json"
    resolved = json.loads(resolved_path.read_text()) if resolved_path.exists() else {}
    from data.bank_mapping import BANK_MAP
    existing_cert_to_ticker: dict[int, str] = {}
    for ticker, info in {**BANK_MAP, **resolved}.items():
        cert = info.get("fdic_cert")
        if cert:
            existing_cert_to_ticker[int(cert)] = ticker
    print(f"[discover] {len(existing_cert_to_ticker)} certs already mapped to tickers")

    print("[discover] Enumerating active FDIC institutions...")
    institutions = list_all_active_institutions()
    candidates = [i for i in institutions
                  if i.get("namehcr") and int(i.get("cert") or 0) not in existing_cert_to_ticker]
    print(f"[discover] {len(institutions)} active banks, "
          f"{len(candidates)} candidates for OTC discovery")

    found: list[dict] = []
    t0 = time.time()
    # Conservative concurrency for EDGAR (free, polite)
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(discover_for_institution, inst, existing_cert_to_ticker): inst
                    for inst in candidates}
        done = 0
        for fut in as_completed(futures):
            try:
                result = fut.result()
                if result:
                    found.append(result)
            except Exception:
                pass
            done += 1
            if done % 100 == 0:
                elapsed = time.time() - t0
                print(f"  {done}/{len(candidates)} ({elapsed:.0f}s, "
                      f"found {len(found)} so far)")

    elapsed = time.time() - t0
    print()
    print(f"✓ Done in {elapsed:.0f}s — discovered {len(found)} new OTC tickers")

    # Merge into bank_map_resolved.json
    for r in found:
        resolved[r["ticker"]] = {
            "cik": r["cik"],
            "fdic_cert": r["fdic_cert"],
            "name": r["name"],
            "fdic_score": r["match_score"],
        }

    resolved_path.write_text(json.dumps(resolved, indent=2, sort_keys=True))
    print(f"Updated {resolved_path}")

    print("\nFirst 25 newly-discovered tickers:")
    for r in sorted(found, key=lambda x: x["ticker"])[:25]:
        print(f"  {r['ticker']:<6} cert={r['fdic_cert']:<6} {r['name'][:50]:<50} (score {r['match_score']})")


if __name__ == "__main__":
    main()
