"""AOCI % TCE columns on both balance sheets (owner 2026-10-05).

Owner decisions: ship holdco at the coverage SEC data allows (~81%; banks
whose preferred is only tagged dimensionally → n/a); goodwill tagged only at
fiscal year-end is used as last reported and FLAGGED; show HoldCo AND Bank
columns; the HTM mark enters PRE-TAX.

Hand-computed from JPMorgan Chase & Co.'s 10-Q (6/30/2026, $M) and its bank's
FDIC record:
  HoldCo AOCI % TCE      = −7,693 / 299,547 × 100            = −2.5682 %
  HoldCo AOCI+HTM % TCE  = (−7,693 + −18,203) / 299,547 × 100 = −8.6450 %
Bank side (stubbed RC-R item 3, $K; TCE = EQTOT − INTAN):
  SBSI  −42,000 / (900,000 − 100,000) × 100                  = −5.25 %
  +HTM  (−42,000 + (−136,388)) / 800,000 × 100               = −22.2985 %

Run: python -m unittest tests.test_aoci_columns
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.metrics import _aoci_metrics, _bank_aoci_by_ticker, build_all_bank_metrics

JPM_FDIC = {"REPDTE": "20260630", "SCHF": 250313000, "SCHA": 268516000,
            "EQTOT": 341610000, "INTAN": 50505000}
JPM_SEC = {"aoci_holdco": -7_693e6, "tce_holdco": 299_547e6, "tce_goodwill_prior": False}


class TestAociMetrics(unittest.TestCase):
    def test_holdco_hand_computed(self):
        m = _aoci_metrics(JPM_FDIC, JPM_SEC, None)
        self.assertAlmostEqual(m["aoci_holdco_pct_tce"], -7693 / 299547 * 100, places=10)
        self.assertAlmostEqual(round(m["aoci_holdco_pct_tce"], 2), -2.57)
        self.assertAlmostEqual(m["aoci_htm_holdco_pct_tce"],
                               (-7693 - 18203) / 299547 * 100, places=10)
        self.assertIs(m["aoci_gw_prior"], False)
        self.assertIsNone(m["aoci_sub_pct_tce"], "no RC-R value → n/a")

    def test_bank_hand_computed(self):
        fdic = {"SCHF": 1000000, "SCHA": 1136388, "EQTOT": 900000, "INTAN": 100000}
        m = _aoci_metrics(fdic, {}, -42000.0)
        self.assertAlmostEqual(m["aoci_sub_pct_tce"], -5.25, places=10)
        self.assertAlmostEqual(m["aoci_htm_sub_pct_tce"], (-42000 - 136388) / 800000 * 100,
                               places=10)
        self.assertIsNone(m["aoci_holdco_pct_tce"])
        self.assertIsNone(m["aoci_gw_prior"], "no holdco figure → no flag either")

    def test_na_rules(self):
        self.assertIsNone(_aoci_metrics(JPM_FDIC, dict(JPM_SEC, tce_holdco=-1.0), None)
                          ["aoci_holdco_pct_tce"], "non-positive TCE")
        self.assertIsNone(_aoci_metrics(dict(JPM_FDIC, SCHF=None), JPM_SEC, None)
                          ["aoci_htm_holdco_pct_tce"], "no HTM mark → AOCI+HTM n/a")
        self.assertAlmostEqual(_aoci_metrics(dict(JPM_FDIC, SCHF=None), JPM_SEC, None)
                               ["aoci_holdco_pct_tce"], -2.5682, places=3)
        self.assertIs(_aoci_metrics(JPM_FDIC, dict(JPM_SEC, tce_goodwill_prior=True), None)
                      ["aoci_gw_prior"], True)


class TestBankAociByTicker(unittest.TestCase):
    def test_group_strict_sum_one_read_per_quarter(self):
        groups = {"JPM": [628, 21761], "SBSI": [3309], "WFC": [3511, 27389]}
        calls = []

        def loader(certs, iso):
            calls.append((sorted(certs), iso))
            return {628: -3500000.0, 21761: -10.0, 3309: -42000.0, 3511: -900.0, 27389: None}
        fdic_all = {t: {"REPDTE": "20260630"} for t in groups}
        with mock.patch("data.cert_group.get_cert_group", side_effect=lambda t: groups[t]):
            got = _bank_aoci_by_ticker(list(groups), fdic_all, loader)
        self.assertEqual(got["JPM"], -3500010.0)
        self.assertEqual(got["SBSI"], -42000.0)
        self.assertIsNone(got["WFC"], "a charter without the item → n/a, never a partial sum")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "2026-06-30")

    def test_loader_failure_is_na_not_crash(self):
        with mock.patch("data.cert_group.get_cert_group", return_value=[3309]):
            got = _bank_aoci_by_ticker(["SBSI"], {"SBSI": {"REPDTE": "20260630"}},
                                       mock.Mock(side_effect=RuntimeError("db down")))
        self.assertEqual(got, {})

    def test_wired_through_build_all_bank_metrics(self):
        fdic = {"REPDTE": "20260630", "SCHF": 1000000, "SCHA": 1136388,
                "EQTOT": 900000, "INTAN": 100000}
        with mock.patch("data.cert_group.get_cert_group", return_value=[3309]), \
             mock.patch("analysis.metrics.build_bank_metrics",
                        side_effect=lambda t, f, s, p, h, b, aoci_bank=None:
                        {"ticker": t, **_aoci_metrics(f, s, aoci_bank)}):
            rows = build_all_bank_metrics(["SBSI"], {"SBSI": fdic}, {"SBSI": {}}, {"SBSI": {}},
                                          {"SBSI": []},
                                          rcr_aoci=lambda certs, iso: {3309: -42000.0})
        self.assertAlmostEqual(rows[0]["aoci_sub_pct_tce"], -5.25, places=10)


def _fact(end, val, form="10-Q", filed="2026-08-01"):
    return {"end": end, "val": val, "form": form, "filed": filed, "fy": 2026, "fp": "Q2"}


def _facts(aoci_end="2026-06-30", gw_end="2026-06-30", preferred=True):
    ug = {
        "StockholdersEquity": {"units": {"USD": [_fact("2026-06-30", 320_587e6)]}},
        "Assets": {"units": {"USD": [_fact("2026-06-30", 4_500_000e6)]}},
        "Goodwill": {"units": {"USD": [_fact(gw_end, 52_711e6,
                                             form="10-K" if gw_end < "2026-06-30" else "10-Q")]}},
        "IntangibleAssetsNetExcludingGoodwill": {"units": {"USD": [_fact("2026-06-30", 0.0)]}},
        "AccumulatedOtherComprehensiveIncomeLossNetOfTax": {"units": {"USD": [
            _fact(aoci_end, -7_693e6)]}},
        "CommonStockSharesOutstanding": {"units": {"shares": [_fact("2026-06-30", 2.7e9)]}},
    }
    if preferred:
        ug["PreferredStockValue"] = {"units": {"USD": [_fact("2026-06-30", 20_000e6)]}}
    return {"cik": 1, "facts": {"us-gaap": ug, "dei": {}}}


class TestSecHoldcoAoci(unittest.TestCase):
    def _run(self, facts):
        from data import sec_client
        with mock.patch.object(sec_client, "fetch_company_facts", return_value=facts):
            return sec_client.get_latest_fundamentals(1)

    def test_aoci_only_at_the_equity_date(self):
        r = self._run(_facts())
        self.assertEqual(r["aoci_holdco"], -7_693e6)
        r = self._run(_facts(aoci_end="2026-03-31"))
        self.assertIsNone(r["aoci_holdco"], "an AOCI from another period is never mixed in")

    def test_tce_is_equity_less_preferred_less_intangibles(self):
        r = self._run(_facts())
        self.assertAlmostEqual(r["tce_holdco"], 320_587e6 - 20_000e6 - 52_711e6, places=0)

    def test_year_end_goodwill_is_used_and_flagged(self):
        r = self._run(_facts(gw_end="2025-12-31"))
        self.assertTrue(r["tce_goodwill_prior"])
        self.assertIsNotNone(r["tce_holdco"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
