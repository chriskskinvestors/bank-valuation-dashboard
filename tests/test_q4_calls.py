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
        self._fetch, self._dc, self._probe = ir._fetch, dc, ir._q4_api_probe
        self._get, self._put = dc.get, dc.put
        dc.get = lambda k: None          # force fresh detection (no cache hit)
        dc.put = lambda k, v: None
        ir._q4_api_probe = lambda host: False   # no network in the marker tests

    def tearDown(self):
        ir._fetch, ir._q4_api_probe = self._fetch, self._probe
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

    def test_markerless_but_api_answers_is_q4(self):
        # No homepage marker (modern/WAF'd build, e.g. Glacier on www.<domain>) —
        # the functional API probe still identifies it as Q4.
        ir._fetch = lambda url, timeout=5: "<html>no marker here</html>"
        ir._q4_api_probe = lambda host: True
        self.assertEqual(ir._q4_site("https://www.glacierbancorp.com"), (True, None))

    def test_markerless_and_api_silent_is_not_q4(self):
        ir._fetch = lambda url, timeout=5: "<html>plain</html>"
        ir._q4_api_probe = lambda host: False
        self.assertEqual(ir._q4_site("https://www.plainbank.com"), (False, None))


class TestQ4ApiProbe(unittest.TestCase):
    """The functional Q4 detector — hits /feed/PressRelease.svc and checks for the
    Q4 envelope, so markerless / WAF'd Q4 sites aren't silently dropped."""

    def setUp(self):
        self._req = ir.requests

    def tearDown(self):
        ir.requests = self._req

    def _patch(self, status, payload):
        class _R:
            @staticmethod
            def get(url, params=None, timeout=None, headers=None):
                class _Resp:
                    status_code = status
                    @staticmethod
                    def json():
                        if payload is None:
                            raise ValueError("no json")
                        return payload
                return _Resp()
        ir.requests = _R

    def test_valid_q4_payload_is_true(self):
        self._patch(200, {"GetPressReleaseListResult": [{"Headline": "x"}]})
        self.assertTrue(ir._q4_api_probe("https://www.glacierbancorp.com"))

    def test_empty_result_list_still_true(self):
        self._patch(200, {"GetPressReleaseListResult": []})   # Q4 site, no PRs in window
        self.assertTrue(ir._q4_api_probe("https://www.x.com"))

    def test_non_200_is_false(self):
        self._patch(404, None)
        self.assertFalse(ir._q4_api_probe("https://www.x.com"))

    def test_non_q4_json_is_false(self):
        self._patch(200, {"something": "else"})
        self.assertFalse(ir._q4_api_probe("https://www.x.com"))

    def test_non_http_host_is_false(self):
        self.assertFalse(ir._q4_api_probe("not-a-url"))       # no network attempted


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

    def test_skips_stale_results_release(self):
        # A past-quarter RESULTS release matches _is_earnings_announcement
        # ("Announces Q1 Results") but has NO future date — its stale call
        # time/webcast must not leak onto the upcoming row (the EGBN bug).
        payload = {"GetPressReleaseListResult": [{
            "Headline": "Acme Bancorp, Inc. Announces First Quarter 2026 Results",
            "Body": "<p>Acme reported Q1 results. A conference call was held at "
                    "10:00 a.m. ET on Tuesday, April 22, 2025. Webcast: "
                    "https://events.q4inc.com/x</p>"}]}

        class _R:
            @staticmethod
            def get(url, params=None, timeout=None, headers=None):
                return _Resp(payload)
        ir.requests = _R
        self.assertIsNone(
            ir._q4_announcement("https://investor.acme.com", "2026-06-29"))

    def test_non_q4_returns_none(self):
        ir._q4_site = lambda home: (False, None)
        self.assertIsNone(ir._q4_announcement("https://x", "2026-06-29"))


class TestCuratedWebcasts(unittest.TestCase):
    """The hand-verified non-Q4 megabank webcast map — the bespoke-IR fallback for
    banks neither the Q4 API nor the HTML scrape reaches reliably."""

    def setUp(self):
        self._cim, self._pr, self._q4 = (
            ec.call_info_map, ec.get_pr_call_details, ir.get_q4_call_details)
        ec.get_pr_call_details = lambda: {}      # isolate from any cached snapshot
        ir.get_q4_call_details = lambda: {}

    def tearDown(self):
        ec.call_info_map = self._cim
        ec.get_pr_call_details = self._pr
        ir.get_q4_call_details = self._q4

    def test_only_webcast_url_safe_https(self):
        from data.events.wire_base import is_safe_news_url
        info = ir.get_curated_call_info()
        self.assertTrue(info)                                  # non-empty
        for tk, d in info.items():
            # Webcast link ONLY — never a release/call date or time, so a stale
            # entry can't ship a wrong number.
            self.assertEqual(set(d), {"webcast_url"})
            url = d["webcast_url"]
            self.assertTrue(url.startswith("https://"), url)
            self.assertTrue(is_safe_news_url(url), url)

    def test_curated_fills_megabank_and_overrides_junk_scrape(self):
        # PNC's scrape yielded a JUNK webcast (a nav link) + a real release date.
        ec.call_info_map = lambda: {
            "PNC": {"webcast_url": "https://www.pnc.com/corporate-profile",
                    "release_date": "2026-07-16"},
        }
        m = ec.merged_call_info()
        # JPM had no other source → gets the curated link.
        self.assertEqual(m["JPM"]["webcast_url"], ir.CURATED_WEBCASTS["JPM"])
        # PNC's junk scraped webcast is overridden by the curated link …
        self.assertEqual(m["PNC"]["webcast_url"], ir.CURATED_WEBCASTS["PNC"])
        # … but its real date is left untouched (curated carries no dates).
        self.assertEqual(m["PNC"]["release_date"], "2026-07-16")


class TestIRappAdapter(unittest.TestCase):
    """The second IR platform — BusinessWire / IRapp: press-release RSS + detail
    page, parsed with the shared body parsers (covers AUB and its cluster)."""

    def setUp(self):
        self._req, self._fetch = ir.requests, ec._fetch_pr_body

    def tearDown(self):
        ir.requests, ec._fetch_pr_body = self._req, self._fetch

    def _rss(self, status, text):
        resp = type("R", (), {"status_code": status, "text": text})()
        ir.requests = type("Req", (), {"get": staticmethod(lambda *a, **k: resp)})

    def test_parses_announcement_from_rss_and_detail(self):
        self._rss(200,
            "<rss><channel><item>"
            "<title>Acme Corp To Release Second Quarter 2026 Financial Results</title>"
            "<link>https://investors.acme.com/news-events/press-releases/detail/9/x</link>"
            "</item></channel></rss>")
        ec._fetch_pr_body = lambda url: (
            "Acme will release second quarter 2026 financial results before the market "
            "opens on Tuesday, July 21, 2026. The Company will host a conference call at "
            "9:00 a.m. Eastern Time on Tuesday, July 21, 2026. Webcast: "
            "https://edge.media-server.com/mmc/p/x")
        info = ir._irapp_announcement("https://investors.acme.com/", "2026-06-30")
        self.assertEqual(info["release_date"], "2026-07-21")
        self.assertEqual(info["call_date"], "2026-07-21")
        self.assertEqual(info["call_time"], "9:00a ET")
        self.assertEqual(info["when"], "Before open")
        self.assertEqual(info["webcast_url"], "https://edge.media-server.com/mmc/p/x")

    def test_non_rss_host_returns_none(self):
        self._rss(404, "Not Found")     # not the IRapp platform
        self.assertIsNone(ir._irapp_announcement("https://x.com/", "2026-06-30"))

    def test_skips_stale_results_item(self):
        # A past-quarter RESULTS item (no future date) must not leak its stale call.
        self._rss(200,
            "<item><title>Acme Corp Announces First Quarter 2026 Results</title>"
            "<link>https://investors.acme.com/detail/8/x</link></item>")
        ec._fetch_pr_body = lambda url: (
            "Acme reported Q1 results. A call was held at 9:00 a.m. ET on "
            "Tuesday, April 22, 2025.")
        self.assertIsNone(
            ir._irapp_announcement("https://investors.acme.com/", "2026-06-30"))


class TestIRappDiscovery(unittest.TestCase):
    """Auto-discovery of IRapp sites via the functional RSS probe — so IRapp banks
    are found without a curated IR_URLS entry (as Q4 sites already are)."""

    def setUp(self):
        import data.cache as dc
        self._dc, self._get, self._put = dc, dc.get, dc.put
        self._req, self._q4, self._ia = ir.requests, ir._q4_site, ir._irapp_site
        dc.get = lambda k: None          # force fresh probe (no cache hit)
        dc.put = lambda k, v: None

    def tearDown(self):
        self._dc.get, self._dc.put = self._get, self._put
        ir.requests, ir._q4_site, ir._irapp_site = self._req, self._q4, self._ia

    def _rss(self, status, text):
        resp = type("R", (), {"status_code": status, "text": text})()
        ir.requests = type("Req", (), {"get": staticmethod(lambda *a, **k: resp)})

    def test_irapp_site_true_on_rss_items(self):
        self._rss(200, "<rss><channel><item><title>x</title></item></channel></rss>")
        self.assertTrue(ir._irapp_site("https://investors.acme.com/"))

    def test_irapp_site_false_on_404(self):
        self._rss(404, "Not Found")
        self.assertFalse(ir._irapp_site("https://investors.acme.com/"))

    def test_discover_finds_irapp_subdomain(self):
        # Not a Q4 site; the investors. subdomain answers the IRapp RSS → found.
        ir._q4_site = lambda url: (False, None)
        ir._irapp_site = lambda url: url == "https://investors.acme.com/"
        self.assertEqual(ir.discover_q4_ir_url("acme.com"),
                         "https://investors.acme.com/")


if __name__ == "__main__":
    unittest.main()
