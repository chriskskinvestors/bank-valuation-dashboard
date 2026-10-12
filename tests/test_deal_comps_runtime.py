"""
refresh-deal-comps went from minutes to ~3h10m (Oct 10/11/12): the compile
loop slept 15s + 45s before re-asking for every bank whose history was empty,
including the ~190 banks whose [] is a genuine, CACHED "no deals".
"""
from __future__ import annotations

import unittest
from unittest.mock import patch


class TestEmptyHistoryRetry(unittest.TestCase):

    def _build(self, cached: bool):
        from data import deal_comps
        sleeps, calls = [], []
        with patch("data.ma_history.get_ma_history",
                   side_effect=lambda *a, **k: calls.append(a) or []), \
             patch("data.ma_history.history_is_cached", return_value=cached), \
             patch("time.sleep", side_effect=lambda s: sleeps.append(s)), \
             patch("data.deal_comps.get_comps_snapshot", return_value=None), \
             patch("data.cache.put"):
            deal_comps.build_comps_snapshot(
                [{"ticker": "NODL", "cert": 1, "cik": 2, "name": "No Deals Bank"}],
                min_deals=10**9)
        return sleeps, calls

    def test_cached_empty_history_is_not_retried(self):
        sleeps, calls = self._build(cached=True)
        self.assertEqual(sleeps, [])
        self.assertEqual(len(calls), 1)

    def test_uncached_empty_history_is_still_retried(self):
        # a failed FDIC fetch also returns [] and is never cached
        sleeps, calls = self._build(cached=False)
        self.assertEqual(sleeps, [15, 45])
        self.assertEqual(len(calls), 3)


class TestHistoryIsCached(unittest.TestCase):

    def test_reads_the_same_key_get_ma_history_writes(self):
        from data import ma_history
        from datetime import datetime
        with patch("data.cache.get",
                   return_value={"deals": [], "cached_at": datetime.now().isoformat()}) as g:
            self.assertTrue(ma_history.history_is_cached(5304, 1179821))
        self.assertEqual(g.call_args.args[0], "ma_history:v15:5304:1179821")
        with patch("data.cache.get", return_value=None):
            self.assertFalse(ma_history.history_is_cached(5304, None))
        # get_ma_history reads the very same key (a version bump must move both)
        fresh = {"deals": [], "cached_at": datetime.now().isoformat()}
        with patch("data.cache.get", return_value=fresh) as g2:
            ma_history.get_ma_history(5304, cik=1179821)
        self.assertEqual(g2.call_args_list[0].args[0], "ma_history:v15:5304:1179821")


if __name__ == "__main__":
    unittest.main()
