"""
(2026-08-02) Multi-charter holding companies were represented by ONE cert.

Found from "why does IBOC only have one call report?" — because the platform
maps one ticker to one FDIC cert and IBOC runs five active bank charters. The
same mapping feeds every FDIC metric, so 11 universe banks displayed a fraction
of their real banking operation: WTFC showed $9.3B of $72.4B (16 charters),
IBOC $9.9B of $17.3B, MS $391B of $633B.

Pins:
  1. levels sum across charters (hand-computed on IBOC's real assets);
  2. average-based ratios (ROA/ROE/NIM/leverage/CET1) go n/a rather than
     silently carrying the LEAD charter's figure onto a consolidated label —
     FDIC computes them against average balances, which period-end levels
     cannot reconstruct;
  3. the three exactly-recomputable ratios ARE rebuilt from the sums;
  4. a single-charter bank is bit-for-bit unchanged (the ~350 other banks);
  5. group resolution degrades to [cert] on failure — never fewer charters
     than we had before.

Pure-function tests; no FDIC calls.

Run: python -m unittest tests.test_cert_group
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from data.cert_group import (AVERAGE_BASED_RATIOS, aggregate_records,  # noqa: E402
                             get_cert_group)

# IBOC's five active charters, assets in $thousands, largest first (FDIC,
# verified live 2026-08-02).
IBOC = [
    {"CERT": 19629, "REPDTE": "20260630", "ASSET": 9_892_909, "DEP": 7_500_000,
     "NETINC": 100_000, "EQTOT": 1_200_000, "ROA": 1.35, "NIMY": 4.10},
    {"CERT": 25679, "ASSET": 4_520_154, "DEP": 3_400_000, "NETINC": 45_000,
     "EQTOT": 500_000, "ROA": 1.20, "NIMY": 3.90},
    {"CERT": 59093, "ASSET": 1_610_821, "DEP": 1_300_000, "NETINC": 15_000,
     "EQTOT": 180_000, "ROA": 1.10, "NIMY": 3.80},
    {"CERT": 23772, "ASSET": 738_516, "DEP": 600_000, "NETINC": 7_000,
     "EQTOT": 90_000, "ROA": 1.05, "NIMY": 3.70},
    {"CERT": 24961, "ASSET": 527_501, "DEP": 420_000, "NETINC": 5_000,
     "EQTOT": 60_000, "ROA": 1.00, "NIMY": 3.60},
]


class TestLevelsSum(unittest.TestCase):
    def test_iboc_assets_hand_computed(self):
        agg = aggregate_records(IBOC)
        # 9,892,909 + 4,520,154 + 1,610,821 + 738,516 + 527,501
        self.assertEqual(agg["ASSET"], 17_289_901)
        self.assertEqual(agg["DEP"], 13_220_000)
        self.assertEqual(agg["NETINC"], 172_000)
        self.assertEqual(agg["EQTOT"], 2_030_000)

    def test_lead_charter_alone_understates_by_43_percent(self):
        """The size of the bug, asserted so it can't quietly return."""
        agg = aggregate_records(IBOC)
        lead_only = IBOC[0]["ASSET"]
        self.assertAlmostEqual(lead_only / agg["ASSET"] * 100, 57.2, places=1)

    def test_identity_comes_from_the_lead_charter(self):
        agg = aggregate_records(IBOC)
        self.assertEqual(agg["CERT"], 19629)
        self.assertEqual(agg["REPDTE"], "20260630")
        self.assertEqual(agg["_charter_count"], 5)
        self.assertTrue(agg["_aggregated"])

    def test_missing_field_on_one_charter_still_sums_the_rest(self):
        recs = [{"CERT": 1, "ASSET": 100}, {"CERT": 2, "ASSET": 50},
                {"CERT": 3}]
        self.assertEqual(aggregate_records(recs)["ASSET"], 150)


class TestAverageBasedRatiosGoNa(unittest.TestCase):
    """The cardinal-rule half: a lead-charter ROA on a consolidated label is a
    plausible-wrong number, so it must be n/a."""

    def test_roa_roe_nim_are_none_not_the_lead_value(self):
        agg = aggregate_records(IBOC)
        for k in ("ROA", "NIMY"):
            self.assertIn(k, agg, f"{k} must be present as an explicit n/a")
            self.assertIsNone(agg[k], f"{k} must not carry the lead charter's value")
        self.assertNotEqual(agg.get("ROA"), 1.35)

    def test_every_average_based_ratio_is_nulled(self):
        recs = [dict({k: 1.0 for k in AVERAGE_BASED_RATIOS}, CERT=1, ASSET=10),
                dict({k: 2.0 for k in AVERAGE_BASED_RATIOS}, CERT=2, ASSET=20)]
        agg = aggregate_records(recs)
        for k in AVERAGE_BASED_RATIOS:
            self.assertIsNone(agg[k], f"{k} must be n/a for a group")
        self.assertEqual(agg["ASSET"], 30)


class TestExactRatiosRecomputed(unittest.TestCase):
    def test_efficiency_from_summed_components(self):
        recs = [
            {"CERT": 1, "INTINC": 1000, "EINTEXP": 400, "NONII": 200, "NONIX": 480},
            {"CERT": 2, "INTINC": 500, "EINTEXP": 200, "NONII": 100, "NONIX": 240},
        ]
        # revenue = (1500-600) + 300 = 1200 ; expense 720 -> 60.00%
        self.assertAlmostEqual(aggregate_records(recs)["EEFFR"], 60.0, places=6)

    def test_capital_ratios_from_summed_dollars(self):
        recs = [
            {"CERT": 1, "RBC": 1000, "RBCT1J": 800, "RWAJ": 8000},
            {"CERT": 2, "RBC": 500, "RBCT1J": 400, "RWAJ": 4000},
        ]
        agg = aggregate_records(recs)
        self.assertAlmostEqual(agg["RBCRWAJ"], 1500 / 12000 * 100, places=6)
        self.assertAlmostEqual(agg["RBC1RWAJ"], 1200 / 12000 * 100, places=6)

    def test_zero_revenue_yields_na_not_a_divide_error(self):
        recs = [{"CERT": 1, "INTINC": 100, "EINTEXP": 150, "NONII": 50, "NONIX": 10},
                {"CERT": 2, "INTINC": 0, "EINTEXP": 0, "NONII": 0, "NONIX": 0}]
        self.assertIsNone(aggregate_records(recs)["EEFFR"])


class TestSingleCharterUnchanged(unittest.TestCase):
    """~350 banks must be bit-for-bit unaffected."""

    def test_passthrough_is_identical(self):
        one = {"CERT": 628, "ASSET": 100, "ROA": 1.23, "NIMY": 3.21}
        agg = aggregate_records([one])
        self.assertEqual(agg, one)
        self.assertNotIn("_aggregated", agg)

    def test_empty_input(self):
        self.assertEqual(aggregate_records([]), {})
        self.assertEqual(aggregate_records([None]), {})


class TestGroupResolutionDegradesSafely(unittest.TestCase):
    def test_no_cert_returns_empty(self):
        with patch("data.bank_mapping.get_fdic_cert", return_value=None):
            self.assertEqual(get_cert_group("NOPE"), [])

    def test_resolution_failure_falls_back_to_the_mapped_cert(self):
        import data.cert_group as cg
        with patch.object(cg, "_resolve_group", return_value=[]), \
                patch("data.cache.get", return_value=None), \
                patch("data.cache.put", return_value=None):
            self.assertEqual(cg.get_cert_group("X", cert=19629), [19629])

    def test_cached_group_is_served(self):
        import data.cert_group as cg
        with patch("data.cache.get", return_value={"certs": [1, 2, 3]}):
            self.assertEqual(cg.get_cert_group("X", cert=1), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
