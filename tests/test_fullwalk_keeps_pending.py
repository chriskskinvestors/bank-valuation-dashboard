"""
2026-10-08: the nightly full walk started 11:30Z on the pre-deploy image,
finished 14:16Z and overwrote the 13:39Z pending pass (company-stated
multiples gone from the board). A walk keeps pending rows from a pending
pass that finished after the walk started.
"""
from __future__ import annotations

import unittest


class TestKeepNewerPending(unittest.TestCase):

    def _rows(self):
        return [{"status": "pending", "announce_date": "2026-10-07", "target_name": "GP",
                 "p_tbv": 1.36},
                {"status": "completed", "announce_date": "2026-01-02", "target_name": "Old",
                 "p_tbv": 1.5}]

    def test_newer_pass_rows_survive_the_walk(self):
        from data.deal_comps import _keep_newer_pending
        served = {"pending_built_at": "2026-10-08T13:39:49",
                  "deals": [{"status": "pending", "announce_date": "2026-10-07",
                             "target_name": "GP", "p_tbv": 1.56, "p_tbv_basis": "stated"},
                            {"status": "completed", "announce_date": "2025-01-01",
                             "target_name": "Stale", "p_tbv": 9.9}]}
        rows, at = _keep_newer_pending(self._rows(), served, "2026-10-08T11:30:00")
        self.assertEqual(at, "2026-10-08T13:39:49")
        self.assertEqual([(r["target_name"], r["p_tbv"]) for r in rows],
                         [("GP", 1.56), ("Old", 1.5)])      # walk's completed rows win

    def test_older_pass_still_beats_the_walks_cached_pending_rows(self):
        # 2026-10-09 14:14Z: the walk's cached pending rows (Bank of Hawaii,
        # Ottawa acquiring itself) replaced a correct pass until the recompute.
        from data.deal_comps import _keep_newer_pending
        served = {"pending_built_at": "2026-10-08T22:04:59",
                  "deals": [{"status": "pending", "announce_date": "2026-10-07",
                             "target_name": "GP", "p_tbv": 1.56}]}
        rows, at = _keep_newer_pending(self._rows(), served, "2026-10-09T11:30:00")
        self.assertEqual(at, "2026-10-08T22:04:59")
        self.assertEqual([(r["target_name"], r["p_tbv"]) for r in rows],
                         [("GP", 1.56), ("Old", 1.5)])

    def test_served_walk_without_a_pass_is_replaced(self):
        from data.deal_comps import _keep_newer_pending
        served = {"deals": [{"status": "pending", "target_name": "GP", "p_tbv": 9.9}]}
        rows, at = _keep_newer_pending(self._rows(), served, "2026-10-09T11:30:00")
        self.assertIsNone(at)
        self.assertEqual(rows, self._rows())

    def test_no_served_snapshot(self):
        from data.deal_comps import _keep_newer_pending
        self.assertEqual(_keep_newer_pending(self._rows(), None, "2026-10-08T11:30:00"),
                         (self._rows(), None))


if __name__ == "__main__":
    unittest.main()
