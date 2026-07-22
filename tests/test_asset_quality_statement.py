"""
Asset Quality Detail statement rebuild (SNL plan §4, 2026-07-13).

Pins:
  • the fratio field-expression math against HAND-COMPUTED values from the
    live FDIC probe of TCBK (cert 21943) 12/31/2025 — the same numbers
    cross-checked against the owner's CapIQ screenshot;
  • numerator/denominator None-semantics (skip-absent vs strict vs
    negative-defaults-0) — the n/a-over-guess contract;
  • every FDIC field named in _ASSET_QUALITY is actually FETCHED by
    fdic_client (the bug class where a spec row silently renders dead
    because the field was never requested);
  • credit_quality_history's newest-wins merge and its cache contract
    (complete walks cache; a fetch failure must NOT bake a 30-day gap).
"""
import sys
import types
import unittest

# Stub streamlit before importing ui modules (same pattern as
# test_audit_regressions — @st.cache_data/@st.fragment at module load).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
_st_components = types.ModuleType("streamlit.components")
_st_components_v1 = types.ModuleType("streamlit.components.v1")
_st_components_v1.html = lambda *a, **k: None
_st_components.v1 = _st_components_v1
_st.components = _st_components
sys.modules.setdefault("streamlit", _st)
sys.modules.setdefault("streamlit.components", _st_components)
sys.modules.setdefault("streamlit.components.v1", _st_components_v1)


# TCBK (Tri Counties Bank, cert 21943) 12/31/2025 — live FDIC probe values.
_TCBK = {
    "NALNLS": 64137, "RSLNLTOT": 839, "NCLNLS": 64219, "P3LNLS": 12514,
    "P9LNLS": 82, "ORE": 6245, "LNATRES": 125762, "ELNATR": 12063,
    "NTLNLS": 9922, "DRLNLS": 11051, "CRLNLS": 1129,
    "LNLSGR": 7113782, "ASSET": 9820725, "EQTOT": 1362147, "INTAN": 315553,
}


class TestExprHelpers(unittest.TestCase):

    def test_parse(self):
        from ui.financials_statements import _expr_terms
        self.assertEqual(_expr_terms("A+B-C"), [(1, "A"), (1, "B"), (-1, "C")])
        self.assertEqual(_expr_terms("ASSET"), [(1, "ASSET")])

    def test_npas_over_assets_hand_computed(self):
        from ui.financials_statements import _eval_expr
        nv, _, nok = _eval_expr(_TCBK.get, "NALNLS+RSLNLTOT+ORE", True)
        dv, _, dok = _eval_expr(_TCBK.get, "ASSET", False)
        self.assertTrue(nok and dok)
        # 64,137 + 839 + 6,245 = 71,221; ÷ 9,820,725 × 100 = 0.7252%
        self.assertEqual(nv, 71221)
        self.assertAlmostEqual(nv / dv * 100, 0.7252, places=4)

    def test_texas_ratio_hand_computed(self):
        from ui.financials_statements import _eval_expr
        nv, _, _ = _eval_expr(_TCBK.get, "NALNLS+RSLNLTOT+ORE+P9LNLS", True)
        dv, _, dok = _eval_expr(_TCBK.get, "EQTOT-INTAN+LNATRES", False)
        self.assertTrue(dok)
        # 71,303 ÷ (1,362,147 − 315,553 + 125,762 = 1,172,356) = 6.08203%
        self.assertEqual(nv, 71303)
        self.assertEqual(dv, 1172356)
        self.assertAlmostEqual(nv / dv * 100, 6.08203, places=4)

    def test_reserves_over_npls_hand_computed(self):
        from ui.financials_statements import _eval_expr
        nv, _, _ = _eval_expr(_TCBK.get, "LNATRES", True)
        dv, _, _ = _eval_expr(_TCBK.get, "NALNLS+RSLNLTOT", False)
        # 125,762 ÷ 64,976 = 193.55%
        self.assertAlmostEqual(nv / dv * 100, 193.5514, places=3)

    def test_numerator_skips_absent_denominator_strict(self):
        from ui.financials_statements import _eval_expr
        rec = {"A": 10, "B": None, "C": 5}
        nv, _, nok = _eval_expr(rec.get, "A+B", True)     # absent ≠ $0: skip
        self.assertEqual((nv, nok), (10, True))
        _, _, nok2 = _eval_expr({"A": None}.get, "A", True)
        self.assertFalse(nok2)                            # nothing present → n/a
        _, _, dok = _eval_expr(rec.get, "A+B", False)     # strict positive term
        self.assertFalse(dok)
        dv, _, dok2 = _eval_expr(rec.get, "A-B+C", False)  # None NEGATIVE → 0
        self.assertEqual((dv, dok2), (15, True))

    def test_nco_reconciles_gross_minus_recoveries(self):
        # DRLNLS − CRLNLS = NTLNLS as filed (11,051 − 1,129 = 9,922) — the
        # spec shows all three; they must reconcile on the face of the table.
        self.assertEqual(_TCBK["DRLNLS"] - _TCBK["CRLNLS"], _TCBK["NTLNLS"])


class TestSpecFieldsFetched(unittest.TestCase):
    """Every FDIC field a spec row names must be in the client's fetch set."""

    def _fields_of(self, spec):
        out = set()
        for _sec, rows in spec:
            for row in rows:
                kind, args = row[1], row[2:]
                if kind in ("dollar", "pct"):
                    out.add(args[0])
                elif kind in ("sum", "diff", "ratio"):
                    out.update(args)
                elif kind == "flow":
                    out.update(a for a in args if a)
                elif kind == "fratio":
                    from ui.financials_statements import _expr_terms
                    for expr in args:
                        out.update(f for _s, f in _expr_terms(expr))
                elif kind == "flowratio":
                    for pair in args:
                        out.update(a for a in pair if a)
                elif kind == "residual":
                    out.update(args)
                elif kind == "yield":
                    out.update(args)
        return out

    def test_asset_quality_fields_are_fetched(self):
        from ui.financials_statements import _ASSET_QUALITY
        from data.fdic_client import _BASE_FINANCIALS_FIELDS
        from config import get_fdic_fields
        have = _BASE_FINANCIALS_FIELDS | get_fdic_fields()
        missing = self._fields_of(_ASSET_QUALITY) - have
        self.assertEqual(missing, set(),
                         f"spec names FDIC fields the client never fetches: {missing}")

    def test_capital_adequacy_fields_are_fetched(self):
        from ui.financials_statements import _CAPITAL_ADEQUACY
        from data.fdic_client import _BASE_FINANCIALS_FIELDS
        from config import get_fdic_fields
        have = _BASE_FINANCIALS_FIELDS | get_fdic_fields()
        missing = self._fields_of(_CAPITAL_ADEQUACY) - have
        self.assertEqual(missing, set(),
                         f"spec names FDIC fields the client never fetches: {missing}")

    def test_sweep_spec_fields_are_fetched(self):
        from ui.financials_statements import (_AQ_BY_LOAN_TYPE,
                                              _DEPOSIT_LOAN_COMP,
                                              _DEPOSIT_TRENDS_TABLE,
                                              _PORTFOLIO, _CAPITAL_STRUCTURE)
        from data.fdic_client import _BASE_FINANCIALS_FIELDS
        from config import get_fdic_fields
        have = _BASE_FINANCIALS_FIELDS | get_fdic_fields()
        for spec in (_AQ_BY_LOAN_TYPE, _DEPOSIT_LOAN_COMP,
                     _DEPOSIT_TRENDS_TABLE, _PORTFOLIO, _CAPITAL_STRUCTURE):
            missing = self._fields_of(spec) - have
            self.assertEqual(missing, set(),
                             f"spec names FDIC fields the client never fetches: {missing}")

    def test_equity_components_identity_tcbk_banr(self):
        # Live probe 2026-07-13: EQPP + EQCS + EQSUR + EQUPTOT = EQTOT to the
        # dollar for both banks — pins the Equity Components tree design.
        for eq in ({"EQPP": 0, "EQCS": 5858, "EQSUR": 785019,
                    "EQUPTOT": 571270, "EQTOT": 1362147},        # TCBK
                   {"EQPP": 0, "EQCS": 2851, "EQSUR": 1627343,
                    "EQUPTOT": 321267, "EQTOT": 1951461}):       # BANR
            self.assertEqual(eq["EQPP"] + eq["EQCS"] + eq["EQSUR"]
                             + eq["EQUPTOT"], eq["EQTOT"])

    def test_securities_tree_residual_tcbk(self):
        # TCBK 12/31/2025 probe: orthogonal leaves + residual = SC; residual
        # equals equity-not-for-trading + other-domestic rounding (2,693 SCEQNFT
        # + 4,958 SCODOT shown as its own row → residual 2,693).
        leaves = {"SCUST": 0, "SCASPNSUM": 0, "SCMUNI": 222250,
                  "SCMTGBK": 1325747, "SCABS": 106401, "SCSFP": 163119,
                  "SCODOT": 4958, "SCFORD": 0}
        sc = 1825168
        residual = sc - sum(leaves.values())
        self.assertEqual(residual, 2693)   # = SCEQNFT as filed

    def test_by_loan_type_residual_excludes_of_which_rows(self):
        # The residual row must subtract ONLY leaf categories: HELOC ⊂ 1-4 fam
        # and OO/NOO ⊂ CRE would double-subtract and turn the residual negative.
        from ui.financials_statements import _BYLT_LEAVES
        for overlap in ("RELOC", "RENROW", "RENROT"):
            self.assertNotIn(overlap, _BYLT_LEAVES)

    def test_by_loan_type_leaf_sum_reconciles_tcbk(self):
        # TCBK 12/31/2025 live probe (values pulled from the FDIC API on
        # 2026-07-13): the leaf categories sum EXACTLY to NALNLS — pins the
        # leaf-set design (no overlap, no gap except the residual's ag).
        na = {"NARECONS": 650, "NARERES": 11720, "NAREMULT": 435,
              "NARENRES": 14822, "NAREAG": 31615, "NACI": 3976, "NACRCD": 0,
              "NAAUTO": 456, "NACONOTH": 3, "NALS": 0, "NAOTHLN": 460}
        self.assertEqual(sum(na.values()), 64137)   # = NALNLS as filed

    def test_tangible_equity_ratio_hand_computed(self):
        # TCBK 12/31/2025: (1,362,147 − 315,553) ÷ (9,820,725 − 315,553)
        # = 1,046,594 ÷ 9,505,172 = 11.0108%
        from ui.financials_statements import _eval_expr
        nv, _, _ = _eval_expr(_TCBK.get, "EQTOT-INTAN", False)
        dv, _, _ = _eval_expr(_TCBK.get, "ASSET-INTAN", False)
        self.assertEqual((nv, dv), (1046594, 9505172))
        self.assertAlmostEqual(nv / dv * 100, 11.0108, places=4)


class TestCompositionVariantMerge(unittest.TestCase):
    """Guarded fold of a filer's own 10-K/10-Q category wording variants
    (2026-07-14). Values are the live TCBK quarterly loan-composition probe:
    the 10-Qs tag "Commercial real estate" / "Consumer" while the FY 10-K tags
    "Total commercial real estate loans" / "Total consumer loans", which split
    each category into two half-blank rows before this merge."""

    PERIODS = ["2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31"]

    def _merge(self, rows):
        """rows: {norm_label: {period: value}} → (order, display, per_val)."""
        from ui.financials_statements import _comp_merge_variants
        order = list(rows)
        display = {k: k for k in rows}
        per_val = {k: dict(v) for k, v in rows.items()}
        return _comp_merge_variants(order, display, per_val, self.PERIODS)

    def test_variant_key_folds_only_identity_neutral_wording(self):
        from ui.financials_statements import _comp_variant_key as vk
        self.assertEqual(vk("commercial real estate"),
                         vk("total commercial real estate loans"))
        self.assertEqual(vk("consumer"), vk("total consumer loans"))
        # Genuinely different categories must never collide.
        self.assertNotEqual(vk("time deposits"), vk("demand deposits"))
        self.assertNotEqual(vk("commercial real estate"), vk("commercial"))
        self.assertNotEqual(vk("commercial and industrial"), vk("commercial"))
        # Nothing distinctive left → never mergeable.
        self.assertEqual(vk("total loans"), frozenset())

    def test_disjoint_variants_merge_and_keep_newest_wording(self):
        order, display, per_val = self._merge({
            "commercial real estate": {"2024-06-30": 4.46e9, "2024-09-30": 4.49e9,
                                       "2025-03-31": 4.63e9},
            "total commercial real estate loans": {"2024-12-31": 4.58e9},
        })
        self.assertEqual(len(order), 1)                  # one row, not two
        key = order[0]
        # Every quarter now populated — the 10-K cell filled the hole.
        self.assertEqual([per_val[key].get(p) for p in self.PERIODS],
                         [4.46e9, 4.49e9, 4.58e9, 4.63e9])
        # Survivor keeps the wording that reaches the NEWEST period.
        self.assertEqual(display[key], "commercial real estate")

    def test_absorbed_row_may_carry_the_newer_wording(self):
        # Reverse case: the 10-K wording reaches the newest period, so it wins.
        order, display, per_val = self._merge({
            "consumer": {"2024-06-30": 1.30e9},
            "total consumer loans": {"2025-03-31": 1.28e9},
        })
        self.assertEqual(len(order), 1)
        self.assertEqual(display[order[0]], "total consumer loans")

    def test_shared_populated_period_blocks_merge(self):
        # CARDINAL GUARD: a subtotal disclosed ALONGSIDE its component in the
        # same period is two real lines — merging would hide one.
        order, display, per_val = self._merge({
            "commercial real estate": {"2024-06-30": 4.46e9},
            "total commercial real estate loans": {"2024-06-30": 4.58e9},
        })
        self.assertEqual(len(order), 2)                  # stayed separate
        self.assertEqual(per_val["commercial real estate"]["2024-06-30"], 4.46e9)
        self.assertEqual(
            per_val["total commercial real estate loans"]["2024-06-30"], 4.58e9)

    def test_merge_never_overwrites_an_existing_value(self):
        order, _display, per_val = self._merge({
            "consumer": {"2024-06-30": 1.30e9, "2024-09-30": 1.28e9},
            "total consumer loans": {"2024-12-31": 1.28e9},
        })
        self.assertEqual(per_val[order[0]]["2024-06-30"], 1.30e9)   # untouched
        self.assertEqual(per_val[order[0]]["2024-12-31"], 1.28e9)   # filled

    def test_unrelated_categories_never_merge(self):
        order, _d, _p = self._merge({
            "time deposits": {"2024-06-30": 1.0e9},
            "demand deposits": {"2024-09-30": 2.0e9},
        })
        self.assertEqual(len(order), 2)


class TestCompositionMemberMerge(unittest.TestCase):
    """TIER 1 of the composition fold: identical XBRL member QName (2026-07-14).
    The member is the filer's own category identity and is stable across its
    10-K and 10-Qs, so it folds wording the token heuristic can't — BANR tags
    'Small balance CRE' quarterly and 'Small Balance Commercial Real Estate
    Loans' in the 10-K under one member."""

    PERIODS = ["2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31"]

    def _merge(self, rows):
        """rows: {norm_label: (member, {period: value})}."""
        from ui.financials_statements import _comp_merge_variants
        order = list(rows)
        display = {k: k for k in rows}
        member = {k: v[0] for k, v in rows.items()}
        per_val = {k: dict(v[1]) for k, v in rows.items()}
        return _comp_merge_variants(order, display, per_val, self.PERIODS, member)

    def test_same_member_folds_wording_the_token_tier_cannot(self):
        # These token sets DIFFER ({small, cre} vs {small, commercial, real,
        # estate}), so only the member tier can fold them.
        from ui.financials_statements import _comp_variant_key as vk
        self.assertNotEqual(vk("small balance cre"),
                            vk("small balance commercial real estate loans"))
        order, display, per_val = self._merge({
            "small balance cre": ("banr:SmallBalanceCREMember",
                                  {"2024-06-30": 1.23e9, "2025-03-31": 1.22e9}),
            "small balance commercial real estate loans":
                ("banr:SmallBalanceCREMember", {"2024-12-31": 1.21e9}),
        })
        self.assertEqual(len(order), 1)
        self.assertEqual([per_val[order[0]].get(p) for p in self.PERIODS],
                         [1.23e9, None, 1.21e9, 1.22e9])
        self.assertEqual(display[order[0]], "small balance cre")   # newest wording

    def test_different_members_never_merge_even_with_same_wording(self):
        # The safety win over pure-wording matching: a filer using two DISTINCT
        # members has two distinct categories, however alike the labels read.
        order, _display, _per_val = self._merge({
            "commercial real estate": ("banr:OwnerOccupiedMember",
                                       {"2024-06-30": 1.0e9}),
            "total commercial real estate loans":
                ("banr:InvestmentPropertiesMember", {"2024-12-31": 2.0e9}),
        })
        self.assertEqual(len(order), 2)

    def test_member_tier_still_honors_the_shared_period_guard(self):
        order, _display, _per_val = self._merge({
            "small balance cre": ("banr:SmallBalanceCREMember",
                                  {"2024-06-30": 1.23e9}),
            "small balance commercial real estate loans":
                ("banr:SmallBalanceCREMember", {"2024-06-30": 1.21e9}),
        })
        self.assertEqual(len(order), 2)      # both populated in one period

    def test_missing_member_falls_back_to_the_wording_tier(self):
        # Legacy cached rows (pre-v2) carry no member — the token tier still
        # folds the clean TCBK-style case.
        order, _display, per_val = self._merge({
            "commercial real estate": (None, {"2024-06-30": 4.46e9}),
            "total commercial real estate loans": (None, {"2024-12-31": 4.58e9}),
        })
        self.assertEqual(len(order), 1)
        self.assertEqual(per_val[order[0]]["2024-12-31"], 4.58e9)


class TestCreditQualityHistory(unittest.TestCase):
    """Merge + cache contract of credit_quality_history (stubbed I/O)."""

    def setUp(self):
        import data.xbrl_dimensional as xd
        self.xd = xd
        self._orig = (xd._list_filings, xd.fetch_dimensional_facts,
                      xd.extract_credit_quality)
        self.cache = {}
        import data.cache as dcache
        self._cache_orig = (dcache.get, dcache.put)
        dcache.get = lambda k: self.cache.get(k)
        dcache.put = lambda k, v: self.cache.__setitem__(k, v)

    def tearDown(self):
        (self.xd._list_filings, self.xd.fetch_dimensional_facts,
         self.xd.extract_credit_quality) = self._orig
        import data.cache as dcache
        dcache.get, dcache.put = self._cache_orig

    def _wire(self, filings, bundles, breakdowns):
        self.xd._list_filings = lambda cik, forms, n: filings[:n]
        self.xd.fetch_dimensional_facts = lambda cik, acc: bundles.get(acc)
        self.xd.extract_credit_quality = lambda facts: breakdowns.get(facts.get("id"))

    def test_merge_newest_wins_and_missing_period_absent(self):
        filings = [
            {"form": "10-K", "accession": "A3", "filed": "2026-02-20", "report_date": "2025-12-31"},
            {"form": "10-K", "accession": "A2", "filed": "2025-02-20", "report_date": "2024-12-31"},
            {"form": "10-K", "accession": "A1", "filed": "2024-02-20", "report_date": "2023-12-31"},
        ]
        bundles = {a: {"facts": {"id": a}, "instance_url": f"http://x/{a}"}
                   for a in ("A3", "A2", "A1")}
        breakdowns = {
            # A2 re-states the same period end as A1 — the NEWER filing (A2,
            # walked later) must win.
            "A3": {"as_of": "2025-12-31", "total_by_grade": {"pass": 3e9}},
            "A2": {"as_of": "2023-12-31", "total_by_grade": {"pass": 2e9}},
            "A1": {"as_of": "2023-12-31", "total_by_grade": {"pass": 1e9}},
        }
        self._wire(filings, bundles, breakdowns)
        out = self.xd.credit_quality_history(123, quarterly=False)
        self.assertEqual(set(out), {"2025-12-31", "2023-12-31"})
        self.assertEqual(out["2023-12-31"]["total_by_grade"]["pass"], 2e9)
        self.assertEqual(out["2023-12-31"]["source"]["accession"], "A2")
        self.assertEqual(len(self.cache), 1)   # complete walk → cached

    def test_fetch_failure_not_cached(self):
        filings = [
            {"form": "10-K", "accession": "B2", "filed": "2026-02-20", "report_date": "2025-12-31"},
            {"form": "10-K", "accession": "B1", "filed": "2025-02-20", "report_date": "2024-12-31"},
        ]
        bundles = {"B2": {"facts": {"id": "B2"}, "instance_url": "http://x/B2"}}  # B1 fetch fails
        breakdowns = {"B2": {"as_of": "2025-12-31", "total_by_grade": {"pass": 1e9}}}
        self._wire(filings, bundles, breakdowns)
        out = self.xd.credit_quality_history(123, quarterly=False)
        self.assertEqual(set(out), {"2025-12-31"})   # partial result served...
        self.assertEqual(self.cache, {})             # ...but NOT cached

    def test_untagged_filing_is_cacheable_absence(self):
        filings = [{"form": "10-K", "accession": "C1",
                    "filed": "2026-02-20", "report_date": "2025-12-31"}]
        bundles = {"C1": {"facts": {"id": "C1"}, "instance_url": "http://x/C1"}}
        self._wire(filings, bundles, {})             # extractor: not tagged
        out = self.xd.credit_quality_history(123, quarterly=False)
        self.assertEqual(out, {})
        self.assertEqual(len(self.cache), 1)         # real absence → cached


if __name__ == "__main__":
    unittest.main()
