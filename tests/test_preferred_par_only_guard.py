"""
Preferred ladder plausibility guard (2026-09-30).

sec_client._resolve_preferred_stock treated 0 as "keep looking" (PNC) but
accepted any tiny NONZERO par-only value as the carrying value. OCFC (CIK
1004702), hand-verified against its 10-Q balance sheets:

  10-Q 0001004702-25-000081, R2.htm, Mar 31 2025: "Preferred stock, $0.01 par
       value, $1,000 liquidation preference, ... 57,370 shares issued" — 1
       ($K). R3.htm tags "Preferred stock, liquidation preference, value $ 1"
       ($K) — the PER-SHARE $1,000, not a total. The ~$55M carrying value sits
       in APIC: 1,170,179 → 1,115,441 ($K) when the shares were redeemed in Q2.
       Served before the fix: preferred = $1,000, so "common" book included
       ~$55M of preferred: BVPS (1,708,322,000 − 1,000) / 58,383,525 = 29.2603
       (vs ~28.28 with the $57.37M liquidation amount out).
  10-Q 0001004702-25-000108, R2.htm, Jun 30 2025: preferred 0, 0 shares
       issued; "OceanFirst Financial Corp. stockholders' equity" 1,642,846
       ($K); 57,383,975 common shares outstanding; goodwill 523,308, other
       intangibles 10,834. PreferredStockLiquidationPreferenceValue still
       $1,000 (per share) → preferred_present stayed True, value $1,000.
       BVPS  = 1,642,846,000 / 57,383,975                    = 28.6290031320
       TBVPS = (1,642,846,000 − 523,308,000 − 10,834,000)
               / 57,383,975 = 1,108,704,000 / 57,383,975     = 19.3207946992

Fix: a ladder value under $1 per same-date share is par only (keep looking →
unresolved → n/a); a liquidation preference with zero shares outstanding, or
≤ $100K over >1 share, is a per-share figure. A fresh explicit zero share
count outranks dividend evidence from a period ending no later than it (the
dividends were paid before the redemption).

Run: python -m unittest tests.test_preferred_par_only_guard
"""
from __future__ import annotations

import sys
import unittest
import warnings
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
warnings.filterwarnings("ignore")

from tests import _streamlit_stub  # noqa: E402

_st = _streamlit_stub.install()
if not hasattr(_st, "query_params"):
    _st.query_params = {}
for _name in ("markdown", "caption", "write", "info", "warning", "error",
              "divider", "metric", "dataframe", "button", "html", "subheader"):
    if not hasattr(_st, _name):
        setattr(_st, _name, lambda *a, **k: None)

import pandas as pd  # noqa: E402

import data.sec_client as sc  # noqa: E402
import data.sec_per_share as sps  # noqa: E402
import ui.financial_highlights as fh  # noqa: E402

Q1_25 = "0001004702-25-000081"
Q2_25 = "0001004702-25-000108"
K_25 = "0001004702-26-000015"
Q2_26 = "0001004702-26-000120"


def _e(end, val, accn, form, filed, start=None):
    d = {"end": end, "val": val, "accn": accn, "form": form, "filed": filed}
    if start:
        d["start"] = start
    return d


def _q1(end, val):
    return _e(end, val, Q1_25, "10-Q", "2025-05-02")


def _q2(end, val, start=None):
    return _e(end, val, Q2_25, "10-Q", "2025-08-04", start)


# Real OCFC companyfacts entries (CIK 1004702).
OCFC = {"cik": 1004702, "entityName": "OceanFirst Financial Corp.", "facts": {
    "us-gaap": {
        "StockholdersEquity": {"units": {"USD": [
            _q1("2025-03-31", 1708322000), _q2("2025-06-30", 1642846000)]}},
        "Goodwill": {"units": {"USD": [
            _q1("2025-03-31", 523308000), _q2("2025-06-30", 523308000)]}},
        "IntangibleAssetsNetExcludingGoodwill": {"units": {"USD": [
            _q1("2025-03-31", 11740000), _q2("2025-06-30", 10834000)]}},
        "PreferredStockValue": {"units": {"USD": [
            _q1("2025-03-31", 1000), _q2("2025-06-30", 0),
            _e("2026-06-30", 0, Q2_26, "10-Q", "2026-08-07")]}},
        "PreferredStockLiquidationPreferenceValue": {"units": {"USD": [
            _q1("2025-03-31", 1000), _q2("2025-06-30", 1000),
            _e("2026-06-30", 1000, Q2_26, "10-Q", "2026-08-07")]}},
        "DividendsPreferredStock": {"units": {"USD": [
            _q1("2025-03-31", 1004000) | {"start": "2025-01-01"},
            _q2("2025-06-30", 2008000, "2025-01-01"),
            _q2("2025-06-30", 1004000, "2025-04-01"),
            _e("2025-12-31", 2008000, K_25, "10-K", "2026-02-27", "2025-01-01")]}},
        "PreferredStockSharesIssued": {"units": {"shares": [
            _q1("2025-03-31", 57370), _q2("2025-06-30", 0),
            _e("2026-06-30", 0, Q2_26, "10-Q", "2026-08-07")]}},
        "CommonStockSharesOutstanding": {"units": {"shares": [
            _q1("2025-03-31", 58383525), _q2("2025-06-30", 57383975)]}},
    },
    "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": []}}},
}}


class TestCarryingTotalPlausibility(unittest.TestCase):
    def test_par_only_value_is_not_a_total(self):
        # OCFC: $1,000 over 57,370 shares = $0.017/share.
        self.assertTrue(sc._not_a_carrying_total("PreferredStockValue", 1_000, 57_370))

    def test_real_carrying_values_pass(self):
        # FBIZ 10-Q Jun 30 2026: $11,992K over 12,500 shares = $959.36/share.
        self.assertFalse(sc._not_a_carrying_total("PreferredStockValue", 11_992_000, 12_500))
        self.assertFalse(sc._not_a_carrying_total(
            "PreferredStockLiquidationPreferenceValue", 57_370_000, 57_370))

    def test_liquidation_per_share_misread_as_total(self):
        liq = "PreferredStockLiquidationPreferenceValue"
        self.assertTrue(sc._not_a_carrying_total(liq, 1_000, 0))        # OCFC post-redemption
        self.assertTrue(sc._not_a_carrying_total(liq, 1_000, 57_370))   # OCFC pre-redemption
        self.assertTrue(sc._not_a_carrying_total(liq, 100_000, 500))    # $100K/share × 500
        # OCFC 10-Q 0001004702-20-000141 (Jun 30 2020): per-share $1,000
        # tagged ×1000 as $1,000,000 over 57,370 shares = $17.43/share.
        self.assertTrue(sc._not_a_carrying_total(liq, 1_000_000, 57_370))
        # BUSE Jun 30 2026: $222,750K over 222,750 shares = $1,000 — a total.
        self.assertFalse(sc._not_a_carrying_total(liq, 222_750_000, 222_750))

    def test_zero_same_date_count_rejects_any_rung(self):
        # BSRR (CIK 1130144) 10-K 0001558370-23-003238 R2: "Serial Preferred
        # stock ... none issued"; the 112,928 ($K) it tagged
        # PreferredStockValue is its COMMON stock line (0 preferred shares
        # tagged at the same date). Old: all of common stock subtracted.
        self.assertTrue(sc._not_a_carrying_total("PreferredStockValue", 112_928_000, 0))

    def test_no_same_date_count_is_accepted(self):
        self.assertFalse(sc._not_a_carrying_total("PreferredStockValue", 1_000, None))

    def test_x1000_mis_scaled_count_keeps_real_value(self):
        # SBFG 2018-12-31: $13,979K over a count tagged 14,995,000 (real
        # 14,995) = $0.93/share — a real carrying value, not par.
        self.assertFalse(sc._not_a_carrying_total("PreferredStockValue",
                                                  13_979_000, 14_995_000))


class TestMisScaledShareCount(unittest.TestCase):
    def test_same_date_count_selection(self):
        pick = sc._same_date_preferred_count
        self.assertEqual(pick([205_327, 205_327_000]), 205_327)   # FMBM ×1000 duplicate
        self.assertEqual(pick([0, 20_000_000]), 20_000_000)       # NEWT: 0 out / issued
        self.assertEqual(pick([0, 0]), 0)
        self.assertIsNone(pick([]))

    def test_asb_smaller_same_date_count_wins(self):
        # ASB (CIK 7789) 10-K 0000007789-18-000013 R2: "Preferred equity
        # 159,929" ($K), 165,000 shares issued; the FY2018 10-K's comparative
        # re-tags the same date as 165,000,000. Old per-share test against the
        # larger count would read $0.97/share; the smaller gives $969.27.
        facts = {"facts": {"us-gaap": {
            "PreferredStockIncludingAdditionalPaidInCapital": {"units": {"USD": [
                _e("2017-12-31", 159929000, "0000007789-18-000013", "10-K", "2018-02-06")]}},
            "PreferredStockSharesIssued": {"units": {"shares": [
                _e("2017-12-31", 165000, "0000007789-18-000013", "10-K", "2018-02-06"),
                _e("2017-12-31", 165000000, "0000007789-19-000014", "10-K", "2019-02-19")]}},
        }}}
        self.assertEqual(sc._preferred_shares_at(facts, "2017-12-31"), 165_000)
        self.assertEqual(sc._resolve_preferred_stock(facts, as_of="2017-12-31"),
                         (159_929_000, True))


class TestOcfcResolver(unittest.TestCase):
    def test_par_only_while_outstanding_is_unresolved(self):
        # Old: (1000, True) — ~$55M of preferred left in "common" equity.
        self.assertEqual(sc._resolve_preferred_stock(OCFC, as_of="2025-03-31"),
                         (None, True))

    def test_redemption_quarter_resolves_to_none_outstanding(self):
        # Old: (1000, True) from the per-share liquidation preference. The
        # H1 dividends end ON the zero-share date → no longer evidence.
        self.assertEqual(sc._resolve_preferred_stock(OCFC, as_of="2025-06-30"),
                         (0.0, False))

    def test_fy_dividends_older_than_zero_count_not_evidence(self):
        # FY2025 dividends (end 2025-12-31) vs 0 shares at 2026-06-30.
        self.assertEqual(sc._resolve_preferred_stock(OCFC, as_of="2026-06-30"),
                         (0.0, False))

    def test_newer_zero_count_supersedes_older_value(self):
        # SFNC (CIK 90498): 10-Q 0001628280-23-037027 R92 tags a class-of-
        # stock "liquidation preference, value $80.0M" at Sep 30 2023; its
        # balance sheet carries no preferred and 0 shares are tagged from
        # Dec 31 2023. Old: (80,000,000, True) through Q3-2024.
        facts = {"facts": {"us-gaap": {
            "PreferredStockLiquidationPreferenceValue": {"units": {"USD": [
                _e("2023-09-30", 80000000, "0001628280-23-037027", "10-Q", "2023-11-06")]}},
            "PreferredStockSharesOutstanding": {"units": {"shares": [
                _e("2023-12-31", 0, "0001628280-24-007263", "10-K", "2024-02-27")]}},
        }}}
        self.assertEqual(sc._resolve_preferred_stock(facts, as_of="2024-03-31"),
                         (0.0, False))

    def test_dividends_newer_than_zero_count_still_evidence(self):
        # A zero count must not mask preferred issued AFTER it: dividends for
        # a later period with no newer share tag keep the cardinal-rule n/a.
        facts = {"facts": {"us-gaap": {
            "PreferredStockSharesIssued": {"units": {"shares": [
                _q2("2025-06-30", 0)]}},
            "DividendsPreferredStock": {"units": {"USD": [
                _e("2025-09-30", 500000, "x", "10-Q", "2025-11-04", "2025-07-01")]}},
        }}}
        self.assertEqual(sc._resolve_preferred_stock(facts, as_of="2025-09-30"),
                         (None, True))


def _fh_row(end: datetime) -> dict:
    with patch.object(fh.sec_client, "fetch_company_facts", return_value=OCFC):
        return fh._per_share_for_ends("0001004702", [end], quarterly=True)[end]


class TestOcfcFinancialHighlights(unittest.TestCase):
    def test_q1_25_is_na_not_preferred_inflated(self):
        r = _fh_row(datetime(2025, 3, 31))
        self.assertTrue(r["_pfd_present"])
        self.assertIsNone(r["bvps"])          # old: 29.2603 (preferred inside)
        self.assertIsNone(r["tbvps"])

    def test_q2_25_post_redemption_hand_computed(self):
        r = _fh_row(datetime(2025, 6, 30))
        self.assertFalse(r["_pfd_present"])
        self.assertEqual(r["_ce"], 1_642_846_000)
        self.assertAlmostEqual(r["bvps"], 28.6290031320, places=8)
        self.assertAlmostEqual(r["tbvps"], 19.3207946992, places=8)


class TestOcfcTrends(unittest.TestCase):
    """sec_per_share mirrors the guard per quarter-end."""
    Q1 = pd.Timestamp("2025-03-31")
    Q2 = pd.Timestamp("2025-06-30")

    def _per(self, overrides=None):
        data = {
            "StockholdersEquity": [(self.Q1, 1_708_322_000.0), (self.Q2, 1_642_846_000.0)],
            "CommonStockSharesOutstanding": [(self.Q1, 58_383_525.0), (self.Q2, 57_383_975.0)],
            "Goodwill": [(self.Q1, 523_308_000.0), (self.Q2, 523_308_000.0)],
            "IntangibleAssetsNetExcludingGoodwill": [(self.Q1, 11_740_000.0),
                                                     (self.Q2, 10_834_000.0)],
            "PreferredStockValue": [(self.Q1, 1_000.0), (self.Q2, 0.0)],
            "PreferredStockLiquidationPreferenceValue": [(self.Q1, 1_000.0),
                                                         (self.Q2, 1_000.0)],
            "PreferredStockSharesIssued": [(self.Q1, 57_370.0), (self.Q2, 0.0)],
        }
        data.update(overrides or {})
        # Both read seams (per-concept frames + raw facts for equity).
        from tests.test_sec_per_share import _mock_hist
        with _mock_hist(data):
            return sps._bank_per_share(1, [self.Q1, self.Q2])

    def test_par_only_quarter_na_redemption_quarter_resolves(self):
        per = self._per()
        self.assertIsNone(per[self.Q1]["bvps_hist"])
        self.assertIsNone(per[self.Q1]["tbvps_hist"])
        self.assertAlmostEqual(per[self.Q2]["bvps_hist"], 28.6290031320, places=8)
        self.assertAlmostEqual(per[self.Q2]["tbvps_hist"], 19.3207946992, places=8)

    def test_zero_count_stops_a_real_value_carrying_forward(self):
        # A real $57.37M carrying value at Q1 must not forward-fill into the
        # redemption quarter (old: subtracted at Q2 for up to a year).
        per = self._per({"PreferredStockValue": [(self.Q1, 57_370_000.0), (self.Q2, 0.0)],
                         "PreferredStockLiquidationPreferenceValue": []})
        self.assertAlmostEqual(per[self.Q1]["bvps_hist"],
                               (1_708_322_000 - 57_370_000) / 58_383_525, places=8)
        self.assertAlmostEqual(per[self.Q2]["bvps_hist"], 28.6290031320, places=8)


if __name__ == "__main__":
    unittest.main()
