"""
2026-10-08: the nightly full walk served pending rows from the per-bank
ma_history cache built by older code (Bank of Hawaii, HBT "acquiring"
Tri-County at 0.28x ... back on the board). The full mode now recomputes
pending rows with the pending pass before the spreads build.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch


class TestNightlyRecomputesPending(unittest.TestCase):

    def _run(self, fresh):
        from jobs import refresh_deal_comps as job
        walk = {"deals_total": 2216, "deals_priced": 900, "banks_covered": 590, "deals": []}
        calls = []
        with patch.object(job, "_universe_banks", return_value=[{"ticker": "X", "cert": 1,
                                                                 "cik": 2, "name": "X"}]), \
             patch("data.ma_history.get_ma_history", return_value=[]), \
             patch("data.deal_comps.build_comps_snapshot",
                   side_effect=lambda *a, **k: calls.append("walk") or walk), \
             patch("data.deal_comps.refresh_pending_snapshot",
                   side_effect=lambda b: calls.append("pending") or fresh), \
             patch.object(job, "_refresh_spreads",
                          side_effect=lambda s: calls.append(("spreads", s))):
            rc = job.main()
        return rc, calls, walk

    def test_pending_pass_runs_after_the_walk_and_feeds_the_spreads(self):
        fresh = {"deals_total": 2216, "deals": [], "pending_built_at": "t"}
        rc, calls, _walk = self._run(fresh)
        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["walk", "pending", ("spreads", fresh)])

    def test_failed_recompute_still_builds_spreads_from_the_walk(self):
        rc, calls, walk = self._run(None)
        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["walk", "pending", ("spreads", walk)])


if __name__ == "__main__":
    unittest.main()
