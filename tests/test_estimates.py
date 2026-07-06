"""
Regression tests for AUDIT-2026-07-02 P2 #19: yfinance failure payloads were
cached as fresh for 6h, so a throttled ticker dropped out of the earnings
calendar until expiry. Failures must NEVER be cached, and a failure payload
persisted by an older build must not be served as fresh.
"""
import unittest
from datetime import datetime
from unittest import mock

import data.estimates as est


class TestEstimatesFailuresNotCached(unittest.TestCase):
    def test_error_payload_is_not_persisted(self):
        err = {"ticker": "JPM", "error": "429 Too Many Requests",
               "earnings_history": []}
        with mock.patch.object(est, "_fetch_from_yfinance", return_value=err), \
             mock.patch.object(est, "load_json", return_value=None), \
             mock.patch.object(est, "save_json") as save:
            out = est.fetch_estimates("JPM")
        self.assertEqual(out.get("error"), "429 Too Many Requests")
        self.assertNotIn("cached_at", out, "a failure must not be stamped fresh")
        save.assert_not_called()  # failures are never written to the cache

    def test_success_payload_is_persisted(self):
        ok = {"ticker": "JPM", "eps_estimate": 4.12, "earnings_history": []}
        with mock.patch.object(est, "_fetch_from_yfinance", return_value=ok), \
             mock.patch.object(est, "load_json", return_value=None), \
             mock.patch.object(est, "save_json") as save:
            out = est.fetch_estimates("JPM")
        self.assertIn("cached_at", out, "a success must be stamped for caching")
        save.assert_called_once()

    def test_cached_error_is_never_fresh(self):
        # An error payload with a brand-new timestamp must still be rejected so
        # the next call retries (covers errors persisted before the fix).
        fresh_error = {"error": "boom", "cached_at": datetime.now().isoformat()}
        self.assertFalse(est._is_fresh_data(fresh_error))
        # A clean, recent payload is still fresh.
        fresh_ok = {"ticker": "JPM", "cached_at": datetime.now().isoformat()}
        self.assertTrue(est._is_fresh_data(fresh_ok))
        self.assertFalse(est._is_fresh_data(None))


if __name__ == "__main__":
    unittest.main()
