"""Unit tests for the finer scope-only asset bands (analysis.peer_groups).

These bands drive ONLY the Screen/Compare "Asset-size tier" scope picker; the
coarse asset_size_tier (peer-percentile cohorts) is intentionally untouched.
"""
import math
import unittest

from analysis.peer_groups import asset_size_band, asset_size_bands, asset_size_tier


class TestAssetBands(unittest.TestCase):
    def test_boundaries_are_lower_inclusive(self):
        cases = {
            None: None,
            0: None,
            -5: None,
            999_000_000: "< $1B",        # just under $1B
            1_000_000_000: "$1-10B",      # exactly $1B -> lower-inclusive
            9_280_000_000: "$1-10B",      # WTFC (bank-sub) sits in-band
            9_999_999_999: "$1-10B",      # just under $10B
            10_000_000_000: "$10-50B",    # exactly $10B
            28_000_000_000: "$10-50B",    # ABCB
            87_956_932_000: "$50-250B",   # ZION
            250_000_000_000: "> $250B",   # exactly $250B
            4_016_571_000_000: "> $250B",  # JPM
        }
        for assets, expected in cases.items():
            self.assertEqual(asset_size_band(assets), expected, f"assets={assets}")

    def test_bands_partition_and_order(self):
        metrics = [
            {"ticker": "A", "total_assets": 5e8},     # < $1B
            {"ticker": "B", "total_assets": 2e9},     # $1-10B
            {"ticker": "C", "total_assets": 8e9},     # $1-10B
            {"ticker": "D", "total_assets": 3e10},    # $10-50B
            {"ticker": "E", "total_assets": 1e12},    # > $250B
            {"ticker": "F", "total_assets": None},    # unbucketed
        ]
        bands = asset_size_bands(metrics)
        # ascending order, empty bands dropped, no overlap (counts sum to bucketed)
        self.assertEqual(list(bands), ["< $1B", "$1-10B", "$10-50B", "> $250B"])
        self.assertEqual([len(v) for v in bands.values()], [1, 2, 1, 1])
        self.assertEqual(sum(len(v) for v in bands.values()), 5)  # F excluded

    def test_peer_tier_unchanged(self):
        # The coarse peer tiers must still behave as before (regression guard).
        self.assertEqual(asset_size_tier(5e9), "Community (<$10B)")
        self.assertEqual(asset_size_tier(5e10), "Regional ($10-100B)")
        self.assertEqual(asset_size_tier(2e12), "Money-Center (>$1T)")


if __name__ == "__main__":
    unittest.main()
