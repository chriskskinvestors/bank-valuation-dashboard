"""Pins the OTC valuation wiring (2026-07-16): non-SEC filers price P/TBV
off their wire-release TBVPS via analysis/valuation._resolve_tbvps, with a
staleness gate and a provenance marker ("tbvps_source") the UI labels.

Run: python -m unittest tests.test_otc_valuation_wiring
"""
import sys
import types
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
sys.modules.setdefault("streamlit", _st)

import analysis.valuation as va  # noqa: E402

_RECENT_QEND = (date.today() - timedelta(days=30)).isoformat()


class TestOtcTbvps(unittest.TestCase):
    def setUp(self):
        import data.otc_release as orl
        self.orl = orl
        self._orig = orl.otc_release_metrics

    def tearDown(self):
        self.orl.otc_release_metrics = self._orig

    def test_fresh_release_tbv_served(self):
        self.orl.otc_release_metrics = lambda t: {
            "qend": _RECENT_QEND, "metrics": {"tbv_ps": 49.57}}
        self.assertEqual(va._otc_tbvps("PBAM"), 49.57)

    def test_stale_release_refused(self):
        # A bank that stopped publishing must not price today's quote
        # against an old TBV.
        self.orl.otc_release_metrics = lambda t: {
            "qend": "2025-01-31", "metrics": {"tbv_ps": 42.20}}
        self.assertIsNone(va._otc_tbvps("PBAM"))

    def test_missing_release_or_tbv_is_none(self):
        self.orl.otc_release_metrics = lambda t: None
        self.assertIsNone(va._otc_tbvps("PBAM"))
        self.orl.otc_release_metrics = lambda t: {
            "qend": _RECENT_QEND, "metrics": {"tbv_ps": None}}
        self.assertIsNone(va._otc_tbvps("PBAM"))


class TestResolveTbvps(unittest.TestCase):
    def setUp(self):
        import data.bank_mapping as bm
        import data.otc_release as orl
        self.bm, self.orl = bm, orl
        self._orig = (bm.get_cik, orl.otc_release_metrics)

    def tearDown(self):
        self.bm.get_cik, self.orl.otc_release_metrics = self._orig

    def test_cikless_bank_uses_company_release(self):
        self.bm.get_cik = lambda t: None
        self.orl.otc_release_metrics = lambda t: {
            "qend": _RECENT_QEND, "metrics": {"tbv_ps": 49.57}}
        self.assertEqual(va._resolve_tbvps("PBAM", None, None),
                         (49.57, "company_release"))

    def test_reconstruction_fallback_keeps_source(self):
        self.bm.get_cik = lambda t: None
        self.orl.otc_release_metrics = lambda t: None
        self.assertEqual(va._resolve_tbvps("PBAM", 12.34, None),
                         (12.34, "reconstructed"))

    def test_nothing_available_is_none_none(self):
        self.bm.get_cik = lambda t: None
        self.orl.otc_release_metrics = lambda t: None
        self.assertEqual(va._resolve_tbvps("PBAM", None, None), (None, None))

    def test_sec_bank_never_touches_otc_path(self):
        import data.sec_earnings_8k as s8k
        orig = s8k.reported_tbvps
        calls = []
        self.bm.get_cik = lambda t: 12345
        self.orl.otc_release_metrics = (
            lambda t: calls.append(t) or {"qend": _RECENT_QEND,
                                          "metrics": {"tbv_ps": 99.0}})
        try:
            s8k.reported_tbvps = (
                lambda cik, reconstructed=None, bvps=None: 27.83)
            self.assertEqual(va._resolve_tbvps("FBK", 27.50, None),
                             (27.83, "reported_8k"))
            self.assertEqual(calls, [])
        finally:
            s8k.reported_tbvps = orig


class TestComputeAllValuationsWiring(unittest.TestCase):
    def test_ptbv_prices_off_release_tbv_for_cikless_bank(self):
        import data.bank_mapping as bm
        import data.otc_release as orl
        orig = (bm.get_cik, orl.otc_release_metrics)
        bm.get_cik = lambda t: None
        orl.otc_release_metrics = lambda t: {
            "qend": _RECENT_QEND, "metrics": {"tbv_ps": 49.57}}
        try:
            out = va.compute_all_valuations(
                {"price": 60.0}, {}, {}, [], ticker="PBAM")
            self.assertEqual(out["tbvps"], 49.57)
            self.assertEqual(out["tbvps_source"], "company_release")
            self.assertAlmostEqual(out["ptbv_ratio"], 60.0 / 49.57, places=6)
        finally:
            bm.get_cik, orl.otc_release_metrics = orig


if __name__ == "__main__":
    unittest.main()
