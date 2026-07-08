"""
(AUDIT-2026-07-02 P2 #33) The thin-reserves alert compared a bank's COMPUTED
reserve coverage (LNATRESR/NCLNLSR — deliberately not FDIC's holdco-mis-scaled
IDERNCVR) against a peer median built from raw IDERNCVR. Same word, different
quantity → a miscalibrated "thin vs peers" alert.

Fix: a shared _reserve_coverage() helper drives BOTH the per-bank timeline and
the peer median, so the two sides of the comparison are the same measure.

All expected values hand-computed; no network.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.credit_dynamics import (  # noqa: E402
    _reserve_coverage, compute_peer_reserve_median, build_credit_timeline,
)


class TestReserveCoverageBasis(unittest.TestCase):
    def test_helper_computes_from_constituents(self):
        # 2.0 / 1.0 * 100 = 200 — IDERNCVR (bogus 888) must be ignored
        self.assertEqual(_reserve_coverage(
            {"LNATRESR": 2.0, "NCLNLSR": 1.0, "IDERNCVR": 888}), 200.0)

    def test_helper_falls_back_to_idercvr(self):
        # NPL ratio missing -> can't compute -> use IDERNCVR
        self.assertEqual(_reserve_coverage(
            {"LNATRESR": 2.0, "NCLNLSR": None, "IDERNCVR": 150}), 150)
        # NPL ratio zero -> no coverage math -> IDERNCVR
        self.assertEqual(_reserve_coverage(
            {"LNATRESR": 2.0, "NCLNLSR": 0.0, "IDERNCVR": 250}), 250)

    def test_peer_median_uses_computed_not_raw_idercvr(self):
        # Computed coverages: A 300, B 200, C 100 -> median 200.
        # Raw IDERNCVR values (999/888/777) would give median 888 (the old bug).
        peers = {
            "A": [{"LNATRESR": 1.5, "NCLNLSR": 0.5, "IDERNCVR": 999}],
            "B": [{"LNATRESR": 2.0, "NCLNLSR": 1.0, "IDERNCVR": 888}],
            "C": [{"LNATRESR": 1.0, "NCLNLSR": 1.0, "IDERNCVR": 777}],
        }
        self.assertEqual(compute_peer_reserve_median(peers), 200.0)

    def test_timeline_reserve_coverage_matches_helper(self):
        rec = {"REPDTE": "2025-03-31", "LNATRESR": 2.0, "NCLNLSR": 1.0,
               "IDERNCVR": 888, "NTLNLSR": 0.1, "P3ASSET": 100.0,
               "P9ASSET": 50.0, "LNLSNET": 10_000.0}
        df = build_credit_timeline([rec])
        # per-bank timeline uses the computed coverage (200), not IDERNCVR (888)
        self.assertEqual(df["reserve_coverage"].iloc[0], 200.0)


if __name__ == "__main__":
    unittest.main()
