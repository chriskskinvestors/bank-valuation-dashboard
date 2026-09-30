"""
A failed People Summary extraction is retried at most every 6 hours, not on
every view (REVIEW-2026-09-24 P1-11).

get_proxy_people downloads the full DEF 14A (up to 400 KB of text) and calls
Claude on a cache miss. A failure (text unavailable, API error, or no rows
surviving the hallucination guards) returned None WITHOUT caching anything, so
every later view repeated the download and the paid LLM call (measured 67–80 s
renders). It now records a short-lived failure marker keyed by the proxy
accession; the marker is not written when this environment has no API key.
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import data.people as people

PROXY = {"form": "DEF 14A", "accession": "0000004242-26-000005",
         "url": "https://sec.gov/p.htm", "date": "2026-03-10"}
TEXT = ("Director nominees. " + "x " * 1500 +
        "Jane Roe, 61, has served as a director since 2015. John Smith, 55, CEO.")


class _Store:
    def __init__(self):
        self.files = {}

    def load(self, prefix, fname):
        return self.files.get((prefix, fname))

    def save(self, prefix, fname, data):
        self.files[(prefix, fname)] = data
        return True


class TestFailureMarker(unittest.TestCase):
    def setUp(self):
        self.store = _Store()
        self.fetches = []
        self._patches = [
            patch.object(people, "_latest_proxy", lambda cik: dict(PROXY)),
            patch("data.cloud_storage.load_json", self.store.load),
            patch("data.cloud_storage.save_json", self.store.save),
            patch("data.filing_summarizer.fetch_filing_text",
                  lambda url, max_chars=None: self.fetches.append(url) or TEXT),
            patch.object(people, "_api_key", lambda: "sk-test"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _fname(self):
        return ("people_cache", "4242_000000424226000005.json")

    def test_api_failure_not_retried_on_next_view(self):
        with patch.object(people, "_extract_via_claude", lambda t, k: None) as _:
            self.assertIsNone(people.get_proxy_people(4242, "TST"))
            self.assertIsNone(people.get_proxy_people(4242, "TST"))
        # Pre-fix: two downloads (one per view).
        self.assertEqual(1, len(self.fetches))
        self.assertEqual("extraction call failed", self.store.files[self._fname()]["failed"])

    def test_no_guarded_rows_not_retried_on_next_view(self):
        hallucinated = [{"name": "Nobody Here", "role": "director"}]
        with patch.object(people, "_extract_via_claude", lambda t, k: hallucinated):
            self.assertIsNone(people.get_proxy_people(4242, "TST"))
            self.assertIsNone(people.get_proxy_people(4242, "TST"))
        self.assertEqual(1, len(self.fetches))

    def test_retry_after_window(self):
        old = (datetime.now() - timedelta(seconds=people._FAILED_RETRY_S + 60)).isoformat()
        self.store.files[self._fname()] = {"failed": "extraction call failed",
                                           "cached_at": old}
        rows = [{"name": "Jane Roe", "role": "director", "age": 61}]
        with patch.object(people, "_extract_via_claude", lambda t, k: rows):
            got = people.get_proxy_people(4242, "TST")
        self.assertEqual(1, len(self.fetches))
        self.assertEqual("Jane Roe", got["people"][0]["name"])
        # The success replaces the marker.
        self.assertNotIn("failed", self.store.files[self._fname()])

    def test_success_is_cached_permanently(self):
        rows = [{"name": "Jane Roe", "role": "director", "age": 61}]
        with patch.object(people, "_extract_via_claude", lambda t, k: rows):
            people.get_proxy_people(4242, "TST")
            people.get_proxy_people(4242, "TST")
        self.assertEqual(1, len(self.fetches))

    def test_no_api_key_writes_no_marker(self):
        with patch.object(people, "_api_key", lambda: None), \
                patch.object(people, "_extract_via_claude", lambda t, k: None):
            self.assertIsNone(people.get_proxy_people(4242, "TST"))
        self.assertNotIn(self._fname(), self.store.files)


if __name__ == "__main__":
    unittest.main()
