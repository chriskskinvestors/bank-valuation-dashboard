"""
Unit tests for data.sec_period.fundamentals_for_period — span/period-aware
extraction of AS-REPORTED HoldCo actuals for one exact period from SEC
companyfacts.

The defining hazard: income-statement values are cumulative (YTD), so the entry
ending 2026-06-30 is H1, not Q2. The standalone quarter is YTD(q) − YTD(q−1);
Q4 in particular is only ever FY − 9M. Mis-picking the YTD is the +284%-"beat"
bug. Every expected value hand-computed.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.sec_client as sec_client  # noqa: E402
import data.sec_period as sp  # noqa: E402


def _e(start, end, val, *, form="10-Q", filed="2026-08-01"):
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed}


def _facts(concepts: dict) -> dict:
    """concepts: {concept_name: (unit_type, [entries])}."""
    us = {name: {"units": {ut: entries}} for name, (ut, entries) in concepts.items()}
    return {"facts": {"us-gaap": us}}


class TestFundamentalsForPeriod(unittest.TestCase):

    def setUp(self):
        self._orig = sec_client.fetch_company_facts
        self._facts = {}
        sec_client.fetch_company_facts = lambda cik: self._facts

    def tearDown(self):
        sec_client.fetch_company_facts = self._orig

    def test_quarter_by_ytd_differencing(self):
        # Only cumulative (YTD) legs tagged — Q2 = H1 − Q1, NOT the H1 value.
        self._facts = _facts({
            "NetIncomeLoss": ("USD", [
                _e("2026-01-01", "2026-03-31", 100e6),   # Q1 YTD
                _e("2026-01-01", "2026-06-30", 250e6),   # H1 YTD
            ]),
            "EarningsPerShareDiluted": ("USD/shares", [
                _e("2026-01-01", "2026-03-31", 2.00),
                _e("2026-01-01", "2026-06-30", 5.10),
            ]),
            "Assets": ("USD", [_e("2026-01-01", "2026-06-30", 12.5e9)]),
        })
        a = sp.fundamentals_for_period(123, "2026Q2")
        self.assertEqual(a["net_income"], 150e6)          # 250 − 100
        self.assertAlmostEqual(a["eps"], 3.10)            # 5.10 − 2.00
        self.assertEqual(a["total_assets"], 12.5e9)       # instant at 6/30

    def test_q4_is_fy_minus_9m(self):
        # Companies never tag a standalone Q4 — it must be FY − 9M.
        self._facts = _facts({
            "NetIncomeLoss": ("USD", [
                _e("2025-01-01", "2025-09-30", 360e6),                      # 9M YTD
                _e("2025-01-01", "2025-12-31", 480e6, form="10-K", filed="2026-02-15"),  # FY
            ]),
        })
        a = sp.fundamentals_for_period(123, "2025Q4")
        self.assertEqual(a["net_income"], 120e6)          # 480 − 360

    def test_standalone_quarter_eps_preferred(self):
        # When the company DOES tag a standalone quarter EPS, use that exact value.
        self._facts = _facts({
            "EarningsPerShareDiluted": ("USD/shares", [
                _e("2026-04-01", "2026-06-30", 6.12),     # standalone Q2 (authoritative)
                _e("2026-01-01", "2026-03-31", 4.44),     # Q1 YTD
                _e("2026-01-01", "2026-06-30", 10.55),    # H1 YTD (diff would give 6.11)
            ]),
        })
        a = sp.fundamentals_for_period(123, "2026Q2")
        self.assertEqual(a["eps"], 6.12)                  # direct tag, not 6.11

    def test_flow_standalone_fallback_when_no_ytd(self):
        # Some filers tag only the standalone quarter (no usable YTD legs).
        self._facts = _facts({
            "NetIncomeLoss": ("USD", [_e("2026-04-01", "2026-06-30", 150e6)]),
        })
        a = sp.fundamentals_for_period(123, "2026Q2")
        self.assertEqual(a["net_income"], 150e6)

    def test_annual_full_year(self):
        self._facts = _facts({
            "NetIncomeLoss": ("USD", [
                _e("2025-01-01", "2025-12-31", 480e6, form="10-K", filed="2026-02-15")]),
            "Assets": ("USD", [_e("2025-01-01", "2025-12-31", 11.8e9, form="10-K")]),
        })
        a = sp.fundamentals_for_period(123, "2025")
        self.assertEqual(a["net_income"], 480e6)
        self.assertEqual(a["total_assets"], 11.8e9)

    def test_nii_derived_from_income_minus_expense(self):
        self._facts = _facts({
            "InterestAndDividendIncomeOperating": ("USD", [
                _e("2026-01-01", "2026-03-31", 200e6),
                _e("2026-01-01", "2026-06-30", 420e6)]),
            "InterestExpense": ("USD", [
                _e("2026-01-01", "2026-03-31", 95e6),
                _e("2026-01-01", "2026-06-30", 200e6)]),
        })
        a = sp.fundamentals_for_period(123, "2026Q2")
        # II_q = 420−200 = 220 ; IE_q = 200−95 = 105 ; NII = 115
        self.assertEqual(a["net_interest_income"], 115e6)

    def test_direct_nii_preferred(self):
        self._facts = _facts({
            "InterestIncomeExpenseNet": ("USD", [
                _e("2026-01-01", "2026-03-31", 100e6),
                _e("2026-01-01", "2026-06-30", 215e6)]),
            "InterestAndDividendIncomeOperating": ("USD", [_e("2026-04-01", "2026-06-30", 420e6)]),
            "InterestExpense": ("USD", [_e("2026-04-01", "2026-06-30", 200e6)]),
        })
        a = sp.fundamentals_for_period(123, "2026Q2")
        self.assertEqual(a["net_interest_income"], 115e6)   # 215 − 100, direct tag

    def test_amended_most_recently_filed_wins(self):
        self._facts = _facts({
            "NetIncomeLoss": ("USD", [
                _e("2026-01-01", "2026-06-30", 250e6, filed="2026-08-01"),
                _e("2026-01-01", "2026-06-30", 248e6, filed="2026-11-01"),   # restatement
                _e("2026-01-01", "2026-03-31", 100e6, filed="2026-05-01"),
            ]),
        })
        a = sp.fundamentals_for_period(123, "2026Q2")
        self.assertEqual(a["net_income"], 148e6)            # 248 − 100

    def test_unfiled_period_is_none(self):
        self._facts = _facts({
            "NetIncomeLoss": ("USD", [_e("2026-01-01", "2026-06-30", 250e6)]),
        })
        self.assertIsNone(sp.fundamentals_for_period(123, "2026Q4"))   # no 9M/FY legs

    def test_no_cik_or_garbage_period_is_none(self):
        self._facts = _facts({"NetIncomeLoss": ("USD", [_e("2026-01-01", "2026-06-30", 1)])})
        self.assertIsNone(sp.fundamentals_for_period(None, "2026Q2"))
        self.assertIsNone(sp.fundamentals_for_period(123, "H1 2026"))
        self.assertIsNone(sp.fundamentals_for_period(123, ""))

    def test_concepts_registered_in_slim_cache(self):
        # Every concept the extractor reads must be in the slim projection, or the
        # cached facts wouldn't contain it (the silent-n/a trap the slim hash guards).
        needed = set()
        for chain in (sp._NET_INCOME, sp._NONINT_INCOME, sp._NONINT_EXPENSE,
                      sp._PROVISION, sp._EPS, sp._NII_DIRECT, sp._INT_INCOME,
                      sp._INT_EXPENSE, *sp._INSTANT.values()):
            needed.update(chain)
        missing = needed - sec_client.SLIM_USGAAP_CONCEPTS
        self.assertEqual(missing, set(), f"concepts missing from SLIM: {missing}")


if __name__ == "__main__":
    unittest.main()
