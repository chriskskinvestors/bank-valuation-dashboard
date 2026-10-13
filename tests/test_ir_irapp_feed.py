"""
IR-site publication dates (data/events/ir_site.py) — never stamp the poll time.

2026-10-10 11:05 UTC: the first scrape of three IRapp IR sites (HAFC, FNWD,
FCBM) emitted their whole archives with published_at = now, because the HTML
listing's links carry no date ("Finward Bancorp Announces Third Quarter 2025
Results" shown as Oct-10 news). Prod held 335 such date_inferred rows (CUBB:
220 releases back to 2021 stamped 07-24). Pinned here:

  • IRapp sites are read from their press-release RSS with real <pubDate>s
    (both the 4-digit and the 2-digit-year shapes the live feeds use), under
    the 30-day lookback, and are never HTML-scraped.
  • The feed path is probed per host: the standard IRapp path, then the
    same platform's alternate sections (HBAN /news-presentations/…, PNC
    /news-events/financial-press-releases/…).
  • An undated HTML-scraped link (any platform, first scrape or not) is
    dropped; a dated one keeps its own date.
  • An undated Q4 API item is dropped instead of stamped now.
  • poll-events' purge deletes the stored date_inferred ir_site rows.

No network: requests / platform probes are mocked.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests import _streamlit_stub

_streamlit_stub.install()

import data.events.ir_site as ir  # noqa: E402

NOW = datetime.now(timezone.utc)
CUTOFF = NOW - timedelta(days=30)


def _rfc(d: datetime, two_digit_year: bool = False) -> str:
    """pubDate in the live feeds' shapes, ET offset (FNWD/FCBM use 2-digit years)."""
    et = d.astimezone(timezone(timedelta(hours=-4)))
    return et.strftime("%a, %d %b %y %H:%M:%S -0400" if two_digit_year
                       else "%a, %d %b %Y %H:%M:%S -0400")


# Dynamic so the fixtures never rot past the 30-day cutoff.
_RECENT = (NOW - timedelta(days=3)).replace(microsecond=0, second=0)
_OLD = NOW - timedelta(days=200)

# FNWD shape: CDATA titles, empty description, 2-digit-year pubDates.
_FNWD_RSS = f"""<?xml version="1.0" encoding="UTF-8" ?><rss version="2.0"><channel>
<title>Finward Bancorp (FNWD) Press Releases</title>
<link>https://investorrelations.ibankpeoples.com/news-events/press-releases/rss</link>
<item>
  <title><![CDATA[Finward Bancorp Announces Dividend]]></title>
  <description><![CDATA[]]></description>
  <link>https://investorrelations.ibankpeoples.com/news-events/press-releases/detail/94/finward-bancorp-announces-dividend</link>
  <pubDate>{_rfc(_RECENT, two_digit_year=True)}</pubDate>
</item>
<item>
  <title><![CDATA[Finward Bancorp Announces Third Quarter 2025 Results]]></title>
  <description><![CDATA[]]></description>
  <link>https://investorrelations.ibankpeoples.com/news-events/press-releases/detail/80/finward-bancorp-announces-third-quarter-2025-results</link>
  <pubDate>{_rfc(_OLD, two_digit_year=True)}</pubDate>
</item>
<item>
  <title><![CDATA[Finward Bancorp Item Without A Date]]></title>
  <link>https://investorrelations.ibankpeoples.com/news-events/press-releases/detail/79/no-date</link>
</item>
</channel></rss>"""

# HAFC shape: plain titles, 4-digit years, full body in content:encoded.
_HAFC_RSS = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<title>Hanmi Financial Corporation (HAFC) News</title><link>https:///rss</link>
<item>
  <title>Hanmi Bank Named to Piper Sandler Sm-All Stars Class of 2026</title>
  <link>https://investors.hanmi.com/news-events/press-releases/detail/407/hanmi-bank-named</link>
  <pubDate>{_rfc(_RECENT)}</pubDate>
  <content:encoded><![CDATA[<p>LOS ANGELES (GLOBE NEWSWIRE) -- body with <a href="x">a link</a></p>]]></content:encoded>
</item>
<item>
  <title>Hanmi Reports 2026 First Quarter Results</title>
  <link>https://investors.hanmi.com/news-events/press-releases/detail/390/hanmi-reports-2026-first-quarter-results</link>
  <pubDate>{_rfc(_OLD)}</pubDate>
</item>
</channel></rss>"""

# HBAN shape (live 2026-10-13): same platform, /news-presentations/ section,
# CDATA titles, 2-digit years.
_HBAN_RSS = f"""<?xml version="1.0" encoding="UTF-8" ?><rss version="2.0"><channel>
<title>Huntington Bancshares Incorporated (HBAN) Press Releases</title>
<item>
  <title><![CDATA[Huntington Bancshares Incorporated Reports Third-Quarter 2026 Earnings]]></title>
  <description><![CDATA[]]></description>
  <link>https://ir.huntington.com/news-presentations/press-releases/detail/1030/huntington-q3-2026-earnings</link>
  <pubDate>{_rfc(_RECENT, two_digit_year=True)}</pubDate>
</item>
<item>
  <title><![CDATA[Huntington Bancshares Incorporated Reports First-Quarter 2026 Earnings]]></title>
  <description><![CDATA[]]></description>
  <link>https://ir.huntington.com/news-presentations/press-releases/detail/990/huntington-q1-2026-earnings</link>
  <pubDate>{_rfc(_OLD, two_digit_year=True)}</pubDate>
</item>
</channel></rss>"""


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class TestRssPubDate(unittest.TestCase):
    def test_four_and_two_digit_years_to_utc(self):
        self.assertEqual(ir._rss_pub_date("Wed, 23 Sep 2026 18:01:00 -0400"),
                         datetime(2026, 9, 23, 22, 1, tzinfo=timezone.utc))
        self.assertEqual(ir._rss_pub_date("Fri, 04 Sep 26 16:05:00 -0400"),
                         datetime(2026, 9, 4, 20, 5, tzinfo=timezone.utc))

    def test_missing_or_garbage_is_none(self):
        self.assertIsNone(ir._rss_pub_date(None))
        self.assertIsNone(ir._rss_pub_date(""))
        self.assertIsNone(ir._rss_pub_date("not a date"))


class TestIrappPressReleases(unittest.TestCase):
    def _run(self, rss, home):
        with patch.object(ir, "_irapp_rss_path", return_value=ir._IRAPP_RSS_PATH), \
             patch.object(ir.requests, "get", return_value=_Resp(rss)):
            return ir._irapp_press_releases(home, CUTOFF)

    def test_fnwd_real_dates_cutoff_and_undated_dropped(self):
        out = self._run(_FNWD_RSS, "https://investorrelations.ibankpeoples.com/")
        self.assertEqual(out, [(
            "https://investorrelations.ibankpeoples.com/news-events/press-releases/"
            "detail/94/finward-bancorp-announces-dividend",
            "Finward Bancorp Announces Dividend", _RECENT)])

    def test_hafc_shape_old_quarter_results_never_emitted(self):
        out = self._run(_HAFC_RSS, "https://investors.hanmi.com/")
        self.assertEqual([h for _u, h, _d in out],
                         ["Hanmi Bank Named to Piper Sandler Sm-All Stars Class of 2026"])
        self.assertEqual(out[0][2], _RECENT)

    def test_not_irapp_is_none(self):
        with patch.object(ir, "_irapp_rss_path", return_value=None):
            self.assertIsNone(ir._irapp_press_releases("https://x.example/", CUTOFF))

    def test_irapp_feed_unreadable_is_empty_not_fallback(self):
        with patch.object(ir, "_irapp_rss_path", return_value=ir._IRAPP_RSS_PATH), \
             patch.object(ir.requests, "get", return_value=_Resp("", status=503)):
            self.assertEqual(ir._irapp_press_releases("https://investors.hanmi.com/", CUTOFF), [])
        with patch.object(ir, "_irapp_rss_path", return_value=ir._IRAPP_RSS_PATH), \
             patch.object(ir.requests, "get", side_effect=TimeoutError("slow")):
            self.assertEqual(ir._irapp_press_releases("https://investors.hanmi.com/", CUTOFF), [])


# What the HTML listing of an IRapp site yields: detail links with no date.
_UNDATED_ARCHIVE = [
    ("https://investors.hanmi.com/news-events/press-releases/detail/390/"
     "hanmi-reports-2026-first-quarter-results", "Hanmi Reports 2026 First Quarter Results"),
    ("https://investors.hanmi.com/news-events/press-releases/detail/300/"
     "hanmi-reports-2025-third-quarter-results", "Hanmi Reports 2025 Third Quarter Results"),
]


class TestAdapterNeverStampsPollTime(unittest.TestCase):
    def _poll(self, endpoints, links, irapp_rss=None, rss_path=ir._IRAPP_RSS_PATH):
        def fake_get(url, *a, **k):
            # Only the host's own feed path serves the feed.
            ok = bool(irapp_rss) and url.endswith(rss_path)
            return _Resp(irapp_rss if ok else "", status=200 if ok else 404)
        with patch.object(ir, "get_ir_endpoints", return_value=endpoints), \
             patch.object(ir, "_q4_press_releases", return_value=None), \
             patch.object(ir, "_irapp_rss_path", return_value=rss_path if irapp_rss else None), \
             patch.object(ir.requests, "get", side_effect=fake_get), \
             patch.object(ir.IRSiteAdapter, "_find_press_links",
                          return_value=links) as scrape:
            evs = ir.IRSiteAdapter().poll(list(endpoints))
        return evs, scrape

    def test_irapp_site_uses_rss_dates_not_html_scrape(self):
        # The 2026-10-10 HAFC failure: archive links emitted at poll time.
        evs, scrape = self._poll({"HAFC": "https://investors.hanmi.com/"},
                                 _UNDATED_ARCHIVE, irapp_rss=_HAFC_RSS)
        scrape.assert_not_called()
        self.assertEqual([(e.headline, e.published_at) for e in evs],
                         [("Hanmi Bank Named to Piper Sandler Sm-All Stars Class of 2026",
                           _RECENT)])
        self.assertEqual(evs[0].raw["platform"], "irapp")
        self.assertTrue(all("First Quarter" not in e.headline for e in evs))

    def test_undated_html_links_dropped_dated_kept(self):
        dated_day = (NOW - timedelta(days=5)).strftime("%B %d, %Y")
        links = [("https://investor.bankofamerica.com/content/newsroom/press-releases/"
                  "2026/09/bank-of-america-declares-preferred-stock-dividends.html",
                  "Bank of America Declares Preferred Stock Dividends Payable in October"),
                 ("https://investor.bankofamerica.com/news/2020-01-01/old-release",
                  "Bank of America Announces Something Long Ago Indeed"),
                 ("https://investor.bankofamerica.com/press-release/new",
                  f"Bank of America Declares Quarterly Dividend {dated_day}")]
        evs, scrape = self._poll({"BAC": "https://investor.bankofamerica.com/"}, links)
        scrape.assert_called_once()
        self.assertEqual(len(evs), 1)
        self.assertIn("Declares Quarterly Dividend", evs[0].headline)
        self.assertEqual(evs[0].published_at.date(), (NOW - timedelta(days=5)).date())
        self.assertNotIn("date_inferred", evs[0].raw)

    def test_hban_alternate_feed_path_uses_rss_dates(self):
        # HBAN's feed lives at /news-presentations/…; before the alternate paths
        # it HTML-scraped (undated → dropped), so its IR releases never surfaced.
        evs, scrape = self._poll({"HBAN": "https://ir.huntington.com/"},
                                 _UNDATED_ARCHIVE, irapp_rss=_HBAN_RSS,
                                 rss_path="/news-presentations/press-releases/rss")
        scrape.assert_not_called()
        self.assertEqual([(e.ticker, e.headline, e.published_at) for e in evs],
                         [("HBAN", "Huntington Bancshares Incorporated Reports "
                           "Third-Quarter 2026 Earnings", _RECENT)])
        self.assertEqual(evs[0].url, "https://ir.huntington.com/news-presentations/"
                         "press-releases/detail/1030/huntington-q3-2026-earnings")


class TestIrappFeedPathProbe(unittest.TestCase):
    """_irapp_rss_path finds the host's feed among _IRAPP_RSS_PATHS (live
    2026-10-13: HBAN → /news-presentations/…, PNC → /news-events/financial-
    press-releases/…; both 404 the standard path)."""

    def _probe(self, home, feed_path=None, raises=False):
        calls = []

        def fake_get(url, *a, **k):
            calls.append(url)
            if raises:
                raise ConnectionError("dns")
            ok = feed_path is not None and url.endswith(feed_path)
            return _Resp(_HBAN_RSS if ok else "Not Found", status=200 if ok else 404)
        import data.cache as dc
        with patch.object(dc, "get", return_value=None), \
             patch.object(dc, "put") as put, \
             patch.object(ir.requests, "get", side_effect=fake_get):
            got = ir._irapp_rss_path(home)
        return got, calls, put

    def test_hban_and_pnc_alternate_paths(self):
        for home, path in (("https://ir.huntington.com/", "/news-presentations/press-releases/rss"),
                           ("https://investor.pnc.com/", "/news-events/financial-press-releases/rss")):
            got, _calls, put = self._probe(home, path)
            self.assertEqual(got, path)
            self.assertEqual(put.call_args[0][1]["rss_path"], path)

    def test_standard_path_probed_first(self):
        got, calls, _ = self._probe("https://investors.hanmi.com/", ir._IRAPP_RSS_PATH)
        self.assertEqual(got, ir._IRAPP_RSS_PATH)
        self.assertEqual(calls, ["https://investors.hanmi.com" + ir._IRAPP_RSS_PATH])

    def test_no_feed_is_none_and_cached_negative(self):
        got, calls, put = self._probe("https://ir.example.com/")
        self.assertIsNone(got)
        self.assertEqual(len(calls), len(ir._IRAPP_RSS_PATHS))
        self.assertEqual(put.call_args[0][1]["rss_path"], "")

    def test_unreachable_host_stops_after_first_attempt(self):
        got, calls, _ = self._probe("https://investors.nowhere.example/", raises=True)
        self.assertIsNone(got)
        self.assertEqual(len(calls), 1)

    def test_irapp_site_wrapper(self):
        with patch.object(ir, "_irapp_rss_path", return_value="/news-presentations/press-releases/rss"):
            self.assertTrue(ir._irapp_site("https://ir.huntington.com/"))
        with patch.object(ir, "_irapp_rss_path", return_value=None):
            self.assertFalse(ir._irapp_site("https://ir.example.com/"))


class TestQ4UndatedItemDropped(unittest.TestCase):
    def test_missing_press_release_date_dropped(self):
        payload = {"GetPressReleaseListResult": [
            {"Headline": "Dated Release Within The Window",
             "PressReleaseDate": (NOW - timedelta(days=2)).strftime("%m/%d/%Y %H:%M:%S"),
             "LinkToDetailPage": "/news/dated/default.aspx"},
            {"Headline": "Release With No Date At All",
             "PressReleaseDate": "",
             "LinkToDetailPage": "/news/undated/default.aspx"},
        ]}

        class _J:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        with patch.object(ir, "_q4_site", return_value=(True, None)), \
             patch.object(ir.requests, "get", return_value=_J()):
            out = ir._q4_press_releases("https://ir.example.com/", CUTOFF)
        self.assertEqual([h for _u, h, _d in out], ["Dated Release Within The Window"])


class TestPurgeDateInferredRows(unittest.TestCase):
    """The store dedups on (source, url), so the mis-dated rows would never be
    re-stamped — poll-events' purge removes them (and IRapp items in the window
    re-ingest from RSS with real dates)."""

    def setUp(self):
        import data.db as db
        import data.events.store as store
        from sqlalchemy import create_engine
        self._saved = (db._engine, db.USE_POSTGRES, store._USE_POSTGRES, store._engine)
        db._engine = create_engine("sqlite://")
        db.USE_POSTGRES = False
        store._USE_POSTGRES = False
        store._engine = None
        store.init_schema()

    def tearDown(self):
        import data.db as db
        import data.events.store as store
        db._engine, db.USE_POSTGRES, store._USE_POSTGRES, store._engine = self._saved

    def test_purges_inferred_keeps_dated(self):
        import data.db as db
        import data.events.store as store
        import jobs.poll_events as pe
        from data.events.base import Event
        from sqlalchemy import text

        def ev(ext, headline, raw):
            return Event(ticker="HAFC", source="ir_site", event_type="press_release",
                         headline=headline, published_at=NOW - timedelta(days=3),
                         url=ext, external_id=ext, raw=raw)

        store.insert_events_returning_new([
            ev("https://investors.hanmi.com/detail/390/q1",
               "Hanmi Reports 2026 First Quarter Results",
               {"ir_home": "https://investors.hanmi.com/", "date_inferred": True}),
            ev("https://investors.hanmi.com/detail/407/piper",
               "Hanmi Bank Named to Piper Sandler Sm-All Stars Class of 2026",
               {"ir_home": "https://investors.hanmi.com/", "platform": "irapp"}),
            ev("https://ir.example.com/news/2026-10-01/x",
               "Example Bancorp Dated Scrape Release Headline",
               {"ir_home": "https://ir.example.com/", "date_inferred": False}),
        ])
        self.assertEqual(pe._purge_junk_events(), 1)
        with db._engine.connect() as c:
            kept = sorted(r[0] for r in c.execute(text("SELECT headline FROM events")).all())
        self.assertEqual(kept, ["Example Bancorp Dated Scrape Release Headline",
                                "Hanmi Bank Named to Piper Sandler Sm-All Stars Class of 2026"])
        self.assertEqual(pe._purge_junk_events(), 0, "idempotent")


class TestIrappAnnouncementFeedPath(unittest.TestCase):
    """Call details read the host's DISCOVERED feed (HBAN/PNC serve alternate
    sections), and a multi-quarter release calendar never supplies a date."""

    HBAN_FEED = ("<rss><channel><item><title><![CDATA[Huntington Bancshares Incorporated "
                 "Announces Third Quarter 2026 Earnings Call Details]]></title>"
                 "<link>https://ir.huntington.com/news-presentations/press-releases/detail/1024/x</link>"
                 "</item></channel></rss>")
    # HBAN's release text (2026-09-30), verbatim excerpt.
    HBAN_BODY = (
        "Huntington Bancshares Incorporated Announces Third Quarter 2026 Earnings Call "
        "Details September 30, 2026 4:23pm EDT COLUMBUS, Ohio , Sept. 30, 2026 /PRNewswire/ "
        "-- Huntington Bancshares Incorporated (Nasdaq: HBAN) will release third quarter "
        "2026 financial results prior to the market opening on Thursday, October 22, 2026. "
        "The Company will host a conference call to review quarterly financial results at "
        "9 a.m. ET. Webcast Information The third quarter 2026 earnings conference call, "
        "along with slides, may be accessed via a live Internet webcast in the Investor "
        "Relations section of Huntington's website or through a dial-in telephone number "
        "at (877) 407-8029 or (201) 689-8029; conference ID #&nbsp;13762987. A replay of "
        "the webcast will be archived in the Investor Relations section of Huntington's "
        "website. A telephone replay will be available approximately three hours after "
        "the completion of the call through Friday, October 30, 2026, at (877) 660-6853 "
        "or (201) 612-7415; conference ID # 13762987.")
    PNC_FEED = ("<rss><channel><item><title>The PNC Financial Services Group Provides "
                "Quarterly 2027 Earnings Release Dates</title>"
                "<link>https://investor.pnc.com/news-events/financial-press-releases/detail/691/x</link>"
                "</item></channel></rss>")
    PNC_BODY = (
        "PNC announced today it expects to issue 2027 quarterly earnings releases "
        "pre-market open and hold conference calls at 10 a.m. (ET) on the following "
        "dates: First Quarter – Thursday, April 15, 2027 Second Quarter – Thursday, "
        "July 15, 2027 Third Quarter – Friday, Oct. 15, 2027")

    def _ann(self, home, feed_path, feed, body):
        import data.earnings_call as ec
        fetched = []

        def fake_get(url, *a, **k):
            fetched.append(url)
            ok = url.endswith(feed_path)
            return _Resp(feed if ok else "Not Found", status=200 if ok else 404)
        import data.cache as dc
        with patch.object(dc, "get", return_value=None), patch.object(dc, "put"), \
             patch.object(ir.requests, "get", side_effect=fake_get), \
             patch.object(ec, "_fetch_pr_body", return_value=body):
            return ir._irapp_announcement(home, "2026-10-13"), fetched

    def test_hban_call_details_from_alternate_feed(self):
        info, fetched = self._ann("https://ir.huntington.com/",
                                  "/news-presentations/press-releases/rss",
                                  self.HBAN_FEED, self.HBAN_BODY)
        self.assertEqual(info, {"release_date": "2026-10-22", "call_time": "9a ET",
                                "dial_in": "(877) 407-8029 (ID 13762987)"})
        self.assertNotIn("https://ir.huntington.com" + ir._IRAPP_RSS_PATH, fetched[1:],
                         "after the probe, the standard path is never read")

    def test_pnc_release_calendar_supplies_nothing(self):
        info, _ = self._ann("https://investor.pnc.com/",
                            "/news-events/financial-press-releases/rss",
                            self.PNC_FEED, self.PNC_BODY)
        self.assertIsNone(info)          # was call_date 2027-04-15 — a far quarter


class TestReleaseCalendarIsNotAnAnnouncement(unittest.TestCase):
    """Titles from prod's events table (survey 2026-10-13)."""

    def test_multi_quarter_calendars_excluded(self):
        from data.earnings_call import _is_earnings_announcement
        for t in ("Fifth Third Bancorp Announces Earnings Release Dates for Fiscal Year 2027",
                  "KEYCORP ANNOUNCES 2027 QUARTERLY EARNINGS CONFERENCE CALL DATES",
                  "M&T Bank Corporation Announces Conference Call Dates to Review Quarterly 2028 Earnings",
                  "The PNC Financial Services Group Provides Quarterly 2027 Earnings Release Dates",
                  "Regions Financial Corp. Announces Quarterly Earnings Release Dates for 2027",
                  "State Street Corporation (NYSE: STT) Announces Remaining 2026 Quarterly "
                  "Financial Results Release Dates and Conference Call",
                  "Bank of America Announces Fourth Quarter 2026 and Quarterly 2027 "
                  "Financial Reporting Dates"):
            self.assertFalse(_is_earnings_announcement(t), t)

    def test_single_quarter_dates_titles_kept(self):
        from data.earnings_call import _is_earnings_announcement
        for t in ("Fulton Financial Corporation Announces Dates for Third Quarter 2026 "
                  "Earnings Release and Webcast",
                  "Metropolitan Bank Holding Corp. Schedules Third Quarter 2026 Earnings "
                  "Release and Conference Call Dates",
                  "Northeast Bank Announces Dates for Fiscal 2026 Fourth Quarter Earnings "
                  "Results and Conference Call",
                  "Bank OZK Announces Third Quarter 2026 Earnings Release and Conference Call Dates",
                  "SmartFinancial Sets Dates for Third Quarter Earnings Release and Conference Call",
                  "Atlantic Union Bankshares Announces Q2 2026 Financial Results and Capital Updates",
                  "Huntington Bancshares Incorporated Announces Third Quarter 2026 Earnings Call Details"):
            self.assertTrue(_is_earnings_announcement(t), t)


if __name__ == "__main__":
    unittest.main()
