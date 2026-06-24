"""
Unit tests for data.consensus.compile_consensus — aggregating multiple sell-side
firms' estimates into the consensus (mean across firms + low/high range + firm
count), with cross-firm unit normalization. Every expected value hand-computed.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.consensus as c  # noqa: E402


def _rec(firm, metrics):
    return {"ticker": "WTFC", "period": "2Q26", "firm": firm, "metrics": metrics}


def _m(name, key, value, unit):
    return {"name": name, "key": key, "value": value, "unit": unit}


class TestCompileConsensus(unittest.TestCase):

    def setUp(self):
        self._orig = c._firm_records
        self._records = []
        c._firm_records = lambda ticker, period: list(self._records)

    def tearDown(self):
        c._firm_records = self._orig

    def _compiled_by_key(self):
        out = c.compile_consensus("WTFC", "2Q26")
        return out, {m["key"]: m for m in (out["metrics"] if out else [])}

    def test_mean_low_high_firm_count(self):
        self._records = [
            _rec("Brean", [_m("EPS", "eps", 1.20, "$")]),
            _rec("KBW",   [_m("EPS", "eps", 1.36, "$")]),
        ]
        out, by = self._compiled_by_key()
        self.assertEqual(out["n_firms"], 2)
        self.assertEqual(out["firms"], ["Brean", "KBW"])
        eps = by["eps"]
        self.assertAlmostEqual(eps["value"], 1.28)        # mean
        self.assertAlmostEqual(eps["low"], 1.20)
        self.assertAlmostEqual(eps["high"], 1.36)
        self.assertEqual(eps["n_firms"], 2)

    def test_cross_firm_unit_normalization(self):
        # One firm quotes net income in $M, another in $B — must align before mean.
        self._records = [
            _rec("A", [_m("Net Income", "netinc", 14500, "$M")]),   # 14,500 $M
            _rec("B", [_m("Net Income", "netinc", 14.6, "$B")]),    # 14,600 $M
        ]
        _, by = self._compiled_by_key()
        ni = by["netinc"]
        self.assertAlmostEqual(ni["value"], 14550.0)      # mean in $M
        self.assertAlmostEqual(ni["low"], 14500.0)
        self.assertAlmostEqual(ni["high"], 14600.0)
        self.assertEqual(ni["unit"], "$M")                # canonical

    def test_single_firm_metric(self):
        self._records = [
            _rec("A", [_m("EPS", "eps", 1.25, "$"), _m("NIM", "nim", 3.4, "%")]),
            _rec("B", [_m("EPS", "eps", 1.31, "$")]),     # B has no NIM
        ]
        _, by = self._compiled_by_key()
        self.assertEqual(by["nim"]["n_firms"], 1)         # only one firm
        self.assertAlmostEqual(by["nim"]["low"], 3.4)
        self.assertAlmostEqual(by["nim"]["high"], 3.4)
        self.assertEqual(by["eps"]["n_firms"], 2)

    def test_compiled_feeds_comparison(self):
        # The compiled consensus must compare correctly against actuals and carry
        # the range through for display.
        self._records = [
            _rec("A", [_m("EPS", "eps", 1.20, "$")]),
            _rec("B", [_m("EPS", "eps", 1.40, "$")]),     # mean 1.30
        ]
        out, _ = self._compiled_by_key()
        res = {r["key"]: r for r in
               c.compare_consensus_to_actual(out, {"eps": 1.35})}["eps"]
        self.assertAlmostEqual(res["consensus"], 1.30)
        self.assertAlmostEqual(res["actual"], 1.35)
        self.assertEqual(res["beat_miss"], "beat")        # 1.35 > 1.30
        self.assertAlmostEqual(res["low"], 1.20)          # range carried through
        self.assertAlmostEqual(res["high"], 1.40)
        self.assertEqual(res["n_firms"], 2)

    def test_empty_returns_none(self):
        self._records = []
        self.assertIsNone(c.compile_consensus("WTFC", "2Q26"))


if __name__ == "__main__":
    unittest.main()
