"""Pin the earnings-aware transcript-list TTL (data/fmp_transcripts.py,
2026-07-14): FBK's Jul-14 call transcript was live at FMP but hidden behind a
morning-cached 12h availability list. When the newest listed call is ≥75 days
old (the next quarterly call is due), the list must refresh every 30 min; a
current-quarter list keeps the cheap 12h TTL.

Run: python -m unittest tests.test_fmp_transcripts_ttl
"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.fmp_transcripts as tx


def _rows(days_old: int) -> list[dict]:
    d = (date.today() - timedelta(days=days_old)).isoformat()
    return [{"quarter": 1, "year": 2026, "date": d}]


class TestNextCallDue(unittest.TestCase):
    def test_stale_quarter_is_due(self):
        self.assertTrue(tx._next_call_due(_rows(91)))

    def test_current_quarter_not_due(self):
        self.assertFalse(tx._next_call_due(_rows(20)))

    def test_no_coverage_not_due(self):
        self.assertFalse(tx._next_call_due([]))
        self.assertFalse(tx._next_call_due([{"quarter": 1, "year": 2026,
                                             "date": None}]))


class TestDueTtlBehavior(unittest.TestCase):
    def setUp(self):
        self._orig = (tx._has_key, tx._get, tx._cache_get, tx._cache_put)
        tx._has_key = lambda: True
        self.fetches = []

    def tearDown(self):
        (tx._has_key, tx._get, tx._cache_get, tx._cache_put) = self._orig

    def test_due_list_older_than_30min_refetches(self):
        stale = _rows(91)
        # 12h read hits (cached 2h ago), 30-min read misses → must refetch.
        tx._cache_get = lambda ck, ttl: (stale if ttl == tx._DATES_TTL_SECONDS
                                         else None)
        tx._cache_put = lambda ck, v: None
        tx._get = lambda p, params, timeout=10: (self.fetches.append(1),
                                                 [{"quarter": 2, "fiscalYear": 2026,
                                                   "date": date.today().isoformat()}])[1]
        out = tx.get_transcript_dates("FBK")
        self.assertEqual(len(self.fetches), 1, "due list must refetch")
        self.assertEqual(out[0]["quarter"], 2)

    def test_due_list_fresh_within_30min_serves(self):
        stale = _rows(91)
        tx._cache_get = lambda ck, ttl: stale        # hits at BOTH TTLs
        tx._get = lambda *a, **k: self.fail("must not fetch when fresh-within-30min")
        self.assertEqual(tx.get_transcript_dates("FBK"), stale)

    def test_current_quarter_list_serves_on_12h_ttl(self):
        current = _rows(20)
        tx._cache_get = lambda ck, ttl: (current if ttl == tx._DATES_TTL_SECONDS
                                         else self.fail("30-min path must not run"))
        tx._get = lambda *a, **k: self.fail("must not fetch within 12h TTL")
        self.assertEqual(tx.get_transcript_dates("FBK"), current)


if __name__ == "__main__":
    unittest.main(verbosity=2)
