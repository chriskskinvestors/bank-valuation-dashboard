"""Uninsured % = uninsured ÷ (insured + uninsured) — the FDIC insurance base.

Before 2026-10-02 both producers divided DEPUNINS by DEP (total deposits,
INCLUDING foreign offices) while DEPUNINS covers domestic offices + insured
US-territory branches only — a mismatched basis that understated every
global/custody bank (C 47.8% vs 76.8%, STT 75.9% vs 92.3% on a third-party
reference screen). ÷DEPDOM overshot instead (STT 102.3%). The insurance base
reproduces the reference on 12/12 banks.

State Street Bank & Trust (cert 14), 6/30/2026, $K as FDIC reports:
  DEPUNINS 247,603,000 / (DEPINS 20,738,000 + 247,603,000) = 92.2718 %
  (the old ÷ DEP 326,247,000 gave 75.89 %)

Run: python -m unittest tests.test_uninsured_basis
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

STT = {"REPDTE": "20260630", "DEP": 326247000, "DEPDOM": 242086000,
       "DEPINS": 20738000, "DEPUNINS": 247603000}
EXPECTED = 247603000 / (20738000 + 247603000) * 100


# The deposit timeline joins a fed-funds rate per quarter (FRED) — irrelevant
# to the uninsured basis; stubbed so the suite stays hermetic.
# A plain function, not a MagicMock: pandas Series.apply treats an (iterable)
# MagicMock as a list of functions.
@mock.patch("analysis.deposit_dynamics._get_fed_funds", new=lambda _d: None)
class TestUninsuredBasis(unittest.TestCase):
    def test_screen_metric(self):
        from analysis.metrics import build_bank_metrics
        m = build_bank_metrics(None, STT, {}, {}, [STT])
        self.assertAlmostEqual(m["uninsured_pct"], EXPECTED, places=10)
        self.assertAlmostEqual(round(m["uninsured_pct"], 1), 92.3)

    def test_deposit_timeline_uses_the_same_basis(self):
        from analysis.deposit_dynamics import build_deposit_timeline
        df = build_deposit_timeline([STT])
        self.assertAlmostEqual(float(df["uninsured_pct"].iloc[0]), EXPECTED, places=10)

    def test_missing_insured_estimate_is_na_not_dep_based(self):
        from analysis.metrics import build_bank_metrics
        m = build_bank_metrics(None, dict(STT, DEPINS=None), {}, {}, [])
        self.assertIsNone(m["uninsured_pct"])

    def test_validation_band_admits_custody_banks(self):
        from data.validation import check_range
        self.assertIsNone(check_range("uninsured_pct", 92.3))
        self.assertIsNotNone(check_range("uninsured_pct", 100.5))


if __name__ == "__main__":
    unittest.main(verbosity=2)
