"""Unit tests for the pay-versus-performance extractor (data/sec_pvp).

Fixtures mirror the companyfacts JSON shape. Live shape verified against WAL
(CIK 1212545, DEF 14A filed 2026-04-22) during the build — 5 years, FY2025
net income tagged as ProfitLoss (the ladder case).

Run: python -m unittest tests.test_sec_pvp
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.sec_pvp import (  # noqa: E402
    get_pay_versus_performance,
    _pick_per_year,
    _filing_url,
)


def _fact(end, val, filed, accn="0001-26-000001", form="DEF 14A"):
    return {"start": end[:4] + "-01-01", "end": end, "val": val,
            "filed": filed, "accn": accn, "form": form}


def _facts_blob(ecd_tags: dict, usgaap_tags: dict | None = None) -> dict:
    def units(entries):
        return {"units": {"USD": entries}}
    return {
        "facts": {
            "ecd": {tag: units(v) for tag, v in ecd_tags.items()},
            "us-gaap": {tag: units(v) for tag, v in (usgaap_tags or {}).items()},
        },
    }


class TestPickPerYear(unittest.TestCase):
    def test_newest_filing_wins_per_year(self):
        # 2023 appears in two proxies; the 2026-filed value must win.
        picked = _pick_per_year([
            _fact("2023-12-31", 100, "2024-04-01", accn="old"),
            _fact("2023-12-31", 105, "2026-04-22", accn="new"),
        ])
        self.assertEqual(picked["2023-12-31"]["values"], [105])
        self.assertEqual(picked["2023-12-31"]["accn"], "new")

    def test_same_filing_distinct_values_kept(self):
        # CEO transition: two PEO totals for one year in ONE filing.
        picked = _pick_per_year([
            _fact("2024-12-31", 5_000_000, "2026-04-22"),
            _fact("2024-12-31", 2_500_000, "2026-04-22"),
        ])
        self.assertEqual(picked["2024-12-31"]["values"], [5_000_000, 2_500_000])

    def test_none_val_skipped(self):
        self.assertEqual(_pick_per_year([_fact("2024-12-31", None, "2026-01-01")]), {})


class TestGetPvp(unittest.TestCase):
    def _run(self, blob):
        with patch("data.sec_client.fetch_company_facts", return_value=blob):
            return get_pay_versus_performance(123)

    def test_full_table_hand_built(self):
        blob = _facts_blob(
            {
                "PeoTotalCompAmt": [_fact("2024-12-31", 9_000_000, "2026-04-22"),
                                    _fact("2023-12-31", 8_000_000, "2026-04-22")],
                "PeoActuallyPaidCompAmt": [_fact("2024-12-31", 12_000_000, "2026-04-22")],
                "NonPeoNeoAvgTotalCompAmt": [_fact("2024-12-31", 3_000_000, "2026-04-22")],
                "NonPeoNeoAvgCompActuallyPaidAmt": [_fact("2024-12-31", 3_100_000, "2026-04-22")],
                "TotalShareholderRtnAmt": [_fact("2024-12-31", 151, "2026-04-22")],
                "PeerGroupTotalShareholderRtnAmt": [_fact("2024-12-31", 143, "2026-04-22")],
                "CoSelectedMeasureAmt": [_fact("2024-12-31", 12.2, "2026-04-22")],
            },
            {"NetIncomeLoss": [_fact("2024-12-31", 788_000_000, "2026-04-22")]},
        )
        pvp = self._run(blob)
        self.assertEqual(len(pvp["years"]), 2)  # keyed off peo_total years
        r = pvp["years"][0]
        self.assertEqual(r["fy_end"], "2024-12-31")
        self.assertEqual(r["peo_total"], [9_000_000])
        self.assertEqual(r["peo_paid"], [12_000_000])
        self.assertEqual(r["non_peo_avg_total"], 3_000_000)
        self.assertEqual(r["tsr"], 151)
        self.assertEqual(r["peer_tsr"], 143)
        self.assertEqual(r["net_income"], 788_000_000)
        self.assertEqual(r["co_selected"], 12.2)
        self.assertFalse(pvp["multi_peo"])
        # older year: sparse fields are None, not invented
        r1 = pvp["years"][1]
        self.assertEqual(r1["peo_total"], [8_000_000])
        self.assertIsNone(r1["tsr"])

    def test_multi_peo_flag(self):
        blob = _facts_blob({
            "PeoTotalCompAmt": [_fact("2024-12-31", 5_000_000, "2026-04-22"),
                                _fact("2024-12-31", 2_500_000, "2026-04-22")],
        })
        pvp = self._run(blob)
        self.assertTrue(pvp["multi_peo"])
        self.assertEqual(pvp["years"][0]["peo_total"], [5_000_000, 2_500_000])

    def test_net_income_ladder_profitloss_fallback(self):
        # The WAL case: newest proxy tags FY as ProfitLoss only.
        blob = _facts_blob(
            {"PeoTotalCompAmt": [_fact("2025-12-31", 10_969_236, "2026-04-22")]},
            {
                "NetIncomeLoss": [_fact("2024-12-31", 788_000_000, "2025-04-20")],
                "ProfitLoss": [_fact("2025-12-31", 991_000_000, "2026-04-22")],
            },
        )
        pvp = self._run(blob)
        self.assertEqual(pvp["years"][0]["net_income"], 991_000_000)

    def test_net_income_ignores_10k_facts(self):
        # Only proxy-filed net income belongs in the disclosed table.
        blob = _facts_blob(
            {"PeoTotalCompAmt": [_fact("2024-12-31", 1_000_000, "2026-04-22")]},
            {"NetIncomeLoss": [_fact("2024-12-31", 999_000_000, "2026-02-20",
                                     form="10-K")]},
        )
        pvp = self._run(blob)
        self.assertIsNone(pvp["years"][0]["net_income"])

    def test_no_ecd_is_none(self):
        # No PvP in companyfacts AND no proxy on file (pre-2023 / non-
        # reporting filers) → None. The proxy fallback is TestProxyFallback.
        with patch("data.sec_filing_scraper.latest_filing", return_value=None):
            self.assertIsNone(self._run({"facts": {"us-gaap": {}}}))
            self.assertIsNone(self._run({}))
        with patch("data.sec_client.fetch_company_facts", return_value={}):
            self.assertIsNone(get_pay_versus_performance(None))


class TestProxyFallback(unittest.TestCase):
    """AUB (2026-10-08): SEC companyfacts carries NO `ecd` namespace for the
    CIK although every proxy since 2023 tags the PvP table, so the
    Compensation tab hid every year. The latest DEF 14A's own XBRL fills it."""

    META = {"accession": "000110465926034176", "doc": "aub-20260505xdef14a.htm",
            "date": "2026-03-25", "form": "DEF 14A"}

    def _facts(self):
        from data.sec_filing_scraper import Fact
        f = lambda c, v, end, m=None: Fact(c, v, end, end[:4] + "-01-01", m or {}, "usd")
        return [
            f("ecd:PeoTotalCompAmt", 5_066_713.0, "2025-12-31"),
            f("ecd:PeoTotalCompAmt", 4_255_285.0, "2024-12-31"),
            f("ecd:PeoActuallyPaidCompAmt", 4_444_172.0, "2025-12-31"),
            f("ecd:TotalShareholderRtnAmt", 128.26, "2025-12-31"),
            f("us-gaap:NetIncomeLoss", 273_715_000.0, "2025-12-31"),
            # adjustment rows (another axis) and unkept tags never land
            f("ecd:AdjToCompAmt", 1.0, "2025-12-31",
              {"ecd:ExecutiveCategoryAxis": "ecd:PeoMember"}),
            f("ecd:PeoTotalCompAmt", 9.0, "2025-12-31",
              {"ecd:ExecutiveCategoryAxis": "ecd:PeoMember", "ecd:AdjToCompAxis": "x"}),
            f("us-gaap:Assets", 1e9, "2025-12-31"),
        ]

    def _run(self, blob, facts=None, meta=META):
        store = {}
        with patch("data.sec_client.fetch_company_facts", return_value=blob), \
             patch("data.sec_filing_scraper.latest_filing", return_value=meta) as lf, \
             patch("data.sec_filing_scraper.instance_facts",
                   return_value=self._facts() if facts is None else facts) as inst, \
             patch("data.cache.get", side_effect=lambda k, **kw: store.get(k)), \
             patch("data.cache.put", side_effect=lambda k, v: store.__setitem__(k, v)):
            out = get_pay_versus_performance(883948)
            out2 = get_pay_versus_performance(883948)
        return out, out2, lf, inst

    def test_no_ecd_in_companyfacts_reads_the_proxy(self):
        pvp, again, _, inst = self._run({"facts": {"us-gaap": {}}})
        self.assertEqual([r["fy_end"] for r in pvp["years"]], ["2025-12-31", "2024-12-31"])
        r = pvp["years"][0]
        self.assertEqual(r["peo_total"], [5_066_713.0])      # adjustment row excluded
        self.assertEqual(r["peo_paid"], [4_444_172.0])
        self.assertEqual(r["tsr"], 128.26)
        self.assertEqual(r["net_income"], 273_715_000.0)
        self.assertEqual(pvp["filed"], "2026-03-25")
        self.assertEqual(pvp["source_url"],
                         "https://www.sec.gov/Archives/edgar/data/883948/"
                         "000110465926034176/0001104659-26-034176-index.htm")
        self.assertEqual(inst.call_count, 1)                 # immutable per accession
        self.assertEqual(again, pvp)

    def test_current_companyfacts_never_fetches_the_proxy(self):
        from datetime import date
        fresh = date.today().isoformat()
        blob = _facts_blob({"PeoTotalCompAmt": [_fact("2025-12-31", 1.0, fresh)]})
        pvp, _, lf, inst = self._run(blob)
        self.assertEqual(pvp["years"][0]["peo_total"], [1.0])
        lf.assert_not_called()
        inst.assert_not_called()

    def test_stale_companyfacts_merges_newer_proxy(self):
        blob = _facts_blob({"PeoTotalCompAmt": [_fact("2023-12-31", 3_554_480.0, "2024-03-26"),
                                                _fact("2024-12-31", 9.0, "2024-03-26")]})
        pvp, _, _, _ = self._run(blob)
        years = {r["fy_end"]: r["peo_total"] for r in pvp["years"]}
        self.assertEqual(years["2024-12-31"], [4_255_285.0])   # newer proxy wins the year
        self.assertEqual(years["2023-12-31"], [3_554_480.0])   # companyfacts kept

    def test_per_peo_individual_axis_kept(self):
        from data.sec_filing_scraper import Fact
        two = [Fact("ecd:PeoTotalCompAmt", 5e6, "2025-12-31", "2025-01-01",
                    {"ecd:IndividualAxis": "aub:SmithMember"}, "usd"),
               Fact("ecd:PeoTotalCompAmt", 2e6, "2025-12-31", "2025-01-01",
                    {"ecd:IndividualAxis": "aub:JonesMember"}, "usd")]
        pvp, _, _, _ = self._run({}, facts=two)
        self.assertEqual(pvp["years"][0]["peo_total"], [5e6, 2e6])
        self.assertTrue(pvp["multi_peo"])

    def test_failures_are_none_and_uncached(self):
        with patch("data.sec_client.fetch_company_facts", return_value={}), \
             patch("data.sec_filing_scraper.latest_filing", return_value=self.META), \
             patch("data.sec_filing_scraper.instance_facts", side_effect=RuntimeError("503")), \
             patch("data.cache.get", return_value=None), \
             patch("data.cache.put") as put:
            self.assertIsNone(get_pay_versus_performance(883948))
        put.assert_not_called()
        pvp, _, _, _ = self._run({}, meta=None)
        self.assertIsNone(pvp)


class TestFilingUrl(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(
            _filing_url(1212545, "0001193125-26-170399"),
            "https://www.sec.gov/Archives/edgar/data/1212545/"
            "000119312526170399/0001193125-26-170399-index.htm")

    def test_missing_inputs(self):
        self.assertIsNone(_filing_url(None, "x"))
        self.assertIsNone(_filing_url(1, ""))


if __name__ == "__main__":
    unittest.main()
