"""REVIEW 2026-10-05 P2: the loan tree didn't sum for foreign-office filers.
JPM 2026-06-30 ($K, FDIC API): foreign-office RE (LNREFOR 3,847,000),
loans to foreign governments (LNFG 6,248,000) and other revolving consumer
credit (LNCONRP 7,669,000) were unshown, so "Other Consumer" was not the
remainder and the categories fell $17.8B short of gross loans.

Identities (verified to the dollar on JPM, ONB, HBAN):
  LNRE   = LNRECONS + LNRERES + LNREMULT + LNRENRES + LNREAG + LNREFOR
  LNCON  = LNAUTO + LNCRCD + LNCONOTH + LNCONRP
  LNLSGR = LNRE + LNCI + LNCON + LNAG + LS + LNOTHER + LNMUNI + LNDEP + LNFG

Run: python -m unittest tests.test_loan_tree_reconciles
"""
import unittest

import pandas as pd

import ui.financials_statements as FS
from tests.test_statement_units_p2 import _render, _row_cells

JPM = {"REPDTE": "2026-06-30", "LNAG": 509000, "LNAUTO": 57822000,
       "LNCI": 241848000, "LNCON": 285078000, "LNCONOTH": 3415000,
       "LNCONRP": 7669000, "LNCRCD": 216172000, "LNDEP": 15651000,
       "LNFG": 6248000, "LNLSGR": 1561577000, "LNMUNI": 29512000,
       "LNOTHER": 476479000, "LNRE": 506147000, "LNREAG": 114000,
       "LNRECONS": 18789000, "LNREFOR": 3847000, "LNRELOC": 13787000,
       "LNREMULT": 107317000, "LNRENRES": 53741000, "LNRENROT": 39378000,
       "LNRENROW": 14363000, "LNRERES": 322339000, "LS": 105000}

RE_LEAVES = ["LNRECONS", "LNRERES", "LNREMULT", "LNRENRES", "LNREAG", "LNREFOR"]
CON_LEAVES = ["LNAUTO", "LNCRCD", "LNCONOTH", "LNCONRP"]
GROSS_PARTS = ["LNRE", "LNCI", "LNCON", "LNAG", "LS", "LNOTHER", "LNMUNI",
               "LNDEP", "LNFG"]


class TestLoanTreeReconciles(unittest.TestCase):
    def test_fixture_identities(self):
        self.assertEqual(sum(JPM[k] for k in RE_LEAVES), JPM["LNRE"])
        self.assertEqual(sum(JPM[k] for k in CON_LEAVES), JPM["LNCON"])
        self.assertEqual(sum(JPM[k] for k in GROSS_PARTS), JPM["LNLSGR"])

    def test_spec_carries_every_leaf(self):
        fields = {r[2] for _, rows in FS._DEPOSIT_LOAN_COMP[:1] for r in rows
                  if len(r) > 2 and r[1] == "dollar"}
        self.assertTrue(set(RE_LEAVES + CON_LEAVES + GROSS_PARTS) <= fields,
                        set(RE_LEAVES + CON_LEAVES + GROSS_PARTS) - fields)

    def test_new_rows_render(self):
        html, _ = _render(FS._DEPOSIT_LOAN_COMP[:1], pd.DataFrame([JPM]), "Quarterly")
        txt = lambda lb: [t for _, t in _row_cells(html, lb)]
        self.assertEqual(txt("Real Estate in Foreign Offices"), ["$3.85B"])
        self.assertEqual(txt("Loans to Foreign Governments"), ["$6.25B"])
        self.assertEqual(txt("of which: Other Revolving Credit"), ["$7.67B"])


if __name__ == "__main__":
    unittest.main()
