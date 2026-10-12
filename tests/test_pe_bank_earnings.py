"""
Owner 2026-10-12: P/E for targets with no SEC EPS = deal value / the bank's
TTM net income from FDIC call reports (same bank-level basis as P/TBV).
Fixture: Century Bank (cert 28362) NETINC YTD $K, raw from FDIC 2026-10-12:
20260630 6,426 · 20251231 8,757 · 20250630 -4,084 -> TTM 19,267 ($K).
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

CENTURY = [{"REPDTE": "20260630", "NETINC": 6426}, {"REPDTE": "20260331", "NETINC": 2966},
           {"REPDTE": "20251231", "NETINC": 8757}, {"REPDTE": "20250930", "NETINC": 5510},
           {"REPDTE": "20250630", "NETINC": -4084}, {"REPDTE": "20250331", "NETINC": 4374}]


def _resp(rows):
    r = MagicMock()
    r.json.return_value = {"data": [{"data": d} for d in rows]}
    return r


class TestTtmNetIncome(unittest.TestCase):

    def _run(self, rows):
        from data import deal_comps
        with patch("data.http.get_with_retry", return_value=_resp(rows)), \
             patch("data.cache.get", return_value=None), patch("data.cache.put"):
            return deal_comps._fdic_ttm_net_income(28362, "2026-09-17")

    def test_ytd_arithmetic(self):
        # 6,426 + 8,757 - (-4,084) = 19,267 $K
        self.assertEqual(self._run(CENTURY), (19_267_000, "2026-06-30", True))

    def test_december_is_the_year(self):
        self.assertEqual(self._run(CENTURY[2:]), (8_757_000, "2025-12-31", True))

    def test_missing_prior_year_quarter_is_none(self):
        self.assertEqual(self._run(CENTURY[:3]), (None, None, True))

    def test_fdic_down(self):
        from data import deal_comps
        with patch("data.http.get_with_retry", return_value=None), \
             patch("data.cache.get", return_value=None):
            self.assertEqual(deal_comps._fdic_ttm_net_income(28362, "2026-09-17"),
                             (None, None, False))


class TestComputeMultiplesBankPe(unittest.TestCase):

    def _deal(self, value=137_300_000):
        return {"deal_kind": "whole_company", "announce_date": "2026-09-17",
                "value_usd": value, "target_cik": None, "terms": {},
                "counterparty": {"cert": 28362}}

    def test_century_pe(self):
        from data import deal_comps
        with patch("data.deal_comps._fdic_at",
                   return_value=(119_400_000, 1_000_000_000, 1_360_343_000, "2026-06-30", True)), \
             patch("data.deal_comps._fdic_ttm_net_income",
                   return_value=(19_267_000, "2026-06-30", True)):
            out, ok = deal_comps.compute_multiples(self._deal())
        self.assertTrue(ok)
        self.assertAlmostEqual(out["p_e"], 137_300_000 / 19_267_000, places=6)   # 7.126x
        self.assertEqual(out["p_e_basis"], "bank-sub")

    def test_loss_year_is_not_a_multiple(self):
        from data import deal_comps
        with patch("data.deal_comps._fdic_at",
                   return_value=(119_400_000, 1_000_000_000, 1_360_343_000, "2026-06-30", True)), \
             patch("data.deal_comps._fdic_ttm_net_income",
                   return_value=(-2_000_000, "2026-06-30", True)):
            out, _ = deal_comps.compute_multiples(self._deal())
        self.assertIsNone(out["p_e"])
        self.assertEqual(out["ttm_net_income"], -2_000_000)

    def test_flagged_value_drops_the_pe_too(self):
        from data import deal_comps
        with patch("data.deal_comps._fdic_at",
                   return_value=(10_000_000, 100_000_000, 1_000_000_000, "2026-06-30", True)), \
             patch("data.deal_comps._fdic_ttm_net_income",
                   return_value=(19_267_000, "2026-06-30", True)):
            out, _ = deal_comps.compute_multiples(self._deal(value=137_300_000))  # 13.7x P/TBV
        self.assertTrue(out["flagged"])
        self.assertIsNone(out["p_e"])

    def test_deck_pe_still_wins(self):
        from data.deal_comps import _company_stated_wins
        row = _company_stated_wins({"p_e": 7.13, "p_e_basis": "bank-sub"},
                                   {"deck_p_e_ltm": 9.3})
        self.assertEqual((row["p_e"], row["p_e_basis"], row["p_e_computed"]), (9.3, "stated", 7.13))


if __name__ == "__main__":
    unittest.main()
