"""
The Earnings Results board is built by poll-events, not on the render thread
(REVIEW-2026-09-24 P1-9).

results_board() was a 15-min served_snapshot whose builder (FMP calendar +
EDGAR release metrics per reporting bank + an FDIC institutions walk) ran on
the render thread, so the first Earnings view every 15 minutes paid the whole
build. poll-events (~30 min) now rebuilds it via refresh_results_board_snapshot
and renders serve it up to _BOARD_RENDER_MAX_AGE_S (2 h); past that the render
rebuilds exactly as before, so a stopped job self-heals instead of freezing.

All DB access runs on an isolated in-memory SQLite engine; no network.
"""
import time
import unittest
from datetime import datetime
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

from sqlalchemy import create_engine, text  # noqa: E402

import data.cache as cache  # noqa: E402
import data.earnings_results as er  # noqa: E402

ROWS = [{"ticker": "BANR", "eps": 1.42}, {"ticker": "FHB", "eps": 0.51}]


def _no_build(days_back):
    raise AssertionError("results board built on the render path")


class _IsolatedCache(unittest.TestCase):
    def setUp(self):
        eng = create_engine("sqlite://")
        with eng.begin() as conn:
            conn.execute(text(
                "CREATE TABLE cache (key VARCHAR(255) PRIMARY KEY, "
                "value TEXT NOT NULL, timestamp DOUBLE PRECISION NOT NULL)"))
        self._eng = eng
        p = patch.object(cache, "_engine", eng)
        p.start()
        self.addCleanup(p.stop)

    def _put_board(self, rows, age_s):
        from datetime import timedelta
        key = er._board_key(30)
        cache.put(key, {"cached_at": (datetime.now() - timedelta(seconds=age_s)).isoformat(),
                        "guard": None, "value": rows})
        with self._eng.begin() as conn:
            conn.execute(text("UPDATE cache SET timestamp = :t WHERE key = :k"),
                         {"t": time.time() - age_s, "k": key})


class TestRenderServesJobBuiltBoard(_IsolatedCache):
    def test_ninety_minute_old_board_served_without_building(self):
        # Pre-fix: 90 min > the 900 s TTL → the render rebuilt the board.
        # Count builds (a raising stub would be masked by the last-good
        # fallback and pass on the old code too).
        self._put_board(ROWS, 90 * 60)
        builds = []
        with patch.object(er, "_build_results_board",
                          lambda d: builds.append(d) or [{"ticker": "NEW"}]):
            self.assertEqual(ROWS, er.results_board())
        self.assertEqual([], builds)

    def test_board_older_than_two_hours_rebuilds_on_render(self):
        self._put_board(ROWS, 3 * 3600)
        fresh = [{"ticker": "COLB", "eps": 0.66}]
        with patch.object(er, "_build_results_board", lambda d: fresh):
            self.assertEqual(fresh, er.results_board())

    def test_no_board_bootstraps_inline(self):
        with patch.object(er, "_build_results_board", lambda d: ROWS):
            self.assertEqual(ROWS, er.results_board())
        self.assertTrue(er.results_board_available())


class TestJobRefresh(_IsolatedCache):
    def test_refresh_persists_fresh_board(self):
        with patch.object(er, "_build_results_board", lambda d: ROWS):
            self.assertEqual(2, er.refresh_results_board_snapshot())
        snap = cache.get(er._board_key(30), max_age_s=None)
        self.assertEqual(ROWS, snap["value"])
        age = (datetime.now() - datetime.fromisoformat(snap["cached_at"])).total_seconds()
        self.assertLess(age, 60)
        # And the render now serves it without building.
        with patch.object(er, "_build_results_board", _no_build):
            self.assertEqual(ROWS, er.results_board())

    def test_failed_refresh_keeps_last_good_board(self):
        self._put_board(ROWS, 60)

        def boom(days_back):
            raise RuntimeError("FMP earnings calendar unavailable")

        with patch.object(er, "_build_results_board", boom):
            with self.assertRaises(RuntimeError):
                er.refresh_results_board_snapshot()
        self.assertEqual(ROWS, cache.get(er._board_key(30), max_age_s=None)["value"])


class TestPollEventsBuildsBoard(unittest.TestCase):
    """_run_main (tests/test_poll_events_budget.py) stubs the board refresh with
    a MagicMock that stays installed until the ExitStack closes — assert on it."""

    def test_poll_events_main_calls_the_board_refresh(self):
        from contextlib import ExitStack
        from tests.test_poll_events_budget import _FakeAdapter, _run_main
        with ExitStack() as stack:
            rc, _ = _run_main(stack, [_FakeAdapter(f"a{i}") for i in range(9)])
            import data.earnings_results as er_mod
            er_mod.refresh_results_board_snapshot.assert_called_once()
        self.assertEqual(0, rc)

    def test_board_skipped_when_budget_spent(self):
        import jobs.poll_events as pe
        from contextlib import ExitStack
        from tests.test_poll_events_budget import _FakeAdapter, _run_main
        with ExitStack() as stack:
            # First reading = start; every later reading is past the budget.
            rc, _ = _run_main(stack, [_FakeAdapter(f"a{i}") for i in range(9)],
                              time_values=[0.0, pe._TASK_BUDGET_S + 100])
            import data.earnings_results as er_mod
            er_mod.refresh_results_board_snapshot.assert_not_called()
        self.assertEqual(0, rc)


if __name__ == "__main__":
    unittest.main()
