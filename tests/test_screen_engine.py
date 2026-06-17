"""Unit tests for analysis.screen_engine — absolute + peer-relative primitives.

Percentiles are hand-computed with the Hazen convention
(percentile = (n_below + 0.5*n_equal) / n_total * 100), matching
analysis.peer_groups.compute_peer_percentile.
"""
import unittest

from analysis.screen_engine import evaluate, _cmp


def _banks(**cols):
    """Build a list of metric dicts from parallel columns keyed by ticker order."""
    tickers = cols.pop("tickers")
    out = []
    for i, t in enumerate(tickers):
        d = {"ticker": t}
        for k, vals in cols.items():
            d[k] = vals[i]
        out.append(d)
    return out


class TestComparators(unittest.TestCase):
    def test_ops(self):
        self.assertTrue(_cmp(5, "<", 10))
        self.assertFalse(_cmp(10, "<", 10))
        self.assertTrue(_cmp(10, "≤", 10))
        self.assertTrue(_cmp(11, ">", 10))
        self.assertTrue(_cmp(10, "≥", 10))
        self.assertTrue(_cmp(10.0, "=", 10.001))   # within 0.005 tolerance
        self.assertFalse(_cmp(10.0, "=", 10.01))


class TestAbsolute(unittest.TestCase):
    def test_threshold_and_nodata(self):
        m = _banks(tickers=["JPM", "BAC", "WFC"], cet1_ratio=[12.0, 9.0, None])
        kept, nodata = evaluate(m, [{"kind": "absolute", "metric": "cet1_ratio",
                                     "op": "<", "value": 10.0}])
        self.assertEqual([b["ticker"] for b in kept], ["BAC"])  # 9<10 only
        self.assertEqual(nodata, 1)                              # WFC has no data

    def test_non_numeric_is_nodata(self):
        m = _banks(tickers=["A", "B"], x=[3.0, "n/a"])
        kept, nodata = evaluate(m, [{"kind": "absolute", "metric": "x", "op": ">", "value": 1}])
        self.assertEqual([b["ticker"] for b in kept], ["A"])
        self.assertEqual(nodata, 1)

    def test_empty_specs_returns_all(self):
        m = _banks(tickers=["A", "B"], x=[1.0, 2.0])
        kept, nodata = evaluate(m, [])
        self.assertEqual(len(kept), 2)
        self.assertEqual(nodata, 0)


class TestPeerRelative(unittest.TestCase):
    # roaa percentiles for [1.0,1.2,1.4,1.6,2.0] → 10,30,50,70,90 (hand-computed)
    def setUp(self):
        self.m = _banks(tickers=["A", "B", "C", "D", "E"],
                        roaa=[1.0, 1.2, 1.4, 1.6, 2.0])

    def test_top_quartile(self):
        kept, nodata = evaluate(self.m, [{"kind": "peer_relative", "metric": "roaa",
                                          "band": "Top", "pct": 25}])
        # Top 25% → percentile >= 75 → only E (90)
        self.assertEqual([b["ticker"] for b in kept], ["E"])
        self.assertEqual(nodata, 0)

    def test_bottom_quartile(self):
        kept, _ = evaluate(self.m, [{"kind": "peer_relative", "metric": "roaa",
                                     "band": "Bottom", "pct": 25}])
        # Bottom 25% → percentile <= 25 → only A (10)
        self.assertEqual([b["ticker"] for b in kept], ["A"])

    def test_top_half(self):
        kept, _ = evaluate(self.m, [{"kind": "peer_relative", "metric": "roaa",
                                     "band": "Top", "pct": 50}])
        # Top 50% → percentile >= 50 → C(50), D(70), E(90)
        self.assertEqual([b["ticker"] for b in kept], ["C", "D", "E"])

    def test_missing_value_is_nodata(self):
        m = _banks(tickers=["A", "B", "C"], roaa=[1.0, 2.0, None])
        kept, nodata = evaluate(m, [{"kind": "peer_relative", "metric": "roaa",
                                     "band": "Top", "pct": 50}])
        # C has no roaa → excluded as no-data, not ranked
        self.assertEqual(nodata, 1)
        self.assertNotIn("C", [b["ticker"] for b in kept])

    def test_percentiles_resolve_within_passed_scope(self):
        # Same bank value ranks differently depending on the scope it's compared in.
        small = _banks(tickers=["X", "Y"], roaa=[1.0, 2.0])
        kept_top, _ = evaluate(small, [{"kind": "peer_relative", "metric": "roaa",
                                        "band": "Top", "pct": 50}])
        # In a 2-bank scope, Y(2.0): below=1,equal=1 → 1.5/2*100=75 ≥ 50 keep;
        # X(1.0): 0.5/2*100=25 < 50 drop.
        self.assertEqual([b["ticker"] for b in kept_top], ["Y"])


class TestCombined(unittest.TestCase):
    def test_and_combination(self):
        m = _banks(tickers=["A", "B", "C", "D", "E"],
                   roaa=[1.0, 1.2, 1.4, 1.6, 2.0],
                   cet1_ratio=[11, 11, 11, 8, 11])
        specs = [
            {"kind": "peer_relative", "metric": "roaa", "band": "Top", "pct": 50},  # C,D,E
            {"kind": "absolute", "metric": "cet1_ratio", "op": "≥", "value": 10},   # not D
        ]
        kept, nodata = evaluate(m, specs)
        self.assertEqual([b["ticker"] for b in kept], ["C", "E"])
        self.assertEqual(nodata, 0)

    def test_nodata_precedence_over_fail(self):
        # A bank missing one spec's metric is no-data even if it would fail another.
        m = _banks(tickers=["A"], roaa=[2.0], cet1_ratio=[None])
        specs = [
            {"kind": "absolute", "metric": "cet1_ratio", "op": "<", "value": 5},  # would be no-data
            {"kind": "absolute", "metric": "roaa", "op": "<", "value": 1},        # would fail
        ]
        kept, nodata = evaluate(m, specs)
        self.assertEqual(kept, [])
        self.assertEqual(nodata, 1)


if __name__ == "__main__":
    unittest.main()
