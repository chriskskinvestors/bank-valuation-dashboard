"""
Tests for analysis/ownership_analytics.py — phase-1 holder analytics for
the Ownership tabs (docs/SNL-BUILD-PLAN.md §13). All storage is faked
in-memory exactly like tests/test_form13f_history.py; no live network. Pins:

  • turnover formula, hand-computed: positions 100 → 150 → 100 give
    mean |Δ| = 50, mean position = 350/3 = 116.67 → 42.857142857%
  • turnover quartile categories within this bank's holder set
    (Very Low / Low / Moderate / High on a hand-built 4-holder fixture)
  • holders without >= 2 quarters of history -> honest None entries;
    category None when fewer than 2 holders have computable turnover
  • concentration on a 3-holder fixture: shares 500/300/200 →
    top5 = top10 = 100%, HHI = 50² + 30² + 20² = 3800, n = 3
  • crossholdings on a multi-bank fixture: CIK matching, min_holders
    filter, self exclusion, names_sample capped at 5 and ordered by
    this bank's position value, sort by shared_holders desc
  • ownership_buckets quarter totals + change_pct hand math
  • empty-store paths return {} / None / [] (never fabricated)
"""
import fnmatch
import json
import sys
import types
import unittest
from unittest.mock import patch

# Stub streamlit before importing modules that decorate with st.cache_data.
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from data import form13f_client as f13  # noqa: E402
from analysis import ownership_analytics as own  # noqa: E402

PREFIX = f13.FORM13F_CACHE_PREFIX


class _MemStore:
    """In-memory stand-in for data.cloud_storage save/load/list."""

    def __init__(self):
        self.files: dict[tuple[str, str], dict] = {}

    def save_json(self, prefix, filename, data) -> bool:
        self.files[(prefix, filename)] = json.loads(json.dumps(data, default=str))
        return True

    def load_json(self, prefix, filename):
        return self.files.get((prefix, filename))

    def list_files(self, prefix, pattern="*.json"):
        return sorted(n for (p, n) in self.files
                      if p == prefix and fnmatch.fnmatch(n, pattern))


def _holder(cik, name, shares, value_usd, date_filed):
    return {
        "filer_cik": cik, "filer_name": name, "date_filed": date_filed,
        "accession": f"0000000000-26-{cik[-6:]}",
        "filing_url": "https://example.invalid",
        "shares": shares, "value_usd": value_usd, "positions": [],
    }


class _StoreBackedTest(unittest.TestCase):
    """Patches form13f_client's storage bindings with an in-memory store.
    ownership_analytics reaches storage THROUGH f13, so this covers it."""

    def setUp(self):
        self.store = _MemStore()
        for fn in ("save_json", "load_json", "list_files"):
            p = patch.object(f13, fn, getattr(self.store, fn))
            p.start()
            self.addCleanup(p.stop)

    def _save_latest(self, ticker, holders):
        self.store.save_json(PREFIX, f"{ticker}.json", {
            "ticker": ticker, "cached_at": "2026-06-12T00:00:00",
            "holders": holders,
        })


# Filing dates → covered quarters (pinned in tests/test_form13f_history.py):
# 2025-11-10 → 2025Q3, 2026-02-10 → 2025Q4, 2026-05-10 → 2026Q1.
D_Q3, D_Q4, D_Q1 = "2025-11-10", "2026-02-10", "2026-05-10"


class TestHolderTurnover(_StoreBackedTest):

    def test_hand_computed_formula_and_no_history_none(self):
        # Alpha: 100 → 150 → 100 across three stored quarters.
        f13._save_quarter_snapshots("BANR", [_holder("0001", "Alpha", 100.0, 1000.0, D_Q3)])
        f13._save_quarter_snapshots("BANR", [_holder("0001", "Alpha", 150.0, 1500.0, D_Q4)])
        f13._save_quarter_snapshots("BANR", [
            _holder("0001", "Alpha", 100.0, 1000.0, D_Q1),
            _holder("0002", "One Quarter Only", 50.0, 500.0, D_Q1),
        ])
        # A latest-window holder with no quarter history at all.
        self._save_latest("BANR", [
            _holder("0001", "Alpha", 100.0, 1000.0, D_Q1),
            _holder("0003", "Fresh Face", 10.0, 100.0, D_Q1),
        ])

        result = own.holder_turnover("BANR")
        self.assertEqual(set(result), {"Alpha", "One Quarter Only", "Fresh Face"})

        # Hand math: mean |Δ| = (|150-100| + |100-150|)/2 = 50;
        # mean position = (100+150+100)/3 = 116.666…;
        # turnover = 50 / 116.666… × 100 = 42.857142857…%
        self.assertAlmostEqual(result["Alpha"]["turnover_pct"],
                               42.857142857142854, places=9)
        # Only one holder has computable turnover → quartiles meaningless
        self.assertIsNone(result["Alpha"]["category"])

        # < 2 quarters of history → honest None entries, never fabricated
        for name in ("One Quarter Only", "Fresh Face"):
            self.assertEqual(result[name],
                             {"turnover_pct": None, "category": None})

    def test_quartile_categories_within_holder_set(self):
        # Four holders, two quarters each: turnover = |Δ| / mean × 100.
        #   VeryLowCo 100→100 → 0%        LowCo  100→110 → 10/105  = 9.5238%
        #   ModCo     100→150 → 40%       HighCo 100→300 → 200/200 = 100%
        f13._save_quarter_snapshots("BANR", [
            _holder("0001", "VeryLowCo", 100.0, 1.0, D_Q4),
            _holder("0002", "LowCo", 100.0, 1.0, D_Q4),
            _holder("0003", "ModCo", 100.0, 1.0, D_Q4),
            _holder("0004", "HighCo", 100.0, 1.0, D_Q4),
        ])
        f13._save_quarter_snapshots("BANR", [
            _holder("0001", "VeryLowCo", 100.0, 1.0, D_Q1),
            _holder("0002", "LowCo", 110.0, 1.0, D_Q1),
            _holder("0003", "ModCo", 150.0, 1.0, D_Q1),
            _holder("0004", "HighCo", 300.0, 1.0, D_Q1),
        ])

        result = own.holder_turnover("BANR")
        self.assertAlmostEqual(result["LowCo"]["turnover_pct"],
                               9.523809523809524, places=9)
        self.assertAlmostEqual(result["ModCo"]["turnover_pct"], 40.0, places=9)
        self.assertEqual(result["VeryLowCo"]["category"], "Very Low")
        self.assertEqual(result["LowCo"]["category"], "Low")
        self.assertEqual(result["ModCo"]["category"], "Moderate")
        self.assertEqual(result["HighCo"]["category"], "High")

    def test_empty_store_returns_empty(self):
        self.assertEqual(own.holder_turnover("BANR"), {})
        self.assertEqual(own.holder_turnover(""), {})


class TestHolderConcentration(_StoreBackedTest):

    def test_three_holder_fixture_hand_computed_hhi(self):
        self._save_latest("BANR", [
            _holder("0001", "A", 500.0, 5000.0, D_Q1),
            _holder("0002", "B", 300.0, 3000.0, D_Q1),
            _holder("0003", "C", 200.0, 2000.0, D_Q1),
        ])
        c = own.holder_concentration("BANR")
        # Shares of SAMPLED total (1000): 50%, 30%, 20%.
        self.assertAlmostEqual(c["top5_pct"], 100.0, places=9)
        self.assertAlmostEqual(c["top10_pct"], 100.0, places=9)
        # HHI = 50² + 30² + 20² = 2500 + 900 + 400 = 3800
        self.assertAlmostEqual(c["hhi"], 3800.0, places=9)
        self.assertEqual(c["n_holders"], 3)

    def test_top5_slices_when_more_than_five_holders(self):
        self._save_latest("BANR", [
            _holder(f"000{i}", f"H{i}", 100.0, 1000.0, D_Q1)
            for i in range(1, 7)  # six equal holders
        ])
        c = own.holder_concentration("BANR")
        self.assertAlmostEqual(c["top5_pct"], 500.0 / 600.0 * 100, places=9)
        self.assertAlmostEqual(c["top10_pct"], 100.0, places=9)
        # HHI = 6 × (100/6)² = 1666.666…
        self.assertAlmostEqual(c["hhi"], 6 * (100.0 / 6.0) ** 2, places=9)
        self.assertEqual(c["n_holders"], 6)

    def test_missing_or_zero_share_data_returns_none(self):
        self.assertIsNone(own.holder_concentration("BANR"))  # nothing stored
        self._save_latest("BANR", [_holder("0001", "A", 0.0, 0.0, D_Q1)])
        self.assertIsNone(own.holder_concentration("BANR"))  # no positive shares


class TestCrossholdings(_StoreBackedTest):

    def _seed_three_banks(self):
        # BANR: six holders, value desc H1 > … > H6.
        self._save_latest("BANR", [
            _holder(f"000{i}", f"H{i}", 100.0, 7000.0 - i * 1000, D_Q1)
            for i in range(1, 7)
        ])
        # PEER1 shares H1+H2; PEER2 shares only H3; PEER3 shares all six.
        self._save_latest("PEER1", [
            _holder("0001", "H1", 5.0, 50.0, D_Q1),
            _holder("0002", "H2", 5.0, 50.0, D_Q1),
        ])
        self._save_latest("PEER2", [_holder("0003", "H3", 5.0, 50.0, D_Q1)])
        self._save_latest("PEER3", [
            _holder(f"000{i}", f"H{i}", 5.0, 50.0, D_Q1) for i in range(1, 7)
        ])

    def test_overlap_counts_sort_self_exclusion_and_sample_cap(self):
        self._seed_three_banks()
        rows = own.crossholdings("BANR", ["BANR", "PEER1", "PEER2", "PEER3"])

        # min_holders=2 default: PEER2 (1 shared) dropped; self excluded;
        # sorted by shared_holders desc.
        self.assertEqual([r["other_ticker"] for r in rows], ["PEER3", "PEER1"])
        self.assertEqual(rows[0]["shared_holders"], 6)
        # names_sample capped at 5, ordered by BANR position value desc
        self.assertEqual(rows[0]["names_sample"], ["H1", "H2", "H3", "H4", "H5"])
        self.assertEqual(rows[1]["shared_holders"], 2)
        self.assertEqual(rows[1]["names_sample"], ["H1", "H2"])

    def test_min_holders_one_includes_single_overlap(self):
        self._seed_three_banks()
        rows = own.crossholdings("BANR", ["PEER2"], min_holders=1)
        self.assertEqual(rows, [{"other_ticker": "PEER2",
                                 "shared_holders": 1,
                                 "names_sample": ["H3"]}])

    def test_no_base_snapshot_returns_empty(self):
        self.assertEqual(own.crossholdings("BANR", ["PEER1"]), [])


class TestOwnershipBuckets(_StoreBackedTest):

    def test_totals_and_quarter_change_hand_math(self):
        # 2025Q4 total = 100 + 150 = 250; 2026Q1 total = 120 + 180 = 300.
        f13._save_quarter_snapshots("BANR", [
            _holder("0001", "Alpha", 100.0, 1000.0, D_Q4),
            _holder("0002", "Beta", 150.0, 1500.0, D_Q4),
        ])
        f13._save_quarter_snapshots("BANR", [
            _holder("0001", "Alpha", 120.0, 1200.0, D_Q1),
            _holder("0002", "Beta", 180.0, 1800.0, D_Q1),
        ])
        self._save_latest("BANR", [
            _holder("0001", "Alpha", 120.0, 1200.0, D_Q1),
            _holder("0002", "Beta", 180.0, 1800.0, D_Q1),
        ])

        b = own.ownership_buckets("BANR")
        self.assertEqual(b["institutional_shares"], 300.0)
        self.assertEqual(list(b["by_quarter_change"]), ["2025Q4", "2026Q1"])
        q4 = b["by_quarter_change"]["2025Q4"]
        q1 = b["by_quarter_change"]["2026Q1"]
        self.assertEqual(q4["total_shares"], 250.0)
        self.assertIsNone(q4["change_pct"])  # no prior stored quarter
        self.assertEqual(q1["total_shares"], 300.0)
        # (300 − 250) / 250 × 100 = 20%
        self.assertAlmostEqual(q1["change_pct"], 20.0, places=9)

    def test_empty_store_is_honest(self):
        self.assertEqual(own.ownership_buckets("BANR"),
                         {"institutional_shares": None,
                          "by_quarter_change": {}})


if __name__ == "__main__":
    unittest.main()
