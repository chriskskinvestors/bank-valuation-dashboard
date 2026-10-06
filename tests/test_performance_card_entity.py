"""REVIEW 2026-09-24 P1-5: the Corporate Profile PERFORMANCE card showed
bank-subsidiary FDIC ratios under company labels, and its ROATCE changed
definition by bank (holdco TTM for some, a bank-level YTD fallback for
others — WTFC 18.50% vs company 14.91%).

Run: python -m unittest tests.test_performance_card_entity
"""
import re
import unittest

from tests import _streamlit_stub

_streamlit_stub.install()

from ui.bank_detail import _render_valuation_performance_tables  # noqa: E402

FDIC = {"REPDTE": "20260630", "ROA": 1.41, "NIMY": 3.60, "EEFFR": 45.98,
        "IDT1CER": 11.34, "NCLNLSR": 0.92, "NETINC": 500_000, "EQTOT": 8_000_000,
        "INTAN": 2_500_000}


def _labels(html):
    return dict(re.findall(r'lg-label">(.*?)</span><span class="lg-val">(.*?)</span>', html))


class TestPerformanceCardEntity(unittest.TestCase):
    def test_holdco_roatce_and_bank_labels(self):
        _, perf = _render_valuation_performance_tables(
            {"roatce_holdco": 15.85, "roatce_blended": 15.85}, FDIC, None)
        lab = _labels(perf)
        self.assertEqual(lab.get("ROATCE (HoldCo, TTM)"), "15.85%")
        for k in ("ROAA (Bank)", "NIM (Bank)", "Efficiency (Bank)", "CET1 (Bank)", "NPL (Bank)"):
            self.assertIn(k, lab)
        self.assertNotIn("ROAA", lab)

    def test_fdic_blend_is_labeled_bank(self):
        _, perf = _render_valuation_performance_tables(
            {"roatce_holdco": None, "roatce_blended": 18.50}, FDIC, None)
        self.assertEqual(_labels(perf).get("ROATCE (Bank, FDIC)"), "18.50%")

    def test_no_inline_ytd_fallback(self):
        # Neither engine figure → no ROATCE row (never the bank YTD ÷ TCE guess).
        _, perf = _render_valuation_performance_tables({}, FDIC, None)
        self.assertFalse(any(k.startswith("ROATCE") for k in _labels(perf)))


if __name__ == "__main__":
    unittest.main()
