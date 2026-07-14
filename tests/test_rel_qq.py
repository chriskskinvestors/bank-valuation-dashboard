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
        # a metric absent from ALL periods must not produce a row
        self.assertNotIn("div_ps", rows)

    def test_detail_row_is_exhibit_table(self):
        html = _rel_detail_tr(self.FIX, ncols=14)
        for want in ("2Q25A", "1Q26A", "2Q26A", "Cons.", "LQ Δ", "Y/Y Δ",
                     "3.95%", "$1.14", "105.70%",
                     "actuals from the release", "release ↗"):
            self.assertIn(want, html)


if __name__ == "__main__":
    unittest.main()
