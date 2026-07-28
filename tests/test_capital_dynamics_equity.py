"""
(AUDIT-2026-07-02 P2 #28) build_capital_timeline must not fabricate equity.

The bug: `equity = r.get("EQTOT") or 0` turned a missing EQTOT into 0, so the
quarter produced a NEGATIVE tangible book (0 − intangibles) and poisoned every
downstream QoQ diff, retention ratio, and capital alert with garbage.

Fix: skip a quarter with no reported EQTOT (cardinal rule — n/a, never a guess).

Pins (pure, no network):
  1. a record with EQTOT=None is dropped from the timeline; the surviving
     quarter keeps its real, hand-computed equity/TBV — no fabricated 0-row.
  2. a fully-populated two-quarter series is unaffected (regression guard).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.capital_dynamics import build_capital_timeline  # noqa: E402


def _rec(repdte, eqtot, intangw=100_000, intan=150_000, netinc=40_000, loans=500_000,
         cet1=11.5, total_cap=13.0, leverage=9.5):
    return {"REPDTE": repdte, "EQTOT": eqtot, "INTANGW": intangw,
            "INTAN": intan, "NETINC": netinc, "LNLSNET": loans,
            "IDT1CER": cet1, "RBCRWAJ": total_cap, "RBCT1JR": leverage}


class TestCapitalTimelineEquityGuard(unittest.TestCase):
    def test_missing_eqtot_row_is_dropped(self):
        recs = [
            _rec("2025-03-31", 1_000_000),
            _rec("2025-06-30", None),   # missing equity -> must be skipped
        ]
        df = build_capital_timeline(recs)
        self.assertEqual(len(df), 1)
        row = df.iloc[0]
        self.assertEqual(row["equity_k"], 1_000_000)
        # tbv = equity - max(goodwill, intangibles) = 1,000,000 - 150,000
        self.assertEqual(row["tbv_k"], 850_000)
        # nothing negative slipped in from a fabricated 0-equity quarter
        self.assertTrue((df["tbv_k"] > 0).all())

    def test_full_series_unaffected(self):
        recs = [
            _rec("2025-03-31", 1_000_000),
            _rec("2025-06-30", 1_050_000),
        ]
        df = build_capital_timeline(recs)
        self.assertEqual(len(df), 2)
        self.assertEqual(list(df["equity_k"]), [1_000_000, 1_050_000])
        # equity QoQ diff is real
        self.assertEqual(df["equity_qoq_k"].iloc[1], 50_000)


class TestQoQNeverSpansAGap(unittest.TestCase):
    """(AUDIT-2026-07-27 P3) The #28 skip above left a residual: every QoQ column
    is a POSITIONAL .diff(), so dropping Q2 made the Q3 row report Q3−Q1 while
    still being labeled and consumed as one quarter. capital_returned_k then
    pairs a single-quarter NI against a TWO-quarter equity change, understating
    retention_ratio enough to fire a false "high_payout" alert. Across a gap the
    honest answer is n/a."""

    def test_diff_across_dropped_quarter_is_na(self):
        import pandas as pd
        recs = [
            _rec("2025-03-31", 1_000_000, netinc=40_000, loans=500_000, cet1=11.5),
            _rec("2025-06-30", None),      # dropped by the #28 guard
            _rec("2025-09-30", 1_200_000, netinc=120_000, loans=560_000, cet1=12.1),
        ]
        df = build_capital_timeline(recs)
        self.assertEqual(len(df), 2)                   # Q1, Q3
        q3 = df.iloc[1]
        # Q3 follows Q1 positionally but is NOT the prior quarter: the raw diff
        # would be 1,200,000-1,000,000 = 200,000 of equity over TWO quarters,
        # 60,000 of loans, and +0.6pp of CET1 — all n/a instead.
        for col in ("equity_qoq_k", "tbv_qoq_k", "loan_growth_qoq_k",
                    "loan_growth_qoq_pct", "cet1_qoq_pp"):
            self.assertTrue(pd.isna(q3[col]), f"{col} must be n/a across a gap")
        # …and the metrics derived from the equity diff go with it, so no
        # payout/retention figure is computed from a two-quarter change.
        self.assertTrue(pd.isna(q3["capital_returned_k"]))
        self.assertTrue(pd.isna(q3["retention_ratio"]))

    def test_adjacent_quarters_still_diff_normally(self):
        """Hand-computed regression guard: no gap → real QoQ values."""
        recs = [
            _rec("2025-03-31", 1_000_000, netinc=40_000, loans=500_000, cet1=11.5),
            _rec("2025-06-30", 1_050_000, netinc=90_000, loans=530_000, cet1=11.9),
        ]
        df = build_capital_timeline(recs)
        q2 = df.iloc[1]
        self.assertEqual(q2["equity_qoq_k"], 50_000)
        self.assertEqual(q2["loan_growth_qoq_k"], 30_000)
        self.assertAlmostEqual(q2["loan_growth_qoq_pct"], 6.0, places=6)
        self.assertAlmostEqual(q2["cet1_qoq_pp"], 0.4, places=6)
        # Q2 quarterly NI = YTD 90,000 − Q1 YTD 40,000 = 50,000;
        # capital_returned = 50,000 − 50,000 = 0 → retention 1.0
        self.assertEqual(q2["net_income_k_qtr"], 50_000)
        self.assertEqual(q2["capital_returned_k"], 0)
        self.assertAlmostEqual(q2["retention_ratio"], 1.0, places=6)

    def test_year_boundary_is_adjacent(self):
        """Q4→Q1 across a year end is a real quarter step, not a gap."""
        import pandas as pd
        recs = [
            _rec("2025-12-31", 1_100_000, netinc=160_000, loans=540_000),
            _rec("2026-03-31", 1_150_000, netinc=45_000, loans=560_000),
        ]
        df = build_capital_timeline(recs)
        self.assertEqual(len(df), 2)
        self.assertFalse(pd.isna(df.iloc[1]["equity_qoq_k"]))
        self.assertEqual(df.iloc[1]["equity_qoq_k"], 50_000)


if __name__ == "__main__":
    unittest.main()
