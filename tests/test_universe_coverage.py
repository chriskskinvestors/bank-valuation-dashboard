"""
Coverage regression test — every universe ticker MUST resolve to a
working data source (SEC CIK with XBRL or FDIC cert that returns
financials). Anything else is a silent gap in the dashboard.

This script returns non-zero exit if ANY ticker fails. Wired into the
GitHub Actions deploy workflow (and the nightly refresh) so a regression
prevents the bad code from going live AND alerts us via the failed job.

Run locally:   python tests/test_universe_coverage.py
"""

from __future__ import annotations
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

UA = {"User-Agent": "BankValuationDashboard test@kskinvestors.com"}


def _has_sec_xbrl(cik: int) -> bool:
    if not cik:
        return False
    try:
        r = requests.get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json",
            headers=UA, timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def _has_fdic_data(cert: int) -> bool:
    if not cert:
        return False
    try:
        r = requests.get(
            "https://banks.data.fdic.gov/api/financials",
            params={"filters": f"CERT:{cert}", "fields": "CERT,REPDTE", "limit": 1},
            headers=UA, timeout=10,
        )
        return r.status_code == 200 and bool(r.json().get("data"))
    except Exception:
        return False


def _check_ticker(ticker: str) -> tuple[str, bool, int | None, int | None]:
    from data.bank_mapping import get_cik, get_fdic_cert
    cik = get_cik(ticker)
    cert = get_fdic_cert(ticker)
    has_sec = _has_sec_xbrl(cik) if cik else False
    has_fdic = _has_fdic_data(cert) if cert else False
    ok = has_sec or has_fdic
    return ticker, ok, cik, cert


def main() -> int:
    import warnings; warnings.filterwarnings("ignore")
    from data.bank_universe import get_universe_tickers
    from config import DEFAULT_WATCHLIST

    tickers = sorted(set(get_universe_tickers()) | set(DEFAULT_WATCHLIST))
    print(f"Checking {len(tickers)} tickers for data-source coverage...")

    failures: list[tuple[str, int | None, int | None]] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_check_ticker, t): t for t in tickers}
        done = 0
        for fut in as_completed(futures):
            ticker, ok, cik, cert = fut.result()
            if not ok:
                failures.append((ticker, cik, cert))
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(tickers)}")

    print()
    print(f"Total: {len(tickers)}")
    print(f"Passed: {len(tickers) - len(failures)}")
    print(f"Failed: {len(failures)}")

    if failures:
        print("\nFAILURES (no working data source):")
        for ticker, cik, cert in sorted(failures):
            print(f"  {ticker:<6} cik={cik} cert={cert}")
        return 1
    print("\n✓ Every universe ticker resolves to at least one data source.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
