"""The "Deposit Funding Mix (%)" trend chart plots its computed ratios
(REVIEW 2026-10-05 P1-5: every key is a computed metric with no fdic_field,
so the chart rendered "Not reported for this bank" for every bank).

ONB 2026-06-30, $K as FDIC reports: DEP 56,985,995; DEPNIDOM 13,562,418;
COREDEP 49,301,968; BRO 4,505,000; DEPINS 32,454,573; DEPUNINS 22,816,000.
"""
import unittest

import pandas as pd

from tests import _streamlit_stub

_streamlit_stub.install()

from ui.charts import grouped_trend_chart  # noqa: E402

KEYS = ["nonint_dep_pct", "core_dep_pct", "uninsured_pct", "brokered_pct"]
ROW = {"REPDTE": pd.Timestamp("2026-06-30"), "DEP": 56_985_995,
       "DEPNIDOM": 13_562_418, "DEPIDOM": 43_423_577, "COREDEP": 49_301_968, "BRO": 4_505_000,
       "DEPINS": 32_454_573, "DEPUNINS": 22_816_000}


class TestDepositMixTrend(unittest.TestCase):
    def test_computed_ratios_are_plotted_with_table_definitions(self):
        fig = grouped_trend_chart(pd.DataFrame([ROW]), KEYS, "Deposit Funding Mix (%)")
        got = {tr.name: float(list(tr.y)[0]) for tr in fig.data}
        # Domestic over domestic (DEPIDOM + DEPNIDOM = DEPDOM; ONB has no
        # foreign offices, so = ÷ DEP here).
        self.assertAlmostEqual(got["Non-Int Dep %"],
                               13_562_418 / (43_423_577 + 13_562_418) * 100)
        self.assertAlmostEqual(got["Core Dep %"], 49_301_968 / 56_985_995 * 100)
        self.assertAlmostEqual(got["Brokered %"], 4_505_000 / 56_985_995 * 100)
        # Insurance base, never ÷ DEP.
        self.assertAlmostEqual(got["Uninsured %"],
                               22_816_000 / (32_454_573 + 22_816_000) * 100)

    def test_missing_inputs_are_skipped_not_zero(self):
        row = {k: v for k, v in ROW.items() if k not in ("BRO", "DEPINS")}
        fig = grouped_trend_chart(pd.DataFrame([row]), KEYS, "x")
        self.assertEqual(sorted(tr.name for tr in fig.data),
                         ["Core Dep %", "Non-Int Dep %"])

    def test_zero_denominator_is_nan(self):
        fig = grouped_trend_chart(pd.DataFrame([dict(ROW, DEP=0)]), ["core_dep_pct"], "x")
        # all-NaN series is skipped → the honest "not reported" figure
        self.assertEqual(len(fig.data), 0)



class TestProFormaCaptionOnDefaultRange(unittest.TestCase):
    """Multi-charter groups' statement columns are today's charters summed at
    every period — the caption says so on the DEFAULT range too (REVIEW
    2026-10-05 P1-4: WTFC FY2021-23 silently included Macatawa Bank)."""

    def _captions(self, note, group=(1, 2)):
        from unittest import mock
        import tests.test_statement_units_p2 as U
        captions = []
        orig = U._stub_st

        def stub(period):
            st = orig(period)
            st.caption = lambda *a, **k: captions.append(a[0] if a else "")
            return st
        with mock.patch.object(U, "_stub_st", stub), \
                mock.patch.object(U.FS, "entity_note", lambda t: note), \
                mock.patch("data.cert_group.get_cert_group_cached",
                           lambda c: list(group)):
            U._render(U.TestFteRowsNotIngestedReason.SPEC,
                      U.TestFteRowsNotIngestedReason.HIST, "Annual")
        return " ".join(captions)

    def test_group_note_shown(self):
        note = ("bank-subsidiary call reports · 16 charters combined (today's "
                "charter group summed at every quarter — pro forma before each "
                "charter joined)")
        self.assertIn("16 charters combined", self._captions(note))

    def test_single_charter_unchanged(self):
        # A single-charter bank never gets the pro-forma note, and the render
        # never calls entity_note (no group lookup beyond the cached map).
        self.assertNotIn("charters combined",
                         self._captions("bank-subsidiary call reports · 2 charters combined",
                                        group=(4242,)))


if __name__ == "__main__":
    unittest.main()
