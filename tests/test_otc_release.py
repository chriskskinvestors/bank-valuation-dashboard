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

    def test_conference_call_notice_refused(self):
        # FRBA (2026-07-16 class sweep): "earnings" as a modifier of
        # "conference call" must not satisfy the results-word signal.
        from data.ir_provider import _is_earnings_headline
        self.assertFalse(_is_earnings_headline(
            "First Bank Announces Second Quarter 2026 Earnings "
            "Conference Call"))

    def test_genuine_earnings_with_call_details_passes(self):
        from data.ir_provider import _is_earnings_headline
        self.assertTrue(_is_earnings_headline(
            "XYZ Bancorp Reports Second Quarter Earnings and Conference "
            "Call Details"))


class TestLatestEarningsPr(unittest.TestCase):
    def setUp(self):
        import data.bank_mapping as bm
        import data.fmp_client as fc
        self._orig = (fc.get_press_releases, bm.get_name)
        self.fc, self.bm = fc, bm
        self.bm.get_name = lambda t: {
            "PBAM": "Private Bancorp of America",
            "OZK": "Bank OZK"}.get(t, t)

    def tearDown(self):
        self.fc.get_press_releases, self.bm.get_name = self._orig

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

    def test_other_companys_release_never_qualifies(self):
        # FMP's symbol index is polluted for short tickers — a wrong story
        # here puts ANOTHER COMPANY'S numbers on this bank's valuation.
        from data.otc_release import _latest_earnings_pr
        self._patch([
            {"title": "Acme Widgets Corp Reports Second Quarter 2026 "
                      "Results", "url": "u1",
             "published_at": "2026-07-17 08:00:00",
             "text": "Acme Widgets reported record net income."},
            {"title": "Private Bancorp of America Announces Second Quarter "
                      "2026 Results", "url": "u2",
             "published_at": "2026-07-16 08:00:00",
             "text": "CalPrivate Bank results."},
        ])
        got = _latest_earnings_pr("PBAM")
        self.assertEqual(got["url"], "u2")


class TestReleaseQend(unittest.TestCase):
    def test_title_period_governs_for_late_release(self):
        # A tiny OTC bank publishing its Q2 results LATE — Oct 2, where the
        # date-derived qend would be 2026-09-30 — must label values with the
        # title's own period, not the publish-date assumption.
        from data.otc_release import _release_qend
        self.assertEqual(
            _release_qend("X Bancorp Reports Second Quarter 2026 Results",
                          "2026-10-02"),
            "2026-06-30")

    def test_implausible_title_period_refused(self):
        from data.otc_release import _release_qend
        self.assertIsNone(
            _release_qend("X Bancorp Reports Fourth Quarter 2019 Results",
                          "2026-07-16"))

    def test_no_title_period_falls_back_to_date(self):
        from data.otc_release import _release_qend
        self.assertEqual(
            _release_qend("X Bancorp Announces Continued Strong Net Income",
                          "2026-07-16"),
            "2026-06-30")


class TestIrCandidateSelection(unittest.TestCase):
    """The IR-site locator's proofs: shared headline gate + a STATED period
    in the link text (PDFs have no publish date), newest period wins,
    ancient archives refused."""

    def setUp(self):
        import data.events.ir_site as irs
        import data.otc_release as orl
        self.irs, self.orl = irs, orl
        self._orig = (irs._fetch, orl._bank_webaddr)
        orl._bank_webaddr = lambda t: "https://www.examplebank.com"

    def tearDown(self):
        self.irs._fetch, self.orl._bank_webaddr = self._orig

    def _page(self, *links):
        html = "".join(f'<a href="{u}">{t}</a>' for u, t in links)
        self.irs._fetch = lambda url, timeout=12: (
            html if url.rstrip("/").endswith("news") else None)

    def test_newest_stated_period_wins_and_pdf_detected(self):
        from datetime import date
        y = date.today().year
        self._page(
            ("/pr/q1.pdf", f"Example Bank Reports First Quarter {y} Results"),
            ("/pr/q2.pdf", f"Example Bank Reports Second Quarter {y} Results"),
            ("/about.html", "About our community bank"))
        got = self.orl._latest_ir_release("EXBK")
        self.assertTrue(got["url"].endswith("q2.pdf"))
        self.assertEqual(got["kind"], "pdf")
        self.assertEqual(got["qend"], f"{y}-06-30")

    def test_link_without_stated_period_never_candidates(self):
        self._page(("/pr/latest.pdf", "Example Bank Reports Record Quarterly "
                                      "Earnings Results"))
        self.assertIsNone(self.orl._latest_ir_release("EXBK"))

    def test_ancient_archive_refused(self):
        self._page(("/pr/old.pdf", "Example Bank Reports Fourth Quarter 2022 "
                                   "Results"))
        self.assertIsNone(self.orl._latest_ir_release("EXBK"))


class TestOtcWrapper(unittest.TestCase):
    def setUp(self):
        import data.cache as dc
        import data.otc_release as orl
        self.orl, self.store = orl, {}
        self._orig = (dc.get, dc.put, orl._latest_earnings_pr,
                      orl._fetch_story, orl._latest_ir_release,
                      orl._fetch_document)
        dc.get = lambda k: self.store.get(k)
        dc.put = lambda k, v: self.store.__setitem__(k, v)
        orl._latest_ir_release = lambda t: None

    def tearDown(self):
        import data.cache as dc
        (dc.get, dc.put, self.orl._latest_earnings_pr,
         self.orl._fetch_story, self.orl._latest_ir_release,
         self.orl._fetch_document) = self._orig

    def test_ir_fallback_extracts_when_no_wire_release(self):
        from datetime import date
        qend = f"{date.today().year}-03-31"
        self.orl._latest_earnings_pr = lambda t: None
        self.orl._latest_ir_release = lambda t: {
            "url": "https://examplebank.com/pr/q1.pdf",
            "title": "Example Bank Reports First Quarter Results",
            "qend": qend, "kind": "pdf"}
        self.orl._fetch_document = lambda u, k: (
            "Net interest margin of 4.10% for the quarter. Tangible book "
            "value per share was $31.25.")
        val = self.orl.otc_release_metrics("EXBK")
        self.assertEqual(val["transport"], "ir_site")
        self.assertEqual(val["qend"], qend)
        self.assertEqual(val["metrics"]["nim"], 4.10)
        self.assertEqual(val["metrics"]["tbv_ps"], 31.25)
        self.assertIsNone(val["filed_date"])

    def test_nothing_anywhere_caches_negative(self):
        crawls = []
        self.orl._latest_earnings_pr = lambda t: None
        self.orl._latest_ir_release = lambda t: crawls.append(1) or None
        self.assertIsNone(self.orl.otc_release_metrics("EXBK"))
        # Fresh sentinel: the second call must serve None WITHOUT re-crawling.
        self.assertIsNone(self.orl.otc_release_metrics("EXBK"))
        self.assertEqual(len(crawls), 1)

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
        self.store["otc_release:v6:PBAM"] = {
            "cached_at": "2020-01-01T00:00:00",       # stale ⇒ re-check
            "value": {"url": "u1", "metrics": {"nim": 5.18}}}
        val = self.orl.otc_release_metrics("PBAM")
        self.assertEqual(val["metrics"], {"nim": 5.18})
        self.assertEqual(fetches, [])                 # no page load

    def test_fetch_failure_serves_previous(self):
        self.orl._latest_earnings_pr = lambda t: {
            "title": "x", "url": "u2", "published_at": "2026-07-16 08:00:00"}
        self.orl._fetch_story = lambda u: None
        self.store["otc_release:v6:PBAM"] = {
            "cached_at": "2020-01-01T00:00:00",
            "value": {"url": "u1", "metrics": {"nim": 5.0}}}
        val = self.orl.otc_release_metrics("PBAM")
        self.assertEqual(val["metrics"], {"nim": 5.0})


if __name__ == "__main__":
    unittest.main()
