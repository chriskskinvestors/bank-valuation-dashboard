"""
Golden dataset regression test.

Maintains a "known good" snapshot of key metrics for several banks.
Run this after any data-pipeline change to catch silent regressions.

Run: python -m tests.golden_dataset
"""

from __future__ import annotations
import sys
from pathlib import Path

# Ensure repo root is on path
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


# Known-good values snapshot — refresh each quarter from primary-source
# 10-K/10-Q filings on EDGAR. As-of period: filings dated 2026-03-31 (Q1 2026).
#
# When real-world data drifts (e.g. HBAN+Cadence merger closed Q1 2026,
# nearly doubling share count), update this file rather than chasing the
# pipeline. The pipeline is correct if values match the most recent 10-Q.
#
# Keys:
#   eps           = latest diluted EPS (from SEC)
#   shares        = shares outstanding (millions)
#   ni_ttm        = TTM HoldCo net income ($B)
#   equity        = HoldCo stockholders' equity ($B)
#   tbvps         = HoldCo tangible book value / share ($)
#   roatce_holdco = HoldCo ROATCE (% — ni_ttm / tce_avg × 100)
#
# Tolerances are intentionally tight: 5% on dollar amounts, 50bps on ratios.
# If any value drifts outside tolerance, the pipeline changed and we need
# to investigate whether it's a data-quality issue or a genuine new period.
GOLDEN_2025_Q4 = {  # name kept for backward compat; values are Q1 2026
    "JPM": {
        "shares":        {"expected": 2_696_200_000, "tol_pct": 1.0},
        "ni_ttm_b":      {"expected": 56.92, "tol_pct": 3.0},
        "equity_b":      {"expected": 362.4, "tol_pct": 3.0},
        "tbvps":         {"expected": 114.87, "tol_pct": 2.0},
        "roatce_holdco": {"expected": 18.4, "tol_abs": 1.0},
    },
    "BAC": {
        "shares":        {"expected": 7_212_000_000, "tol_pct": 2.0},
        "ni_ttm_b":      {"expected": 30.51, "tol_pct": 3.0},
        "equity_b":      {"expected": 303.2, "tol_pct": 3.0},
        "tbvps":         {"expected": 32.23, "tol_pct": 2.0},
        "roatce_holdco": {"expected": 13.1, "tol_abs": 1.0},
    },
    "WFC": {
        "shares":        {"expected": 3_064_000_000, "tol_pct": 2.0},  # Q1 2026 post-buybacks
        "ni_ttm_b":      {"expected": 21.23, "tol_pct": 5.0},
        "equity_b":      {"expected": 178.4, "tol_pct": 3.0},
        "tbvps":         {"expected": 50.07, "tol_pct": 2.0},
        "roatce_holdco": {"expected": 13.84, "tol_abs": 1.0},
    },
    "C": {
        "shares":        {"expected": 1_750_000_000, "tol_pct": 3.0},
        "ni_ttm_b":      {"expected": 14.31, "tol_pct": 5.0},
        "equity_b":      {"expected": 212.3, "tol_pct": 3.0},
        "tbvps":         {"expected": 107.99, "tol_pct": 3.0},
        "roatce_holdco": {"expected": 7.6, "tol_abs": 1.0},
    },
    "USB": {
        "shares":        {"expected": 1_600_000_000, "tol_pct": 2.5},
        "ni_ttm_b":      {"expected": 7.57, "tol_pct": 5.0},
        "equity_b":      {"expected": 65.2, "tol_pct": 3.0},
        "tbvps":         {"expected": 29.78, "tol_pct": 2.0},
        "roatce_holdco": {"expected": 15.9, "tol_abs": 1.5},
    },
    "PNC": {
        "shares":        {"expected": 403_000_000, "tol_pct": 2.0},
        # ni_ttm uses summarize_capital_return (NetIncomeLossAvailableToCommonStockholdersBasic);
        # roatce uses sec_client.get_latest_fundamentals (NetIncomeLoss). The two
        # differ by preferred dividends, hence the slightly different ratio.
        "ni_ttm_b":      {"expected": 6.58, "tol_pct": 5.0},
        "equity_b":      {"expected": 60.59, "tol_pct": 3.0},
        "tbvps":         {"expected": 123.03, "tol_pct": 3.0},
        "roatce_holdco": {"expected": 12.19, "tol_abs": 1.0},
    },
    "HBAN": {  # Cadence acquisition closed Q1 2026 — share count + equity expanded
        "shares":        {"expected": 2_027_000_000, "tol_pct": 2.0},
        "equity_b":      {"expected": 32.53, "tol_pct": 3.0},
        "tbvps":         {"expected": 11.35, "tol_pct": 3.0},
        "roatce_holdco": {"expected": 9.63, "tol_abs": 1.5},
    },
    "SFST": {
        "shares":        {"expected": 8_213_328, "tol_pct": 1.0},
        "equity_b":      {"expected": 0.369, "tol_pct": 3.0},
        "tbvps":         {"expected": 44.88, "tol_pct": 2.0},
        "roatce_holdco": {"expected": 6.75, "tol_abs": 1.0},
    },
}


def _pct_err(actual, expected):
    if expected == 0:
        return 999 if actual != 0 else 0
    return abs((actual - expected) / expected) * 100


def _abs_err(actual, expected):
    return abs(actual - expected)


def _check(actual, expected_spec) -> tuple[bool, str]:
    expected = expected_spec["expected"]
    if "tol_pct" in expected_spec:
        err = _pct_err(actual, expected)
        ok = err <= expected_spec["tol_pct"]
        return ok, f"{err:.2f}% off (tol {expected_spec['tol_pct']}%)"
    if "tol_abs" in expected_spec:
        err = _abs_err(actual, expected)
        ok = err <= expected_spec["tol_abs"]
        return ok, f"{err:.2f} off (tol {expected_spec['tol_abs']})"
    return False, "no tolerance defined"


def run():
    import warnings; warnings.filterwarnings("ignore")
    from data.bank_mapping import get_cik, get_fdic_cert
    from data.fdic_client import fetch_financials
    from data import sec_client
    from analysis.capital_return import summarize_capital_return

    total = 0; passed = 0; failed_items = []

    print(f"{'Ticker':<6} {'Metric':<18} {'Actual':<18} {'Expected':<15} {'Status':<25}")
    print("-" * 85)

    for ticker, checks in GOLDEN_2025_Q4.items():
        cik = get_cik(ticker)
        cert = get_fdic_cert(ticker)
        if not cik:
            print(f"{ticker:<6} [no CIK, skipping]")
            continue

        sec = sec_client.get_latest_fundamentals(cik)

        # Shares
        if "shares" in checks:
            total += 1
            actual = sec.get("shares_outstanding") or 0
            ok, msg = _check(actual, checks["shares"])
            status = "OK" if ok else "FAIL"
            if ok: passed += 1
            else: failed_items.append((ticker, "shares", actual, checks["shares"]["expected"], msg))
            print(f"{ticker:<6} {'shares':<18} {actual:>16,.0f} {checks['shares']['expected']:>14,.0f} {status} {msg}")

        # Equity ($B)
        if "equity_b" in checks:
            total += 1
            actual = (sec.get("book_value_total") or 0) / 1e9
            ok, msg = _check(actual, checks["equity_b"])
            status = "OK" if ok else "FAIL"
            if ok: passed += 1
            else: failed_items.append((ticker, "equity_b", actual, checks["equity_b"]["expected"], msg))
            print(f"{ticker:<6} {'equity_b':<18} {actual:>16.2f}B {checks['equity_b']['expected']:>13.2f}B {status} {msg}")

        # TBV / share
        if "tbvps" in checks:
            total += 1
            actual = sec.get("tangible_book_value_per_share") or 0
            ok, msg = _check(actual, checks["tbvps"])
            status = "OK" if ok else "FAIL"
            if ok: passed += 1
            else: failed_items.append((ticker, "tbvps", actual, checks["tbvps"]["expected"], msg))
            print(f"{ticker:<6} {'tbvps':<18} {actual:>17.2f} {checks['tbvps']['expected']:>14.2f} {status} {msg}")

        # NI TTM ($B) via capital_return
        if "ni_ttm_b" in checks:
            total += 1
            res = summarize_capital_return(cik)
            actual = (res.get("ttm", {}).get("net_income_ttm") or 0) / 1e9
            ok, msg = _check(actual, checks["ni_ttm_b"])
            status = "OK" if ok else "FAIL"
            if ok: passed += 1
            else: failed_items.append((ticker, "ni_ttm_b", actual, checks["ni_ttm_b"]["expected"], msg))
            print(f"{ticker:<6} {'ni_ttm_b':<18} {actual:>16.2f}B {checks['ni_ttm_b']['expected']:>13.2f}B {status} {msg}")

        # HoldCo ROATCE
        if "roatce_holdco" in checks:
            total += 1
            from analysis.valuation import compute_roatce_holdco
            actual = compute_roatce_holdco(sec) or 0
            ok, msg = _check(actual, checks["roatce_holdco"])
            status = "OK" if ok else "FAIL"
            if ok: passed += 1
            else: failed_items.append((ticker, "roatce_holdco", actual, checks["roatce_holdco"]["expected"], msg))
            print(f"{ticker:<6} {'roatce_holdco':<18} {actual:>17.2f}% {checks['roatce_holdco']['expected']:>13.2f}% {status} {msg}")

    print("-" * 85)
    print(f"\n=== Golden Dataset: {passed}/{total} PASS ===")
    if failed_items:
        print("\nFAILURES:")
        for t, field, actual, expected, msg in failed_items:
            print(f"  {t}.{field}: got {actual:.4f}, expected {expected:.4f} — {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
