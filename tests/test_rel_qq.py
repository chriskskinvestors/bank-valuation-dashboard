"""Unit tests for the Results-board Q/Q release cells (ui/earnings).

Directional coloring is correctness: a falling efficiency ratio must read
GREEN (better), a falling NIM red. Run: python -m unittest tests.test_rel_qq
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from ui.earnings import _rel_qq_html, _rel_detail_tr  # noqa: E402


class TestRelQqHtml(unittest.TestCase):
    def test_higher_better_up_is_pos(self):
        out = _rel_qq_html("nim", 3.95, 3.94, "%")
        self.assertIn("3.95%", out)
        self.assertIn('class="pos"', out)
        self.assertIn("+0.01", out)

    def test_lower_better_down_is_pos(self):
        out = _rel_qq_html("efficiency", 52.3, 55.2, "%")
        self.assertIn('class="pos"', out)      # efficiency FELL = good
        self.assertIn("-2.90", out)

    def test_lower_better_up_is_neg(self):
        out = _rel_qq_html("npa_assets", 1.14, 0.98, "%")
        self.assertIn('class="neg"', out)

    def test_acl_is_neutral(self):
        out = _rel_qq_html("acl_loans", 1.51, 1.49, "%")
        self.assertIn('class="mut"', out)

    def test_revenue_relative_delta(self):
        out = _rel_qq_html("total_revenue", 174_752_000.0, 172_340_000.0, "$M")
        self.assertIn("$175M", out)
        self.assertIn("+1.4%", out)            # 174.752/172.340 - 1
        self.assertIn('class="pos"', out)

    def test_no_prior_is_value_only(self):
        self.assertEqual(_rel_qq_html("nim", 3.95, None, "%"), "3.95%")

    def test_missing_is_muted_dash(self):
        self.assertIn("mut", _rel_qq_html("nim", None, 3.94, "%"))


class TestRelExhibit(unittest.TestCase):
    FIX = {"rel": {"qend": "2026-06-30",
                   "metrics": {"nim": 3.95, "efficiency": 52.3,
                               "eps_adj": 1.14},
                   "prior_metrics": {"nim": 3.94, "efficiency": 55.2,
                                     "eps_adj": 1.12},
                   "prior_qend": "2026-03-31",
                   "yoy_metrics": {"nim": 3.68, "efficiency": 105.7,
                                   "eps_adj": 0.88},
                   "yoy_qend": "2025-06-30",
                   "capital": {"cet1_ratio": 11.0},
                   "url": "https://x"},
           "eps_est": 1.14, "rev_est": None,
           "eps_act_src": "release, adj."}

    def test_q_labels(self):
        from ui.earnings import _q_label
        self.assertEqual(_q_label("2026-06-30"), "2Q26")
        self.assertEqual(_q_label("2025-12-31"), "4Q25")
        self.assertEqual(_q_label(None), "—")

    def test_exhibit_rows_hand_computed(self):
        from ui.earnings import _rel_exhibit_rows
        rows = {r["key"]: r for r in _rel_exhibit_rows(self.FIX)}
        eff = rows["efficiency"]
        self.assertEqual((eff["yoy"], eff["prior"], eff["cur"]),
                         (105.7, 55.2, 52.3))
        self.assertIn("-2.90", eff["lq_html"])        # 52.3 - 55.2
        self.assertIn('class="pos"', eff["lq_html"])  # falling eff = good
        self.assertIn("-53.40", eff["yy_html"])       # 52.3 - 105.7
        eps = rows["eps_adj"]
        self.assertEqual(eps["cons"], 1.14)           # consensus lands on EPS
        self.assertIn("+0.26", eps["yy_html"])        # 1.14 - 0.88
        # FIXED broker-sheet shape (owner decision 2026-07-14): every bank
        # gets the same row set — an all-absent metric still renders, muted.
        self.assertIn("div_ps", rows)
        self.assertIsNone(rows["div_ps"]["cur"])
        from ui.earnings import _REL_METRICS
        self.assertEqual(len(rows), len(_REL_METRICS))

    def test_release_history_wins_over_platform(self):
        """Owner decision 2026-07-14: the bank's own comparative column is
        the history when stated (same basis as current → true deltas);
        platform data fills only the gaps."""
        import ui.earnings as ue
        orig = ue._platform_hist_val
        ue._platform_hist_val = lambda tk, key, q: 9.99
        try:
            rows = {r["key"]: r for r in ue._rel_exhibit_rows(self.FIX)}
            self.assertEqual(rows["nim"]["prior"], 3.94)   # release column wins
            self.assertEqual(rows["roa"]["prior"], 9.99)   # gap → platform fills
        finally:
            ue._platform_hist_val = orig

    def test_fdic_quarterly_hist_beats_grid_never_release(self):
        """History chain: release column → FDIC quarterly ratios → grids."""
        import ui.earnings as ue
        orig = ue._platform_hist_val
        ue._platform_hist_val = lambda tk, key, q: 9.99   # grid (last resort)
        fix = dict(self.FIX)
        fix["fdic_hist"] = {"prior": {"nim": 8.88, "roe": 11.5}}
        try:
            rows = {r["key"]: r for r in ue._rel_exhibit_rows(fix)}
            self.assertEqual(rows["nim"]["prior"], 3.94)    # release wins
            self.assertEqual(rows["roe"]["prior"], 11.5)    # FDIC quarterly
            self.assertEqual(rows["tbv_ps"]["prior"], 9.99)  # grid (SEC) fills
            # board-built sec_hist beats the grid (grid-coverage holes)
            fix2 = dict(fix)
            fix2["sec_hist"] = {"prior": {"tbv_ps": 13.54}}
            rows2 = {r["key"]: r for r in ue._rel_exhibit_rows(fix2)}
            self.assertEqual(rows2["tbv_ps"]["prior"], 13.54)
        finally:
            ue._platform_hist_val = orig

    def test_consensus_actuals_fill_consensus_rows_only(self):
        from ui.earnings import _rel_exhibit_rows
        # FMP-sourced actuals (no _src flag) land on EPS adj / Revenue cur.
        fix = {"rel": {"qend": "2026-06-30", "metrics": {},
                       "prior_metrics": {}, "yoy_metrics": {}, "capital": {}},
               "eps_act": 3.46, "rev_act": 19.6e9, "eps_est": 2.89}
        rows = {r["key"]: r for r in _rel_exhibit_rows(fix)}
        self.assertEqual(rows["eps_adj"]["cur"], 3.46)
        self.assertEqual(rows["total_revenue"]["cur"], 19.6e9)
        self.assertIsNone(rows["eps_diluted"]["cur"])   # never the GAAP row
        # Release-sourced board actuals (starred) must NOT double-land: the
        # release extraction already carries them on their own basis.
        fix2 = {"rel": {"qend": "2026-06-30", "metrics": {"eps_diluted": 1.64},
                        "prior_metrics": {}, "yoy_metrics": {}, "capital": {}},
                "eps_act": 1.64, "eps_act_src": "release, GAAP"}
        rows2 = {r["key"]: r for r in _rel_exhibit_rows(fix2)}
        self.assertIsNone(rows2["eps_adj"]["cur"])
        self.assertEqual(rows2["eps_diluted"]["cur"], 1.64)

    def test_detail_row_is_exhibit_table(self):
        html = _rel_detail_tr(self.FIX, ncols=14)
        for want in ("2Q25A", "1Q26A", "2Q26A", "Cons.", "LQ Δ", "Y/Y Δ",
                     "3.95%", "$1.14", "105.70%",
                     "actuals from the release", "release ↗"):
            self.assertIn(want, html)


if __name__ == "__main__":
    unittest.main()
