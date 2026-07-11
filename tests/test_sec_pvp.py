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
        self.assertIsNone(self._run({"facts": {"us-gaap": {}}}))
        self.assertIsNone(self._run({}))
        with patch("data.sec_client.fetch_company_facts", return_value={}):
            self.assertIsNone(get_pay_versus_performance(None))


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
