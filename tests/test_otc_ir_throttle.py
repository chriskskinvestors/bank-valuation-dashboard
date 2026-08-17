"""(2026-08-17) IR-site crawl throttle in otc_release_metrics.

The two-hop bank-site crawl (_latest_ir_release) costs 30-84s for the slow
banks, and the metrics build re-entered it EVERY 30 MINUTES for ~80 no-wire
banks once the 15-min envelope serve lapsed — the slowest 10 banks alone were
29% of a 1741s build (measured 2026-08-03: NARA 84s, TCNB 64s). Releases are
quarterly, so the crawl now runs at most once per _IR_CRAWL_TTL_S (24h) per
bank, marked by ir_checked_at on the cache envelope.

Pins:
  1. within the TTL, a pr-less bank serves the envelope WITHOUT crawling —
     the exact per-build cost that was 29% of the build;
  2. past the TTL, the crawl runs again;
  3. first-ever discovery (no marker) crawls immediately;
  4. a garbled marker crawls (never wedges a bank into permanent no-crawl);
  5. the wire-PR path is untouched by the throttle;
  6. re-stamps PRESERVE the marker (cached_at moves on every serve, so it
     cannot be the throttle clock).

Run: python -m unittest tests.test_otc_ir_throttle
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

import data.otc_release as otc  # noqa: E402


def _envelope(minutes_ago_cached: float, ir_checked_minutes_ago=None,
              value=None):
    env = {
        "cached_at": (datetime.now()
                      - timedelta(minutes=minutes_ago_cached)).isoformat(),
        "value": value if value is not None else {
            "url": "https://example.com/old", "qend": "2026-06-30",
            "metrics": {"tbv_ps": 12.34}},
    }
    if ir_checked_minutes_ago is not None:
        env["ir_checked_at"] = (
            datetime.now()
            - timedelta(minutes=ir_checked_minutes_ago)).isoformat()
    return env


class TestIrCrawlThrottle(unittest.TestCase):
    def _run(self, envelope, pr=None):
        """Drive otc_release_metrics with a stubbed cache + sources; returns
        (result, ir_crawl_call_count, last_put)."""
        calls = {"ir": 0}
        puts = {}

        def _ir(ticker):
            calls["ir"] += 1
            return None

        # otc_release_metrics does `from data import cache as _cache` at call
        # time, so patching data.cache's functions intercepts it.
        with patch("data.cache.get", return_value=envelope), \
                patch("data.cache.put",
                      side_effect=lambda k, v: puts.update({k: v})), \
                patch.object(otc, "_latest_earnings_pr", return_value=pr), \
                patch.object(otc, "_latest_ir_release", side_effect=_ir), \
                patch.object(otc, "_fetch_story", return_value=None):
            result = otc.otc_release_metrics("XXXX")
        return result, calls["ir"], (next(iter(puts.values())) if puts else None)

    def test_within_ttl_serves_envelope_without_crawling(self):
        env = _envelope(minutes_ago_cached=60, ir_checked_minutes_ago=120)
        result, crawls, _ = self._run(env)
        self.assertEqual(crawls, 0, "the crawl is the 29%% — it must not run")
        self.assertEqual((result or {}).get("url"), "https://example.com/old")

    def test_past_ttl_crawls_again(self):
        env = _envelope(minutes_ago_cached=60,
                        ir_checked_minutes_ago=25 * 60)   # >24h
        _, crawls, put = self._run(env)
        self.assertEqual(crawls, 1)
        self.assertIsNotNone(put, "attempt must be marked")
        self.assertIn("ir_checked_at", put)

    def test_first_discovery_crawls_immediately(self):
        env = _envelope(minutes_ago_cached=60)             # no marker
        _, crawls, put = self._run(env)
        self.assertEqual(crawls, 1)
        self.assertIn("ir_checked_at", put)

    def test_garbled_marker_crawls(self):
        env = _envelope(minutes_ago_cached=60)
        env["ir_checked_at"] = "not-a-timestamp"
        _, crawls, _ = self._run(env)
        self.assertEqual(crawls, 1)

    def test_wire_path_ignores_throttle(self):
        env = _envelope(minutes_ago_cached=60, ir_checked_minutes_ago=120)
        pr = {"url": "https://example.com/old", "title": "Q2 results",
              "published_at": "2026-08-01T12:00:00"}
        # Same story URL -> serve prev via the wire path; the IR crawl must
        # not run, and NOT because of the throttle (marker is fresh here) —
        # assert the wire branch was taken by checking the serve.
        result, crawls, _ = self._run(env, pr=pr)
        self.assertEqual(crawls, 0)
        self.assertEqual((result or {}).get("url"), "https://example.com/old")

    def test_restamp_preserves_marker(self):
        """A throttled serve re-stamps cached_at but must carry
        ir_checked_at forward unchanged."""
        env = _envelope(minutes_ago_cached=60, ir_checked_minutes_ago=120)
        _, _, put = self._run(env)
        self.assertEqual(put.get("ir_checked_at"), env["ir_checked_at"])


class TestIrCheckedWithin(unittest.TestCase):
    def test_semantics(self):
        now = datetime.now()
        fresh = {"ir_checked_at": (now - timedelta(hours=1)).isoformat()}
        stale = {"ir_checked_at": (now - timedelta(hours=25)).isoformat()}
        self.assertTrue(otc._ir_checked_within(fresh, 24 * 3600))
        self.assertFalse(otc._ir_checked_within(stale, 24 * 3600))
        self.assertFalse(otc._ir_checked_within(None, 24 * 3600))
        self.assertFalse(otc._ir_checked_within({}, 24 * 3600))


if __name__ == "__main__":
    unittest.main()
