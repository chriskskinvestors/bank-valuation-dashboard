"""The nightly Trends pre-warm must REBUILD, not re-serve its own cache.

Incident 2026-10-12: the grids' freshness policy is 36h (_GRID_TTL_S) but
refresh-trends runs every 24h, and it called the same cache-first functions the
view uses. A grid built at 11:11 was "fresh" at the next night's run, so the job
logged "✓ FDIC grid: 593 banks (100% coverage) in 0s", wrote nothing, and exited
0 — every other night was a no-op. The all-banks view then went uncached from
hour 36 to 48 (≈12h of every 48) and data refreshed only every other day.

Pins:
  1. quarterly_series / sec_per_share_grid with rebuild=True ignore a FRESH
     cached grid and build;
  2. without rebuild, a fresh grid is still served (the view's cheap path);
  3. jobs/refresh_trends passes rebuild=True for both grids.

Run: python -m unittest tests.test_trends_job_rebuilds
"""
import unittest
from unittest import mock

from tests import _streamlit_stub

_streamlit_stub.install()

import data.as_of_metrics as aom  # noqa: E402
import data.sec_per_share as sps  # noqa: E402
import jobs.refresh_trends as job  # noqa: E402

_FRESH = {"labels": ["Q2 2026"], "rows": [{"ticker": "OLD"}],
          "cached_at": "2999-01-01T00:00:00"}


class TestRebuildBypassesFreshCache(unittest.TestCase):
    def _fdic(self, rebuild):
        with mock.patch("data.cache.get", return_value=dict(_FRESH)), \
             mock.patch("data.freshness.is_fresh", return_value=True), \
             mock.patch("data.cache.put") as put, \
             mock.patch("data.fdic_client.fetch_quarter_financials",
                        return_value={1: {"CERT": 1}}) as fetch, \
             mock.patch("analysis.metrics.build_bank_metrics",
                        return_value={k: 1.0 for k in aom.TREND_KEYS}):
            out = aom.quarterly_series({1: "NEW"}, 2, scope_id="T", rebuild=rebuild)
        return out, fetch, put

    def test_fdic_rebuild_builds_over_a_fresh_grid(self):
        out, fetch, put = self._fdic(rebuild=True)
        self.assertTrue(fetch.called)
        self.assertEqual(out["rows"][0]["ticker"], "NEW")
        put.assert_called_once()

    def test_fdic_without_rebuild_serves_the_fresh_grid(self):
        out, fetch, put = self._fdic(rebuild=False)
        self.assertFalse(fetch.called)
        self.assertEqual(out["rows"][0]["ticker"], "OLD")
        put.assert_not_called()

    def test_sec_rebuild_skips_the_fresh_grid(self):
        with mock.patch("data.cache.get", return_value=dict(_FRESH)), \
             mock.patch("data.freshness.is_fresh", return_value=True), \
             mock.patch.object(sps, "_bank_per_share", return_value=None) as per:
            sps.sec_per_share_grid({320193: "NEW"}, 2, scope_id="T", rebuild=True)
            self.assertTrue(per.called, "rebuild=True still returned the cached grid")
            per.reset_mock()
            out = sps.sec_per_share_grid({320193: "NEW"}, 2, scope_id="T")
            self.assertFalse(per.called)
            self.assertEqual(out["rows"][0]["ticker"], "OLD")


class TestJobForcesRebuild(unittest.TestCase):
    def test_both_grids_are_rebuilt(self):
        grid = {"rows": [{"ticker": "X"}], "persisted": True, "coverage": 1.0}
        with mock.patch("data.bank_universe.get_universe_tickers", return_value=["X"]), \
             mock.patch("data.bank_mapping.get_fdic_cert", return_value=1), \
             mock.patch("data.bank_mapping.get_cik", return_value=2), \
             mock.patch("data.as_of_metrics.quarterly_series", return_value=grid) as q, \
             mock.patch("data.sec_per_share.sec_per_share_grid", return_value=grid) as s:
            self.assertEqual(job.main(), 0)
        self.assertIs(q.call_args.kwargs.get("rebuild"), True)
        self.assertIs(s.call_args.kwargs.get("rebuild"), True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
