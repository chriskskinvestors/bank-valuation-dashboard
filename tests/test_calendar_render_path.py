"""
Render-path contract for Home's Calendar pane (the 2026-08-24 stall).

Home's grid renders col1 -> col2 -> col3 in script order, so the Bank News
Feed (col3) is the LAST thing on the page and sits directly behind the
Calendar pane (col2). Two calendar data sources fetched live whenever their
Postgres cache lapsed, ON the render thread:

  • econ_calendar.get_us_calendar   — 2 FMP calls, timeout=20 x 3 attempts
  • macro_calendar._fetch_release_dates — 7 serial FRED calls (one per
    TRACKED_RELEASE), timeout=15 x 3 attempts + backoff, and NOTHING warmed it

Measured in prod: home.af.calendar 112091ms / 117997ms / 350716ms against a
documented 3s budget. While it stalled, the Calendar pane AND the news feed
below it simply never rendered — column 3 sat empty for minutes with no error
logged anywhere, because execution had not reached it.

These pin the fix: the render passes cache_only=True, which serves whatever is
cached at ANY age and never fetches; jobs/refresh_macro does the fetching. Same
doctrine as fetch_earnings_calendar (2026-06-13) and fmp_announcement_call_info
(2026-08-17), which were both fixed for this identical failure shape.
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

# Order-independent streamlit stub (shared helper).
from tests import _streamlit_stub

_streamlit_stub.install()

from data import econ_calendar, macro_calendar  # noqa: E402


def _stale(payload: dict) -> dict:
    """A cache blob whose cached_at is far older than any TTL here."""
    return dict(payload,
                cached_at=(datetime.now() - timedelta(days=30)).isoformat())


_EVENT = {"date": "2026-08-25", "event": "CPI", "released": False,
          "datetime": "2026-08-25T08:30:00", "estimate": 0.2,
          "previous": 0.1, "unit": "%"}


class TestEconCalendarCacheOnly(unittest.TestCase):
    """get_us_calendar(cache_only=True) must never reach FMP."""

    @patch("data.econ_calendar._get")
    def test_stale_cache_is_served_without_fetching(self, mock_get):
        stale = _stale({"events": [_EVENT]})
        with patch("data.cache.get", return_value=stale):
            out = econ_calendar.get_us_calendar(cache_only=True)
        self.assertEqual(out, [_EVENT])
        mock_get.assert_not_called()

    @patch("data.econ_calendar._get")
    def test_no_cache_returns_empty_without_fetching(self, mock_get):
        with patch("data.cache.get", return_value=None):
            out = econ_calendar.get_us_calendar(cache_only=True)
        self.assertEqual(out, [])
        mock_get.assert_not_called()

    @patch("data.econ_calendar._get")
    def test_upcoming_releases_passes_the_flag_through(self, mock_get):
        stale = _stale({"events": [_EVENT]})
        with patch("data.cache.get", return_value=stale):
            out = econ_calendar.get_upcoming_releases(days=30, cache_only=True)
        mock_get.assert_not_called()
        self.assertEqual([e["event"] for e in out], ["CPI"])

    @patch("data.cache.put")
    @patch("data.econ_calendar._get", return_value=[])
    def test_warm_path_still_fetches_when_stale(self, mock_get, _put):
        """The job path (cache_only=False) must keep rebuilding — otherwise
        nothing would ever refill the cache the render now depends on."""
        with patch("data.cache.get", return_value=_stale({"events": []})):
            econ_calendar.get_us_calendar()
        self.assertEqual(mock_get.call_count, 2)   # both FMP calendar paths


class TestMacroCalendarCacheOnly(unittest.TestCase):
    """get_upcoming_prints(cache_only=True) must never fan out to FRED."""

    def _dates(self, days_out: int) -> dict:
        iso = (datetime.now() + timedelta(days=days_out)).date().isoformat()
        return {str(macro_calendar.TRACKED_RELEASES[0]["release_id"]): [iso]}

    @patch("data.http.get_with_retry")
    @patch.object(macro_calendar, "FOMC_DECISION_DATES", [])
    def test_stale_cache_is_served_without_fetching(self, mock_http):
        stale = _stale({"by_release": self._dates(3)})
        with patch("data.cache.get", return_value=stale):
            out = macro_calendar.get_upcoming_prints(days=14, cache_only=True)
        mock_http.assert_not_called()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"],
                         macro_calendar.TRACKED_RELEASES[0]["name"])

    @patch("data.http.get_with_retry")
    @patch.object(macro_calendar, "FOMC_DECISION_DATES", [])
    def test_no_cache_returns_empty_without_fetching(self, mock_http):
        with patch("data.cache.get", return_value=None):
            out = macro_calendar.get_upcoming_prints(days=14, cache_only=True)
        self.assertEqual(out, [])
        mock_http.assert_not_called()

    @patch("data.cache.put")
    @patch("data.http.get_with_retry")
    @patch.dict("os.environ", {"FRED_API_KEY": "test-key"})
    def test_warm_path_still_fans_out(self, mock_http, _put):
        """The job path keeps fetching all 7 releases — that is what refills
        the cache the render reads."""
        resp = MagicMock()
        resp.json.return_value = {"release_dates": []}
        mock_http.return_value = resp
        with patch("data.cache.get", return_value=None):
            macro_calendar.get_upcoming_prints(days=14)
        self.assertEqual(mock_http.call_count,
                         len(macro_calendar.TRACKED_RELEASES))


class TestCallInfoMapIsSnapshotOnly(unittest.TestCase):
    """call_info_map was the LAST call-detail source still building inline:
    an 800-row events query with no event_type index, under Streamlit's
    cache lock, so concurrent sessions queued behind one build (observed
    home.af.calendar 1065234ms / 138761ms / 78539ms flushing at the same
    instant). Its three siblings were already snapshot-only."""

    def test_serves_snapshot_at_any_age_without_querying(self):
        from data import earnings_call as ec

        stale = _stale({"value": {"ABC": {"call_time": "9:00 AM ET"}}})
        with patch("data.cache.get", return_value=stale), \
             patch("data.events.store.get_events_by_type") as mock_q:
            out = ec.call_info_map()
        mock_q.assert_not_called()
        self.assertEqual(out, {"ABC": {"call_time": "9:00 AM ET"}})

    def test_no_snapshot_degrades_to_empty_without_querying(self):
        from data import earnings_call as ec

        with patch("data.cache.get", return_value=None), \
             patch("data.events.store.get_events_by_type") as mock_q:
            out = ec.call_info_map()
        self.assertEqual(out, {})
        mock_q.assert_not_called()

    def test_refresher_builds_and_persists(self):
        """The job path must still do the query — it is what fills the
        snapshot the render now reads."""
        from data import earnings_call as ec

        rows = [{"ticker": "ABC", "summary": "", "headline": ""}]
        with patch("data.events.store.get_events_by_type",
                   return_value=rows) as mock_q, \
             patch("data.earnings_call._announced_release_date",
                   return_value="2026-09-01"), \
             patch("data.cache.put") as mock_put:
            out = ec.refresh_call_info_snapshot()
        mock_q.assert_called_once()
        self.assertEqual(out, {"ABC": {"release_date": "2026-09-01"}})
        key, blob = mock_put.call_args[0]
        self.assertEqual(key, ec.CALL_INFO_SNAP_KEY)
        self.assertEqual(blob["value"], out)

    def test_failed_build_never_overwrites_the_snapshot(self):
        from data import earnings_call as ec

        with patch("data.events.store.get_events_by_type",
                   side_effect=RuntimeError("db down")), \
             patch("data.cache.put") as mock_put:
            out = ec.refresh_call_info_snapshot()
        self.assertEqual(out, {})
        mock_put.assert_not_called()


class TestHomeCalendarPaneDoesNoNetwork(unittest.TestCase):
    """The integration pin: with every cache cold, rendering the Calendar pane
    must not make a single outbound call. This is the bug — the pane blocked
    for minutes here and took the news feed below it down with it."""

    # FRED_API_KEY must be set or _fetch_release_dates returns early and the
    # fan-out leg is never exercised — the key IS set in prod, which is where
    # this hurt.
    @patch.dict("os.environ", {"FRED_API_KEY": "test-key"})
    def test_render_makes_no_outbound_calls(self):
        from ui import home

        # COUNT the calls, never raise: _af_calendar_table wraps each leg in
        # `except Exception: pass`, so a raising mock would be swallowed and
        # this test would pass even with the bug present.
        with patch("data.cache.get", return_value=None), \
             patch("data.http.get_with_retry") as mock_http, \
             patch("data.econ_calendar._get") as mock_fmp, \
             patch("data.events.store.get_events_by_type") as mock_events:
            html = home._af_calendar_table([])

        mock_http.assert_not_called()           # no FRED fan-out
        mock_fmp.assert_not_called()            # no FMP econ-calendar calls
        mock_events.assert_not_called()         # no inline 800-row events scan
        self.assertIn("Calendar", html)         # header always renders


if __name__ == "__main__":
    unittest.main()
