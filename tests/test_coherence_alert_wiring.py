"""(2026-08-19) Alert wiring for the coherence guards — smoke detectors get
a speaker.

The FSUN incident produced two machine-readable flags (tbvps_conflict from
analysis/valuation, shares_asof_incoherent from data/sec_client) but nothing
ALERTED on them: a new conflict would render silently until a human noticed.
The nightly gate (jobs/refresh_universe) fails when a previously-clean bank
gains an ERROR-severity finding — so wiring the flags into
validate_bank_metrics makes the system page on wrong-number risk.

Pinned here:
- build_bank_metrics carries tbvps_conflict through as a diagnostic key
  (it is NOT a config.py column; tables render only declared columns).
- validate_bank_metrics emits ERROR on tbvps_conflict (one of the two
  numbers IS wrong — must page) and WARNING on shares_asof_incoherent
  (the guard already renders n/a; visible, not job-failing).
- Clean banks emit neither.

Run: python -m unittest tests.test_coherence_alert_wiring
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

import analysis.metrics as am  # noqa: E402
from data import validation  # noqa: E402


class TestFlagPassthrough(unittest.TestCase):
    def _metrics(self, computed):
        with patch.object(am, "compute_all_valuations", return_value=computed):
            return am.build_bank_metrics("FSUN", {}, {}, {"price": None}, [])

    def test_conflict_flag_reaches_the_metrics_row(self):
        row = self._metrics({"tbvps": 58.97, "tbvps_source": "reconstructed",
                             "tbvps_conflict": True})
        self.assertTrue(row.get("tbvps_conflict"))

    def test_clean_bank_flag_stays_falsy(self):
        row = self._metrics({"tbvps": 18.41, "tbvps_source": "reconstructed",
                             "tbvps_conflict": False})
        self.assertFalse(row.get("tbvps_conflict"))


class TestValidationFindings(unittest.TestCase):
    def test_conflict_is_an_error_finding(self):
        """ERROR severity is the contract: jobs/refresh_universe fails the
        nightly when a previously-clean bank gains an error — this is what
        makes a new FSUN page instead of rendering silently."""
        fs = validation.check_coherence_flags(
            {"tbvps": 58.97, "tbvps_conflict": True}, {})
        self.assertEqual([f.severity for f in fs], ["error"])
        self.assertEqual(fs[0].field, "tbvps")

    def test_incoherent_shares_is_a_warning_finding(self):
        fs = validation.check_coherence_flags(
            {"tbvps_conflict": False}, {"shares_asof_incoherent": True})
        self.assertEqual([f.severity for f in fs], ["warning"])
        self.assertEqual(fs[0].field, "shares_outstanding")

    def test_clean_bank_emits_nothing(self):
        self.assertEqual(
            validation.check_coherence_flags(
                {"tbvps_conflict": False},
                {"shares_asof_incoherent": False}),
            [])
        self.assertEqual(validation.check_coherence_flags({}, {}), [])
        self.assertEqual(validation.check_coherence_flags(None, None), [])

    def test_wired_into_validate_bank_metrics(self):
        """The full entry point the nightly calls must surface the error."""
        fs = validation.validate_bank_metrics(
            {"tbvps_conflict": True},
            sec_data={"shares_asof_incoherent": True})
        sevs = {(f.field, f.severity) for f in fs}
        self.assertIn(("tbvps", "error"), sevs)
        self.assertIn(("shares_outstanding", "warning"), sevs)


if __name__ == "__main__":
    unittest.main()
