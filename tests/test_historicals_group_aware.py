"""Financials › Historicals goes through the group-aware FDIC seam (2026-10-12).

ui/historicals.fetch_historical used a raw per-cert request, so a multi-charter
holdco charted ONE charter: WTFC (16 charters) showed total assets $9.57B
(cert 33935 alone) vs the consolidated $74.8B, net income $93.6M vs $595.4M,
and the lead charter's ROAA/CET1 as the company's. It now reads
data.loaders.load_fdic_hist_df (cert_group consolidation), whose average-based
ratios are n/a for a group — explained by ui.states.group_ratio_note.

Run: python -m unittest tests.test_historicals_group_aware
"""
import unittest
from unittest.mock import patch

import pandas as pd

from tests import _streamlit_stub

_streamlit_stub.install()

import ui.historicals as H  # noqa: E402

# WTFC consolidated 2026-06-30 / 2026-03-31 (values from the live group seam).
_WTFC = pd.DataFrame([
    {"REPDTE": pd.Timestamp("2026-06-30"), "ASSET": 74846093.0, "DEP": 62458831.0,
     "NETINC": 595402.0, "ROA": None, "NIMY": None, "EEFFR": 42.38,
     "_charter_count": 16},
    {"REPDTE": pd.Timestamp("2026-03-31"), "ASSET": 73100000.0, "DEP": 61000000.0,
     "NETINC": 290000.0, "ROA": None, "NIMY": None, "EEFFR": 43.10,
     "_charter_count": 16},
])


class TestHistoricalsGroupAware(unittest.TestCase):

    def test_reads_the_consolidated_group_series(self):
        with patch("data.loaders.load_fdic_hist_df", return_value=_WTFC) as m:
            df = H.fetch_historical("WTFC", quarters=20)
        m.assert_called_once_with("WTFC", 20)
        self.assertEqual(df["ASSET"].iloc[0], 74846093.0)       # not one charter
        self.assertEqual(list(df["REPDTE"]), ["20260630", "20260331"])
        self.assertEqual(list(df["Period"]), ["2026Q2", "2026Q1"])
        self.assertTrue(pd.isna(df["ROA"].iloc[0]))              # n/a, not lead's

    def test_annual_view_works_on_the_normalized_dates(self):
        with patch("data.loaders.load_fdic_hist_df", return_value=_WTFC):
            ann = H._annualize(H.fetch_historical("WTFC", quarters=20))
        self.assertEqual(ann["Period"].tolist(), ["2026 YTD"])

    def test_group_note_explains_the_blank_ratios(self):
        from ui.states import group_ratio_note
        self.assertIn("16 bank charters", group_ratio_note("WTFC", _WTFC.iloc[0]))

    def test_empty_loader_is_empty_frame(self):
        with patch("data.loaders.load_fdic_hist_df", return_value=pd.DataFrame()):
            self.assertTrue(H.fetch_historical("NONE", 20).empty)


if __name__ == "__main__":
    unittest.main(verbosity=2)
