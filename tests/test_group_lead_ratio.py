"""REVIEW 2026-09-24 P1-4: JPM's $71M Dearborn charter (cert 21761, 0.0017%
of assets, zero loans) made JPM a two-charter "group", and every FDIC
average-based ratio (ROAA, NIM, NCO rate…) was dropped as n/a.

data/cert_group._lead_ratio keeps the LEAD charter's reported ratio only when
the other charters provably can't move the group value by 0.005pp:
2 × (siblings' share of the ratio's base) × max|charter − lead| < 0.005.

Fixture: FDIC financials, 2026-06-30, $K (live pull 2026-10-06).
  628   ASSET 4,091,315,000  EQTOT 341,610,000  ERNAST 3,646,803,000
        LNLSGR 1,561,577,000  ROA 1.58517  ROE 18.55  NIMY 2.88181  NTLNLSR 0.61431
  21761 ASSET 71,467  EQTOT 70,955  ERNAST 61,000  LNLSGR 0
        ROA 2.85137  ROE 2.87  NIMY 4.39016  NTLNLSR 0
Bounds: ROA 2×1.747e-5×1.266 = 4.4e-5 ✓; NIMY 2×1.673e-5×1.508 = 5.0e-5 ✓;
NTLNLSR share 0 ✓; ROE 2×2.077e-4×15.68 = 0.0065 ✗ → n/a.

Run: python -m unittest tests.test_group_lead_ratio
"""
import unittest

import pandas as pd

import ui.financials_statements as FS  # noqa: F401  (harness patches it)
from tests.test_statement_units_p2 import _render, _row_cells
from data.cert_group import aggregate_records

JPM = {"REPDTE": "20260630", "CERT": 628, "ASSET": 4_091_315_000,
       "EQTOT": 341_610_000, "ERNAST": 3_646_803_000, "LNLSGR": 1_561_577_000,
       "ROA": 1.5851712753913223, "ROE": 18.55, "NIMY": 2.881812914887761,
       "NTLNLSR": 0.6143123776693258}
DEARBORN = {"REPDTE": "20260630", "CERT": 21761, "ASSET": 71_467, "EQTOT": 70_955,
            "ERNAST": 61_000, "LNLSGR": 0, "ROA": 2.8513735618689835,
            "ROE": 2.87, "NIMY": 4.39016393442623, "NTLNLSR": 0}


class TestLeadRatioMateriality(unittest.TestCase):
    def test_jpm_keeps_lead_ratios_that_siblings_cannot_move(self):
        out = aggregate_records([JPM, DEARBORN])
        self.assertEqual(out["ROA"], JPM["ROA"])
        self.assertEqual(out["NIMY"], JPM["NIMY"])
        self.assertEqual(out["NTLNLSR"], JPM["NTLNLSR"])
        self.assertIsNone(out["ROE"])                     # bound 0.0065pp > 0.005
        self.assertEqual(out["_lead_ratio_fields"], ["NIMY", "NTLNLSR", "ROA"])
        self.assertAlmostEqual(out["_lead_asset_share"], 4_091_315_000 / 4_091_386_467)

    def test_material_sibling_stays_na(self):
        # WTFC-shaped: a second charter at 10% of assets with a different ROA.
        big = dict(DEARBORN, ASSET=455_000_000, EQTOT=40_000_000,
                   ERNAST=420_000_000, LNLSGR=300_000_000)
        out = aggregate_records([JPM, big])
        for f in ("ROA", "ROE", "NIMY", "NTLNLSR"):
            self.assertIsNone(out[f], f)
        self.assertNotIn("_lead_ratio_fields", out)

    def test_missing_sibling_ratio_or_base_is_na(self):
        out = aggregate_records([JPM, dict(DEARBORN, ROA=None)])
        self.assertIsNone(out["ROA"])
        out = aggregate_records([JPM, dict(DEARBORN, ASSET=None)])
        self.assertIsNone(out["ROA"])

    def test_single_charter_passthrough_unchanged(self):
        self.assertEqual(aggregate_records([JPM]), JPM)


class TestStatementLabelsLeadRatio(unittest.TestCase):
    def test_pct_row_shows_lead_value_labeled(self):
        rec = aggregate_records([JPM, DEARBORN])
        rec["REPDTE"] = "2025-12-31"   # Annual view: year-end records
        spec = [("Ratios", [("Return on average assets (reported)", "pct", "ROA"),
                            ("Return on average equity (reported)", "pct", "ROE")])]
        html, _ = _render(spec, pd.DataFrame([rec]), "Annual")
        roa = [t for _, t in _row_cells(html, "Return on average assets (reported)")]
        roe = [t for _, t in _row_cells(html, "Return on average equity (reported)")]
        self.assertEqual(roa, ["1.59%"])
        self.assertIn("lead charter", html)
        self.assertIn("0.0017% of assets", html)
        self.assertEqual(roe, ["—"])                      # n/a token on screen


if __name__ == "__main__":
    unittest.main()
