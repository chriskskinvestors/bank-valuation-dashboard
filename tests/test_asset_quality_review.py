"""REVIEW 2026-10-06 Asset Quality findings.

* P0-1 "Loan Loss Provision / NCO" divided ELNATR (FDIC "PROVISIONS FOR
  CREDIT LOSSES" — incl. securities + unfunded commitments) by NCOs; the
  label (and FDIC's own ELNANTR) means ELNLOS "PROVISIONS FOR LN & LEASE
  LOSSES". JPM Q4'25 showed 179.14% vs 95.01%.
* P1-1 The NCO chart / "NCO accelerating" alert used calendar-YTD NTLNLSR
  against quarter dates (LARK Q3'25 0.29% vs the quarter's 0.83%).
* P1-2 / P2-1 The "Reserve / NPL" chart and the under-reserved alert are
  reserves ÷ NONCURRENT loans; "below 100% minimum" cites a minimum that
  doesn't exist.
* P2-4 (cert_group) a zero-base sibling (JPM's Dearborn: no loans) carries
  zero weight, so its missing NTLNLSQR can't blank JPM's NCO rate.

Run: python -m unittest tests.test_asset_quality_review
"""
import unittest

import pandas as pd

import ui.financials_statements as FS
from tests.test_statement_units_p2 import _render, _row_cells


def _row(label):
    for _sec, rows in FS._ASSET_QUALITY:
        for r in rows:
            if r[0] == label:
                return r
    raise AssertionError(label)


class TestProvisionOverNco(unittest.TestCase):
    def test_uses_loan_loss_provision(self):
        # Hand: 11,234,000 / 9,996,000 = 112.385% → 112.38% (ELNATR 13,994,000 would give 140.0%).
        spec = [("AQ", [_row("Loan Loss Provision / NCO")])]
        rec = {"REPDTE": "2025-12-31", "ELNATR": 13_994_000, "ELNLOS": 11_234_000,
               "NTLNLS": 9_996_000}
        html, _ = _render(spec, pd.DataFrame([rec]), "Annual")
        self.assertEqual([t for _, t in _row_cells(html, "Loan Loss Provision / NCO")],
                         ["112.38%"])


class TestCreditTimelineQuarterly(unittest.TestCase):
    def test_nco_series_is_single_quarter(self):
        from analysis.credit_dynamics import build_credit_timeline
        hist = [{"REPDTE": "20250930", "NTLNLSR": 0.29, "NTLNLSQR": 0.83},
                {"REPDTE": "20250630", "NTLNLSR": 0.03, "NTLNLSQR": 0.04}]
        tl = build_credit_timeline(hist)
        self.assertEqual(sorted(tl["nco_ratio"].dropna().tolist()), [0.04, 0.83])

    def test_under_reserved_wording(self):
        from analysis.credit_dynamics import detect_credit_alerts
        tl = pd.DataFrame([{"date": pd.Timestamp("2026-03-31"), "reserve_coverage": 99.0},
                           {"date": pd.Timestamp("2026-06-30"), "reserve_coverage": 97.0}])
        msgs = " ".join(a["message"] for a in detect_credit_alerts(tl))
        self.assertIn("97% of noncurrent loans", msgs)
        self.assertNotIn("minimum", msgs)


class TestZeroBaseSibling(unittest.TestCase):
    def test_dearborn_without_loans_does_not_blank_jpm_nco_q(self):
        from data.cert_group import aggregate_records
        jpm = {"CERT": 628, "REPDTE": "20260630", "ASSET": 4_091_315_000,
               "LNLSGR": 1_561_577_000, "NTLNLSQR": 0.615}
        dearborn = {"CERT": 21761, "REPDTE": "20260630", "ASSET": 71_467,
                    "LNLSGR": 0, "NTLNLSQR": None}
        out = aggregate_records([jpm, dearborn])
        self.assertEqual(out["NTLNLSQR"], 0.615)
        self.assertIn("NTLNLSQR", out["_lead_ratio_fields"])

    def test_sibling_with_loans_but_no_ratio_still_blanks(self):
        from data.cert_group import aggregate_records
        jpm = {"CERT": 628, "ASSET": 100.0, "LNLSGR": 50.0, "NTLNLSQR": 0.6}
        sib = {"CERT": 2, "ASSET": 0.001, "LNLSGR": 0.001, "NTLNLSQR": None}
        self.assertIsNone(aggregate_records([jpm, sib])["NTLNLSQR"])


if __name__ == "__main__":
    unittest.main()
