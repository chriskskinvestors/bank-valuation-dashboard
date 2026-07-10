"""
Pins the Ownership → Holder History QoQ move classification (SNL plan §13
phase 1, built 2026-07-10): _qoq_moves over the stored holder × quarter
snapshots. New = present latest-only, Exited = prior-only (sample presence,
not proof of a market exit), unchanged omitted, Δ% only when a prior base
exists.
"""
import sys
import types
import unittest

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from ui.ownership import _qoq_moves  # noqa: E402

Q1, Q0 = "2026Q1", "2025Q4"

HIST = {
    "Adder LLC":    {Q1: {"shares": 150.0}, Q0: {"shares": 100.0}},
    "Trimmer LP":   {Q1: {"shares": 40.0},  Q0: {"shares": 100.0}},
    "Newcomer Inc": {Q1: {"shares": 500.0}},
    "Ghost Capital": {Q0: {"shares": 80.0}},
    "Steady Fund":  {Q1: {"shares": 200.0}, Q0: {"shares": 200.0}},
    "Ancient Hold": {"2024Q2": {"shares": 999.0}},   # outside both quarters
}


class TestQoqMoves(unittest.TestCase):
    def setUp(self):
        self.by_name = {m["Institution"]: m for m in _qoq_moves(HIST, Q1, Q0)}

    def test_adder_positive_delta_and_pct(self):
        m = self.by_name["Adder LLC"]
        self.assertEqual(m["Δ Shares"], 50.0)
        self.assertAlmostEqual(m["Δ %"], 50.0)
        self.assertEqual(m["Status"], "")

    def test_trimmer_negative_delta(self):
        m = self.by_name["Trimmer LP"]
        self.assertEqual(m["Δ Shares"], -60.0)
        self.assertAlmostEqual(m["Δ %"], -60.0)

    def test_new_position_no_pct(self):
        m = self.by_name["Newcomer Inc"]
        self.assertEqual(m["Status"], "New")
        self.assertEqual(m["Δ Shares"], 500.0)
        self.assertIsNone(m["Δ %"])

    def test_exited_position_negative_full(self):
        m = self.by_name["Ghost Capital"]
        self.assertEqual(m["Status"], "Exited")
        self.assertEqual(m["Δ Shares"], -80.0)

    def test_unchanged_and_out_of_window_omitted(self):
        self.assertNotIn("Steady Fund", self.by_name)
        self.assertNotIn("Ancient Hold", self.by_name)


class TestInsiderWindowAggregates(unittest.TestCase):
    """Pins ui/insider_activity._window_aggregates (SNL spec 3M/1Y windows):
    P/S market trades only, trailing-window cutoff, distinct buyer/seller
    counts. Deterministic via the injected `today`."""

    TODAY = __import__("datetime").date(2026, 7, 10)

    TXS = [
        {"date": "2026-07-01", "code": "P", "direction": "Buy",
         "insider": "CEO", "value_usd": 100_000.0},
        {"date": "2026-06-20", "code": "S", "direction": "Sell",
         "insider": "CFO", "value_usd": 40_000.0},
        {"date": "2026-02-01", "code": "P", "direction": "Buy",
         "insider": "Director A", "value_usd": 25_000.0},   # inside 1Y, outside 3M
        {"date": "2026-07-05", "code": "A", "direction": "Buy",
         "insider": "CEO", "value_usd": 999_999.0},          # grant — excluded
        {"date": "2024-01-01", "code": "P", "direction": "Buy",
         "insider": "Old Guy", "value_usd": 77_000.0},       # outside 1Y
    ]

    def test_3m_window(self):
        from ui.insider_activity import _window_aggregates
        w = _window_aggregates(self.TXS, 91, today=self.TODAY)
        self.assertEqual(w["buys_usd"], 100_000.0)   # grant + Feb buy excluded
        self.assertEqual(w["sells_usd"], 40_000.0)
        self.assertEqual((w["buyers"], w["sellers"]), (1, 1))
        self.assertEqual(w["net_usd"], 60_000.0)

    def test_1y_window_includes_feb_excludes_2024(self):
        from ui.insider_activity import _window_aggregates
        w = _window_aggregates(self.TXS, 365, today=self.TODAY)
        self.assertEqual(w["buys_usd"], 125_000.0)
        self.assertEqual(w["buyers"], 2)

    def test_filing_url_shape(self):
        from ui.insider_activity import _filing_url
        url = _filing_url(19617, "0000019617-26-000123")
        self.assertEqual(url, "https://www.sec.gov/Archives/edgar/data/19617/"
                              "000001961726000123/0000019617-26-000123-index.htm")
        self.assertIsNone(_filing_url(19617, None))
        self.assertIsNone(_filing_url(None, "x"))


if __name__ == "__main__":
    unittest.main()
