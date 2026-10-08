"""FDIC quarter fetch: the 500-row page cap and failures that never pass as data.

Incident 2026-10-05..08: FDIC rejects ``limit`` > 500 once a request names more
than 250 fields (measured 2026-10-08: 250 fields at limit 1000 → 200, 251+ →
400 "Number must be less than or equal to 500"). #272 took the base field set
to 253, so every ``fetch_quarter_financials`` call 400'd. ``_fetch_fin_page``
swallowed the error into ``[]``, so the As-of screen said "No FDIC filings
reconstructed for Q4 2021" (and cached that for 24h), the Trends grid failed
its nightly build, and earnings prior/YoY comparisons went blank.

Pins:
  1. every page request asks for ≤ 500 rows — and the live field set is > 250,
     so the cap is the binding one;
  2. a full 500-row page is followed by the next offset (pagination), within
     a cert chunk too;
  3. an HTTP error or exhausted 429 retries RAISES FdicQuarterFetchError —
     never an empty dict that reads as "nobody filed";
  4. as_of_quarter_metrics propagates the error and caches nothing; an empty
     (not-yet-published) quarter is not cached either.

Run: python -m unittest tests.test_fdic_quarter_page_cap
"""
import unittest
from unittest.mock import patch

import requests

from tests import _streamlit_stub

_streamlit_stub.install()

import data.cache as cache  # noqa: E402
import data.as_of_metrics as aom  # noqa: E402
import data.fdic_client as fc  # noqa: E402
from config import get_fdic_fields  # noqa: E402
from tests.test_cache_read_ceilings import _IsolatedCache  # noqa: E402


class _Resp:
    def __init__(self, n, start=0):
        self._rows = [{"data": {"CERT": start + i, "REPDTE": "20211231"}}
                      for i in range(n)]

    def json(self):
        return {"data": self._rows}


class TestPageCap(unittest.TestCase):
    def test_live_field_set_is_past_the_250_threshold(self):
        n = len(fc._BASE_FINANCIALS_FIELDS | get_fdic_fields())
        self.assertGreater(n, 250, "if this drops, the cap still holds — keep it")
        self.assertLessEqual(fc._FIN_PAGE, 500)

    def test_whole_system_paginates_in_500s(self):
        sizes = {0: 500, 500: 500, 1000: 37}
        calls = []

        def fake(url, params):
            calls.append((params["limit"], params["offset"]))
            return _Resp(sizes[params["offset"]], start=params["offset"])
        with patch.object(fc, "_get_with_retry", side_effect=fake), \
             patch.object(fc, "_coerce_fin_record", side_effect=lambda d: d):
            out = fc.fetch_quarter_financials("20211231")
        self.assertEqual(calls, [(500, 0), (500, 500), (500, 1000)])
        self.assertEqual(len(out), 1037)

    def test_cert_chunk_paginates_too(self):
        calls = []

        def fake(url, params):
            calls.append(params["offset"])
            return _Resp(500 if params["offset"] == 0 else 3, start=params["offset"])
        with patch.object(fc, "_get_with_retry", side_effect=fake), \
             patch.object(fc, "_coerce_fin_record", side_effect=lambda d: d):
            out = fc.fetch_quarter_financials("20211231", certs=[1, 2])
        self.assertEqual(calls, [0, 500])
        self.assertEqual(len(out), 503)


class TestFailuresRaise(unittest.TestCase):
    def test_http_error_raises(self):
        err = requests.HTTPError("400 Client Error: Bad Request")
        with patch.object(fc, "_get_with_retry", side_effect=err):
            with self.assertRaises(fc.FdicQuarterFetchError):
                fc.fetch_quarter_financials("20211231", certs=[628])

    def test_exhausted_429_raises(self):
        with patch.object(fc, "_get_with_retry", return_value=None):
            with self.assertRaises(fc.FdicQuarterFetchError):
                fc.fetch_quarter_financials("20211231")

    def test_second_chunk_failure_is_not_a_partial_result(self):
        seen = []

        def fake(url, params):
            seen.append(params["filters"])
            if len(seen) == 2:
                raise requests.ConnectionError("reset")
            return _Resp(1)
        with patch.object(fc, "_get_with_retry", side_effect=fake), \
             patch.object(fc, "_coerce_fin_record", side_effect=lambda d: d):
            with self.assertRaises(fc.FdicQuarterFetchError):
                fc.fetch_quarter_financials("20211231", certs=range(1, 300))


class TestAsOfNeverCachesAFailureOrEmpty(_IsolatedCache):
    KEY = "as_of_metrics:v2:20211231:1:w2"

    def test_error_propagates_and_nothing_is_cached(self):
        with patch.object(fc, "fetch_quarter_financials",
                          side_effect=fc.FdicQuarterFetchError("400")):
            with self.assertRaises(fc.FdicQuarterFetchError):
                aom.as_of_quarter_metrics("2021-12-31", {628: "JPM"}, window=2)
        self.assertIsNone(cache.get(self.KEY, max_age_s=None))

    def test_unpublished_quarter_is_not_cached(self):
        with patch.object(fc, "fetch_quarter_financials", return_value={}):
            self.assertEqual(aom.as_of_quarter_metrics("2021-12-31", {628: "JPM"},
                                                       window=2), [])
        self.assertIsNone(cache.get(self.KEY, max_age_s=None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
