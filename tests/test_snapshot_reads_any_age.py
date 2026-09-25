"""
(REVIEW-2026-09-24 P1-5) Job-produced snapshots must be read with
max_age_s=None.

cache.get() defaults to a 24h ceiling. Every key here is written by a
nightly job (or is run-to-run state) and read back ~24h later, so under the
default ceiling the read was alive-or-expired by the SECONDS of run-to-run
jitter — the same flip class as the refresh-universe gate baseline
(tests/test_nightly_gate_baseline). An expired read returns None, which each
reader treats as "missing":

  - earnings_calendar_snap: Home's Alert Inbox / the Earnings agenda went
    "unavailable" once the row was 24h+ old (the docstring promises "whatever
    its age");
  - sector_val_hist: the read-modify-write in refresh-home-snapshot rewrote
    the 365-day YoY history as a SINGLE record after a >24h scheduler gap;
  - ir_q4_endpoints: poll-events silently dropped every discovered endpoint
    past 24h; q4_calls_snap's no-clobber guard treated a >24h last-good as
    absent (so {} could overwrite it) and its reader returned {};
  - pr_call_snap: reader returned {} (its siblings already read None);
  - trend_series:* / sec_pershare:*: grids judged fresh to _GRID_TTL_S (36h)
    but read at 24h → the 24-36h window was unreachable ("not warmed").

One pin per site: put the writer's shape, age the row past 24h, assert the
reader still serves it. All DB access runs on an isolated in-memory SQLite
engine; no network. Each pin was confirmed RED against the pre-fix reads.
"""
import re
import unittest
from datetime import datetime, timedelta, date
from pathlib import Path
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

import data.cache as cache  # noqa: E402
import data.estimates as est  # noqa: E402
import data.earnings_call as ecall  # noqa: E402
import data.events.ir_site as irs  # noqa: E402
import data.as_of_metrics as aom  # noqa: E402
import data.sec_per_share as sps  # noqa: E402
import jobs.refresh_home_snapshot as rhs  # noqa: E402
import ui.home as home  # noqa: E402
from tests.test_cache_read_ceilings import _IsolatedCache  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
_AGE_S = 30 * 3600          # past the 24h default ceiling


def _stamp(age_s: float = 0.0) -> str:
    return (datetime.now() - timedelta(seconds=age_s)).isoformat()


class TestEarningsCalendarSnapshot(_IsolatedCache):
    ROWS = [{"ticker": "WAL", "next_earnings_date": "2026-10-20"}]

    def setUp(self):
        super().setUp()
        cache.put("earnings_calendar_snap",
                  {"cached_at": _stamp(), "guard": 1, "value": self.ROWS})
        self._age("earnings_calendar_snap", _AGE_S)

    def test_fetch_serves_30h_old_snapshot(self):
        self.assertEqual(est.fetch_earnings_calendar(("WAL",)), self.ROWS)

    def test_available_true_for_30h_old_snapshot(self):
        self.assertTrue(est.earnings_calendar_available())


class TestSectorValHistory(_IsolatedCache):
    def _seed(self, n_records: int):
        today = date.today()
        recs = [{"date": (today - timedelta(days=n_records - i)).isoformat(),
                 "tiers": {}} for i in range(n_records)]
        cache.put("sector_val_hist", {"records": recs, "cached_at": _stamp()})
        self._age("sector_val_hist", 25 * 3600)      # one skipped-run gap
        return recs

    def test_job_append_keeps_25h_old_history(self):
        self._seed(100)
        rhs._append_sector_val_history([])
        new = cache.get("sector_val_hist", max_age_s=None)
        self.assertEqual(len(new["records"]), 101)   # appended, not reset to 1
        self.assertEqual(new["records"][-1]["date"], date.today().isoformat())

    def test_home_reader_serves_25h_old_history(self):
        recs = self._seed(3)
        self.assertEqual(home._sector_val_history()["records"], recs)


class TestIrEndpointsSnapshot(_IsolatedCache):
    def test_discovered_endpoints_survive_30h(self):
        cache.put(irs._IR_ENDPOINTS_CACHE_KEY,
                  {"endpoints": {"ONB": "https://ir.oldnational.com/"},
                   "cached_at": _stamp()})
        self._age(irs._IR_ENDPOINTS_CACHE_KEY, _AGE_S)
        with patch.object(irs, "IR_URLS", {}):
            eps = irs.get_ir_endpoints()
        self.assertEqual(eps, {"ONB": "https://ir.oldnational.com/"})


class TestQ4CallsSnapshot(_IsolatedCache):
    PREV = {"JPM": {"call_date": "2026-10-14", "call_time": "8:30a ET"}}

    def setUp(self):
        super().setUp()
        cache.put(irs._Q4_CALLS_SNAP_KEY, {"value": self.PREV, "cached_at": _stamp()})
        self._age(irs._Q4_CALLS_SNAP_KEY, _AGE_S)

    def test_reader_serves_30h_old_snapshot(self):
        self.assertEqual(irs.get_q4_call_details(), self.PREV)

    def test_all_fail_scan_does_not_clobber_30h_old_snapshot(self):
        with patch.object(irs, "get_ir_endpoints",
                          return_value={"JPM": "https://investor.example/"}), \
                patch.object(irs, "_q4_events", side_effect=RuntimeError("down")):
            out = irs.refresh_q4_calls_snapshot()
        self.assertEqual(out, {})
        kept = cache.get(irs._Q4_CALLS_SNAP_KEY, max_age_s=None)
        self.assertEqual(kept["value"], self.PREV)   # last-good kept, not {}


class TestPrCallSnapshot(_IsolatedCache):
    def test_reader_serves_30h_old_snapshot(self):
        val = {"BANR": {"release_date": "2026-10-21", "dial_in": "800-555-0100"}}
        cache.put("pr_call_snap", {"value": val, "cached_at": _stamp()})
        self._age("pr_call_snap", _AGE_S)
        self.assertEqual(ecall.get_pr_call_details(), val)


def _grid_payload(age_s: float) -> dict:
    return {"labels": ["Q2 2026", "Q1 2026"],
            "rows": [{"ticker": "ABC", "_fdic_cert": 1,
                      "series": {"roaa": [1.1, 1.0]}}],
            "coverage": 1.0, "persisted": True, "cached_at": _stamp(age_s)}


class TestAllBanksGrids(_IsolatedCache):
    """The pre-warmed grids' own policy is _GRID_TTL_S (36h); a 30h-old grid is
    fresh by that policy and must be served, not reported 'not warmed'."""

    def test_window_is_inside_module_policy(self):
        self.assertLess(_AGE_S, aom._GRID_TTL_S)

    def test_fdic_trend_grid_served_at_30h(self):
        key = "trend_series:ALLBANKS:20"
        cache.put(key, _grid_payload(_AGE_S))
        self._age(key, _AGE_S)
        out = aom.quarterly_series({}, 20, build_if_missing=False, scope_id="ALLBANKS")
        self.assertIsNotNone(out, "30h-old ALLBANKS grid read as 'not warmed'")
        self.assertEqual(out["rows"][0]["ticker"], "ABC")

    def test_sec_pershare_grid_served_at_30h(self):
        # Version-agnostic: read the CURRENT grid-spec version out of the module
        # (v4->v5 bumps are intentional and must not look like a regression).
        ver = re.search(r'key = f"sec_pershare:(v\d+):',
                        (REPO / "data/sec_per_share.py").read_text(encoding="utf-8"))
        self.assertIsNotNone(ver, "sec_per_share cache key shape changed")
        key = f"sec_pershare:{ver.group(1)}:ALLBANKS:20"
        cache.put(key, _grid_payload(_AGE_S))
        self._age(key, _AGE_S)
        out = sps.sec_per_share_grid({}, 20, build_if_missing=False, scope_id="ALLBANKS")
        self.assertIsNotNone(out, "30h-old ALLBANKS SEC grid read as 'not warmed'")
        self.assertEqual(out["rows"][0]["ticker"], "ABC")


if __name__ == "__main__":
    unittest.main()
