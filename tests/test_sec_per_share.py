"""
(AUDIT-2026-07-02 P1 #5) Trends BVPS/TBVPS must be per-COMMON-share on the
audited snapshot-path conventions — the old code divided TOTAL equity
(preferred included) by the raw cover-page share count, overstating TBV/share
for every preferred issuer (USB: placeholder 1,600,000,000 count + $6.8B
preferred → $33.23 shown vs ~$26.7 real).

Pins (all against a mocked get_historical_fundamentals; no network):
  1. preferred carrying value is subtracted from equity (both TBVPS and BVPS);
  2. preferred present (shares outstanding) but value unresolved → n/a for the
     quarter, never a preferred-inflated figure (cardinal rule);
  3. a whole-100M cover-page count is a placeholder → same-end
     issued − treasury derivation is used instead;
  4. placeholder count + a treasury series that lacks this end → n/a, not a
     treasury-less guess;
  5. no preferred anywhere → numbers unchanged from the plain computation;
  6. annual-only preferred tagging forward-fills into off-quarters (value and
     presence together), like the goodwill convention.
"""
import sys
import types
import unittest
from unittest.mock import patch

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

import pandas as pd  # noqa: E402

import data.sec_per_share as sps  # noqa: E402

Q1 = pd.Timestamp("2026-03-31")
Q4 = pd.Timestamp("2025-12-31")


def _df(rows):
    return pd.DataFrame([{"end": q, "val": v} for q, v in rows])


def _mock_hist(series_by_concept):
    def fake(cik, concept):
        rows = series_by_concept.get(concept)
        return _df(rows) if rows else None
    return patch.object(sps, "get_historical_fundamentals", side_effect=fake)


class TestPreferredSubtracted(unittest.TestCase):
    def test_preferred_carrying_value_removed_from_both(self):
        data = {
            "StockholdersEquity": [(Q1, 1_000.0)],
            "CommonStockSharesOutstanding": [(Q1, 10.0)],
            "Goodwill": [(Q1, 100.0)],
            "PreferredStockValue": [(Q1, 200.0)],
        }
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertAlmostEqual(r["bvps_hist"], (1_000 - 200) / 10)      # 80.0
        self.assertAlmostEqual(r["tbvps_hist"], (1_000 - 200 - 100) / 10)  # 70.0

    def test_no_preferred_unchanged(self):
        data = {
            "StockholdersEquity": [(Q1, 1_000.0)],
            "CommonStockSharesOutstanding": [(Q1, 10.0)],
        }
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertAlmostEqual(r["bvps_hist"], 100.0)
        self.assertAlmostEqual(r["tbvps_hist"], 100.0)

    def test_present_but_unresolved_is_na(self):
        # Preferred shares outstanding but no value tag resolves → n/a, never
        # a preferred-inflated per-share figure.
        data = {
            "StockholdersEquity": [(Q1, 1_000.0)],
            "CommonStockSharesOutstanding": [(Q1, 10.0)],
            "PreferredStockSharesOutstanding": [(Q1, 5_000.0)],
        }
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertIsNone(r["bvps_hist"])
        self.assertIsNone(r["tbvps_hist"])

    def test_annual_only_preferred_forward_fills(self):
        # Preferred tagged only at year-end must not read as preferred-free in
        # the next quarter (value + presence carry forward together).
        data = {
            "StockholdersEquity": [(Q4, 1_000.0), (Q1, 1_000.0)],
            "CommonStockSharesOutstanding": [(Q4, 10.0), (Q1, 10.0)],
            "PreferredStockValue": [(Q4, 200.0)],
        }
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertAlmostEqual(r["bvps_hist"], 80.0)


class TestPlaceholderShareCount(unittest.TestCase):
    def test_round_placeholder_replaced_by_issued_minus_treasury(self):
        # USB shape: cover count an exact 100M multiple, true count derivable.
        data = {
            "StockholdersEquity": [(Q1, 65_786.0)],
            "CommonStockSharesOutstanding": [(Q1, 1_600_000_000.0)],
            "CommonStockSharesIssued": [(Q1, 2_125_725_742.0)],
            "TreasuryStockCommonShares": [(Q1, 571_140_185.0)],
        }
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertAlmostEqual(r["bvps_hist"],
                               65_786.0 / (2_125_725_742 - 571_140_185))

    def test_placeholder_with_missing_same_end_treasury_is_na(self):
        # The filer HAS a treasury series, but not at this end — deriving with
        # treasury=0 would overstate the count, so n/a.
        data = {
            "StockholdersEquity": [(Q1, 1_000.0)],
            "CommonStockSharesOutstanding": [(Q1, 100_000_000.0)],
            "CommonStockSharesIssued": [(Q1, 120_000_000.0)],
            "TreasuryStockCommonShares": [(Q4, 15_000_000.0)],
        }
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertIsNone(r["bvps_hist"])

    def test_no_treasury_series_derives_from_issued_alone(self):
        data = {
            "StockholdersEquity": [(Q1, 1_200.0)],
            "CommonStockSharesOutstanding": [(Q1, 100_000_000.0)],
            "CommonStockSharesIssued": [(Q1, 120_000_000.0)],
        }
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertAlmostEqual(r["bvps_hist"], 1_200.0 / 120_000_000)

    def test_exact_count_kept(self):
        data = {
            "StockholdersEquity": [(Q1, 1_000.0)],
            "CommonStockSharesOutstanding": [(Q1, 99_123_456.0)],
        }
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertAlmostEqual(r["bvps_hist"], 1_000.0 / 99_123_456)


class TestMergedSeriesLadder(unittest.TestCase):
    def test_abandoned_early_tag_does_not_shadow_modern_ends(self):
        # USB shape: PreferredStockValue stops in 2013; the modern tag carries
        # today's value. Per-END merge must resolve both eras.
        old = pd.Timestamp("2013-03-31")
        data = {
            "PreferredStockValue": [(old, 4_769.0)],
            "PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount":
                [(Q1, 6_808.0)],
        }
        with _mock_hist(data):
            merged = sps._merged_series(1, sps._PREFERRED_VALUE_CONCEPTS)
        self.assertEqual(merged[old], 4_769.0)
        self.assertEqual(merged[Q1], 6_808.0)

    def test_par_zero_keeps_looking(self):
        # A par-only tag of exactly 0 is not a resolved value (PNC shape).
        data = {
            "PreferredStockValue": [(Q1, 0.0)],
            "PreferredStockIncludingAdditionalPaidInCapital": [(Q1, 4_000.0)],
        }
        with _mock_hist(data):
            merged = sps._merged_series(1, sps._PREFERRED_VALUE_CONCEPTS)
        self.assertEqual(merged[Q1], 4_000.0)


class TestIntangibleAdjustmentMirrorsMainPath(unittest.TestCase):
    """(AUDIT-2026-07-02 #5 RESIDUAL, closed 2026-07-10) The trends path used
    raw Goodwill + ExcludingGoodwill tags, so BKU-class filers (combined
    IncludingGoodwill tag only) got TBVPS == BVPS, and USB-class MSR-inclusive
    rollups over-deducted. Per-END adjustment now mirrors
    sec_client._resolve_intangible_adjustment with origin-vintage guards."""

    def _tbvps(self, data, q=Q1):
        with _mock_hist(data):
            per = sps._bank_per_share(1, [q])
        return per[q]["tbvps_hist"], per[q]["bvps_hist"]

    def test_bku_class_combined_tag_only(self):
        # equity 1000, shares 100, ONLY the combined tag (200): old behavior
        # deducted nothing (TBVPS == BVPS == 10.0); now TBVPS = 8.0.
        data = {"StockholdersEquity": [(Q1, 1000.0)],
                "CommonStockSharesOutstanding": [(Q1, 101.0)],
                "IntangibleAssetsNetIncludingGoodwill": [(Q1, 200.0)]}
        tbv, bv = self._tbvps(data)
        self.assertAlmostEqual(bv, 1000.0 / 101.0, places=6)
        self.assertAlmostEqual(tbv, 800.0 / 101.0, places=6)
        self.assertNotAlmostEqual(tbv, bv, places=6)

    def test_usb_class_msr_netted_from_rollup(self):
        # Rollup other-intangibles 160 INCLUDES a same-end MSR 60 → deduct
        # goodwill 100 + (160 − 60) = 200, not 260.
        data = {"StockholdersEquity": [(Q1, 1000.0)],
                "CommonStockSharesOutstanding": [(Q1, 101.0)],
                "Goodwill": [(Q1, 100.0)],
                "IntangibleAssetsNetExcludingGoodwill": [(Q1, 160.0)],
                "ServicingAssetAtFairValueAmount": [(Q1, 60.0)]}
        tbv, _ = self._tbvps(data)
        self.assertAlmostEqual(tbv, (1000.0 - 200.0) / 101.0, places=6)

    def test_fitb_class_separate_msr_not_stripped(self):
        # MSR (180) LARGER than the rollup (120) cannot be bundled inside it —
        # no stripping; deduct goodwill 100 + 120.
        data = {"StockholdersEquity": [(Q1, 1000.0)],
                "CommonStockSharesOutstanding": [(Q1, 101.0)],
                "Goodwill": [(Q1, 100.0)],
                "IntangibleAssetsNetExcludingGoodwill": [(Q1, 120.0)],
                "ServicingAssetAtFairValueAmount": [(Q1, 180.0)]}
        tbv, _ = self._tbvps(data)
        self.assertAlmostEqual(tbv, (1000.0 - 220.0) / 101.0, places=6)

    def test_finite_lived_never_msr_stripped(self):
        # FiniteLivedIntangibleAssetsNet (80) never contains MSRs — even a
        # same-end smaller MSR (50) must NOT be netted out of it.
        data = {"StockholdersEquity": [(Q1, 1000.0)],
                "CommonStockSharesOutstanding": [(Q1, 101.0)],
                "Goodwill": [(Q1, 100.0)],
                "FiniteLivedIntangibleAssetsNet": [(Q1, 80.0)],
                "ServicingAssetAtFairValueAmount": [(Q1, 50.0)]}
        tbv, _ = self._tbvps(data)
        self.assertAlmostEqual(tbv, (1000.0 - 180.0) / 101.0, places=6)

    def test_stale_combined_ignored_when_goodwill_fresher(self):
        # Combined tag last seen Q4 (150, pre-acquisition); goodwill re-tagged
        # at Q1 (140 > combined-implied). The stale combined must be ignored:
        # deduct goodwill 140 + explicit rollup 30 = 170 — NOT max() against a
        # pre-acquisition combined that would understate.
        data = {"StockholdersEquity": [(Q1, 1000.0)],
                "CommonStockSharesOutstanding": [(Q1, 101.0)],
                "Goodwill": [(Q4, 90.0), (Q1, 140.0)],
                "IntangibleAssetsNetExcludingGoodwill": [(Q1, 30.0)],
                "IntangibleAssetsNetIncludingGoodwill": [(Q4, 150.0)]}
        tbv, _ = self._tbvps(data)
        self.assertAlmostEqual(tbv, (1000.0 - 170.0) / 101.0, places=6)


if __name__ == "__main__":
    unittest.main()
