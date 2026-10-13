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
        with patch.object(ir, "_irapp_site", return_value=True), \
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
        with patch.object(ir, "_irapp_site", return_value=False):
            self.assertIsNone(ir._irapp_press_releases("https://x.example/", CUTOFF))

    def test_irapp_feed_unreadable_is_empty_not_fallback(self):
        with patch.object(ir, "_irapp_site", return_value=True), \
             patch.object(ir.requests, "get", return_value=_Resp("", status=503)):
            self.assertEqual(ir._irapp_press_releases("https://investors.hanmi.com/", CUTOFF), [])
        with patch.object(ir, "_irapp_site", return_value=True), \
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
    def _poll(self, endpoints, links, irapp_rss=None):
        def fake_get(url, *a, **k):
            return _Resp(irapp_rss or "", status=200 if irapp_rss else 404)
        with patch.object(ir, "get_ir_endpoints", return_value=endpoints), \
             patch.object(ir, "_q4_press_releases", return_value=None), \
             patch.object(ir, "_irapp_site", return_value=irapp_rss is not None), \
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
        links = [("https://ir.huntington.com/news/detail/1026/huntington-invests",
                  "Huntington Bank Announces More Than $40 Million Investment in Tupelo"),
                 ("https://ir.example.com/news/2020-01-01/old-release",
                  "Example Bancorp Announces Something Long Ago Indeed"),
                 ("https://ir.example.com/press-release/new",
                  f"Example Bancorp Declares Quarterly Dividend {dated_day}")]
        evs, scrape = self._poll({"HBAN": "https://ir.huntington.com/"}, links)
        scrape.assert_called_once()
        self.assertEqual(len(evs), 1)
        self.assertIn("Declares Quarterly Dividend", evs[0].headline)
        self.assertEqual(evs[0].published_at.date(), (NOW - timedelta(days=5)).date())
        self.assertNotIn("date_inferred", evs[0].raw)


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


if __name__ == "__main__":
    unittest.main()
