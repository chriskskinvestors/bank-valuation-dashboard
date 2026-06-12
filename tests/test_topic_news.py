"""
Tests for the Google News TOPIC feeds (Home page categorized overnight news:
Macro / Geopolitical / Domestic / Markets — docs/HOME-MACRO-PLAN.md).

Pins the contract end-to-end with a mocked RSS layer (no live network):
  • category stamping  — sentinel ticker 'TOPIC:<CAT>' + raw['category']
  • dedup              — same headline from two outlets → one event; store
                         dedup on (source, external_id) across cycles
  • cap                — at most 15 stored per topic per cycle, newest kept
  • junk discipline    — is_junk_news + is_safe_news_url applied
  • read filter        — get_topic_news(category, hours) respects category
                         + time window; default get_universe_recent()
                         excludes topic rows (bank panels never see them)
"""
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

# Stub streamlit before importing modules that decorate with st.cache_data.
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

import data.db as db
import data.events.store as store
from data.events.wire_base import RSSItem

NOW = datetime.now(timezone.utc)


def _fresh_db():
    """Point the shared engine at a private in-memory SQLite DB so tests
    never touch cache.db (or, worse, a configured Postgres)."""
    from sqlalchemy import create_engine
    db._engine = create_engine("sqlite://")
    db.USE_POSTGRES = False
    store._USE_POSTGRES = False
    store._engine = None  # force store to re-grab the engine + init schema
    store.init_schema()


def _item(title, link="https://example.com/article", published=None):
    return RSSItem(title=title, summary="", link=link,
                   published=published or NOW, guid=link)


class TopicTestCase(unittest.TestCase):
    def setUp(self):
        _fresh_db()


class TestCategoryStamping(TopicTestCase):
    def test_each_topic_polled_once_and_stamped(self):
        from data.events.google_news import GoogleNewsTopicAdapter, TOPIC_QUERIES
        import urllib.parse

        calls = []

        def fake_fetch(url, user_agent=None, **kw):
            calls.append(url)
            q = urllib.parse.unquote(url)
            if "Federal Reserve" in q:
                return [_item("Fed holds rates steady - Reuters",
                              "https://reuters.com/fed")]
            if "military conflict" in q:
                return [_item("New sanctions package announced - AP",
                              "https://apnews.com/sanc")]
            if "US economy" in q:
                return [_item("Shutdown deadline looms - Politico",
                              "https://politico.com/shut")]
            return [_item("S&P 500 rallies 2% - CNBC", "https://cnbc.com/spx")]

        with patch("data.events.google_news.fetch_rss", side_effect=fake_fetch):
            evs = GoogleNewsTopicAdapter().poll(["JPM", "BAC"])  # tickers ignored

        # One feed call per topic per cycle — NOT per ticker.
        self.assertEqual(len(calls), len(TOPIC_QUERIES))
        self.assertEqual(len(evs), 4)
        by_cat = {e.raw["category"]: e for e in evs}
        self.assertEqual(set(by_cat), {"macro", "geopolitical", "domestic", "markets"})
        macro = by_cat["macro"]
        self.assertEqual(macro.ticker, "TOPIC:MACRO")
        self.assertEqual(macro.source, "google_news_topic")
        self.assertEqual(macro.event_type, "topic_news")
        self.assertEqual(macro.headline, "Fed holds rates steady")  # source split off
        self.assertTrue(macro.external_id.startswith("macro::"))


class TestDedup(TopicTestCase):
    def test_same_headline_two_outlets_collapses_in_cycle(self):
        from data.events.google_news import GoogleNewsTopicAdapter

        def fake_fetch(url, user_agent=None, **kw):
            import urllib.parse
            if "Federal Reserve" not in urllib.parse.unquote(url):
                return []
            return [_item("CPI cools to 2.4% in May - Reuters", "https://reuters.com/cpi"),
                    _item("CPI cools to 2.4% in May - Bloomberg", "https://bloomberg.com/cpi")]

        with patch("data.events.google_news.fetch_rss", side_effect=fake_fetch):
            evs = GoogleNewsTopicAdapter().poll([])
        self.assertEqual(len(evs), 1)

    def test_store_dedups_across_cycles(self):
        from data.events.google_news import GoogleNewsTopicAdapter

        def fake_fetch(url, user_agent=None, **kw):
            import urllib.parse
            if "Federal Reserve" not in urllib.parse.unquote(url):
                return []
            return [_item("FOMC minutes signal patience - WSJ", "https://wsj.com/fomc")]

        with patch("data.events.google_news.fetch_rss", side_effect=fake_fetch):
            first = store.insert_events_returning_new(GoogleNewsTopicAdapter().poll([]))
            second = store.insert_events_returning_new(GoogleNewsTopicAdapter().poll([]))
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0, "re-poll must dedup on (source, external_id)")


class TestCapAndJunk(TopicTestCase):
    def test_cap_15_per_topic_keeps_newest(self):
        from data.events.google_news import GoogleNewsTopicAdapter

        def fake_fetch(url, user_agent=None, **kw):
            import urllib.parse
            if "Federal Reserve" not in urllib.parse.unquote(url):
                return []
            return [_item(f"Inflation story number {i} - Reuters",
                          f"https://reuters.com/{i}",
                          published=NOW - timedelta(minutes=i))
                    for i in range(40)]

        with patch("data.events.google_news.fetch_rss", side_effect=fake_fetch):
            evs = GoogleNewsTopicAdapter().poll([])
        self.assertEqual(len(evs), 15)
        headlines = [e.headline for e in evs]
        self.assertIn("Inflation story number 0", headlines)   # newest kept
        self.assertNotIn("Inflation story number 39", headlines)  # oldest dropped
        # Newest first
        self.assertEqual(headlines[0], "Inflation story number 0")

    def test_junk_and_unsafe_urls_filtered(self):
        from data.events.google_news import GoogleNewsTopicAdapter

        def fake_fetch(url, user_agent=None, **kw):
            import urllib.parse
            if "Federal Reserve" not in urllib.parse.unquote(url):
                return []
            return [
                _item("Fed cuts rates by 25bp - Reuters", "https://reuters.com/cut"),
                # is_junk_news: third-party SEO spam marker
                _item("Analyst issues optimistic forecast for inflation plays - Zacks",
                      "https://zacks.com/x"),
                # is_safe_news_url: messaging-app link can't be real news
                _item("Inflation chat group - Spam", "https://chat.whatsapp.com/abc"),
            ]

        with patch("data.events.google_news.fetch_rss", side_effect=fake_fetch):
            evs = GoogleNewsTopicAdapter().poll([])
        self.assertEqual([e.headline for e in evs], ["Fed cuts rates by 25bp"])

    def test_stale_items_skipped_by_cutoff(self):
        from data.events.google_news import GoogleNewsTopicAdapter

        def fake_fetch(url, user_agent=None, **kw):
            import urllib.parse
            if "Federal Reserve" not in urllib.parse.unquote(url):
                return []
            return [_item("Old Fed story - Reuters", "https://reuters.com/old",
                          published=NOW - timedelta(days=5))]

        with patch("data.events.google_news.fetch_rss", side_effect=fake_fetch):
            evs = GoogleNewsTopicAdapter().poll([])  # default 48h lookback
        self.assertEqual(evs, [])


class TestReadApi(TopicTestCase):
    def _seed(self):
        from data.events.base import Event
        from data.events.store import topic_ticker, TOPIC_SOURCE

        def topic_ev(cat, headline, age_hours, url="https://example.com/a"):
            return Event(ticker=topic_ticker(cat), source=TOPIC_SOURCE,
                         event_type="topic_news", headline=headline,
                         published_at=NOW - timedelta(hours=age_hours), url=url,
                         external_id=f"{cat}::{headline.lower().replace(' ', '-')}",
                         raw={"category": cat})

        bank_ev = Event(ticker="JPM", source="google_news", event_type="press_release",
                        headline="JPMorgan announces dividend",
                        published_at=NOW - timedelta(hours=1),
                        url="https://example.com/jpm", external_id="JPM::div")
        n = store.insert_events([
            topic_ev("macro", "Fed holds rates", 2),
            topic_ev("macro", "CPI surprise", 5),
            topic_ev("macro", "Ancient inflation print", 30),   # outside 24h
            topic_ev("geopolitical", "New sanctions on exports", 3),
            bank_ev,
        ])
        self.assertEqual(n, 5)

    def test_get_topic_news_filters_category_and_window(self):
        self._seed()
        rows = store.get_topic_news("macro", hours=24)
        self.assertEqual([r["headline"] for r in rows],
                         ["Fed holds rates", "CPI surprise"])  # newest first, 30h-old excluded
        self.assertTrue(all(r["category"] == "macro" for r in rows))
        self.assertTrue(all(r["ticker"] == "TOPIC:MACRO" for r in rows))
        # Wider window picks up the old one
        self.assertEqual(len(store.get_topic_news("macro", hours=48)), 3)
        # Other categories / unknown categories
        self.assertEqual(len(store.get_topic_news("geopolitical")), 1)
        self.assertEqual(store.get_topic_news("nonsense"), [])

    def test_topic_rows_excluded_from_default_universe_view(self):
        self._seed()
        rows = store.get_universe_recent(limit=50)
        self.assertEqual([r["ticker"] for r in rows], ["JPM"],
                         "bank-activity panels must never see topic rows")
        # Explicit source filter still reaches them if ever needed
        rows = store.get_universe_recent(limit=50, sources=["google_news_topic"])
        self.assertEqual(len(rows), 4)


if __name__ == "__main__":
    unittest.main()
