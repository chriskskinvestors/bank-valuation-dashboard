"""
build_capital_timeline must not crash when a ratio column is all-None.

The bug (found 2026-09-22 on JPM and MTB, Company Analysis › Financials ›
Templated › Capital Adequacy): a multi-charter group's consolidated record
carries None for every average-based ratio (cert_group drops them), so
`cet1_pct` reached `_qoq` as an all-None OBJECT column. A raw `.diff()` on an
object column subtracts None from None and raised

    TypeError: unsupported operand type(s) for -: 'NoneType' and 'NoneType'

which took down the whole tab. A single None among floats never crashed —
pandas builds that column as float64 — so the pin below is the all-None case,
plus the mixed case as a cardinal-rule guard (a missing quarter must be n/a
before and after, never a fabricated 0 pp change).

Fix (analysis/capital_dynamics._qoq): coerce to numeric before diffing; the
gap mask semantics are unchanged.

Pins (pure, no network):
  1. all-None CET1 / total capital / leverage → no exception; cet1_qoq_pp is
     n/a on every row; the numeric columns still diff normally.
  2. one None mid-series → n/a AT the missing quarter and the quarter AFTER it
     (no value to diff from); the neighbouring QoQ values are hand-computed.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.capital_dynamics import build_capital_timeline  # noqa: E402


def _rec(repdte, eqtot, cet1, total_cap=None, leverage=None,
         intangw=100_000, intan=150_000, netinc=40_000, loans=500_000):
    return {"REPDTE": repdte, "EQTOT": eqtot, "INTANGW": intangw,
            "INTAN": intan, "NETINC": netinc, "LNLSNET": loans,
            "IDT1CER": cet1, "RBCRWAJ": total_cap, "RBCT1JR": leverage}


class TestAllNoneRatioColumn(unittest.TestCase):
    def test_all_none_cet1_does_not_raise_and_is_na_everywhere(self):
        recs = [
            _rec("2025-03-31", 1_000_000, None),
            _rec("2025-06-30", 1_050_000, None),
            _rec("2025-09-30", 1_100_000, None),
            _rec("2025-12-31", 1_150_000, None),
        ]
        df = build_capital_timeline(recs)   # raised TypeError before the fix
        self.assertEqual(len(df), 4)
        self.assertTrue(df["cet1_qoq_pp"].isna().all(),
                        "an all-None CET1 series has no defined QoQ change")
        # The input ratio itself stays absent — never coerced to 0.
        self.assertTrue(df["cet1_pct"].isna().all())
        # Numeric columns on the same frame still diff normally (hand-computed:
        # equity steps of 50,000 each quarter; TBV = equity − 150,000 → same step).
        self.assertTrue(pd.isna(df["equity_qoq_k"].iloc[0]))
        self.assertEqual(list(df["equity_qoq_k"].iloc[1:]), [50_000, 50_000, 50_000])
        self.assertEqual(list(df["tbv_qoq_k"].iloc[1:]), [50_000, 50_000, 50_000])


class TestSingleNoneMidSeries(unittest.TestCase):
    def test_none_quarter_is_na_at_and_after_never_zero(self):
        recs = [
            _rec("2025-03-31", 1_000_000, 11.5),
            _rec("2025-06-30", 1_050_000, None),    # FDIC did not report CET1
            _rec("2025-09-30", 1_100_000, 12.1),
            _rec("2025-12-31", 1_150_000, 12.4),
        ]
        df = build_capital_timeline(recs)
        self.assertEqual(len(df), 4)
        q1, q2, q3, q4 = (df.iloc[i] for i in range(4))
        self.assertTrue(pd.isna(q1["cet1_qoq_pp"]))     # first row: nothing prior
        self.assertTrue(pd.isna(q2["cet1_pct"]))        # the gap stays n/a
        self.assertTrue(pd.isna(q2["cet1_qoq_pp"]))     # n/a − 11.5 → n/a
        self.assertTrue(pd.isna(q3["cet1_qoq_pp"]))     # 12.1 − n/a → n/a
        # Q4 − Q3 = 12.4 − 12.1 = 0.3 pp: the one adjacent numeric pair.
        self.assertAlmostEqual(q4["cet1_qoq_pp"], 0.3, places=6)
        # Nothing was fabricated: no 0.0 stands in for the missing quarter.
        self.assertFalse((df["cet1_qoq_pp"].fillna(-999) == 0).any())


if __name__ == "__main__":
    unittest.main()
