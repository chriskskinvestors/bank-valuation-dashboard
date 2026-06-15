"""jobs/refresh_capital._warm_one status classification (no network)."""
import unittest
from unittest import mock


class TestRefreshCapital(unittest.TestCase):
    def _warm(self, ret=None, exc=None):
        import jobs.refresh_capital as rc
        import data.sec_filing_scraper as s
        kw = {"side_effect": exc} if exc else {"return_value": ret}
        with mock.patch.object(s, "holdco_capital_for", **kw):
            return rc._warm_one("T", 1, 2)[1]

    def test_ok_when_cet1_present(self):
        self.assertEqual(self._warm({"capital": {"2025-12-31": {"cet1_ratio": 0.11}}}), "ok")

    def test_cblr_when_leverage_only(self):
        self.assertEqual(self._warm({"capital": {"2025-12-31": {"_cblr": True, "lev_ratio": 0.09}}}), "cblr")

    def test_miss_when_empty(self):
        self.assertEqual(self._warm(None), "miss")
        self.assertEqual(self._warm({"capital": {}}), "miss")

    def test_err_caught(self):
        self.assertTrue(self._warm(exc=ValueError("boom")).startswith("err"))


class TestSecThrottle(unittest.TestCase):
    """The SEC rate limiter keeps the combined request rate under the cap and
    wraps _get idempotently."""

    def test_throttle_spaces_requests(self):
        import jobs.refresh_capital as rc
        import time as _t
        th = rc._SecThrottle(max_rps=8)   # 0.125s min interval
        delays = []
        with mock.patch.object(_t, "sleep", side_effect=lambda d: delays.append(d)):
            for _ in range(5):
                th.wait()
        # First call doesn't wait; the next four are spaced one interval apart.
        self.assertGreaterEqual(sum(delays), 4 * (1.0 / 8) - 1e-9)

    def test_install_is_idempotent_and_wraps_get(self):
        import jobs.refresh_capital as rc
        import data.sec_filing_scraper as s
        orig = s._get
        try:
            rc._install_sec_rate_limit()
            wrapped = s._get
            self.assertTrue(getattr(s._get, "_rate_limited", False))
            rc._install_sec_rate_limit()        # second call is a no-op
            self.assertIs(s._get, wrapped)
        finally:
            s._get = orig


if __name__ == "__main__":
    unittest.main()
