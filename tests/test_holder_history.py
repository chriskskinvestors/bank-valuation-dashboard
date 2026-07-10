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


if __name__ == "__main__":
    unittest.main()
