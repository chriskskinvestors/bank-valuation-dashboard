"""(2026-08-18) PNC's Capital Dynamics showed TTM net income = $0.0 — a
plausible-wrong zero, caught by the golden dataset's ni_ttm_b check.

Chain of failure, hand-verified against raw companyfacts (CIK 713676):
1. PNC's Q2-2026 10-Q (accn 0001628280-26-053170, new filing agent)
   re-tagged us-gaap:NetIncomeLoss after a ~12-year gap — but only as two
   6-month YTD rows (2026-06-30 and comparative 2025-06-30). The concept
   ended up with 8 usable rows total (2009/2013/2014 + these).
2. _extract_series' staleness guard checks only the LATEST end date, so
   the fresh rows let this sparse series beat the complete
   NetIncomeLossAvailableToCommonStockholdersBasic fallback (72 rows).
3. _derive_quarterly_from_ytd can't decompose a 6M YTD row without a Q1
   sibling → every recent quarter's net_income_q = NaN.
4. pandas .sum() over an all-NaN window returns 0.0, not None → the UI
   showed $0 TTM net income for a bank that earned $7.3B.

Fix pinned here at both layers:
- _extract_series(min_recent_ends=N) skips fresh-but-sparse series in
  favor of a complete fallback, but still uses the sparse one when NO
  concept has quarterly coverage (young filers keep their data).
- compute_ttm_capital_return returns None (→ n/a) for an all-NaN window.

Run: python -m unittest tests.test_capital_return_pnc_regression
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.capital_return import (  # noqa: E402
    _NET_INCOME_CONCEPTS,
    _extract_series,
    compute_shareholder_yield,
    compute_ttm_capital_return,
)


def _d(days_ago: int) -> str:
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _rows(ends_days_ago, val=100, form="10-Q"):
    return [{"end": _d(d), "filed": _d(max(d - 40, 1)), "val": val,
             "form": form, "fp": "Q2", "fy": 2026, "start": _d(d + 90)}
            for d in ends_days_ago]


def _gaap(concept_rows: dict) -> dict:
    return {c: {"units": {"USD": rows}} for c, rows in concept_rows.items()}


# Quarterly-ish spacing: 8 distinct ends inside the 2-year window.
COMPLETE = [50, 140, 230, 320, 410, 500, 590, 680]
# PNC's re-tagged NetIncomeLoss: two fresh 6M-YTD ends + ancient history.
SPARSE_FRESH = [50, 415]
ANCIENT = [4400, 4500]


class TestSparseConceptFallback(unittest.TestCase):
    def test_sparse_but_fresh_series_loses_to_complete_fallback(self):
        """The exact PNC shape: NetIncomeLoss fresh-but-sparse must NOT win."""
        gaap = _gaap({
            "NetIncomeLoss": _rows(SPARSE_FRESH + ANCIENT, val=1),
            "NetIncomeLossAvailableToCommonStockholdersBasic":
                _rows(COMPLETE, val=2),
        })
        out = _extract_series(gaap, _NET_INCOME_CONCEPTS, min_recent_ends=4)
        self.assertTrue(out)
        self.assertEqual({e["val"] for e in out}, {2},
                         "sparse fresh NetIncomeLoss beat the complete fallback")
        self.assertEqual(len(out), len(COMPLETE))

    def test_young_filer_sparse_only_series_is_still_served(self):
        """When NO concept clears the bar, fall back to the fresh sparse one —
        a 2-quarter-old filer must not lose its data to the guard."""
        gaap = _gaap({"NetIncomeLoss": _rows([50, 140], val=7)})
        out = _extract_series(gaap, _NET_INCOME_CONCEPTS, min_recent_ends=4)
        self.assertEqual({e["val"] for e in out}, {7})
        self.assertEqual(len(out), 2)

    def test_stale_series_still_skipped_entirely(self):
        """The original staleness guard is intact: an old-only concept falls
        through even if a later fallback is also sparse."""
        gaap = _gaap({
            "NetIncomeLoss": _rows(ANCIENT, val=1),
            "ProfitLoss": _rows([50], val=3),
        })
        out = _extract_series(gaap, _NET_INCOME_CONCEPTS, min_recent_ends=4)
        self.assertEqual({e["val"] for e in out}, {3})


class TestAllNanTtmIsNone(unittest.TestCase):
    def _timeline(self, ni_values):
        return pd.DataFrame({
            "end": [_d(d) for d in (320, 230, 140, 50)],
            "net_income_q": ni_values,
            "dividends_q": [100.0, 100.0, 100.0, 100.0],
        })

    def test_all_nan_window_returns_none_not_zero(self):
        ttm = compute_ttm_capital_return(
            self._timeline([float("nan")] * 4))
        self.assertIsNone(
            ttm["net_income_ttm"],
            "all-NaN TTM window summed to a plausible-wrong 0.0 — must be "
            "None so the UI renders n/a (cardinal rule)")
        self.assertIsNone(ttm["payout_ratio_ttm"])

    def test_partial_window_still_sums(self):
        ttm = compute_ttm_capital_return(
            self._timeline([float("nan"), 10.0, 20.0, 30.0]))
        self.assertEqual(ttm["net_income_ttm"], 60.0)
        self.assertEqual(ttm["dividends_ttm"], 400.0)

    def test_true_zero_stays_zero(self):
        """A genuinely observed zero (all four quarters reported 0) is data,
        not absence — it must survive as 0.0."""
        ttm = compute_ttm_capital_return(self._timeline([0.0, 0.0, 0.0, 0.0]))
        self.assertEqual(ttm["net_income_ttm"], 0.0)


class TestUnobservedCapitalReturnIsNone(unittest.TestCase):
    """PNC's second face of the same bug (seen live 2026-08-18): dividend and
    buyback dollars both UNOBSERVED still produced 'Total Return Ratio 0% of
    TTM net income' and 'Shareholder Yield 0.00%' — total = (None or 0) +
    (None or 0). Both-unknown must be None → n/a; one known side still treats
    the other as 0 (banks that never buy back)."""

    def _timeline(self, divs, bbs):
        return pd.DataFrame({
            "end": [_d(d) for d in (320, 230, 140, 50)],
            "net_income_q": [10.0, 20.0, 30.0, 40.0],
            "dividends_q": divs,
            "buybacks_q": bbs,
        })

    NAN4 = [float("nan")] * 4

    def test_both_unobserved_total_and_ratio_are_none(self):
        ttm = compute_ttm_capital_return(self._timeline(self.NAN4, self.NAN4))
        self.assertIsNone(ttm["total_returned_ttm"])
        self.assertIsNone(ttm["total_return_ratio_ttm"])

    def test_both_unobserved_shareholder_yield_is_none(self):
        y = compute_shareholder_yield(
            self._timeline(self.NAN4, self.NAN4), market_cap=1_000_000.0)
        self.assertIsNone(y["total_shareholder_yield_pct"],
                          "unknown capital return rendered as a 0.00% yield")
        self.assertIsNone(y["dividend_yield_pct"])
        self.assertIsNone(y["buyback_yield_pct"])

    def test_one_known_side_still_totals_with_zero_for_the_other(self):
        ttm = compute_ttm_capital_return(
            self._timeline([5.0, 5.0, 5.0, 5.0], self.NAN4))
        self.assertEqual(ttm["total_returned_ttm"], 20.0)
        self.assertEqual(ttm["total_return_ratio_ttm"], 0.2)
        y = compute_shareholder_yield(
            self._timeline([5.0, 5.0, 5.0, 5.0], self.NAN4),
            market_cap=1000.0)
        self.assertEqual(y["total_shareholder_yield_pct"], 2.0)


if __name__ == "__main__":
    unittest.main()
