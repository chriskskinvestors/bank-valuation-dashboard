"""REVIEW 2026-10-05 P2: Performance Analysis' per-share rows showed a Q4
derived as FY − 9-mo (no Q4 10-Q exists) as if filed — per-share values
aren't strictly additive across quarters' share counts (JPM Q4'24 $4.82 vs
$4.81 reported). The click-through now says "derived"; Financial Highlights
already did.

Run: python -m unittest tests.test_derived_q4_per_share
"""
import types
import unittest
from datetime import datetime
from unittest import mock

import pandas as pd

import ui.financials_statements as FS
import tests.test_statement_units_p2 as U

SPEC = [("Per Share", [("Diluted EPS", "ps", "eps"),
                       ("Diluted EPS before amortization", "ps", "eps_before_amort")])]
HIST = pd.DataFrame([{"REPDTE": "2024-09-30"}, {"REPDTE": "2024-12-31"}])
PS = {datetime(2024, 9, 30): {"eps": 4.37, "eps_note": None, "eps_before_amort": 4.40},
      datetime(2024, 12, 31): {"eps": 4.82, "eps_note": "derived: FY 19.75 − 9-mo 14.93",
                               "eps_before_amort": 4.85}}


def _render():
    calls, html = [], []
    fake_ex = types.SimpleNamespace(
        download_button=lambda label, data, **k: calls.append(data),
        container=lambda *a, **k: U._Ctx())
    import data.loaders as loaders
    import ui.export as ex
    import ui.financial_highlights as FH
    with mock.patch.object(FS, "st", U._stub_st("Quarterly")), \
            mock.patch.object(FS, "components",
                              types.SimpleNamespace(html=lambda h, **k: html.append(h))), \
            mock.patch.object(FS, "get_bank_info",
                              lambda t: {"name": "JPM", "fdic_cert": 628, "cik": 19617}), \
            mock.patch.object(FS, "_render_statement_trends", lambda *a, **k: None), \
            mock.patch.object(loaders, "load_fdic_hist_df", lambda t, q: HIST.copy()), \
            mock.patch.object(FH, "_per_share_for_ends", lambda cik, ends, quarterly=False: PS), \
            mock.patch.object(ex, "st", fake_ex):
        FS.render_statement("JPM", "stmt", "Statement", SPEC, trends=[], with_persh=True)
    return html[0]


class TestDerivedQ4Labeled(unittest.TestCase):
    def test_derived_q4_says_derived_filed_quarter_does_not(self):
        html = _render()
        self.assertIn("derived: FY 19.75", html)
        self.assertIn("strictly additive", html)
        self.assertEqual(html.count("(derived)"), 2)     # EPS + EPS before amortization, Q4 only


if __name__ == "__main__":
    unittest.main()
