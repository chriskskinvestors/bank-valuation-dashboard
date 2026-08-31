"""(2026-08-31) Serve-only contract for otc_release_metrics.

The valuation resolvers (_otc_release_ps and friends) run inside the 592-bank
snapshot build AND on single-bank Company-page renders. They used to fetch
inline: the wire index recheck per call, and — once the 24h IR-crawl TTLs all
lapsed together — ONE refresh-home-snapshot run per day paid ~80 no-wire
banks' 30-100s site crawls serially inside build_metrics (measured 818s and
1438s spikes against a ~120s norm, prod logs 2026-08-31), holding that run's
snapshot for 14-24 minutes; a Company-page render could pay one crawl inline.

Now: valuation passes allow_fetch=False (serve the cached envelope at ANY
age, no network, no re-stamp), and jobs/refresh_home_snapshot warms the
envelopes AFTER the snapshot write (_warm_otc_releases, no-XBRL banks only).

Pins:
  • allow_fetch=False + envelope of any age -> value served; the wire index,
    IR crawl, and story fetch are NEVER touched; nothing is written
  • allow_fetch=False + empty-sentinel or missing envelope -> None, still no
    network, no write
  • the default (allow_fetch=True) is unchanged — test_otc_ir_throttle and
    test_cache_read_ceilings keep pinning that behavior
  • valuation's _otc_release_ps is wired serve-only end to end
  • _warm_otc_releases targets exactly the no-XBRL set, fetch allowed
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

from data import otc_release as otc  # noqa: E402


def _envelope(minutes_ago: float, value=None, empty=False):
    return {
        "cached_at": (datetime.now() - timedelta(minutes=minutes_ago)).isoformat(),
        "value": {"empty": True} if empty else (value or {
            "url": "https://example.com/old", "qend": "2026-06-30",
            "metrics": {"tbv_ps": 12.34, "efficiency": 61.0}}),
    }


class _ServeOnlyHarness(unittest.TestCase):
    def _run(self, envelope, ticker="XXXX", allow_fetch=False):
        calls = {"pr": 0, "ir": 0, "story": 0}
        puts = {}

        with patch("data.cache.get", return_value=envelope), \
                patch("data.cache.put",
                      side_effect=lambda k, v: puts.update({k: v})), \
                patch.object(otc, "_latest_earnings_pr",
                             side_effect=lambda t: calls.__setitem__(
                                 "pr", calls["pr"] + 1)), \
                patch.object(otc, "_latest_ir_release",
                             side_effect=lambda t: calls.__setitem__(
                                 "ir", calls["ir"] + 1)), \
                patch.object(otc, "_fetch_story",
                             side_effect=lambda u: calls.__setitem__(
                                 "story", calls["story"] + 1)):
            result = otc.otc_release_metrics(ticker, allow_fetch=allow_fetch)
        return result, calls, puts


class TestServeOnly(_ServeOnlyHarness):

    def test_ancient_envelope_is_served_without_any_network(self):
        """40 days stale — far past the 15-min serve AND the 24h crawl TTL.
        Serve-only must still just hand it over."""
        result, calls, puts = self._run(_envelope(minutes_ago=40 * 24 * 60))
        self.assertEqual((result or {}).get("metrics", {}).get("tbv_ps"), 12.34)
        self.assertEqual(calls, {"pr": 0, "ir": 0, "story": 0})
        self.assertEqual(puts, {}, "serve-only must not re-stamp")

    def test_empty_sentinel_returns_none_without_network(self):
        result, calls, puts = self._run(_envelope(60, empty=True))
        self.assertIsNone(result)
        self.assertEqual(calls, {"pr": 0, "ir": 0, "story": 0})
        self.assertEqual(puts, {})

    def test_missing_envelope_returns_none_without_network(self):
        result, calls, puts = self._run(None)
        self.assertIsNone(result)
        self.assertEqual(calls, {"pr": 0, "ir": 0, "story": 0})
        self.assertEqual(puts, {})

    def test_default_still_fetches_past_the_serve_window(self):
        """The warm job's contract: allow_fetch defaults True and a stale
        envelope re-checks the (cheap) wire index."""
        _, calls, _ = self._run(_envelope(minutes_ago=60), allow_fetch=True)
        self.assertEqual(calls["pr"], 1)


class TestValuationIsServeOnly(unittest.TestCase):

    def test_otc_release_ps_never_fetches(self):
        """The resolver the snapshot build and Company renders share."""
        from analysis import valuation

        env = _envelope(minutes_ago=3 * 24 * 60)
        with patch("data.cache.get", return_value=env), \
                patch("data.cache.put") as mock_put, \
                patch.object(otc, "_latest_earnings_pr") as mock_pr, \
                patch.object(otc, "_latest_ir_release") as mock_ir, \
                patch.object(otc, "_fetch_story") as mock_story:
            v = valuation._otc_release_ps("XXXX", "tbv_ps")
        self.assertEqual(v, 12.34)
        mock_pr.assert_not_called()
        mock_ir.assert_not_called()
        mock_story.assert_not_called()
        mock_put.assert_not_called()


class TestWarmTargetsNoXbrlSet(unittest.TestCase):

    def test_warm_calls_fetch_allowed_for_exactly_the_no_sec_banks(self):
        from jobs import refresh_home_snapshot as job

        seen = []
        with patch.object(otc, "otc_release_metrics",
                          side_effect=lambda t, allow_fetch=True:
                          seen.append((t, allow_fetch))):
            job._warm_otc_releases(["AAA", "BBB", "CCC"], sec={"AAA": {"x": 1}})
        self.assertEqual(seen, [("BBB", True), ("CCC", True)])


if __name__ == "__main__":
    unittest.main()
