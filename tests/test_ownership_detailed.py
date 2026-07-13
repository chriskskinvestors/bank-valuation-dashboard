"""Unit tests for the Ownership Detailed row builder (SNL plan §13 phase 1).

Run: python -m unittest tests.test_ownership_detailed
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# House pattern (test_holder_history): a minimal streamlit stub via
# setdefault so this suite is order-safe next to the stub-installing suites.
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from ui.ownership import _detailed_rows  # noqa: E402

_HOLDERS = [
    {"filer_name": "Alpha LLC", "filer_cik": "1", "accession": "a",
     "shares": 1_000_000.0, "value_usd": 80_000_000.0, "date_filed": "2026-05-15"},
    {"filer_name": "Beta LP", "filer_cik": "2", "accession": "b",
     "shares": 250_000.0, "value_usd": 20_000_000.0, "date_filed": "2026-05-14"},
]


class TestDetailedRows(unittest.TestCase):
    def test_hand_computed_full_inputs(self):
        prior = {"Alpha LLC": 800_000.0}  # Beta absent → New
        rows = _detailed_rows(_HOLDERS, prior, shares_out=10_000_000.0, price=82.5)
        alpha, beta = rows[0], rows[1]  # sorted by shares desc
        self.assertEqual(alpha["holder"], "Alpha LLC")
        self.assertEqual(alpha["d_shares"], 200_000.0)          # 1.0M - 0.8M
        self.assertAlmostEqual(alpha["d_pct"], 25.0)            # 200k/800k
        self.assertAlmostEqual(alpha["pct_cso"], 10.0)          # 1M/10M
        self.assertAlmostEqual(alpha["mkt_value"], 82_500_000)  # 1M × 82.5
        self.assertEqual(alpha["reported_value"], 80_000_000.0)
        self.assertFalse(alpha["is_new"])
        self.assertTrue(beta["is_new"])
        self.assertIsNone(beta["d_shares"])

    def test_no_prior_snapshot_means_na_not_new(self):
        rows = _detailed_rows(_HOLDERS, None, 10_000_000.0, 82.5)
        self.assertFalse(rows[0]["is_new"])
        self.assertIsNone(rows[0]["d_shares"])

    def test_bad_inputs_render_none_not_zero(self):
        rows = _detailed_rows(_HOLDERS, None, shares_out=None, price=0)
        self.assertIsNone(rows[0]["pct_cso"])   # no share count
        self.assertIsNone(rows[0]["mkt_value"])  # zero price is bad data

    def test_zero_prior_shares_no_divide(self):
        prior = {"Alpha LLC": 0.0}
        rows = _detailed_rows(_HOLDERS, prior, 10_000_000.0, 82.5)
        self.assertEqual(rows[0]["d_shares"], 1_000_000.0)
        self.assertIsNone(rows[0]["d_pct"])

    def test_sorted_by_shares_desc(self):
        rows = _detailed_rows(list(reversed(_HOLDERS)), None, None, None)
        self.assertEqual([r["holder"] for r in rows], ["Alpha LLC", "Beta LP"])


if __name__ == "__main__":
    unittest.main()
