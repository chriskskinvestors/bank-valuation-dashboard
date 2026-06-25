"""
Unit tests for analysis.period_actuals.period_actuals — the thin ticker→CIK
wrapper over data.sec_period.fundamentals_for_period. The extraction logic itself
is covered in tests/test_sec_period.py.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import analysis.period_actuals as pa  # noqa: E402


class TestPeriodActuals(unittest.TestCase):

    def setUp(self):
        self._info = pa.get_bank_info
        self._fund = pa.fundamentals_for_period

    def tearDown(self):
        pa.get_bank_info = self._info
        pa.fundamentals_for_period = self._fund

    def test_resolves_cik_and_delegates(self):
        pa.get_bank_info = lambda t: {"cik": 19617}
        seen = {}

        def _fake(cik, period):
            seen["args"] = (cik, period)
            return {"net_income": 150e6, "eps": 3.05}

        pa.fundamentals_for_period = _fake
        out = pa.period_actuals("JPM", "2026Q2")
        self.assertEqual(seen["args"], (19617, "2026Q2"))
        self.assertEqual(out["net_income"], 150e6)
        self.assertEqual(out["eps"], 3.05)

    def test_no_cik_returns_none_without_calling_sec(self):
        pa.get_bank_info = lambda t: {"cik": None}
        called = {"n": 0}

        def _fake(cik, period):
            called["n"] += 1
            return {}

        pa.fundamentals_for_period = _fake
        self.assertIsNone(pa.period_actuals("XXXX", "2026Q2"))
        self.assertEqual(called["n"], 0)   # short-circuits, no SEC fetch

    def test_missing_bank_info_returns_none(self):
        pa.get_bank_info = lambda t: None
        self.assertIsNone(pa.period_actuals("XXXX", "2026Q2"))


if __name__ == "__main__":
    unittest.main()
