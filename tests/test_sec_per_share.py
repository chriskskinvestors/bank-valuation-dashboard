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
     presence together), like the goodwill convention;
  7. equity falls back to the including-NCI concept (AUBN/PRK, 2026-07-22);
  8. preferred presence/value carry at most ~1y past origin — an abandoned
     tag is not presence (CTBI/WABC, 2026-07-22) — with the PNC par-zero
     guard intact;
  9. equity is PARENT equity per end via sec_client._parent_equity_at — NCI
     removed (RBB), unseparable NCI n/a (2026-09-30).
"""
import unittest
from contextlib import ExitStack
from unittest.mock import patch

# Order-independent streamlit stub (shared helper) before importing data
# modules that decorate with st.cache_data.
from tests import _streamlit_stub

_streamlit_stub.install()

import pandas as pd  # noqa: E402

import data.sec_per_share as sps  # noqa: E402
import tests.test_parent_equity_resolution as _pe  # noqa: E402

Q1 = pd.Timestamp("2026-03-31")
Q4 = pd.Timestamp("2025-12-31")


def _df(rows):
    return pd.DataFrame([{"end": q, "val": v} for q, v in rows])


def _mock_hist(series_by_concept, facts=None):
    """Stub both read seams: get_historical_fundamentals (per-concept frames)
    and fetch_company_facts (equity is resolved from raw facts by
    sec_client._parent_equity_at). Without `facts`, the equity blob is built
    from the same {concept: [(end, val)]} rows."""
    def fake(cik, concept):
        rows = series_by_concept.get(concept)
        return _df(rows) if rows else None
    if facts is None:
        facts = {"facts": {"us-gaap": {
            c: {"units": {"USD": [
                {"end": q.date().isoformat(), "val": v, "form": "10-Q",
                 "filed": "2026-05-01", "accn": "x"} for q, v in rows]}}
            for c, rows in series_by_concept.items()}}}
    stack = ExitStack()
    stack.enter_context(patch.object(sps, "get_historical_fundamentals",
                                     side_effect=fake))
    stack.enter_context(patch.object(sps, "fetch_company_facts",
                                     return_value=facts))
    return stack


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


class TestEquityConceptLadder(unittest.TestCase):
    """AUBN/PRK (2026-07-22): filers that tag ONLY
    StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest had
    their entire per-share history n/a — the audited snapshot path already
    falls back to the including-NCI tag; the historical path must too."""

    def test_including_nci_only_filer_resolves(self):
        data = {
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest":
                [(Q1, 1_000.0)],
            "CommonStockSharesOutstanding": [(Q1, 10.0)],
            "Goodwill": [(Q1, 100.0)],
        }
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertAlmostEqual(r["bvps_hist"], 100.0)
        self.assertAlmostEqual(r["tbvps_hist"], 90.0)

    def test_primary_wins_when_both_tagged(self):
        # An NCI filer tags both; the narrower common-equity concept must win.
        data = {
            "StockholdersEquity": [(Q1, 900.0)],
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest":
                [(Q1, 1_000.0)],
            "CommonStockSharesOutstanding": [(Q1, 10.0)],
        }
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertAlmostEqual(r["bvps_hist"], 90.0)


class TestStalePreferredPresence(unittest.TestCase):
    """CTBI/WABC (2026-07-22): a nonzero PreferredStockShares(Issued|
    Outstanding) tag abandoned years ago (CTBI 2014, WABC 2009) forward-filled
    presence forever while the carrying value never resolved — every modern
    quarter n/a. Presence and value now carry at most ~1y past their origin,
    mirroring _resolve_preferred_stock's max_age_years=1."""

    def test_stale_shares_tag_does_not_poison_modern_quarters(self):
        # CTBI shape: 2014 Issued tag + explicit modern PreferredStockValue=0.
        old = pd.Timestamp("2014-12-31")
        data = {
            "StockholdersEquity": [(Q1, 1_000.0)],
            "CommonStockSharesOutstanding": [(Q1, 10.0)],
            "PreferredStockSharesIssued": [(old, 300.0)],
            "PreferredStockValue": [(Q1, 0.0)],
        }
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertAlmostEqual(r["bvps_hist"], 100.0)

    def test_stale_shares_tag_ages_out_without_explicit_zero(self):
        # WABC shape: nothing preferred-tagged since 2009, no zero tag either.
        old = pd.Timestamp("2009-12-31")
        data = {
            "StockholdersEquity": [(Q1, 1_000.0)],
            "CommonStockSharesOutstanding": [(Q1, 10.0)],
            "PreferredStockSharesOutstanding": [(old, 300.0)],
        }
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertAlmostEqual(r["bvps_hist"], 100.0)

    def test_current_shares_with_par_zero_value_still_na(self):
        # PNC guard unchanged: FRESH nonzero shares + par-zero value → n/a.
        data = {
            "StockholdersEquity": [(Q1, 1_000.0)],
            "CommonStockSharesOutstanding": [(Q1, 10.0)],
            "PreferredStockSharesOutstanding": [(Q1, 5_000.0)],
            "PreferredStockValue": [(Q1, 0.0)],
        }
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertIsNone(r["bvps_hist"])
        self.assertIsNone(r["tbvps_hist"])

    def test_stale_value_with_fresh_shares_is_na(self):
        # Value tag abandoned >1y while shares are still tagged: presence is
        # real, value unresolved → n/a, never a stale-value subtraction.
        old = pd.Timestamp("2020-12-31")
        data = {
            "StockholdersEquity": [(Q1, 1_000.0)],
            "CommonStockSharesOutstanding": [(Q1, 10.0)],
            "PreferredStockSharesOutstanding": [(Q1, 5_000.0)],
            "PreferredStockValue": [(old, 200.0)],
        }
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertIsNone(r["bvps_hist"])

    def test_presence_evaluated_relative_to_each_end(self):
        # The same abandoned tag WAS presence back when it was fresh: the 2014
        # quarter still honors the cardinal rule while 2026 resolves.
        old = pd.Timestamp("2014-12-31")
        data = {
            "StockholdersEquity": [(old, 1_000.0), (Q1, 1_000.0)],
            "CommonStockSharesOutstanding": [(old, 10.0), (Q1, 10.0)],
            "PreferredStockSharesIssued": [(old, 300.0)],
        }
        with _mock_hist(data):
            per = sps._bank_per_share(1, [old, Q1])
        self.assertIsNone(per[old]["bvps_hist"])          # present, unresolved
        self.assertAlmostEqual(per[Q1]["bvps_hist"], 100.0)  # aged out


class TestSharesCorroboration(unittest.TestCase):
    """CCFN 2026-07-21: CommonStockSharesOutstanding tagged 3.54M while the
    filer's own NI/EPS implies 10.6M — BV/TBV history rendered ~3x high
    (TBV above BV). Every share-count candidate must corroborate against
    the period's diluted average when one exists."""

    def test_wrong_point_in_time_count_falls_to_diluted_avg(self):
        data = {"StockholdersEquity": [(Q1, 192_058.0)],
                "CommonStockSharesOutstanding": [(Q1, 3_537.0)],   # ~3x under
                "WeightedAverageNumberOfDilutedSharesOutstanding":
                    [(Q1, 10_640.0)]}
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertAlmostEqual(r["bvps_hist"], 192_058.0 / 10_640.0, places=4)

    def test_corroborated_count_still_preferred(self):
        data = {"StockholdersEquity": [(Q1, 1_000.0)],
                "CommonStockSharesOutstanding": [(Q1, 100.0)],
                "WeightedAverageNumberOfDilutedSharesOutstanding":
                    [(Q1, 102.0)]}                                  # ~1x: fine
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertAlmostEqual(r["bvps_hist"], 10.0)   # point-in-time count

    def test_no_diluted_series_keeps_legacy_behavior(self):
        data = {"StockholdersEquity": [(Q1, 1_000.0)],
                "CommonStockSharesOutstanding": [(Q1, 100.0)]}
        with _mock_hist(data):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertAlmostEqual(r["bvps_hist"], 10.0)


class TestParentEquityPerEnd(unittest.TestCase):
    """Equity per end is PARENT equity, resolved like the snapshot
    (sec_client._parent_equity_at). The old per-end tag merge served RBB's
    NCI-inclusive total as parent equity at every end it lacked plain SE.
    Real companyfacts fragments (tests.test_parent_equity_resolution),
    hand-verified against R2.htm:

      RBB 10-Q 0001437749-26-015865, Mar 31 2026: "Total shareholders'
          equity" 531,054,000 incl. "Non-controlling interest" 72,000 →
          parent 530,982,000; Goodwill 71,498,000; 17,074,159 shares; no
          preferred. BVPS 530,982,000 / 17,074,159 = 31.0986 (old 31.1028);
          TBVPS 459,484,000 / 17,074,159 = 26.9111.
      TMP 10-Q 0001005817-26-000112, Jun 30 2026: "Total Equity" 959,932
          ($K), no NCI line, tagged ONLY as the including-NCI concept;
          14,410,189 issued − 90,521 treasury = 14,319,668 shares →
          BVPS 67.0359.
    """

    RBB_SHARES = {
        "CommonStockSharesOutstanding": [(Q1, 17_074_159.0)],
        "WeightedAverageNumberOfDilutedSharesOutstanding": [(Q1, 17_174_526.0)],
        "Goodwill": [(Q1, 71_498_000.0)],
        "PreferredStockValue": [(Q1, 0.0)],
    }

    def test_rbb_nci_removed_per_end(self):
        with _mock_hist({}, facts=_pe.RBB):
            eq = sps._equity_series(1)
        self.assertEqual(eq[Q1], 531_054_000 - 72_000)
        self.assertEqual(eq[pd.Timestamp("2026-06-30")], 535_177_000 - 72_000)

    def test_rbb_per_share_on_parent_equity(self):
        with _mock_hist(self.RBB_SHARES, facts=_pe.RBB):
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertAlmostEqual(r["bvps_hist"], 530_982_000 / 17_074_159)
        self.assertAlmostEqual(r["bvps_hist"], 31.0986, places=4)
        self.assertAlmostEqual(r["tbvps_hist"], 459_484_000 / 17_074_159)
        self.assertAlmostEqual(r["tbvps_hist"], 26.9111, places=4)

    def test_unseparable_nci_is_na_not_the_total(self):
        # MinorityInterest missing at Q1 while RBB's $72K NCI was tagged a
        # quarter earlier: the total can't be split → n/a, never 531,054,000.
        facts = _pe._drop(_pe.RBB, _pe.MI, "2026-03-31")
        with _mock_hist(self.RBB_SHARES, facts=facts):
            self.assertNotIn(Q1, sps._equity_series(1))
            r = sps._bank_per_share(1, [Q1])[Q1]
        self.assertIsNone(r["bvps_hist"])
        self.assertIsNone(r["tbvps_hist"])

    def test_tmp_including_nci_only_resolves(self):
        q = pd.Timestamp("2026-06-30")
        data = {"CommonStockSharesIssued": [(q, 14_410_189.0)],
                "TreasuryStockCommonShares": [(q, 90_521.0)],
                "WeightedAverageNumberOfDilutedSharesOutstanding":
                    [(q, 14_333_390.0)]}
        with _mock_hist(data, facts=_pe.TMP):
            eq = sps._equity_series(1)
            r = sps._bank_per_share(1, [q])[q]
        self.assertEqual(eq[q], 959_932_000)
        self.assertEqual(eq[pd.Timestamp("2024-12-31")], 713_444_000)
        self.assertAlmostEqual(r["bvps_hist"], 959_932_000 / 14_319_668)
        self.assertAlmostEqual(r["bvps_hist"], 67.0359, places=4)

    def test_deal_comps_tce_uses_parent_equity(self):
        with _mock_hist(self.RBB_SHARES, facts=_pe.RBB):
            tce, end = sps.tangible_common_equity_at(1, "2026-04-30")
        self.assertEqual((tce, end), (459_484_000.0, "2026-03-31"))


if __name__ == "__main__":
    unittest.main()
