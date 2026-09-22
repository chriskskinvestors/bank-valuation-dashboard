"""SEC XBRL-API lag must be VISIBLE on the card (2026-09-22).

Full-universe scan on 2026-09-22: SEC's companyfacts API held nothing past
Q1-2026 for ONB, FRME, HBAN and CCBG (Q2 10-Qs filed Jul 28-31, full inline
XBRL in each) and nothing past FY2025 for Citi (10-Qs filed May and Aug). The
Corporate Profile card priced today's quote against a March-31 book value
with no date on it — a plausible-stale number, the cardinal rule's class.

Pins:
  1. data/sec_earnings_8k: the cached submissions record carries the latest
     10-Q/10-K (form, filed date, period end) — same JSON, same cache row.
  2. analysis/valuation._sec_facts_lag: lag True / False / None (unknown),
     never a guess, and rides the computed row.
  3. ui/bank_detail labels: reconstructed per-share values are dated while
     lagging; release-sourced figures keep their provenance label; the card
     footnote names the filed-but-unavailable quarter. Portable date text
     (strftime %-d is Linux-only and the suite runs on Windows).
"""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text

from tests import _streamlit_stub

_streamlit_stub.install()

import data.cache as cache  # noqa: E402
import data.sec_earnings_8k as se8k  # noqa: E402
import analysis.valuation as val  # noqa: E402
import ui.bank_detail as bd  # noqa: E402

_SUBS_JSON = (
    '{"filings": {"recent": {'
    '"form": ["8-K", "10-Q", "8-K", "10-Q"], '
    '"items": ["7.01", "", "2.02,9.01", ""], '
    '"accessionNumber": ["a-1", "0000707179-26-000046", "b-2", "c-3"], '
    '"filingDate": ["2026-08-01", "2026-07-29", "2026-07-21", "2026-04-29"], '
    '"reportDate": ["2026-08-01", "2026-06-30", "2026-07-21", "2026-03-31"]}}}'
)


class TestSubmissionsRecordCarriesPeriodicFiling(unittest.TestCase):
    def setUp(self):
        eng = create_engine("sqlite://")
        with eng.begin() as conn:
            conn.execute(text(
                "CREATE TABLE cache (key VARCHAR(255) PRIMARY KEY, "
                "value TEXT NOT NULL, timestamp DOUBLE PRECISION NOT NULL)"))
        p = patch.object(cache, "_engine", eng)
        p.start()
        self.addCleanup(p.stop)

    def test_latest_10q_from_the_same_cached_fetch(self):
        calls = []

        def fake_get(url):
            calls.append(url)
            return _SUBS_JSON

        with patch.object(se8k, "_get", side_effect=fake_get):
            f8k = se8k._latest_earnings_8k(707179)
            filed = se8k.latest_periodic_filing(707179)
            filed_again = se8k.latest_periodic_filing(707179)
        self.assertEqual(len(calls), 1)                      # one fetch, both answers
        self.assertEqual(f8k["accession_dash"], "b-2")
        self.assertEqual(filed, {"form": "10-Q", "date": "2026-07-29",
                                 "report_date": "2026-06-30"})
        self.assertEqual(filed_again, filed)

    def test_old_shape_cache_row_is_refreshed_once(self):
        # A pre-2026-09-22 row ({"f8k": ...} only) must not answer "no filing".
        cache.put("earnings_8k_latest:v1:707179", {"f8k": None})
        with patch.object(se8k, "_get", return_value=_SUBS_JSON) as g:
            self.assertEqual(se8k.latest_periodic_filing(707179)["report_date"],
                             "2026-06-30")
            se8k.latest_periodic_filing(707179)
        self.assertEqual(g.call_count, 1)


class TestSecFactsLag(unittest.TestCase):
    def _lag(self, filed, sec_as_of="2026-03-31"):
        with patch("data.bank_mapping.get_cik", return_value=707179), \
             patch("data.sec_earnings_8k.latest_periodic_filing",
                   return_value=filed):
            return val._sec_facts_lag("ONB", sec_as_of)

    def test_filed_quarter_newer_than_facts_is_lag(self):
        out = self._lag({"form": "10-Q", "date": "2026-07-29",
                         "report_date": "2026-06-30"})
        self.assertEqual(out, {"lag": True, "facts_as_of": "2026-03-31",
                               "filed_period": "2026-06-30",
                               "filed_date": "2026-07-29", "filed_form": "10-Q"})

    def test_facts_current_is_not_lag(self):
        out = self._lag({"form": "10-Q", "date": "2026-07-29",
                         "report_date": "2026-06-30"}, sec_as_of="2026-06-30")
        self.assertIs(out["lag"], False)

    def test_unknown_stays_unknown_never_current(self):
        self.assertIsNone(self._lag(None)["lag"])
        self.assertIsNone(self._lag({"form": "10-Q", "date": "x",
                                     "report_date": ""})["lag"])
        self.assertIsNone(val._sec_facts_lag("ONB", None)["lag"])
        self.assertIsNone(val._sec_facts_lag(None, "2026-03-31")["lag"])

    def test_index_failure_is_unknown_not_crash(self):
        with patch("data.bank_mapping.get_cik", return_value=707179), \
             patch("data.sec_earnings_8k.latest_periodic_filing",
                   side_effect=RuntimeError("EDGAR 503")):
            self.assertIsNone(val._sec_facts_lag("ONB", "2026-03-31")["lag"])

    def test_rides_the_computed_row(self):
        sec = {"sec_as_of": "2026-03-31", "eps": 1.95,
               "book_value_per_share": 21.43,
               "tangible_book_value_per_share": 13.96}
        with patch.object(val, "_sec_facts_lag", return_value={
                "lag": True, "facts_as_of": "2026-03-31",
                "filed_period": "2026-06-30", "filed_date": "2026-07-29",
                "filed_form": "10-Q"}), \
             patch.object(val, "_resolve_eps", return_value=(1.95, "reconstructed", False)), \
             patch.object(val, "_resolve_tbvps", return_value=(13.96, "reconstructed", False)), \
             patch.object(val, "_resolve_bvps", return_value=(21.43, "reconstructed", False)), \
             patch.object(val, "_resolve_release_efficiency", return_value=(None, None)):
            row = val.compute_all_valuations({"price": 25.08}, sec, {}, None, "ONB")
        self.assertIs(row["sec_facts_lag"], True)
        self.assertEqual(row["sec_filed_period"], "2026-06-30")
        self.assertEqual(row["sec_facts_as_of"], "2026-03-31")


_LAG_ROW = {"sec_facts_lag": True, "sec_facts_as_of": "2026-03-31",
            "sec_filed_period": "2026-06-30", "sec_filed_date": "2026-07-29",
            "sec_filed_form": "10-Q"}


class TestCardLabels(unittest.TestCase):
    def test_reconstructed_values_are_dated_while_lagging(self):
        row = {**_LAG_ROW, "tbvps_source": "reconstructed",
               "bvps_source": "reconstructed", "eps_source": "reconstructed"}
        self.assertEqual(bd._ps_label(row, "TBV / Share", "tbvps_source"),
                         "TBV / Share (as of Mar 2026)")
        self.assertEqual(bd._ps_label(row, "BV / Share", "bvps_source"),
                         "BV / Share (as of Mar 2026)")
        self.assertEqual(bd._eps_label(row), "EPS (TTM, thru Mar 2026)")

    def test_release_sourced_figures_keep_provenance_label(self):
        row = {**_LAG_ROW, "tbvps_source": "reported_8k",
               "eps_source": "release_ttm"}
        self.assertEqual(bd._ps_label(row, "TBV / Share", "tbvps_source"),
                         "TBV / Share (co. release)")
        self.assertEqual(bd._eps_label(row), "EPS (TTM, co. release)")

    def test_current_or_unknown_is_unlabeled(self):
        for lag in (False, None):
            row = {"sec_facts_lag": lag, "sec_facts_as_of": "2026-06-30",
                   "tbvps_source": "reconstructed", "eps_source": "reconstructed"}
            self.assertEqual(bd._ps_label(row, "TBV / Share", "tbvps_source"),
                             "TBV / Share")
            self.assertEqual(bd._eps_label(row), "EPS (TTM)")
            self.assertIsNone(bd._sec_lag_note(row))

    def test_footnote_names_the_missing_quarter(self):
        self.assertEqual(
            bd._sec_lag_note(_LAG_ROW),
            "SEC XBRL data lags this filer: the Q2 2026 10-Q filed Jul 29, 2026 "
            "is not yet in SEC companyfacts — HoldCo per-share figures above "
            "are as of Mar 31, 2026.")

    def test_footnote_for_a_lagging_10k(self):
        row = {**_LAG_ROW, "sec_filed_period": "2025-12-31",
               "sec_filed_date": "2026-02-19", "sec_filed_form": "10-K",
               "sec_facts_as_of": "2025-09-30"}
        self.assertIn("the FY2025 10-K filed Feb 19, 2026", bd._sec_lag_note(row))
        self.assertIn("as of Sep 30, 2025", bd._sec_lag_note(row))


if __name__ == "__main__":
    unittest.main()
