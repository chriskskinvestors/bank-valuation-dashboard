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
# ui.financials_statements decorates its statement pages with @st.fragment (both
# bare `@st.fragment` and `@st.fragment(run_every=...)`); the identity-decorator
# lambda handles both forms just like cache_data above.
_st.fragment = _st.cache_data
sys.modules.setdefault("streamlit", _st)
# ui.financials_statements does `import streamlit.components.v1` at module load.
# The balance-sheet render tests below reload that module and, in their finally
# block, restore THIS module table before a final reload — so the table must
# keep streamlit.components.v1 registered, or that reload (and running those
# tests standalone) dies with "No module named 'streamlit.components'". Register
# the components packages here so the restore target is always import-safe,
# independent of whether another test class seeded a fuller stub first.
_st_components = types.ModuleType("streamlit.components")
_st_components_v1 = types.ModuleType("streamlit.components.v1")
_st_components_v1.html = lambda *a, **k: None
_st_components.v1 = _st_components_v1
_st.components = _st_components
sys.modules.setdefault("streamlit.components", _st_components)
sys.modules.setdefault("streamlit.components.v1", _st_components_v1)


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


class TestValuationEngineTceConvention(unittest.TestCase):
    """(AUDIT-2026-07-02 P2 #24) The FDIC ROATCE engine in analysis/valuation.py
    must subtract INTAN (total intangibles) — the same house convention the
    Financials tab, Capital Dynamics, and the golden hand-check use, and the one
    CLAUDE.md declares. It previously subtracted INTANGW (goodwill only), so the
    Valuation tab and the Financials tab reported a DIFFERENT ROATCE for the same
    bank/quarter. Behavioral pin: EQTOT 1000, goodwill 100, total intangibles 300
    → TCE denominator must be 1000−300=700 (INTAN), not 1000−100=900 (INTANGW)."""

    def test_compute_roatce_subtracts_total_intangibles(self):
        from analysis.valuation import compute_roatce
        # Q4 (REPDTE 20251231) → YTD annualization ×1, so ROATCE = NI / TCE × 100.
        rec = {"NETINC": 70, "EQTOT": 1000, "INTAN": 300, "INTANGW": 100,
               "REPDTE": "20251231"}
        got = compute_roatce(rec)
        self.assertAlmostEqual(got, 70 / 700 * 100, places=2)      # 10.0% — INTAN
        self.assertNotAlmostEqual(got, 70 / 900 * 100, places=2)   # 7.78% — INTANGW

    def test_compute_roatce_4q_subtracts_total_intangibles(self):
        from analysis.valuation import compute_roatce_4q
        # 4 Q1 records (different years) → each quarterly NI = its YTD, count=4
        # (no scale-up): ttm_ni = 4×17.5 = 70, avg_tce = 1000−300 = 700 → 10.0%.
        recs = [{"NETINC": 17.5, "EQTOT": 1000, "INTAN": 300, "INTANGW": 100,
                 "REPDTE": f"{y}0331"} for y in (2025, 2024, 2023, 2022)]
        got = compute_roatce_4q(recs)
        self.assertAlmostEqual(got, 70 / 700 * 100, places=2)      # 10.0% — INTAN
        self.assertNotAlmostEqual(got, 70 / 900 * 100, places=2)   # 7.78% — INTANGW


class TestHoldcoRoatcePreferredCommonBasis(unittest.TestCase):
    """(AUDIT-2026-07-02 preferred follow-up) compute_roatce_holdco must be
    COMMON-basis: NI-available-to-common ÷ (common equity − intangibles). It used
    total NI ÷ (total equity − intangibles), overstating the denominator by
    preferred and mislabeling ROATCE (the C = common). Honors the cardinal rule:
    preferred present but unresolved → n/a."""

    def test_common_basis_subtracts_preferred_both_sides(self):
        from analysis.valuation import compute_roatce_holdco
        # total equity 1000, preferred 200, intangibles 100, NI-to-common 60,
        # total NI 72. Correct = 60 / (1000-200-100) = 60/700 = 8.571%.
        sd = {"book_value_total": 1000, "preferred_present": True,
              "preferred_stock": 200, "intangible_adjustment": 100,
              "net_income_to_common_ttm": 60, "net_income": 72}
        got = compute_roatce_holdco(sd)
        self.assertAlmostEqual(got, 60 / 700 * 100, places=2)        # common-basis
        self.assertNotAlmostEqual(got, 72 / 900 * 100, places=2)     # NOT total/total

    def test_no_preferred_falls_back_to_total_ni(self):
        from analysis.valuation import compute_roatce_holdco
        # No preferred → to-common == total; missing the to-common tag is fine.
        sd = {"book_value_total": 500, "preferred_present": False,
              "preferred_stock": 0, "intangible_adjustment": 50,
              "net_income_to_common_ttm": None, "net_income": 45}
        got = compute_roatce_holdco(sd)
        self.assertAlmostEqual(got, 45 / 450 * 100, places=2)        # 45/(500-50)

    def test_unresolved_preferred_is_na(self):
        from analysis.valuation import compute_roatce_holdco
        # Preferred present but carrying value unresolved (PNC-class) → n/a.
        sd = {"book_value_total": 800, "preferred_present": True,
              "preferred_stock": None, "intangible_adjustment": 100,
              "net_income_to_common_ttm": 50, "net_income": 55}
        self.assertIsNone(compute_roatce_holdco(sd))

    def test_preferred_present_but_no_to_common_is_na(self):
        from analysis.valuation import compute_roatce_holdco
        # Has resolvable preferred but the filer never tags NI-to-common → no
        # honest common return, so n/a beats a preferred-inflated one.
        sd = {"book_value_total": 900, "preferred_present": True,
              "preferred_stock": 100, "intangible_adjustment": 100,
              "net_income_to_common_ttm": None, "net_income": 70}
        self.assertIsNone(compute_roatce_holdco(sd))


class TestHistoricalsFailureNotCached(unittest.TestCase):
    """(AUDIT-2026-07-02 P2 #27) fetch_historical must PROPAGATE a transient
    FDIC failure instead of catching it and returning an empty DataFrame:
    st.cache_data does not cache exceptions, but it happily memoizes an empty
    frame for the full 1h TTL — a process-global blank Historicals tab. The
    caller shows the error and the next rerun retries."""

    def test_fetch_error_raises_instead_of_empty_frame(self):
        from unittest import mock
        import requests
        import ui.historicals as H
        with mock.patch.object(requests, "get",
                               side_effect=requests.exceptions.ConnectionError("FDIC down")):
            with self.assertRaises(Exception):
                H.fetch_historical(3510, quarters=4)


class TestConsensusRoatceNotRoe(unittest.TestCase):
    """(AUDIT-2026-07-02 P2 #31) the Model-vs-Consensus 'roatce' row compared
    street ROATCE against FDIC ROE — return on TOTAL equity with no intangible
    deduction, a different (lower) definition, so the Δ/verdict was
    cross-definition. It must use compute_roatce (annualized NETINC ÷
    (EQTOT − INTAN), the house convention) or skip the row."""

    @staticmethod
    def _fn_src():
        from pathlib import Path
        src = (Path(__file__).parent.parent / "ui" /
               "valuation_model.py").read_text(encoding="utf-8")
        return src.split("def _render_consensus_vs_model")[1].split("\ndef ")[0]

    def test_roatce_row_uses_computed_roatce(self):
        fn = self._fn_src()
        block = fn.split('elif key == "roatce":')[1].split("elif key ==")[0]
        self.assertIn("actual_roatce", block,
                      "roatce row must compare against a computed ROATCE")
        self.assertNotIn("actual_roe", block,
                         "roatce row must not be wired to FDIC ROE")
        self.assertIn("compute_roatce(fdic_latest)", fn)
        self.assertNotIn('fdic_latest.get("ROE")', fn,
                         "no raw-ROE actual should remain in this comparison")


class TestAudit0702AvgEquityDenominators(unittest.TestCase):
    """(AUDIT-2026-07-02 P1 #12) roatce/roate/roace are labeled and popup-
    documented as returns on AVERAGE equity but were computed on the period-END
    balance — understating the return for a bank that raised equity
    mid-period. They must use the same 2-point _avg denominator as the other
    avg-denominated kinds (netopex, costfunds, core_roae)."""

    @staticmethod
    def _block(kind):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "ui" /
               "financials_statements.py").read_text(encoding="utf-8")
        return src.split(f'if kind == "{kind}":')[1].split('if kind ==')[0]

    def test_avg_kinds_use_avg_equity(self):
        for kind in ("roatce", "roate", "roace"):
            with self.subTest(kind=kind):
                block = self._block(kind)
                self.assertIn('_avg(ci, "EQTOT")', block,
                              f"{kind} must average equity over the period")
                self.assertNotIn('_num(rec.get("EQTOT"))', block,
                                 f"{kind} must not use period-end equity")

    def test_roatce_still_total_intangibles_averaged(self):
        block = self._block("roatce")
        self.assertIn('_avg(ci, "INTAN")', block)
        self.assertNotIn('"INTANGW"', block)


class TestAudit0702QuarterlyGrowthAnnualized(unittest.TestCase):
    """(AUDIT-2026-07-02 P1 #11) the 'Annualized Growth Rates (%)' section
    showed raw QoQ in Quarterly view (~4x off vs its header). Quarterly view
    must compound QoQ to an annual rate ((1+g)^4 - 1); Annual view stays plain
    YoY. Owner decision 2026-07-06: annualize the number, not the label."""

    @staticmethod
    def _growth_block():
        from pathlib import Path
        src = (Path(__file__).parent.parent / "ui" /
               "financials_statements.py").read_text(encoding="utf-8")
        return src.split('if kind == "growth":')[1].split('if kind ==')[0]

    def test_quarterly_compounds_to_annual(self):
        block = self._growth_block()
        self.assertIn('period == "Quarterly"', block,
                      "growth must branch on the view's period mode")
        self.assertIn("ratio ** 4", block,
                      "quarterly growth must compound QoQ to an annual rate")

    def test_math_pins(self):
        # The compounding the block implements, pinned by hand: +2% QoQ
        # annualizes to +8.2432%; a plain YoY stays as-is.
        self.assertAlmostEqual((1.02 ** 4 - 1.0) * 100.0, 8.243216, places=5)

    def test_non_positive_ratio_is_na_not_number(self):
        block = self._growth_block()
        self.assertIn("ratio <= 0", block,
                      "a non-positive balance ratio must render n/a, never a "
                      "fabricated compounded rate")


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


class TestTceIntangibleConvention(unittest.TestCase):
    """Fallback TBVPS/BVPS reconstruction follows the standard tangible-common-
    equity convention: deduct goodwill + other intangibles but KEEP mortgage
    servicing rights (MSRs) in tangible equity, and use the true period-end
    common-share count (not a rounded cover-page placeholder).

    Pins the USB Q1-2026 defects: (1) the intangible resolver deducted GROSS
    goodwill+intangibles via IntangibleAssetsNetIncludingGoodwill, bundling in
    ~$3.15B of MSRs that TCE keeps; (2) shares_outstanding read a rounded
    1,600,000,000 placeholder instead of the ~1,555M actually outstanding."""

    # A recent as-of within the 1-year staleness window so extractors accept it.
    from datetime import date as _date
    AS_OF = (_date.today().replace(day=1)).isoformat()  # 1st of this month
    PRIOR = "2025-12-31"                                 # stale prior year-end
    FILED = _date.today().isoformat()

    def _facts(self, usgaap: dict) -> dict:
        return {"facts": {"us-gaap": usgaap, "dei": {}}}

    def _pt(self, end, val, form="10-Q"):
        """Point-in-time balance-sheet fact row."""
        return {"end": end, "val": val, "form": form, "filed": self.FILED}

    def _usb_like_facts(self):
        """USB-shaped: goodwill + MSR-inclusive IntangibleAssetsNetExcludingGoodwill
        rollup (same period), a stale MSR-inclusive combined tag, a rounded
        share placeholder, and precise issued/treasury at the equity date."""
        A = self.AS_OF
        return self._facts({
            "StockholdersEquity": {"units": {"USD": [self._pt(A, 65_786_000_000)]}},
            # preferred carried as par+APIC (USB's tag)
            "PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount": {
                "units": {"USD": [self._pt(A, 6_808_000_000)]}},
            "Goodwill": {"units": {"USD": [self._pt(A, 12_625_000_000)]}},
            # rollup that BUNDLES the MSR (4,799 includes the 3,152 MSR)
            "IntangibleAssetsNetExcludingGoodwill": {
                "units": {"USD": [self._pt(A, 4_799_000_000)]}},
            # stale pre-acquisition combined tag — must NOT win over fresh pieces
            "IntangibleAssetsNetIncludingGoodwill": {
                "units": {"USD": [self._pt(self.PRIOR, 17_539_000_000, "10-K")]}},
            "ServicingAssetAtFairValueAmount": {
                "units": {"USD": [self._pt(A, 3_152_000_000)]}},
            # rounded cover placeholder (exact 1.6B) instead of the real count
            "CommonStockSharesOutstanding": {
                "units": {"shares": [self._pt(self.PRIOR, 1_600_000_000, "10-K")]}},
            "CommonStockSharesIssued": {
                "units": {"shares": [self._pt(A, 2_125_725_742)]}},
            "TreasuryStockCommonShares": {
                "units": {"shares": [self._pt(A, 571_140_185)]}},
        })

    def test_msr_excluded_from_intangible_deduction(self):
        from data.sec_client import _resolve_intangible_adjustment
        facts = self._usb_like_facts()
        result = {"goodwill": 12_625_000_000, "intangibles": 4_799_000_000}
        adj = _resolve_intangible_adjustment(facts, result)
        # Deduction = goodwill 12,625 + (other-intangibles 4,799 − MSR 3,152)
        #           = 12,625 + 1,647 = 14,272M. NOT the gross 17,539M rollup
        #           (which also bundles the MSR) and NOT 17,539−3,152.
        self.assertEqual(adj, 14_272_000_000)
        self.assertEqual(result["mortgage_srv_rights"], 3_152_000_000)

    def test_rounded_share_placeholder_replaced_by_issued_minus_treasury(self):
        from unittest.mock import patch
        from data import sec_client
        facts = self._usb_like_facts()
        with patch.object(sec_client, "fetch_company_facts", return_value=facts):
            result = sec_client.get_latest_fundamentals(1)
        # 1,600,000,000 is an exact 100M multiple → placeholder → replaced by
        # issued − treasury at the equity date = 2,125,725,742 − 571,140,185.
        self.assertEqual(result["shares_outstanding"], 1_554_585_557)

    def test_usb_tbvps_matches_tce_convention(self):
        from unittest.mock import patch
        from data import sec_client
        facts = self._usb_like_facts()
        with patch.object(sec_client, "fetch_company_facts", return_value=facts):
            result = sec_client.get_latest_fundamentals(1)
        # common equity = 65,786 − 6,808 = 58,978M; TCE = 58,978 − 14,272 =
        # 44,706M; / 1,554,585,557 shares = $28.76. Reported $29.56 (2.7% off,
        # residual = DTL-on-intangibles netting, documented & intentionally not
        # guessed). The pre-fix reconstruction was $25.90 (MSR over-deducted +
        # rounded shares) — a plausible-wrong ~14% miss.
        self.assertAlmostEqual(result["tangible_book_value_per_share"], 28.76, places=1)
        # Sanity: closes most of the gap to the reported figure.
        self.assertLess(abs(result["tangible_book_value_per_share"] - 29.56) / 29.56, 0.03)

    def test_finite_lived_intangibles_never_lose_msr(self):
        """A bank that tags MSRs SEPARATELY (FITB-shape: MSR > FiniteLived
        intangibles, and other-intangibles come from FiniteLivedIntangibleAssetsNet
        which never contains MSRs) must deduct the FULL goodwill + intangibles —
        the MSR stays out of the deduction because it was never in it."""
        from data.sec_client import _resolve_intangible_adjustment
        A = self.AS_OF
        facts = self._facts({
            "Goodwill": {"units": {"USD": [self._pt(A, 9_966_000_000)]}},
            # IExGW == FiniteLived (1,233M); MSR (1,583M) is tagged separately and
            # is LARGER than the intangibles, so it cannot be bundled inside them.
            "IntangibleAssetsNetExcludingGoodwill": {
                "units": {"USD": [self._pt(A, 1_233_000_000)]}},
            "ServicingAssetAtFairValueAmount": {
                "units": {"USD": [self._pt(A, 1_583_000_000)]}},
        })
        result = {"goodwill": 9_966_000_000, "intangibles": 1_233_000_000}
        adj = _resolve_intangible_adjustment(facts, result)
        # Full deduction, MSR untouched: 9,966 + 1,233 = 11,199M.
        self.assertEqual(adj, 11_199_000_000)

    def test_no_msr_bank_deduction_unchanged(self):
        """A plain bank (goodwill + finite-lived intangibles, no MSR) deducts
        goodwill + intangibles exactly — the MSR machinery is a no-op."""
        from data.sec_client import _resolve_intangible_adjustment
        A = self.AS_OF
        facts = self._facts({
            "Goodwill": {"units": {"USD": [self._pt(A, 253_805_000)]}},
            "IntangibleAssetsNetExcludingGoodwill": {
                "units": {"USD": [self._pt(A, 145_985_000)]}},
        })
        result = {"goodwill": 253_805_000, "intangibles": 145_985_000}
        adj = _resolve_intangible_adjustment(facts, result)
        self.assertEqual(adj, 253_805_000 + 145_985_000)
        self.assertIsNone(result["mortgage_srv_rights"])

    def test_genuine_share_count_not_flagged_as_placeholder(self):
        """A precise (non-round) CommonStockSharesOutstanding is kept as-is even
        when dated at a prior period — only exact 100M multiples are placeholders
        (guards JPM: 2,696,200,000 is genuine, not a rounded 100M placeholder)."""
        from unittest.mock import patch
        from data import sec_client
        A = self.AS_OF
        facts = self._facts({
            "StockholdersEquity": {"units": {"USD": [self._pt(A, 362_400_000_000)]}},
            "CommonStockSharesOutstanding": {
                "units": {"shares": [self._pt(self.PRIOR, 2_696_200_000, "10-K")]}},
            # issued/treasury present but must NOT override the genuine count
            "CommonStockSharesIssued": {
                "units": {"shares": [self._pt(A, 3_400_000_000)]}},
            "TreasuryStockCommonShares": {
                "units": {"shares": [self._pt(A, 720_488_582)]}},
        })
        with patch.object(sec_client, "fetch_company_facts", return_value=facts):
            result = sec_client.get_latest_fundamentals(1)
        self.assertEqual(result["shares_outstanding"], 2_696_200_000)


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
        from ui.company_nav import COMPANY_NAV, _RENDERERS, _CR_RENDERERS
        # Flat sections + the Templated Financials basis dispatch via _RENDERERS;
        # the Company Reported basis dispatches via _CR_RENDERERS.
        flat = {leaf for v in COMPANY_NAV.values() if isinstance(v, list) for leaf in v}
        templated = set(COMPANY_NAV["Financials"]["Templated"])
        company = set(COMPANY_NAV["Financials"]["Company Reported"])
        self.assertEqual(flat | templated, set(_RENDERERS),
                         "flat + Templated nav entries must match _RENDERERS exactly")
        self.assertEqual(company, set(_CR_RENDERERS),
                         "Company Reported nav entries must match _CR_RENDERERS exactly")

    def test_no_duplicate_leaves_within_a_list(self):
        from ui.company_nav import COMPANY_NAV
        for sec, v in COMPANY_NAV.items():
            for lst in (v.values() if isinstance(v, dict) else [v]):
                self.assertEqual(len(lst), len(set(lst)), f"duplicate leaf in {sec}")

    def test_no_leaf_in_two_different_sections(self):
        # A leaf may repeat across Financials bases (same section), but never
        # across two DIFFERENT sections — that would make COMPANY_SECTION_OF
        # ambiguous.
        from ui.company_nav import COMPANY_NAV, _all_leaves
        seen = {}
        for sec, v in COMPANY_NAV.items():
            for leaf in set(_all_leaves(v)):
                self.assertNotIn(leaf, seen, f"{leaf} in {seen.get(leaf)} and {sec}")
                seen[leaf] = sec


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


class TestFteAdjustment(unittest.TestCase):
    """FTE NII derivation (ui/financials_statements._fte_adjustment):
    FTE adjustment = (RIAD4313 + RIAD4507) × t/(1−t), t = 0.21 federal
    statutory rate. Hand-computed pin — Banner Bank 12/31/2025 filed
    4313 = 15,532 and 4507 = 14,865 ($000):
    30,397 × 0.21/0.79 = 638,337 ÷ 79 = 8,080.215 → rounds to 8,080."""

    @classmethod
    def setUpClass(cls):
        # ui.financials_statements imports streamlit.components.v1 (via
        # ui.financial_highlights); the module-level stub only registers
        # the bare "streamlit" module.
        comp_pkg = types.ModuleType("streamlit.components")
        comp_v1 = types.ModuleType("streamlit.components.v1")
        comp_v1.html = lambda *a, **k: None
        comp_pkg.v1 = comp_v1
        sys.modules.setdefault("streamlit.components", comp_pkg)
        sys.modules.setdefault("streamlit.components.v1", comp_v1)
        from ui.financials_statements import _fte_adjustment
        cls.fte = staticmethod(_fte_adjustment)

    def test_banner_hand_check(self):
        v = self.fte(15_532, 14_865)
        self.assertAlmostEqual(v, 30_397 * 0.21 / 0.79, places=9)
        self.assertEqual(round(v), 8_080)

    def test_no_components_is_none_not_zero(self):
        # "Tax-exempt income not reported" must surface as n/a, never $0.
        self.assertIsNone(self.fte(None, None))

    def test_filed_zero_is_true_zero(self):
        # A filed $0 in both components is a real $0 FTE adjustment.
        self.assertEqual(self.fte(0.0, 0.0), 0.0)

    def test_single_component_present_computes(self):
        self.assertAlmostEqual(self.fte(15_532, None),
                               15_532 * 0.21 / 0.79, places=9)
        self.assertAlmostEqual(self.fte(None, 14_865),
                               14_865 * 0.21 / 0.79, places=9)


class TestDepositCostDetail(unittest.TestCase):
    """CD vs other-deposit cost split (Schedule RI 2.a YTD numerators +
    RC-K quarterly average balances). Values are Banner Bank's filed
    12/31/2025 call report (RSSD 352772, live-probed) — the reconciliation
    identity holds to the dollar:
      4508+0093+HK03+HK04 = 202,540 (= FDIC EDEP);
      + 4180 (3,799) + 4185 (5,774) + 4200 (0) = 212,113 = RIAD4073."""

    BANNER = [
        ("RIAD4508", 39_383),       # interest: transaction accounts (YTD)
        ("RIAD0093", 108_789),      # interest: savings incl MMDAs (YTD)
        ("RIADHK03", 35_801),       # interest: time ≤ $250k (YTD)
        ("RIADHK04", 18_567),       # interest: time > $250k (YTD)
        ("RCON3485", 2_669_745),    # avg IB transaction (quarter)
        ("RCONB563", 5_227_531),    # avg savings (quarter)
        ("RCONHK16", 1_025_301),    # avg time ≤ $250k (quarter)
        ("RCONHK17", 514_544),      # avg time > $250k (quarter)
        ("RIAD4180", 3_799),        # fed funds purchased / repo
        ("RIAD4185", 5_774),        # other borrowed money
        ("RIAD4200", 0),            # subordinated debt — true $0
        ("RIAD4073", 212_113),      # total interest expense
    ]

    def _detail(self, rows):
        from data.ffiec_client import get_deposit_cost_detail
        return get_deposit_cost_detail(
            999999, "12/31/2025", call_report_df=_ffiec_df(rows))

    def test_banner_components_extract(self):
        d = self._detail(self.BANNER)
        self.assertEqual(d["int_transaction"], 39_383)
        self.assertEqual(d["int_savings"], 108_789)
        self.assertEqual(d["int_time_le250"], 35_801)
        self.assertEqual(d["int_time_gt250"], 18_567)
        self.assertEqual(d["avg_transaction"], 2_669_745)
        self.assertEqual(d["avg_savings"], 5_227_531)
        self.assertEqual(d["avg_time_le250"], 1_025_301)
        self.assertEqual(d["avg_time_gt250"], 514_544)
        # 041 filer: no foreign-office deposit interest reported → None
        self.assertIsNone(d["int_foreign_deposits"])
        # True $0 stays 0.0, never None
        self.assertEqual(d["int_sub_debt"], 0.0)

    def test_cd_and_other_ib_sums(self):
        d = self._detail(self.BANNER)
        self.assertEqual(d["int_cds"], 35_801 + 18_567)            # 54,368
        self.assertEqual(d["int_other_ib"], 39_383 + 108_789)      # 148,172
        self.assertEqual(d["avg_cds"], 1_025_301 + 514_544)        # 1,539,845
        self.assertEqual(d["avg_other_ib"], 2_669_745 + 5_227_531)

    def test_reconciles_true_on_full_banner_row(self):
        # 202,540 deposit components = 212,113 − 3,799 − 5,774 − 0, $0 residual
        self.assertIs(self._detail(self.BANNER)["reconciles"], True)

    def test_tampered_component_flags_false(self):
        rows = [("RIADHK03", 30_801) if r[0] == "RIADHK03" else r
                for r in self.BANNER]
        self.assertIs(self._detail(rows)["reconciles"], False)

    def test_pre2017_absent_hk_codes_are_none(self):
        # HK03/HK04/HK16/HK17 begin 3/31/2017 — an older report omits them.
        rows = [r for r in self.BANNER
                if r[0] not in ("RIADHK03", "RIADHK04", "RCONHK16", "RCONHK17")]
        d = self._detail(rows)
        for k in ("int_time_le250", "int_time_gt250", "int_cds", "int_cds_usd",
                  "avg_time_le250", "avg_time_gt250", "avg_cds"):
            self.assertIsNone(d[k])
        # The split is missing the time-deposit piece — must flag, never
        # let int_other_ib pose as the whole deposit cost.
        self.assertIs(d["reconciles"], False)

    def test_reconciles_none_when_total_absent(self):
        rows = [r for r in self.BANNER if r[0] != "RIAD4073"]
        self.assertIsNone(self._detail(rows)["reconciles"])

    def test_usd_scaling(self):
        d = self._detail(self.BANNER)
        self.assertEqual(d["int_cds_usd"], 54_368_000)
        self.assertEqual(d["int_other_ib_usd"], 148_172_000)
        self.assertEqual(d["avg_savings_usd"], 5_227_531_000)
        self.assertEqual(d["int_sub_debt_usd"], 0.0)

    def test_no_split_component_returns_none(self):
        # Recon-only fields present but no numerator/denominator → no row.
        self.assertIsNone(self._detail([("RIAD4073", 212_113)]))
        self.assertIsNone(self._detail([("RIAD4340", 123)]))


class TestDepositCostRateMath(unittest.TestCase):
    """Deposit-cost rate math (ui/financials_statements._dep_quarterly_cost_rate
    / _dep_annual_cost_rate): Schedule RI numerators are calendar-YTD while
    RC-K denominators are single-quarter averages, so quarterly rates must
    de-cumulate consecutive YTD flows and FY rates must average the four
    quarterly balances. Hand-computed pins:
      quarterly — YTD Q2 30,000, YTD Q1 14,000, avg_q2 1,520,000 ($000):
        (30,000 − 14,000) ÷ 1,520,000 × 4 × 100 = 4.2105%;
      annual — FY CD interest 54,368, quarterly avgs 1,500,000 / 1,520,000 /
        1,540,000 / 1,539,845 → mean 1,524,961.25:
        54,368 ÷ 1,524,961.25 × 100 = 3.5652% (displays 3.57%)."""

    @classmethod
    def setUpClass(cls):
        # ui.financials_statements imports streamlit.components.v1 (via
        # ui.financial_highlights); the module-level stub only registers
        # the bare "streamlit" module.
        comp_pkg = types.ModuleType("streamlit.components")
        comp_v1 = types.ModuleType("streamlit.components.v1")
        comp_v1.html = lambda *a, **k: None
        comp_pkg.v1 = comp_v1
        sys.modules.setdefault("streamlit.components", comp_pkg)
        sys.modules.setdefault("streamlit.components.v1", comp_v1)
        from ui.financials_statements import (_dep_quarterly_cost_rate,
                                              _dep_annual_cost_rate,
                                              _prior_quarter_end)
        cls.qrate = staticmethod(_dep_quarterly_cost_rate)
        cls.arate = staticmethod(_dep_annual_cost_rate)
        cls.prior_qe = staticmethod(_prior_quarter_end)

    def test_quarterly_decumulation_hand_check(self):
        rate, reason = self.qrate(30_000, 14_000, 1_520_000, 2)
        self.assertIsNone(reason)
        self.assertAlmostEqual(rate, 16_000 / 1_520_000 * 4 * 100, places=9)
        self.assertAlmostEqual(rate, 4.2105, places=4)
        self.assertEqual(f"{rate:.2f}%", "4.21%")   # display rounding

    def test_q1_uses_ytd_directly(self):
        # Calendar YTD resets Jan 1 — Q1's YTD IS the discrete quarter.
        rate, reason = self.qrate(14_000, None, 1_400_000, 1)
        self.assertIsNone(reason)
        self.assertAlmostEqual(rate, 14_000 / 1_400_000 * 4 * 100, places=9)

    def test_missing_prior_is_na_never_raw_ytd(self):
        # Raw YTD ÷ one quarter's average would overstate Q2 by ~2× — the
        # function must refuse, not guess.
        rate, reason = self.qrate(30_000, None, 1_520_000, 2)
        self.assertIsNone(rate)
        self.assertEqual(reason,
                         "prior quarter not ingested — cannot de-cumulate YTD")

    def test_quarterly_missing_inputs_are_na(self):
        self.assertIsNone(self.qrate(None, 14_000, 1_520_000, 2)[0])
        self.assertIsNone(self.qrate(30_000, 14_000, None, 2)[0])
        self.assertIsNone(self.qrate(30_000, 14_000, 0, 2)[0])

    def test_annual_hand_check(self):
        avgs = [1_500_000, 1_520_000, 1_540_000, 1_539_845]
        self.assertAlmostEqual(sum(avgs) / 4.0, 1_524_961.25, places=9)
        rate, reason = self.arate(54_368, avgs)
        self.assertIsNone(reason)
        self.assertAlmostEqual(rate, 54_368 / 1_524_961.25 * 100, places=9)
        self.assertAlmostEqual(rate, 3.5652, places=4)
        self.assertEqual(f"{rate:.2f}%", "3.57%")   # display rounding

    def test_annual_incomplete_avgs_is_na(self):
        # Any missing quarterly average → n/a; never a 3-quarter mean (and
        # NEVER FY interest ÷ the Q4-only average, which would be wrong).
        rate, reason = self.arate(
            54_368, [1_500_000, None, 1_540_000, 1_539_845])
        self.assertIsNone(rate)
        self.assertEqual(reason, "incomplete quarterly average history")
        self.assertEqual(self.arate(54_368, [1_539_845])[1],
                         "incomplete quarterly average history")

    def test_annual_missing_interest_is_na(self):
        rate, reason = self.arate(
            None, [1_500_000, 1_520_000, 1_540_000, 1_539_845])
        self.assertIsNone(rate)

    def test_prior_quarter_end(self):
        self.assertEqual(self.prior_qe(pd.Timestamp("2025-06-30")),
                         pd.Timestamp("2025-03-31"))
        self.assertEqual(self.prior_qe(pd.Timestamp("2026-03-31")),
                         pd.Timestamp("2025-12-31"))
        self.assertEqual(self.prior_qe(pd.Timestamp("2025-12-31")),
                         pd.Timestamp("2025-09-30"))


class TestBalanceSheetComputedLines(unittest.TestCase):
    """SNL Balance Sheet computed-line math (ui/financials_statements._BALANCE
    + the new cell kinds), pinned to HAND-COMPUTED Banner Bank values from the
    live FDIC call report (cert 28489, 03/31/2026; the figures the build spec
    hand-checks). Drives the real render path with a streamlit stub and reads
    the produced iframe HTML, so the closure's arithmetic actually runs.

    Hand-computed pins ($000):
      reserve        = LNLSGR − LNLSNET = 11,741,404 − 11,581,052 = 160,352
      loans/deposits = 11,741,404 ÷ 13,928,915 × 100 = 84.30%
      HTM            = filed SCHA = 943,973 (not the SC−SCAF−TRADE residual)
      other-assets residual = ASSET − (CHBAL + FREPO + TRADE + SCAF + SCHA +
        LNLSNET + ORE + INTAN + MSA + BKPREM) = 16,338,071 − 15,577,230 =
        760,841  (displayed-field basis, so residual + every shown line ties
        to ASSET to the dollar; uses SCAF + SCHA, not SC which is 225 higher)
      » Total Cash & Securities = CHBAL + FREPO + TRADE + SCAF + SCHA =
        439,239 + 0 + 0 + 2,035,021 + 943,973 = 3,418,233
      common equity  = EQTOT − EQPP = 1,952,235 − 0 = 1,952,235
    """

    BANR_Q1_26 = {
        "REPDTE": "2026-03-31",
        "CHBAL": 439_239, "CHBALI": 259_081, "FREPO": 0, "TRADE": 0,
        "SCAF": 2_035_021, "SCHA": 943_973, "SC": 2_979_219,
        "LNLSGR": 11_741_404, "LNLSNET": 11_581_052, "LNATRESR": 1.3656969813831463,
        "ORE": 6_248, "INTAN": 386_768, "INTANGW": 373_121, "INTANMSR": 11_321,
        "MSA": 47_460, "BKPREM": 137_469, "ASSET": 16_338_071,
        "DEP": 13_928_915, "OTHBFHLB": 0, "SUBND": 0, "LIAB": 14_385_836,
        "EQPP": 0, "EQTOT": 1_952_235,
    }
    # Prior column so the YoY growth rows compute (only growth fields matter).
    BANR_PRIOR = {
        "REPDTE": "2025-12-31",
        "ASSET": 16_347_870, "LNLSGR": 11_764_589, "DEP": 13_812_149,
        "CHBAL": 422_640, "FREPO": 0, "SC": 2_977_863, "LNLSNET": 11_604_313,
        "ORE": 5_578, "INTAN": 387_214, "INTANGW": 373_121, "INTANMSR": 11_498,
        "MSA": 47_460, "BKPREM": 141_799, "SCAF": 2_016_261, "SCHA": 961_487,
        "CHBALI": 239_868, "EQPP": 0, "EQTOT": 1_951_461, "LIAB": 14_396_409,
        "OTHBFHLB": 150_000, "SUBND": 0, "LNATRESR": 1.3624,
    }

    def setUp(self):
        # Pure-math hand-checks (independent of render), guarding the spec.
        r = self.BANR_Q1_26
        self.assertEqual(r["LNLSGR"] - r["LNLSNET"], 160_352)          # reserve
        self.assertAlmostEqual(r["LNLSGR"] / r["DEP"] * 100, 84.2952, places=3)
        # Other Assets residual + every displayed itemized line ties to ASSET
        # exactly (displayed-field basis: SCAF + SCHA, not SC).
        itemized = (r["CHBAL"] + r["FREPO"] + r["TRADE"] + r["SCAF"] +
                    r["SCHA"] + r["LNLSNET"] + r["ORE"] + r["INTAN"] +
                    r["MSA"] + r["BKPREM"])
        self.assertEqual(r["ASSET"] - itemized, 760_841)             # other assets
        self.assertEqual((r["ASSET"] - itemized) + itemized, r["ASSET"])
        # » Total Cash & Securities = the displayed component fields, to the
        # dollar. Cash+Due (CHBAL−CHBALI) + Deposits-at-FIs (CHBALI) collapse
        # to CHBAL; securities are the shown TRADE + SCAF + SCHA. SCHA
        # (943,973) vs the SC−SCAF−TRADE residual (944,198) differ by 225
        # (0.02%) — the subtotal uses the filed SCHA that is displayed.
        cash_due = r["CHBAL"] - r["CHBALI"]
        components = (cash_due + r["CHBALI"] + r["FREPO"] + r["TRADE"] +
                      r["SCAF"] + r["SCHA"])
        self.assertEqual(components, 3_418_233)
        self.assertEqual(r["EQTOT"] - r["EQPP"], 1_952_235)           # common equity

    def _render(self, hist_rows):
        # Drive the real render path against a streamlit stub and read the
        # produced iframe HTML. The fixtures are a Dec year-end PRIOR + a
        # 03/31 quarter, so the view MUST be Quarterly for the quarter column
        # to render (Annual keeps only December periods).
        #
        # This temporarily swaps sys.modules["streamlit*"] and reloads
        # ui.financials_statements, so it MUST restore both in finally —
        # otherwise the stub leaks into later test classes (e.g. it errored
        # TestUsDomicileFilter in the full-suite run before this fix).
        import types as _t
        st = _t.ModuleType("streamlit")

        class _Ctx:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def __getattr__(self, n): return lambda *a, **k: None

        st.columns = lambda spec, **k: [
            _Ctx() for _ in range(spec if isinstance(spec, int) else len(spec))]
        st.spinner = lambda *a, **k: _Ctx()
        st.radio = lambda label, options=None, **k: "Quarterly"
        # The Trends timeframe selector: mirror "nothing clicked" by returning
        # the widget's default (render code falls back to "5Y" on a falsy value).
        st.segmented_control = lambda *a, **k: k.get("default")
        # @st.fragment on the statement pages (bare + run_every= forms).
        st.fragment = lambda *a, **k: (
            a[0] if a and callable(a[0]) else (lambda f: f))
        # reload(fs) can re-trigger a fresh `data.fdic_client` import, which
        # decorates at module load with @st.cache_data — the stub must carry
        # every import-time decorator or the stripped module leaks to later
        # classes (they then fail on the missing attribute, not this test).
        st.cache_data = st.fragment
        st.cache_resource = st.fragment
        for n in ("markdown", "caption", "write", "info", "warning", "error",
                  "divider", "plotly_chart", "html"):
            setattr(st, n, lambda *a, **k: None)
        st.session_state = {}
        comp_pkg = _t.ModuleType("streamlit.components")
        comp_v1 = _t.ModuleType("streamlit.components.v1")
        captured = []
        comp_v1.html = lambda html, **k: captured.append(html)
        comp_pkg.v1 = comp_v1
        st.components = comp_pkg
        import importlib
        keys = ("streamlit", "streamlit.components", "streamlit.components.v1")
        saved_mods = {k: sys.modules.get(k) for k in keys}
        sys.modules["streamlit"] = st
        sys.modules["streamlit.components"] = comp_pkg
        sys.modules["streamlit.components.v1"] = comp_v1
        import ui.financials_statements as fs
        fs = importlib.reload(fs)
        import data.fdic_client as fc
        saved = (fs.get_bank_info, fc.get_historical_financials)
        try:
            fs.get_bank_info = lambda t: {
                "name": "Banner Bank", "fdic_cert": 28489, "cik": None}
            fc.get_historical_financials = (
                lambda cert, quarters=36: pd.DataFrame([dict(r) for r in hist_rows]))
            fs.render_balance_sheet("BANR")
        finally:
            fs.get_bank_info, fc.get_historical_financials = saved
            # Restore the module table and reload fs against the real streamlit
            # so this test does not poison any class that runs after it.
            for k in keys:
                if saved_mods[k] is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = saved_mods[k]
            importlib.reload(fs)
        return captured[0] if captured else ""

    def test_computed_lines_match_hand_values(self):
        # The Q1-2026 column renders (Quarterly view). Values are asserted in
        # the table's COMPACT form (the engine renders $thousands compact, same
        # as the Income Statement tab); the raw $000 figures still appear in the
        # click-through where the source field is shown verbatim (HTM/SCHA). We
        # avoid asserting the popup formula op strings, which use Unicode glyphs
        # (− U+2212, ÷, ×) and are encoding-fragile; the displayed value is what
        # pins the math here, and setUp pins the arithmetic independently.
        h = self._render([dict(self.BANR_PRIOR), dict(self.BANR_Q1_26)])
        # reserve = LNLSGR − LNLSNET = 160,352 ($000) → $160.4M.
        self.assertIn("Loan Loss Reserve", h)
        self.assertIn("$160.4M", h)
        # loans/deposits = LNLSGR ÷ DEP × 100 = 84.30%.
        self.assertIn("Loans / Deposits", h)
        self.assertIn("84.30%", h)
        # HTM = filed SCHA 943,973 → $944.0M; source field + raw value in popup.
        self.assertIn("FDIC field SCHA", h)
        self.assertIn("943,973", h)
        # Other Assets residual = 760,841 ($000) → $760.8M.
        self.assertIn("Other Assets", h)
        self.assertIn("$760.8M", h)
        # » Total Cash & Securities = 3,418,233 ($000) → $3.42B.
        self.assertIn("$3.42B", h)
        # Asset Growth, Quarterly view: QoQ compounded to an annual rate
        # (AUDIT-2026-07-02 P1 #11) — ((16,338,071 ÷ 16,347,870)^4 − 1) × 100
        # = −0.24% (raw QoQ would be −0.06%).
        self.assertIn("-0.24%", h)
        # n/a lines carry a reason (never $0).
        self.assertIn("EQUPTOT is not AOCI", h)

    def test_negative_residual_renders_na(self):
        bad = dict(self.BANR_Q1_26)
        bad["ASSET"] = 1_000_000   # $1.0B — below the ~$15.6B itemized sum
        h = self._render([dict(self.BANR_PRIOR), bad])
        # Negative residual → n/a + flag in the click-through, never a negative
        # plug shown as the cell value.
        self.assertIn("itemized lines exceed total", h)


class TestPreferredStockExcludedFromBookValue(unittest.TestCase):
    """Book / tangible-book per share are per-COMMON-share, so preferred equity
    must be subtracted from total StockholdersEquity first.

    The bug shipped once: bvps/tbvps used total equity, overstating both for
    every bank with preferred outstanding. FBIZ Q1-2026 (real numbers below)
    read tbvps $44.11 instead of its earnings-release $42.68 — a CARDINAL-RULE
    plausible-wrong number on the Overview 'TBV / Share' and 'P/TBV'.
    """

    # FBIZ 2026-03-31 (CIK 1521951): total equity $380.08M includes $11.992M
    # preferred; common shares 8,343,519; goodwill $10.7M; combined
    # goodwill+intangibles (IntangibleAssetsNetIncludingGoodwill) $12.011M.
    END = "2026-03-31"
    FILED = "2026-05-01"

    @staticmethod
    def _facts(equity, shares, *, goodwill=None, incl_intang=None,
               preferred_value=None, preferred_shares=None):
        """Build a slim-shaped companyfacts blob. Only the instant balance-sheet
        concepts bvps/tbvps read; None args omit the tag entirely."""
        end, filed = TestPreferredStockExcludedFromBookValue.END, \
            TestPreferredStockExcludedFromBookValue.FILED

        def usd(v):
            return {"units": {"USD": [
                {"end": end, "val": v, "form": "10-Q", "filed": filed}]}}

        def sh(v):
            return {"units": {"shares": [
                {"end": end, "val": v, "form": "10-Q", "filed": filed}]}}

        ug = {
            "StockholdersEquity": usd(equity),
            "CommonStockSharesOutstanding": sh(shares),
        }
        if goodwill is not None:
            ug["Goodwill"] = usd(goodwill)
        if incl_intang is not None:
            ug["IntangibleAssetsNetIncludingGoodwill"] = usd(incl_intang)
        if preferred_value is not None:
            ug["PreferredStockValue"] = usd(preferred_value)
        if preferred_shares is not None:
            ug["PreferredStockSharesOutstanding"] = sh(preferred_shares)
        return {"facts": {"us-gaap": ug, "dei": {}}}

    def _fundamentals(self, facts):
        from unittest.mock import patch
        # A sibling test class may have swapped sys.modules["streamlit"] for a
        # bare stub lacking cache_data; restore the decorator shim so importing
        # data.sec_client (module-level @st.cache_data) succeeds under any order.
        import sys
        if not hasattr(sys.modules.get("streamlit"), "cache_data"):
            sys.modules["streamlit"] = _st
        from data import sec_client
        with patch.object(sec_client, "fetch_company_facts", return_value=facts):
            return sec_client.get_latest_fundamentals(1)

    def test_preferred_subtracted_common_based(self):
        # FBIZ real numbers → ties the Q1-2026 earnings release.
        r = self._fundamentals(self._facts(
            equity=380_080_000, shares=8_343_519,
            goodwill=10_700_000, incl_intang=12_011_000,
            preferred_value=11_992_000, preferred_shares=12_500))
        self.assertTrue(r["preferred_present"])
        self.assertEqual(r["preferred_stock"], 11_992_000)
        self.assertEqual(r["common_equity"], 368_088_000)
        # bvps = (380.08M − 11.992M) / 8,343,519 = $44.12
        self.assertAlmostEqual(r["book_value_per_share"], 44.1166, places=3)
        # tbvps = (368.088M − 12.011M) / 8,343,519 = $42.68 (earnings release)
        self.assertAlmostEqual(r["tangible_book_value_per_share"], 42.6771, places=3)
        # Downstream P/TBV at $64.44 → 1.51 (CapIQ).
        from analysis.valuation import compute_ptbv_ratio
        self.assertAlmostEqual(
            compute_ptbv_ratio(64.44, r["tangible_book_value_per_share"]),
            1.51, places=2)

    def test_no_preferred_unchanged(self):
        # No PreferredStock* tag at all → common_equity == equity, values are
        # the pure equity-based figures (unchanged from before the fix).
        r = self._fundamentals(self._facts(
            equity=4_082_127_000, shares=67_292_503,
            goodwill=1_000_000_000, incl_intang=1_070_470_000))
        self.assertFalse(r["preferred_present"])
        self.assertEqual(r["preferred_stock"], 0.0)
        self.assertEqual(r["common_equity"], 4_082_127_000)
        self.assertAlmostEqual(r["book_value_per_share"], 60.6624, places=3)
        self.assertAlmostEqual(
            r["tangible_book_value_per_share"], 44.7547, places=3)

    def test_preferred_present_but_unresolvable_renders_na(self):
        # Preferred SHARES outstanding prove the filer has preferred, but no
        # PreferredStockValue* tag resolves → cardinal rule: n/a, never a
        # preferred-inflated "common" figure.
        r = self._fundamentals(self._facts(
            equity=380_080_000, shares=8_343_519,
            goodwill=10_700_000, incl_intang=12_011_000,
            preferred_value=None, preferred_shares=12_500))
        self.assertTrue(r["preferred_present"])
        self.assertIsNone(r["preferred_stock"])
        self.assertIsNone(r["common_equity"])
        self.assertIsNone(r["book_value_per_share"])
        self.assertIsNone(r["tangible_book_value_per_share"])

    def test_stale_preferred_value_ignored_shares_trigger_na(self):
        # A PreferredStockValue tag exists but is abandoned (>1yr stale, like
        # FBIZ's 2022 PreferredStockValueOutstanding): the staleness guard drops
        # it, current preferred shares still flag preferred → n/a, not a stale
        # or preferred-inclusive number.
        facts = self._facts(
            equity=380_080_000, shares=8_343_519,
            goodwill=10_700_000, incl_intang=12_011_000,
            preferred_shares=12_500)
        facts["facts"]["us-gaap"]["PreferredStockValue"] = {"units": {"USD": [
            {"end": "2022-12-31", "val": 12_500_000,
             "form": "10-K", "filed": "2023-03-01"}]}}
        r = self._fundamentals(facts)
        self.assertTrue(r["preferred_present"])
        self.assertIsNone(r["preferred_stock"])
        self.assertIsNone(r["tangible_book_value_per_share"])

    def test_par_zero_preferred_value_renders_na(self):
        # PNC-style: PreferredStockValue reads exactly $0 (preferred carried in
        # an untagged APIC line) with real preferred shares outstanding. A $0
        # deduction would overstate common book — treat 0 as unresolved → n/a.
        facts = self._facts(
            equity=63_630_000_000, shares=397_000_000,
            goodwill=10_987_000_000, incl_intang=12_500_000_000,
            preferred_value=0, preferred_shares=58_000)
        r = self._fundamentals(facts)
        self.assertTrue(r["preferred_present"])
        self.assertIsNone(r["preferred_stock"])
        self.assertIsNone(r["tangible_book_value_per_share"])

    def test_preferred_apic_carrying_value_used(self):
        # BAC-style: no PreferredStockValue, carrying value tagged as par+APIC
        # under PreferredStockIncludingAdditionalPaidInCapital.
        from unittest.mock import patch
        from data import sec_client
        facts = self._facts(
            equity=300_668_000_000, shares=7_129_908_032,
            goodwill=68_951_000_000, incl_intang=70_821_000_000,
            preferred_shares=3_951_164)
        facts["facts"]["us-gaap"]["PreferredStockIncludingAdditionalPaidInCapital"] = {
            "units": {"USD": [{"end": self.END, "val": 25_992_000_000,
                               "form": "10-Q", "filed": self.FILED}]}}
        with patch.object(sec_client, "fetch_company_facts", return_value=facts):
            r = sec_client.get_latest_fundamentals(1)
        self.assertEqual(r["preferred_stock"], 25_992_000_000)
        self.assertEqual(r["common_equity"], 274_676_000_000)
        # tbvps = (274.676B − 70.821B) / 7,129,908,032 = $28.59
        self.assertAlmostEqual(r["tangible_book_value_per_share"], 28.59, places=1)


class TestAudit0702P0FieldMislabels(unittest.TestCase):
    """2026-07-02 audit P0 #1-2: wrong FDIC field wired under a right label.

    #1 Income Statement showed TRADE (balance-sheet trading-account ASSET,
       ~$B) as "Trading account income" and subtracted it in the "Other
       non-interest income" residual, driving that residual hugely negative
       for any bank with a trading book. The income field is ITRADE.
    #2 Historical Financials showed ELNATR (provision for credit losses) under
       "Net Charge-Offs ($K)". The net-charge-off dollar field is NTLNLS;
       reserve-building banks showed provision as a fake, larger NCO.
    """

    def test_is_trading_row_uses_income_field_not_balance_sheet_asset(self):
        from ui.financials_statements import _INCOME
        noni = dict(_INCOME)["Non-Interest Income"]
        trad = [r for r in noni if r[0] == "Trading account income"]
        self.assertEqual(len(trad), 1)
        # field (index 2) must be ITRADE (RI trading revenue), never TRADE.
        self.assertEqual(trad[0][2], "ITRADE")

    def test_no_balance_sheet_trade_field_leaks_into_is_income_rows(self):
        from ui.financials_statements import _INCOME
        noni = dict(_INCOME)["Non-Interest Income"]
        for row in noni:
            fields = row[2:]  # everything after (label, kind)
            self.assertNotIn(
                "TRADE", fields,
                f"balance-sheet TRADE leaked into IS income row {row[0]!r}")

    def test_itrade_is_actually_fetched(self):
        from data.fdic_client import _BASE_FINANCIALS_FIELDS
        # The row would render '—' forever if the field were never requested.
        self.assertIn("ITRADE", _BASE_FINANCIALS_FIELDS)

    def test_historicals_nco_row_uses_ntlnls_not_provision(self):
        from ui.historicals import HIST_METRICS, HIST_FIELDS
        nco = [m for m in HIST_METRICS if m[1] == "Net Charge-Offs ($K)"]
        self.assertEqual(len(nco), 1)
        self.assertEqual(nco[0][0], "NTLNLS")  # not ELNATR
        self.assertIn("NTLNLS", HIST_FIELDS)

    def test_annualize_nco_takes_q4_ytd_ntlnls(self):
        # Two quarters of FY2025 (BANR actuals, cert 28489, probed live):
        #   Q2 YTD NTLNLS 3,770 / ELNATR 7,934; Q4 YTD NTLNLS 6,882 / ELNATR 13,045.
        # Annual NCO must be the Q4 YTD net-charge-off (6,882) — NOT provision.
        from ui.historicals import _annualize
        df = pd.DataFrame([
            {"REPDTE": 20251231, "NTLNLS": 6882, "ELNATR": 13045, "NETINC": 100},
            {"REPDTE": 20250630, "NTLNLS": 3770, "ELNATR": 7934, "NETINC": 50},
        ])
        ann = _annualize(df)
        row = ann[ann["Period"] == "2025"].iloc[0]
        self.assertEqual(row["NTLNLS"], 6882)
        # provision is no longer promoted into the annual (NCO) frame.
        self.assertNotIn("ELNATR", ann.columns)


if __name__ == "__main__":
    unittest.main(verbosity=2)
