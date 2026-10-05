"""REVIEW 2026-10-05 P1-2: TRN/NTR/NTRTIME/DEPNIDOM are DOMESTIC-office
fields. JPM's $593.5B of foreign-office deposits (21% of DEP) sat unshown
between "Total Nontransaction" and "Total Deposits", and the domestic
numerators were divided by all-office DEP.

JPM (cert 628) 2026-06-30, $K as the FDIC API reports: DEP 2,820,284,000;
DEPDOM 2,226,790,000 (= TRN 532,851,000 + NTR 1,693,939,000 = DEPIDOM
1,597,995,000 + DEPNIDOM 628,795,000); DEPFOR 593,494,000; NTRTIME
296,876,000. Hand-computed:
  non-int / domestic = 628,795,000 / 2,226,790,000 = 28.24 %  (was 22.30 %)
  time / domestic    = 296,876,000 / 2,226,790,000 = 13.33 %  (was 10.53 %)

Run: python -m unittest tests.test_foreign_office_deposits
"""
import unittest
from unittest import mock

import pandas as pd

import ui.financials_statements as FS
from tests.test_statement_units_p2 import _render, _row_cells

JPM = {"REPDTE": "2026-06-30", "DEP": 2_820_284_000, "DEPDOM": 2_226_790_000,
       "TRN": 532_851_000, "NTR": 1_693_939_000, "DDT": 479_865_000,
       "NTRSMMDA": 1_084_237_000, "NTRSOTH": 312_826_000, "NTRTIME": 296_876_000,
       "DEPIDOM": 1_597_995_000, "DEPNIDOM": 628_795_000,
       "COREDEP": 1_986_736_000, "BRO": 54_909_000, "DEPINS": 893_279_000,
       "DEPUNINS": 1_392_514_000, "LNLSNET": 1_535_635_000,
       "ASSET": 4_000_000_000}


def _texts(html, label):
    return [t for _, t in _row_cells(html, label)]


class TestForeignOfficeDeposits(unittest.TestCase):
    def setUp(self):
        self.html, _ = _render(FS._DEPOSIT_TRENDS_TABLE, pd.DataFrame([JPM]), "Quarterly")

    def test_tree_reconciles_on_its_face(self):
        self.assertEqual(_texts(self.html, "» Total Domestic Deposits"), ["$2,226.79B"])
        self.assertEqual(_texts(self.html, "Deposits in Foreign Offices"), ["$593.49B"])
        self.assertEqual(_texts(self.html, "» Total Deposits"), ["$2,820.28B"])

    def test_domestic_numerators_over_domestic_deposits(self):
        self.assertEqual(_texts(self.html, "Non-Interest-Bearing / Domestic Deposits"),
                         ["28.24%"])
        self.assertEqual(_texts(self.html, "Time Deposits / Domestic Deposits"),
                         ["13.33%"])


@mock.patch("analysis.deposit_dynamics._get_fed_funds", new=lambda _d: None)
class TestNonIntMetricDomesticBasis(unittest.TestCase):
    def test_valuation_metric(self):
        from analysis.valuation import compute_all_valuations
        out = compute_all_valuations({}, {}, dict(JPM), [])
        self.assertAlmostEqual(out["nonint_dep_pct"], 628_795_000 / 2_226_790_000 * 100)

    def test_deposit_timeline(self):
        from analysis.deposit_dynamics import build_deposit_timeline
        tl = build_deposit_timeline([dict(JPM, REPDTE="20260630")])
        self.assertAlmostEqual(tl["nonint_dep_pct"].iloc[-1],
                               628_795_000 / 2_226_790_000 * 100)

    def test_missing_domestic_component_is_na_not_all_office(self):
        from analysis.deposit_dynamics import build_deposit_timeline
        row = {k: v for k, v in JPM.items() if k != "DEPIDOM"}
        tl = build_deposit_timeline([dict(row, REPDTE="20260630")])
        self.assertTrue(pd.isna(tl["nonint_dep_pct"].iloc[-1]))


if __name__ == "__main__":
    unittest.main()
