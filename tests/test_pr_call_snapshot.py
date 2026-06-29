"""
Unit tests for the universe-wide full-body PR call-detail pipeline
(data.earnings_call): _fetch_pr_body, refresh_pr_call_snapshot, and the
merged_call_info layering (snippet < PR body < Q4 events).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.earnings_call as ec  # noqa: E402


class TestFetchPrBody(unittest.TestCase):
    def setUp(self):
        import data.events.ir_site as ir
        self._ir = ir
        self._orig = ir._fetch

    def tearDown(self):
        self._ir._fetch = self._orig

    def test_inlines_hrefs_and_strips_tags(self):
        self._ir._fetch = lambda url, timeout=8: (
            '<div>Webcast: <a href="https://events.q4inc.com/x">click here</a>'
            "</div><p>Dial-in 1-800-555-1212</p>")
        body = ec._fetch_pr_body("http://pr")
        self.assertIn("https://events.q4inc.com/x", body)   # href survived
        self.assertNotIn("<a", body)                         # tags stripped
        self.assertNotIn("<div", body)

    def test_empty_on_fetch_failure(self):
        self._ir._fetch = lambda url, timeout=8: None
        self.assertEqual(ec._fetch_pr_body("http://pr"), "")


class TestRefreshPrCallSnapshot(unittest.TestCase):
    def setUp(self):
        import data.events.store as store
        import data.cache as dc
        self._store, self._dc = store, dc
        self._g, self._f, self._p = store.get_events_by_type, ec._fetch_pr_body, dc.put

    def tearDown(self):
        self._store.get_events_by_type = self._g
        ec._fetch_pr_body = self._f
        self._dc.put = self._p

    def test_picks_announcement_fetches_body_parses_and_stores(self):
        self._store.get_events_by_type = lambda et, limit=800: [
            {"ticker": "BANR", "url": "http://pr/banr",
             "headline": "Banner Will Announce Q2 2026 Results on July 24, 2026",
             "summary": ""},
            {"ticker": "ZZZ", "url": None, "headline": "no url", "summary": ""},
            {"ticker": "REC", "url": "http://pr/rec",
             "headline": "Recap Bancorp Reports Second Quarter 2026 Results",
             "summary": "results recap, no future date"},
        ]
        ec._fetch_pr_body = lambda url: (
            "Banner will host a conference call on July 25, 2026 at 11:00 a.m. PT. "
            "Webcast: https://events.q4inc.com/banr . Dial-in 1-800-555-0100.")
        cap = {}
        self._dc.put = lambda k, v: cap.update({k: v})

        out = ec.refresh_pr_call_snapshot()
        self.assertIn("BANR", out)
        self.assertNotIn("ZZZ", out)                         # no url → skipped
        self.assertNotIn("REC", out)                         # recap, not an announcement
        self.assertEqual(out["BANR"]["release_date"], "2026-07-24")  # from headline
        self.assertEqual(out["BANR"]["call_date"], "2026-07-25")     # from body
        self.assertEqual(out["BANR"]["webcast_url"], "https://events.q4inc.com/banr")
        self.assertEqual(out["BANR"]["call_time"], "11:00a PT")
        self.assertEqual(cap.get("pr_call_snap", {}).get("value"), out)

    def test_no_date_headline_is_picked_and_body_parsed(self):
        # "Announces Schedule for … Results" — no date in the headline; it must
        # still be picked and the dates parsed from the body (the SBFG/HFWA case).
        self._store.get_events_by_type = lambda et, limit=800: [
            {"ticker": "SBFG", "url": "http://pr/sbfg",
             "headline": "SB Financial Group Announces Schedule for Second "
                         "Quarter 2026 Results", "summary": ""},
        ]
        ec._fetch_pr_body = lambda url: (
            "expects to release its second quarter 2026 results on Thursday, "
            "July 23, 2026, after the close. The company will hold a conference "
            "call on Friday, July 24, 2026 at 11:00 a.m. EDT. "
            "Webcast: https://events.q4inc.com/sbfg")
        self._dc.put = lambda k, v: None
        out = ec.refresh_pr_call_snapshot()
        self.assertIn("SBFG", out)
        self.assertEqual(out["SBFG"]["release_date"], "2026-07-23")
        self.assertEqual(out["SBFG"]["call_date"], "2026-07-24")
        self.assertEqual(out["SBFG"]["webcast_url"], "https://events.q4inc.com/sbfg")


class TestMergedLayering(unittest.TestCase):
    def setUp(self):
        import data.events.ir_site as ir
        self._ir = ir
        self._cim, self._pr, self._q4 = (
            ec.call_info_map, ec.get_pr_call_details, ir.get_q4_call_details)

    def tearDown(self):
        ec.call_info_map = self._cim
        ec.get_pr_call_details = self._pr
        self._ir.get_q4_call_details = self._q4

    def test_q4_over_pr_over_snippet(self):
        ec.call_info_map = lambda: {"AAA": {"call_time": "8:00a ET",
                                            "dial_in": "snippet-dial"}}
        ec.get_pr_call_details = lambda: {
            "AAA": {"webcast_url": "https://pr/wc", "dial_in": "pr-dial"},
            "BBB": {"release_date": "2026-07-20"}}
        self._ir.get_q4_call_details = lambda: {
            "AAA": {"webcast_url": "https://q4/wc", "call_time": "9:00a ET"}}
        m = ec.merged_call_info()
        # Q4 wins webcast + call_time:
        self.assertEqual(m["AAA"]["webcast_url"], "https://q4/wc")
        self.assertEqual(m["AAA"]["call_time"], "9:00a ET")
        # PR overrides the snippet dial-in (Q4 has none):
        self.assertEqual(m["AAA"]["dial_in"], "pr-dial")
        # A PR-only ticker still surfaces:
        self.assertEqual(m["BBB"]["release_date"], "2026-07-20")


if __name__ == "__main__":
    unittest.main()
