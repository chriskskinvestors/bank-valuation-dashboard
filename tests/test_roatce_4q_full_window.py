"""
"4Q" figures are a full four-quarter window or None (REVIEW-2026-09-24 P0-3 / P0-4).

compute_roatce_4q used to annualize 1–3 quarters (× 4/count) and compute_4q_avg
averaged whatever existed in the first four rows; both were displayed under a
"4Q" label. Neither checked that the four rows were consecutive quarters.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.valuation import compute_4q_avg, compute_roatce_4q  # noqa: E402


def _r(repdte, netinc, eqtot=1000.0, intan=0.0, nimy=3.0):
    return {"REPDTE": repdte, "NETINC": netinc, "EQTOT": eqtot, "INTAN": intan,
            "NIMY": nimy}


# Four consecutive fiscal-2025 quarters, YTD NI +100 per quarter, TCE flat 1000.
CLEAN = [_r("20251231", 400.0), _r("20250930", 300.0),
         _r("20250630", 200.0), _r("20250331", 100.0)]


class TestRoatce4qFullWindow(unittest.TestCase):
    def test_clean_series_hand_value(self):
        # TTM NI 400 / avg TCE 1000 → 40.0 %
        self.assertAlmostEqual(compute_roatce_4q(CLEAN), 40.0, places=9)

    def test_missing_netinc_in_window_is_none(self):
        hist = [dict(q) for q in CLEAN]
        hist[2]["NETINC"] = None          # Q2 YTD absent
        # Old: Q2 skipped, Q3 (300 − None) skipped, 200 × 4/2 = 400 → 40.0 %
        # presented as a 4Q figure. Now: unknown.
        self.assertIsNone(compute_roatce_4q(hist))

    def test_missing_equity_in_window_is_none(self):
        hist = [dict(q) for q in CLEAN]
        hist[1]["EQTOT"] = None
        self.assertIsNone(compute_roatce_4q(hist))

    def test_three_quarters_are_not_a_4q_window(self):
        # Old: 300 × 4/3 = 400 → 40.0 % from three quarters.
        self.assertIsNone(compute_roatce_4q(CLEAN[1:]))

    def test_gap_in_window_is_none(self):
        # Q3-25 missing: rows are Q4-25, Q2-25, Q1-25, Q4-24 — four rows, not
        # four consecutive quarters.
        hist = [_r("20251231", 400.0), _r("20250630", 200.0),
                _r("20250331", 100.0), _r("20241231", 380.0)]
        self.assertIsNone(compute_roatce_4q(hist))

    def test_missing_repdte_is_none(self):
        hist = [dict(q) for q in CLEAN]
        hist[0]["REPDTE"] = None
        self.assertIsNone(compute_roatce_4q(hist))


class TestFourQuarterAverageFullWindow(unittest.TestCase):
    def test_clean_series_hand_value(self):
        hist = [_r("20251231", 0, nimy=3.0), _r("20250930", 0, nimy=3.2),
                _r("20250630", 0, nimy=2.8), _r("20250331", 0, nimy=3.0)]
        self.assertAlmostEqual(compute_4q_avg(hist, "NIMY"), 3.0, places=9)

    def test_one_none_in_window_is_none(self):
        # Old: mean of the three present values (3.1) displayed as "NIM 4Q".
        hist = [_r("20251231", 0, nimy=3.0), _r("20250930", 0, nimy=None),
                _r("20250630", 0, nimy=3.2), _r("20250331", 0, nimy=3.1)]
        self.assertIsNone(compute_4q_avg(hist, "NIMY"))

    def test_short_history_is_none(self):
        hist = [_r("20251231", 0, nimy=3.0), _r("20250930", 0, nimy=3.2)]
        self.assertIsNone(compute_4q_avg(hist, "NIMY"))

    def test_gap_is_none(self):
        hist = [_r("20251231", 0, nimy=3.0), _r("20250630", 0, nimy=3.2),
                _r("20250331", 0, nimy=2.8), _r("20241231", 0, nimy=3.0)]
        self.assertIsNone(compute_4q_avg(hist, "NIMY"))

    def test_empty_is_none(self):
        self.assertIsNone(compute_4q_avg([], "NIMY"))


if __name__ == "__main__":
    unittest.main()
