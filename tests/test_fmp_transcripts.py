"""data.fmp_transcripts — parsing + caching of FMP transcript endpoints.

The HTTP layer (fmp_client._get) and the key check are monkeypatched, so the
tests never hit the network. Verifies dates parsing/sorting and the full
transcript shape, including tolerant field names and miss handling.
"""
import unittest

import data.fmp_transcripts as tx


class _Patch(unittest.TestCase):
    def setUp(self):
        self._orig_get = tx._get
        self._orig_haskey = tx._has_key
        self._orig_cget = tx._cache_get
        self._orig_cput = tx._cache_put
        tx._has_key = lambda: True
        tx._cache_get = lambda k, ttl: None      # always a cache miss
        tx._cache_put = lambda k, v: None

    def tearDown(self):
        tx._get = self._orig_get
        tx._has_key = self._orig_haskey
        tx._cache_get = self._orig_cget
        tx._cache_put = self._orig_cput


class TestTranscriptDates(_Patch):
    def test_parsed_and_sorted_newest_first(self):
        tx._get = lambda path, params: [
            {"quarter": 1, "fiscalYear": 2025, "date": "2025-04-11"},
            {"quarter": 3, "fiscalYear": 2024, "date": "2024-10-11"},
            {"quarter": 4, "fiscalYear": 2024, "date": "2025-01-15"},
        ]
        out = tx.get_transcript_dates("jpm")
        # newest first: 2025Q1, then 2024Q4, then 2024Q3
        self.assertEqual([(d["quarter"], d["year"]) for d in out],
                         [(1, 2025), (4, 2024), (3, 2024)])
        self.assertEqual(out[0], {"quarter": 1, "year": 2025, "date": "2025-04-11"})

    def test_tolerates_year_alias_and_bad_rows(self):
        tx._get = lambda path, params: [
            {"quarter": 2, "year": 2023, "date": "2023-07-14"},  # 'year' not 'fiscalYear'
            {"quarter": None, "fiscalYear": 2023},               # dropped (no quarter)
            "garbage",                                            # dropped
        ]
        out = tx.get_transcript_dates("ABC")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["year"], 2023)

    def test_empty_on_non_list(self):
        tx._get = lambda path, params: {"Error Message": "nope"}
        self.assertEqual(tx.get_transcript_dates("ABC"), [])


class TestTranscriptBody(_Patch):
    def test_full_transcript_shape(self):
        tx._get = lambda path, params: [
            {"symbol": "JPM", "period": "Q1", "year": 2025,
             "date": "2025-04-11", "content": "Operator: Hello.\nCEO: Hi."}
        ]
        rec = tx.get_transcript("jpm", 2025, 1)
        self.assertEqual(rec["quarter"], 1)
        self.assertEqual(rec["year"], 2025)
        self.assertEqual(rec["date"], "2025-04-11")
        self.assertIn("Operator:", rec["content"])

    def test_missing_content_returns_none(self):
        tx._get = lambda path, params: [{"symbol": "JPM", "content": ""}]
        self.assertIsNone(tx.get_transcript("JPM", 2025, 1))

    def test_empty_list_returns_none(self):
        tx._get = lambda path, params: []
        self.assertIsNone(tx.get_transcript("JPM", 2025, 1))


if __name__ == "__main__":
    unittest.main()
