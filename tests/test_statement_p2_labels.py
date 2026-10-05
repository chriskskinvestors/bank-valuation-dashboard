"""REVIEW 2026-10-05 statements P2 labels.

* For a multi-charter group the "Noncurrent Ratio … (%, reported)" values
  are data/cert_group's Σnumerator ÷ Σdenominator rebuild — right, but the
  click-through said "as reported".
* Deposit Trends' table carried Gross Loan and CRE growth.

Run: python -m unittest tests.test_statement_p2_labels
"""
import unittest

import pandas as pd

import ui.financials_statements as FS
from tests.test_statement_units_p2 import _render

SPEC = [("Noncurrent Ratio (%, reported)", [("» Total Loans & Leases", "pct", "NCLNLSR")])]


class TestGroupRatioProvenance(unittest.TestCase):
    def test_group_value_says_recomputed(self):
        rec = {"REPDTE": "2026-06-30", "NCLNLSR": 0.8123, "_charter_count": 16,
               "_aggregated": True}
        html, _ = _render(SPEC, pd.DataFrame([rec]), "Quarterly")
        self.assertIn("recomputed for the group", html)
        self.assertIn("16 charters", html)
        self.assertNotIn("(as reported)", html)

    def test_single_charter_still_as_reported(self):
        html, _ = _render(SPEC, pd.DataFrame([{"REPDTE": "2026-06-30", "NCLNLSR": 0.8}]),
                          "Quarterly")
        self.assertIn("(as reported)", html)
        self.assertNotIn("recomputed for the group", html)


class TestDepositTrendsTableIsDepositsOnly(unittest.TestCase):
    def test_no_loan_growth_rows(self):
        rows = [r[0] for _, rs in FS._DEPOSIT_TRENDS_TABLE for r in rs]
        self.assertIn("Deposit Growth", rows)
        self.assertIn("Core Deposit Growth", rows)
        self.assertNotIn("Gross Loan Growth", rows)
        self.assertNotIn("CRE Growth", rows)
        # the composition page keeps them
        comp = [r[0] for _, rs in FS._DEPOSIT_LOAN_COMP for r in rs]
        self.assertIn("Gross Loan Growth", comp)


if __name__ == "__main__":
    unittest.main()
