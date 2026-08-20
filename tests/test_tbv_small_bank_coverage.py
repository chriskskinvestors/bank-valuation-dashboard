"""
(2026-08-02) TBV coverage at the small end of the universe.

Found auditing the 75 smallest banks ($122M–$1,407M assets). Correctness was
clean — 55 valued, zero invariant violations (no tbvps > bvps, nothing
non-positive rendered, no absurd P/TBV). But 20 returned n/a, and two causes
were fixable rather than genuine:

1. THE ELIF GAP. analysis.valuation._resolve_tbvps only reached the earnings-
   release fallback in an `elif cik`-style branch — i.e. only for banks with NO
   CIK. A registrant that HAS a CIK but no usable XBRL never tried its own
   release and rendered n/a permanently (PNSB and DWNX return no facts at all;
   CMHF's companyfacts 404s).

2. THE DOLLAR-CHANGE-THEN-LEVEL FORM. FSRL's 2Q26 release states "Tangible book
   value per share (Non-GAAP) increased $1.67, or 15.6%, to $12.38 from $10.71".
   Both existing prose patterns fail by design — their [^$%] connector cannot
   cross the $1.67 — so the value was missed entirely.

Deliberately NOT changed: OAKV and BMBN publish only a plain book value per
share and no tangible figure at all, so their n/a is correct. Most small-bank
n/a is genuine non-disclosure, not a parser gap.

Run: python -m unittest tests.test_tbv_small_bank_coverage
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

from data.release_metrics import _TBV_BAND, _TBV_PATS, _dollar_metric  # noqa: E402


def _tbv(text: str):
    return _dollar_metric(text, _TBV_PATS, _TBV_BAND)


class TestDollarChangeThenLevel(unittest.TestCase):
    def test_fsrl_2q26_verbatim(self):
        """The exact sentence from the live release (verified 2026-08-02)."""
        t = ("Tangible book value per share (Non-GAAP) increased $1.67, or "
             "15.6%, to $12.38 from $10.71 over the same period.")
        self.assertAlmostEqual(_tbv(t), 12.38, places=6)

    def test_the_change_is_never_captured(self):
        """The whole risk of this pattern: grabbing $1.67 instead of $12.38."""
        t = ("Tangible book value per share increased $1.67, or 15.6%, to "
             "$12.38 from $10.71.")
        self.assertNotAlmostEqual(_tbv(t) or 0.0, 1.67, places=2)

    def test_without_the_percent_clause(self):
        t = "Tangible book value per common share rose $0.94 to $21.15."
        self.assertAlmostEqual(_tbv(t), 21.15, places=6)

    def test_by_connector_and_decrease(self):
        t = "Tangible book value per share decreased by $0.40, or 2.1%, to $18.60."
        self.assertAlmostEqual(_tbv(t), 18.60, places=6)

    def test_existing_forms_still_work(self):
        self.assertAlmostEqual(
            _tbv("Tangible book value per share was $31.05 at June 30, 2026."),
            31.05, places=6)
        self.assertAlmostEqual(
            _tbv("Tangible book value per share increased 3.0% to $23.45."),
            23.45, places=6)

    def test_a_growth_restatement_is_still_refused(self):
        """"up 8.2% from $22.71" narrates the PRIOR period — must stay None,
        the discipline the existing patterns were written around."""
        self.assertIsNone(_tbv("Tangible book value per share, up 8.2% from $22.71."))

    def test_band_still_applies(self):
        self.assertIsNone(
            _tbv("Tangible book value per share increased $0.10 to $0.55."))


class TestReleaseFallbackNotGatedOnMissingCik(unittest.TestCase):
    """Fix 1: a bank with a CIK but no usable SEC facts must still try its own
    earnings release rather than render n/a forever.

    _resolve_tbvps returns (value, source, conflict); the SEC seam is
    reported_tbvps_status → (value, status). None of these scenarios is a
    gate_rejected disagreement, so conflict is False throughout (the conflict
    path is pinned in tests/test_tbvps_conflict_signal.py)."""

    def test_cik_present_but_no_reconstruction_uses_release(self):
        from analysis import valuation
        with patch("data.bank_mapping.get_cik", return_value=123456), \
                patch("data.sec_earnings_8k.reported_tbvps_status",
                      return_value=(None, "not_disclosed")), \
                patch.object(valuation, "_otc_tbvps", return_value=14.25):
            val, src, conflict = valuation._resolve_tbvps("PNSB", None, None)
        self.assertAlmostEqual(val, 14.25, places=6)
        self.assertEqual(src, "company_release")
        self.assertIs(conflict, False)

    def test_reconstruction_still_wins_over_the_release(self):
        """The release is a FALLBACK — a working reconstruction is not replaced."""
        from analysis import valuation
        with patch("data.bank_mapping.get_cik", return_value=123456), \
                patch("data.sec_earnings_8k.reported_tbvps_status",
                      return_value=(None, "not_disclosed")), \
                patch.object(valuation, "_otc_tbvps", return_value=99.99):
            val, src, conflict = valuation._resolve_tbvps("XYZ", 20.00, 22.00)
        self.assertAlmostEqual(val, 20.00, places=6)
        self.assertEqual(src, "reconstructed")
        self.assertIs(conflict, False)

    def test_reported_8k_still_outranks_everything(self):
        from analysis import valuation
        with patch("data.bank_mapping.get_cik", return_value=123456), \
                patch("data.sec_earnings_8k.reported_tbvps_status",
                      return_value=(30.00, "ok")), \
                patch.object(valuation, "_otc_tbvps", return_value=99.99):
            val, src, conflict = valuation._resolve_tbvps("XYZ", 20.00, 22.00)
        self.assertAlmostEqual(val, 30.00, places=6)
        self.assertEqual(src, "reported_8k")
        self.assertIs(conflict, False)

    def test_nothing_anywhere_is_na_with_no_source(self):
        from analysis import valuation
        with patch("data.bank_mapping.get_cik", return_value=None), \
                patch.object(valuation, "_otc_tbvps", return_value=None):
            val, src, conflict = valuation._resolve_tbvps("NONE", None, None)
        self.assertIsNone(val)
        self.assertIsNone(src)
        self.assertIs(conflict, False)


class TestCacheVersionsStayCoupled(unittest.TestCase):
    """otc_release's extractions are immutable per URL, so an extraction-spec
    bump in release_metrics that isn't mirrored here leaves OTC banks pinned to
    pre-fix values until their next release (~a quarter)."""

    def test_both_versions_bumped_together(self):
        rm = (REPO / "data/release_metrics.py").read_text(encoding="utf-8")
        otc = (REPO / "data/otc_release.py").read_text(encoding="utf-8")
        self.assertIn('key = f"release_metrics:v18:', rm)
        self.assertIn('key = f"otc_release:v9:', otc)


if __name__ == "__main__":
    unittest.main()
