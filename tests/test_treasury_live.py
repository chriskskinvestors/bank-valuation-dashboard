"""Tests for data/treasury_live.py — the live CBOE-yield overlay for the Rates
board. Network-free: pins the plausibility guard (so a mis-scaled 10x tick can
never reach the board) and the cache decode round-trip.
"""
import unittest
from datetime import datetime

from data.treasury_live import _plausible, _decode


class TestPlausible(unittest.TestCase):
    def test_accepts_real_yields(self):
        for y in (0.05, 3.658, 4.388, 4.872, 19.9):
            self.assertTrue(_plausible(y), y)

    def test_rejects_scaled_negative_and_junk(self):
        # 43.88 = the classic 10x-scaled ^TNX quote — must be rejected, never shown.
        for y in (43.88, 100.0, 25.0, 0.0, -1.0, None, float("nan"), "x"):
            self.assertFalse(_plausible(y), y)


class TestDecode(unittest.TestCase):
    def test_round_trips_iso_timestamps(self):
        ts = datetime(2026, 6, 29, 7, 30)
        enc = {"DGS10": {"yield": 4.388, "asof": ts.isoformat()}}
        out = _decode(enc)
        self.assertEqual(out["DGS10"]["yield"], 4.388)
        self.assertEqual(out["DGS10"]["asof"], ts)

    def test_skips_corrupt_entries(self):
        out = _decode({"DGS10": {"yield": 4.4, "asof": "not-a-date"}, "X": {}})
        self.assertEqual(out, {})

    def test_handles_empty(self):
        self.assertEqual(_decode(None), {})
        self.assertEqual(_decode({}), {})


if __name__ == "__main__":
    unittest.main()
