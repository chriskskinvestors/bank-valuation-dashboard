"""
load_fdic_hist never asks for more than the warm window can satisfy
(REVIEW-2026-09-24 P1-3).

The warm `fdic_hist:{ticker}` row holds 20 quarters and the live fallback is
capped at 20, so a `min_quarters` above 20 could never be met: the Corporate
Profile prefetch (`ui/bank_detail._prefetch_profile_data`, min_quarters=44)
refetched FDIC live on EVERY render and rewrote the same 20 quarters
(measured 0.8–2.6 s of `cp.prefetch` per visit). With the cap, a warm
20-quarter row is served with zero fetches, and a deep store holding at least
the warm depth is served at its full length.
"""
import unittest
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

from data import loaders  # noqa: E402
import data.cache as cache  # noqa: E402

WARM_20 = [{"REPDTE": f"{y}{q}", "ASSET": 1} for y in (2026, 2025, 2024, 2023, 2022)
           for q in ("1231", "0930", "0630", "0331")]


class TestMinQuartersCappedAtWarmWindow(unittest.TestCase):
    def test_deep_request_with_empty_store_serves_warm_row_without_fetch(self):
        calls = []

        def fake_group(ticker, limit=20, cert=None):
            calls.append(limit)
            return WARM_20

        with patch("data.fdic_history_store.deep_group_history", return_value=[]), \
             patch("data.cert_group.fetch_group_history", side_effect=fake_group), \
             patch("data.bank_mapping.get_fdic_cert", return_value=123), \
             patch.object(cache, "get", return_value=WARM_20), \
             patch.object(cache, "put") as put_:
            out1 = loaders.load_fdic_hist("FAKE", min_quarters=44, limit=44)
            out2 = loaders.load_fdic_hist("FAKE", min_quarters=44, limit=44)
        # Pre-fix: two live fetches ([20, 20]) and two rewrites of the same row.
        self.assertEqual([], calls)
        put_.assert_not_called()
        self.assertEqual(20, len(out1))
        self.assertEqual(20, len(out2))

    def test_deep_store_shorter_than_asked_but_at_least_warm_depth_is_served(self):
        deep_30 = [{"REPDTE": f"{y}1231", "ASSET": y} for y in range(2025, 1995, -1)]
        with patch("data.fdic_history_store.deep_group_history", return_value=deep_30), \
             patch("data.cert_group.fetch_group_history") as fetch_, \
             patch.object(cache, "get", return_value=WARM_20):
            out = loaders.load_fdic_hist("FAKE", min_quarters=44, limit=44)
        # Pre-fix: 30 < 44 → fell through to the warm 20 (then live).
        self.assertEqual(30, len(out))
        fetch_.assert_not_called()

    def test_short_warm_row_still_fetches_live_capped_at_twenty(self):
        calls = []

        def fake_group(ticker, limit=20, cert=None):
            calls.append(limit)
            return WARM_20

        with patch("data.fdic_history_store.deep_group_history", return_value=[]), \
             patch("data.cert_group.fetch_group_history", side_effect=fake_group), \
             patch("data.bank_mapping.get_fdic_cert", return_value=123), \
             patch.object(cache, "get", return_value=WARM_20[:5]), \
             patch.object(cache, "put"):
            out = loaders.load_fdic_hist("FAKE", min_quarters=44, limit=44)
        self.assertEqual([20], calls)
        self.assertEqual(20, len(out))


class TestCorporateProfilePrefetchAsksForWarmDepth(unittest.TestCase):
    def test_prefetch_min_quarters_is_the_warm_window(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "ui" / "bank_detail.py"
               ).read_text(encoding="utf-8")
        self.assertIn("load_fdic_hist(ticker, min_quarters=20, limit=44)", src)
        self.assertNotIn("min_quarters=44", src)


if __name__ == "__main__":
    unittest.main()
