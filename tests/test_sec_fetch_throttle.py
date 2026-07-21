"""
Pins the 2026-07-20 SEC-fetch storm fix.

sec_filing_scraper._get was raw urllib — no retry, no 429 handling, no rate
limit, 60s timeout. This module is the SEC provider's fetch choke point, called
per-ticker across the whole universe by the valuation reported_tbvps path. A
universe walk (532 banks) burst past SEC's ~10 req/s cap, got HTTP-429 stormed,
and the hung calls ran refresh-universe into its 1800s task timeout.

Pinned here:
  • _get routes through data/http.py's shared retry policy and returns bytes.
  • On 429-exhaustion (get_with_retry -> None) _get RAISES, so a caller's
    try/except fails soft (transient throttle => None UNcached) instead of
    json.loads(None) crashing.
  • the SEC throttle enforces a min interval so a parallel burst can't storm.

Offline: get_with_retry / requests are mocked; no network.
"""
import sys
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

import data.sec_filing_scraper as sfs  # noqa: E402


class _Resp:
    def __init__(self, content=b"", data=None):
        self.content = content
        self._data = data

    def json(self):
        return self._data


class TestGetRoutesThroughSharedPolicy(unittest.TestCase):
    def test_get_returns_response_content_bytes(self):
        with patch("data.http.get_with_retry",
                   return_value=_Resp(content=b'{"ok":1}')) as gw:
            out = sfs._get("https://data.sec.gov/x.json")
        self.assertEqual(out, b'{"ok":1}')
        # Went through the shared policy with a bounded (not 60s) timeout + UA.
        _, kwargs = gw.call_args
        self.assertEqual(kwargs.get("headers"), sfs._UA)
        self.assertLessEqual(kwargs.get("timeout"), 30)

    def test_get_raises_when_429s_exhaust_not_returns_none(self):
        # get_with_retry returns None when every attempt was a 429. _get must
        # RAISE (caller catches -> None uncached), never return None which would
        # make json.loads(_get(...)) throw a confusing TypeError instead.
        with patch("data.http.get_with_retry", return_value=None):
            with self.assertRaises(Exception):
                sfs._get("https://data.sec.gov/x.json")

    def test_no_raw_urllib_fetch_left_in_module(self):
        # Guard the anti-pattern specifically (a raw urlopen bypasses the shared
        # retry policy + throttle), not the word "urllib" in prose.
        import inspect
        src = inspect.getsource(sfs)
        self.assertNotIn("import urllib", src)
        self.assertNotIn("urlopen", src)


class TestSecThrottle(unittest.TestCase):
    def setUp(self):
        sfs._sec_last[0] = 0.0

    def test_serial_calls_are_spaced_by_the_min_interval(self):
        calls = []
        with patch("data.http.get_with_retry",
                   side_effect=lambda *a, **k: (calls.append(time.monotonic())
                                                or _Resp(content=b"x"))):
            for _ in range(4):
                sfs._get("https://data.sec.gov/x")
        gaps = [b - a for a, b in zip(calls, calls[1:])]
        for g in gaps:
            self.assertGreaterEqual(
                g, sfs._SEC_MIN_INTERVAL * 0.8,
                "consecutive SEC fetches must respect the throttle interval")

    def test_parallel_burst_is_rate_limited_not_simultaneous(self):
        # The bug's signature was dozens of 429s in the SAME millisecond — a
        # parallel burst. The throttle must serialize them under the cap.
        stamps = []
        with patch("data.http.get_with_retry",
                   side_effect=lambda *a, **k: (stamps.append(time.monotonic())
                                                or _Resp(content=b"x"))):
            with ThreadPoolExecutor(max_workers=8) as ex:
                list(ex.map(lambda _: sfs._get("https://data.sec.gov/x"),
                            range(8)))
        stamps.sort()
        span = stamps[-1] - stamps[0]
        # 8 calls at ~9/s can't all land in a sub-10ms burst.
        self.assertGreaterEqual(span, sfs._SEC_MIN_INTERVAL * 5,
                                "parallel SEC fetches must be throttled, not burst")


if __name__ == "__main__":
    unittest.main()
