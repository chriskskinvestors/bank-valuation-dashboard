"""Inline-XBRL parser (data/sec_filing_scraper.parse_inline_xbrl).

Pins the parsing logic deterministically (no network) on a synthetic iXBRL
snippet shaped like a bank 10-K's regulatory-capital disclosure: scale,
accounting-sign, the context join, and the dimensional member that separates
the holding company (ParentCompanyMember) from the bank (SubsidiariesMember).

The values it must reproduce are the ones verified LIVE against Regions' FY2025
10-K (CET1 $13.49B / 10.89%, leverage 9.68% — exact to SNL); here we assert the
mechanics so a regression can't silently corrupt them.
"""
import unittest

from data.sec_filing_scraper import parse_inline_xbrl, extract_holdco_capital, Fact


def _f(concept, val, members=None, period="2025-12-31"):
    return Fact(concept, val, period, None, members or {}, "pure")


_AX = "us-gaap:ConsolidatedEntitiesAxis"
_LE = "us-gaap:LegalEntityAxis"


class TestHoldcoExtraction(unittest.TestCase):
    """The holdco regulatory-capital selection — validated across 100 banks
    (tools/validate_capital_scrape.py: 95% effective coverage, 100% of extracted
    consistent with FDIC). These pin the load-bearing rules deterministically so
    a regression can't reintroduce a wrong number."""

    def test_default_context_is_holdco(self):
        out = extract_holdco_capital([_f("us-gaap:CommonEquityTier1CapitalToRiskWeightedAssets", 0.11)])
        self.assertAlmostEqual(out["2025-12-31"]["cet1_ratio"], 0.11)

    def test_tierone_and_tier1_spellings_both_match(self):
        for c in ("CommonEquityTierOneCapitalRatio", "CommonEquityTier1CapitalToRiskWeightedAssets",
                  "CommonEquityTierOneRiskBasedCapitalToRiskWeightedAssets"):
            out = extract_holdco_capital([_f("us-gaap:" + c, 0.105)])
            self.assertAlmostEqual(out["2025-12-31"]["cet1_ratio"], 0.105, msg=c)

    def test_holdco_preferred_over_bank(self):
        facts = [
            _f("us-gaap:CommonEquityTierOneCapitalRatio", 0.108, {_AX: "us-gaap:ParentCompanyMember"}),
            _f("us-gaap:CommonEquityTierOneCapitalRatio", 0.117, {_AX: "us-gaap:SubsidiariesMember"}),
        ]
        out = extract_holdco_capital(facts)
        self.assertAlmostEqual(out["2025-12-31"]["cet1_ratio"], 0.108)
        self.assertNotIn("_basis", out["2025-12-31"])   # holdco -> no bank flag

    def test_holdco_suffix_overrides_bank_substring(self):
        # "BlueRidgeBanksharesIncMember" contains "Bank" but is the PARENT.
        facts = [
            _f("us-gaap:CommonEquityTierOneRiskBasedCapitalToRiskWeightedAssets", 0.192,
               {_AX: "banr:BlueRidgeBanksharesIncMember"}),
            _f("us-gaap:CommonEquityTierOneRiskBasedCapitalToRiskWeightedAssets", 0.181,
               {_AX: "banr:BlueRidgeBankNAMember"}),
        ]
        out = extract_holdco_capital(facts)
        self.assertAlmostEqual(out["2025-12-31"]["cet1_ratio"], 0.192)   # the Inc parent

    def test_regulatory_minimum_excluded_by_name(self):
        facts = [
            _f("us-gaap:CommonEquityTier1CapitalRequiredForCapitalAdequacyToRiskWeightedAssets", 0.045),
            _f("us-gaap:CommonEquityTier1CapitalToRiskWeightedAssets", 0.112),
        ]
        out = extract_holdco_capital(facts)
        self.assertAlmostEqual(out["2025-12-31"]["cet1_ratio"], 0.112)

    def test_anchor_rejects_minimum_collision(self):
        # Same concept + ParentCompanyMember for BOTH the 7% (a minimum) and the
        # 11.5% actual (M&T-style collision). The FDIC anchor disambiguates.
        facts = [
            _f("us-gaap:CommonEquityTierOneCapitalRatio", 0.07, {_AX: "us-gaap:ParentCompanyMember"}),
            _f("us-gaap:CommonEquityTierOneCapitalRatio", 0.115, {_AX: "us-gaap:ParentCompanyMember"}),
        ]
        out = extract_holdco_capital(facts, anchor_cet1=12.0)
        self.assertAlmostEqual(out["2025-12-31"]["cet1_ratio"], 0.115)
        self.assertTrue(out["2025-12-31"]["_anchored"])

    def test_anchor_na_when_nothing_reconciles(self):
        # Only a 20% tiny-sub value, anchor says 11% -> out of band -> CET1 n/a.
        facts = [_f("us-gaap:CommonEquityTierOneCapitalRatio", 0.20, {_AX: "x:TrustMember"})]
        out = extract_holdco_capital(facts, anchor_cet1=11.0)
        self.assertNotIn("cet1_ratio", out.get("2025-12-31", {}))

    def test_bank_fallback_when_only_bank_tagged(self):
        # Single-bank holdco: only the bank member is tagged. Use it, flag basis.
        facts = [_f("us-gaap:CommonEquityTierOneCapitalRatio", 0.16, {_LE: "x:AuburnBankMember"})]
        out = extract_holdco_capital(facts, anchor_cet1=15.0)
        self.assertAlmostEqual(out["2025-12-31"]["cet1_ratio"], 0.16)
        self.assertEqual(out["2025-12-31"]["_basis"], "bank")

    def test_cblr_leverage_only(self):
        out = extract_holdco_capital([_f("us-gaap:TierOneLeverageCapitalToAverageAssets", 0.092)])
        self.assertTrue(out["2025-12-31"]["_cblr"])
        self.assertNotIn("cet1_ratio", out["2025-12-31"])

    def test_rwa_derived_from_cet1(self):
        facts = [
            _f("us-gaap:CommonEquityTier1CapitalToRiskWeightedAssets", 0.10),
            _f("us-gaap:CommonEquityTier1Capital", 10_000_000_000.0),
        ]
        out = extract_holdco_capital(facts)
        self.assertAlmostEqual(out["2025-12-31"]["rwa"], 100_000_000_000.0)


_IXBRL = b"""<html><body>
<ix:header><ix:resources>
  <xbrli:context id="cP">
    <xbrli:entity><xbrli:segment>
      <xbrldi:explicitMember dimension="us-gaap:ConsolidatedEntitiesAxis">us-gaap:ParentCompanyMember</xbrldi:explicitMember>
    </xbrli:segment></xbrli:entity>
    <xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period>
  </xbrli:context>
  <xbrli:context id="cS">
    <xbrli:entity><xbrli:segment>
      <xbrldi:explicitMember dimension="us-gaap:ConsolidatedEntitiesAxis">us-gaap:SubsidiariesMember</xbrldi:explicitMember>
    </xbrli:segment></xbrli:entity>
    <xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period>
  </xbrli:context>
</ix:resources></ix:header>
<ix:nonFraction name="us-gaap:CommonEquityTierOneCapital" contextRef="cP" unitRef="usd" scale="6" decimals="-6">13,490</ix:nonFraction>
<ix:nonFraction name="us-gaap:TierOneLeverageCapitalToAverageAssets" contextRef="cP" unitRef="pure" decimals="4">0.0968</ix:nonFraction>
<ix:nonFraction name="us-gaap:CommonEquityTierOneCapital" contextRef="cS" unitRef="usd" scale="6" decimals="-6">14,475</ix:nonFraction>
<ix:nonFraction name="us-gaap:OtherComprehensiveIncomeLoss" contextRef="cP" unitRef="usd" scale="6" sign="-">289</ix:nonFraction>
</body></html>"""


class TestInlineXbrlParser(unittest.TestCase):
    def setUp(self):
        self.facts = parse_inline_xbrl(_IXBRL)

    def _one(self, concept, member_value):
        hits = [f for f in self.facts if f.concept.endswith(concept) and
                member_value in f.members.get("us-gaap:ConsolidatedEntitiesAxis", "")]
        self.assertEqual(len(hits), 1, f"{concept}/{member_value}: {len(hits)} hits")
        return hits[0]

    def test_scale_applied_to_dollar_fact(self):
        f = self._one("CommonEquityTierOneCapital", "ParentCompanyMember")
        self.assertEqual(f.value, 13_490 * 10**6)   # scale=6 → $13.49B
        self.assertEqual(f.period_end, "2025-12-31")

    def test_holdco_vs_bank_member_separation(self):
        holdco = self._one("CommonEquityTierOneCapital", "ParentCompanyMember")
        bank = self._one("CommonEquityTierOneCapital", "SubsidiariesMember")
        self.assertEqual(holdco.value, 13_490_000_000)
        self.assertEqual(bank.value, 14_475_000_000)

    def test_ratio_fact_unscaled(self):
        f = self._one("TierOneLeverageCapitalToAverageAssets", "ParentCompanyMember")
        self.assertAlmostEqual(f.value, 0.0968)
        self.assertEqual(f.unit, "pure")

    def test_accounting_sign_negative(self):
        f = self._one("OtherComprehensiveIncomeLoss", "ParentCompanyMember")
        self.assertEqual(f.value, -289 * 10**6)   # sign="-"


if __name__ == "__main__":
    unittest.main()
