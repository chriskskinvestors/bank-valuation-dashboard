"""
Regression tests for the 2026-06-11 audit's P0 correctness fixes
(docs/AUDIT-2026-06-11.md). Each test pins a bug that shipped once:

A1  — unit-guess double-converted sub-$1B banks into trillion-dollar tiers
A12 — "TCE" and "ROATCE" on the statement pages used different intangibles fields
A13 — missing loan totals produced absurd past-due ratios via an `or 1` denominator
A19 — quarters beyond the hand-maintained Fed-funds table silently dropped out
"""
import sys
import types
import unittest

import pandas as pd

# Stub streamlit before importing modules that decorate with st.cache_data.
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)


class TestA1PeerTierUnits(unittest.TestCase):
    """total_assets is raw dollars; no '< 1e9 → ×1000' guessing."""

    def test_800m_bank_is_community_not_money_center(self):
        from analysis.peer_groups import group_banks, asset_size_tier
        # A genuine $800M community bank (raw dollars). The old heuristic
        # multiplied this by 1000 → $800B → "Large Regional ($100B-$1T)".
        m = {"ticker": "TINY", "total_assets": 8e8}
        groups = group_banks([m])
        self.assertIn("Community (<$10B)", groups["by_size"])
        self.assertNotIn("Large Regional ($100B-$1T)", groups["by_size"])
        self.assertEqual(asset_size_tier(8e8), "Community (<$10B)")

    def test_tier_context_uses_raw_dollars(self):
        from analysis.peer_groups import metric_percentile_context
        mets = [{"ticker": f"B{i}", "total_assets": 5e8 + i * 1e7,
                 "roaa": 1.0 + i * 0.05} for i in range(8)]
        ctx = metric_percentile_context("B0", mets, metric_keys=["roaa"], mode="size")
        self.assertEqual(ctx["_meta"]["tier"], "Community (<$10B)")

    def test_metrics_boundary_converts_asset_to_dollars(self):
        # The contract A1 relies on: build_bank_metrics emits total_assets in
        # raw dollars (FDIC reports $thousands).
        from analysis.metrics import build_bank_metrics
        out = build_bank_metrics("X", {"ASSET": 800_000, "REPDTE": "2025-12-31"},
                                 {}, {}, [])
        self.assertEqual(out.get("total_assets"), 800_000 * 1000)


class TestA12TceConvention(unittest.TestCase):
    """One TCE convention on the statement pages: equity − INTAN (total
    intangibles), for BOTH the tce row and the roatce row."""

    def test_tce_kind_uses_total_intangibles(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "ui" /
               "financials_statements.py").read_text(encoding="utf-8")
        tce_block = src.split('if kind == "tce":')[1].split('if kind ==')[0]
        self.assertIn('"INTAN"', tce_block,
                      "tce kind must subtract INTAN (total intangibles)")
        self.assertNotIn('"INTANGW"', tce_block,
                         "tce kind must not use INTANGW (goodwill only)")
        roatce_block = src.split('if kind == "roatce":')[1].split('if kind ==')[0]
        self.assertIn('"INTAN"', roatce_block)


class TestA13PastDueDenominator(unittest.TestCase):
    """Missing total_loans must skip the ratio, not divide by 1."""

    def test_missing_loans_skips_past_due_pct(self):
        from analysis.credit_dynamics import build_credit_timeline
        recs = [{"REPDTE": "2025-12-31", "P3ASSET": 5_000,  # $5M past due ($000)
                 "LNLSNET": None}]
        df = build_credit_timeline(recs)
        if "past_due_30_89_pct" in df.columns:
            self.assertTrue(df["past_due_30_89_pct"].isna().all(),
                            "past-due % must be absent when loans are missing "
                            "(the old `or 1` produced 500000%)")

    def test_present_loans_computes_normally(self):
        from analysis.credit_dynamics import build_credit_timeline, _CREDIT_FIELDS
        loans_field = _CREDIT_FIELDS.get("total_loans", "LNLSNET")
        pd3089_field = _CREDIT_FIELDS.get("past_due_30_89", "P3ASSET")
        recs = [{"REPDTE": "2025-12-31", loans_field: 1_000_000,
                 pd3089_field: 10_000}]
        df = build_credit_timeline(recs)
        self.assertAlmostEqual(float(df["past_due_30_89_pct"].iloc[0]), 1.0)


class TestA19FedFunds(unittest.TestCase):
    """Quarters beyond the static table derive from FRED instead of vanishing."""

    def test_table_quarter_still_served(self):
        from analysis.deposit_dynamics import _get_fed_funds
        self.assertEqual(_get_fed_funds("2025-12-31"), 4.00)

    def test_missing_quarter_derives_from_fred(self):
        import analysis.deposit_dynamics as dd
        dd._FED_FUNDS_LIVE.clear()
        fred = types.ModuleType("data.fred_client")
        fred.fetch_series = lambda sid, years=3: pd.DataFrame({
            "date": pd.to_datetime(["2026-04-01", "2026-05-01", "2026-06-01"]),
            "value": [3.70, 3.60, 3.50],
        })
        old = sys.modules.get("data.fred_client")
        sys.modules["data.fred_client"] = fred
        try:
            v = dd._get_fed_funds("2026-06-30")  # not in the static table
            self.assertAlmostEqual(v, 3.60, places=2)
        finally:
            if old is not None:
                sys.modules["data.fred_client"] = old
            else:
                sys.modules.pop("data.fred_client", None)

    def test_fred_failure_returns_none_not_garbage(self):
        import analysis.deposit_dynamics as dd
        dd._FED_FUNDS_LIVE.clear()
        fred = types.ModuleType("data.fred_client")
        def _boom(sid, years=3):
            raise ConnectionError("offline")
        fred.fetch_series = _boom
        old = sys.modules.get("data.fred_client")
        sys.modules["data.fred_client"] = fred
        try:
            self.assertIsNone(dd._get_fed_funds("2027-03-31"))
        finally:
            if old is not None:
                sys.modules["data.fred_client"] = old
            else:
                sys.modules.pop("data.fred_client", None)


def _flow_facts(entries):
    """Build a minimal companyfacts dict for NetIncomeLoss from
    (start, end, val, form, filed) tuples."""
    return {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        {"start": s, "end": e, "val": v, "form": f, "filed": d}
        for s, e, v, f, d in entries
    ]}}}}}


class TestTtmWindowIntegrity(unittest.TestCase):
    """_extract_ttm_value must never span 5 quarters.

    Most issuers tag Q4 only inside the 10-K's FY duration. The old code
    summed 'the 4 most recent ~3-month facts', which right after a Q1
    filing was Q1+Q2+Q3 of last year plus Q1 of this year — Q4 dropped,
    year-ago Q1 double-counted. Verified live: JPM ROATCE printed 19.52%
    (NI window 60.5B) vs the true filing-derived 19.00% (58.9B); SFST
    8.01% vs 9.25%."""

    # Discrete Q1-Q3, FY + 9M YTD in the 10-K, then the new-year Q1.
    SFST_SHAPE = [
        ("2025-01-01", "2025-03-31", 5.0, "10-Q", "2025-05-01"),
        ("2025-04-01", "2025-06-30", 6.0, "10-Q", "2025-08-01"),
        ("2025-07-01", "2025-09-30", 8.0, "10-Q", "2025-11-01"),
        ("2025-01-01", "2025-09-30", 19.0, "10-Q", "2025-11-01"),   # 9M YTD
        ("2025-01-01", "2025-12-31", 29.0, "10-K", "2026-02-20"),   # FY
        ("2026-01-01", "2026-03-31", 9.0, "10-Q", "2026-05-01"),
    ]

    def test_q4_derived_from_fy_minus_9m(self):
        from data.sec_client import _extract_ttm_value
        ttm = _extract_ttm_value(_flow_facts(self.SFST_SHAPE), "NetIncomeLoss")
        # Q4 = 29 − 19 = 10; TTM = 6 + 8 + 10 + 9 = 33.
        # The old 5-quarter window returned 5 + 6 + 8 + 9 = 28.
        self.assertEqual(ttm, 33.0)

    def test_non_contiguous_quarters_fall_back_to_annual(self):
        from data.sec_client import _extract_ttm_value
        # No 9M YTD → Q4 underivable → the 4 newest quarters span 5
        # calendar quarters and must be rejected in favor of the FY value.
        entries = [r for r in self.SFST_SHAPE if r[:2] != ("2025-01-01", "2025-09-30")]
        ttm = _extract_ttm_value(_flow_facts(entries), "NetIncomeLoss")
        self.assertEqual(ttm, 29.0)

    def test_four_discrete_consecutive_quarters_sum_directly(self):
        from data.sec_client import _extract_ttm_value
        entries = [
            ("2025-04-01", "2025-06-30", 6.0, "10-Q", "2025-08-01"),
            ("2025-07-01", "2025-09-30", 8.0, "10-Q", "2025-11-01"),
            ("2025-10-01", "2025-12-31", 10.0, "10-K", "2026-02-20"),
            ("2026-01-01", "2026-03-31", 9.0, "10-Q", "2026-05-01"),
        ]
        ttm = _extract_ttm_value(_flow_facts(entries), "NetIncomeLoss")
        self.assertEqual(ttm, 33.0)

    def test_restatement_latest_filing_wins(self):
        from data.sec_client import _extract_ttm_value
        entries = self.SFST_SHAPE + [
            # Q2 restated from 6.0 → 6.5 in a later filing
            ("2025-04-01", "2025-06-30", 6.5, "10-Q", "2026-05-01"),
        ]
        ttm = _extract_ttm_value(_flow_facts(entries), "NetIncomeLoss")
        self.assertEqual(ttm, 33.5)


class TestTtmOrNoneInvariant(unittest.TestCase):
    """net_income / eps are 12-month values or None — never a single quarter.

    The old fallback served the latest single-period value when TTM
    derivation failed, understating ROATCE/ROE ~4x and overstating P/E ~4x
    (a plausible-wrong number, worse than an honest n/a)."""

    @staticmethod
    def _facts(ni_entries, eps_entries=(), dei_shares=None, shares_entries=()):
        def rows(entries):
            return [{"start": s, "end": e, "val": v, "form": f, "filed": d}
                    for s, e, v, f, d in entries]
        facts = {"facts": {"us-gaap": {
            "NetIncomeLoss": {"units": {"USD": rows(ni_entries)}},
            "EarningsPerShareDiluted": {"units": {"USD/shares": rows(eps_entries)}},
        }, "dei": {}}}
        if shares_entries:
            facts["facts"]["us-gaap"]["CommonStockSharesOutstanding"] = {
                "units": {"shares": [
                    {"end": e, "val": v, "form": f, "filed": d}
                    for e, v, f, d in shares_entries]}}
        if dei_shares:
            facts["facts"]["dei"]["EntityCommonStockSharesOutstanding"] = {
                "units": {"shares": [
                    {"end": e, "val": v, "form": f, "filed": d}
                    for e, v, f, d in dei_shares]}}
        return facts

    # Two lone quarters: no contiguous window, no annual — no honest TTM.
    ORPHAN_QUARTERS = [
        ("2025-07-01", "2025-09-30", 8.0, "10-Q", "2025-11-01"),
        ("2026-01-01", "2026-03-31", 9.0, "10-Q", "2026-05-01"),
    ]

    def test_no_ttm_means_none_not_single_quarter(self):
        from unittest.mock import patch
        from data import sec_client
        facts = self._facts(self.ORPHAN_QUARTERS, eps_entries=[
            ("2026-01-01", "2026-03-31", 1.65, "10-Q", "2026-05-01")])
        with patch.object(sec_client, "fetch_company_facts", return_value=facts):
            result = sec_client.get_latest_fundamentals(1)
        self.assertIsNone(result["net_income"])
        self.assertFalse(result["net_income_is_ttm"])
        self.assertEqual(result["net_income_latest_period"], 9.0)
        self.assertIsNone(result["eps"])
        self.assertFalse(result["eps_is_ttm"])
        self.assertEqual(result["eps_latest_period"], 1.65)

    def test_honest_ttm_flows_through(self):
        from unittest.mock import patch
        from data import sec_client
        facts = self._facts(TestTtmWindowIntegrity.SFST_SHAPE)
        with patch.object(sec_client, "fetch_company_facts", return_value=facts):
            result = sec_client.get_latest_fundamentals(1)
        self.assertEqual(result["net_income"], 33.0)
        self.assertTrue(result["net_income_is_ttm"])

    def test_shares_cover_divergence_recorded_and_flagged(self):
        from unittest.mock import patch
        from data import sec_client
        from data.validation import check_shares_cover
        # SFST April-2026 raise: 8,247,665 at quarter-end vs 9,455,165 cover
        facts = self._facts(
            self.ORPHAN_QUARTERS,
            dei_shares=[("2026-04-27", 9_455_165, "10-Q", "2026-05-01")],
            shares_entries=[("2026-03-31", 8_247_665, "10-Q", "2026-05-01")])
        with patch.object(sec_client, "fetch_company_facts", return_value=facts):
            result = sec_client.get_latest_fundamentals(1)
        self.assertEqual(result["shares_outstanding_cover"], 9_455_165)
        self.assertAlmostEqual(result["shares_cover_divergence_pct"], 12.77, places=1)
        findings = check_shares_cover(result)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "warning")
        # Small drift stays silent
        self.assertEqual(check_shares_cover({"shares_cover_divergence_pct": 2.0}), [])


class TestUsDomicileFilter(unittest.TestCase):
    """Universe scope is US-domiciled filers only. Two signals required
    because foreign issuers can carry an EMPTY stateOfIncorporation (HSBC)."""

    def test_foreign_state_code_rejected(self):
        from data.bank_universe import _is_us_domestic_filer
        # BAWAG: Austria ("C4"), SIC empty at discovery time
        self.assertFalse(_is_us_domestic_filer(
            {"stateOfIncorporation": "C4",
             "filings": {"recent": {"form": ["20-F", "6-K"]}}}))

    def test_empty_state_foreign_forms_rejected(self):
        from data.bank_universe import _is_us_domestic_filer
        # HSBC: empty state, files only 6-K — the state check alone misses it
        self.assertFalse(_is_us_domestic_filer(
            {"stateOfIncorporation": "",
             "filings": {"recent": {"form": ["6-K", "6-K", "F-3"]}}}))

    def test_us_bank_passes(self):
        from data.bank_universe import _is_us_domestic_filer
        self.assertTrue(_is_us_domestic_filer(
            {"stateOfIncorporation": "DE",
             "filings": {"recent": {"form": ["10-K", "10-Q", "8-K"]}}}))
        # US filer with omitted state but domestic forms still passes
        self.assertTrue(_is_us_domestic_filer(
            {"stateOfIncorporation": "",
             "filings": {"recent": {"form": ["10-Q", "8-K"]}}}))

    def test_x1_united_states_code_passes(self):
        from data.bank_universe import _is_us_domestic_filer
        # ATLO (Ames National Corp): EDGAR codes federally chartered
        # entities as "X1" = United States. Regression 2026-06-12: the
        # filter treated X1 as foreign and dropped a real Iowa bank.
        self.assertTrue(_is_us_domestic_filer(
            {"stateOfIncorporation": "X1",
             "filings": {"recent": {"form": ["10-K", "10-Q", "8-K"]}}}))

    def test_x0_united_kingdom_still_rejected(self):
        from data.bank_universe import _is_us_domestic_filer
        # Barclays: "X0" = United Kingdom — the X prefix is NOT US-generic
        self.assertFalse(_is_us_domestic_filer(
            {"stateOfIncorporation": "X0",
             "filings": {"recent": {"form": ["20-F", "6-K"]}}}))


class TestServedSnapshot(unittest.TestCase):
    """Cross-instance snapshot serving (data/cache.served_snapshot) — the
    pattern that keeps cold instances from rebuilding read-time aggregates
    (Home metrics 60s, earnings calendar 11.6s, FRED bundle 3.6s)."""

    def _patch(self, store):
        import data.cache as cache
        self._orig = (cache.get, cache.put)
        cache.get = lambda k: store.get(k)
        cache.put = lambda k, v: store.__setitem__(k, v)
        self.addCleanup(lambda: (setattr(cache, "get", self._orig[0]),
                                 setattr(cache, "put", self._orig[1])))

    def test_fresh_snapshot_served_without_build(self):
        from datetime import datetime
        import data.cache as cache
        store = {"k": {"cached_at": datetime.now().isoformat(),
                       "guard": 3, "value": [1, 2]}}
        self._patch(store)
        self.assertEqual(
            cache.served_snapshot(
                "k", 3600, lambda: self.fail("build must not run"), guard=3),
            [1, 2])

    def test_stale_snapshot_rebuilds_and_persists(self):
        import data.cache as cache
        store = {"k": {"cached_at": "2020-01-01T00:00:00",
                       "guard": 3, "value": [1, 2]}}
        self._patch(store)
        self.assertEqual(
            cache.served_snapshot("k", 3600, lambda: [9], guard=3), [9])
        self.assertEqual(store["k"]["value"], [9])

    def test_guard_mismatch_rebuilds(self):
        from datetime import datetime
        import data.cache as cache
        store = {"k": {"cached_at": datetime.now().isoformat(),
                       "guard": 3, "value": [1, 2]}}
        self._patch(store)
        # universe grew 3 -> 4: fresh-but-mismatched snapshot must rebuild
        self.assertEqual(
            cache.served_snapshot("k", 3600, lambda: [9], guard=4), [9])


def _ffiec_df(rows):
    """Build a synthetic Call Report DF in the ffiec-data-connect v3 long
    form from (mdrm, value) pairs. TEXT* mnemonics become str rows (the
    RI-E write-in labels); everything else is an int row."""
    out = []
    for m, v in rows:
        if str(m).upper().startswith("TEXT"):
            out.append({
                "mdrm": m, "rssd": "999999", "quarter": "12/31/2025",
                "data_type": "str", "int_data": None,
                "float_data": None, "bool_data": None, "str_data": v,
            })
        else:
            out.append({
                "mdrm": m, "rssd": "999999", "quarter": "12/31/2025",
                "data_type": "int", "int_data": v,
                "float_data": None, "bool_data": None, "str_data": None,
            })
    return pd.DataFrame(out)


class TestRiIncomeDetail(unittest.TestCase):
    """Schedule RI detail extraction: a true $0 stays 0.0, an absent code
    is None (never conflated), provision_unfunded only derives when both
    components were reported, *_usd scales ×1000."""

    # Banner Bank 12/31/2025 values from docs/SNL-BUILD-PLAN.md
    ROWS = [
        ("RIADC014", 10_152),    # BOLI income
        ("RIAD4230", 11_637),    # provision: loans
        ("RIADJJ33", 13_045),    # provision: total
        ("RIAD4080", 25_433),    # service charges
        ("RIAD4135", 243_487),   # comp & benefits
        ("RIADC216", 0),         # goodwill impairment — true $0
    ]

    def _detail(self, rows=None):
        from data.ffiec_client import get_ri_income_detail
        return get_ri_income_detail(
            999999, reporting_period="12/31/2025",
            call_report_df=_ffiec_df(self.ROWS if rows is None else rows))

    def test_known_codes_extract(self):
        d = self._detail()
        self.assertEqual(d["boli_income"], 10_152.0)
        self.assertEqual(d["service_charges"], 25_433.0)
        self.assertEqual(d["comp_benefits"], 243_487.0)
        self.assertEqual(d["reporting_period"], "12/31/2025")

    def test_true_zero_stays_zero_not_none(self):
        d = self._detail()
        self.assertEqual(d["goodwill_impairment"], 0.0)
        self.assertEqual(d["goodwill_impairment_usd"], 0.0)

    def test_absent_code_is_none(self):
        d = self._detail()
        self.assertIsNone(d["trading_revenue"])
        self.assertIsNone(d["trading_revenue_usd"])

    def test_provision_unfunded_derivation(self):
        d = self._detail()
        self.assertEqual(d["provision_unfunded"], 13_045.0 - 11_637.0)  # 1,408
        # One component missing → underivable, never imputed as $0
        rows = [r for r in self.ROWS if r[0] != "RIADJJ33"]
        self.assertIsNone(self._detail(rows)["provision_unfunded"])

    def test_usd_scaling(self):
        d = self._detail()
        self.assertEqual(d["boli_income_usd"], 10_152_000.0)
        self.assertEqual(d["provision_unfunded_usd"], 1_408_000.0)


class TestCompanyNavRegistry(unittest.TestCase):
    """Every COMPANY_NAV leaf has a renderer and vice versa (A17 class:
    a nav entry silently rendering nothing, or dead renderers)."""

    def test_nav_and_renderers_in_sync(self):
        from ui.company_nav import COMPANY_NAV, _RENDERERS
        leaves = {leaf for subs in COMPANY_NAV.values() for leaf in subs}
        self.assertEqual(leaves - set(_RENDERERS), set(),
                         "nav entries with no renderer (would render blank)")
        self.assertEqual(set(_RENDERERS) - leaves, set(),
                         "renderers with no nav entry (dead code)")

    def test_no_duplicate_leaves_across_sections(self):
        from ui.company_nav import COMPANY_NAV
        all_leaves = [leaf for subs in COMPANY_NAV.values() for leaf in subs]
        self.assertEqual(len(all_leaves), len(set(all_leaves)))


class TestRcrCapitalDetail(unittest.TestCase):
    """Schedule RC-R Part I capital walk. Values are Banner Bank's filed
    12/31/2025 call report (RSSD 352772, live-probed) — the walk identities
    re-sum to the filed totals to the dollar:
      1,951,461 − 372,990 − 6,912 + 213,012 + 0 = 1,784,571 (CET1)
      0 + 173,048 + 0 = 173,048 (T2); total 1,957,619."""

    BANNER = [
        ("RCOAP840", 1_951_461),   # CET1 before adjustments
        ("RCOAP841", 370_753),     # goodwill deduction
        ("RCOAP842", 2_237),       # other intangibles deduction
        ("RCOAP843", 6_912),       # DTA deduction
        ("RCOAP844", -213_012),    # 9.a unrealized AFS losses (negative)
        ("RCOAP859", 1_784_571),   # CET1
        ("RCOAP865", 0),           # additional tier 1
        ("RCOA8274", 1_784_571),   # tier 1
        ("RCOAP866", 0),           # T2 instruments
        ("RCOA5310", 173_048),     # T2 allowance
        ("RCOA5311", 173_048),     # tier 2
        ("RCOA3792", 1_957_619),   # total capital
        ("RCOAA223", 13_841_345),  # RWA
    ]

    def _detail(self, rows):
        from data.ffiec_client import get_rcr_capital_detail
        return get_rcr_capital_detail(
            999999, "12/31/2025", call_report_df=_ffiec_df(rows))

    def test_banner_walk_identities(self):
        d = self._detail(self.BANNER)
        self.assertEqual(d["intangibles_deduction"], 372_990)
        self.assertEqual(d["aoci_adjustment"], 213_012)  # losses added back
        # Identity: cet1 = before − intangibles − dta + aoci + other
        self.assertEqual(d["other_cet1_adjustments"], 0)
        self.assertEqual(
            d["cet1_before_adjustments"] - d["intangibles_deduction"]
            - d["dta_deduction"] + d["aoci_adjustment"]
            + d["other_cet1_adjustments"], d["cet1"])
        # T2 residual: 173,048 − (0 + 173,048) = 0
        self.assertEqual(d["t2_other"], 0)
        self.assertEqual(d["total_capital"], 1_957_619)
        self.assertEqual(d["rwa_usd"], 13_841_345_000)

    def test_first_prefix_wins_not_max(self):
        # Sign-safety: a negative RCOA value must beat a larger RCFW value
        # (max() would pick the wrong filer variant for negative items).
        rows = self.BANNER + [("RCFWP844", 999_999)]
        d = self._detail(rows)
        # RCOA comes before RCFW in priority — but RCFA comes FIRST: add one
        rows2 = [("RCFAP844", -50_000)] + rows
        d2 = self._detail(rows2)
        self.assertEqual(d["aoci_adjustment"], 213_012)
        self.assertEqual(d2["aoci_adjustment"], 50_000)

    def test_no_rcr_content_returns_none(self):
        d = self._detail([("RIAD4340", 123)])
        self.assertIsNone(d)


class TestRiEDetail(unittest.TestCase):
    """Schedule RI-E itemizations. Banner Bank 12/31/2025 (live-probed):
    only data processing crosses the threshold (C017 = 30,787) and one
    labeled income write-in (4461 = 2,186 'Merchant Fee Income') — every
    other preprinted line is None (below threshold), never $0."""

    BANNER = [
        ("RIADC017", 30_787),
        ("RIAD4461", 2_186),
        ("TEXT4461", "Merchant Fee Income"),
    ]

    def _detail(self, rows):
        from data.ffiec_client import get_ri_e_detail
        return get_ri_e_detail(
            999999, "12/31/2025", call_report_df=_ffiec_df(rows))

    def test_banner_itemization(self):
        d = self._detail(self.BANNER)
        self.assertEqual(d["data_processing"], 30_787)
        self.assertEqual(d["data_processing_usd"], 30_787_000)
        # Below-threshold preprinted lines are None — not $0
        self.assertIsNone(d["marketing_professional"])
        self.assertIsNone(d["fdic_assessments"])
        self.assertEqual(d["income_writeins"], [{
            "label": "Merchant Fee Income",
            "value": 2_186, "value_usd": 2_186_000}])
        self.assertEqual(d["expense_writeins"], [])

    def test_filed_zero_stays_zero(self):
        d = self._detail(self.BANNER + [("RIAD4141", 0)])
        self.assertEqual(d["legal"], 0.0)
        self.assertEqual(d["legal_usd"], 0.0)

    def test_writein_without_text_gets_code_label(self):
        d = self._detail([("RIAD4464", 5_000)])
        self.assertEqual(d["expense_writeins"][0]["label"], "Write-in 4464")

    def test_nothing_itemized_returns_none(self):
        self.assertIsNone(self._detail([("RIAD4340", 123)]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
