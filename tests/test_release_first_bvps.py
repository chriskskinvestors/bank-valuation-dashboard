"""(2026-08-19) Release-first increment 1: BVPS + P/B (owner directive —
"every headline metric a bank publishes should display release-first").

BVPS is the exact sibling of the proven TBVPS pattern: the bank's own
reported book value per common share (8-K EX-99.1, gated) wins over the
reconstruction; the wire release serves non-XBRL banks; disagreement ≥15%
is a recorded conflict, never a silent pick. pb_ratio prices off the SAME
resolved figure the bvps column displays.

Pinned here:
- extract_reported_bvps_status gates: magnitude, book ≥ tangible (EQUALITY
  allowed — SFST-class no-intangibles banks print equal values), ±15%
  reconstruction band → ok/gate_rejected, in-release TBVPS as the
  no-reconstruction anchor, nothing-ties → not_disclosed.
- _resolve_bvps source vocabulary + conflict semantics match _resolve_tbvps.
- compute-side: bvps/bvps_source/bvps_conflict emitted; build_bank_metrics
  passes bvps_conflict through; validation errors on it.

Run: python -m unittest tests.test_release_first_bvps
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

import data.sec_earnings_8k as s8k  # noqa: E402
import analysis.valuation as va  # noqa: E402
from data import validation  # noqa: E402


def _rows(rows):
    return patch.object(s8k, "_table_rows", return_value=rows)


class TestExtractBvpsStatus(unittest.TestCase):
    def test_clean_figure_within_band_is_ok(self):
        with _rows([("book value per common share", [47.77, 46.00])]):
            v, status = s8k.extract_reported_bvps_status(
                b"<html/>", reconstructed=47.50)
        self.assertEqual((v, status), (47.77, "ok"))

    def test_disagreement_is_gate_rejected(self):
        with _rows([("book value per common share", [4.83])]):
            v, status = s8k.extract_reported_bvps_status(
                b"<html/>", reconstructed=28.22)
        self.assertEqual((v, status), (None, "gate_rejected"))

    def test_no_reconstruction_anchors_on_in_release_tbvps(self):
        with _rows([
            ("tangible book value per common share", [35.16]),
            ("book value per common share", [36.10]),
        ]):
            v, status = s8k.extract_reported_bvps_status(b"<html/>")
        self.assertEqual((v, status), (36.10, "ok"))

    def test_book_equal_to_tangible_is_accepted(self):
        """SFST class: no intangibles, book == tangible — equality must not
        be rejected as a mismatched row."""
        with _rows([
            ("tangible book value per common share", [47.77]),
            ("book value per common share", [47.77]),
        ]):
            v, status = s8k.extract_reported_bvps_status(b"<html/>")
        self.assertEqual((v, status), (47.77, "ok"))

    def test_book_below_tangible_is_noise(self):
        with _rows([
            ("tangible book value per common share", [35.16]),
            ("book value per common share", [30.00]),
        ]):
            v, status = s8k.extract_reported_bvps_status(b"<html/>")
        self.assertEqual((v, status), (None, "not_disclosed"))

    def test_nothing_ties_the_match_is_not_disclosed(self):
        with _rows([("book value per common share", [36.10])]):
            v, status = s8k.extract_reported_bvps_status(b"<html/>")
        self.assertEqual((v, status), (None, "not_disclosed"))

    def test_magnitude_noise_is_not_disclosed(self):
        with _rows([("book value per common share", [1_834_473.0])]):
            v, status = s8k.extract_reported_bvps_status(
                b"<html/>", reconstructed=40.0)
        self.assertEqual((v, status), (None, "not_disclosed"))


class TestResolveBvps(unittest.TestCase):
    def setUp(self):
        import data.bank_mapping as bm
        import data.otc_release as orl
        self.bm, self.orl = bm, orl
        self._orig = (bm.get_cik, s8k.reported_bvps_status,
                      orl.otc_release_metrics)

    def tearDown(self):
        (self.bm.get_cik, s8k.reported_bvps_status,
         self.orl.otc_release_metrics) = self._orig

    def test_reported_wins(self):
        self.bm.get_cik = lambda t: 1
        s8k.reported_bvps_status = (
            lambda cik, reconstructed=None, tbvps=None: (47.77, "ok"))
        self.assertEqual(va._resolve_bvps("SFST", 47.50, 47.77),
                         (47.77, "reported_8k", False))

    def test_gate_rejected_serves_reconstruction_with_conflict(self):
        self.bm.get_cik = lambda t: 1
        s8k.reported_bvps_status = (
            lambda cik, reconstructed=None, tbvps=None: (None, "gate_rejected"))
        self.assertEqual(va._resolve_bvps("BAFN", 28.22, None),
                         (28.22, "reconstructed", True))

    def test_cikless_bank_uses_wire_release(self):
        from datetime import date, timedelta
        self.bm.get_cik = lambda t: None
        self.orl.otc_release_metrics = lambda t: {
            "qend": (date.today() - timedelta(days=40)).isoformat(),
            "metrics": {"bv_ps": 51.20}}
        self.assertEqual(va._resolve_bvps("PBAM", None, None),
                         (51.20, "company_release", False))

    def test_nothing_available_is_none(self):
        self.bm.get_cik = lambda t: None
        self.orl.otc_release_metrics = lambda t: None
        self.assertEqual(va._resolve_bvps("PBAM", None, None),
                         (None, None, False))


class TestWiring(unittest.TestCase):
    def test_metrics_row_carries_bvps_and_flags(self):
        import analysis.metrics as am
        resolved = {"tbvps": 35.16, "tbvps_source": "reported_8k",
                    "tbvps_conflict": False,
                    "bvps": 36.10, "bvps_source": "reported_8k",
                    "bvps_conflict": True, "pb_ratio": 1.1}
        with patch.object(am, "compute_all_valuations", return_value=resolved):
            row = am.build_bank_metrics("X", {}, {}, {"price": None}, [])
        self.assertEqual(row.get("bvps"), 36.10)
        self.assertEqual(row.get("bvps_source"), "reported_8k")
        self.assertEqual(row.get("pb_ratio"), 1.1)
        self.assertTrue(row.get("bvps_conflict"))

    def test_validation_errors_on_bvps_conflict(self):
        fs = validation.check_coherence_flags(
            {"bvps": 28.22, "bvps_conflict": True, "tbvps_conflict": False},
            {})
        self.assertEqual([(f.field, f.severity) for f in fs],
                         [("bvps", "error")])


if __name__ == "__main__":
    unittest.main()
