"""
Unit tests for analysis.period_actuals.period_actuals — the period-matched
reported actuals that feed the consensus-vs-actual comparison.

Pins the two cardinal-rule failures that motivated it:
  * a not-yet-reported period must return None (no actuals at all), and
  * a reported quarter must return the SINGLE-quarter figure (YTD differenced),
    not the trailing/annualized snapshot.
Every expected value hand-computed from the synthetic FDIC history.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import analysis.period_actuals as pa  # noqa: E402


def _q(repdte, netinc, nim, nonii, nonix, elnatr, dep, lnls, asset):
    """One FDIC call-report record (income fields are YTD, in $000)."""
    return {"REPDTE": repdte, "NETINC": netinc, "NIM": nim, "NONII": nonii,
            "NONIX": nonix, "ELNATR": elnatr, "DEP": dep, "LNLSNET": lnls,
            "ASSET": asset}


# Desc-sorted (most recent first), as load_fdic_hist returns. 2026 has Q1+Q2
# only (Q3/Q4 not reported); 2025 has a Q4 (full year).
HIST = [
    _q("20260630", 250, 420, 110, 170, 25, 9500, 7300, 12500),   # 2026 Q2 (6mo YTD)
    _q("20260331", 100, 200,  50,  80, 10, 9000, 7000, 12000),   # 2026 Q1 (3mo YTD)
    _q("20251231", 480, 800, 210, 330, 40, 8800, 6900, 11800),   # 2025 Q4 (full yr)
]


class TestPeriodActuals(unittest.TestCase):

    def setUp(self):
        self._orig = pa.load_fdic_hist
        pa.load_fdic_hist = lambda ticker: list(HIST)

    def tearDown(self):
        pa.load_fdic_hist = self._orig

    def test_quarter_is_ytd_differenced_and_scaled(self):
        a = pa.period_actuals("X", "2026Q2")
        # Q2 single-quarter = YTD(Q2) − YTD(Q1), then ×1000 to raw dollars.
        self.assertEqual(a["net_income"], (250 - 100) * 1000)          # 150,000
        self.assertEqual(a["net_interest_income"], (420 - 200) * 1000)  # 220,000
        self.assertEqual(a["nonint_income"], (110 - 50) * 1000)        # 60,000
        self.assertEqual(a["nonint_expense"], (170 - 80) * 1000)       # 90,000
        self.assertEqual(a["provision"], (25 - 10) * 1000)             # 15,000
        # Balance-sheet items are point-in-time at the quarter-end.
        self.assertEqual(a["total_deposits"], 9500 * 1000)
        self.assertEqual(a["total_loans"], 7300 * 1000)
        self.assertEqual(a["total_assets"], 12500 * 1000)
        # No per-share / ratio metrics fabricated.
        self.assertNotIn("eps", a)
        self.assertNotIn("nim", a)

    def test_q1_quarter_equals_ytd(self):
        a = pa.period_actuals("X", "2026Q1")
        self.assertEqual(a["net_income"], 100 * 1000)                  # Q1 YTD = Q1
        self.assertEqual(a["net_interest_income"], 200 * 1000)

    def test_annual_uses_q4_full_year(self):
        a = pa.period_actuals("X", "2025")
        self.assertEqual(a["net_income"], 480 * 1000)                  # full-year YTD
        self.assertEqual(a["total_assets"], 11800 * 1000)             # year-end stock

    def test_unreported_quarter_is_none(self):
        self.assertIsNone(pa.period_actuals("X", "2026Q3"))           # not in hist
        self.assertIsNone(pa.period_actuals("X", "2026Q4"))

    def test_unreported_year_is_none(self):
        # 2026 has no Q4 yet → the full year is not reported.
        self.assertIsNone(pa.period_actuals("X", "2026"))

    def test_garbage_period_is_none(self):
        self.assertIsNone(pa.period_actuals("X", ""))
        self.assertIsNone(pa.period_actuals("X", "H1 2026"))

    def test_no_history_is_none(self):
        pa.load_fdic_hist = lambda ticker: []
        self.assertIsNone(pa.period_actuals("X", "2026Q2"))


if __name__ == "__main__":
    unittest.main()
