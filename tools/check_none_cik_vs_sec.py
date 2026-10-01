"""Curated `cik: None` mappings that SEC now lists as registrants.

A curated None in BANK_MAP / bank_map_resolved.json wins over discovery, so a
bank that registers with the SEC after it was mapped keeps rendering as a
non-filer (no filings, no Company Reported, no EPS) forever — PBAM, a 2026
Form 10 registrant (review P1-8). This lists every mapped ticker whose
effective CIK is None while SEC's company_tickers.json has a CIK for it.

A hit is a lead, not a verdict: confirm the SEC registrant IS this bank (name,
SIC, state; tickers get reused) and that its companyfacts are current before
setting the CIK — the §12(i) block in data/bank_mapping.py documents banks
whose old CIK is deliberately withheld because its XBRL is years stale.

Live network; manual only (NOT wired into CI). The offline CI guard is
tests/test_none_cik_guard.py over a checked-in fixture.

    python tools/check_none_cik_vs_sec.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_UA = {"User-Agent": "KSK Investors research chris@kskinvestors.com"}

# None-mapped tickers SEC lists that were checked against SEC submissions
# (2026-09-30) and deliberately stay None. Anything SEC lists that is NOT here
# fails tests/test_none_cik_guard.py until someone triages it.
_FOREIGN = "foreign issuer (ADR) — no FDIC/US-GAAP bank-sub coverage"
_STALE = "stopped periodic SEC reporting (last 10-Q/10-K {}) — old CIK withheld"
_NO_PERIODIC = "no 10-Q/10-K ever filed ({}) — revisit when one is"
REVIEWED_NON_FILERS = {
    "BBD": _FOREIGN, "BBDO": _FOREIGN, "BNS": _FOREIGN, "DB": _FOREIGN,
    "ITUB": _FOREIGN, "SHG": _FOREIGN,
    "BCOW": _STALE.format("2024-11; 15F-12B 2025-03"),
    "BCTF": _STALE.format("FY2024 10-K, 2025-03"),
    "BMBN": _STALE.format("2004"),
    "CIZN": _STALE.format("FY2023 10-K, 2024-03"),
    "CPKF": _STALE.format("2002"),
    "CULL": _STALE.format("2024-05; 15-12G 2024-07"),
    "FBSI": _STALE.format("2012; defunct"),
    "KSBI": _STALE.format("2000"),
    "PTBS": _STALE.format("2012"),
    "STBI": _STALE.format("2004"),
    "UNIB": _STALE.format("2008"),
    "FNFI": _NO_PERIODIC.format("Form D only"),
    "FNFPA": _NO_PERIODIC.format("Form D only; FNFI's second class"),
    "OAKC": _NO_PERIODIC.format("Form D only"),
    "MFDB": _NO_PERIODIC.format("SEC 'MFB Bancorp', S-1 2026-09-14 conversion"),
    # Rechecked 2026-10-01: SEC's CIK 1947463 is ODNB Financial Corp (VA; bank
    # Old Dominion National Bank, FDIC cert 58504; Form D only, never a
    # 10-Q/10-K) — the ACQUIRER of our NACB (National Capital Bancorp, DC;
    # cert 2093, FDIC NAMEHCR "NATIONAL CAPITAL BCORP INC"), which has no SEC
    # CIK. Per S-4/A 2026-09-16, NACB merges into ODNB (close expected Q4-2026),
    # ODNB is renamed National Capital Bancorp and lists as NACB, cert 2093
    # survives. Mapping 1947463 now would show ODNB's filings for NACB; set it
    # only after closing AND its first 10-Q/10-K.
    "NACB": "SEC lists NACB under the pending acquirer's CIK (ODNB Financial "
            "Corp, S-4/425 2026; no 10-Q/10-K) — not our bank until the "
            "merger closes and that CIK files periodically",
    "WCCB": _NO_PERIODIC.format("rechecked 2026-10-01: 10-12B 2026-08-31, "
                                "CERT/8-K 2026-09-15, S-8 2026-09-17; "
                                "first 10-Q expected for Q3-2026"),
}


def mapped_none_cik_tickers() -> set[str]:
    """Tickers whose curated CIK is None, with get_cik's precedence
    (BANK_MAP over the resolved JSON)."""
    from data.bank_mapping import BANK_MAP, _RESOLVED_FROM_JSON
    merged = {**_RESOLVED_FROM_JSON, **BANK_MAP}
    return {t.upper() for t, v in merged.items() if v.get("cik") is None}


def shadowed_registrants(none_tickers, sec_rows) -> list[tuple[str, int, str]]:
    """(ticker, sec_cik, sec_title) for each None-mapped ticker SEC lists.
    sec_rows: the values of company_tickers.json ({cik_str, ticker, title})."""
    none_tickers = {t.upper() for t in none_tickers}
    hits = {}
    for r in sec_rows:
        t = str(r.get("ticker") or "").upper()
        if t in none_tickers and r.get("cik_str"):
            hits[t] = (t, int(r["cik_str"]), str(r.get("title") or ""))
    return sorted(hits.values())


def main() -> int:
    import requests
    resp = requests.get(SEC_TICKERS_URL, headers=_UA, timeout=30)
    resp.raise_for_status()
    rows = list(resp.json().values())
    none_tickers = mapped_none_cik_tickers()
    hits = shadowed_registrants(none_tickers, rows)
    print(f"{len(none_tickers)} tickers mapped cik=None; "
          f"{len(hits)} listed by SEC company_tickers.json:")
    for t, cik, title in hits:
        note = REVIEWED_NON_FILERS.get(t, "UNREVIEWED — triage")
        print(f"  {t:<7} CIK {cik:<10} {title}  [{note}]")
    return 1 if any(t not in REVIEWED_NON_FILERS for t, _, _ in hits) else 0


if __name__ == "__main__":
    sys.exit(main())
