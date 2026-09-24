"""
(P0-5, 2026-09-24) build_capital_timeline must not fabricate net income or
loans from an absent FDIC field.

The bug: three lines under the EQTOT guard still read

    net_income = r.get("NETINC") or 0
    total_loans = r.get("LNLSNET") or 0

so a quarter whose YTD NETINC was absent got a quarterly NI of 0 (Q1) or
0 − prior YTD (Q2–Q4: a large NEGATIVE "quarterly NI"), and the quarter after
it got YTD − 0 (a 6-/9-month figure labeled one quarter). capital_returned_k,
retention_ratio, the high_payout alert, payout_ratio_4q, the buyback-capacity
explainer and the Capital Generation waterfall all computed from that number.
An absent LNLSNET became a −100% / +inf loan-growth pair on adjacent rows.

Fix: NaN at the boundary; _compute_quarterly_ni treats a NaN prior YTD as
unknown (YTD(Q3) − YTD(Q2 missing) is not derivable); the buyback-capacity
helpers return None on any NaN term.

Pins (pure, no network) — every value hand-computed in the fixtures below.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.capital_dynamics import (  # noqa: E402
    build_capital_timeline,
    compute_buyback_capacity,
    compute_capital_screening_metrics,
    compute_organic_capital_need,
    detect_capital_alerts,
    summarize_bank_capital,
)


def _rec(repdte, eqtot, netinc, loans, cet1=12.0):
    return {"REPDTE": repdte, "EQTOT": eqtot, "INTANGW": 100_000,
            "INTAN": 150_000, "NETINC": netinc, "LNLSNET": loans,
            "IDT1CER": cet1, "RBCRWAJ": 14.0, "RBCT1JR": 9.0}


# Equity steps +50,000 every quarter → equity_qoq_k = 50,000 on rows 2..5.
# YTD NETINC: Q1 100k · Q2 ABSENT · Q3 300k · Q4 400k · Q1'26 100k.
#   quarterly NI: Q1 = 100,000 · Q2 n/a · Q3 n/a (300k − unknown) ·
#                 Q4 = 400,000 − 300,000 = 100,000 · Q1'26 = 100,000
#   capital_returned = NI − ΔEquity: Q4 = Q1'26 = 100,000 − 50,000 = 50,000
#   retention = 1 − 50,000 / 100,000 = 0.5 on Q4 and Q1'26; n/a elsewhere.
# Before the fix: Q2 NI = 0 − 100,000 = −100,000 → returned −150,000 →
#   retention −0.5; Q3 NI = 300,000 − 0 → returned 250,000 → retention 0.1667;
#   tail-4 mean = (−0.5 + 0.1667 + 0.5 + 0.5) / 4 = 0.1667 → "payout 83%" →
#   a false high_payout alert. After: tail-4 dropna = [0.5, 0.5] → 50%.
MISSING_Q2 = [
    _rec("2025-03-31", 1_000_000, 100_000, 500_000),
    _rec("2025-06-30", 1_050_000, None,    520_000),   # NETINC not reported
    _rec("2025-09-30", 1_100_000, 300_000, 540_000),
    _rec("2025-12-31", 1_150_000, 400_000, 560_000),
    _rec("2026-03-31", 1_200_000, 100_000, 580_000),
]


class TestMissingNetIncome(unittest.TestCase):
    def test_quarterly_ni_is_na_at_and_after_the_missing_quarter(self):
        df = build_capital_timeline(MISSING_Q2)
        self.assertEqual(len(df), 5)
        ni = df["net_income_k_qtr"]
        self.assertEqual(ni.iloc[0], 100_000)            # Q1 = YTD
        self.assertTrue(pd.isna(ni.iloc[1]))             # Q2: absent
        self.assertTrue(pd.isna(ni.iloc[2]))             # Q3: 300k − unknown
        self.assertEqual(ni.iloc[3], 100_000)            # Q4 = 400k − 300k
        self.assertEqual(ni.iloc[4], 100_000)            # Q1'26 = YTD
        # The input itself stays absent — never coerced to 0.
        self.assertTrue(pd.isna(df["net_income_k_ytd"].iloc[1]))
        # Nothing fabricated: no 0 and no −100,000 (0 − prior YTD) anywhere.
        self.assertFalse((ni.fillna(-1) == 0).any())
        self.assertFalse((ni.fillna(0) < 0).any())

    def test_derived_capital_columns_are_na_on_those_rows(self):
        df = build_capital_timeline(MISSING_Q2)
        for i in (1, 2):
            self.assertTrue(pd.isna(df["capital_returned_k"].iloc[i]))
            self.assertTrue(pd.isna(df["retention_ratio"].iloc[i]))
        # The derivable rows keep their hand-computed values.
        self.assertEqual(df["capital_returned_k"].iloc[3], 50_000)
        self.assertEqual(df["capital_returned_k"].iloc[4], 50_000)
        self.assertAlmostEqual(df["retention_ratio"].iloc[3], 0.5, places=9)
        self.assertAlmostEqual(df["retention_ratio"].iloc[4], 0.5, places=9)

    def test_no_high_payout_alert_from_fabricated_rows(self):
        df = build_capital_timeline(MISSING_Q2)
        codes = [a["code"] for a in detect_capital_alerts(df)]
        self.assertNotIn("high_payout", codes)
        # Same through the public summary (what the UI reads).
        summary = summarize_bank_capital(MISSING_Q2)
        self.assertNotIn("high_payout", [a["code"] for a in summary["alerts"]])
        # payout_ratio_4q = (1 − mean([0.5, 0.5])) × 100 = 50, not 83.
        self.assertAlmostEqual(
            compute_capital_screening_metrics(MISSING_Q2)["payout_ratio_4q"], 50.0,
            places=9)

    def test_buyback_capacity_is_na_when_latest_ni_is_absent(self):
        # History ending on the absent quarter: latest NI is unknown, so the
        # explainer / screening capacity must be None (UI skips it), not NaN.
        recs = MISSING_Q2[:2]
        summary = summarize_bank_capital(recs)
        self.assertEqual(summary["buyback_capacity"],
                         {"retained": None, "organic_need": None, "free_capital": None})
        self.assertIsNone(compute_capital_screening_metrics(recs)["buyback_capacity_usd"])
        # And directly: a NaN in any term is unknown, never NI − 0.
        nan = float("nan")
        for args in ((nan, 50_000, 2_000), (100_000, nan, 2_000), (100_000, 50_000, nan)):
            self.assertIsNone(compute_buyback_capacity(*args)["free_capital"], args)
        self.assertIsNone(compute_organic_capital_need(nan))


class TestMissingLoans(unittest.TestCase):
    def test_loan_growth_is_na_at_and_after_never_minus_100_or_inf(self):
        recs = [
            _rec("2025-03-31", 1_000_000, 100_000, 500_000),
            _rec("2025-06-30", 1_050_000, 250_000, None),      # LNLSNET absent
            _rec("2025-09-30", 1_100_000, 400_000, 540_000),
            _rec("2025-12-31", 1_150_000, 600_000, 560_000),
        ]
        df = build_capital_timeline(recs)
        pct, k = df["loan_growth_qoq_pct"], df["loan_growth_qoq_k"]
        self.assertTrue(pd.isna(df["total_loans_k"].iloc[1]))
        self.assertTrue(pd.isna(pct.iloc[1]))    # was (0 − 500k)/500k = −100%
        self.assertTrue(pd.isna(pct.iloc[2]))    # was (540k − 0)/0 = +inf
        self.assertTrue(pd.isna(k.iloc[1]))
        self.assertTrue(pd.isna(k.iloc[2]))
        # Q4 vs Q3 is the one adjacent numeric pair:
        # (560,000 − 540,000) / 540,000 × 100 = 3.7037…%; 20,000 $K.
        self.assertAlmostEqual(pct.iloc[3], 20_000 / 540_000 * 100, places=9)
        self.assertEqual(k.iloc[3], 20_000)
        self.assertFalse(pct.apply(lambda v: pd.notna(v) and math.isinf(v)).any())
        self.assertFalse((pct.fillna(0) == -100).any())


class TestHappyPathUnchanged(unittest.TestCase):
    def test_all_fields_present_hand_computed(self):
        # YTD: 100k · 250k · 400k · 600k · Q1'26 120k
        #   quarterly: 100k · 150k · 150k · 200k · 120k
        # equity +50k each quarter → returned: n/a · 100k · 100k · 150k · 70k
        #   retention: n/a · 1−100/150 = 0.3333 · 0.3333 · 1−150/200 = 0.25 ·
        #              1−70/120 = 0.41667
        recs = [
            _rec("2025-03-31", 1_000_000, 100_000, 500_000),
            _rec("2025-06-30", 1_050_000, 250_000, 520_000),
            _rec("2025-09-30", 1_100_000, 400_000, 540_000),
            _rec("2025-12-31", 1_150_000, 600_000, 560_000),
            _rec("2026-03-31", 1_200_000, 120_000, 580_000),
        ]
        df = build_capital_timeline(recs)
        self.assertEqual(list(df["net_income_k_qtr"]),
                         [100_000, 150_000, 150_000, 200_000, 120_000])
        self.assertTrue(pd.isna(df["capital_returned_k"].iloc[0]))
        self.assertEqual(list(df["capital_returned_k"].iloc[1:]),
                         [100_000, 100_000, 150_000, 70_000])
        for i, want in ((1, 1 - 100 / 150), (2, 1 - 100 / 150),
                        (3, 0.25), (4, 1 - 70 / 120)):
            self.assertAlmostEqual(df["retention_ratio"].iloc[i], want, places=9)
        self.assertEqual(list(df["loan_growth_qoq_k"].iloc[1:]),
                         [20_000, 20_000, 20_000, 20_000])
        # Latest-quarter buyback capacity: organic need = 20,000 × 10% = 2,000;
        # free = 120,000 − 70,000 − 2,000 = 48,000 $K → 48,000,000 raw dollars.
        bb = summarize_bank_capital(recs)["buyback_capacity"]
        self.assertEqual(bb, {"retained": 50_000, "organic_need": 2_000,
                              "free_capital": 48_000})
        self.assertEqual(
            compute_capital_screening_metrics(recs)["buyback_capacity_usd"], 48_000_000)


if __name__ == "__main__":
    unittest.main()
