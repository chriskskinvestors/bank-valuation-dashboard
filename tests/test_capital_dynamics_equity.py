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


if __name__ == "__main__":
    unittest.main()
