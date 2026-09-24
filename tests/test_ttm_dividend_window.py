"""
TTM dividends per share = four consecutive quarters ending at the anchor, or
None (REVIEW-2026-09-24 P0-2).

The old step 2 summed every ~3-month fact inside a 370-day window, which holds
FIVE quarter-ends for an issuer that tags a discrete Q4 (E − 365 d is inside
the window), substituted the year-ago quarter for an untagged Q4, or returned
ONE quarter for a YTD-only tagger; step 3 then served a 9-month cumulative as
"TTM". Fixtures use dates relative to today so the 400-day staleness guard in
_extract_ttm_dividend does not fire.
"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests import _streamlit_stub  # noqa: E402,F401  (installs the stub)
from data.sec_client import _extract_ttm_dividend  # noqa: E402

CONCEPT = "CommonStockDividendsPerShareDeclared"


def _quarter_ends(n: int) -> list[date]:
    """The n most recent calendar quarter-ends on or before today, newest first."""
    today = date.today()
    ends = []
    y, m = today.year, ((today.month - 1) // 3) * 3  # last completed quarter's end month
    if m == 0:
        y, m = y - 1, 12
    for _ in range(n):
        d = date(y, m, 1) + timedelta(days=32)
        ends.append(date(d.year, d.month, 1) - timedelta(days=1))
        m -= 3
        if m == 0:
            y, m = y - 1, 12
    return ends


def _entry(start: date, end: date, val: float, form: str = "10-Q", filed: str = "2026-01-01"):
    return {"start": start.isoformat(), "end": end.isoformat(), "val": val,
            "form": form, "filed": filed}


def _facts(entries):
    return {"facts": {"us-gaap": {CONCEPT: {"units": {"USD/shares": entries}}}}}


def _q_start(end: date) -> date:
    """First day of the quarter that ends on `end`."""
    return date(end.year, end.month - 2, 1)


class TestTtmDividendWindow(unittest.TestCase):
    def setUp(self):
        self.q = _quarter_ends(6)  # q[0] = anchor E, q[1] = one quarter earlier, …

    def test_five_discrete_quarters_sum_four_not_five(self):
        # Issuer tags a discrete Q4 in its 10-K: five 3-month facts inside the
        # old 370-day window. Old result 1.25; TTM is four quarters = 1.00.
        entries = [_entry(_q_start(e), e, 0.25) for e in self.q[:5]]
        self.assertAlmostEqual(_extract_ttm_dividend(_facts(entries)), 1.00, places=9)

    def test_q4_derived_from_fy_minus_nine_months(self):
        # Q1..Q3 tagged as 3-month facts, Q4 only inside the FY annual fact and
        # the 9M cumulative. Anchor = the quarter after that FY.
        e_new, e_fy, e_q3, e_q2, e_q1 = self.q[0], self.q[1], self.q[2], self.q[3], self.q[4]
        fy_start = _q_start(e_q1)
        entries = [
            _entry(_q_start(e_new), e_new, 0.25),          # anchor quarter
            _entry(_q_start(e_q3), e_q3, 0.20),
            _entry(_q_start(e_q2), e_q2, 0.20),
            _entry(_q_start(e_q1), e_q1, 0.20),
            _entry(fy_start, e_q3, 0.60),                  # 9M YTD
            _entry(fy_start, e_fy, 0.85, form="10-K"),     # FY → Q4 = 0.85 − 0.60 = 0.25
        ]
        # Old: 0.25 + 0.20 + 0.20 + 0.20 (year-ago Q1 substituted for Q4) = 0.85.
        self.assertAlmostEqual(_extract_ttm_dividend(_facts(entries)),
                               0.25 + 0.25 + 0.20 + 0.20, places=9)

    def test_untagged_q4_with_no_cumulative_is_none(self):
        # Q4 missing and nothing to derive it from → the window has a hole.
        e_new, _e_fy, e_q3, e_q2, e_q1 = self.q[0], self.q[1], self.q[2], self.q[3], self.q[4]
        entries = [_entry(_q_start(e), e, 0.25) for e in (e_new, e_q3, e_q2, e_q1)]
        # Old: summed the four (year-ago Q1 standing in for Q4) = 1.00.
        self.assertIsNone(_extract_ttm_dividend(_facts(entries)))

    def test_ytd_only_tagger_is_none_not_one_quarter(self):
        # Only the anchor quarter is a 3-month fact; the rest is a 9M cumulative
        # with no same-start sibling to difference against.
        e_new, e_q3 = self.q[0], self.q[1]
        entries = [_entry(_q_start(e_new), e_new, 0.25),
                   _entry(_q_start(self.q[3]), e_q3, 0.75)]
        # Old: 0.25 (one quarter as TTM).
        self.assertIsNone(_extract_ttm_dividend(_facts(entries)))

    def test_nine_month_cumulative_alone_is_none_not_ttm(self):
        # Old step 3 returned the latest cumulative (0.75) under the TTM label.
        e_q3 = self.q[0]
        entries = [_entry(_q_start(self.q[2]), e_q3, 0.75)]
        self.assertIsNone(_extract_ttm_dividend(_facts(entries)))

    def test_annual_at_anchor_is_authoritative(self):
        e_fy = self.q[0]
        entries = [_entry(_q_start(self.q[3]), e_fy, 0.85, form="10-K"),
                   _entry(_q_start(e_fy), e_fy, 0.30)]
        self.assertAlmostEqual(_extract_ttm_dividend(_facts(entries)), 0.85, places=9)

    def test_restatement_latest_filing_wins(self):
        entries = [_entry(_q_start(e), e, 0.25) for e in self.q[:4]]
        entries.append(_entry(_q_start(self.q[1]), self.q[1], 0.30, filed="2026-06-01"))
        self.assertAlmostEqual(_extract_ttm_dividend(_facts(entries)), 1.05, places=9)


if __name__ == "__main__":
    unittest.main()
