"""(2026-10-06) The "Latest quarter — preliminary" banner hides once superseded.

ui/financials_statements._render_preliminary_quarter shows the earnings
release's headline figures as "Latest quarter — preliminary (from earnings
release, not yet in the 10-Q)", its caption promising "Superseded by the
audited 10-Q when filed". Nothing enforced that: every Q2 banner kept showing
after the Q2 10-Qs landed in August, and banks whose latest earnings 8-K is
years old (CIZN 2023-11, BCTF 2024-03) showed it as their "latest quarter".

latest_earnings_8k_figures now returns None (no banner) when
_release_superseded: a filed 10-Q/10-K covers the release quarter, or that
quarter ended more than _SUPPLEMENT_STALE_DAYS (200) ago.

Run: python -m unittest tests.test_preliminary_banner_staleness
"""
import unittest
from datetime import date
from unittest.mock import patch

import data.sec_earnings_8k as se8k

_Q2_RELEASE = {"accession_dash": "0001234567-26-000042",
               "accession": "000123456726000042", "date": "2026-07-21", "cik": 77}


def _superseded(periodic, f8k=_Q2_RELEASE, today=date(2026, 8, 1)):
    with patch.object(se8k, "latest_periodic_filing", return_value=periodic):
        return se8k._release_superseded(77, f8k, today=today)


class TestReleaseSuperseded(unittest.TestCase):
    def test_q2_release_before_its_10q_is_current(self):
        """July: the Q1 10-Q is the latest periodic report — banner shows."""
        self.assertFalse(_superseded({"form": "10-Q", "date": "2026-05-07",
                                      "report_date": "2026-03-31"}))

    def test_q2_release_after_its_10q_is_superseded(self):
        """August: the Q2 10-Q (report date 2026-06-30) is filed."""
        self.assertTrue(_superseded({"form": "10-Q", "date": "2026-08-06",
                                     "report_date": "2026-06-30"}))

    def test_q4_release_superseded_by_the_10k(self):
        f8k = dict(_Q2_RELEASE, date="2027-01-22")             # reports Q4-2026
        self.assertFalse(_superseded({"form": "10-Q", "report_date": "2026-09-30"},
                                     f8k, today=date(2027, 2, 1)))
        self.assertTrue(_superseded({"form": "10-K", "report_date": "2026-12-31"},
                                    f8k, today=date(2027, 3, 2)))

    def test_cizn_years_old_release_is_stale_even_without_a_newer_10q(self):
        """CIZN's latest earnings 8-K is 2023-11-01 (Q3-2023): 200+ days past
        its quarter-end, stale whatever the periodic lookup says."""
        f8k = dict(_Q2_RELEASE, date="2023-11-01")
        self.assertTrue(_superseded(None, f8k, today=date(2026, 10, 6)))

    def test_age_boundary_is_200_days(self):
        # Q2-2026 ended 2026-06-30; day 200 is 2027-01-16.
        self.assertFalse(_superseded(None, today=date(2027, 1, 16)))
        self.assertTrue(_superseded(None, today=date(2027, 1, 17)))

    def test_failed_periodic_lookup_never_hides_a_current_banner(self):
        with patch.object(se8k, "latest_periodic_filing",
                          side_effect=RuntimeError("network")):
            self.assertFalse(se8k._release_superseded(
                77, _Q2_RELEASE, today=date(2026, 8, 1)))

    def test_superseded_release_returns_no_figures_and_fetches_nothing(self):
        def boom(*a, **k):
            raise AssertionError("a superseded release must not be fetched")
        with patch.object(se8k, "_latest_earnings_8k", return_value=_Q2_RELEASE), \
                patch.object(se8k, "_release_superseded", return_value=True), \
                patch.object(se8k, "_ex991_document", boom), \
                patch.object(se8k, "_get", boom):
            self.assertIsNone(se8k.latest_earnings_8k_figures(77))


if __name__ == "__main__":
    unittest.main()
