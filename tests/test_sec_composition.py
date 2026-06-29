"""Unit tests for the as-reported loan/deposit composition engine.

Each test pins a real failure mode discovered across the bank universe, with a
synthetic fact set (no live EDGAR), so the reconcile-gate and the three de-dup
passes can't silently regress:

  - clean single-level breakdown reconciles            (BCML/PNFP class)
  - synonym-label collapse                             (AROW: one category tagged
                                                        under two member qnames)
  - value-aggregate finest-partition of a flat-tagged
    hierarchy with NO linkbase nesting                 (CFR)
  - linkbase-descendant drop (parent+child)            (def-linkbase nesting)
  - ambiguous finest-partition -> n/a (never guess)
  - non-reconciling breakdown -> n/a
  - the stock allowance concept is never a composition
  - past-due / vintage concepts are skipped
  - deposit reconcile incl. interest-bearing parent drop, and 2-way fallback
"""
import unittest

from data.sec_filing_scraper import Fact
from data.sec_composition import (
    extract_loan_composition, extract_deposit_composition, _finest_partition,
    _collapse_equal_value, _unique_reconciling_subset,
)

LOAN_CONCEPT = "us-gaap:FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss"
SEG_AXIS = "us-gaap:FinancingReceivablePortfolioSegmentAxis"
CLASS_AXIS = "us-gaap:FinancingReceivableRecordedInvestmentByClassOfFinancingReceivableAxis"
PER = "2025-12-31"
M = 1_000_000  # value-partition tests use dollar magnitudes (the engine's tol has
#                a $0.5M absolute floor tuned for real iXBRL dollar values).


def total_fact(concept, value, period=PER):
    return Fact(concept, value, period, None, {}, "USD")


def member_fact(concept, value, member, axis=SEG_AXIS, period=PER):
    return Fact(concept, value, period, None, {axis: member}, "USD")


def rows_of(comp):
    """{label: value} for the single period in a composition result."""
    _p, d = next(iter(comp.items()))
    return d["total"], dict(d["rows"])


class TestLoanComposition(unittest.TestCase):
    def test_clean_single_level_reconciles(self):
        """No parents, no synonyms: the three categories ARE the composition."""
        facts = [
            total_fact(LOAN_CONCEPT, 1000.0),
            member_fact(LOAN_CONCEPT, 500.0, "us-gaap:CommercialRealEstateMember"),
            member_fact(LOAN_CONCEPT, 300.0, "us-gaap:CommercialPortfolioSegmentMember"),
            member_fact(LOAN_CONCEPT, 200.0, "us-gaap:ConsumerPortfolioSegmentMember"),
        ]
        labels = {"CommercialRealEstateMember": "Commercial Real Estate",
                  "CommercialPortfolioSegmentMember": "Commercial",
                  "ConsumerPortfolioSegmentMember": "Consumer"}
        comp = extract_loan_composition(facts, labels, {})
        total, rows = rows_of(comp)
        self.assertEqual(total, 1000.0)
        self.assertEqual(rows, {"Commercial Real Estate": 500.0,
                                "Commercial": 300.0, "Consumer": 200.0})

    def test_synonym_label_collapse_AROW(self):
        """AROW: the SAME category is tagged under two member qnames that resolve
        to the SAME terseLabel (CommercialLoanMember + CommercialPortfolioSegment-
        Member -> 'Commercial', both equal). Without the collapse the segment
        members sum to 4437 != the 3453 total and the candidate is rejected."""
        facts = [
            total_fact(LOAN_CONCEPT, 3453.0),
            member_fact(LOAN_CONCEPT, 1393.0, "us-gaap:ResidentialPortfolioSegmentMember"),
            member_fact(LOAN_CONCEPT, 1076.0, "us-gaap:ConsumerPortfolioSegmentMember"),
            member_fact(LOAN_CONCEPT, 818.3, "us-gaap:CommercialRealEstateMember"),
            member_fact(LOAN_CONCEPT, 818.3, "us-gaap:CommercialRealEstatePortfolioSegmentMember"),
            member_fact(LOAN_CONCEPT, 165.7, "us-gaap:CommercialLoanMember"),
            member_fact(LOAN_CONCEPT, 165.7, "us-gaap:CommercialPortfolioSegmentMember"),
        ]
        labels = {"ResidentialPortfolioSegmentMember": "Residential",
                  "ConsumerPortfolioSegmentMember": "Consumer",
                  "CommercialRealEstateMember": "Commercial Real Estate",
                  "CommercialRealEstatePortfolioSegmentMember": "Commercial Real Estate",
                  "CommercialLoanMember": "Commercial",
                  "CommercialPortfolioSegmentMember": "Commercial"}
        total, rows = rows_of(extract_loan_composition(facts, labels, {}))
        self.assertEqual(total, 3453.0)
        self.assertEqual(set(rows), {"Residential", "Consumer",
                                     "Commercial Real Estate", "Commercial"})
        self.assertAlmostEqual(rows["Commercial Real Estate"], 818.3)
        self.assertAlmostEqual(rows["Commercial"], 165.7)
        self.assertAlmostEqual(sum(rows.values()), 3453.0, places=1)

    def test_value_aggregate_finest_partition_CFR(self):
        """CFR: parents AND children are tagged flat under one domain with NO
        linkbase nesting. The values prove the tree (RealEstate=CRE+Consumer;
        CRE=Owner+NonOwner). The finest partition drops the parents, keeping the
        four leaves that reconcile to the total."""
        c = LOAN_CONCEPT
        facts = [
            total_fact(c, 900 * M),
            member_fact(c, 600 * M, "x:RealEstateLoanMember", CLASS_AXIS),     # = CRE + Consumer
            member_fact(c, 400 * M, "x:CommercialRealEstatePortfolioSegmentMember", CLASS_AXIS),  # = Owner+NonOwner
            member_fact(c, 250 * M, "x:CommercialRealEstateOwnerOccupiedMember", CLASS_AXIS),
            member_fact(c, 150 * M, "x:CommercialRealEstateNonOwnerOccupiedMember", CLASS_AXIS),
            member_fact(c, 200 * M, "x:ConsumerLoanMember", CLASS_AXIS),
            member_fact(c, 300 * M, "x:CommercialPortfolioSegmentMember", CLASS_AXIS),
        ]
        labels = {"RealEstateLoanMember": "Real Estate",
                  "CommercialRealEstatePortfolioSegmentMember": "Commercial Real Estate",
                  "CommercialRealEstateOwnerOccupiedMember": "CRE Owner Occupied",
                  "CommercialRealEstateNonOwnerOccupiedMember": "CRE Non-Owner Occupied",
                  "ConsumerLoanMember": "Consumer", "CommercialPortfolioSegmentMember": "Commercial"}
        total, rows = rows_of(extract_loan_composition(facts, labels, {}))
        self.assertEqual(total, 900 * M)
        self.assertEqual(set(rows), {"CRE Owner Occupied", "CRE Non-Owner Occupied",
                                     "Consumer", "Commercial"})
        self.assertAlmostEqual(sum(rows.values()), 900 * M, delta=M)

    def test_linkbase_descendant_drop(self):
        """A parent member is dropped when a finer member it CONTAINS (per the
        definition linkbase) is also tagged."""
        c = LOAN_CONCEPT
        facts = [
            total_fact(c, 900.0),
            member_fact(c, 500.0, "x:RealEstateMember", CLASS_AXIS),   # parent
            member_fact(c, 300.0, "x:CommercialRealEstateMember", CLASS_AXIS),
            member_fact(c, 200.0, "x:ResidentialMember", CLASS_AXIS),
            member_fact(c, 400.0, "x:CommercialMember", CLASS_AXIS),
        ]
        labels = {"RealEstateMember": "Real Estate", "CommercialRealEstateMember": "CRE",
                  "ResidentialMember": "Residential", "CommercialMember": "Commercial"}
        children = {"RealEstateMember": {"CommercialRealEstateMember", "ResidentialMember"}}
        total, rows = rows_of(extract_loan_composition(facts, labels, children))
        self.assertEqual(total, 900.0)
        self.assertEqual(set(rows), {"CRE", "Residential", "Commercial"})

    def test_non_reconciling_returns_none(self):
        """Members that don't sum to the disclosed total -> n/a, never a guess."""
        facts = [
            total_fact(LOAN_CONCEPT, 1000.0),
            member_fact(LOAN_CONCEPT, 500.0, "us-gaap:CommercialRealEstateMember"),
            member_fact(LOAN_CONCEPT, 200.0, "us-gaap:ConsumerPortfolioSegmentMember"),
        ]
        labels = {"CommercialRealEstateMember": "CRE", "ConsumerPortfolioSegmentMember": "Consumer"}
        self.assertIsNone(extract_loan_composition(facts, labels, {}))

    def test_ambiguous_partition_returns_none(self):
        """Four equal members, total = 3x: multiple distinct finest partitions ->
        we cannot tell which split is real -> n/a (safety over a guess)."""
        self.assertIsNone(_finest_partition(
            {"a": 100 * M, "b": 100 * M, "c": 100 * M, "d": 100 * M}, 300 * M))

    def test_allowance_concept_never_a_composition(self):
        """The STOCK allowance concept reconciles internally but is NOT a loan
        balance -> must never render as a composition."""
        c = "us-gaap:FinancingReceivableAllowanceForCreditLossExcludingAccruedInterest"
        facts = [
            total_fact(c, 100.0),
            member_fact(c, 60.0, "us-gaap:CommercialRealEstateMember"),
            member_fact(c, 40.0, "us-gaap:ConsumerPortfolioSegmentMember"),
        ]
        labels = {"CommercialRealEstateMember": "CRE", "ConsumerPortfolioSegmentMember": "Consumer"}
        self.assertIsNone(extract_loan_composition(facts, labels, {}))

    def test_past_due_concept_skipped(self):
        """A by-segment past-due table reconciles but is not the balance."""
        c = "us-gaap:FinancingReceivableExcludingAccruedInterest90DaysOrMorePastDueStillAccruing"
        facts = [
            total_fact(c, 10.0),
            member_fact(c, 6.0, "us-gaap:CommercialRealEstateMember"),
            member_fact(c, 4.0, "us-gaap:ConsumerPortfolioSegmentMember"),
        ]
        labels = {"CommercialRealEstateMember": "CRE", "ConsumerPortfolioSegmentMember": "Consumer"}
        self.assertIsNone(extract_loan_composition(facts, labels, {}))

    def test_subslice_rejected_by_book_coverage(self):
        """A giant filer whose full book doesn't partition, but a small sub-line
        does (INDB tags only $2.1B revolving of an $18.5B book), must render n/a —
        never a sub-slice masquerading as the whole composition."""
        gross = "us-gaap:FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss"
        revolving = "us-gaap:FinancingReceivableRevolving"
        facts = [
            # the real $10B book — members do NOT reconcile to it (multi-membered)
            total_fact(gross, 10000 * M),
            member_fact(gross, 6000 * M, "us-gaap:CommercialRealEstateMember"),
            member_fact(gross, 1000 * M, "us-gaap:ConsumerPortfolioSegmentMember"),
            # a clean $2B revolving sub-table that DOES reconcile
            total_fact(revolving, 2000 * M),
            member_fact(revolving, 1200 * M, "us-gaap:CommercialRealEstateMember"),
            member_fact(revolving, 800 * M, "us-gaap:ConsumerPortfolioSegmentMember"),
        ]
        labels = {"CommercialRealEstateMember": "CRE", "ConsumerPortfolioSegmentMember": "Consumer"}
        self.assertIsNone(extract_loan_composition(facts, labels, {}))

    def test_multiyear_periods_returned_newest_first(self):
        """A 10-K tags the current FY plus prior comparatives. Every period whose
        members reconcile is returned, keyed newest-first, so the UI can build a
        categories x FY table."""
        c = LOAN_CONCEPT
        facts = [
            total_fact(c, 1000.0, "2024-12-31"),
            member_fact(c, 600.0, "us-gaap:CommercialRealEstateMember", period="2024-12-31"),
            member_fact(c, 400.0, "us-gaap:ConsumerPortfolioSegmentMember", period="2024-12-31"),
            total_fact(c, 900.0, "2023-12-31"),
            member_fact(c, 500.0, "us-gaap:CommercialRealEstateMember", period="2023-12-31"),
            member_fact(c, 400.0, "us-gaap:ConsumerPortfolioSegmentMember", period="2023-12-31"),
        ]
        labels = {"CommercialRealEstateMember": "CRE", "ConsumerPortfolioSegmentMember": "Consumer"}
        comp = extract_loan_composition(facts, labels, {})
        self.assertEqual(list(comp), ["2024-12-31", "2023-12-31"])   # newest first
        self.assertEqual(comp["2024-12-31"]["total"], 1000.0)
        self.assertEqual(comp["2023-12-31"]["total"], 900.0)
        for d in comp.values():
            self.assertAlmostEqual(sum(v for _l, v in d["rows"]), d["total"], places=1)

    def test_bad_coverage_period_is_dropped_ABCB2023(self):
        """ABCB's 2025 10-K tags the full ~$21.5B book undimensioned only for
        2025/2024; for the 2023 comparative the ONLY undimensioned loan-balance
        total tagged is a tiny ~$20M modified-loans subtotal that happens to
        reconcile to its own members. Measured against the LARGEST clean book in
        the filing it's a sub-slice, so the 2023 period must be DROPPED entirely —
        never shipped as a $20M 'loan composition'. The good years still render."""
        c = LOAN_CONCEPT
        facts = [
            # 2025 + 2024: the real book (~1000) reconciles
            total_fact(c, 1000.0, "2025-12-31"),
            member_fact(c, 600.0, "us-gaap:CommercialRealEstateMember", period="2025-12-31"),
            member_fact(c, 400.0, "us-gaap:ConsumerPortfolioSegmentMember", period="2025-12-31"),
            total_fact(c, 980.0, "2024-12-31"),
            member_fact(c, 590.0, "us-gaap:CommercialRealEstateMember", period="2024-12-31"),
            member_fact(c, 390.0, "us-gaap:ConsumerPortfolioSegmentMember", period="2024-12-31"),
            # 2023: only a tiny $20 sub-line is tagged undimensioned (and its two
            # members reconcile to it) — but $20 << 0.5 * 1000, so it is gated out.
            total_fact(c, 20.0, "2023-12-31"),
            member_fact(c, 12.0, "us-gaap:CommercialRealEstateMember", period="2023-12-31"),
            member_fact(c, 8.0, "us-gaap:ConsumerPortfolioSegmentMember", period="2023-12-31"),
        ]
        labels = {"CommercialRealEstateMember": "CRE", "ConsumerPortfolioSegmentMember": "Consumer"}
        comp = extract_loan_composition(facts, labels, {})
        self.assertEqual(set(comp), {"2025-12-31", "2024-12-31"})    # 2023 dropped
        self.assertNotIn("2023-12-31", comp)

    def test_equal_value_synonym_collapse_BANR(self):
        """BANR tags two categories under TWO member qnames each, whose DISPLAY
        labels DIFFER (so `_dedup_synonyms`, which keys on the label, misses them):
        SmallBalance...RealEstateLoans + SmallBalanceCRE both = 1212.357M, and
        Land-and-Land-Development + Land-and-Land-Improvements both = 433.678M. The
        un-collapsed members sum to 1646.035M OVER the total (the two dups double-
        counted); equal-value collapse removes the double-count and the set
        reconciles EXACTLY to the disclosed book."""
        # Exact values from BANR's FY2025 10-K (NotesReceivableGross x class axis).
        c = LOAN_CONCEPT
        facts = [
            total_fact(c, 11721687000),
            member_fact(c, 1701413000, "x:InvestmentPropertiesCommericalRealEstateMember", CLASS_AXIS),
            member_fact(c, 1573191000, "x:OneToFourFamilyResidentialMember", CLASS_AXIS),
            member_fact(c, 1225108000, "x:CommercialBusinessMember", CLASS_AXIS),
            # synonym pair #1 — same value, DIFFERENT label
            member_fact(c, 1212357000, "x:SmallBalanceCommercialRealEstateLoansMember", CLASS_AXIS),
            member_fact(c, 1212357000, "x:SmallBalanceCREMember", CLASS_AXIS),
            member_fact(c, 1187360000, "x:SmallCreditScoredBusinessLoansMember", CLASS_AXIS),
            member_fact(c, 1138298000, "x:OwnerOccupiedCommercialRealEstateMember", CLASS_AXIS),
            member_fact(c, 850789000, "x:MultifamilyRealEstateMember", CLASS_AXIS),
            member_fact(c, 679489000, "x:HomeEquityMember", CLASS_AXIS),
            member_fact(c, 607447000, "x:OneToFourFamilyConstructionMember", CLASS_AXIS),
            member_fact(c, 514330000, "x:MultifamilyConstructionMember", CLASS_AXIS),
            # synonym pair #2 — same value, DIFFERENT label
            member_fact(c, 433678000, "x:LandandLandDevelopmentTypeMember", CLASS_AXIS),
            member_fact(c, 433678000, "x:LandAndLandImprovementsMember", CLASS_AXIS),
            member_fact(c, 353152000, "x:AgriculturalBusinessMember", CLASS_AXIS),
            member_fact(c, 156021000, "x:CommercialConstructionMember", CLASS_AXIS),
            member_fact(c, 89054000, "x:ConsumerLoanMember", CLASS_AXIS),
        ]
        labels = {
            "InvestmentPropertiesCommericalRealEstateMember": "Commercial real estate - investment",
            "OneToFourFamilyResidentialMember": "One- to four-family residential",
            "CommercialBusinessMember": "Commercial business",
            "SmallBalanceCommercialRealEstateLoansMember": "Small Balance Commercial Real Estate Loans",
            "SmallBalanceCREMember": "Small Balance CRE",
            "SmallCreditScoredBusinessLoansMember": "Small Credit-Scored Business Loans",
            "OwnerOccupiedCommercialRealEstateMember": "Owner-occupied Commercial Real Estate",
            "MultifamilyRealEstateMember": "Multifamily real estate",
            "HomeEquityMember": "Home Equity Line of Credit",
            "OneToFourFamilyConstructionMember": "One-to four-family construction",
            "MultifamilyConstructionMember": "Multifamily construction",
            "LandandLandDevelopmentTypeMember": "Land and Land Development Type",
            "LandAndLandImprovementsMember": "Land and Land Improvements",
            "AgriculturalBusinessMember": "Agricultural Business",
            "CommercialConstructionMember": "Commercial Construction",
            "ConsumerLoanMember": "Consumer Loan",
        }
        total, rows = rows_of(extract_loan_composition(facts, labels, {}))
        self.assertEqual(total, 11721687000)
        self.assertEqual(len(rows), 14)                                 # 16 members, 2 dups collapsed
        self.assertEqual(sum(rows.values()), 11721687000)               # reconciles EXACTLY

    def test_collapse_equal_value_helper(self):
        # Same value -> one representative; distinct values -> all kept.
        out = _collapse_equal_value({"a": 100.0, "b": 100.0, "c": 50.0})
        self.assertEqual(sorted(out.values()), [50.0, 100.0])           # one of a/b dropped
        self.assertEqual(len(out), 2)

    def test_two_genuine_equal_value_leaves_not_falsely_collapsed(self):
        """SAFETY: two GENUINELY distinct categories that happen to share a balance
        must NOT be collapsed to a wrong number. The collapse is reconcile-gated, so
        dropping a needed leaf makes the sum fall short and the candidate is
        rejected — here the only valid composition keeps both, via a clean partition
        of distinct values, never the collapsed (short) set."""
        c = LOAN_CONCEPT
        facts = [
            total_fact(c, 1000.0 * M),
            member_fact(c, 300.0 * M, "x:CommercialMember", CLASS_AXIS),     # equal value,
            member_fact(c, 300.0 * M, "x:ConsumerMember", CLASS_AXIS),       # DISTINCT category
            member_fact(c, 400.0 * M, "x:RealEstateMember", CLASS_AXIS),
        ]
        labels = {"CommercialMember": "Commercial", "ConsumerMember": "Consumer",
                  "RealEstateMember": "Real Estate"}
        total, rows = rows_of(extract_loan_composition(facts, labels, {}))
        self.assertEqual(total, 1000.0 * M)
        # both 300M leaves survive (collapsing one -> sum 700M, gate would reject)
        self.assertEqual(set(rows), {"Commercial", "Consumer", "Real Estate"})
        self.assertAlmostEqual(sum(rows.values()), 1000.0 * M, delta=M)

    def test_unique_reconciling_subset_flat_aggregate_partial_children_FBK(self):
        """FBK flat-tags a coarse 2-way book — TotalCommercial (9165.158M) +
        TotalConsumer (3218.468M) = the 12383.626M total — PLUS three commercial
        SUB-types (Commercial, Construction, Consumer-and-other) that do NOT fully
        decompose the commercial total. No finest partition exists; exactly ONE
        subset reconciles, so the unique-subset resolver recovers the 2-way split.
        Components sum to the disclosed book."""
        # Exact values from FBK's FY2025 10-K (gross-loan concept x segment axis).
        c = LOAN_CONCEPT
        facts = [
            total_fact(c, 12383626000),
            member_fact(c, 9165158000, "x:TotalCommercialLoansMember"),
            member_fact(c, 3218468000, "x:ConsumerPortfolioSegmentMember"),
            member_fact(c, 2181935000, "x:CommercialPortfolioSegmentMember"),
            member_fact(c, 1188494000, "x:ConstructionPortfolioSegmentMember"),
            member_fact(c, 639037000, "x:ConsumerAndOtherPortfolioSegmentMember"),
        ]
        labels = {"TotalCommercialLoansMember": "Total commercial loan types",
                  "ConsumerPortfolioSegmentMember": "Total consumer type loans",
                  "CommercialPortfolioSegmentMember": "Commercial and industrial",
                  "ConstructionPortfolioSegmentMember": "Construction",
                  "ConsumerAndOtherPortfolioSegmentMember": "Consumer and other"}
        total, rows = rows_of(extract_loan_composition(facts, labels, {}))
        self.assertEqual(total, 12383626000)
        self.assertEqual(set(rows),
                         {"Total commercial loan types", "Total consumer type loans"})
        self.assertEqual(sum(rows.values()), 12383626000)               # reconciles EXACTLY

    def test_unique_reconciling_subset_ambiguous_returns_none(self):
        """If more than one subset reconciles, the resolver can't tell which split
        is real -> None (never a guess)."""
        M2 = M
        # {a,b}=300 and {a,c}=300 both reconcile to 300 (b==c) -> ambiguous
        self.assertIsNone(_unique_reconciling_subset(
            {"a": 200 * M2, "b": 100 * M2, "c": 100 * M2}, 300 * M2))

    def test_largest_reconciling_candidate_wins(self):
        """Gross (before-allowance) and net (after-allowance) both reconcile; the
        larger gross book is preferred."""
        gross = "us-gaap:FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss"
        net = "us-gaap:FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss"
        facts = [
            total_fact(gross, 1000.0), member_fact(gross, 600.0, "us-gaap:CommercialRealEstateMember"),
            member_fact(gross, 400.0, "us-gaap:ConsumerPortfolioSegmentMember"),
            total_fact(net, 980.0), member_fact(net, 590.0, "us-gaap:CommercialRealEstateMember"),
            member_fact(net, 390.0, "us-gaap:ConsumerPortfolioSegmentMember"),
        ]
        labels = {"CommercialRealEstateMember": "CRE", "ConsumerPortfolioSegmentMember": "Consumer"}
        total, _rows = rows_of(extract_loan_composition(facts, labels, {}))
        self.assertEqual(total, 1000.0)


class TestDepositComposition(unittest.TestCase):
    def _labels(self):
        return {
            "NoninterestBearingDepositLiabilities": "Noninterest-bearing",
            "InterestBearingDepositLiabilities": "Interest-bearing",
            "InterestBearingDomesticDepositSavings": "Savings",
            "InterestBearingDomesticDepositMoneyMarket": "Money market",
            "TimeDeposits": "Time deposits",
            "DepositsSavingsDeposits": "Savings",
        }

    def test_deposit_reconcile_with_parent_drop_CFR(self):
        """CFR-style: the interest-bearing PARENT is tagged alongside its savings/
        money-market/time components; the parent is dropped, leaving NIB + the
        three components reconciling to the Deposits total."""
        facts = [
            total_fact("us-gaap:Deposits", 42918 * M),
            total_fact("us-gaap:NoninterestBearingDepositLiabilities", 14144 * M),
            total_fact("us-gaap:InterestBearingDepositLiabilities", 28774 * M),  # parent
            total_fact("us-gaap:InterestBearingDomesticDepositSavings", 10457 * M),
            total_fact("us-gaap:InterestBearingDomesticDepositMoneyMarket", 11889 * M),
            total_fact("us-gaap:TimeDeposits", 6428 * M),
        ]
        total, rows = rows_of(extract_deposit_composition(facts, self._labels(), {}))
        self.assertEqual(total, 42918 * M)
        self.assertEqual(set(rows), {"Noninterest-bearing", "Savings", "Money market", "Time deposits"})
        self.assertAlmostEqual(sum(rows.values()), 42918 * M, delta=M)

    def test_deposit_two_way_fallback(self):
        """A bank tagging only the interest 2-way still reconciles."""
        facts = [
            total_fact("us-gaap:Deposits", 2892 * M),
            total_fact("us-gaap:NoninterestBearingDepositLiabilities", 1007 * M),
            total_fact("us-gaap:InterestBearingDepositLiabilities", 1885 * M),
        ]
        total, rows = rows_of(extract_deposit_composition(facts, self._labels(), {}))
        self.assertEqual(total, 2892 * M)
        self.assertEqual(set(rows), {"Noninterest-bearing", "Interest-bearing"})

    def test_deposit_multiyear_changing_granularity(self):
        """Filers change deposit granularity year to year: a coarse interest-bearing
        2-way one period and the full product split the next. Each reconciling
        period is returned (newest-first); the UI unions the differing category sets
        and leaves blanks where a year didn't report a line — never carry-forward."""
        facts = [
            # 2025: full product split  (dollar magnitudes — the tol has a $0.5M+
            # absolute floor tuned for real iXBRL values)
            total_fact("us-gaap:Deposits", 100 * M, "2025-12-31"),
            total_fact("us-gaap:NoninterestBearingDepositLiabilities", 40 * M, "2025-12-31"),
            total_fact("us-gaap:InterestBearingDomesticDepositSavings", 35 * M, "2025-12-31"),
            total_fact("us-gaap:TimeDeposits", 25 * M, "2025-12-31"),
            # 2024: only the interest 2-way is tagged
            total_fact("us-gaap:Deposits", 90 * M, "2024-12-31"),
            total_fact("us-gaap:NoninterestBearingDepositLiabilities", 36 * M, "2024-12-31"),
            total_fact("us-gaap:InterestBearingDepositLiabilities", 54 * M, "2024-12-31"),
        ]
        comp = extract_deposit_composition(facts, self._labels(), {})
        self.assertEqual(list(comp), ["2025-12-31", "2024-12-31"])    # newest first
        self.assertEqual(set(dict(comp["2025-12-31"]["rows"])),
                         {"Noninterest-bearing", "Savings", "Time deposits"})
        self.assertEqual(set(dict(comp["2024-12-31"]["rows"])),
                         {"Noninterest-bearing", "Interest-bearing"})
        for d in comp.values():
            self.assertAlmostEqual(sum(v for _l, v in d["rows"]), d["total"], places=1)

    def test_deposit_non_reconciling_returns_none(self):
        facts = [
            total_fact("us-gaap:Deposits", 5000 * M),
            total_fact("us-gaap:NoninterestBearingDepositLiabilities", 1000 * M),
            total_fact("us-gaap:TimeDeposits", 1000 * M),
        ]
        self.assertIsNone(extract_deposit_composition(facts, self._labels(), {}))


class TestMultiDocParse(unittest.TestCase):
    """The multi-document iXBRL fix: large filers (USB/WFC/…) put the financial
    statements in a secondary document while the <xbrli:context> blocks stay in
    the primary, so the secondary's facts only resolve against the primary's
    contexts. parse_inline_xbrl_documentset must merge contexts across docs."""

    CONTEXT_DOC = (b'<html><body><div style="display:none">'
                   b'<ix:header><ix:resources>'
                   b'<xbrli:context id="c1"><xbrli:entity><xbrli:identifier>x'
                   b'</xbrli:identifier></xbrli:entity><xbrli:period>'
                   b'<xbrli:instant>2025-12-31</xbrli:instant></xbrli:period>'
                   b'</xbrli:context></ix:resources></ix:header></div></body></html>')
    FACT_DOC = (b'<html><body>'
                b'<ix:nonfraction name="us-gaap:Deposits" contextref="c1" scale="3" '
                b'unitref="usd">1,000</ix:nonfraction></body></html>')

    def test_facts_resolve_against_other_documents_contexts(self):
        from data.sec_filing_scraper import parse_inline_xbrl, parse_inline_xbrl_documentset
        # the fact document alone yields nothing (its context lives elsewhere)
        self.assertEqual(parse_inline_xbrl(self.FACT_DOC), [])
        # the document SET resolves the fact against the primary's context
        facts = parse_inline_xbrl_documentset([self.CONTEXT_DOC, self.FACT_DOC])
        self.assertEqual(len(facts), 1)
        f = facts[0]
        self.assertEqual(f.concept, "us-gaap:Deposits")
        self.assertEqual(f.value, 1000 * 10 ** 3)   # scale="3"
        self.assertEqual(f.period_end, "2025-12-31")

    def test_single_document_unchanged(self):
        from data.sec_filing_scraper import parse_inline_xbrl, parse_inline_xbrl_documentset
        one = self.CONTEXT_DOC[:-len(b"</body></html>")] + (
            b'<ix:nonfraction name="us-gaap:Deposits" contextref="c1" unitref="usd">'
            b'500</ix:nonfraction></body></html>')
        self.assertEqual([(f.concept, f.value) for f in parse_inline_xbrl(one)],
                         [(f.concept, f.value) for f in parse_inline_xbrl_documentset([one])])


if __name__ == "__main__":
    unittest.main()
