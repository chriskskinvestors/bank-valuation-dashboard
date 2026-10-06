"""(2026-10-06) A "book value per common share" that is TOTAL equity ÷ common
shares — data/sec_earnings_8k.extract_reported_bvps_status.

PCB Bancorp 2Q26 (8-K 0001423869-26-000022, pcbpr20260723earnings.htm):
  Book value per common share   $28.40 = total shareholders' equity $400,463K
                                        ÷ 14,102,189 outstanding common shares
  TCE per common share          $23.49 = (400,463 − 69,141 preferred stock)
                                        ÷ 14,102,189
PCB carries no intangibles, so per-common book = TCE = $23.49, which is our
reconstruction ($400,463K − $69,141K ÷ 14,102,189 = 23.494). The release's
"book value per common share" is preferred-INCLUSIVE despite its label:
20.9% above per-common, it tripped the ±15% gate as a release-vs-
reconstruction CONFLICT (the refresh-universe growth gate's standing PCB
failure), and with no reconstruction its "common" label would have served
$28.40 outright.

Rule: beside a preferred-equity row, a candidate the release's own total
shareholders' equity ÷ ending common shares reproduces within 1% is
per-total-equity → "not_disclosed" (the reconstruction serves, silently).
The same arithmetic tying COMMON equity, or a release with no preferred
row, is untouched.

Run: python -m unittest tests.test_pcb_total_equity_bvps
"""
import unittest
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

import analysis.valuation as va  # noqa: E402
from data.sec_earnings_8k import (  # noqa: E402
    _book_value_rows, _ties_total_equity, extract_reported_bvps_status,
    extract_reported_tbvps_status)


def _tr(*cells):
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def _doc(*tables):
    return ("<html><body>" + "".join(f"<table>{t}</table>" for t in tables)
            + "</body></html>").encode("utf-8")


# PCB's real rows (Selected Financial Data + the TCE reconciliation).
_PCB_SELECTED = (_tr("Book value per common share (4)", "$", "28.40", "$", "27.88")
                 + _tr("TCE per common share (2)", "23.49", "23.02"))
_PCB_BALANCE = (_tr("Preferred stock", "69,141", "69,141")
                + _tr("Total shareholders' equity", "400,463", "396,718")
                + _tr("Outstanding common shares", "14,102,189", "14,231,423"))
_PCB_RECON = (_tr("Total shareholders' equity", "$", "400,463", "$", "396,718")
              + _tr("Less: preferred stock", "69,141", "69,141")
              + _tr("TCE", "331,322", "327,577")
              + _tr("Outstanding common shares", "14,102,189", "14,231,423")
              + _tr("Book value per common share", "$", "28.40", "$", "27.88")
              + _tr("TCE per common share", "$", "23.49", "$", "23.02"))

RECON = 400_463_000 / 14_102_189 * (331_322 / 400_463)      # 23.494…


class TestHandVerified(unittest.TestCase):
    def test_pcb_arithmetic(self):
        self.assertAlmostEqual(400_463 / 14_102.189, 28.40, places=2)
        self.assertAlmostEqual((400_463 - 69_141) / 14_102.189, 23.49, places=2)
        self.assertAlmostEqual(RECON, 23.494, places=3)


class TestTiesTotalEquity(unittest.TestCase):
    def test_pcb_rows_tie(self):
        rows = _book_value_rows(_doc(_PCB_SELECTED, _PCB_BALANCE))
        self.assertTrue(_ties_total_equity(rows, 28.40))
        self.assertFalse(_ties_total_equity(rows, 23.49))

    def test_missing_input_never_ties(self):
        rows = _book_value_rows(_doc(_PCB_SELECTED,
                                     _tr("Total shareholders' equity", "400,463")))
        self.assertFalse(_ties_total_equity(rows, 28.40))


class TestPcbBvpsIsNotAConflict(unittest.TestCase):
    def test_with_reconstruction_not_disclosed_not_gate_rejected(self):
        html = _doc(_PCB_SELECTED, _PCB_BALANCE, _PCB_RECON)
        self.assertEqual(extract_reported_bvps_status(
            html, reconstructed=RECON, tbvps=23.49), (None, "not_disclosed"))
        # The TCE line is the per-common figure and still serves.
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=RECON, bvps=RECON), (23.49, "ok"))

    def test_without_reconstruction_the_common_label_no_longer_passes(self):
        html = _doc(_PCB_SELECTED, _PCB_BALANCE, _PCB_RECON)
        self.assertEqual(extract_reported_bvps_status(html),
                         (None, "not_disclosed"))

    def test_common_equity_tie_is_untouched(self):
        # Same shape, but the per-share row IS common equity ÷ shares
        # (23.49): below a 15% gap it serves as before.
        html = _doc(_tr("Book value per common share", "$", "23.49")
                    + _PCB_BALANCE)
        self.assertEqual(extract_reported_bvps_status(
            html, reconstructed=RECON, tbvps=23.49), (23.49, "ok"))

    def test_no_preferred_row_keeps_the_gate(self):
        # Total equity ÷ shares with NO preferred shown: the release and the
        # reconstruction genuinely disagree — still a conflict to surface.
        html = _doc(_tr("Book value per common share", "$", "28.40")
                    + _tr("Total shareholders' equity", "400,463")
                    + _tr("Outstanding common shares", "14,102,189"))
        self.assertEqual(extract_reported_bvps_status(
            html, reconstructed=RECON, tbvps=23.49), (None, "gate_rejected"))

    def test_resolver_serves_the_reconstruction_silently(self):
        with patch("data.bank_mapping.get_cik", return_value=1423869), \
                patch.object(va, "_earnings_8k_predates", return_value=False), \
                patch("data.sec_earnings_8k.reported_bvps_status",
                      return_value=(None, "not_disclosed")), \
                patch.object(va, "_otc_release_ps", return_value=None):
            out = va._resolve_bvps("PCB", RECON, 23.49, sec_as_of="2026-06-30")
        self.assertEqual(out, (RECON, "reconstructed", False))


if __name__ == "__main__":
    unittest.main()
