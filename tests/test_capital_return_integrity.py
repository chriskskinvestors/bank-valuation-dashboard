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
D7 — equity read the first fresh concept (plain StockholdersEquity, 2-year
     tolerance) and forward-filled it: tag-switchers carried their last
     plain-SE balance into later quarters (TMP 713,444,000 @ 2024-12-31 in
     every 2025-26 row) and RBB's NCI-inclusive total read as parent equity
     (2026-09-30; real fragments from tests.test_parent_equity_resolution).

All expectations hand-computed. Dates are generated relative to today so the
2-year freshness cutoff in _extract_series never silently stales the fixtures.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from unittest.mock import patch
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

import analysis.capital_return as cr  # noqa: E402
import tests.test_parent_equity_resolution as _pe  # noqa: E402
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


# ── P2-9 (numbers review 2026-09-24) ─────────────────────────────────────
# JPM's "Dividend Payout" rendered "—" with "Dividend data not available in
# SEC filings". JPM tags its cash-flow "Dividends paid" (common + preferred,
# one line) as us-gaap:PaymentsOfDividends and buybacks as
# PaymentsForRepurchaseOfCommonStock — but sec_client.SLIM_USGAAP_CONCEPTS
# drops every dividend-$ / buyback-$ concept, so fetch_company_facts never
# serves them. Values below are JPM's own filings, hand-read 2026-09-30:
#   10-Q 2026-06-30 (acc 0001628280-26-054343) cash flow, $M:
#     Dividends paid (8,716) 6M'26 / (8,028) 6M'25
#     Treasury stock repurchased (15,113) 6M'26 / (15,034) 6M'25
#   10-K 2025 (acc 0001628280-26-008131): Dividends paid (16,625),
#     Treasury stock repurchased (31,591)
#   10-Q Q1'26 / Q1'25 / Q3'25 companyfacts YTD: dividends 4,374 / 3,823 /
#     12,208; buybacks 8,325 / 7,528 / 23,327.
# TTM dividends paid = 16,625 − 8,028 + 8,716 = 17,313 $M;
# TTM buybacks       = 31,591 − 15,034 + 15,113 = 31,670 $M.
# Years are shifted to the latest completed June so the 2-year freshness
# guard in _extract_series never stales the fixture; the values are JPM's.

def _jpm_blob():
    t = date.today()
    y1 = t.year if t >= date(t.year, 7, 1) else t.year - 1   # "2026"
    y0 = y1 - 1                                              # "2025"
    M = 1e6

    def ytd(end, v, fp, form="10-Q"):
        return {"start": f"{end[:4]}-01-01", "end": end, "val": v * M,
                "form": form, "filed": f"{y1}-08-06", "fp": fp, "fy": None}

    def flows(vals):
        q1a, h1a, m9a, fya, q1b, h1b = vals
        return [ytd(f"{y0}-03-31", q1a, "Q1"), ytd(f"{y0}-06-30", h1a, "Q2"),
                ytd(f"{y0}-09-30", m9a, "Q3"),
                ytd(f"{y0}-12-31", fya, "FY", form="10-K"),
                ytd(f"{y1}-03-31", q1b, "Q1"), ytd(f"{y1}-06-30", h1b, "Q2")]

    def qtr(end, start, v):
        return {"start": start, "end": end, "val": v * M, "form": "10-Q",
                "filed": f"{y1}-08-06", "fp": None, "fy": None}

    # Net income: synthetic (not JPM) 16,000 $M every quarter — only the
    # payout arithmetic depends on it.
    ni = [qtr(f"{y0}-09-30", f"{y0}-07-01", 16000),
          qtr(f"{y0}-12-31", f"{y0}-10-01", 16000),
          qtr(f"{y1}-03-31", f"{y1}-01-01", 16000),
          qtr(f"{y1}-06-30", f"{y1}-04-01", 16000)]
    return {"facts": {"us-gaap": {
        "PaymentsOfDividends": {"units": {"USD": flows(
            (3823, 8028, 12208, 16625, 4374, 8716))}},
        "PaymentsForRepurchaseOfCommonStock": {"units": {"USD": flows(
            (7528, 15034, 23327, 31591, 8325, 15113))}},
        "NetIncomeLoss": {"units": {"USD": ni}},
    }}}


class TestP29DividendsFromCashFlow(unittest.TestCase):
    """With the cash-flow concepts served, the pipeline yields JPM's filed
    TTM figures — labeled as TOTAL dividends (JPM's line includes preferred),
    never passed off as common."""

    def _summarize(self, blob):
        from unittest import mock
        import analysis.capital_return as cr
        with mock.patch.object(cr, "fetch_company_facts", lambda cik: blob):
            return cr.summarize_capital_return(19617, market_cap=None)

    def test_jpm_ttm_dividends_and_buybacks_match_filings(self):
        res = self._summarize(_jpm_blob())
        self.assertEqual(res["dividend_source"], "total (includes preferred)")
        ttm = res["ttm"]
        self.assertEqual(ttm["dividends_ttm"], 17_313e6)
        self.assertEqual(ttm["buybacks_ttm"], 31_670e6)
        # Quarters by YTD differencing: Q3 12,208−8,028; Q4 16,625−12,208;
        # Q1 4,374; Q2 8,716−4,374.
        tl = res["timeline"].tail(4)
        self.assertEqual(list(tl["dividends_q"]),
                         [4_180e6, 4_417e6, 4_374e6, 4_342e6])
        # Payout = 17,313 / (4 × 16,000) against the synthetic NI.
        self.assertAlmostEqual(ttm["payout_ratio_ttm"], 17_313 / 64_000, places=12)

    def test_slim_projection_serves_dividend_and_buyback_concepts(self):
        # THE P2-9 failure, end to end: the same blob through the slim
        # companyfacts projection (what fetch_company_facts really serves)
        # lost PaymentsOfDividends / PaymentsForRepurchaseOfCommonStock →
        # "Dividend data not available".
        from data.sec_client import _slim_facts
        res = self._summarize(_slim_facts(_jpm_blob()))
        self.assertEqual(res["ttm"]["dividends_ttm"], 17_313e6)
        self.assertEqual(res["ttm"]["buybacks_ttm"], 31_670e6)

    def test_every_capital_return_concept_is_slimmed_in(self):
        # Any concept capital_return reads but the slim projection drops is
        # silently absent in production (P2-9 class).
        import analysis.capital_return as CR
        from data.sec_client import SLIM_USGAAP_CONCEPTS
        read = (CR._DIVIDEND_COMMON_CONCEPTS + CR._DIVIDEND_TOTAL_CONCEPTS
                + CR._DIVIDEND_PREFERRED_CONCEPTS + CR._BUYBACK_CONCEPTS
                + CR._NET_INCOME_CONCEPTS)
        self.assertEqual([c for c in read if c not in SLIM_USGAAP_CONCEPTS], [])

    def test_total_minus_preferred_matches_duration_not_just_end(self):
        # A preferred concept tagged BOTH 3-month and YTD at the same end
        # (equity-statement DividendsPreferredStockCash) was subtracted twice
        # from the one YTD total when matched by end date alone:
        # H1 common = 1,000 − 30 − 60 = 910 → Q2 460 (wrong).
        # By duration: Q1 = 480 − 30 = 450; H1 = 1,000 − 60 = 940;
        # Q2 = 940 − 450 = 490 (hand).
        y = date.today().year - 1
        M = 1e6

        def e(start, end, v):
            return {"start": start, "end": end, "val": v * M, "form": "10-Q",
                    "filed": f"{y}-08-01", "fp": None, "fy": None}
        blob = {"facts": {"us-gaap": {
            "PaymentsOfDividends": {"units": {"USD": [
                e(f"{y}-01-01", f"{y}-03-31", 480),
                e(f"{y}-01-01", f"{y}-06-30", 1000)]}},
            "DividendsPreferredStockCash": {"units": {"USD": [
                e(f"{y}-01-01", f"{y}-03-31", 30),
                e(f"{y}-04-01", f"{y}-06-30", 30),
                e(f"{y}-01-01", f"{y}-06-30", 60)]}},
        }}}
        res = self._summarize(blob)
        self.assertEqual(res["dividend_source"], "total minus preferred")
        tl = res["timeline"]
        self.assertEqual(list(tl["dividends_q"]), [450e6, 490e6])
        self.assertEqual(list(tl["dividends_q_ytd"]), [450e6, 940e6])
class TestParentEquityTimeline(unittest.TestCase):
    """D7. Values hand-verified against R2.htm (see
    tests.test_parent_equity_resolution): RBB Mar 31 2026 total 531,054,000
    less NCI 72,000 = 530,982,000 (10-Q 0001437749-26-015865); TMP Jun 30
    2026 "Total Equity" 959,932 ($K), no NCI (10-Q 0001005817-26-000112)."""

    def _equity(self, facts):
        with patch.object(cr, "fetch_company_facts", return_value=facts):
            tl = cr.build_capital_return_timeline(1)
        return dict(zip(tl["end"], tl["equity"]))

    def test_tmp_stale_plain_se_not_carried_forward(self):
        eq = self._equity(_pe.TMP)
        self.assertEqual(eq["2026-06-30"], 959_932_000)     # was 713,444,000
        self.assertEqual(eq["2026-03-31"], 946_741_000)
        self.assertEqual(eq["2024-12-31"], 713_444_000)

    def test_rbb_nci_removed(self):
        eq = self._equity(_pe.RBB)
        self.assertEqual(eq["2026-03-31"], 531_054_000 - 72_000)
        self.assertEqual(eq["2026-06-30"], 535_177_000 - 72_000)

    def test_unseparable_nci_is_na_not_forward_filled(self):
        # RBB's real Q1-2026 dividend fact (10-Q 0001437749-26-015865) gives
        # 2026-03-31 a timeline row even when equity can't resolve there.
        facts = _pe._drop(_pe.RBB, _pe.MI, "2026-03-31")
        facts["facts"]["us-gaap"]["PaymentsOfDividends"] = {"units": {"USD": [
            _pe._e("2026-03-31", 2758000, "0001437749-26-015865", "10-Q",
                   "2026-05-08", "2026-01-01")]}}
        eq = self._equity(facts)
        self.assertTrue(pd.isna(eq["2026-03-31"]))
        self.assertEqual(eq["2026-06-30"], 535_105_000)


if __name__ == "__main__":
    unittest.main()
