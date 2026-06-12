"""
Topic-news curation rules (data/events/topic_curation.py).

Pins the 2026-06-12 user decision: Overnight & Breaking shows reputable
sources only, relevance-ranked — the exact chaff the user flagged must
never pass.

Run: python -m unittest tests.test_topic_curation
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.events.topic_curation import curate_topic_news


def _item(headline, source):
    return {"headline": headline, "source_name": source,
            "url": "https://example.com/x"}


class TestTopicCuration(unittest.TestCase):

    def test_flagged_chaff_is_rejected(self):
        # The literal items the user flagged on the live Home page
        chaff = [
            _item("The Weekly Closeout: Trump loves inflation and Abercrombie "
                  "opens a 'pinnacle' store in SoHo", "Modern Retail"),
            _item("Bad Bunny concerts resulted in hotel and restaurant "
                  "inflation", "Local 10"),
            _item("Congressional hopefuls talk inflation in Daytona Beach",
                  "Daytona Beach News-Journal"),
        ]
        self.assertEqual(curate_topic_news(chaff, "macro"), [])

    def test_reputable_relevant_passes(self):
        items = [
            _item("ECB's Kazimir Says Rates Must Be Lifted More to Tackle "
                  "Inflation", "Bloomberg"),
            _item("Fed seen holding rates as inflation cools", "Reuters"),
        ]
        out = curate_topic_news(items, "macro")
        self.assertEqual(len(out), 2)

    def test_reputable_but_irrelevant_rejected(self):
        # Right outlet, lifestyle story — keyword gate must still apply
        items = [_item("The 10 best beach reads of the summer",
                       "New York Times")]
        self.assertEqual(curate_topic_news(items, "macro"), [])

    def test_relevant_but_unknown_source_rejected(self):
        items = [_item("Fed expected to cut rates in September",
                       "Hometown Gazette")]
        self.assertEqual(curate_topic_news(items, "macro"), [])

    def test_more_hits_rank_higher_and_limit_applies(self):
        items = [
            _item("Stocks edge up", "Reuters"),
            _item("Stocks rally as bond yields fall and bank earnings beat",
                  "Bloomberg"),
        ]
        out = curate_topic_news(items, "markets", limit=1)
        self.assertEqual(len(out), 1)
        self.assertIn("rally", out[0]["headline"])

    def test_near_duplicate_headlines_deduped(self):
        items = [
            _item("Fed holds rates steady as inflation cools in May",
                  "Reuters"),
            _item("Fed holds rates steady as inflation cools in May report",
                  "Bloomberg"),
        ]
        self.assertEqual(len(curate_topic_news(items, "macro")), 1)

    def test_missing_source_name_rejected(self):
        items = [{"headline": "Fed cuts rates", "url": "https://x.com"}]
        self.assertEqual(curate_topic_news(items, "macro"), [])

    def test_sports_with_relevance_keyword_still_rejected(self):
        # Live leak 2026-06-12: 'sanctions' keyword let a college-sports
        # story onto GEOPOLITICAL. Stop-list must beat keyword hits.
        items = [_item("Paxton warns Big 12 of potential legal action over "
                       "any Texas Tech Sorsby sanctions", "The Hill")]
        self.assertEqual(curate_topic_news(items, "geopolitical"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
