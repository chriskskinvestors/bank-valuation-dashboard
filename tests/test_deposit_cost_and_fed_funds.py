"""Deposit Trends: cost of deposits is the single-quarter cost of
INTEREST-BEARING deposits, and Fed funds are FRED quarterly means
(REVIEW 2026-10-05 P0-1 / P0-2).

Before: "cost of deposits" was FDIC INTEXPY — YTD-annualized interest expense
over EARNING ASSETS — so the cycle beta subtracted a 9-month figure from a
6-month one and reported ONB's deposit cost RISING +0.39pp into a 1.70pp cut
cycle (beta −0.23). The Fed funds series came from a hand-typed table wrong in
13 of 29 quarters (2026Q1 3.85 vs FRED 3.64).

ONB (cert 3832) as FDIC reports, $K:
  20260331 EDEP 233,330  DEPIDOM 42,681,440  DEPIFOR 0
  20260630 EDEP 470,834  DEPIDOM 43,423,577  DEPIFOR 0
  Q2 cost = (470,834 − 233,330) × 4 / ((43,423,577 + 42,681,440) / 2)
          = 237,504 × 4 / 43,052,508.5 = 2.2067 %
"""
import unittest
from unittest import mock

import pandas as pd

import analysis.deposit_dynamics as D

ONB = [
    {"REPDTE": "20260630", "DEP": 56_985_995, "DEPDOM": 56_985_995,
     "EDEP": 470_834, "DEPIDOM": 43_423_577, "DEPIFOR": 0},
    {"REPDTE": "20260331", "DEP": 56_500_217, "DEPDOM": 56_500_217,
     "EDEP": 233_330, "DEPIDOM": 42_681_440, "DEPIFOR": 0},
]


@mock.patch("analysis.deposit_dynamics._get_fed_funds", new=lambda _d: None)
class TestCostOfIntBearingDeposits(unittest.TestCase):
    def test_single_quarter_hand_value(self):
        df = D.build_deposit_timeline(ONB)
        q2 = df[df["date"] == pd.Timestamp("2026-06-30")]["cost_of_deposits"].iloc[0]
        self.assertAlmostEqual(q2, 237_504 * 4 / ((43_423_577 + 42_681_440) / 2) * 100,
                               places=10)
        self.assertAlmostEqual(round(q2, 3), 2.207)

    def test_first_quarter_without_prior_quarter_end_is_na(self):
        # Q1 interest is as filed, but the average needs the prior quarter-end.
        df = D.build_deposit_timeline(ONB)
        q1 = df[df["date"] == pd.Timestamp("2026-03-31")]["cost_of_deposits"].iloc[0]
        self.assertTrue(pd.isna(q1))

    def test_missing_prior_quarter_is_na_not_ytd(self):
        rows = [dict(ONB[0])]          # Q2 alone: no de-cumulation possible
        df = D.build_deposit_timeline(rows)
        self.assertTrue(pd.isna(df["cost_of_deposits"].iloc[0]))

    def test_foreign_ib_deposits_included_or_na(self):
        # JPM (cert 628, 20260630): DEPIDOM 1,597,995,000 + DEPIFOR 549,733,000.
        jpm = {"DEPIDOM": 1_597_995_000, "DEPIFOR": 549_733_000,
               "DEP": 2_820_284_000, "DEPDOM": 2_226_790_000}
        self.assertEqual(D._interest_bearing_deposits(jpm), 2_147_728_000)
        # A cached row without DEPIFOR: foreign offices exist → n/a, never 0.
        self.assertIsNone(D._interest_bearing_deposits(
            {k: v for k, v in jpm.items() if k != "DEPIFOR"}))
        # Domestic-only bank (DEP == DEPDOM; FDIC null as NaN) → foreign 0.
        lark = {"DEPIDOM": 930_895, "DEPIFOR": float("nan"),
                "DEP": 1_311_438, "DEPDOM": 1_311_438}
        self.assertEqual(D._interest_bearing_deposits(lark), 930_895)


class TestFedFundsFromFred(unittest.TestCase):
    def setUp(self):
        D._FED_FUNDS_LIVE.clear()
        self.addCleanup(D._FED_FUNDS_LIVE.clear)

    def test_no_static_table(self):
        self.assertFalse(hasattr(D, "FED_FUNDS_QUARTERLY"))

    def test_quarter_average_of_monthly_fred(self):
        fred = pd.DataFrame({"date": ["2026-01-01", "2026-02-01", "2026-03-01"],
                             "value": [3.64, 3.64, 3.64]})
        with mock.patch("data.fred_client.fetch_series", lambda *a, **k: fred):
            self.assertEqual(D._get_fed_funds("2026-03-31"), 3.64)   # table said 3.85

    def test_fred_unavailable_is_none(self):
        with mock.patch("data.fred_client.fetch_series", lambda *a, **k: None):
            self.assertIsNone(D._get_fed_funds("2026-03-31"))


class TestUninsuredTableBasis(unittest.TestCase):
    def test_statement_row_uses_insurance_base(self):
        import ui.financials_statements as FS  # noqa: F401
        from pathlib import Path
        src = Path(FS.__file__).read_text(encoding="utf-8")
        self.assertIn('"fratio", "DEPUNINS", "DEPINS+DEPUNINS")', src)
        self.assertNotIn('"fratio", "DEPUNINS", "DEP")', src)


if __name__ == "__main__":
    unittest.main()
