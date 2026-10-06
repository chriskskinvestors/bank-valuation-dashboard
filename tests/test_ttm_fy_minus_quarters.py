"""Q4 = FY − (Q1+Q2+Q3) when a filer tags discrete quarters + the full year
but no year-to-date cumulatives (2026-10-06).

The prod coverage audit showed 175 SEC filers with no dividend yield.
BANC and ACNB (hand-read from companyfacts) tag CommonStockDividendsPerShare-
Declared as FY plus three discrete quarters and never a 9M figure, so the
same-start rule (FY − 9M) could not derive Q4 and the TTM went None.

Hand-computed:
  BANC  FY2025 0.40; Q1–Q3 2025 0.10 each → Q4 0.10;
        Q1-26 0.12, Q2-26 0.12 → TTM at 2026-06-30 = 0.10+0.10+0.12+0.12 = 0.44
  ACNB  FY2025 1.38; Q1 0.32, Q2 0.34, Q3 0.34 → Q4 0.38;
        Q1-26 0.38, Q2-26 0.92 → TTM = 0.34+0.38+0.38+0.92 = 2.02
Negative pins: two inner quarters → no Q4 → None; a direct Q4 beats the
derivation; a same-start 9M derivation beats it too; the dollar-flow TTM
builder gains the same rule.
"""
import unittest
from datetime import date, timedelta

from data.sec_client import _extract_ttm_dividend, _extract_ttm_value


def _recent(iso: str) -> str:
    """Shift a fixture date so the latest period is always 'recent' for the
    staleness guards, keeping quarter alignment (shift by whole years)."""
    y = date.today().year - 2026
    return f"{int(iso[:4]) + y}{iso[4:]}"


def _e(start, end, val, form="10-Q", filed=None):
    return {"start": _recent(start), "end": _recent(end), "val": val, "form": form,
            "filed": _recent(filed or end), "accn": "x", "fy": 2026, "fp": "Q"}


def _facts(entries, concept="CommonStockDividendsPerShareDeclared", unit="USD/shares"):
    return {"facts": {"us-gaap": {concept: {"units": {unit: entries}}}}}


BANC = [
    _e("2025-01-01", "2025-03-31", 0.10), _e("2025-04-01", "2025-06-30", 0.10),
    _e("2025-07-01", "2025-09-30", 0.10), _e("2025-01-01", "2025-12-31", 0.40, "10-K", "2026-02-20"),
    _e("2026-01-01", "2026-03-31", 0.12), _e("2026-04-01", "2026-06-30", 0.12),
]


class TestDividendTtm(unittest.TestCase):
    def test_banc_shape_derives_q4_from_fy_minus_quarters(self):
        self.assertAlmostEqual(_extract_ttm_dividend(_facts(BANC)), 0.44, places=9)

    def test_acnb_shape(self):
        acnb = [
            _e("2025-01-01", "2025-03-31", 0.32), _e("2025-04-01", "2025-06-30", 0.34),
            _e("2025-07-01", "2025-09-30", 0.34), _e("2025-01-01", "2025-12-31", 1.38, "10-K", "2026-02-20"),
            _e("2026-01-01", "2026-03-31", 0.38), _e("2026-04-01", "2026-06-30", 0.92),
        ]
        self.assertAlmostEqual(_extract_ttm_dividend(_facts(acnb)), 2.02, places=9)

    def test_two_inner_quarters_is_not_enough(self):
        partial = [e for e in BANC if e["end"] != _recent("2025-06-30")]
        self.assertIsNone(_extract_ttm_dividend(_facts(partial)))

    def test_direct_q4_beats_derivation(self):
        with_q4 = BANC + [_e("2025-10-01", "2025-12-31", 0.11)]
        # 0.10 + 0.11 + 0.12 + 0.12
        self.assertAlmostEqual(_extract_ttm_dividend(_facts(with_q4)), 0.45, places=9)

    def test_same_start_ytd_derivation_still_wins(self):
        with_9m = BANC + [_e("2025-01-01", "2025-09-30", 0.31)]   # FY − 9M = 0.09
        self.assertAlmostEqual(_extract_ttm_dividend(_facts(with_9m)), 0.43, places=9)


class TestFlowTtm(unittest.TestCase):
    def test_dollar_flow_q4_from_fy_minus_quarters(self):
        ni = [
            _e("2025-01-01", "2025-03-31", 10e6), _e("2025-04-01", "2025-06-30", 11e6),
            _e("2025-07-01", "2025-09-30", 12e6), _e("2025-01-01", "2025-12-31", 46e6, "10-K", "2026-02-20"),
            _e("2026-01-01", "2026-03-31", 14e6), _e("2026-04-01", "2026-06-30", 15e6),
        ]
        # Q4-25 = 46 − 33 = 13; TTM = 12 + 13 + 14 + 15 = 54
        self.assertAlmostEqual(_extract_ttm_value(_facts(ni, "NetIncomeLoss", "USD"),
                                                  "NetIncomeLoss"), 54e6, places=3)


if __name__ == "__main__":
    unittest.main()
