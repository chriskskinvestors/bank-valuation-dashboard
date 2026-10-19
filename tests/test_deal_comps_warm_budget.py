"""
refresh-deal-comps hit its 7200s task timeout on 2026-10-14: the v15 bump
(10-06) had cached every bank's ma_history within a day, so all ~595
entries expired together a week later and one run re-walked the universe
cold (~12s/bank) — and that re-walk re-synchronized them for the next week.
The warm pass now re-walks only the missing/expired entries, oldest first,
at most ceil(banks/5) and 40 min per run; the compile serves every cached
history at any age, and while banks with NO cached history remain
unreached the served snapshot is kept (only the pending pass runs).
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()


def _banks(n):
    return [{"ticker": f"B{i}", "cert": 100 + i, "cik": 200 + i, "name": f"Bank {i}"}
            for i in range(n)]


def _key(b):
    return f"ma_history:v15:{b['cert']}:{b['cik']}"


class _Store:
    """In-memory stand-in for data.cache: get_ma_history's walk is faked as
    'write a fresh entry', history_cached_at reads the real key."""

    def __init__(self):
        self.d: dict[str, dict] = {}
        self.walked: list[str] = []

    def put_age(self, b, days):
        self.d[_key(b)] = {"deals": [], "cached_at":
                           (datetime.now() - timedelta(days=days)).isoformat()}

    def get(self, key, max_age_s=None):
        return self.d.get(key)

    def walk(self, cert, cik=None, name=None, ticker=None, allow_stale=False):
        self.walked.append(ticker)
        self.d[f"ma_history:v15:{cert}:{cik}"] = {
            "deals": [], "cached_at": datetime.now().isoformat()}
        return []


class TestWarmBudget(unittest.TestCase):

    def _run(self, store, banks, clock=None):
        from jobs import refresh_deal_comps as job
        calls = []
        patches = [
            patch.object(job, "_universe_banks", return_value=banks),
            patch("data.cache.get", side_effect=store.get),
            patch("data.ma_history.get_ma_history", side_effect=store.walk),
            patch("data.deal_comps.build_comps_snapshot",
                  side_effect=lambda *a, **k: calls.append(("compile", k)) or
                  {"deals_total": 200, "deals_priced": 1, "banks_covered": 1,
                   "deals": []}),
            patch("data.deal_comps.refresh_pending_snapshot",
                  side_effect=lambda b: calls.append("pending") or
                  {"deals_total": 200, "deals": []}),
            patch.object(job, "_refresh_spreads"),
        ]
        if clock is not None:
            patches.append(patch.object(job.time, "time", side_effect=clock))
        for p in patches:
            p.start()
        try:
            rc = job.main()
        finally:
            for p in reversed(patches):
                p.stop()
        return rc, calls

    def test_oldest_first_capped_at_a_fifth(self):
        banks = _banks(10)                       # cap = ceil(10/5) = 2
        store = _Store()
        ages = [1, 9, 2, 12, 3, 8, 4, 10, 5, 6]  # days; >=7 is expired
        for b, a in zip(banks, ages):
            store.put_age(b, a)
        rc, calls = self._run(store, banks)
        self.assertEqual(rc, 0)
        # expired: B3 (12d), B7 (10d), B1 (9d), B5 (8d) -> the two oldest
        self.assertEqual(store.walked, ["B3", "B7"])
        # the still-expired B1/B5 serve their cached history: compile runs,
        # told to serve stale entries rather than re-walk them
        self.assertEqual(calls[0][0], "compile")
        self.assertTrue(calls[0][1]["allow_stale"])
        self.assertEqual(calls[1], "pending")

    def test_missing_entries_go_first(self):
        banks = _banks(10)
        store = _Store()
        for b in banks:
            store.put_age(b, 20)
        del store.d[_key(banks[6])]
        self._run(store, banks)
        self.assertEqual(store.walked, ["B6", "B0"])

    def test_fresh_store_walks_nothing(self):
        banks = _banks(10)
        store = _Store()
        for b in banks:
            store.put_age(b, 1)
        rc, calls = self._run(store, banks)
        self.assertEqual((rc, store.walked), (0, []))
        self.assertEqual(calls[0][0], "compile")

    def test_wall_clock_budget_stops_the_walk(self):
        from jobs import refresh_deal_comps as job
        banks = _banks(25)                       # cap 5
        store = _Store()
        for b in banks:
            store.put_age(b, 8)
        # each time.time() call advances the clock 15 minutes: t0 = 0, then
        # the budget check before each bank sees 15, 30, 45 (>= 40) min
        ticks = iter(range(0, 10**6, 900))
        rc, calls = self._run(store, banks, clock=lambda: next(ticks))
        self.assertEqual(len(store.walked), 2)
        self.assertLess(len(store.walked), -(-len(banks) // job._WARM_SHARE))
        self.assertEqual(calls[0][0], "compile")   # the rest serve stale

    def test_cold_store_fills_over_runs_keeping_the_served_snapshot(self):
        banks = _banks(12)                       # cap 3 -> 4 runs
        store = _Store()
        for run in range(1, 4):
            rc, calls = self._run(store, banks)
            self.assertEqual(rc, 0)
            # banks with no history yet -> no compile (their deals would
            # drop off the board); the pending pass keeps the board current
            self.assertEqual(calls, ["pending"], f"run {run}")
            self.assertEqual(len(store.d), 3 * run)
        rc, calls = self._run(store, banks)
        self.assertEqual(len(store.d), 12)
        self.assertEqual(calls[0][0], "compile")
        self.assertEqual(len(set(store.walked)), 12)

    def test_desynchronized_after_a_mass_expiry(self):
        # every bank expired on the same day (the 10-14 shape): each run
        # walks only a fifth, so the re-walked entries land on 5 different
        # days instead of re-synchronizing.
        banks = _banks(20)
        store = _Store()
        for b in banks:
            store.put_age(b, 7.5)
        self._run(store, banks)
        self.assertEqual(len(store.walked), 4)


class TestStaleServing(unittest.TestCase):

    def _old(self, days):
        return {"deals": [{"x": 1}], "cached_at":
                (datetime.now() - timedelta(days=days)).isoformat()}

    @patch("data.http.get_with_retry")
    def test_allow_stale_serves_an_expired_entry_without_network(self, mock_get):
        from data import ma_history
        with patch("data.cache.get", return_value=self._old(30)):
            self.assertEqual(ma_history.get_ma_history(5304, cik=1, allow_stale=True),
                             [{"x": 1}])
        self.assertEqual(mock_get.call_count, 0)

    def test_default_still_rewalks_an_expired_entry(self):
        from data import ma_history
        with patch("data.cache.get", return_value=self._old(8)), \
             patch("data.ma_history.fetch_history_rows", return_value=None) as f:
            self.assertEqual(ma_history.get_ma_history(5304, cik=1), [])
        self.assertTrue(f.called)

    def test_history_cached_at_and_is_cached(self):
        from data import ma_history
        old = self._old(30)
        with patch("data.cache.get", return_value=old) as g:
            self.assertEqual(ma_history.history_cached_at(5304, 1),
                             datetime.fromisoformat(old["cached_at"]))
            self.assertFalse(ma_history.history_is_cached(5304, 1))
            self.assertTrue(ma_history.history_is_cached(5304, 1, allow_stale=True))
        self.assertEqual(g.call_args.args[0], "ma_history:v15:5304:1")
        with patch("data.cache.get", return_value=None):
            self.assertIsNone(ma_history.history_cached_at(5304, 1))
            self.assertFalse(ma_history.history_is_cached(5304, 1, allow_stale=True))

    def test_compile_serves_stale_without_the_empty_retry(self):
        # a stale CACHED [] is a genuine "no deals" — no 15s/45s re-walks
        from data import deal_comps
        sleeps, kw = [], []
        old = {"deals": [], "cached_at":
               (datetime.now() - timedelta(days=9)).isoformat()}
        with patch("data.cache.get", return_value=old), \
             patch("data.ma_history.fetch_history_rows") as f, \
             patch("time.sleep", side_effect=sleeps.append), \
             patch("data.deal_comps.get_comps_snapshot", return_value=None), \
             patch("data.cache.put"):
            deal_comps.build_comps_snapshot(
                [{"ticker": "NODL", "cert": 1, "cik": 2, "name": "No Deals"}],
                min_deals=10**9, allow_stale=True)
        self.assertEqual(sleeps, [])
        self.assertFalse(f.called)


if __name__ == "__main__":
    unittest.main()
