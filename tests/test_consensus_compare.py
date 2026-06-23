"""
Unit tests for data.consensus.compare_consensus_to_actual — the consensus-vs-
actual beat/miss engine. Pins the unit + key normalization that the audit fixed:
consensus is entered in display magnitude ($M/$B) while actuals are RAW dollars
under DIFFERENT keys (consensus "netinc" vs actual "net_income"). Every expected
value here is hand-computed.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.consensus import compare_consensus_to_actual  # noqa: E402


def _consensus(metrics):
    return {"ticker": "TST", "period": "2026Q1", "metrics": metrics}


def _m(key, value, unit, name=None):
    return {"name": name or key, "key": key, "value": value, "unit": unit}


def _by_key(results):
    return {r["key"]: r for r in results}


class TestCompareUnitsAndKeys(unittest.TestCase):

    # Actuals are RAW dollars for $-amounts (analysis/metrics.py ×1000), under the
    # config keys — note net_income / net_interest_income, not netinc / nii.
    ACTUAL = {
        "eps": 1.30, "nim": 3.40, "efficiency_ratio": 60.0,
        "provision": 55_000_000,          # raw $  (= $55M)
        "total_assets": 3_900_000_000_000,  # raw $ (= $3,900B)
        "net_income": 15_000_000_000,     # raw $  (= $15,000M)
    }

    def test_dollar_metric_key_and_unit_normalized(self):
        # netinc consensus is $M; actual lives under "net_income" in raw $.
        res = _by_key(compare_consensus_to_actual(
            _consensus([_m("netinc", 14500, "$M")]), self.ACTUAL))["netinc"]
        self.assertAlmostEqual(res["consensus"], 14500.0)   # shown in $M
        self.assertAlmostEqual(res["actual"], 15000.0)      # 15e9 raw → $M
        self.assertAlmostEqual(res["delta"], 500.0)
        self.assertAlmostEqual(res["delta_pct"], 500 / 14500 * 100, places=4)
        self.assertEqual(res["beat_miss"], "beat")          # higher income = beat

    def test_provision_billions_inline_and_cost_direction(self):
        # Exact match → inline; provision is a cost so higher = miss.
        inline = _by_key(compare_consensus_to_actual(
            _consensus([_m("provision", 55, "$M")]), self.ACTUAL))["provision"]
        self.assertAlmostEqual(inline["actual"], 55.0)
        self.assertAlmostEqual(inline["delta"], 0.0)
        self.assertEqual(inline["beat_miss"], "inline")

        worse = _by_key(compare_consensus_to_actual(
            _consensus([_m("provision", 50, "$M")]), self.ACTUAL))["provision"]
        self.assertAlmostEqual(worse["delta"], 5.0)         # 55 vs 50
        self.assertEqual(worse["beat_miss"], "miss")        # cost ↑ = miss

    def test_total_assets_billions(self):
        res = _by_key(compare_consensus_to_actual(
            _consensus([_m("total_assets", 3900, "$B")]), self.ACTUAL))["total_assets"]
        self.assertAlmostEqual(res["consensus"], 3900.0)
        self.assertAlmostEqual(res["actual"], 3900.0)       # 3.9e12 raw → $B
        self.assertEqual(res["beat_miss"], "inline")

    def test_entered_unit_billions_still_aligns(self):
        # A PDF that quotes net income in $B (14.5) must still line up with the
        # $M-canonical actual — the entered unit drives the raw conversion.
        res = _by_key(compare_consensus_to_actual(
            _consensus([_m("netinc", 14.5, "$B")]), self.ACTUAL))["netinc"]
        self.assertAlmostEqual(res["consensus"], 14500.0)
        self.assertAlmostEqual(res["actual"], 15000.0)
        self.assertEqual(res["beat_miss"], "beat")

    def test_ratio_and_pershare_unscaled(self):
        res = _by_key(compare_consensus_to_actual(_consensus([
            _m("eps", 1.25, "$"), _m("nim", 3.45, "%"),
            _m("efficiency_ratio", 58.0, "%"),
        ]), self.ACTUAL))
        self.assertAlmostEqual(res["eps"]["delta"], 0.05, places=6)
        self.assertEqual(res["eps"]["beat_miss"], "beat")
        self.assertEqual(res["nim"]["beat_miss"], "miss")       # 3.40 < 3.45
        # efficiency is lower-is-better: 60 actual vs 58 → worse → miss
        self.assertAlmostEqual(res["efficiency_ratio"]["delta"], 2.0)
        self.assertEqual(res["efficiency_ratio"]["beat_miss"], "miss")

    def test_no_actual_counterpart_is_na(self):
        # revenue and dps have no actual metric — must be n/a, never a fake beat.
        res = _by_key(compare_consensus_to_actual(_consensus([
            _m("revenue", 1000, "$M"), _m("dps", 0.5, "$"),
        ]), self.ACTUAL))
        self.assertEqual(res["revenue"]["beat_miss"], "n/a")
        self.assertIsNone(res["revenue"]["actual"])
        self.assertEqual(res["dps"]["beat_miss"], "n/a")

    def test_actual_missing_is_na(self):
        res = compare_consensus_to_actual(
            _consensus([_m("eps", 1.25, "$")]), {})        # no actuals at all
        self.assertEqual(res[0]["beat_miss"], "n/a")
        self.assertIsNone(res[0]["actual"])


if __name__ == "__main__":
    unittest.main()
