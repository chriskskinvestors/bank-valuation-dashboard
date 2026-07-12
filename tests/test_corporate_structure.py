"""Unit tests for the Corporate Structure sub-tab's pure helpers.

The NIC client itself is covered by tests/test_nic_client.py; these pin the
UI-side climb + flatten logic. Live chain verified during the build:
Banner Bank (RSSD 352772) → Banner Corporation (BHC 2126977) → tree with
Banner Bank, statutory trusts, and Community Financial Corp under the bank.

Run: python -m unittest tests.test_corporate_structure
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from ui.corporate_structure import _top_holder_rssd, _flatten  # noqa: E402


def _tree():
    return {
        "entity": {"name": "BANNER CORPORATION", "rssd": 2126977,
                   "type": "Bank Holding Company", "location": "Walla Walla, WA"},
        "children": [
            {"entity": {"name": "BANNER BANK", "rssd": 352772,
                        "type": "State Non-member Bank", "location": "Walla Walla, WA"},
             "ownership_pct": 100.0, "relationship": "Controlled",
             "children": [
                 {"entity": {"name": "COMMUNITY FINANCIAL CORPORATION",
                             "rssd": 111, "type": "Domestic Entity (Other)",
                             "location": "Lake Oswego, OR"},
                  "ownership_pct": 100.0, "relationship": "Controlled",
                  "children": []},
             ]},
            {"entity": {"name": "BANNER CAPITAL TRUST V", "rssd": 222,
                        "type": "Domestic Entity (Other)", "location": "Walla Walla, WA"},
             "ownership_pct": 100.0, "relationship": "Controlled", "children": []},
        ],
    }


class TestFlatten(unittest.TestCase):
    def test_preorder_with_depths(self):
        rows = _flatten(_tree(), subject_rssd=352772)
        self.assertEqual([(r["name"], r["depth"]) for r in rows], [
            ("BANNER CORPORATION", 0),
            ("BANNER BANK", 1),
            ("COMMUNITY FINANCIAL CORPORATION", 2),
            ("BANNER CAPITAL TRUST V", 1),
        ])

    def test_root_has_no_ownership_and_subject_flagged(self):
        rows = _flatten(_tree(), subject_rssd=352772)
        self.assertIsNone(rows[0]["ownership_pct"])
        self.assertIsNone(rows[0]["relationship"])
        self.assertFalse(rows[0]["is_subject"])
        self.assertTrue(rows[1]["is_subject"])
        self.assertEqual(rows[1]["ownership_pct"], 100.0)
        self.assertEqual(rows[1]["relationship"], "Controlled")

    def test_no_subject_marks_nothing(self):
        rows = _flatten(_tree(), subject_rssd=None)
        self.assertFalse(any(r["is_subject"] for r in rows))


class TestTopHolderClimb(unittest.TestCase):
    def _climb(self, parents: dict, start: int):
        def fake_get_parent(rssd):
            return parents.get(int(rssd))
        with patch("data.nic_client.get_parent", side_effect=fake_get_parent):
            return _top_holder_rssd(start)

    def test_single_hop(self):
        top, chain = self._climb({352772: {"name": "BANNER CORPORATION",
                                           "rssd": 2126977}}, 352772)
        self.assertEqual(top, 2126977)
        self.assertEqual([c["name"] for c in chain], ["BANNER CORPORATION"])

    def test_multi_tier_chain(self):
        parents = {
            1: {"name": "MID-TIER HOLDCO", "rssd": 2},
            2: {"name": "TOP HOLDCO", "rssd": 3},
        }
        top, chain = self._climb(parents, 1)
        self.assertEqual(top, 3)
        self.assertEqual([c["name"] for c in chain],
                         ["MID-TIER HOLDCO", "TOP HOLDCO"])

    def test_no_parent_returns_self(self):
        top, chain = self._climb({}, 42)
        self.assertEqual(top, 42)
        self.assertEqual(chain, [])

    def test_cycle_guard(self):
        # A → B → A must terminate at B, not loop.
        parents = {1: {"name": "B", "rssd": 2}, 2: {"name": "A", "rssd": 1}}
        top, chain = self._climb(parents, 1)
        self.assertEqual(top, 2)
        self.assertEqual([c["name"] for c in chain], ["B"])


if __name__ == "__main__":
    unittest.main()
