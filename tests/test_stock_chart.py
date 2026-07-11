"""Unit tests for the Stock Chart sub-tab's pure helpers.

Run: python -m unittest tests.test_stock_chart
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from ui.stock_chart import _indexed_pct, _period_stats, _nearest_size_order  # noqa: E402


class TestIndexedPct(unittest.TestCase):
    def test_hand_computed(self):
        # base 50: 50→0%, 55→+10%, 45→-10%
        out = _indexed_pct(pd.Series([50.0, 55.0, 45.0]))
        self.assertAlmostEqual(out.iloc[0], 0.0)
        self.assertAlmostEqual(out.iloc[1], 10.0)
        self.assertAlmostEqual(out.iloc[2], -10.0)

    def test_leading_nan_uses_first_real_base(self):
        out = _indexed_pct(pd.Series([float("nan"), 40.0, 44.0]))
        self.assertAlmostEqual(out.iloc[1], 0.0)
        self.assertAlmostEqual(out.iloc[2], 10.0)

    def test_empty_and_bad_base(self):
        self.assertTrue(_indexed_pct(pd.Series(dtype=float)).empty)
        # zero/negative base is bad data → empty, never a fake index
        self.assertTrue(_indexed_pct(pd.Series([0.0, 10.0])).empty)
        self.assertTrue(_indexed_pct(pd.Series([-5.0, 10.0])).empty)


class TestPeriodStats(unittest.TestCase):
    def test_hand_computed(self):
        df = pd.DataFrame({
            "close": [100.0, 110.0, 105.0],
            "high": [101.0, 112.0, 106.0],
            "low": [99.0, 108.0, 103.0],
            "volume": [1000, 3000, 2000],
        })
        s = _period_stats(df)
        self.assertAlmostEqual(s["return_pct"], 5.0)   # 105/100 - 1
        self.assertEqual(s["high"], 112.0)
        self.assertEqual(s["low"], 99.0)
        self.assertEqual(s["avg_volume"], 2000.0)

    def test_falls_back_to_close_without_hilo(self):
        df = pd.DataFrame({"close": [10.0, 12.0, 11.0]})
        s = _period_stats(df)
        self.assertEqual(s["high"], 12.0)
        self.assertEqual(s["low"], 10.0)
        self.assertIsNone(s["avg_volume"])

    def test_empty_is_all_none(self):
        s = _period_stats(pd.DataFrame())
        self.assertTrue(all(v is None for v in s.values()))

    def test_single_point_has_no_return(self):
        s = _period_stats(pd.DataFrame({"close": [10.0]}))
        self.assertIsNone(s["return_pct"])
        self.assertEqual(s["high"], 10.0)


class TestNearestSizeOrder(unittest.TestCase):
    COHORT = [
        {"ticker": "ME", "total_assets": 100.0},
        {"ticker": "BIG", "total_assets": 500.0},
        {"ticker": "NEAR", "total_assets": 110.0},
        {"ticker": "MID", "total_assets": 200.0},
        {"ticker": "NOASSETS"},
    ]

    def test_orders_by_asset_distance_subject_excluded(self):
        out = _nearest_size_order(self.COHORT, "ME")
        self.assertEqual(out, ["NEAR", "MID", "BIG", "NOASSETS"])
        self.assertNotIn("ME", out)

    def test_subject_without_assets_alphabetical(self):
        out = _nearest_size_order(self.COHORT, "NOASSETS")
        self.assertEqual(out, sorted(["ME", "BIG", "NEAR", "MID"]))

    def test_empty_cohort(self):
        self.assertEqual(_nearest_size_order([], "ME"), [])


if __name__ == "__main__":
    unittest.main()
