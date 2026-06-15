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


if __name__ == "__main__":
    unittest.main()
