"""
Tests for data/deal_comps.py — Comparable Deal Analysis (§14). All lookups
mocked. Pins:

  • basis selection: target_cik -> SEC holdco TCE (the RATIO's priced
    entity — the Columbia/Umpqua flip guard); else FDIC bank-sub EQ−INTAN
  • hand math: $191.1M ÷ ($97,000k−$17,375k bank-sub TBV) = 2.40x;
    core deposit premium (value−TBV)÷COREDEP
  • the P/TBV sanity band flags-and-n/a's out-of-range multiples
  • core-deposit premium only on the bank-sub basis, and only when core
    deposits are a real funding base (>10% of assets)
  • snapshot: completed sale rows skipped (the acquirer carries the deal),
    accession-level dedupe across banks, lookup failure -> no cache
"""
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# Full house stub (see tests/test_audit_regressions.py): a minimal stub that
# wins the sys.modules setdefault race would break later suites needing
# st.fragment / streamlit.components.v1 at module load (the stub-rot trap,
# memory 2026-07-02).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
_st_components = types.ModuleType("streamlit.components")
_st_components_v1 = types.ModuleType("streamlit.components.v1")
_st_components_v1.html = lambda *a, **k: None
_st_components.v1 = _st_components_v1
_st.components = _st_components
sys.modules.setdefault("streamlit", _st)
sys.modules.setdefault("streamlit.components", _st_components)
sys.modules.setdefault("streamlit.components.v1", _st_components_v1)


def _deal(**kw):
    base = {"deal_kind": "whole_company", "direction": "acquisition",
            "status": "completed", "counterparty": {"name": "T", "cert": 17874},
            "announce_date": "2018-07-26", "completion_date": "2018-11-01",
            "termination_date": None, "value_usd": 191_100_000,
            "value_basis": "stated", "value_note": None, "target_cik": None,
            "announce_url": "https://www.sec.gov/Archives/edgar/data/1/acc123/x.htm",
            "target_assets": 922_000_000, "target_assets_repdte": "2018-06-30"}
    base.update(kw)
    return base


def _fdic_resp(eq_k, intan_k, coredep_k, asset_k, repdte="20180630"):
    r = MagicMock()
    r.json.return_value = {"data": [{"data": {
        "REPDTE": repdte, "EQ": eq_k, "INTAN": intan_k,
        "COREDEP": coredep_k, "ASSET": asset_k}}]}
    return r


class TestComputeMultiples(unittest.TestCase):

    @patch("data.http.get_with_retry")
    def test_bank_sub_hand_math(self, mock_get):
        from data.deal_comps import compute_multiples
        # TBV = (97,000 − 17,375)k = $79,625,000 -> P/TBV = 2.4000x (hand)
        # CDP = (191.1M − 79.625M) / 700,000k = 0.15925
        mock_get.return_value = _fdic_resp(97_000, 17_375, 700_000, 920_000)
        out, ok = compute_multiples(_deal())
        self.assertTrue(ok)
        self.assertEqual(out["tbv_basis"], "bank-sub")
        self.assertEqual(out["tbv_usd"], 79_625_000)
        self.assertAlmostEqual(out["p_tbv"], 2.4, places=4)
        self.assertAlmostEqual(out["core_dep_premium"], 0.15925, places=5)
        self.assertAlmostEqual(out["price_assets"],
                               191_100_000 / 920_000_000, places=6)
        self.assertEqual(out["tbv_asof"], "2018-06-30")
        # Anchored at the ANNOUNCE date.
        self.assertIn("TO 20180726", mock_get.call_args[0][1]["filters"])

    @patch("data.deal_comps._sec_assets_at", return_value=(30_000_000_000,
                                                           "2021-09-30"))
    @patch("data.sec_per_share.tangible_common_equity_at",
           return_value=(2_717_000_000, "2021-09-30"))
    @patch("data.http.get_with_retry")
    def test_holdco_basis_from_target_cik(self, mock_fdic, _tce, _assets):
        # The priced entity's SEC TCE wins; FDIC is never called; core
        # deposit premium stays n/a on the holdco basis (flip safety).
        from data.deal_comps import compute_multiples
        out, ok = compute_multiples(_deal(value_usd=5_189_818_466,
                                          value_basis="computed",
                                          target_cik=1077771))
        self.assertTrue(ok)
        self.assertEqual(out["tbv_basis"], "holdco")
        self.assertAlmostEqual(out["p_tbv"], 5_189_818_466 / 2_717_000_000,
                               places=6)
        self.assertIsNone(out["core_dep_premium"])
        self.assertAlmostEqual(out["price_assets"],
                               5_189_818_466 / 30_000_000_000, places=6)
        mock_fdic.assert_not_called()

    @patch("data.http.get_with_retry")
    def test_sanity_band_flags_mismatch(self, mock_get):
        # A flipped stated-value pairing would produce an absurd multiple —
        # flagged + n/a, never displayed.
        from data.deal_comps import compute_multiples
        mock_get.return_value = _fdic_resp(20_000, 1_000, 400_000, 500_000)
        out, ok = compute_multiples(_deal(value_usd=500_000_000))
        self.assertTrue(ok)
        self.assertIsNone(out["p_tbv"])
        self.assertIsNone(out["core_dep_premium"])
        self.assertIn("sanity band", out["flagged"])

    @patch("data.http.get_with_retry")
    def test_thin_core_deposits_no_premium(self, mock_get):
        from data.deal_comps import compute_multiples
        # COREDEP 5% of assets — a wholesale-funded shell; premium n/a.
        mock_get.return_value = _fdic_resp(97_000, 17_375, 46_000, 920_000)
        out, _ = compute_multiples(_deal())
        self.assertIsNotNone(out["p_tbv"])
        self.assertIsNone(out["core_dep_premium"])

    def test_unpriced_or_branch_no_multiples(self):
        from data.deal_comps import compute_multiples
        out, ok = compute_multiples(_deal(value_usd=None))
        self.assertTrue(ok)
        self.assertIsNone(out["tbv_usd"])
        out, ok = compute_multiples(_deal(deal_kind="branch"))
        self.assertIsNone(out["tbv_usd"])

    @patch("data.http.get_with_retry", side_effect=Exception("down"))
    def test_lookup_failure_not_ok(self, _mg):
        from data.deal_comps import compute_multiples
        out, ok = compute_multiples(_deal())
        self.assertFalse(ok)
        self.assertIsNone(out["p_tbv"])


class TestSnapshot(unittest.TestCase):

    @patch("data.cache.put")
    @patch("data.deal_comps.compute_multiples",
           return_value=({"tbv_usd": None, "tbv_basis": None, "tbv_asof": None,
                          "p_tbv": None, "price_assets": None,
                          "core_dep_premium": None, "comp_assets": None,
                          "flagged": None}, True))
    @patch("data.ma_history.get_ma_history")
    def test_dedupe_and_sale_skip(self, mock_hist, _cm, mock_cput):
        from data.deal_comps import build_comps_snapshot
        shared = _deal()  # same accession appears under both banks
        mock_hist.side_effect = [
            [shared, _deal(direction="sale", status="completed",
                           announce_url="https://x/accSALE/y.htm")],
            [dict(shared)],
        ]
        snap = build_comps_snapshot([
            {"ticker": "A", "cert": 1, "cik": 10},
            {"ticker": "B", "cert": 2, "cik": 20}])
        self.assertEqual(snap["deals_total"], 1)   # dedup + sale skipped
        self.assertEqual(snap["banks_covered"], 2)
        mock_cput.assert_called_once()

    @patch("data.cache.put")
    @patch("data.deal_comps.compute_multiples",
           return_value=({"tbv_usd": None, "tbv_basis": None, "tbv_asof": None,
                          "p_tbv": None, "price_assets": None,
                          "core_dep_premium": None, "comp_assets": None,
                          "flagged": None}, False))
    @patch("data.ma_history.get_ma_history")
    def test_lookup_failure_skips_cache(self, mock_hist, _cm, mock_cput):
        from data.deal_comps import build_comps_snapshot
        mock_hist.return_value = [_deal()]
        snap = build_comps_snapshot([{"ticker": "A", "cert": 1, "cik": 10}])
        self.assertIsNone(snap)
        mock_cput.assert_not_called()

    @patch("data.cache.get")
    def test_get_snapshot_reads_only(self, mock_cget):
        from data.deal_comps import get_comps_snapshot
        mock_cget.return_value = {"deals": [], "built_at": "x"}
        self.assertEqual(get_comps_snapshot()["built_at"], "x")
        mock_cget.return_value = None
        self.assertIsNone(get_comps_snapshot())


if __name__ == "__main__":
    unittest.main()
