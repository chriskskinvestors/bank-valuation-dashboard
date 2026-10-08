"""
2026-10-08: GlobeNewswire began refusing server fetches (Akamai "Access
Denied"); the pending pass skipped TowneBank's unreachable blueharbor story
as if it were Business Wire's permanent 403, and the live deal vanished.
"""
from __future__ import annotations

import datetime as _dt
import unittest
from unittest.mock import patch

GNW = ("https://www.globenewswire.com/news-release/2026/10/06/3375489/10357/en/"
       "townebank-enhances-north-carolina-presence-through-agreement-to-acquire-"
       "blueharbor-bank.html")


class _FakeDate(_dt.date):
    @classmethod
    def today(cls):
        return _dt.date(2026, 10, 8)


def _prs(url):
    return [{"title": "TowneBank Enhances North Carolina Presence Through Agreement To "
                      "Acquire blueharbor bank",
             "url": url, "published_at": "2026-10-06 08:00:00", "text": "TowneBank"}]


class TestUnreachableStory(unittest.TestCase):

    def _run(self, url):
        from data import ma_pending
        with patch("data.ma_pending._wire_releases", return_value=_prs(url)), \
             patch("data.ma_pending._wire_story_text", return_value=None), \
             patch("data.events.fmp_news._is_subject", return_value=True), \
             patch("data.ma_pending.time.sleep", lambda *_: None), \
             patch("data.ma_pending.date", _FakeDate):
            return ma_pending.find_pending_wire("TOWN", "TowneBank")

    def test_blocked_globenewswire_story_is_a_failure(self):
        # ok=False: the bank keeps its previous pending rows
        self.assertEqual(self._run(GNW), ([], False))

    def test_business_wire_permanent_block_is_still_skipped(self):
        self.assertEqual(self._run("https://www.businesswire.com/news/home/x/en/"), ([], True))


class TestStoryCache(unittest.TestCase):

    def test_successful_fetch_is_served_when_the_site_later_refuses(self):
        from data import ma_announcements as ma
        store = {}
        body = "<p>" + "TowneBank will acquire blueharbor bank. " * 30 + "</p>"
        with patch("data.cache.get", side_effect=lambda k, max_age_s=None: store.get(k)), \
             patch("data.cache.put", side_effect=lambda k, v: store.__setitem__(k, v)):
            with patch("data.otc_release._fetch_story", return_value=body):
                first = ma._wire_story_text(GNW)
            with patch("data.otc_release._fetch_story", return_value=None) as f:
                again = ma._wire_story_text(GNW)
        self.assertTrue(first and "blueharbor" in first)
        self.assertEqual(again, first)
        f.assert_not_called()

    def test_access_denied_page_is_never_cached(self):
        from data import ma_announcements as ma
        store = {}
        denied = "<h1>Access Denied</h1> You don't have permission " + "x" * 600
        with patch("data.cache.get", side_effect=lambda k, max_age_s=None: store.get(k)), \
             patch("data.cache.put", side_effect=lambda k, v: store.__setitem__(k, v)), \
             patch("data.otc_release._fetch_story", return_value=denied):
            ma._wire_story_text(GNW)
        self.assertEqual(store, {})


if __name__ == "__main__":
    unittest.main()
