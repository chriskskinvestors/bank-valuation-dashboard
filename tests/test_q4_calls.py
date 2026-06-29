"""
Unit tests for the Q4 IR events path — the structured earnings-call details
(date / time / webcast link) that fill the Calls & Webcasts columns the press-
release parser leaves blank.

Covers data.events.ir_site (_q4_call_time, _q4_events, refresh_q4_calls_snapshot)
and data.earnings_call.merged_call_info (Q4 layered over the PR parser).
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.events.ir_site as ir  # noqa: E402
import data.earnings_call as ec  # noqa: E402


class TestQ4SiteDetection(unittest.TestCase):
    """The bug that hid EQBK: a Q4 site whose homepage doesn't inline the apiKey
    (modern Q4) was treated as NOT a Q4 site, so discovery and polling skipped it.
    Detection must key off the Q4 marker, not the apiKey."""

    def setUp(self):
        import data.cache as dc
        self._fetch, self._dc = ir._fetch, dc
        self._get, self._put = dc.get, dc.put
        dc.get = lambda k: None          # force fresh detection (no cache hit)
        dc.put = lambda k, v: None

    def tearDown(self):
        ir._fetch = self._fetch
        self._dc.get, self._dc.put = self._get, self._put

    def test_marker_without_apikey_is_still_q4(self):
        ir._fetch = lambda url, timeout=5: '<script src="https://q4cdn.com/a.js"></script>'
        is_q4, key = ir._q4_site("https://investor.x.com")
        self.assertTrue(is_q4)           # detected by marker …
        self.assertIsNone(key)           # … even with no inline key (the fix)

    def test_marker_with_apikey_extracted(self):
        ir._fetch = lambda url, timeout=5: 'q4inc config apiKey: "abcdef0123456789abcdef01"'
        self.assertEqual(ir._q4_site("https://investor.x.com"),
                         (True, "abcdef0123456789abcdef01"))

    def test_non_q4_site(self):
        ir._fetch = lambda url, timeout=5: "<html>just a plain IR page</html>"
        self.assertEqual(ir._q4_site("https://investor.x.com"), (False, None))


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class TestQ4CallTime(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(ir._q4_call_time("07/15/2026 09:00:00", "ET"), "9:00a ET")
        self.assertEqual(ir._q4_call_time("07/14/2026 14:30:00", "CT"), "2:30p CT")
        self.assertEqual(ir._q4_call_time("07/14/2026 12:00:00", "ET"), "12:00p ET")
        # Midnight stamp = the event carries only a date, no time-of-day.
        self.assertIsNone(ir._q4_call_time("07/14/2026 00:00:00", "ET"))
        self.assertIsNone(ir._q4_call_time("", "ET"))


class TestQ4Events(unittest.TestCase):
    def setUp(self):
        self._site, self._req = ir._q4_site, ir.requests
        ir._q4_site = lambda home: (True, None)   # treat as Q4, skip homepage fetch

    def tearDown(self):
        ir._q4_site, ir.requests = self._site, self._req

    def _patch(self, payload):
        class _R:
            @staticmethod
            def get(url, params=None, timeout=None, headers=None):
                return _Resp(payload)
        ir.requests = _R

    def test_keeps_only_earnings_calls_with_fields(self):
        self._patch({"GetEventListResult": [
            {"SeoName": "Q2-2026-Acme-Bancorp-Earnings-Conference-Call",
             "Title": "", "StartDate": "07/15/2026 09:00:00", "TimeZone": "ET",
             "WebCastLink": "https://events.q4inc.com/attendee/123",
             "LinkToDetailPage": "/events/q2/default.aspx"},
            {"SeoName": "Annual-Shareholder-Meeting", "Title": "",
             "StartDate": "05/01/2026 10:00:00", "TimeZone": "ET",
             "WebCastLink": "https://x/y", "LinkToDetailPage": "/agm"},
            {"SeoName": "Investor-Day-Earnings", "Title": "",
             "StartDate": "08/01/2026 08:00:00", "TimeZone": "ET",
             "WebCastLink": "javascript:void(0)", "LinkToDetailPage": "/id"},
        ]})
        evs = ir._q4_events("https://investor.acme.com")
        # AGM dropped (not an earnings call); two earnings-named events kept.
        names = len(evs)
        self.assertEqual(names, 2)
        e0 = next(e for e in evs if e["start"].month == 7)
        self.assertEqual(e0["call_time"], "9:00a ET")
        self.assertEqual(e0["webcast_url"], "https://events.q4inc.com/attendee/123")
        self.assertEqual(e0["detail_url"],
                         "https://investor.acme.com/events/q2/default.aspx")
        # Non-http webcast (javascript:) is dropped to None.
        e1 = next(e for e in evs if e["start"].month == 8)
        self.assertIsNone(e1["webcast_url"])

    def test_unreachable_returns_none(self):
        class _R:
            @staticmethod
            def get(*a, **k):
                raise RuntimeError("boom")
        ir.requests = _R
        self.assertIsNone(ir._q4_events("https://investor.acme.com"))


class TestRefreshSnapshot(unittest.TestCase):
    def setUp(self):
        self._eps, self._evs, self._ann = (
            ir.get_ir_endpoints, ir._q4_events, ir._q4_announcement)
        ir.get_ir_endpoints = lambda: {"ACME": "https://investor.acme.com",
                                       "NONE": "https://investor.none.com"}
        ir._q4_announcement = lambda url, today_iso: None   # no PR body in this test

    def tearDown(self):
        ir.get_ir_endpoints, ir._q4_events = self._eps, self._evs
        ir._q4_announcement = self._ann

    def test_picks_soonest_upcoming_and_skips_empty(self):
        now = datetime.now(timezone.utc)
        soon = now + timedelta(days=20)
        later = now + timedelta(days=110)

        def fake_events(url):
            if "acme" in url:
                return [
                    {"start": later, "call_time": "8:00a ET",
                     "webcast_url": "https://events.q4inc.com/late", "detail_url": "x"},
                    {"start": soon, "call_time": "9:00a ET",
                     "webcast_url": "https://events.q4inc.com/soon", "detail_url": "y"},
                ]
            return []                              # NONE bank: no events
        ir._q4_events = fake_events

        captured = {}
        import data.cache as dc
        orig_put = dc.put
        dc.put = lambda k, v: captured.update({k: v})
        try:
            out = ir.refresh_q4_calls_snapshot()
        finally:
            dc.put = orig_put

        self.assertIn("ACME", out)
        self.assertNotIn("NONE", out)             # no events → omitted
        self.assertEqual(out["ACME"]["call_time"], "9:00a ET")   # soonest wins
        self.assertEqual(out["ACME"]["webcast_url"], "https://events.q4inc.com/soon")
        self.assertEqual(captured.get("q4_calls_snap", {}).get("value"), out)


class TestMergedCallInfo(unittest.TestCase):
    def setUp(self):
        self._cim = ec.call_info_map

    def tearDown(self):
        ec.call_info_map = self._cim

    def test_q4_wins_time_and_webcast_pr_keeps_dialin(self):
        ec.call_info_map = lambda: {
            "EQBK": {"call_time": None, "webcast_url": None, "dial_in": "+1-800-555-1212"},
            "PRONLY": {"call_time": "7:00a ET", "webcast_url": "https://pr/wc", "dial_in": None},
        }
        ir.get_q4_call_details = lambda: {
            "EQBK": {"call_time": "9:00a ET", "webcast_url": "https://events.q4inc.com/x",
                     "call_date": "2026-07-15", "detail_url": "z"},
        }
        try:
            m = ec.merged_call_info()
        finally:
            if hasattr(ir, "get_q4_call_details"):
                pass
        # Q4 supplies the call time + webcast EQBK's PR lacked; dial-in preserved.
        self.assertEqual(m["EQBK"]["call_time"], "9:00a ET")
        self.assertEqual(m["EQBK"]["webcast_url"], "https://events.q4inc.com/x")
        self.assertEqual(m["EQBK"]["dial_in"], "+1-800-555-1212")
        # A bank with only PR-parsed info and no Q4 event is unchanged.
        self.assertEqual(m["PRONLY"]["call_time"], "7:00a ET")


class TestQ4Announcement(unittest.TestCase):
    """The clean Q4 PressRelease-API body parse (the reliable Q4-bank source)."""

    def setUp(self):
        self._site, self._req = ir._q4_site, ir.requests
        ir._q4_site = lambda home: (True, "")

    def tearDown(self):
        ir._q4_site, ir.requests = self._site, self._req

    def test_parses_clean_body(self):
        payload = {"GetPressReleaseListResult": [{
            "Headline": "Acme Bancorp Announces Schedule for Second Quarter 2026 Results",
            "Body": "<p>Acme will release its second quarter 2026 results on Thursday, "
                    "July 23, 2026, after the close. The company will host a conference "
                    "call on Friday, July 24, 2026 at 11:00 a.m. ET. Dial-in 888-555-1212.</p>"}]}

        class _R:
            @staticmethod
            def get(url, params=None, timeout=None, headers=None):
                return _Resp(payload)
        ir.requests = _R
        info = ir._q4_announcement("https://investor.acme.com", "2026-06-29")
        self.assertEqual(info["release_date"], "2026-07-23")
        self.assertEqual(info["call_date"], "2026-07-24")
        self.assertEqual(info["call_time"], "11:00a ET")
        self.assertEqual(info["dial_in"], "888-555-1212")

    def test_non_q4_returns_none(self):
        ir._q4_site = lambda home: (False, None)
        self.assertIsNone(ir._q4_announcement("https://x", "2026-06-29"))


if __name__ == "__main__":
    unittest.main()
