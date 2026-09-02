"""stable/batch-quote path in get_quote_batch (2026-09-02).

The refresh-prices job fetched 600 quotes one call each (paced ~2.5 min),
overlapping its own 2-min cadence — the deadlock incident's root. The batch
path collapses that to ~6 calls, but plan entitlement is unknown, so it is
capability-probed: a denial memoizes and everything falls back to the
per-symbol fan-out. These tests pin both branches and the shared
normalization (frozen gate included).
"""
import time
import unittest
from unittest.mock import patch

from tests import _streamlit_stub  # noqa: F401  (cache stubs)

import data.fmp_client as fmp


def _row(sym, price=10.0, ts=None):
    return {"symbol": sym, "price": price, "previousClose": price - 1,
            "open": price, "dayHigh": price, "dayLow": price - 2,
            "volume": 100, "change": 1.0, "changePercentage": 2.0,
            "timestamp": int(ts if ts is not None else time.time())}


class _BatchCase(unittest.TestCase):
    def setUp(self):
        fmp._batch_quote_denied = False
        self._p_key = patch.object(fmp, "_has_key", return_value=True)
        self._p_cget = patch.object(fmp, "_cache_get", return_value=None)
        self._p_cput = patch.object(fmp, "_cache_put")
        for p in (self._p_key, self._p_cget, self._p_cput):
            p.start()
            self.addCleanup(p.stop)
        fmp._batch_quote_denied = False


class TestBatchQuotePath(_BatchCase):
    def test_bulk_request_uses_batch_endpoint(self):
        calls = []

        def fake_get(path, params, timeout=10):
            calls.append((path, params.get("symbols", params.get("symbol"))))
            return [_row(s) for s in params["symbols"].split(",")]

        with patch.object(fmp, "_get", side_effect=fake_get):
            out = fmp.get_quote_batch([f"T{i:03d}" for i in range(250)])
        self.assertEqual(250, len(out))
        self.assertEqual(3, len(calls), "250 symbols = 3 chunked calls")
        self.assertTrue(all(p == "batch-quote" for p, _ in calls))
        self.assertEqual(9.0, out["T000"]["close"])   # normalized mapping

    def test_denial_memoizes_and_falls_back_to_singles(self):
        paths = []

        def fake_get(path, params, timeout=10):
            paths.append(path)
            if path == "batch-quote":
                return None                      # plan denial
            return [_row(params["symbol"])]      # single-quote fallback

        with patch.object(fmp, "_get", side_effect=fake_get):
            out = fmp.get_quote_batch([f"T{i:02d}" for i in range(30)])
            self.assertEqual(30, len(out))
            self.assertTrue(fmp._batch_quote_denied)
            # Second bulk call must not re-probe the denied endpoint.
            fmp.get_quote_batch([f"T{i:02d}" for i in range(30)])
        self.assertEqual(1, paths.count("batch-quote"),
                         "denial must be memoized after the first probe")

    def test_small_sets_stay_on_singles(self):
        paths = []

        def fake_get(path, params, timeout=10):
            paths.append(path)
            return [_row(params.get("symbol", "X"))]

        with patch.object(fmp, "_get", side_effect=fake_get):
            fmp.get_quote_batch(["JPM", "BAC"])
        self.assertNotIn("batch-quote", paths,
                         "the UI's small cache-miss sets keep per-ticker calls")

    def test_frozen_quote_is_nulled_in_batch_rows(self):
        week_old = time.time() - 7 * 86400

        def fake_get(path, params, timeout=10):
            syms = params["symbols"].split(",")
            return [_row(s, ts=week_old if s == "DEAD" else None)
                    for s in syms]

        with patch.object(fmp, "_get", side_effect=fake_get):
            out = fmp.get_quote_batch(
                ["DEAD"] + [f"T{i:02d}" for i in range(29)])
        self.assertIsNone(out["DEAD"]["price"], "frozen quote must null price")
        self.assertIsNotNone(out["DEAD"]["timestamp"],
                             "timestamp kept — row retirement keys off it")
        self.assertIsNotNone(out["T00"]["price"])

    def test_symbols_missing_from_response_get_empty_quote(self):
        def fake_get(path, params, timeout=10):
            syms = params["symbols"].split(",")
            return [_row(s) for s in syms if s != "GONE"]

        with patch.object(fmp, "_get", side_effect=fake_get):
            out = fmp.get_quote_batch(
                ["GONE"] + [f"T{i:02d}" for i in range(29)])
        self.assertIsNone(out["GONE"]["price"])
        self.assertIsNone(out["GONE"]["timestamp"])


if __name__ == "__main__":
    unittest.main()
