"""(2026-08-17) Home's 100-373s above-fold stalls — the Calendar pane.

Per-pane timing (home.af.*) named the cause the first stall after it
deployed: home.af.calendar 208,136ms against Home's 3s budget. The pane's
fmp_announcement_call_info was a per-instance @st.cache_data(6h) whose
builder fetched press releases for EVERY universe bank reporting in the
next 14 days — a cold Cloud Run instance rebuilt it INLINE on the render
thread, and Streamlit's cache lock queued every concurrent session behind
the build (two sessions observed unfreezing at the same instant). Same
failure shape — and same fix — as fetch_earnings_calendar's 2026-06-13
regression: snapshots are built by jobs, renders only read.

Pins:
  1. the render path serves the cross-instance snapshot and performs NO
     fetch — no FMP call, no universe load, ever;
  2. an absent snapshot returns {} (degrade, don't build);
  3. the snapshot is served at WHATEVER age (no read ceiling drops it);
  4. refresh_announcement_call_snapshot persists the built map;
  5. a failed build returns {} and never overwrites the stored snapshot;
  6. structural: poll-events rebuilds it; the render fn contains no
     st.cache_data build.

Run: python -m unittest tests.test_announcement_call_snapshot
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

import data.earnings_call as ec  # noqa: E402


class TestRenderPathIsReadOnly(unittest.TestCase):
    def test_serves_snapshot_without_any_fetch(self):
        snap = {"value": {"FRME": {"call_time": "2:30 PM ET"}},
                "cached_at": "2026-08-17T04:00:00"}
        with patch("data.cache.get", return_value=snap) as get_, \
                patch("data.fmp_client.get_earnings_calendar",
                      side_effect=AssertionError("render path must not call FMP")), \
                patch("data.bank_universe.get_universe",
                      side_effect=AssertionError("render path must not load universe")):
            out = ec.fmp_announcement_call_info()
        self.assertEqual(out, {"FRME": {"call_time": "2:30 PM ET"}})
        # Served at WHATEVER age — the read must not apply a ceiling that
        # would drop an old-but-correct snapshot and return {}.
        self.assertIsNone(get_.call_args.kwargs.get("max_age_s", "MISSING"),
                          "snapshot read must pass max_age_s=None")

    def test_absent_snapshot_degrades_to_empty_not_build(self):
        with patch("data.cache.get", return_value=None), \
                patch("data.fmp_client.get_earnings_calendar",
                      side_effect=AssertionError("render path must not build")):
            self.assertEqual(ec.fmp_announcement_call_info(), {})

    def test_garbled_snapshot_degrades_to_empty(self):
        with patch("data.cache.get", return_value={"value": "not-a-dict"}):
            self.assertEqual(ec.fmp_announcement_call_info(), {})


class TestJobSideBuilder(unittest.TestCase):
    def test_build_persists_snapshot(self):
        puts = {}
        with patch("data.fmp_client.get_earnings_calendar", return_value=[]), \
                patch("data.bank_universe.get_universe", return_value={}), \
                patch("data.cache.put",
                      side_effect=lambda k, v: puts.update({k: v})):
            out = ec.refresh_announcement_call_snapshot()
        self.assertEqual(out, {})
        self.assertIn("announcement_call_snap", puts)
        self.assertIn("cached_at", puts["announcement_call_snap"])
        self.assertEqual(puts["announcement_call_snap"]["value"], {})

    def test_failed_build_never_overwrites(self):
        """The builder swallows source failures into {} internally (its
        calendar/universe try blocks), so force the failure past them and
        pin that NOTHING is written."""
        puts = {}
        with patch.object(ec, "_fmp_announcement_infos",
                          side_effect=RuntimeError("boom")), \
                patch("data.fmp_client.get_earnings_calendar",
                      return_value=[{"symbol": "FRME", "date": "2026-08-20"}]), \
                patch("data.bank_universe.get_universe",
                      return_value={"FRME": {"share_class": "common"}}), \
                patch("data.fmp_client.get_press_releases", return_value=[]), \
                patch("data.cache.put",
                      side_effect=lambda k, v: puts.update({k: v})):
            out = ec.refresh_announcement_call_snapshot()
        self.assertEqual(out, {})
        self.assertNotIn("announcement_call_snap", puts,
                         "a failed build must keep the stored snapshot")


class TestStructural(unittest.TestCase):
    def test_poll_events_rebuilds_the_snapshot(self):
        src = (REPO / "jobs/poll_events.py").read_text(encoding="utf-8")
        self.assertIn("refresh_announcement_call_snapshot", src)

    def test_render_fn_has_no_inline_build(self):
        # Inspect CODE only — the docstring deliberately narrates the old
        # @st.cache_data design as its regression history.
        import ast
        import inspect
        import textwrap
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(ec.fmp_announcement_call_info)))
        fn = tree.body[0]
        fn.body = fn.body[1:]          # drop the docstring statement
        src = ast.unparse(fn)
        self.assertNotIn("st.cache_data", src)
        self.assertNotIn("get_earnings_calendar", src)
        self.assertNotIn("ThreadPoolExecutor", src)


if __name__ == "__main__":
    unittest.main()
