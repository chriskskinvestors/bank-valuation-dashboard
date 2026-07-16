"""Pins the OTC (non-SEC filer) earnings-release locator + extraction wrapper
(data/otc_release.py, 2026-07-16). PBAM-class banks publish no EDGAR filings;
their wire press release is the primary disclosure and the ONLY per-share
source, so the locator's gating is a cardinal-rule surface: a scheduling
notice or product PR must never be extracted as "the release".

Run: python -m unittest tests.test_otc_release
"""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
sys.modules.setdefault("streamlit", _st)


class TestHeadlineDateNotice(unittest.TestCase):
    def test_date_announcement_refused(self):
        # Passed the triple gate live 2026-07-16 (OZK) — has announces +
        # quarter + earnings, but it's a scheduling notice.
        from data.ir_provider import _is_earnings_headline
        self.assertFalse(_is_earnings_headline(
            "Bank OZK Announces Date for Second Quarter 2026 Earnings "
            "Release and Conference Call"))

    def test_real_release_with_call_boilerplate_passes(self):
        from data.ir_provider import _is_earnings_headline
        self.assertTrue(_is_earnings_headline(
            "XYZ Bancorp Reports Second Quarter 2026 Results. The Company "
            "will host a conference call today at 5:00 pm ET"))


class TestLatestEarningsPr(unittest.TestCase):
    def setUp(self):
        import data.fmp_client as fc
        self._orig = fc.get_press_releases
        self.fc = fc

    def tearDown(self):
        self.fc.get_press_releases = self._orig

    def _patch(self, prs):
        self.fc.get_press_releases = lambda t, limit=25: prs

    def test_picks_newest_gated_release(self):
        from data.otc_release import _latest_earnings_pr
        self._patch([
            {"title": "CalPrivate Bank Appoints General Counsel",
             "url": "u1", "published_at": "2026-07-17 09:00:00"},
            {"title": "Private Bancorp of America, Inc. Announces Continued "
                      "Strong Net Income for Second Quarter 2026",
             "url": "u2", "published_at": "2026-07-16 08:00:00"},
            {"title": "Private Bancorp of America Announces First Quarter "
                      "2026 Results", "url": "u3",
             "published_at": "2026-04-16 08:00:00"},
        ])
        got = _latest_earnings_pr("PBAM")
        self.assertEqual(got["url"], "u2")

    def test_date_notice_and_missing_fields_skipped(self):
        from data.otc_release import _latest_earnings_pr
        self._patch([
            {"title": "Bank OZK Announces Date for Second Quarter 2026 "
                      "Earnings Release", "url": "u1",
             "published_at": "2026-06-30 08:00:00"},
            {"title": "Bank OZK Reports First Quarter 2026 Earnings",
             "url": None, "published_at": "2026-04-17 08:00:00"},
        ])
        self.assertIsNone(_latest_earnings_pr("OZK"))


class TestOtcWrapper(unittest.TestCase):
    def setUp(self):
        import data.cache as dc
        import data.otc_release as orl
        self.orl, self.store = orl, {}
        self._orig = (dc.get, dc.put, orl._latest_earnings_pr,
                      orl._fetch_story)
        dc.get = lambda k: self.store.get(k)
        dc.put = lambda k, v: self.store.__setitem__(k, v)

    def tearDown(self):
        import data.cache as dc
        (dc.get, dc.put, self.orl._latest_earnings_pr,
         self.orl._fetch_story) = self._orig

    def test_extracts_and_labels_source(self):
        self.orl._latest_earnings_pr = lambda t: {
            "title": "T Reports Second Quarter 2026 Results", "url": "u1",
            "published_at": "2026-07-16 08:00:00"}
        self.orl._fetch_story = lambda u: (
            "<p>Net interest margin of 5.18% for the quarter. Tangible book "
            "value per share was $49.57.</p>")
        val = self.orl.otc_release_metrics("PBAM")
        self.assertEqual(val["source"], "company_release")
        self.assertEqual(val["qend"], "2026-06-30")
        self.assertEqual(val["metrics"]["nim"], 5.18)
        self.assertEqual(val["metrics"]["tbv_ps"], 49.57)

    def test_same_url_restamps_without_refetch(self):
        self.orl._latest_earnings_pr = lambda t: {
            "title": "x", "url": "u1", "published_at": "2026-07-16 08:00:00"}
        fetches = []
        self.orl._fetch_story = lambda u: fetches.append(u) or "<p>x</p>"
        self.store["otc_release:v3:PBAM"] = {
            "cached_at": "2020-01-01T00:00:00",       # stale ⇒ re-check
            "value": {"url": "u1", "metrics": {"nim": 5.18}}}
        val = self.orl.otc_release_metrics("PBAM")
        self.assertEqual(val["metrics"], {"nim": 5.18})
        self.assertEqual(fetches, [])                 # no page load

    def test_fetch_failure_serves_previous(self):
        self.orl._latest_earnings_pr = lambda t: {
            "title": "x", "url": "u2", "published_at": "2026-07-16 08:00:00"}
        self.orl._fetch_story = lambda u: None
        self.store["otc_release:v3:PBAM"] = {
            "cached_at": "2020-01-01T00:00:00",
            "value": {"url": "u1", "metrics": {"nim": 5.0}}}
        val = self.orl.otc_release_metrics("PBAM")
        self.assertEqual(val["metrics"], {"nim": 5.0})


if __name__ == "__main__":
    unittest.main()
