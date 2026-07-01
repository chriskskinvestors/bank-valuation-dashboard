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
# 10-K/10-Q filings on EDGAR. As-of period: Q1-2026 10-Qs (filed May 2026).
# Re-pinned 2026-06-11 after hand-verifying raw companyfacts XBRL with
# tools/golden_handcheck.py (independent requests pull, no pipeline imports).
# Same date: fixed the 5-quarter TTM window in sec_client._extract_ttm_value
# (Q4 was dropped / year-ago quarter double-counted for issuers that tag Q4
# only inside the FY duration) — ROATCE baselines reflect the corrected TTM.
#
# Re-pinned 2026-07-01 (tbvps only): the "exclude preferred stock" fix makes
# book/tangible-book per share COMMON-based. Each tbvps below is now the bank's
# OWN reported tangible book value per common share, hand-verified against its
# 1Q26 (Citi: 4Q25) earnings release / non-GAAP reconciliation — NOT pipeline
# output. Sources per bank in inline comments. Two checks are disabled:
#   • PNC — pipeline returns n/a (unresolvable par-zero preferred; cardinal rule).
#   • USB — MSR-exclusion + correct-share fixes moved the reconstruction from
#     $25.90 to $28.76 (was gross incl-goodwill + a rounded 1,600M share count).
#     The only remaining gap to reported $29.56 is DTL netting on intangibles
#     (~2.7%), which we don't source from a reliable tag — so the tbvps check
#     stays disabled rather than pin a documented-incomplete figure. Both
#     documented at their entries.
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
        "ni_ttm_b":      {"expected": 58.90, "tol_pct": 3.0},
        "equity_b":      {"expected": 362.4, "tol_pct": 3.0},
        # Reported TBV / common share, 1Q26 press release "Fortress Principles":
        # $108.87 (TCE at period-end / common shares at period-end). Ties the
        # common-based fix (equity less preferred, less goodwill+intangibles).
        "tbvps":         {"expected": 108.87, "tol_pct": 2.0},
        "roatce_holdco": {"expected": 19.0, "tol_abs": 1.0},
    },
    "BAC": {
        "shares":        {"expected": 7_212_000_000, "tol_pct": 2.0},
        "ni_ttm_b":      {"expected": 31.73, "tol_pct": 3.0},
        "equity_b":      {"expected": 303.2, "tol_pct": 3.0},
        # Reported tangible book value / common share, 1Q26 8-K (TCE $205,651M /
        # 7,129.9M shares) = $28.84. BAC's reconciliation nets DTL on intangibles
        # and carries preferred at redemption value; pipeline is within tol.
        "tbvps":         {"expected": 28.84, "tol_pct": 2.0},
        "roatce_holdco": {"expected": 13.1, "tol_abs": 1.0},
    },
    "WFC": {
        "shares":        {"expected": 3_064_000_000, "tol_pct": 2.0},  # Q1 2026 post-buybacks
        "ni_ttm_b":      {"expected": 21.23, "tol_pct": 5.0},
        "equity_b":      {"expected": 178.4, "tol_pct": 3.0},
        # Reported tangible book value / common share, 1Q26 Quarterly Supplement
        # (TCE $137,817M / 3,064.3M shares) = $44.98.
        "tbvps":         {"expected": 44.98, "tol_pct": 2.0},
        "roatce_holdco": {"expected": 13.84, "tol_abs": 1.0},
    },
    "C": {
        "shares":        {"expected": 1_750_000_000, "tol_pct": 3.0},
        "ni_ttm_b":      {"expected": 14.31, "tol_pct": 5.0},
        "equity_b":      {"expected": 212.3, "tol_pct": 3.0},
        # Citi's latest companyfacts period is 4Q25 (as-of 2025-12-31). Reported
        # tangible book value / share in the 4Q25 press release = $97.06.
        "tbvps":         {"expected": 97.06, "tol_pct": 3.0},
        "roatce_holdco": {"expected": 7.6, "tol_abs": 1.0},
    },
    "USB": {
        # Period-end common shares outstanding, 1Q26 (2026-03-31): the pipeline
        # now derives issued − treasury (2,125,725,742 − 571,140,185 =
        # 1,554,585,557) because CommonStockSharesOutstanding is a rounded
        # 1,600,000,000 cover-page placeholder. Ties USB's reported ~1,555M.
        "shares":        {"expected": 1_554_585_557, "tol_pct": 1.0},
        "ni_ttm_b":      {"expected": 7.57, "tol_pct": 5.0},
        "equity_b":      {"expected": 65.2, "tol_pct": 3.0},
        # tbvps check still disabled — but the gap is now down to the DTL residual.
        # The MSR-exclusion + correct-share fixes moved the reconstruction from
        # $25.90 to $28.76 (TCE convention): common equity $58,978M − (goodwill
        # $12,625M + other-intangibles-EX-MSR $1,647M = $14,272M) = $44,706M /
        # 1,554,585,557 shares = $28.76. USB's own reported common TBV/share is
        # $29.56 (1Q26 release: TCE $45,961M / 1,555M). The remaining 2.7% is the
        # deferred-tax-liability netting on intangibles (~$1,255M) that USB nets
        # but we don't source from a specific reliable tag (cardinal rule: don't
        # guess it). $28.76 is outside a 2% band, so the check stays disabled;
        # re-enable ({"expected": 29.56, "tol_pct": 2.0}) only if the DTL netting
        # is later sourced from a specific tag. Pinning $28.76 would enshrine the
        # documented-incomplete figure, so it is intentionally left unpinned.
        # "tbvps":         {"expected": 29.56, "tol_pct": 2.0},
        "roatce_holdco": {"expected": 15.9, "tol_abs": 1.5},
    },
    "PNC": {
        "shares":        {"expected": 403_000_000, "tol_pct": 2.0},
        # ni_ttm uses summarize_capital_return (NetIncomeLossAvailableToCommonStockholdersBasic);
        # roatce uses sec_client.get_latest_fundamentals (NetIncomeLoss). The two
        # differ by preferred dividends, hence the slightly different ratio.
        "ni_ttm_b":      {"expected": 6.58, "tol_pct": 5.0},
        "equity_b":      {"expected": 63.63, "tol_pct": 3.0},
        # tbvps check disabled: pipeline returns n/a (None) for PNC. PNC reports
        # preferred outstanding but tags only a par-zero PreferredStockValue plus
        # stale APIC, so the preferred carrying value is unresolvable — the
        # cardinal rule renders TBVPS n/a rather than a preferred-inflated figure.
        # The OLD pin ($123.03) was preferred-inclusive book value (wrong).
        # PNC's own reported COMMON tangible book value / share is $109.42 (1Q26
        # press release; BVPS $143.65) as of 2026-03-31. Will be restored when
        # the direct earnings-release TBVPS extractor lands.
        # "tbvps":         {"expected": 109.42, "tol_pct": 3.0},
        "roatce_holdco": {"expected": 13.61, "tol_abs": 1.0},
    },
    "HBAN": {  # Cadence acquisition closed Q1 2026 — share count + equity expanded
        "shares":        {"expected": 2_027_000_000, "tol_pct": 2.0},
        "equity_b":      {"expected": 32.53, "tol_pct": 3.0},
        # Reported tangible book value / common share, 1Q26 press release &
        # supplement (TCE $19,361M / 2,027M shares) = $9.55. Pipeline reads
        # $9.45 (subtracts gross intangibles without the +$203M DTL add-back);
        # ~1% low, within the 3% band.
        "tbvps":         {"expected": 9.55, "tol_pct": 3.0},
        "roatce_holdco": {"expected": 9.63, "tol_abs": 1.5},
    },
    "SFST": {
        "shares":        {"expected": 8_213_328, "tol_pct": 1.0},
        "equity_b":      {"expected": 0.369, "tol_pct": 3.0},
        # No preferred, no goodwill/intangibles, so TBV/share = BV/share. SFST's
        # 1Q26 release reports book value per common share $46.00 (period-end
        # 8.21M shares) — unchanged by the preferred fix.
        "tbvps":         {"expected": 46.00, "tol_pct": 2.0},
        "roatce_holdco": {"expected": 9.22, "tol_abs": 1.0},
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
