"""
(REVIEW-2026-09-24 P1-4) Two as-of caches could NEVER hit.

Both writers stamped ``cached_at`` with the as-of QUARTER date (already part of
the key) instead of the write time, so the reader's ``is_fresh(cached, ttl)``
was false for every past quarter and each Run of the As-of screen re-did the
network work the cache exists to avoid:

  - data/as_of_metrics.as_of_quarter_metrics: ``fetch_quarter_financials``
    for every quarter of the window (20 FDIC slices per Run);
  - data/entity_graph.lineage_predecessors: a full paginated walk of the FDIC
    history endpoint per Run.

Pins (isolated in-memory engine, network seams faked + counted):
  1. the same past quarter costs ONE fetch round; the second call is served
     from the cache and equals the first;
  2. the stored ``cached_at`` is the write time (parses to within a minute of
     now), not the quarter date.

Both pins were confirmed RED against the pre-fix stamp (second call
re-fetched; ``cached_at`` == quarter date).
"""
import unittest
from datetime import datetime
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

import data.cache as cache  # noqa: E402
import data.as_of_metrics as aom  # noqa: E402
import data.entity_graph as eg  # noqa: E402
import data.fdic_client as fdic_client  # noqa: E402
import data.http as http  # noqa: E402
from tests.test_cache_read_ceilings import _IsolatedCache  # noqa: E402


def _assert_stamped_now(test, row):
    stamped = datetime.fromisoformat(row["cached_at"])
    test.assertLess(abs((datetime.now() - stamped).total_seconds()), 60,
                    f"cached_at {row['cached_at']!r} is not the write time")


class TestAsOfQuarterMetricsCacheHits(_IsolatedCache):
    QUARTER = "2023-06-30"          # a PAST quarter: the case that never hit
    CERTS = {123: "ABC"}
    WINDOW = 3

    def test_same_past_quarter_fetches_once(self):
        calls = []

        def fake_fetch(repdte, certs=None):
            calls.append(repdte)
            return {123: {"cert": 123, "repdte": repdte}}

        with patch.object(fdic_client, "fetch_quarter_financials",
                          side_effect=fake_fetch), \
                patch("analysis.metrics.build_bank_metrics",
                      return_value={"total_assets": 1.0}):
            first = aom.as_of_quarter_metrics(self.QUARTER, self.CERTS,
                                              window=self.WINDOW)
            second = aom.as_of_quarter_metrics(self.QUARTER, self.CERTS,
                                               window=self.WINDOW)
        # One window of fetches (Q, Q-1, Q-2) — never a second round.
        self.assertEqual(calls, ["20230630", "20230331", "20221231"])
        self.assertEqual(first[0]["ticker"], "ABC")
        self.assertEqual(first[0]["_as_of"], "20230630")
        self.assertEqual(second, first)

    def test_cached_at_is_write_time_not_quarter(self):
        with patch.object(fdic_client, "fetch_quarter_financials",
                          return_value={123: {"cert": 123}}), \
                patch("analysis.metrics.build_bank_metrics",
                      return_value={"total_assets": 1.0}):
            aom.as_of_quarter_metrics(self.QUARTER, self.CERTS, window=self.WINDOW)
        row = cache.get(f"as_of_metrics:20230630:1:w{self.WINDOW}", max_age_s=None)
        self.assertIsNotNone(row)
        _assert_stamped_now(self, row)


class _Resp:
    def __init__(self, rows):
        self._rows = rows

    def json(self):
        return {"data": [{"data": d} for d in self._rows]}


class TestLineagePredecessorsCacheHits(_IsolatedCache):
    SINCE = "2025-01-01"
    ROWS = [{"EFFDATE": "2025-06-01", "SUR_CERT": "100", "OUT_CERT": "900",
             "OUT_INSTNAME": "Old Bank A"}]

    def _fake_get(self, calls):
        def fake(url, params, timeout=40):
            calls.append(params.get("offset", 0))
            return _Resp(self.ROWS)      # < 1000 rows → single page
        return fake

    def test_same_since_date_walks_history_once(self):
        calls = []
        with patch.object(http, "get_with_retry", side_effect=self._fake_get(calls)):
            first = eg.lineage_predecessors({100}, self.SINCE)
            second = eg.lineage_predecessors({100}, self.SINCE)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first, {900: {"name": "Old Bank A", "survivor_cert": 100,
                                       "date": "2025-06-01"}})
        self.assertEqual(second, first)

    def test_cached_at_is_write_time_not_since_date(self):
        with patch.object(http, "get_with_retry", side_effect=self._fake_get([])):
            eg.lineage_predecessors({100}, self.SINCE)
        row = cache.get(f"entity_lineage:{self.SINCE}:1", max_age_s=None)
        self.assertIsNotNone(row)
        _assert_stamped_now(self, row)


if __name__ == "__main__":
    unittest.main()
