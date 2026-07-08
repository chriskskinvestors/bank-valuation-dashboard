"""
(AUDIT-2026-07-02 P2 #25) _derive_quarterly_value must NOT report a cumulative
YTD figure as a single quarter when the prior quarter is missing from history.

The bug: on a data gap (e.g. Q2 absent), deriving Q3's single-quarter net income
fell back to `return curr_ytd` — the 9-month cumulative — which then summed into
TTM net income, inflating TTM ROATCE and the linear fair-P/TBV "undervalued"
flag. Fix: return None (cardinal rule); both callers skip a None quarter.

All expected values hand-computed; no network.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.valuation import _derive_quarterly_value, compute_roatce_4q  # noqa: E402


def _r(repdte, netinc, eqtot=1000.0, intan=0.0):
    return {"REPDTE": repdte, "NETINC": netinc, "EQTOT": eqtot, "INTAN": intan}


class TestDeriveQuarterly(unittest.TestCase):
    def test_normal_q2_diff(self):
        hist = [_r("2025-06-30", 200), _r("2025-03-31", 80)]
        # Q2 single quarter = YTD(H1) 200 - YTD(Q1) 80 = 120
        self.assertEqual(_derive_quarterly_value("NETINC", hist, 0), 120)

    def test_q1_is_ytd(self):
        hist = [_r("2025-03-31", 80)]
        self.assertEqual(_derive_quarterly_value("NETINC", hist, 0), 80)

    def test_gap_returns_none_not_ytd(self):
        # Q2 missing -> can't derive Q3's single quarter. Old code returned the
        # 9-month YTD (300); the fix returns None.
        hist = [_r("2025-09-30", 300), _r("2025-03-31", 80)]
        self.assertIsNone(_derive_quarterly_value("NETINC", hist, 0))

    def test_roatce_4q_clean_series_unaffected(self):
        # Four consecutive quarters, +100 NI each; TCE flat at 1000.
        hist = [_r("2025-12-31", 400), _r("2025-09-30", 300),
                _r("2025-06-30", 200), _r("2025-03-31", 100)]
        # TTM NI = 400, avg TCE = 1000 -> 40.0%
        self.assertAlmostEqual(compute_roatce_4q(hist), 40.0, places=6)


if __name__ == "__main__":
    unittest.main()
