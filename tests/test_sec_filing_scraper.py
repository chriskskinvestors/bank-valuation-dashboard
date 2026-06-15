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

from data.sec_filing_scraper import parse_inline_xbrl

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
