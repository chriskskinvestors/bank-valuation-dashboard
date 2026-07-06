"""
Pins AUDIT-2026-07-02 finding #15 (ui/historicals.py Annual Summary):

  (a) the in-progress year must NOT render as a bare full-year column —
      its label carries a YTD marker ("2026 YTD") and its flow value is
      the honest YTD (2-quarter) cumulative, never annualized/extrapolated;
  (b) ratio fields (point-in-time NCLNLSR/IDT1CER and FDIC YTD-annualized
      ROA/NIMY/EEFFR/...) take the year's LAST reported quarter's value —
      Q4 for a complete year — never the mean of the four quarter-ends.

Synthetic FDIC quarterly rows; no network, no streamlit runtime.
"""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Stub streamlit (and the module tree ui.historicals pulls in at import
# time) before importing it (house pattern — see test_sec_8k_adapter.py).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.session_state = {}
for _name in ("markdown", "caption", "write", "info", "warning", "error",
              "dataframe", "plotly_chart", "html"):
    setattr(_st, _name, lambda *a, **k: None)
sys.modules.setdefault("streamlit", _st)

import pandas as pd  # noqa: E402

from ui.historicals import _annualize, _year_label, RATIO_FIELDS  # noqa: E402


def _row(repdte, netinc, ntlnls, roa, nclnlsr, idt1cer, asset):
    """One synthetic FDIC financials row. Flow fields (NETINC, NTLNLS) are
    YTD-cumulative like the real API; ratios ROA (YTD-annualized) and
    NCLNLSR/IDT1CER (point-in-time) vary by quarter so mean != last."""
    return {
        "CERT": 12345, "REPDTE": repdte,
        "Period": f"{str(repdte)[:4]}Q{(int(str(repdte)[4:6]) - 1) // 3 + 1}",
        "NETINC": netinc, "NTLNLS": ntlnls,
        "ROA": roa, "NCLNLSR": nclnlsr, "IDT1CER": idt1cer,
        "ASSET": asset,
    }


# Two complete years + one in-progress (2-quarter) year, newest first as
# fetch_historical returns (REPDTE sort DESC).
#   2024 discrete-quarter NI: 100, 150, 130, 120  -> FY 500 (YTD cum.)
#   2025 discrete-quarter NI: 200, 210, 190, 200  -> FY 800
#   2026 discrete-quarter NI: 110, 140            -> YTD 250
ROWS = [
    _row(20260630, 250, 50, 1.10, 0.55, 11.5, 10_500_000),   # 2026 Q2 (YTD)
    _row(20260331, 110, 20, 1.30, 0.50, 11.2, 10_300_000),   # 2026 Q1
    _row(20251231, 800, 160, 1.05, 0.60, 11.0, 10_000_000),  # 2025 Q4
    _row(20250930, 600, 120, 0.95, 0.75, 10.8, 9_800_000),
    _row(20250630, 410, 70, 0.90, 0.80, 10.6, 9_600_000),
    _row(20250331, 200, 30, 0.85, 0.90, 10.4, 9_500_000),
    _row(20241231, 500, 100, 0.70, 1.10, 10.0, 9_200_000),   # 2024 Q4
    _row(20240930, 380, 80, 0.65, 1.20, 9.8, 9_000_000),
    _row(20240630, 250, 50, 0.60, 1.30, 9.6, 8_900_000),
    _row(20240331, 100, 20, 0.55, 1.40, 9.4, 8_800_000),
]


def _df():
    return pd.DataFrame([dict(r) for r in ROWS])


def _annual():
    ann = _annualize(_df())
    return {row["Period"]: row for _, row in ann.iterrows()}


class TestFlowFieldsSumPerYear(unittest.TestCase):
    """Annual flow value == sum of the year's discrete quarters (FDIC rows
    are YTD-cumulative, so the year's last quarter carries that sum)."""

    def test_complete_year_flow_is_full_year_sum(self):
        by = _annual()
        # 2024: 100+150+130+120 = 500 ; 2025: 200+210+190+200 = 800
        self.assertEqual(by["2024"]["NETINC"], 500)
        self.assertEqual(by["2025"]["NETINC"], 800)
        self.assertEqual(by["2024"]["NTLNLS"], 100)
        self.assertEqual(by["2025"]["NTLNLS"], 160)

    def test_flow_never_a_stale_earlier_quarter(self):
        by = _annual()
        # Guard against picking Q1 (min REPDTE) by accident.
        self.assertNotEqual(by["2025"]["NETINC"], 200)


class TestRatioFieldsLastQuarterNotMean(unittest.TestCase):
    """Ratio annual value == the year's LAST quarter (Q4), not the mean —
    correct for both point-in-time (NCLNLSR, IDT1CER) and FDIC
    YTD-annualized (ROA) kinds."""

    def test_complete_year_ratios_are_q4(self):
        by = _annual()
        self.assertAlmostEqual(by["2024"]["ROA"], 0.70)       # mean = 0.625
        self.assertAlmostEqual(by["2025"]["ROA"], 1.05)       # mean = 0.9375
        self.assertAlmostEqual(by["2024"]["NCLNLSR"], 1.10)   # mean = 1.25
        self.assertAlmostEqual(by["2025"]["IDT1CER"], 11.0)   # mean = 10.7

    def test_ratios_not_the_mean(self):
        by = _annual()
        self.assertNotAlmostEqual(by["2024"]["ROA"], 0.625)
        self.assertNotAlmostEqual(by["2024"]["NCLNLSR"], 1.25)

    def test_ratio_last_available_skips_null_latest_quarter(self):
        # n/a-over-guess, dense-info: if Q4's ratio is missing, show the
        # latest quarter that HAS one rather than a mean or a blank year.
        df = _df()
        df.loc[df["REPDTE"] == 20251231, "IDT1CER"] = None
        ann = _annualize(df)
        by = {row["Period"]: row for _, row in ann.iterrows()}
        self.assertAlmostEqual(by["2025"]["IDT1CER"], 10.8)   # 2025 Q3


class TestInProgressYearLabeledYTD(unittest.TestCase):
    """The 2-quarter in-progress year renders as '2026 YTD', its flow value
    is the honest 2-quarter sum (never annualized), and its ratios are the
    latest quarter's."""

    def test_label_carries_ytd_marker(self):
        by = _annual()
        self.assertIn("2026 YTD", by)
        self.assertNotIn("2026", by)  # never a bare full-year column

    def test_flow_is_two_quarter_sum_not_extrapolated(self):
        by = _annual()
        # 110 + 140 = 250 (Q2 YTD row) — NOT 500 (a 2x annualization).
        self.assertEqual(by["2026 YTD"]["NETINC"], 250)
        self.assertEqual(by["2026 YTD"]["NTLNLS"], 50)

    def test_ratios_are_latest_quarter(self):
        by = _annual()
        self.assertAlmostEqual(by["2026 YTD"]["ROA"], 1.10)      # Q2, not mean 1.20
        self.assertAlmostEqual(by["2026 YTD"]["IDT1CER"], 11.5)  # Q2 point-in-time

    def test_point_in_time_balance_is_latest_quarter(self):
        by = _annual()
        self.assertEqual(by["2026 YTD"]["ASSET"], 10_500_000)

    def test_complete_years_keep_bare_labels_and_sort_newest_first(self):
        ann = _annualize(_df())
        self.assertEqual(list(ann["Period"]), ["2026 YTD", "2025", "2024"])

    def test_year_label_helper(self):
        self.assertEqual(_year_label("2025", 20251231), "2025")
        self.assertEqual(_year_label("2026", 20260630), "2026 YTD")
        self.assertEqual(_year_label("2026", 20260331), "2026 YTD")


class TestRatioFieldListIntegrity(unittest.TestCase):
    def test_every_ratio_field_is_known_kind(self):
        """Every field routed through the last-quarter ratio rule must be one
        of the two conventions that rule is correct for."""
        ytd_annualized = {"ROA", "NIMY", "EEFFR", "INTINCY", "INTEXPY",
                          "NONIIAY", "NONIXAY"}
        point_in_time = {"NCLNLSR", "IDT1CER"}
        self.assertEqual(set(RATIO_FIELDS), ytd_annualized | point_in_time)


if __name__ == "__main__":
    unittest.main()
