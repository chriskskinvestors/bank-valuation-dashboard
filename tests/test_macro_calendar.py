"""
Tests for data/macro_calendar.py — the FRED-based macro-print calendar
behind Home's "Today's Agenda" and Market & Macro's Economy section
(docs/HOME-MACRO-PLAN.md). All HTTP is mocked; the FRED
/fred/release/dates JSON is built in-memory per release id with dates
set relative to today so the window math is stable on any run date. Pins:

  • happy path: all 7 tracked releases fetched, entries shaped
    {date, name, release_id, kind, importance}, sorted by date then
    importance (high first)
  • importance tiers: CPI/PCE/NFP/GDP/FOMC high; Retail Sales/PPI/
    jobless claims medium
  • day filter: inclusive [today, today+days]; outside-window dropped
  • FOMC merge: static decision dates appear with kind="fomc",
    release_id None, importance high — and only inside the window
  • date parse: malformed/missing date strings are skipped, not raised
  • get_prints_for_date: one-day filter; accepts date, datetime, and
    ISO string; garbage day → []
  • [] (never raise) on missing FRED_API_KEY, HTTP failure, 429
    exhaustion
  • a fresh cache entry short-circuits the network entirely

Live smoke (TestLiveSmoke) runs only when FRED_API_KEY is set — it is
not set on this machine, so the suite is fully mocked here.
"""
import os
import sys
import types
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

# Stub streamlit before importing data modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from data import macro_calendar  # noqa: E402


def _iso(days_from_today: int) -> str:
    return (date.today() + timedelta(days=days_from_today)).isoformat()


def _fred_responder(dates_by_release: dict[int, list[str]]):
    """side_effect for get_with_retry: answer each release_id with its
    canned /fred/release/dates JSON."""
    def respond(url, params=None, **kwargs):
        rid = params["release_id"]
        r = MagicMock()
        r.json.return_value = {
            "release_dates": [{"release_id": rid, "date": d}
                              for d in dates_by_release.get(rid, [])]
        }
        return r
    return respond


KEY_ENV = {"FRED_API_KEY": "test-key"}


class TestGetUpcomingPrints(unittest.TestCase):

    @patch.dict(os.environ, KEY_ENV)
    @patch.object(macro_calendar, "FOMC_DECISION_DATES", [])
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_happy_path_shape_sort_and_tiers(self, mock_get, _cg, mock_cput):
        mock_get.side_effect = _fred_responder({
            10: [_iso(-30), _iso(2)],     # CPI: one past (filtered), one in window
            54: [_iso(2)],                # PCE same day as CPI
            50: [_iso(4)],                # NFP
            53: [_iso(40)],               # GDP beyond window
            9:  [_iso(2)],                # Retail Sales same day, medium
            46: [_iso(6)],                # PPI
            180: [_iso(0)],               # claims today
        })

        out = macro_calendar.get_upcoming_prints(days=7)
        self.assertEqual(mock_get.call_count, 7)  # one call per tracked release
        self.assertEqual(
            [(e["name"], e["date"]) for e in out],
            [("Jobless Claims", _iso(0)),
             # day +2: highs first (alphabetical within tier), then medium
             ("CPI", _iso(2)),
             ("PCE (Personal Income & Outlays)", _iso(2)),
             ("Retail Sales", _iso(2)),
             ("Employment Situation (NFP)", _iso(4)),
             ("PPI", _iso(6))])
        # Shape + tiers
        by_name = {e["name"]: e for e in out}
        cpi = by_name["CPI"]
        self.assertEqual(cpi, {"date": _iso(2), "name": "CPI", "release_id": 10,
                               "kind": "print", "importance": "high",
                               "time": "8:30 ET"})  # BLS 8:30am ET release
        self.assertEqual(by_name["PCE (Personal Income & Outlays)"]["importance"], "high")
        self.assertEqual(by_name["Employment Situation (NFP)"]["importance"], "high")
        self.assertEqual(by_name["Retail Sales"]["importance"], "medium")
        self.assertEqual(by_name["PPI"]["importance"], "medium")
        self.assertEqual(by_name["Jobless Claims"]["importance"], "medium")
        # Cached under the documented key with a cached_at stamp.
        key, payload = mock_cput.call_args[0]
        self.assertEqual(key, "macro_calendar:release_dates")
        self.assertIn("cached_at", payload)
        self.assertEqual(payload["by_release"]["10"], [_iso(-30), _iso(2)])

    @patch.dict(os.environ, KEY_ENV)
    @patch.object(macro_calendar, "FOMC_DECISION_DATES", [])
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_window_is_inclusive_both_ends(self, mock_get, _cg, _cp):
        mock_get.side_effect = _fred_responder({
            10: [_iso(-1), _iso(0), _iso(7), _iso(8)],
        })
        out = macro_calendar.get_upcoming_prints(days=7)
        self.assertEqual([e["date"] for e in out], [_iso(0), _iso(7)])

    @patch.dict(os.environ, KEY_ENV)
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_fomc_merge(self, mock_get, _cg, _cp):
        mock_get.side_effect = _fred_responder({10: [_iso(3)]})
        with patch.object(macro_calendar, "FOMC_DECISION_DATES",
                          [_iso(3), _iso(60)]):  # one in window, one beyond
            out = macro_calendar.get_upcoming_prints(days=7)
        fomc = [e for e in out if e["kind"] == "fomc"]
        self.assertEqual(fomc, [{"date": _iso(3), "name": "FOMC Rate Decision",
                                 "release_id": None, "kind": "fomc",
                                 "importance": "high",
                                 "time": "2:00 ET"}])  # FOMC statement drop
        # Same day as CPI: both high, alphabetical → CPI first.
        self.assertEqual([e["name"] for e in out],
                         ["CPI", "FOMC Rate Decision"])

    @patch.dict(os.environ, KEY_ENV)
    @patch.object(macro_calendar, "FOMC_DECISION_DATES", [])
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_malformed_dates_skipped_not_raised(self, mock_get, _cg, _cp):
        mock_get.side_effect = _fred_responder({
            10: ["06/15/2026", "", None, "2026-13-45", _iso(1)],
        })
        out = macro_calendar.get_upcoming_prints(days=7)
        self.assertEqual([e["date"] for e in out], [_iso(1)])

    @patch.dict(os.environ, KEY_ENV)
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_http_failure_returns_empty(self, mock_get, _cg, mock_cput):
        mock_get.side_effect = Exception("connection reset")
        self.assertEqual(macro_calendar.get_upcoming_prints(), [])
        mock_cput.assert_not_called()

    @patch.dict(os.environ, KEY_ENV)
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry", return_value=None)
    def test_retries_exhausted_returns_empty(self, _mg, _cg, mock_cput):
        self.assertEqual(macro_calendar.get_upcoming_prints(), [])
        mock_cput.assert_not_called()

    @patch.dict(os.environ, {}, clear=False)
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_missing_api_key_returns_empty_without_network(self, mock_get, _cg, mock_cput):
        os.environ.pop("FRED_API_KEY", None)
        self.assertEqual(macro_calendar.get_upcoming_prints(), [])
        self.assertEqual(mock_get.call_count, 0)
        mock_cput.assert_not_called()

    @patch.object(macro_calendar, "FOMC_DECISION_DATES", [])
    @patch("data.http.get_with_retry")
    @patch("data.cache.get")
    def test_cache_hit_short_circuits_network(self, mock_cget, mock_get):
        mock_cget.return_value = {
            "cached_at": datetime.now().isoformat(),
            "by_release": {"10": [_iso(2)]},
        }
        out = macro_calendar.get_upcoming_prints(days=7)
        self.assertEqual([e["name"] for e in out], ["CPI"])
        self.assertEqual(mock_get.call_count, 0)

    @patch.dict(os.environ, KEY_ENV)
    @patch.object(macro_calendar, "FOMC_DECISION_DATES", [])
    @patch("data.cache.put")
    @patch("data.cache.get")
    @patch("data.http.get_with_retry")
    def test_stale_cache_refetches(self, mock_get, mock_cget, _cp):
        mock_cget.return_value = {
            "cached_at": (datetime.now() - timedelta(days=2)).isoformat(),
            "by_release": {"10": ["1999-01-01"]},
        }
        mock_get.side_effect = _fred_responder({10: [_iso(2)]})
        out = macro_calendar.get_upcoming_prints(days=7)
        self.assertEqual([e["date"] for e in out], [_iso(2)])
        self.assertEqual(mock_get.call_count, 7)


class TestGetPrintsForDate(unittest.TestCase):

    @patch.dict(os.environ, KEY_ENV)
    @patch.object(macro_calendar, "FOMC_DECISION_DATES", [])
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_single_day_filter(self, mock_get, _cg, _cp):
        mock_get.side_effect = _fred_responder({
            10: [_iso(2)], 46: [_iso(2)], 50: [_iso(3)],
        })
        out = macro_calendar.get_prints_for_date(date.today() + timedelta(days=2))
        self.assertEqual([e["name"] for e in out], ["CPI", "PPI"])  # high first
        self.assertTrue(all(e["date"] == _iso(2) for e in out))

    @patch.dict(os.environ, KEY_ENV)
    @patch.object(macro_calendar, "FOMC_DECISION_DATES", [])
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_accepts_iso_string_and_datetime(self, mock_get, _cg, _cp):
        mock_get.side_effect = _fred_responder({10: [_iso(2)]})
        self.assertEqual(
            [e["name"] for e in macro_calendar.get_prints_for_date(_iso(2))],
            ["CPI"])
        dt = datetime.combine(date.today() + timedelta(days=2),
                              datetime.min.time())
        self.assertEqual(
            [e["name"] for e in macro_calendar.get_prints_for_date(dt)],
            ["CPI"])

    @patch.dict(os.environ, KEY_ENV)
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_fomc_day(self, mock_get, _cg, _cp):
        mock_get.side_effect = _fred_responder({})
        with patch.object(macro_calendar, "FOMC_DECISION_DATES", [_iso(5)]):
            out = macro_calendar.get_prints_for_date(_iso(5))
        self.assertEqual(out, [{"date": _iso(5), "name": "FOMC Rate Decision",
                                "release_id": None, "kind": "fomc",
                                "importance": "high",
                                "time": "2:00 ET"}])  # FOMC statement drop

    @patch("data.http.get_with_retry")
    @patch("data.cache.get", return_value=None)
    def test_garbage_day_returns_empty_without_network(self, _cg, mock_get):
        self.assertEqual(macro_calendar.get_prints_for_date("not-a-date"), [])
        self.assertEqual(macro_calendar.get_prints_for_date(None), [])
        self.assertEqual(mock_get.call_count, 0)

    @patch.dict(os.environ, KEY_ENV)
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_failure_returns_empty(self, mock_get, _cg, _cp):
        mock_get.side_effect = Exception("timeout")
        self.assertEqual(macro_calendar.get_prints_for_date(date.today()), [])


@unittest.skipUnless(os.environ.get("FRED_API_KEY"),
                     "FRED_API_KEY not set — live smoke skipped")
class TestLiveSmoke(unittest.TestCase):
    """One real round-trip when a key is available: a 90-day window must
    contain at least one CPI and one Employment Situation date (both are
    monthly), every entry well-shaped."""

    def test_live_upcoming_window(self):
        out = macro_calendar.get_upcoming_prints(days=90)
        self.assertTrue(out, "expected at least one print in 90 days")
        names = {e["name"] for e in out}
        self.assertIn("CPI", names)
        self.assertIn("Employment Situation (NFP)", names)
        for e in out:
            self.assertIsNotNone(macro_calendar._parse_date(e["date"]))
            self.assertIn(e["kind"], ("print", "fomc"))
            self.assertIn(e["importance"], ("high", "medium"))


if __name__ == "__main__":
    unittest.main()
