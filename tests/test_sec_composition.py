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

    def test_deposit_non_reconciling_returns_none(self):
        facts = [
            total_fact("us-gaap:Deposits", 5000 * M),
            total_fact("us-gaap:NoninterestBearingDepositLiabilities", 1000 * M),
            total_fact("us-gaap:TimeDeposits", 1000 * M),
        ]
        self.assertIsNone(extract_deposit_composition(facts, self._labels(), {}))


if __name__ == "__main__":
    unittest.main()
