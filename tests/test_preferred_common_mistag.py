"""
Common stock tagged as preferred (2026-09-30).

PLBC (Plumas Bancorp, CIK 1168455), 10-Q 0001437749-18-008338 (filed
2018-05-02), hand-verified against its R2.htm / R3.htm:

  R2 (balance sheet, $K): no preferred line. The equity section is
      "Common stock, no par value; 22,500,000 shares authorized; issued and
       outstanding – 5,082,676 shares at March 31, 2018 and 5,064,972 at
       December 31, 2017"   6,544 | 6,415   ← tagged us-gaap:PreferredStockValue
      Retained earnings 53,135 | 49,855; AOCI (2,388) | (570)
      Total shareholders' equity 57,291 | 55,700
  R3 (parentheticals): 5,082,676 / 5,064,972 tagged
      PreferredStockSharesOutstanding (and CommonStockSharesIssued).

The par-only cut ($6,544K / 5,082,676 = $1.29 > $0.10) and the zero-count
rule (a count exists) both let it through, so the whole common-stock line was
subtracted from equity as "preferred":
  Snapshot/FH as_of 2017-12-31..2018-09-30: (6,415,000 | 6,544,000, True).
  Trends BVPS 2018-03-31: (57,291,000 − 6,544,000) / 5,082,676
                          = 50,747,000 / 5,082,676 = 9.98430748 (served)
                    true:   57,291,000 / 5,082,676 = 11.27181823

Fix: a preferred share count EQUAL to a same-date common share count is the
common line tagged as preferred — not preferred evidence, and no ladder value
at that date is accepted. Universe survey (359 CIKs, full history): exact
equality hits only PLBC 2017-12-31/2018-03-31 (plus pre-2016 CCBG/FCAP counts
with no value). Near-matches are real preferred or scale noise (TFC's
$6.67B preferred vs $6.63–6.69B common stock 2021–24; FBP 2010 22,004,000
preferred vs 21,963,522 common issued; ASB's ×1000 count), so the rule is
exact.

Run: python -m unittest tests.test_preferred_common_mistag
"""
from __future__ import annotations

import sys
import unittest
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
warnings.filterwarnings("ignore")

from tests import _streamlit_stub  # noqa: E402

_st = _streamlit_stub.install()

import pandas as pd  # noqa: E402

import data.sec_client as sc  # noqa: E402
import data.sec_per_share as sps  # noqa: E402

Q1_18 = "0001437749-18-008338"   # the mis-tagging 10-Q
K_17 = "0001437749-18-004387"
K_18 = "0001437749-19-004293"


def _e(end, val, accn, form, filed):
    return {"end": end, "val": val, "accn": accn, "form": form, "filed": filed}


def _q1(end, val):
    return _e(end, val, Q1_18, "10-Q", "2018-05-02")


# Real PLBC companyfacts entries (CIK 1168455).
PLBC = {"cik": 1168455, "entityName": "Plumas Bancorp", "facts": {"us-gaap": {
    "StockholdersEquity": {"units": {"USD": [
        _e("2017-12-31", 55700000, K_17, "10-K", "2018-03-12"),
        _q1("2018-03-31", 57291000)]}},
    "PreferredStockValue": {"units": {"USD": [
        _q1("2017-12-31", 6415000), _q1("2018-03-31", 6544000)]}},
    "PreferredStockSharesOutstanding": {"units": {"shares": [
        _e("2017-12-31", 0, K_17, "10-K", "2018-03-12"),
        _q1("2017-12-31", 5064972),
        _e("2017-12-31", 0, K_18, "10-K", "2019-03-07"),
        _q1("2018-03-31", 5082676),
        _e("2018-12-31", 0, K_18, "10-K", "2019-03-07")]}},
    "CommonStockSharesIssued": {"units": {"shares": [
        _e("2017-12-31", 5064972, K_17, "10-K", "2018-03-12"),
        _q1("2017-12-31", 5064972),
        _q1("2018-03-31", 5082676)]}},
    "CommonStockSharesOutstanding": {"units": {"shares": [
        _e("2017-12-31", 5064972, K_17, "10-K", "2018-03-12")]}},
}}}


class TestCommonAsPreferredDetection(unittest.TestCase):
    def test_equal_counts_flag_the_date(self):
        self.assertTrue(sc._common_as_preferred_at(PLBC, "2017-12-31"))
        self.assertTrue(sc._common_as_preferred_at(PLBC, "2018-03-31"))
        self.assertFalse(sc._common_as_preferred_at(PLBC, "2018-12-31"))  # 0 only

    def test_near_equal_real_preferred_not_flagged(self):
        # FBP (CIK 1057706) 2010-12-31: preferred count 22,004,000 vs
        # 21,963,522 common issued — 0.2% apart, not the same number.
        facts = {"facts": {"us-gaap": {
            "PreferredStockSharesOutstanding": {"units": {"shares": [
                _e("2010-12-31", 22004000, "0001193125-13-136825", "10-K", "2013-04-01")]}},
            "CommonStockSharesIssued": {"units": {"shares": [
                _e("2010-12-31", 21963522, "0001193125-12-112382", "10-K", "2012-03-13")]}},
        }}}
        self.assertFalse(sc._common_as_preferred_at(facts, "2010-12-31"))


class TestPlbcResolver(unittest.TestCase):
    def test_every_affected_date_resolves_no_preferred(self):
        # Old: (6415000, True) at Dec-17, (6544000, True) Mar..Sep-18 — the
        # 6,415/6,544 ($K) common-stock line subtracted as preferred.
        for as_of in ("2017-12-31", "2018-03-31", "2018-06-30", "2018-09-30"):
            with self.subTest(as_of=as_of):
                self.assertEqual(sc._resolve_preferred_stock(PLBC, as_of=as_of),
                                 (0.0, False))

    def test_real_preferred_with_distinct_count_still_resolves(self):
        # Guard is exact-equality only: a genuine count keeps its value.
        facts = {"facts": {"us-gaap": {
            "PreferredStockValue": {"units": {"USD": [
                _e("2018-03-31", 11992000, "x", "10-Q", "2018-05-01")]}},
            "PreferredStockSharesOutstanding": {"units": {"shares": [
                _e("2018-03-31", 12500, "x", "10-Q", "2018-05-01")]}},
            "CommonStockSharesOutstanding": {"units": {"shares": [
                _e("2018-03-31", 8700000, "x", "10-Q", "2018-05-01")]}},
        }}}
        self.assertEqual(sc._resolve_preferred_stock(facts, as_of="2018-03-31"),
                         (11_992_000, True))


class TestPlbcTrends(unittest.TestCase):
    """sec_per_share mirrors the guard per quarter-end."""
    Q4 = pd.Timestamp("2017-12-31")
    Q1 = pd.Timestamp("2018-03-31")

    def _per(self):
        # _series dedups each concept to the latest-filed value per end —
        # the Dec-17 preferred count is the 2019 10-K's 0.
        data = {
            "StockholdersEquity": [(self.Q4, 55_700_000.0), (self.Q1, 57_291_000.0)],
            "CommonStockSharesOutstanding": [(self.Q4, 5_064_972.0)],
            "CommonStockSharesIssued": [(self.Q4, 5_064_972.0), (self.Q1, 5_082_676.0)],
            "PreferredStockValue": [(self.Q4, 6_415_000.0), (self.Q1, 6_544_000.0)],
            "PreferredStockSharesOutstanding": [(self.Q4, 0.0), (self.Q1, 5_082_676.0)],
        }
        # Both read seams (per-concept frames + raw facts for equity).
        from tests.test_sec_per_share import _mock_hist
        with _mock_hist(data):
            return sps._bank_per_share(1168455, [self.Q4, self.Q1])

    def test_q1_18_common_line_not_subtracted(self):
        per = self._per()
        # Old: 50,747,000 / 5,082,676 = 9.98430748.
        self.assertAlmostEqual(per[self.Q1]["bvps_hist"], 11.2718182312, places=8)
        # No intangibles on the R2 face → TBVPS == BVPS in this fragment.
        self.assertAlmostEqual(per[self.Q1]["tbvps_hist"], 11.2718182312, places=8)

    def test_q4_17_zero_count_already_held(self):
        # 55,700,000 / 5,064,972 = 10.99709929
        self.assertAlmostEqual(self._per()[self.Q4]["bvps_hist"],
                               10.9970992929, places=8)


if __name__ == "__main__":
    unittest.main()
