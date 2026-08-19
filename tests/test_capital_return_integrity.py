"""
Regression tests for the 2026-08-19 capital-return fabrication audit
(analysis/capital_return.py). Each class pins a defect that shipped:

D1 — an empty/partial/gapped 4-quarter window summed to a fabricated "TTM"
     (PNC: all-NaN net income window → $0.0B; every major bank's untagged
     dividends → "$0 Divs TTM" in the screener).
D2 — a FRESH but SPARSE concept shadowed the dense fallback (PNC's Q2-2026
     10-Q tagged one undimensioned NetIncomeLoss; the whole NI timeline
     went NaN because quarterly derivation had no priors).
D3 — absent dividend/buyback data fabricated 0% shareholder yields and
     $0 quarterly totals (fillna(0)).
D6 — duration concepts (DPS, flows) deduped by end-date alone mixed 3-month
     and YTD-cumulative facts (JPM "DPS TTM" $14.60 vs true ~$5.70).

All expectations hand-computed. Dates are generated relative to today so the
2-year freshness cutoff in _extract_series never silently stales the fixtures.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# MUST precede the analysis import: analysis.capital_return imports
# data.sec_client at module load, and without the stub sec_client binds REAL
# streamlit — whose cache_data memoizes get_latest_fundamentals by cik, so
# any LATER test module that patches fetch_company_facts and reuses a cik
# gets this-module-era cached results (test_share_equity_coherence's FSUN
# fixture was served JPM's share count when composed after this file).
from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from analysis.capital_return import (  # noqa: E402
    _derive_quarterly_from_ytd,
    _full_window_sum,
    compute_shareholder_yield,
    compute_ttm_capital_return,
)


def _recent_quarter_ends(n: int) -> list[str]:
    """The n most recent COMPLETED calendar quarter-ends, oldest first."""
    ends = []
    y, m = date.today().year, date.today().month
    q_end = {3: (3, 31), 6: (6, 30), 9: (9, 30), 12: (12, 31)}
    # Last completed quarter
    qm = ((m - 1) // 3) * 3  # 0, 3, 6, 9
    if qm == 0:
        y, qm = y - 1, 12
    cur = (y, qm)
    for _ in range(n):
        yy, mm = cur
        ends.append(f"{yy}-{q_end[mm][0]:02d}-{q_end[mm][1]:02d}")
        cur = (yy - 1, 12) if mm == 3 else (yy, mm - 3)
    return ends[::-1]


def _q_start(end: str) -> str:
    """First day of the calendar quarter ending at `end`."""
    y, m = int(end[:4]), int(end[5:7])
    return f"{y}-{m - 2:02d}-01"


def _fy_start(end: str) -> str:
    return f"{end[:4]}-01-01"


def _entry(end, val, start, filed="2026-01-01", fp=None, form="10-Q"):
    return {"end": end, "start": start, "val": val, "form": form,
            "filed": filed, "fp": fp, "fy": None}


class TestFullWindowSum(unittest.TestCase):
    """D1 — TTM is a full consecutive 4-quarter window or None."""

    ENDS = _recent_quarter_ends(4)

    def _frame(self, vals, ends=None):
        return pd.DataFrame({"end": ends or self.ENDS, "net_income_q": vals})

    def test_all_nan_window_is_none_not_zero(self):
        # PNC 2026-08: sum(skipna=True) over all-NaN returned 0.0 — a
        # fabricated "TTM net income $0.0B".
        self.assertIsNone(_full_window_sum(self._frame([None] * 4), "net_income_q"))

    def test_partial_window_is_none(self):
        self.assertIsNone(
            _full_window_sum(self._frame([1e9, None, 2e9, 3e9]), "net_income_q"))

    def test_full_window_sums_exactly(self):
        self.assertEqual(
            _full_window_sum(self._frame([8.469e9, 7.510e9, 8.584e9, 9.074e9]),
                             "net_income_q"),
            33.637e9)  # BAC Q2-2026 hand-check quarters

    def test_gapped_window_is_none(self):
        # 4 filled rows spanning ~2 years (missing quarters in between) must
        # NOT be labeled a trailing-twelve-month figure.
        e8 = _recent_quarter_ends(8)
        gapped = [e8[0], e8[2], e8[4], e8[7]]
        self.assertIsNone(
            _full_window_sum(self._frame([1e9, 1e9, 1e9, 1e9], ends=gapped),
                             "net_income_q"))

    def test_short_window_is_none(self):
        df = pd.DataFrame({"end": self.ENDS[:2], "net_income_q": [1e9, 2e9]})
        self.assertIsNone(_full_window_sum(df, "net_income_q"))


class TestTtmComposition(unittest.TestCase):
    """D1/D3 — unknown component ⇒ unknown total; ratios stay None."""

    ENDS = _recent_quarter_ends(4)

    def _timeline(self, ni, divs, bb):
        return pd.DataFrame({"end": self.ENDS, "net_income_q": ni,
                             "dividends_q": divs, "buybacks_q": bb})

    def test_one_known_component_composes_both_unknown_is_none(self):
        # Merged convention (test_capital_return_pnc_regression): one known
        # side treats the other as 0 (a bank tagging buybacks but no dividend
        # concept genuinely pays none); BOTH unknown is no observation → None.
        ttm = compute_ttm_capital_return(
            self._timeline([2e9] * 4, [None] * 4, [1e8] * 4))
        self.assertEqual(ttm["net_income_ttm"], 8e9)
        self.assertIsNone(ttm["dividends_ttm"])
        self.assertEqual(ttm["buybacks_ttm"], 4e8)
        self.assertEqual(ttm["total_returned_ttm"], 4e8)
        self.assertIsNone(ttm["payout_ratio_ttm"])
        both_none = compute_ttm_capital_return(
            self._timeline([2e9] * 4, [None] * 4, [None] * 4))
        self.assertIsNone(both_none["total_returned_ttm"])
        self.assertIsNone(both_none["total_return_ratio_ttm"])

    def test_full_components_compose(self):
        ttm = compute_ttm_capital_return(
            self._timeline([2e9] * 4, [5e8] * 4, [2.5e8] * 4))
        self.assertEqual(ttm["dividends_ttm"], 2e9)
        self.assertEqual(ttm["total_returned_ttm"], 3e9)
        self.assertAlmostEqual(ttm["payout_ratio_ttm"], 0.25)
        self.assertAlmostEqual(ttm["total_return_ratio_ttm"], 0.375)


class TestShareholderYield(unittest.TestCase):
    """D3 — absent data is an unknown yield (None), never '0.00%'."""

    ENDS = _recent_quarter_ends(4)

    def test_unknown_components_yield_none(self):
        tl = pd.DataFrame({"end": self.ENDS, "net_income_q": [2e9] * 4,
                           "dividends_q": [None] * 4, "buybacks_q": [None] * 4})
        y = compute_shareholder_yield(tl, market_cap=100e9)
        self.assertIsNone(y["dividend_yield_pct"])
        self.assertIsNone(y["buyback_yield_pct"])
        self.assertIsNone(y["total_shareholder_yield_pct"])

    def test_known_components_compute(self):
        tl = pd.DataFrame({"end": self.ENDS, "net_income_q": [2e9] * 4,
                           "dividends_q": [5e8] * 4, "buybacks_q": [5e8] * 4})
        y = compute_shareholder_yield(tl, market_cap=100e9)
        self.assertAlmostEqual(y["dividend_yield_pct"], 2.0)
        self.assertAlmostEqual(y["total_shareholder_yield_pct"], 4.0)


# D2 (fresh-but-sparse concept shadowing a dense fallback) is pinned by
# tests/test_capital_return_pnc_regression.py::TestSparseConceptFallback.


class TestDurationDerivation(unittest.TestCase):
    """D6 — direct 3-month facts win; YTD-only quarters derive by same-year
    cumulative differencing; durations never mix."""

    def test_mixed_duration_dps_derives_true_quarters(self):
        # JPM shape, one calendar year: quarterly DPS 1.40/1.40/1.50/1.50
        # tagged as direct Q facts AND YTD cumulatives (1.40/2.80/4.30/5.80).
        # End-date dedup used to keep an arbitrary duration per end and the
        # "TTM" summed cumulatives (14.30) instead of quarters (5.80).
        y = date.today().year - 1
        q = [f"{y}-03-31", f"{y}-06-30", f"{y}-09-30", f"{y}-12-31"]
        entries = [
            _entry(q[0], 1.40, f"{y}-01-01", fp="Q1"),
            _entry(q[1], 1.40, f"{y}-04-01", fp="Q2"),
            _entry(q[1], 2.80, f"{y}-01-01", fp="Q2"),
            _entry(q[2], 1.50, f"{y}-07-01", fp="Q3"),
            _entry(q[2], 4.30, f"{y}-01-01", fp="Q3"),
            _entry(q[3], 1.50, f"{y}-10-01", fp="FY"),
            _entry(q[3], 5.80, f"{y}-01-01", fp="FY", form="10-K"),
        ]
        out = _derive_quarterly_from_ytd(entries)
        self.assertEqual([e["val_quarterly"] for e in out],
                         [1.40, 1.40, 1.50, 1.50])
        self.assertAlmostEqual(sum(e["val_quarterly"] for e in out), 5.80)

    def test_ytd_only_series_derives_by_differencing(self):
        # Cash-flow shape: only cumulatives tagged (Q1 3.0, H1 7.0, 9M 12.0,
        # FY 18.0) → quarters 3, 4, 5, 6 by hand.
        y = date.today().year - 1
        entries = [
            _entry(f"{y}-03-31", 3.0e9, f"{y}-01-01", fp="Q1"),
            _entry(f"{y}-06-30", 7.0e9, f"{y}-01-01", fp="Q2"),
            _entry(f"{y}-09-30", 12.0e9, f"{y}-01-01", fp="Q3"),
            _entry(f"{y}-12-31", 18.0e9, f"{y}-01-01", fp="FY", form="10-K"),
        ]
        out = _derive_quarterly_from_ytd(entries)
        self.assertEqual([e["val_quarterly"] for e in out],
                         [3.0e9, 4.0e9, 5.0e9, 6.0e9])

    def test_missing_prior_ytd_yields_none_not_mixed_subtraction(self):
        # Q3 cumulative present but no H1 fact → Q3 quarter is unknowable.
        y = date.today().year - 1
        entries = [
            _entry(f"{y}-03-31", 3.0e9, f"{y}-01-01", fp="Q1"),
            _entry(f"{y}-09-30", 12.0e9, f"{y}-01-01", fp="Q3"),
        ]
        out = _derive_quarterly_from_ytd(entries)
        q3 = [e for e in out if e["quarter"] == 3][0]
        self.assertIsNone(q3["val_quarterly"])


if __name__ == "__main__":
    unittest.main()
