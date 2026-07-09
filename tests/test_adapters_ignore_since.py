"""
(AUDIT-2026-07-02 P2 #21) Every event adapter must IGNORE `since` and scan its
own full lookback window.

The runner used to pass since = the source-wide MAX(published_at), which
permanently dropped items syndicated late (published_at older than the newest
stored row) or missed on a failed/abandoned per-ticker fetch. The cutoff bought
nothing: every adapter's fetch pulls the same payload regardless (RSS feeds,
FMP limit=250 batches, per-ticker queries — the cutoff only filtered
post-fetch), and the store dedups on (source, external_id) + the cross-source
content key, so the full-window re-scan is free.

The two SEC 8-K adapters were fixed first (P1 #4, TestBackstopIgnoresSince);
this suite pins the same contract on the remaining adapters: an item published
BEFORE `since` but inside the adapter's LOOKBACK window must still be emitted
when since = now.

All network is mocked; no live calls.
"""
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

# Stub streamlit before importing data modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

import data.events.businesswire as bw  # noqa: E402
import data.events.prnewswire as prn  # noqa: E402
import data.events.globenewswire as gn  # noqa: E402
import data.events.google_news as gnews  # noqa: E402
import data.events.ir_site as ir  # noqa: E402
import data.fmp_client as fmp  # noqa: E402
from data.events.fmp_news import FMPPressReleaseAdapter  # noqa: E402
from data.events.yfinance_news import YFinanceNewsAdapter  # noqa: E402
from data.events.wire_base import RSSItem  # noqa: E402

NOW = datetime.now(timezone.utc)
# Published BEFORE since (= NOW) but well inside every adapter's lookback.
STALE_PUB = NOW - timedelta(days=2)
# A headline that passes name-matching (curated JPM alias), the first-party PR
# verb gate, and every junk filter.
HEADLINE = "JPMorgan Announces Quarterly Common Stock Dividend Increase"


def _rss(title=HEADLINE, pub=STALE_PUB, link="https://www.prnewswire.com/x"):
    return RSSItem(title=title, summary="", link=link, published=pub, guid=link)


class TestWireAdaptersIgnoreSince(unittest.TestCase):
    """A wire-feed item older than `since` (but inside LOOKBACK_DAYS) must be
    emitted — dedup, not the cutoff, handles re-ingest."""

    def test_prnewswire(self):
        with patch.object(prn, "fetch_rss", return_value=[_rss()]):
            evs = prn.PRNewswireAdapter().poll(["JPM"], since=NOW)
        self.assertEqual([e.ticker for e in evs], ["JPM"])

    def test_globenewswire(self):
        with patch.object(gn, "fetch_rss", return_value=[_rss(
                link="https://www.globenewswire.com/x")]):
            evs = gn.GlobeNewswireAdapter().poll(["JPM"], since=NOW)
        self.assertEqual([e.ticker for e in evs], ["JPM"])

    def test_businesswire(self):
        # BW_FEEDS is currently empty (dead tokens) — inject one so the loop
        # runs; the contract must hold when the feed is re-enabled.
        with patch.object(bw, "BW_FEEDS", ["https://feed.businesswire.example/rss"]), \
             patch.object(bw, "fetch_rss", return_value=[_rss(
                 link="https://www.businesswire.com/x")]):
            evs = bw.BusinessWireAdapter().poll(["JPM"], since=NOW)
        self.assertEqual([e.ticker for e in evs], ["JPM"])


class TestFMPIgnoresSince(unittest.TestCase):
    def test_fmp_press_release_older_than_since_kept(self):
        ts = STALE_PUB.strftime("%Y-%m-%d %H:%M:%S")
        row = {"symbol": "JPM",
               "title": "JPMorgan Chase Declares Dividend on Preferred Stock",
               "publisher": "Business Wire",
               "url": "https://www.businesswire.com/news/home/x/en/y/",
               "published_at": ts,
               "text": "JPMorgan Chase & Co. (NYSE: JPM) today declared..."}
        with patch.object(fmp, "_has_key", return_value=True), \
             patch.object(fmp, "get_press_releases_multi", return_value=[row]):
            evs = FMPPressReleaseAdapter().poll(["JPM"], since=NOW)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].ticker, "JPM")


class TestYFinanceIgnoresSince(unittest.TestCase):
    def test_yahoo_item_older_than_since_kept(self):
        item = {"content": {
            "id": "uuid-1",
            "title": HEADLINE,
            "canonicalUrl": {"url": "https://finance.yahoo.com/news/x.html"},
            "pubDate": STALE_PUB.isoformat(),
            "summary": "JPMorgan raised its quarterly dividend.",
            "provider": {"displayName": "Business Wire"},
        }}
        fake_yf = types.ModuleType("yfinance")
        fake_yf.Ticker = type("Ticker", (), {
            "__init__": lambda self, tk: None, "news": [item]})
        with patch.dict(sys.modules, {"yfinance": fake_yf}):
            evs = YFinanceNewsAdapter().poll(["JPM"], since=NOW)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].ticker, "JPM")


class TestGoogleNewsIgnoresSince(unittest.TestCase):
    def test_per_ticker_item_older_than_since_kept(self):
        entry = _rss(title=f"{HEADLINE} - Business Wire",
                     link="https://www.businesswire.com/x")
        with patch.object(gnews, "fetch_rss", return_value=[entry]), \
             patch.object(gnews.GoogleNewsAdapter, "_blocked", return_value=False), \
             patch.object(gnews, "get_name", return_value="JPMorgan Chase & Co"):
            evs = gnews.GoogleNewsAdapter().poll(["JPM"], since=NOW)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].ticker, "JPM")

    def test_topic_item_older_than_since_kept(self):
        entry = _rss(title="Federal Reserve Signals Slower Rate Path - Reuters",
                     link="https://www.reuters.com/x",
                     pub=NOW - timedelta(hours=30))
        with patch.object(gnews, "fetch_rss", return_value=[entry]):
            evs = gnews.GoogleNewsTopicAdapter().poll([], since=NOW)
        self.assertTrue(evs, "a 30h-old topic item (inside the 48h lookback) "
                             "must survive since=now")
        self.assertTrue(all(e.published_at < NOW for e in evs))


class TestIRSiteIgnoresSince(unittest.TestCase):
    def test_full_lookback_cutoff_despite_since(self):
        captured = {}

        def fake_q4(ir_home, cutoff):
            captured["cutoff"] = cutoff
            return [("https://ir.jpmorganchase.com/news/x",
                     "JPMorgan Announces Quarterly Dividend Increase",
                     NOW - timedelta(days=5))]

        with patch.object(ir, "get_ir_endpoints",
                          return_value={"JPM": "https://ir.jpmorganchase.com/"}), \
             patch.object(ir, "_q4_press_releases", side_effect=fake_q4):
            evs = ir.IRSiteAdapter().poll(["JPM"], since=NOW)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].ticker, "JPM")
        # The cutoff handed to the Q4 fetch must be the adapter's own 30-day
        # lookback, not the intraday `since`.
        self.assertLess(captured["cutoff"], NOW - timedelta(days=29))


if __name__ == "__main__":
    unittest.main()
