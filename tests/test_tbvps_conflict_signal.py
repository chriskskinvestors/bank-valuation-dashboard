"""(2026-08-18) FSUN rendered TBVPS $58.97 while its own release said $35.16
for THREE WEEKS — and the extractor had cleanly found the correct figure.

The ±15% reconstruction band in extract_reported_tbvps exists to reject
mis-grabs (an EPS or dividend value in the TBVPS slot). But when the
reconstruction itself is wrong (FSUN: post-offering equity ÷ a share count
frozen two quarters earlier), the band rejects the TRUTH and the wrong
reconstruction renders — silently. A ≥15% disagreement between a CLEAN
release figure and the reconstruction means one of them IS wrong; that must
be a visible signal, never a silent fallback.

Pinned here:
- extract_reported_tbvps_status distinguishes "gate_rejected" (clean figure,
  failed only the reconstruction band — a real conflict) from
  "not_disclosed" (no row / extraction noise).
- _resolve_tbvps carries the conflict out: still serves the reconstruction
  (the release figure failed its cross-check; neither is trusted more), but
  returns conflict=True, and compute-side metrics expose "tbvps_conflict"
  for jobs/monitors to alert on.

Run: python -m unittest tests.test_tbvps_conflict_signal
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


def _rows(rows):
    return patch.object(s8k, "_table_rows", return_value=rows)


class TestExtractStatus(unittest.TestCase):
    def test_clean_figure_far_from_reconstruction_is_gate_rejected(self):
        """The exact FSUN shape: release says 35.16, wrong reconstruction
        58.97 → the band rejects it, and that MUST read as a conflict."""
        with _rows([("tangible book value per share", [35.16, 33.10])]):
            v, status = s8k.extract_reported_tbvps_status(
                b"<html/>", reconstructed=58.97, bvps=65.89)
        self.assertIsNone(v)
        self.assertEqual(status, "gate_rejected")

    def test_agreeing_figure_is_ok(self):
        with _rows([("tangible book value per share", [35.16, 33.10])]):
            v, status = s8k.extract_reported_tbvps_status(
                b"<html/>", reconstructed=34.80, bvps=36.00)
        self.assertEqual(v, 35.16)
        self.assertEqual(status, "ok")

    def test_no_tbvps_row_is_not_disclosed(self):
        with _rows([("book value per share", [47.77])]):
            v, status = s8k.extract_reported_tbvps_status(
                b"<html/>", reconstructed=58.97)
        self.assertIsNone(v)
        self.assertEqual(status, "not_disclosed")

    def test_extraction_noise_is_not_a_conflict(self):
        """A $-thousands equity total mis-aligned into the slot fails the
        magnitude gate — noise, not a genuine disagreement."""
        with _rows([("tangible book value per share", [1644404.0])]):
            v, status = s8k.extract_reported_tbvps_status(
                b"<html/>", reconstructed=58.97)
        self.assertIsNone(v)
        self.assertEqual(status, "not_disclosed")

    def test_tangible_not_below_book_is_noise_not_conflict(self):
        with _rows([("tangible book value per share", [70.00])]):
            v, status = s8k.extract_reported_tbvps_status(
                b"<html/>", reconstructed=58.97, bvps=65.89)
        self.assertIsNone(v)
        self.assertEqual(status, "not_disclosed")

    def test_internal_tie_out_never_bypasses_the_conflict(self):
        """The release-internal tie-out (the MBIN anchor) is a LAST-resort
        anchor for the no-reconstruction case, never a second opinion: a clean
        figure that disagrees ≥15% with a RESOLVED reconstruction stays
        gate_rejected even when the release's own reconciliation inputs
        (tangible common equity ÷ ending shares) reproduce it exactly."""
        with _rows([
            ("tangible common shareholders' equity", [1614000.0]),
            ("ending common shares", [45900000.0]),
            ("tangible book value per share", [35.16, 33.10]),
        ]):
            v, status = s8k.extract_reported_tbvps_status(
                b"<html/>", reconstructed=58.97)
        self.assertIsNone(v)
        self.assertEqual(status, "gate_rejected")

    def test_value_wrapper_contract_unchanged(self):
        with _rows([("tangible book value per share", [35.16])]):
            self.assertEqual(
                s8k.extract_reported_tbvps(b"<html/>", reconstructed=34.80,
                                           bvps=36.00),
                35.16)


class TestResolveCarriesConflict(unittest.TestCase):
    def setUp(self):
        import data.bank_mapping as bm
        self.bm = bm
        self._orig = (bm.get_cik, s8k.reported_tbvps_status)
        bm.get_cik = lambda t: 1709442

    def tearDown(self):
        self.bm.get_cik, s8k.reported_tbvps_status = self._orig

    def test_gate_rejected_serves_reconstruction_with_conflict_true(self):
        s8k.reported_tbvps_status = (
            lambda cik, reconstructed=None, bvps=None: (None, "gate_rejected"))
        self.assertEqual(va._resolve_tbvps("FSUN", 58.97, 65.89),
                         (58.97, "reconstructed", True))

    def test_not_disclosed_stays_conflict_free(self):
        s8k.reported_tbvps_status = (
            lambda cik, reconstructed=None, bvps=None: (None, "not_disclosed"))
        self.assertEqual(va._resolve_tbvps("FSUN", 58.97, 65.89),
                         (58.97, "reconstructed", False))

    def test_reported_ok_stays_conflict_free(self):
        s8k.reported_tbvps_status = (
            lambda cik, reconstructed=None, bvps=None: (35.16, "ok"))
        self.assertEqual(va._resolve_tbvps("FSUN", 34.80, 36.00),
                         (35.16, "reported_8k", False))


if __name__ == "__main__":
    unittest.main()
