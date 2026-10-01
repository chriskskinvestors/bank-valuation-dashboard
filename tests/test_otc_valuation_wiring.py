"""Pins the OTC valuation wiring (2026-07-16): non-SEC filers price P/TBV
off their wire-release TBVPS via analysis/valuation._resolve_tbvps, with a
staleness gate and a provenance marker ("tbvps_source") the UI labels.

Run: python -m unittest tests.test_otc_valuation_wiring
"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Order-independent streamlit stub (shared helper).
from tests import _streamlit_stub

_streamlit_stub.install()

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
        self.orl.otc_release_metrics = lambda t, allow_fetch=True: {
            "qend": _RECENT_QEND, "metrics": {"tbv_ps": 49.57}}
        self.assertEqual(va._otc_tbvps("PBAM"), 49.57)

    def test_stale_release_refused(self):
        # A bank that stopped publishing must not price today's quote
        # against an old TBV.
        self.orl.otc_release_metrics = lambda t, allow_fetch=True: {
            "qend": "2025-01-31", "metrics": {"tbv_ps": 42.20}}
        self.assertIsNone(va._otc_tbvps("PBAM"))

    def test_missing_release_or_tbv_is_none(self):
        self.orl.otc_release_metrics = lambda t, allow_fetch=True: None
        self.assertIsNone(va._otc_tbvps("PBAM"))
        self.orl.otc_release_metrics = lambda t, allow_fetch=True: {
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
        self.orl.otc_release_metrics = lambda t, allow_fetch=True: {
            "qend": _RECENT_QEND, "metrics": {"tbv_ps": 49.57}}
        self.assertEqual(va._resolve_tbvps("PBAM", None, None),
                         (49.57, "company_release", False))

    def test_reconstruction_fallback_keeps_source(self):
        self.bm.get_cik = lambda t: None
        self.orl.otc_release_metrics = lambda t, allow_fetch=True: None
        self.assertEqual(va._resolve_tbvps("PBAM", 12.34, None),
                         (12.34, "reconstructed", False))

    def test_nothing_available_is_none_none(self):
        self.bm.get_cik = lambda t: None
        self.orl.otc_release_metrics = lambda t, allow_fetch=True: None
        self.assertEqual(va._resolve_tbvps("PBAM", None, None),
                         (None, None, False))

    def test_sec_bank_never_touches_otc_path(self):
        import data.sec_earnings_8k as s8k
        orig = s8k.reported_tbvps_status
        calls = []
        self.bm.get_cik = lambda t: 12345
        self.orl.otc_release_metrics = (
            lambda t: calls.append(t) or {"qend": _RECENT_QEND,
                                          "metrics": {"tbv_ps": 99.0}})
        try:
            s8k.reported_tbvps_status = (
                lambda cik, reconstructed=None, bvps=None: (27.83, "ok"))
            self.assertEqual(va._resolve_tbvps("FBK", 27.50, None),
                             (27.83, "reported_8k", False))
            self.assertEqual(calls, [])
        finally:
            s8k.reported_tbvps_status = orig


class TestCompanyReportedFirstForSecFilers(unittest.TestCase):
    """Owner, 2026-09-30: "Company reported should always take priority".
    PBAM became an SEC registrant (CIK 1705284) but publishes earnings only
    on GlobeNewswire (no Item 2.02 8-K). Q2-2026 release: TBVPS $49.57 =
    (285,516 − 1,717 servicing asset) / 5,725,696; BVPS $49.87. Our
    reconstruction keeps the servicing asset (TCE convention) → $49.87."""

    def setUp(self):
        import data.bank_mapping as bm
        import data.otc_release as orl
        import data.sec_earnings_8k as s8k
        self.bm, self.orl, self.s8k = bm, orl, s8k
        self._orig = (bm.get_cik, orl.otc_release_metrics,
                      s8k.reported_tbvps_status, s8k.reported_bvps_status,
                      s8k._latest_earnings_8k)
        bm.get_cik = lambda t: 1705284
        s8k._latest_earnings_8k = lambda cik: None     # PBAM: no Item 2.02 8-K
        s8k.reported_tbvps_status = (
            lambda cik, reconstructed=None, bvps=None: (None, "not_disclosed"))
        s8k.reported_bvps_status = (
            lambda cik, reconstructed=None, tbvps=None: (None, "not_disclosed"))

    def tearDown(self):
        (self.bm.get_cik, self.orl.otc_release_metrics,
         self.s8k.reported_tbvps_status, self.s8k.reported_bvps_status,
         self.s8k._latest_earnings_8k) = self._orig

    def _release(self, tbv, bv):
        self.orl.otc_release_metrics = lambda t, allow_fetch=True: {
            "qend": _RECENT_QEND, "metrics": {"tbv_ps": tbv, "bv_ps": bv}}

    def test_pbam_release_beats_reconstruction(self):
        self._release(49.57, 49.87)
        self.assertEqual(va._resolve_tbvps("PBAM", 49.87, 49.87),
                         (49.57, "company_release", False))
        self.assertEqual(va._resolve_bvps("PBAM", 49.87, 49.57),
                         (49.87, "company_release", False))

    def test_release_15pct_off_reconstruction_is_a_conflict(self):
        # One of the two is wrong (a mis-grabbed figure, a stale input):
        # flag it and serve the reconstruction, same as the 8-K gate.
        self._release(60.00, 60.00)
        self.assertEqual(va._resolve_tbvps("PBAM", 49.87, 49.87),
                         (49.87, "reconstructed", True))
        self.assertEqual(va._resolve_bvps("PBAM", 49.87, 49.87),
                         (49.87, "reconstructed", True))

    def test_8k_conflict_is_not_papered_over_by_the_wire_copy(self):
        self._release(49.57, 49.87)
        self.s8k.reported_tbvps_status = (
            lambda cik, reconstructed=None, bvps=None: (None, "gate_rejected"))
        self.assertEqual(va._resolve_tbvps("PBAM", 49.87, 49.87),
                         (49.87, "reconstructed", True))

    def _release_at(self, qend, tbv, bv):
        self.orl.otc_release_metrics = lambda t, allow_fetch=True: {
            "qend": qend, "metrics": {"tbv_ps": tbv, "bv_ps": bv}}

    def test_release_older_than_the_10q_never_wins(self):
        # A wire release for a quarter before the latest 10-Q must not win
        # (TYFG's numbers; its stale figure actually came through the 8-K
        # path — see TestStale8kNeverOutranksTheNewer10q).
        q_old = (date.today() - timedelta(days=120)).isoformat()
        q_10q = (date.today() - timedelta(days=30)).isoformat()
        self._release_at(q_old, 61.17, 64.82)
        self.assertEqual(va._resolve_tbvps("TYFG", 65.44, 69.08, sec_as_of=q_10q),
                         (65.44, "reconstructed", False))
        self.assertEqual(va._resolve_bvps("TYFG", 69.08, 65.44, sec_as_of=q_10q),
                         (69.08, "reconstructed", False))

    def test_release_same_or_newer_quarter_wins(self):
        q = (date.today() - timedelta(days=30)).isoformat()
        self._release_at(q, 49.57, 49.87)          # PBAM: same quarter
        self.assertEqual(va._resolve_tbvps("PBAM", 49.87, 49.87, sec_as_of=q),
                         (49.57, "company_release", False))
        # GLBZ: Q2-2026 release ($7.16) vs a 10-Q stuck at 2025-09-30 ($6.99).
        old_10q = (date.today() - timedelta(days=300)).isoformat()
        self._release_at(q, 7.16, 7.27)
        self.assertEqual(va._resolve_tbvps("GLBZ", 6.99, 7.10, sec_as_of=old_10q),
                         (7.16, "company_release", False))

    def test_cikless_bank_has_no_10q_to_be_older_than(self):
        self.bm.get_cik = lambda t: None
        q_old = (date.today() - timedelta(days=120)).isoformat()
        self._release_at(q_old, 49.57, 49.87)
        self.assertEqual(va._resolve_tbvps("PBAM", None, None,
                                           sec_as_of="2026-12-31"),
                         (49.57, "company_release", False))

    def test_no_release_keeps_reconstruction(self):
        self.orl.otc_release_metrics = lambda t, allow_fetch=True: None
        self.assertEqual(va._resolve_tbvps("PBAM", 49.87, 49.87),
                         (49.87, "reconstructed", False))


class TestStale8kNeverOutranksTheNewer10q(unittest.TestCase):
    """2026-10-01 sweep: three banks served an earnings 8-K for a quarter
    older than their latest 10-Q. TYFG: last Item 2.02 8-K 2025-10-29 (Q3)
    → $61.17 / $64.82 shown; its 2026-06-30 10-Q reconstructs $65.44 /
    $69.08 and the Q2 release says $65.45 / $69.08. FBP: Q2 release filed as
    Item 2.01, so the Q1 8-K ($12.45) kept winning over the Q2 10-Q ($12.68)."""

    def setUp(self):
        import data.bank_mapping as bm
        import data.otc_release as orl
        import data.sec_earnings_8k as s8k
        self.bm, self.orl, self.s8k = bm, orl, s8k
        self._orig = (bm.get_cik, orl.otc_release_metrics, s8k._latest_earnings_8k,
                      s8k.reported_tbvps_status, s8k.reported_bvps_status)
        bm.get_cik = lambda t: 1725262
        orl.otc_release_metrics = lambda t, allow_fetch=True: None

    def tearDown(self):
        (self.bm.get_cik, self.orl.otc_release_metrics, self.s8k._latest_earnings_8k,
         self.s8k.reported_tbvps_status, self.s8k.reported_bvps_status) = self._orig

    def _eightk(self, filed, tbv, bv):
        self.s8k._latest_earnings_8k = lambda cik: {"date": filed}
        self.s8k.reported_tbvps_status = (
            lambda cik, reconstructed=None, bvps=None: (tbv, "ok"))
        self.s8k.reported_bvps_status = (
            lambda cik, reconstructed=None, tbvps=None: (bv, "ok"))

    def test_tyfg_q3_8k_loses_to_the_q2_10q(self):
        self._eightk("2025-10-29", 61.17, 64.82)
        self.assertEqual(va._resolve_tbvps("TYFG", 65.44, 69.08, sec_as_of="2026-06-30"),
                         (65.44, "reconstructed", False))
        self.assertEqual(va._resolve_bvps("TYFG", 69.08, 65.44, sec_as_of="2026-06-30"),
                         (69.08, "reconstructed", False))

    def test_fbp_q1_8k_loses_to_the_q2_10q(self):
        self._eightk("2026-04-22", 12.45, 12.72)
        self.assertEqual(va._resolve_tbvps("FBP", 12.68, 12.95, sec_as_of="2026-06-30"),
                         (12.68, "reconstructed", False))

    def test_same_quarter_8k_still_wins(self):
        # The normal cycle: Q2 release (July) vs Q2 10-Q (Aug) — 8-K first.
        self._eightk("2026-07-22", 65.45, 69.08)
        self.assertEqual(va._resolve_tbvps("TYFG", 65.44, 69.08, sec_as_of="2026-06-30"),
                         (65.45, "reported_8k", False))

    def test_no_reconstruction_keeps_the_8k(self):
        # NPB: nothing newer to serve — the release stays (no regression to n/a).
        self._eightk("2026-04-21", 16.35, None)
        self.assertEqual(va._resolve_tbvps("NPB", None, None, sec_as_of="2026-06-30"),
                         (16.35, "reported_8k", False))


class TestComputeAllValuationsWiring(unittest.TestCase):
    def test_ptbv_prices_off_release_tbv_for_cikless_bank(self):
        import data.bank_mapping as bm
        import data.otc_release as orl
        orig = (bm.get_cik, orl.otc_release_metrics)
        bm.get_cik = lambda t: None
        orl.otc_release_metrics = lambda t, allow_fetch=True: {
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
