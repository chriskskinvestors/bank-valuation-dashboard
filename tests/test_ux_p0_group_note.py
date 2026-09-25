"""UX review 2026-09-24 P0-05: a multi-charter bank's average-based FDIC
ratios (ROA, ROE, NIMY, NTLNLSR …) are deliberately None after cert_group
consolidation, and JPM showed them as five unexplained dashes. The pages now
carry one explanatory line; single-charter banks get nothing."""
import unittest

from tests import _streamlit_stub

_streamlit_stub.install()

import pandas as pd  # noqa: E402

from ui.states import group_ratio_note  # noqa: E402


class TestGroupRatioNote(unittest.TestCase):
    def test_multi_charter_dict_record(self):
        note = group_ratio_note("JPM", {"REPDTE": "20260630", "ROA": None,
                                        "_charter_count": 2})
        self.assertIsNotNone(note)
        self.assertIn("JPM", note)
        self.assertIn("2 bank charters", note)
        self.assertIn("ROAA", note)

    def test_multi_charter_pandas_row(self):
        # Financial Highlights / Performance Analysis pass a DataFrame row.
        row = pd.DataFrame([{"REPDTE": "20260630", "_charter_count": 16}]).iloc[-1]
        self.assertIn("16 bank charters", group_ratio_note("WTFC", row))

    def test_single_charter_is_silent(self):
        self.assertIsNone(group_ratio_note("BSBK", {"_charter_count": 1}))
        self.assertIsNone(group_ratio_note("BSBK", {"ROA": 0.29}))  # no marker

    def test_nan_marker_is_silent(self):
        # Round-tripped DataFrame rows carry NaN where one charter reported.
        row = pd.DataFrame([{"REPDTE": "20210630", "_charter_count": float("nan")}]).iloc[-1]
        self.assertIsNone(group_ratio_note("JPM", row))

    def test_missing_record_is_silent(self):
        self.assertIsNone(group_ratio_note("JPM", {}))
        self.assertIsNone(group_ratio_note("JPM", None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
