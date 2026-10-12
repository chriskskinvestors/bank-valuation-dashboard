"""
2026-10-08: GlobeNewswire began refusing server fetches (Akamai "Access
Denied" 403). The deals lane was fixed in #352; these pin the other lanes
that fetch wire stories:

  • data/otc_release._fetch_story — a successful fetch is kept for good, so a
    re-extraction (envelope version bump) after the block still has the page;
    a 200 bot wall is a failed fetch, never a document.
  • data/otc_release.backfill_eps_history — a blocked GlobeNewswire story is
    a failure (retried daily), not an absent quarter behind a once-only
    marker; Business Wire's permanent refusal is still skipped.
  • data/earnings_call._fetch_pr_body — a wire announcement's body survives
    the block across the ~30-min snapshot rebuilds.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import requests

GNW = ("https://www.globenewswire.com/news-release/2026/07/22/3331757/0/en/"
       "ES-Bancshares-Inc-Announces-Second-Quarter-2026-Results.html")
BW = "https://www.businesswire.com/news/home/20260722/en/T-Bancorp-Reports"

STORY = ("<html><head><title>T Bancorp Reports Second Quarter 2026 Results"
         "</title></head><body><p>Net interest margin of 5.18% for the quarter."
         " Tangible book value per share was $49.57.</p>"
         + "<p>Forward-looking statements.</p>" * 80 + "</body></html>")
DENIED = ('<HTML><HEAD>\n<TITLE>Access Denied</TITLE>\n</HEAD><BODY>\n'
          '<H1>Access Denied</H1>\n You don\'t have permission to access '
          '"http://www.globenewswire.com/" on this server.</BODY></HTML>')


class _Resp:
    def __init__(self, text):
        self.text = text


def _forbidden(*_a, **_k):
    r = requests.Response()
    r.status_code = 403
    raise requests.HTTPError("403 Forbidden", response=r)


class _Store(unittest.TestCase):
    def setUp(self):
        self.store = {}
        for p in (patch("data.cache.get",
                        side_effect=lambda k, max_age_s=None: self.store.get(k)),
                  patch("data.cache.put",
                        side_effect=lambda k, v: self.store.__setitem__(k, v))):
            p.start()
            self.addCleanup(p.stop)
        from data import otc_release as orl
        orl._host_down.clear()
        self.addCleanup(orl._host_down.clear)


class TestStoryCache(_Store):

    def test_successful_fetch_is_served_when_the_wire_later_refuses(self):
        from data import otc_release as orl
        with patch("data.http.get_with_retry", return_value=_Resp(STORY)):
            first = orl._fetch_story(GNW)
        with patch("data.http.get_with_retry", side_effect=_forbidden) as f:
            again = orl._fetch_story(GNW)
        self.assertEqual(first, STORY)
        self.assertEqual(again, STORY)
        f.assert_not_called()
        # hashed key: wire URLs carry the headline slug, the column is 255
        (key,) = self.store
        self.assertLess(len(key), 80)

    def test_bot_wall_served_as_200_is_a_failed_fetch(self):
        from data import otc_release as orl
        with patch("data.http.get_with_retry", return_value=_Resp(DENIED)):
            self.assertIsNone(orl._fetch_story(GNW))
        self.assertEqual(self.store, {})

    def test_refused_fetch_caches_nothing(self):
        from data import otc_release as orl
        with patch("data.http.get_with_retry", side_effect=_forbidden):
            self.assertIsNone(orl._fetch_story(GNW))
        self.assertEqual(self.store, {})

    def test_one_refusal_backs_the_host_off(self):
        # Cloud Run sees the block as a read timeout (3 x 30s per story): a
        # warm pass must pay it once per host, not once per bank.
        from data import otc_release as orl
        other = GNW.replace("3331757", "3331758")
        with patch("data.http.get_with_retry", side_effect=requests.Timeout) as f:
            self.assertIsNone(orl._fetch_story(GNW))
            self.assertIsNone(orl._fetch_story(other))
        self.assertEqual(f.call_count, 1)
        with patch("data.http.get_with_retry", return_value=_Resp(STORY)) as f:
            self.assertEqual(orl._fetch_story("https://www.prnewswire.com/x.html"),
                             STORY)                      # other hosts unaffected
        orl._host_down["www.globenewswire.com"] -= orl._HOST_BACKOFF_S
        with patch("data.http.get_with_retry", return_value=_Resp(STORY)):
            self.assertEqual(orl._fetch_story(GNW), STORY)   # retried later

    def test_a_missing_story_does_not_back_the_host_off(self):
        from data import otc_release as orl
        r = requests.Response()
        r.status_code = 404
        with patch("data.http.get_with_retry",
                   side_effect=requests.HTTPError("404", response=r)):
            self.assertIsNone(orl._fetch_story(GNW))
        self.assertEqual(orl._host_down, {})

    def test_reextraction_after_a_version_bump_reads_the_kept_page(self):
        # The envelope key changes on every extraction-spec bump; the story
        # does not. Before the cache, a bump during the block blanked every
        # GlobeNewswire-sourced bank (no page to re-extract from).
        from data import otc_release as orl
        pr = {"title": "T Bancorp Reports Second Quarter 2026 Results",
              "url": GNW, "published_at": "2026-07-22 08:00:00"}
        with patch.object(orl, "_latest_earnings_pr", return_value=pr), \
             patch.object(orl, "_append_eps_history"):
            with patch("data.http.get_with_retry", return_value=_Resp(STORY)):
                first = orl.otc_release_metrics("ESBS")
            for k in [k for k in self.store if k.startswith("otc_release:")]:
                del self.store[k]                       # the bump
            with patch("data.http.get_with_retry", side_effect=_forbidden):
                again = orl.otc_release_metrics("ESBS")
        self.assertEqual(first["metrics"]["tbv_ps"], 49.57)
        self.assertEqual(again["metrics"], first["metrics"])


class TestBackfillBlocked(_Store):

    def setUp(self):
        super().setUp()
        from data import otc_release as orl
        self.orl = orl
        self.store["otc_release:v18:T"] = {
            "cached_at": "2026-10-08T00:00:00",
            "value": {"url": "latest", "qend": "2026-06-30", "metrics": {}}}

    def _prs(self, url):
        return [{"title": "T Bancorp Reports First Quarter 2026 Results",
                 "url": url, "published_at": "2026-04-20 08:00:00"}]

    def _run(self, url, story):
        with patch.object(self.orl, "_earnings_prs", return_value=self._prs(url)), \
             patch.object(self.orl, "_ir_release_candidates", return_value=[]), \
             patch.object(self.orl, "_fetch_story", return_value=story) as f:
            return self.orl.backfill_eps_history("T"), f

    def test_blocked_globenewswire_story_does_not_mark_done(self):
        ran, _ = self._run(GNW, None)
        self.assertTrue(ran)                        # it spent a budget slot
        hist = self.orl.get_eps_history("T")
        self.assertNotIn("backfill", hist)
        self.assertIn("backfill_blocked_at", hist)
        # within the day: skipped before any fetch
        ran, f = self._run(GNW, None)
        self.assertFalse(ran)
        f.assert_not_called()

    def test_blocked_attempt_retries_after_a_day_and_completes(self):
        self._run(GNW, None)
        key = "otc_eps_history:v1:T"
        self.store[key]["value"]["backfill_blocked_at"] = (
            datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
        ran, _ = self._run(GNW, "<p>Diluted earnings per share were $0.36 "
                                "for the quarter.</p>")
        self.assertTrue(ran)
        hist = self.orl.get_eps_history("T")
        self.assertEqual(hist["quarters"]["2026-03-31"]["eps_diluted"], 0.36)
        self.assertEqual(hist["backfill"]["urls"], [GNW])

    def test_business_wire_permanent_block_is_skipped_and_marked_done(self):
        ran, _ = self._run(BW, None)
        self.assertTrue(ran)
        self.assertEqual(self.orl.get_eps_history("T")["backfill"]["urls"], [BW])


class TestPrBodyCache(_Store):

    def test_wire_body_survives_the_block(self):
        import data.earnings_call as ec
        body = ("<p>T Bancorp will release its third quarter 2026 results on "
                "October 22, 2026 and host a conference call at 10:00 a.m. ET."
                "</p>" + "<p>About T Bancorp.</p>" * 40)
        with patch("data.events.ir_site._fetch", return_value=body):
            first = ec._fetch_pr_body(GNW)
        with patch("data.events.ir_site._fetch", return_value=None) as f:
            again = ec._fetch_pr_body(GNW)
        self.assertIn("October 22, 2026", first)
        self.assertEqual(again, first)
        f.assert_not_called()

    def test_bank_ir_page_is_not_kept(self):
        # Some event links are a bank's news LIST — its content moves.
        import data.earnings_call as ec
        body = "<p>News</p>" * 200
        with patch("data.events.ir_site._fetch", return_value=body):
            ec._fetch_pr_body("https://www.tbank.com/news")
        self.assertEqual(self.store, {})

    def test_denied_body_is_not_kept(self):
        import data.earnings_call as ec
        with patch("data.events.ir_site._fetch", return_value=DENIED):
            ec._fetch_pr_body(GNW)
        self.assertEqual(self.store, {})


class TestFirstPartyFallback(_Store):
    """A refused wire story is read from the bank's OWN channels for the same
    quarter (Q4 IR API body, then the IR-site crawl) — never a different
    quarter's document, and never at the cost of the previous figures."""

    PR = {"title": "T Bancorp Reports Third Quarter 2026 Results",
          "url": GNW.replace("2026/07/22", "2026/10/21"),
          "published_at": "2026-10-21 08:00:00"}
    Q3 = ("<p>Net interest margin of 3.40% for the quarter. Tangible book "
          "value per share was $21.07.</p>")

    def setUp(self):
        super().setUp()
        from data import otc_release as orl
        self.orl = orl
        for p in (patch.object(orl, "_latest_earnings_pr", return_value=self.PR),
                  patch.object(orl, "_fetch_story", return_value=None),
                  patch.object(orl, "_append_eps_history")):
            p.start()
            self.addCleanup(p.stop)

    def _prev(self, **extra):
        self.store["otc_release:v18:T"] = {
            "cached_at": "2020-01-01T00:00:00",
            "value": {"url": GNW, "qend": "2026-06-30",
                      "metrics": {"tbv_ps": 20.5}}, **extra}

    def test_q4_body_for_the_same_quarter_is_read(self):
        doc = {"url": "https://ir.t.com/news/q3", "title": self.PR["title"],
               "html": self.Q3, "transport": "ir_q4"}
        with patch.object(self.orl, "_q4_release", return_value=doc) as q4,              patch.object(self.orl, "_ir_release_candidates") as crawl:
            v = self.orl.otc_release_metrics("T")
        q4.assert_called_once_with("T", "2026-09-30")
        crawl.assert_not_called()
        self.assertEqual((v["qend"], v["metrics"]["tbv_ps"], v["metrics"]["nim"]),
                         ("2026-09-30", 21.07, 3.4))
        self.assertEqual((v["url"], v["transport"], v["wire_url"]),
                         (doc["url"], "ir_q4", self.PR["url"]))
        # the wire URL short-circuits the next pass: no second read
        self.store["otc_release:v18:T"]["cached_at"] = "2020-01-01T00:00:00"
        with patch.object(self.orl, "_first_party_release") as fp:
            self.assertEqual(self.orl.otc_release_metrics("T")["qend"], "2026-09-30")
        fp.assert_not_called()

    def test_site_crawl_takes_only_the_same_quarter(self):
        cands = [{"url": "https://t.com/q2.pdf", "title": "Q2 2026 Earnings",
                  "qend": "2026-06-30", "kind": "pdf"},
                 {"url": "https://t.com/q3.pdf", "title": "Q3 2026 Earnings",
                  "qend": "2026-09-30", "kind": "pdf"}]
        fetched = []
        with patch.object(self.orl, "_q4_release", return_value=None),              patch.object(self.orl, "_ir_release_candidates", return_value=cands),              patch.object(self.orl, "_fetch_document",
                          side_effect=lambda u, k: fetched.append(u) or self.Q3):
            v = self.orl.otc_release_metrics("T")
        self.assertEqual(fetched, ["https://t.com/q3.pdf"])
        self.assertEqual((v["transport"], v["qend"], v["wire_url"]),
                         ("ir_site", "2026-09-30", self.PR["url"]))

    def test_nothing_first_party_keeps_previous_and_crawls_once_a_day(self):
        self._prev()
        with patch.object(self.orl, "_q4_release", return_value=None),              patch.object(self.orl, "_ir_release_candidates", return_value=[]) as crawl:
            v = self.orl.otc_release_metrics("T")
            self.assertEqual((v["qend"], v["metrics"]), ("2026-06-30", {"tbv_ps": 20.5}))
            self.assertTrue(self.store["otc_release:v18:T"]["ir_checked_at"])
            self.store["otc_release:v18:T"]["cached_at"] = "2020-01-01T00:00:00"
            self.assertEqual(self.orl.otc_release_metrics("T")["qend"], "2026-06-30")
        self.assertEqual(crawl.call_count, 1)

    def test_no_previous_and_nothing_found_is_none(self):
        with patch.object(self.orl, "_q4_release", return_value=None),              patch.object(self.orl, "_ir_release_candidates", return_value=[]):
            self.assertIsNone(self.orl.otc_release_metrics("T"))
        self.assertTrue(self.store["otc_release:v18:T"]["value"]["empty"])

    def test_unprovable_title_period_reads_nothing(self):
        self._prev()
        bad = {**self.PR, "title": "T Bancorp Reports Fourth Quarter 2025 Results"}
        with patch.object(self.orl, "_latest_earnings_pr", return_value=bad),              patch.object(self.orl, "_first_party_release") as fp:
            self.assertEqual(self.orl.otc_release_metrics("T")["qend"], "2026-06-30")
        fp.assert_not_called()
        self.orl._fetch_story.assert_not_called()


class TestQ4Release(unittest.TestCase):

    def _items(self, *rows):
        return {"GetPressReleaseListResult": [
            {"Headline": h, "Body": b, "PressReleaseDate": d,
             "LinkToDetailPage": "/news/" + str(i)}
            for i, (h, b, d) in enumerate(rows)]}

    def _run(self, payload, qend="2026-09-30"):
        from data import otc_release as orl
        resp = type("R", (), {"json": lambda self: payload})()
        with patch("data.events.ir_site.get_ir_endpoints",
                   return_value={"T": "https://ir.t.com/"}),              patch("data.events.ir_site._q4_site", return_value=(True, None)),              patch("data.http.get_with_retry", return_value=resp):
            return orl._q4_release("T", qend)

    def test_picks_the_same_quarter_results_release(self):
        doc = self._run(self._items(
            ("T Bancorp Declares Dividend", "<p>div</p>", "10/22/2026 08:00:00"),
            ("T Bancorp Reports Third Quarter 2026 Results", "<p>Q3</p>",
             "10/21/2026 08:00:00"),
            ("T Bancorp Reports Second Quarter 2026 Results", "<p>Q2</p>",
             "07/21/2026 08:00:00")))
        self.assertEqual((doc["html"], doc["url"], doc["transport"]),
                         ("<p>Q3</p>", "https://ir.t.com/news/1", "ir_q4"))

    def test_other_quarter_or_empty_body_is_none(self):
        self.assertIsNone(self._run(self._items(
            ("T Bancorp Reports Third Quarter 2026 Results", "",
             "10/21/2026 08:00:00"),
            ("T Bancorp Reports Second Quarter 2026 Results", "<p>Q2</p>",
             "07/21/2026 08:00:00"))))

    def test_pdf_only_release_reads_its_document(self):
        # FGFH: the Q4 item has no Body; the release is the attached PDF
        from data import otc_release as orl
        payload = {"GetPressReleaseListResult": [{
            "Headline": "Foresight Reports 2026 Third Quarter Results",
            "Body": "", "PressReleaseDate": "10/14/2026 08:00:00",
            "DocumentPath": "https://s206.q4cdn.com/x/Q3-2026-Earnings-Release.pdf"}]}
        with patch.object(orl, "_fetch_document", return_value="Q3 text") as fd:
            doc = self._run(payload)
        fd.assert_called_once_with(
            "https://s206.q4cdn.com/x/Q3-2026-Earnings-Release.pdf", "pdf")
        self.assertEqual((doc["html"], doc["url"]),
                         ("Q3 text", "https://s206.q4cdn.com/x/Q3-2026-Earnings-Release.pdf"))

    def test_bank_without_q4_endpoint_is_none(self):
        from data import otc_release as orl
        with patch("data.events.ir_site.get_ir_endpoints", return_value={}):
            self.assertIsNone(orl._q4_release("T", "2026-09-30"))


class TestVersionHold(unittest.TestCase):
    """Owner, 2026-10-09: hold otc_release version bumps until GlobeNewswire
    serves server fetches again. 30 banks' envelopes come from GlobeNewswire
    stories fetched before the story cache existed: a bump discards them and
    the blocked re-fetch leaves those banks blank.

    Lift the hold only after GlobeNewswire answers 200 from Cloud Run (not
    just locally) — then delete this test and do the owed bump listed at the
    key in data/otc_release._env_record."""

    def test_envelope_version_is_held_at_v17(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "data/otc_release.py").read_text(
            encoding="utf-8")
        self.assertIn('key = f"otc_release:v18:', src,
                      "otc_release version bumps are ON HOLD (owner 2026-10-09) "
                      "while GlobeNewswire blocks server fetches; bump "
                      "release_metrics alone and add it to the 'Owed' list at "
                      "the key. See TestVersionHold's docstring.")


if __name__ == "__main__":
    unittest.main()
