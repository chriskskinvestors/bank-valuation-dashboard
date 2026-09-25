"""Rate & Funding Risk metrics (owner 2026-09-25, "Higher Rate Bank Screen").

Pins, with HAND-COMPUTED values from JPMorgan Chase Bank's real FDIC records
(cert 628, 6/30/2026 and 3/31/2026, $thousands as the API returns them):

  * non-core funding  = 1 − COREDEP/LIAB = 1 − 1,986,736/3,749,705   = 47.016 %
  * time dep % dom    = NTRTIME/DEPDOM   = 296,876/2,226,790          = 13.332 %
  * CDs ≤3m % dom     = (157,680 + 29,689)/2,226,790                  =  8.414 %
  * CDs ≤12m % dom    = (157,680 + 29,689 + 43,934 + 49,881)/2,226,790 = 12.627 %
  * HTM mark          = SCHF − SCHA = 250,313 − 268,516 = −18,203 ($M) → raw $
  * AFS mark          = SCAF − SCAA = 536,045 − 538,704 =  −2,659 ($M) → raw $
  * CD book rate      = (2,037 + 604)×4 / avg(296,876, 294,402)       =  3.573 %
  * vs 6M bill        = 3.573 − 4.01 (DGS6MO 6/30/2026)               = −0.437 pp

The same seven figures were checked against a third-party 6/30/2026 screen on
12 banks (84/84 match); JPM is the pinned representative. Also pins the two
securities-mark defects fixed alongside (IGLSEC realized gains shown as
"Unreal G/L"; SCSNHAA structured notes shown as "HTM Unreal"), n/a on missing
components, and exactness for multi-charter groups.

Run: python -m unittest tests.test_rate_funding_risk
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from analysis.valuation import compute_rate_funding_risk, _prior_quarter_record
from analysis.metrics import bill_yield_on, build_bank_metrics

JPM_Q2 = {
    "REPDTE": "20260630", "LIAB": 3749705000, "COREDEP": 1986736000,
    "DEPDOM": 2226790000, "NTRTIME": 296876000,
    "CD3LES": 157680000, "CD3LESS": 29689000, "CD3T12": 43934000, "CD3T12S": 49881000,
    "SCHF": 250313000, "SCHA": 268516000, "SCAF": 536045000, "SCAA": 538704000,
    "ECD100Q": 2037000, "EOTHTIMQ": 604000,
}
JPM_Q1 = {"REPDTE": "20260331", "NTRTIME": 294402000}


class TestJpmHandComputed(unittest.TestCase):
    def setUp(self):
        self.m = compute_rate_funding_risk(JPM_Q2, [JPM_Q2, JPM_Q1])

    def test_funding_mix(self):
        self.assertAlmostEqual(self.m["noncore_funding_pct"],
                               (1 - 1986736 / 3749705) * 100, places=9)
        self.assertAlmostEqual(round(self.m["noncore_funding_pct"], 3), 47.016)
        self.assertAlmostEqual(round(self.m["time_dep_pct"], 3), 13.332)

    def test_cd_repricing(self):
        self.assertAlmostEqual(round(self.m["cd_reprice_3m_pct"], 3), 8.414)
        self.assertAlmostEqual(round(self.m["cd_reprice_12m_pct"], 3), 12.627)

    def test_securities_marks_in_raw_dollars(self):
        self.assertEqual(self.m["sec_htm_unreal"], -18_203_000 * 1000)
        self.assertEqual(self.m["sec_unreal_gl"], -2_659_000 * 1000)

    def test_cd_book_rate(self):
        expected = (2037000 + 604000) * 4 / ((296876000 + 294402000) / 2) * 100
        self.assertAlmostEqual(self.m["cd_book_rate"], expected, places=12)
        self.assertAlmostEqual(round(self.m["cd_book_rate"], 3), 3.573)


class TestNaNotGuess(unittest.TestCase):
    def test_missing_prior_quarter_is_na(self):
        m = compute_rate_funding_risk(JPM_Q2, [JPM_Q2])
        self.assertIsNone(m["cd_book_rate"])

    def test_gap_quarter_is_na_not_the_next_older_one(self):
        older = {"REPDTE": "20251231", "NTRTIME": 290000000}
        m = compute_rate_funding_risk(JPM_Q2, [JPM_Q2, older])
        self.assertIsNone(m["cd_book_rate"])

    def test_missing_bucket_makes_only_its_sums_na(self):
        rec = dict(JPM_Q2, CD3T12S=None)
        m = compute_rate_funding_risk(rec, [rec, JPM_Q1])
        self.assertIsNone(m["cd_reprice_12m_pct"], "a partial sum is never the whole")
        self.assertAlmostEqual(round(m["cd_reprice_3m_pct"], 3), 8.414)

    def test_nan_component_is_missing(self):
        rec = dict(JPM_Q2, SCHF=float("nan"))
        self.assertIsNone(compute_rate_funding_risk(rec, [rec])["sec_htm_unreal"])

    def test_no_time_deposits_is_real_zero_and_rate_na(self):
        rec = dict(JPM_Q2, NTRTIME=0, CD3LES=0, CD3LESS=0, CD3T12=0, CD3T12S=0,
                   ECD100Q=0, EOTHTIMQ=0)
        m = compute_rate_funding_risk(rec, [rec, {"REPDTE": "20260331", "NTRTIME": 0}])
        self.assertEqual(m["time_dep_pct"], 0.0)
        self.assertEqual(m["cd_reprice_12m_pct"], 0.0)
        self.assertIsNone(m["cd_book_rate"], "no balance → no rate, not 0 %")

    def test_zero_denominators_are_na(self):
        rec = dict(JPM_Q2, DEPDOM=0, LIAB=0)
        m = compute_rate_funding_risk(rec, [rec, JPM_Q1])
        self.assertIsNone(m["time_dep_pct"])
        self.assertIsNone(m["noncore_funding_pct"])

    def test_prior_quarter_accepts_timestamp_and_string_dates(self):
        hist = [{"REPDTE": pd.Timestamp("2026-06-30")}, {"REPDTE": "2026-03-31T00:00:00"}]
        self.assertIs(_prior_quarter_record(hist, "20260630"), hist[1])
        self.assertIsNone(_prior_quarter_record(hist, None))


class TestMultiCharterExactness(unittest.TestCase):
    def test_group_ratio_is_ratio_of_sums(self):
        """cert_group sums levels; the metric must be the ratio of the sums,
        not the average of each charter's ratio."""
        from data.cert_group import aggregate_records
        a = {"REPDTE": "20260630", "ASSET": 900, "DEPDOM": 800, "NTRTIME": 400,
             "LIAB": 850, "COREDEP": 700}
        b = {"REPDTE": "20260630", "ASSET": 100, "DEPDOM": 100, "NTRTIME": 10,
             "LIAB": 90, "COREDEP": 50}
        g = aggregate_records([a, b])
        m = compute_rate_funding_risk(g, [g])
        self.assertAlmostEqual(m["time_dep_pct"], 410 / 900 * 100, places=12)
        self.assertAlmostEqual(m["noncore_funding_pct"], (1 - 750 / 940) * 100, places=12)


    def test_group_loans_to_deposits_is_recomputed_not_summed(self):
        """WFC showed 233.3 % and BAC 133.1 % (the charters' LNLSDEPR summed);
        the group figure is LNLSNET/DEP of the summed levels."""
        from data.cert_group import aggregate_records
        a = {"REPDTE": "20260630", "ASSET": 900, "LNLSNET": 600, "DEP": 1000,
             "LNLSDEPR": 60.0}
        b = {"REPDTE": "20260630", "ASSET": 100, "LNLSNET": 170, "DEP": 100,
             "LNLSDEPR": 170.0}
        g = aggregate_records([a, b])
        self.assertAlmostEqual(g["LNLSDEPR"], 770 / 1100 * 100, places=12)
        g0 = aggregate_records([dict(a, DEP=None), dict(b, DEP=None)])
        self.assertIsNone(g0["LNLSDEPR"], "no deposits → n/a, never the summed ratio")
        self.assertEqual(aggregate_records([a])["LNLSDEPR"], 60.0,
                         "single charter passes through untouched")


class TestBillSpread(unittest.TestCase):
    BILL = pd.Series([3.94, 4.00, 4.01, 4.00],
                     index=pd.to_datetime(["2026-06-26", "2026-06-29",
                                           "2026-06-30", "2026-07-01"]))

    def test_quarter_end_observation(self):
        self.assertEqual(bill_yield_on(self.BILL, "20260630"), 4.01)

    def test_weekend_quarter_end_uses_last_prior_observation(self):
        self.assertEqual(bill_yield_on(self.BILL, "2026-06-28"), 3.94)

    def test_stale_or_absent_is_na(self):
        self.assertIsNone(bill_yield_on(self.BILL, "2026-09-30"))
        self.assertIsNone(bill_yield_on(None, "20260630"))
        self.assertIsNone(bill_yield_on(pd.Series(dtype=float), "20260630"))

    def test_wired_through_build_bank_metrics(self):
        # ticker=None: the as-of/metric-history path — no release, SEC or
        # network lookups (verified with a socket guard), so this stays
        # hermetic in keyless CI and on a workstation with live keys alike.
        with_bill = build_bank_metrics(None, JPM_Q2, {}, {}, [JPM_Q2, JPM_Q1],
                                       bill_6m=self.BILL)
        no_bill = build_bank_metrics(None, JPM_Q2, {}, {}, [JPM_Q2, JPM_Q1])
        self.assertAlmostEqual(with_bill["cd_rate_vs_6m_bill"],
                               with_bill["cd_book_rate"] - 4.01, places=12)
        self.assertAlmostEqual(round(with_bill["cd_rate_vs_6m_bill"], 3), -0.437)
        self.assertIsNone(no_bill["cd_rate_vs_6m_bill"])
        self.assertAlmostEqual(round(with_bill["cd_reprice_12m_pct"], 3), 12.627)


class TestRegistry(unittest.TestCase):
    def test_table_columns_all_resolve(self):
        from config import TABS, METRICS_BY_KEY, TAB_META
        t = next(t for t in TABS if t["key"] == "rate_funding_risk")
        missing = [c for c in t["columns"] if c not in METRICS_BY_KEY]
        self.assertEqual(missing, [])
        self.assertIn("rate_funding_risk", TAB_META)

    def test_securities_marks_no_longer_read_the_wrong_fields(self):
        from config import METRICS_BY_KEY
        for key, wrong in (("sec_unreal_gl", "IGLSEC"), ("sec_htm_unreal", "SCSNHAA")):
            m = METRICS_BY_KEY[key]
            self.assertEqual(m["source"], "computed", key)
            self.assertNotEqual(m.get("fdic_field"), wrong, key)
            self.assertIn("pre-tax", m["label"])

    def test_inputs_are_fetched(self):
        from data.fdic_client import _BASE_FINANCIALS_FIELDS
        for f in ("SCHF", "SCAA", "DEPDOM", "LIAB", "NTRTIME", "CD3LES", "CD3LESS",
                  "CD3T12", "CD3T12S", "ECD100Q", "EOTHTIMQ", "SCAF", "SCHA"):
            self.assertIn(f, _BASE_FINANCIALS_FIELDS, f)
        self.assertNotIn("EQCCOMPI", _BASE_FINANCIALS_FIELDS,
                         "EQCCOMPI is YTD OCI, not accumulated OCI")


if __name__ == "__main__":
    unittest.main(verbosity=2)
