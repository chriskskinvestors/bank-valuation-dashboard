"""UX review P1 (2026-09-30): signed values that DISPLAY as zero must not
carry a sign.

  * Ownership (Detailed / holder history): f"{p:+.0f}%" printed "-0%" for a
    holder that trimmed 0.3% of its stake.
  * Peer Comparison "vs median" rows: "+0.00%" / "−0.00%" for a bank at (or
    within rounding of) the median. A tiny non-zero money difference keeps its
    sign ("+<$0.1M" = up by under $0.1M) — that is information, not noise.
"""
import unittest

from tests import _streamlit_stub

_streamlit_stub.install()

from ui.ownership import signed_pct0  # noqa: E402
from ui.peer_comparison import _delta_cell  # noqa: E402


class TestOwnershipSignedPct(unittest.TestCase):
    def test_rounds_to_zero_is_unsigned(self):
        for p in (-0.3, -0.49, 0.0, 0.4):
            self.assertEqual(signed_pct0(p), "0%", p)

    def test_real_changes_keep_sign(self):
        self.assertEqual(signed_pct0(-0.6), "-1%")
        self.assertEqual(signed_pct0(12.4), "+12%")
        self.assertEqual(signed_pct0(-3), "-3%")


class TestPeerDeltaCell(unittest.TestCase):
    def test_zero_display_is_unsigned(self):
        self.assertEqual(_delta_cell(0.0, "pct", 2), "0.00%")
        self.assertEqual(_delta_cell(-0.001, "pct", 2), "0.00%")
        self.assertEqual(_delta_cell(0.004, "pct", 2), "0.00%")

    def test_real_differences_signed(self):
        self.assertEqual(_delta_cell(1.2, "pct", 2), "+1.20%")
        self.assertEqual(_delta_cell(-3.4e6, "dollars_auto", 1), "−$3.4M")
        self.assertEqual(_delta_cell(3e4, "dollars_auto", 1), "+$30K")


if __name__ == "__main__":
    unittest.main(verbosity=2)
