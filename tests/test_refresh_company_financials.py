"""jobs/refresh_company_financials._warm_one classification + throttle install."""
import unittest
from unittest import mock


class TestWarmOne(unittest.TestCase):
    def _warm(self, income=False, balance=False, exc=None):
        import jobs.refresh_company_financials as rc
        import data.sec_statements as s

        def fake(cik, stype, n_years=5):
            if exc:
                raise exc
            hit = income if stype == "income" else balance
            return {"statement": {"rows": [1]}} if hit else None

        with mock.patch.object(s, "as_reported_statement_multiyear", side_effect=fake):
            return rc._warm_one("T", 1)[1]

    def test_ok_when_both(self):
        self.assertEqual(self._warm(income=True, balance=True), "ok")

    def test_partial_when_one(self):
        self.assertEqual(self._warm(income=True, balance=False), "partial")

    def test_miss_when_none(self):
        self.assertEqual(self._warm(income=False, balance=False), "miss")

    def test_err_caught(self):
        self.assertTrue(self._warm(exc=ValueError("x")).startswith("err"))


class TestThrottleInstall(unittest.TestCase):
    def test_rebinds_both_modules_idempotently(self):
        import jobs.refresh_company_financials as rc
        import data.sec_filing_scraper as sfs
        import data.sec_statements as ss
        o1, o2 = sfs._get, ss._get
        try:
            rc._install_sec_rate_limit()
            self.assertTrue(getattr(sfs._get, "_rate_limited", False))
            self.assertIs(sfs._get, ss._get)     # both rebound to one throttle
            wrapped = sfs._get
            rc._install_sec_rate_limit()          # idempotent
            self.assertIs(sfs._get, wrapped)
        finally:
            sfs._get, ss._get = o1, o2


if __name__ == "__main__":
    unittest.main()
