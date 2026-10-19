"""
Pins the verify-metrics oracle to the HOUSE conventions (2026-07-09).

The 2026-07-09 18:00 ET run failed with 231 divergences led by roatce_holdco
(116) and fmp:tbvps (88) — the oracle still computed the OLD preferred-inclusive
ROATCE after the display path moved to the common basis (audit #24b), and the
FMP tangible-book cross-check compared FMP's total-basis figure against our
common-basis TBVPS. Pure convention skew, not a regression — but it failed the
job and paged the owner. The oracle must (1) re-derive ROATCE on the common
basis, (2) keep cross-source/cross-convention checks in a WARN tier that never
fails the job.
"""
import unittest
from pathlib import Path

# Order-independent streamlit stub (shared helper).
from tests import _streamlit_stub

_streamlit_stub.install()

from tools.verify_metrics import (  # noqa: E402
    _oracle, _split_pe_basis, _split_release_basis, _quarter_ends)


def _sec(**over):
    base = {
        "book_value_total": 1000.0, "goodwill": 60.0, "intangibles": 40.0,
        "intangible_adjustment": 100.0, "shares_outstanding": 100.0,
        "net_income": 72.0, "net_income_to_common_ttm": 60.0,
        "eps": 0.72, "dividends_per_share": 0.30,
        "preferred_present": True, "preferred_stock": 200.0,
        "tangible_book_value_per_share": 7.0,
        "sec_as_of": "2026-06-30",
    }
    base.update(over)
    return base


def _fact(end, val, form="10-Q", filed="2026-08-05"):
    return {"end": end, "val": val, "form": form, "filed": filed}


def _facts(series: dict, dei=None, **top) -> dict:
    """companyfacts blob: {concept: [(end, val), ...]} under us-gaap/USD."""
    blob = {"facts": {"us-gaap": {
        c: {"units": {"USD": [_fact(e, v) for e, v in rows]}}
        for c, rows in series.items()}}}
    if dei:
        blob["facts"]["dei"] = {"EntityCommonStockSharesOutstanding": {
            "units": {"shares": [_fact(e, v) for e, v in dei]}}}
    blob.update(top)
    return blob


# The four earlier quarter-ends of the TTM window ending 2026-06-30.
_EARLIER = ["2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"]


def _flat_facts(equity=1000.0, pref=200.0, gw=60.0, oth=40.0) -> dict:
    """Constant balance sheet at every earlier quarter-end -> the average
    TCE equals the period-end TCE, so the common-basis hand values hold."""
    return _facts({
        "StockholdersEquity": [(d, equity) for d in _EARLIER],
        "PreferredStockValue": [(d, pref) for d in _EARLIER],
        "Goodwill": [(d, gw) for d in _EARLIER],
        "IntangibleAssetsNetExcludingGoodwill": [(d, oth) for d in _EARLIER],
    })


class TestOracleCommonBasisRoatce(unittest.TestCase):
    def test_preferred_bank_common_basis(self):
        # 60 / (1000 − 200 − 100) = 60/700 = 8.571% — NOT 72/900 = 8.0% (old).
        hard, _ = _oracle({}, _sec(), price=None, facts=_flat_facts())
        self.assertAlmostEqual(hard["roatce_holdco"], 60 / 700 * 100, places=3)
        self.assertNotAlmostEqual(hard["roatce_holdco"], 72 / 900 * 100, places=3)

    def test_unresolved_preferred_is_none_matching_dashboard(self):
        hard, _ = _oracle({}, _sec(preferred_stock=None), price=None)
        self.assertIsNone(hard["roatce_holdco"])

    def test_no_preferred_falls_back_to_total_ni(self):
        hard, _ = _oracle({}, _sec(preferred_present=False, preferred_stock=0.0,
                                   net_income_to_common_ttm=None), price=None,
                          facts=_flat_facts(pref=0.0))
        # 72 / (1000 − 0 − 100) = 8.0%
        self.assertAlmostEqual(hard["roatce_holdco"], 72 / 900 * 100, places=3)

    def test_preferred_without_to_common_is_none(self):
        hard, _ = _oracle({}, _sec(net_income_to_common_ttm=None), price=None)
        self.assertIsNone(hard["roatce_holdco"])


class TestAverageTceRoatce(unittest.TestCase):
    """2026-10-19 on-demand run: 308 roatce_holdco divergences (AUB 16.18
    dashboard vs 15.27 oracle) — the dashboard moved to AVERAGE common TCE
    over the TTM window (REVIEW 2026-10-06 P1-1, commit 45a5c88) while the
    oracle still divided by period-end TCE. The oracle re-derives the
    window itself from raw companyfacts quarter-end facts."""

    def _window(self, **over):
        series = {
            "StockholdersEquity": list(zip(_EARLIER, [980.0, 960.0, 940.0, 920.0])),
            "Goodwill": [(d, 60.0) for d in _EARLIER],
            "IntangibleAssetsNetExcludingGoodwill":
                list(zip(_EARLIER, [40.0, 42.0, 44.0, 46.0])),
        }
        series.update(over)
        return _facts(series)

    def _sec_np(self, **over):
        return _sec(preferred_present=False, preferred_stock=0.0,
                    net_income_to_common_ttm=None, **over)

    def test_quarter_ends(self):
        self.assertEqual(_quarter_ends("2026-06-30"), ["2026-06-30"] + _EARLIER)
        self.assertEqual(_quarter_ends("2026-03-31"),
                         ["2026-03-31", "2025-12-31", "2025-09-30",
                          "2025-06-30", "2025-03-31"])

    def test_five_quarter_average(self):
        # TCE: end 1000 − 0 − 100 = 900; then 980−100=880, 960−102=858,
        # 940−104=836, 920−106=814. Avg = 4288 / 5 = 857.6.
        # ROATCE = 72 / 857.6 = 8.3955% — period-end would be 72/900 = 8.0%.
        hard, _ = _oracle({}, self._sec_np(), None, facts=self._window())
        self.assertAlmostEqual(hard["roatce_holdco"], 72 / 857.6 * 100, places=9)
        self.assertAlmostEqual(hard["roatce_holdco"], 8.3955, places=4)

    def test_year_end_goodwill_carried_and_nci_total_fallback(self):
        # Goodwill tagged only at FY-ends: 2025-12-31 (60) carries to
        # 2026-03-31; 2025-06/09 carry the 2024-12-31 tag (55). 2025-09-30
        # has only the NCI-inclusive total 950 less MinorityInterest 10.
        facts = self._window(
            StockholdersEquity=[("2026-03-31", 980.0), ("2025-12-31", 960.0),
                                ("2025-06-30", 920.0)],
            StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest=[
                ("2025-09-30", 950.0)],
            MinorityInterest=[("2025-09-30", 10.0)],
            Goodwill=[("2025-12-31", 60.0), ("2024-12-31", 55.0)])
        # 980−60−40=880, 960−60−42=858, 940−55−44=841, 920−55−46=819; end 900.
        avg = (900 + 880 + 858 + 841 + 819) / 5        # 859.6
        hard, _ = _oracle({}, self._sec_np(), None, facts=facts)
        self.assertAlmostEqual(hard["roatce_holdco"], 72 / avg * 100, places=9)

    def test_msr_inside_rollup_stays_in_tce(self):
        # 2026-03-31 IANEG 40 includes a same-date $15 MSR → deduct 25 only.
        facts = self._window(ServicingAssetAtFairValueAmount=[("2026-03-31", 15.0)])
        avg = (900 + (980 - 60 - 25) + 858 + 836 + 814) / 5
        hard, _ = _oracle({}, self._sec_np(), None, facts=facts)
        self.assertAlmostEqual(hard["roatce_holdco"], 72 / avg * 100, places=9)

    def test_merger_step_up_is_none(self):
        # 2025-06-30 TCE 606−106 = 500 → 2025-09-30 836: +336 > 0.25×500 + 72 = 197.
        facts = self._window(StockholdersEquity=list(zip(
            _EARLIER, [980.0, 960.0, 940.0, 606.0])))
        hard, _ = _oracle({}, self._sec_np(), None, facts=facts)
        self.assertIsNone(hard["roatce_holdco"])

    def test_retained_earnings_growth_is_not_a_merger(self):
        # 2025-06-30 TCE 816−106 = 710 → 836: +126 < 0.25×710 + 72 = 249.5.
        facts = self._window(StockholdersEquity=list(zip(
            _EARLIER, [980.0, 960.0, 940.0, 816.0])))
        avg = (900 + 880 + 858 + 836 + 710) / 5
        hard, _ = _oracle({}, self._sec_np(), None, facts=facts)
        self.assertAlmostEqual(hard["roatce_holdco"], 72 / avg * 100, places=9)

    def test_window_begin_unresolvable_is_none(self):
        facts = self._window(StockholdersEquity=list(zip(
            _EARLIER[:3], [980.0, 960.0, 940.0])))
        hard, _ = _oracle({}, self._sec_np(), None, facts=facts)
        self.assertIsNone(hard["roatce_holdco"])

    def test_no_facts_is_none(self):
        hard, _ = _oracle({}, self._sec_np(), None, facts=None)
        self.assertIsNone(hard["roatce_holdco"])

    def test_untagged_holdco_goodwill_beside_bank_goodwill_is_none(self):
        # Holdco intangibles 0 while the bank sub reports $50K (×1000 = 50,000
        # > 1% of TCE 1000) of goodwill → n/a, never a 0 deduction.
        facts = _flat_facts(pref=0.0, gw=0.0, oth=0.0)
        sec = self._sec_np(intangible_adjustment=0.0)
        hard, _ = _oracle({"INTANGW": 50.0}, sec, None, facts=facts)
        self.assertIsNone(hard["roatce_holdco"])
        hard, _ = _oracle({"INTANGW": 0.0}, sec, None, facts=facts)
        self.assertAlmostEqual(hard["roatce_holdco"], 72 / 1000 * 100, places=9)


class TestTcePointRules(unittest.TestCase):
    """Per-date TCE rules found by the 2026-10-19 363-bank calibration
    (each class diverged until the oracle followed the house convention)."""

    def _tce(self, series, end="2026-03-31", preferred_now=False, shares=None):
        from tools.verify_metrics import _tce_at
        facts = _facts(series)
        if shares:
            facts["facts"]["us-gaap"].update({
                c: {"units": {"shares": [_fact(e, v) for e, v in rows]}}
                for c, rows in shares.items()})
        return _tce_at(facts, end, preferred_now)

    def test_rollup_beats_year_end_goodwill(self):
        # PFS: Goodwill tagged only at FY-ends (624.069M), the goodwill-
        # inclusive rollup quarterly (773.585M at 2026-03-31 incl. CDI).
        tce = self._tce({
            "StockholdersEquity": [("2026-03-31", 2_862_869_000)],
            "Goodwill": [("2025-12-31", 624_069_000)],
            "IntangibleAssetsNetIncludingGoodwill": [("2026-03-31", 773_585_000)]})
        self.assertEqual(tce, 2_862_869_000 - 773_585_000)        # 2,089,284,000

    def test_goodwill_plus_other_beats_smaller_rollup(self):
        tce = self._tce({
            "StockholdersEquity": [("2026-03-31", 1000.0)],
            "Goodwill": [("2026-03-31", 60.0)],
            "IntangibleAssetsNetExcludingGoodwill": [("2026-03-31", 40.0)],
            "IntangibleAssetsNetIncludingGoodwill": [("2026-03-31", 90.0)]})
        self.assertEqual(tce, 900.0)                               # 1000 − (60+40)

    def test_goodwill_far_above_rollup_is_stale(self):
        # Goodwill 120 > rollup 100 × 1.05 → the rollup serves.
        tce = self._tce({
            "StockholdersEquity": [("2026-03-31", 1000.0)],
            "Goodwill": [("2026-03-31", 120.0)],
            "IntangibleAssetsNetIncludingGoodwill": [("2026-03-31", 100.0)]})
        self.assertEqual(tce, 900.0)

    def test_goodwill_carried_beside_dated_other_intangibles(self):
        # MCHB 2025-06-30: other intangibles 33.308M tagged, goodwill
        # 843.305M tagged only at 2024-12-31 and 2025-09-30 — goodwill
        # still deducted: 2,416.617M − 843.305M − 33.308M = 1,540.004M.
        tce = self._tce({
            "StockholdersEquity": [("2025-06-30", 2_416_617_000)],
            "Goodwill": [("2024-12-31", 843_305_000), ("2025-09-30", 843_305_000)],
            "IntangibleAssetsNetExcludingGoodwill": [("2025-06-30", 33_308_000)]},
            end="2025-06-30")
        self.assertEqual(tce, 1_540_004_000)

    def test_redeemed_preferred_still_deducted_while_outstanding(self):
        # CUBI: no preferred now; 82.201M outstanding at 2025-09-30.
        tce = self._tce({"StockholdersEquity": [("2025-09-30", 2_126_059_000)],
                         "PreferredStockValue": [("2025-09-30", 82_201_000),
                                                 ("2025-12-31", 0)]},
                        end="2025-09-30")
        self.assertEqual(tce, 2_126_059_000 - 82_201_000)

    def test_par_zero_preferred_uses_liquidation_preference(self):
        # BUSE tags PreferredStockValue 0 beside a 222.75M liquidation value.
        series = {"StockholdersEquity": [("2025-09-30", 2_448_835_000)],
                  "PreferredStockValue": [("2025-09-30", 0)],
                  "PreferredStockLiquidationPreferenceValue": [("2025-09-30", 222_750_000)]}
        shares = {"PreferredStockSharesOutstanding": [("2025-09-30", 222_750)]}
        self.assertEqual(self._tce(series, "2025-09-30", True, shares),
                         2_448_835_000 - 222_750_000)
        # Same shares with no value or liquidation tag: unresolved → None.
        del series["PreferredStockLiquidationPreferenceValue"]
        self.assertIsNone(self._tce(series, "2025-09-30", True, shares))

    def test_preferred_issued_after_the_date_is_zero_then(self):
        # PGC: 30M issued 2025-12 (shares first tagged then), TARP 2010-12.
        series = {"StockholdersEquity": [("2025-09-30", 642_550_000)],
                  "PreferredStockValue": [("2010-12-31", 20_746_000),
                                          ("2026-03-31", 30_000_000)]}
        shares = {"PreferredStockSharesIssued": [("2025-12-31", 30_000)]}
        self.assertEqual(self._tce(series, "2025-09-30", True, shares), 642_550_000)

    def test_outstanding_but_untagged_preferred_is_none(self):
        # LOB: preferred outstanding now (overlay only), last tag an explicit
        # 0 at 2025-06-30, nothing at 2025-09-30 → unresolved there.
        series = {"StockholdersEquity": [("2025-06-30", 1_062_889_000),
                                         ("2025-09-30", 1_198_223_000)],
                  "PreferredStockValue": [("2025-06-30", 0)]}
        self.assertIsNone(self._tce(series, "2025-09-30", True))
        self.assertEqual(self._tce(series, "2025-06-30", True), 1_062_889_000)


    def test_year_end_only_preferred_carries(self):
        # BAC tags preferred carrying value only at 10-K dates: 25.992B at
        # 2025-12-31 carries to 2026-03-31 (shares tagged every quarter).
        series = {"StockholdersEquity": [("2026-03-31", 300_000_000_000)],
                  "PreferredStockIncludingAdditionalPaidInCapital": [
                      ("2025-12-31", 25_992_000_000)]}
        shares = {"PreferredStockSharesOutstanding": [("2026-03-31", 3_951_164)]}
        self.assertEqual(self._tce(series, "2026-03-31", True, shares),
                         300_000_000_000 - 25_992_000_000)

    def test_explicit_zero_preferred_is_not_overridden_by_carry(self):
        # CUBI redeemed its last series: 82.201M at 2025-09-30, an explicit
        # 0 at 2025-12-31 — that date deducts nothing.
        series = {"StockholdersEquity": [("2025-12-31", 2_115_517_000)],
                  "PreferredStockValue": [("2025-09-30", 82_201_000),
                                          ("2025-12-31", 0)]}
        self.assertEqual(self._tce(series, "2025-12-31", False), 2_115_517_000)

    def test_stale_goodwill_judged_on_raw_rollup(self):
        # NEWT 2026-03-31: rollup 14.561M holds a 13.622M MSR; goodwill
        # 14.085M is NOT "far above" the 14.561M rollup — deduct
        # max(14.085 + 0.476, 14.561 − 13.622) = 14.561M.
        tce = self._tce({
            "StockholdersEquity": [("2026-03-31", 404_691_000)],
            "Goodwill": [("2026-03-31", 14_085_000)],
            "FiniteLivedIntangibleAssetsNet": [("2026-03-31", 476_000)],
            "IntangibleAssetsNetIncludingGoodwill": [("2026-03-31", 14_561_000)],
            "ServicingAssetAtFairValueAmount": [("2026-03-31", 13_622_000)]})
        self.assertEqual(tce, 404_691_000 - 14_561_000)

    def test_year_end_cdi_carries_beside_same_date_goodwill(self):
        # CCNE 2026-03-31: rollup 88.512M ≈ goodwill 88.4M alone; the
        # 33.693M CDI is tagged only at 2025-12-31 → 88.4 + 33.693 = 122.093M.
        tce = self._tce({
            "StockholdersEquity": [("2026-03-31", 889_101_000)],
            "Goodwill": [("2026-03-31", 88_400_000)],
            "FiniteLivedIntangibleAssetsNet": [("2025-12-31", 33_693_000)],
            "IntangibleAssetsNetIncludingGoodwill": [("2026-03-31", 88_512_000)]})
        self.assertEqual(tce, 889_101_000 - 122_093_000)

    def test_carried_rollup_netted_of_its_own_dates_msr(self):
        # FULT 2025-12-31: goodwill 553.346M; other intangibles only in the
        # 2024 10-K rollup 82.111M, which holds a 54.0M MSR (tagged at
        # 2024-12-31) → other 28.111M; same-date rollup 612.996M − 52M MSR.
        # max(553.346 + 28.111, 560.996) = 581.457M.
        tce = self._tce({
            "StockholdersEquity": [("2025-12-31", 3_490_447_000)],
            "Goodwill": [("2025-12-31", 553_346_000)],
            "IntangibleAssetsNetExcludingGoodwill": [("2024-12-31", 82_111_000)],
            "IntangibleAssetsNetIncludingGoodwill": [("2025-12-31", 612_996_000)],
            "ServicingAssetAtFairValueAmount": [("2024-12-31", 54_000_000),
                                                ("2025-12-31", 52_000_000)]},
            end="2025-12-31")
        self.assertEqual(tce, 3_490_447_000 - 581_457_000)

    def test_material_preferred_flow_without_value_is_none(self):
        # OPHC: no preferred now and none tagged, but $1.932M of preferred
        # issued in the period ending 2024-12-31 (1.7% of equity).
        from tools.verify_metrics import _tce_at
        facts = _facts({"StockholdersEquity": [("2025-06-30", 111_348_000)]})
        facts["facts"]["us-gaap"]["ProceedsFromIssuanceOfPreferredStockAndPreferenceStock"] = {
            "units": {"USD": [{"start": "2024-01-01", "end": "2024-12-31",
                               "val": 1_932_000, "form": "10-K", "filed": "2025-03-01"}]}}
        self.assertIsNone(_tce_at(facts, "2025-06-30", False))

    def test_immaterial_preferred_dividend_is_ignored(self):
        # LKFN-style constant $13K quarterly dividend tag: 13K × 4 / 4% =
        # $1.3M implied < 0.2% of $1B equity → not evidence.
        from tools.verify_metrics import _tce_at
        facts = _facts({"StockholdersEquity": [("2025-06-30", 1_000_000_000)]})
        facts["facts"]["us-gaap"]["DividendsPreferredStock"] = {
            "units": {"USD": [{"start": "2025-04-01", "end": "2025-06-30",
                               "val": 13_000, "form": "10-Q", "filed": "2025-08-01"}]}}
        self.assertEqual(_tce_at(facts, "2025-06-30", False), 1_000_000_000)

class TestMarketCapShareBasis(unittest.TestCase):
    """2026-10-19: 39 market_cap divergences (AROW 685.1M dashboard vs 611.9M
    oracle). The dashboard prices the CURRENT count — the dei cover-page
    count when it post-dates the balance sheet (AROW: 16,545,170 at
    2026-06-30, 18,523,723 on the July-31 cover after the July-1 Adirondack
    merger issued ~1.98M shares) — while the oracle used the quarter-end."""

    def _bs(self):
        return {"Assets": [("2026-06-30", 5000.0)]}

    def test_cover_newer_than_balance_sheet_wins(self):
        facts = _facts(self._bs(), dei=[("2026-04-30", 16_529_019),
                                        ("2026-07-31", 18_523_723)])
        hard, _ = _oracle({}, _sec(shares_outstanding=16_545_170), 37.0,
                          facts=facts)
        self.assertEqual(hard["market_cap"], 685_377_751.0)   # 37 × 18,523,723

    def test_cover_not_newer_keeps_balance_sheet_count(self):
        facts = _facts(self._bs(), dei=[("2026-06-30", 16_600_000)])
        hard, _ = _oracle({}, _sec(shares_outstanding=16_545_170), 37.0,
                          facts=facts)
        self.assertEqual(hard["market_cap"], 612_171_290.0)   # 37 × 16,545,170

    def test_multi_class_cover_never_substitutes(self):
        facts = _facts(self._bs(), dei=[("2026-07-31", 9_000_000)],
                       _class_shares={"end": "2026-06-30", "value": 12_000_000})
        hard, _ = _oracle({}, _sec(shares_outstanding=12_000_000), 10.0,
                          facts=facts)
        self.assertEqual(hard["market_cap"], 120_000_000.0)


class TestReleaseBasisIdentity(unittest.TestCase):
    """Non-SEC filers (ARBV: dashboard 163.6M vs oracle None) price market
    cap off the release share count tied out to the release's equity and
    P/E off four discrete release quarters — no XBRL for the oracle to
    rebuild. The hard check is the identity on the displayed inputs."""

    def test_otc_release_share_row(self):
        price = 41.5
        hard, _ = _oracle({}, {}, price)
        self.assertIsNone(hard["market_cap"])
        dash = {"market_cap_source": "company_release",
                "shares_outstanding": 3_942_000, "market_cap": 163_593_000.0,
                "eps_source": "release_ttm_otc", "eps": 4.15, "pe_ratio": 10.0}
        _split_release_basis(dash, hard, price)
        self.assertEqual(hard["market_cap"], 163_593_000.0)    # 41.5 × 3,942,000
        self.assertAlmostEqual(hard["pe_ratio"], 10.0, places=9)  # 41.5 / 4.15

    def test_identity_still_catches_wiring_bug(self):
        # Displayed count 3,942,000 but the cap was priced on 394,200.
        hard, _ = _oracle({}, {}, 41.5)
        dash = {"market_cap_source": "company_release",
                "shares_outstanding": 3_942_000, "market_cap": 16_359_300.0}
        _split_release_basis(dash, hard, 41.5)
        self.assertNotAlmostEqual(hard["market_cap"], dash["market_cap"], places=0)

    def test_sec_basis_untouched(self):
        hard, _ = _oracle({}, _sec(), 10.0)
        before = dict(hard)
        _split_release_basis({"eps_source": "reconstructed",
                              "market_cap_source": None, "eps": 9.0,
                              "shares_outstanding": 1}, hard, 10.0)
        self.assertEqual(hard, before)


class TestWarnTierNeverFailsJob(unittest.TestCase):
    def test_ptbv_is_warn_tier_not_hard(self):
        hard, warn = _oracle({}, _sec(), price=14.0)
        self.assertNotIn("ptbv_ratio", hard)
        self.assertAlmostEqual(warn["ptbv_ratio"], 2.0, places=6)  # 14 / 7

    def test_source_structure_warn_never_in_exit(self):
        src = (Path(__file__).parent.parent / "tools" /
               "verify_metrics.py").read_text(encoding="utf-8")
        # fmp cross-checks + ptbv go through _warn(...), not divergences
        self.assertIn('_warn("fmp:tbvps"', src)
        self.assertIn('_warn("fmp:pe_ratio"', src)
        self.assertIn('_warn("ptbv_ratio"', src)
        self.assertIn('_warn("pe_ratio:xbrl_basis"', src)
        # exit is decided by hard divergences only
        self.assertIn("return 1 if diverged else 0", src)
        self.assertNotIn("warnings else 1", src)
        # FMP tbvps compares TOTAL-basis reconstruction (FMP's convention)
        self.assertIn("tbvps_total", src)


class TestDeclaredZeroDividend(unittest.TestCase):
    """KFFB (2026-07-13 nightly alert): every recent 10-Q explicitly tags
    $0 declared dividends, so the dashboard's 0.0% yield is evidence-backed.
    The oracle's truthiness check (`dps and ...`) collapsed declared-zero
    into None and the hard gate flagged the honest 0.0 as a divergence."""

    def test_declared_zero_dividend_is_zero_not_none(self):
        hard, _ = _oracle({}, _sec(dividends_per_share=0.0), price=4.0)
        self.assertEqual(hard["dividend_yield"], 0.0)

    def test_missing_dps_is_still_none(self):
        hard, _ = _oracle({}, _sec(dividends_per_share=None), price=4.0)
        self.assertIsNone(hard["dividend_yield"])

    def test_missing_price_is_still_none(self):
        hard, _ = _oracle({}, _sec(dividends_per_share=0.0), price=None)
        self.assertIsNone(hard["dividend_yield"])


class TestPeOnServedEpsBasis(unittest.TestCase):
    """Every weekday run from ~2026-09-08 failed on pe_ratio for ONB, FRME,
    HBAN, CCBG: the dashboard served the release-anchored composite TTM EPS
    (SEC companyfacts still held only Q1-2026 for them) while the oracle
    priced P/E off the stale XBRL TTM. ONB: price/2.26 = 11.17 vs
    price/1.95 = 12.95. Convention skew must be WARN, and the hard check
    must verify the identity on the basis actually served."""

    def test_composite_basis_moves_xbrl_pe_to_warn_and_checks_identity(self):
        price = 25.25
        hard, warn = _oracle({}, _sec(eps=1.95), price=price)
        self.assertAlmostEqual(hard["pe_ratio"], 25.25 / 1.95, places=9)
        dash = {"eps": 2.26, "eps_source": "release_ttm",
                "pe_ratio": 25.25 / 2.26}
        _split_pe_basis(dash, hard, warn, price)
        self.assertAlmostEqual(hard["pe_ratio"], 25.25 / 2.26, places=9)
        self.assertAlmostEqual(warn["pe_ratio:xbrl_basis"], 25.25 / 1.95,
                               places=9)

    def test_composite_basis_still_catches_wiring_bug(self):
        # The dashboard displays EPS 2.26 but priced P/E off something else.
        hard, warn = _oracle({}, _sec(eps=1.95), price=25.25)
        dash = {"eps": 2.26, "eps_source": "release_ttm", "pe_ratio": 9.0}
        _split_pe_basis(dash, hard, warn, 25.25)
        self.assertNotAlmostEqual(hard["pe_ratio"], dash["pe_ratio"], places=2)

    def test_xbrl_basis_is_untouched(self):
        hard, warn = _oracle({}, _sec(eps=3.53), price=20.0)
        dash = {"eps": 3.53, "eps_source": "reconstructed", "pe_ratio": 20 / 3.53}
        _split_pe_basis(dash, hard, warn, 20.0)
        self.assertAlmostEqual(hard["pe_ratio"], 20 / 3.53, places=9)
        self.assertNotIn("pe_ratio:xbrl_basis", warn)

    def test_composite_with_no_price_is_none(self):
        hard, warn = _oracle({}, _sec(eps=1.95), price=None)
        dash = {"eps": 2.26, "eps_source": "release_ttm", "pe_ratio": None}
        _split_pe_basis(dash, hard, warn, None)
        self.assertIsNone(hard["pe_ratio"])
        self.assertIsNone(warn["pe_ratio:xbrl_basis"])


class TestAbsentValuesAgree(unittest.TestCase):
    """(2026-09-30) 10 of 12 nightly runs failed on 112 'divergences' that were
    all `cet1_ratio` / `total_capital_ratio` with dashboard=nan oracle=nan:
    CBLR banks (CBLRIND=1, RWAJ=0) whose FDIC literal-0 risk-based ratios are
    nulled by null_unreported_capital, then arrive as NaN through
    DataFrame.to_dict — and NaN is never "close" to NaN."""

    def test_agreeing_absence_is_close(self):
        from tools.verify_metrics import _abs_close, _rel_close
        nan = float("nan")
        for close in (_abs_close, _rel_close):
            self.assertTrue(close(nan, nan))
            self.assertTrue(close(None, nan))
            self.assertTrue(close(None, None))

    def test_one_sided_absence_still_diverges(self):
        from tools.verify_metrics import _abs_close, _rel_close
        nan = float("nan")
        for close in (_abs_close, _rel_close):
            self.assertFalse(close(nan, 12.0))
            self.assertFalse(close(12.0, None))

    def test_tolerances_unchanged(self):
        from tools.verify_metrics import _abs_close, _rel_close
        self.assertTrue(_abs_close(12.0, 12.005))     # within 1 bp
        self.assertFalse(_abs_close(12.0, 12.02))
        self.assertTrue(_rel_close(1000.0, 1004.0))   # 0.4% < 0.5%
        self.assertFalse(_rel_close(1000.0, 1006.0))

    def test_cblr_record_path_yields_nan_and_is_close(self):
        """ASCN's real 6/30/2026 capital fields, through the same boundary the
        job uses: null_unreported_capital, then fetch_financials' per-column
        pd.to_numeric — which is what turns the None into NaN."""
        import pandas as pd
        from data.fdic_client import null_unreported_capital
        from tools.verify_metrics import _abs_close
        rec = null_unreported_capital({
            "REPDTE": "20260630", "IDT1CER": 0, "RBCRWAJ": 0, "RWAJ": 0,
            "RBCT1J": 20249, "RBC1AAJ": 10.208670488175004, "CBLRIND": 1})
        df = pd.DataFrame([rec])
        for col in df.columns:
            if col != "REPDTE":
                df[col] = pd.to_numeric(df[col], errors="coerce")
        row = df.to_dict("records")[0]
        for f in ("IDT1CER", "RBCRWAJ"):
            v = row[f]
            self.assertTrue(isinstance(v, float) and v != v,
                            f"{f} arrives as NaN, not None")
            self.assertTrue(_abs_close(v, v), f"{f}: n/a vs n/a is agreement")


if __name__ == "__main__":
    unittest.main()
