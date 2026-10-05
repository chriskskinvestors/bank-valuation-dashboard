"""
REVIEW 2026-10-05 P1-7: a measured cycle deposit beta <= 0 (costs moved
against Fed funds — lag at a turn) was used as the forward beta, floored at
-0.20: a +200bp shock then LOWERED interest-bearing deposit cost by 40bp and
printed an NII gain. It now falls back to the textbook beta with a label
saying why; a positive measured beta is still used as measured.

Run: python -m unittest tests.test_deposit_beta_resolution
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests import _streamlit_stub  # noqa: E402,F401

import analysis.rate_sensitivity as rs  # noqa: E402


def _resolve(measured, mode="historical", custom=None):
    with patch.object(rs, "compute_historical_deposit_beta", return_value=measured):
        return rs._resolve_deposit_beta({}, [{}], mode, custom)


class TestNonPositiveMeasuredBeta(unittest.TestCase):
    def test_negative_measured_beta_is_textbook_labeled(self):
        self.assertEqual(_resolve(-0.12), (rs.TEXTBOOK_INT_BEARING_BETA,
                                           rs.TEXTBOOK_NON_INT_BETA,
                                           "textbook (measured ≤ 0)"))

    def test_zero_measured_beta_is_textbook_labeled(self):
        self.assertEqual(_resolve(0.0)[2], "textbook (measured ≤ 0)")

    def test_positive_measured_beta_used_as_measured(self):
        self.assertEqual(_resolve(0.31), (0.31, 0.0, "historical"))
        self.assertEqual(_resolve(2.4), (1.50, 0.0, "historical"))   # cap kept

    def test_unmeasured_and_explicit_modes_unchanged(self):
        self.assertEqual(_resolve(None)[2], "textbook_fallback")
        self.assertEqual(_resolve(-0.12, mode="textbook")[2], "textbook")
        self.assertEqual(_resolve(-0.12, custom=0.4), (0.4, 0.0, "custom"))


if __name__ == "__main__":
    unittest.main()
