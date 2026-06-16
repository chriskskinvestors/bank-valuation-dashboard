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
from unittest import mock

from data.sec_filing_scraper import (
    parse_inline_xbrl, extract_holdco_capital, extract_fair_value, Fact)


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

    def test_prefers_standardized_over_advanced_methodology(self):
        # Advanced-approach banks (WFC/BK/…) tag each ratio under BOTH a
        # Standardized and an Advanced methodology member. Standardized is the
        # binding, reported figure — and the FDIC CET1 anchor, which for these
        # names sits closer to the Advanced ratio, must NOT override that choice.
        MX = "us-gaap:RiskWeightedAssetsCalculationMethodologyAxis"
        std, adv = "us-gaap:StandardizedApproachMember", "us-gaap:AdvancedApproachMember"
        facts = [
            _f("us-gaap:CommonEquityTier1CapitalToRiskWeightedAssets", 0.1061, {MX: std}),
            _f("us-gaap:CommonEquityTier1CapitalToRiskWeightedAssets", 0.1235, {MX: adv}),
            _f("us-gaap:Tier1RiskBasedCapitalToRiskWeightedAssets", 0.1186, {MX: std}),
            _f("us-gaap:Tier1RiskBasedCapitalToRiskWeightedAssets", 0.1380, {MX: adv}),
        ]
        # anchor 12.5 is nearer the Advanced 12.35 than the Standardized 10.61
        out = extract_holdco_capital(facts, anchor_cet1=12.5)["2025-12-31"]
        self.assertAlmostEqual(out["cet1_ratio"], 0.1061)   # binding, not 0.1235
        self.assertAlmostEqual(out["t1_ratio"], 0.1186)     # same methodology

    def test_inconsistent_tagged_rwa_overridden_by_implied(self):
        # A filer can tag a sub-component as RWA (GBNY tagged $40M vs the $306M
        # implied by CET1-cap / ratio). Trust the implied, internally-consistent RWA.
        facts = [
            _f("us-gaap:CommonEquityTier1CapitalToRiskWeightedAssets", 0.1283),
            _f("us-gaap:CommonEquityTier1Capital", 39.3e6),
            _f("us-gaap:RiskWeightedAssets", 40e6),   # wrong tag (~1/8th of real)
        ]
        out = extract_holdco_capital(facts)["2025-12-31"]
        self.assertAlmostEqual(out["rwa"], 39.3e6 / 0.1283, delta=1e3)

    def test_regulatory_capital_axis_parent_is_holdco(self):
        # PEBO splits CET1 on RegulatoryCapitalRequirementsForBanksAxis
        # (ParentCompany = holdco vs Bank = subsidiary) — pick the holdco ratio.
        AX = "us-gaap:RegulatoryCapitalRequirementsForBanksAxis"
        facts = [
            _f("us-gaap:CommonEquityTier1CapitalToRiskWeightedAssets", 0.1229, {AX: "us-gaap:ParentCompanyMember"}),
            _f("us-gaap:CommonEquityTier1CapitalToRiskWeightedAssets", 0.1198, {AX: "us-gaap:BankMember"}),
        ]
        out = extract_holdco_capital(facts)["2025-12-31"]
        self.assertAlmostEqual(out["cet1_ratio"], 0.1229)   # holdco, not bank 0.1198

    def test_ratio_suffix_variant_and_derived_cet1(self):
        # WSBC tags ratios as '...ToRiskWeightedAssetsRatio' (trailing 'Ratio')
        # and tags CET1 *capital* but no CET1 *ratio*. Match the spelling, and
        # reconstruct the CET1 ratio from CET1-cap / (T1-cap / T1-ratio).
        facts = [
            _f("us-gaap:CommonEquityTierOneCapital", 2219.2e6),
            _f("us-gaap:TierOneRiskBasedCapital", 2443.4e6),
            _f("us-gaap:TierOneRiskBasedCapitalToRiskWeightedAssetsRatio", 0.1142),
            _f("us-gaap:CapitalToRiskWeightedAssetsRatio", 0.1392),
        ]
        out = extract_holdco_capital(facts)["2025-12-31"]
        self.assertAlmostEqual(out["t1_ratio"], 0.1142)       # '...Ratio' spelling matched
        self.assertAlmostEqual(out["total_ratio"], 0.1392)
        self.assertAlmostEqual(out["cet1_ratio"], 2219.2e6 / (2443.4e6 / 0.1142), places=4)

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


class TestCapitalWalk(unittest.TestCase):
    """The holdco regulatory-capital WALK reconstruction + reconciliation gate
    (extract_holdco_capital → _build_capital_walk). The walk is built from the
    filing's UNDIMENSIONED balance-sheet tags and shown ONLY when the CET1 build
    reconciles to the extracted (anchored) CET1 capital — never via a plug."""

    def _walk_facts(self, equity, goodwill, cet1_cap, other_intang=None,
                    aoci=None, preferred=None):
        # Values passed in $millions; banks tag in actual dollars, so scale up
        # (the reconciliation tolerance has a $5M floor for filing rounding).
        M = 1e6
        facts = [
            _f("us-gaap:CommonEquityTier1CapitalToRiskWeightedAssets", 0.10),
            _f("us-gaap:CommonEquityTier1Capital", cet1_cap * M),
            _f("us-gaap:StockholdersEquity", equity * M),
            _f("us-gaap:Goodwill", goodwill * M),
        ]
        if other_intang is not None:
            facts.append(_f("us-gaap:IntangibleAssetsNetExcludingGoodwill", other_intang * M))
        if aoci is not None:
            facts.append(_f("us-gaap:AccumulatedOtherComprehensiveIncomeLossNetOfTax", aoci * M))
        if preferred is not None:
            facts.append(_f("us-gaap:PreferredStockValue", preferred * M))
        return facts

    def test_walk_reconciles_aoci_included(self):
        # common 1500 − intangibles (400+100) = 1000 == CET1 1000 (AOCI in CET1).
        out = extract_holdco_capital(
            self._walk_facts(1500.0, 400.0, 1000.0, other_intang=100.0))
        d = out["2025-12-31"]
        self.assertTrue(d["_walk_reconciles"])
        self.assertEqual(d["_walk"]["aoci_treatment"], "included")
        self.assertAlmostEqual(d["_walk"]["common_equity"], 1500e6)
        self.assertAlmostEqual(d["_walk"]["intangibles"], 500e6)

    def test_walk_reconciles_aoci_excluded(self):
        # common 1500 − 500 = 1000; AOCI loss −50 removed → 1050 == CET1 1050.
        out = extract_holdco_capital(
            self._walk_facts(1500.0, 400.0, 1050.0, other_intang=100.0, aoci=-50.0))
        d = out["2025-12-31"]
        self.assertTrue(d["_walk_reconciles"])
        self.assertEqual(d["_walk"]["aoci_treatment"], "excluded")

    def test_walk_subtracts_tagged_preferred(self):
        # total equity 1600 − preferred 100 = common 1500; build reconciles.
        out = extract_holdco_capital(
            self._walk_facts(1600.0, 400.0, 1000.0, other_intang=100.0, preferred=100.0))
        d = out["2025-12-31"]
        self.assertTrue(d["_walk_reconciles"])
        self.assertAlmostEqual(d["_walk"]["common_equity"], 1500e6)
        self.assertAlmostEqual(d["_walk"]["preferred"], 100e6)

    def test_walk_na_when_build_does_not_reconcile(self):
        # common 1500 − goodwill 400 = 1100, ~10% off CET1 1000 → NOT shown.
        out = extract_holdco_capital(self._walk_facts(1500.0, 400.0, 1000.0))
        d = out["2025-12-31"]
        self.assertFalse(d["_walk_reconciles"])
        # Components are still recorded (the UI renders them only when reconciled).
        self.assertAlmostEqual(d["_walk"]["common_equity"], 1500e6)

    def test_walk_na_when_goodwill_untagged(self):
        facts = [
            _f("us-gaap:CommonEquityTier1CapitalToRiskWeightedAssets", 0.10),
            _f("us-gaap:CommonEquityTier1Capital", 1000.0),
            _f("us-gaap:StockholdersEquity", 1500.0),
        ]
        out = extract_holdco_capital(facts)
        d = out["2025-12-31"]
        self.assertFalse(d["_walk_reconciles"])
        self.assertIsNone(d["_walk"]["goodwill"])

    def test_walk_ignores_dimensional_breakdown_for_total(self):
        # A StockholdersEquity broken out by PreferredStockMember must NOT be
        # mistaken for the undimensioned total — first (undimensioned) wins.
        facts = self._walk_facts(1500.0, 400.0, 1000.0, other_intang=100.0)
        facts.append(_f("us-gaap:StockholdersEquity", 369.0,
                        {"us-gaap:StatementEquityComponentsAxis": "us-gaap:PreferredStockMember"}))
        out = extract_holdco_capital(facts)
        self.assertAlmostEqual(out["2025-12-31"]["_walk"]["common_equity"], 1500e6)

    def test_no_walk_on_cblr_period(self):
        out = extract_holdco_capital(
            [_f("us-gaap:TierOneLeverageCapitalToAverageAssets", 0.092)])
        self.assertNotIn("_walk", out["2025-12-31"])


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


class TestFairValueHierarchy(unittest.TestCase):
    """The recurring ASC 820 fair-value hierarchy extraction (extract_fair_value).
    Each case mirrors a real-filer tagging shape dumped by tools/_probe_fairvalue.py
    (ABCB/CFG/TFC/RF), so a regression can't silently corrupt the level split,
    the recurring-vs-nonrecurring filter, or the netting reconcile gate."""

    _HIER = "us-gaap:FairValueByFairValueHierarchyLevelAxis"
    _FREQ = "us-gaap:FairValueByMeasurementFrequencyAxis"
    _REC = "us-gaap:FairValueMeasurementsRecurringMember"
    _NONREC = "us-gaap:FairValueMeasurementsNonrecurringMember"
    _INSTR = "us-gaap:FinancialInstrumentAxis"
    M = 1e6

    def _fv(self, concept, val_m, level=None, freq=None, extra=None):
        m = {}
        if level:
            m[self._HIER] = f"us-gaap:FairValueInputsLevel{level}Member"
        if freq:
            m[self._FREQ] = freq
        if extra:
            m.update(extra)
        return _f(concept, val_m * self.M, m)

    def test_recurring_assets_reconcile(self):
        # ABCB-shape: L1 661, L2 2179, L3 1, grand 2841 (recurring) → reconciles.
        facts = [
            self._fv("us-gaap:AssetsFairValueDisclosure", 661, "1", self._REC),
            self._fv("us-gaap:AssetsFairValueDisclosure", 2179, "2", self._REC),
            self._fv("us-gaap:AssetsFairValueDisclosure", 1, "3", self._REC),
            self._fv("us-gaap:AssetsFairValueDisclosure", 2841, None, self._REC),
        ]
        a = extract_fair_value(facts)["2025-12-31"]["assets"]
        self.assertEqual(a["l1"], 661 * self.M)
        self.assertEqual(a["l3"], 1 * self.M)
        self.assertEqual(a["total"], 2841 * self.M)
        self.assertEqual(a["grand"], 2841 * self.M)
        self.assertTrue(a["_reconciles"])
        self.assertAlmostEqual(a["l3_pct"], 1 / 2841)

    def test_nonrecurring_and_instrument_rows_excluded(self):
        # A recurring L3 plus a nonrecurring L3 and an instrument sub-row at L3 —
        # only the clean recurring total counts.
        facts = [
            self._fv("us-gaap:AssetsFairValueDisclosure", 100, "3", self._REC),
            self._fv("us-gaap:AssetsFairValueDisclosure", 37, "3", self._NONREC),
            self._fv("us-gaap:AssetsFairValueDisclosure", 37, "3", self._NONREC,
                     {self._INSTR: "x:ImpairedLoansMember"}),
        ]
        a = extract_fair_value(facts)["2025-12-31"]["assets"]
        self.assertEqual(a["l3"], 100 * self.M)
        self.assertEqual(a["total"], 100 * self.M)

    def test_no_frequency_member_treated_as_recurring(self):
        # CFG-shape: levels tagged with NO frequency member → still the table.
        facts = [
            self._fv("us-gaap:AssetsFairValueDisclosure", 3414, "1"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 35195, "2"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 1463, "3"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 40072, None),
        ]
        a = extract_fair_value(facts)["2025-12-31"]["assets"]
        self.assertEqual(a["total"], 40072 * self.M)
        self.assertTrue(a["_reconciles"])

    def test_netting_delta_surfaced_when_grand_differs(self):
        # TFC-shape: L1+L2+L3 = 79,941 but grand = 78,162 → netting −1,779, n/recon.
        facts = [
            self._fv("us-gaap:AssetsFairValueDisclosure", 2515, "1"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 73439, "2"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 3987, "3"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 78162, None),
        ]
        a = extract_fair_value(facts)["2025-12-31"]["assets"]
        self.assertEqual(a["total"], 79941 * self.M)
        self.assertFalse(a["_reconciles"])
        self.assertAlmostEqual(a["netting"], (78162 - 79941) * self.M)

    def test_instrument_only_rows_yield_na(self):
        # RF-shape: tagged ONLY with instrument members (no clean level total) →
        # no assets entry (n/a, never component-summed into a guessed total).
        facts = [
            self._fv("us-gaap:AssetsFairValueDisclosure", 970, "3", None,
                     {self._INSTR: "us-gaap:ResidentialMortgageMember"}),
            self._fv("us-gaap:AssetsFairValueDisclosure", 93, "3", None,
                     {self._INSTR: "us-gaap:CommercialRealEstateMember"}),
        ]
        self.assertEqual(extract_fair_value(facts), {})

    def test_liabilities_side_independent(self):
        facts = [
            self._fv("us-gaap:LiabilitiesFairValueDisclosure", 168, "1", self._REC),
            self._fv("us-gaap:LiabilitiesFairValueDisclosure", 2089, "2", self._REC),
            self._fv("us-gaap:LiabilitiesFairValueDisclosure", 128, "3", self._REC),
        ]
        out = extract_fair_value(facts)["2025-12-31"]
        self.assertNotIn("assets", out)
        self.assertEqual(out["liabilities"]["total"], (168 + 2089 + 128) * self.M)
        self.assertIsNone(out["liabilities"]["grand"])
        self.assertTrue(out["liabilities"]["_reconciles"])


class TestFairValueCaching(unittest.TestCase):
    """A transient fetch/parse exception must NOT be cached as an empty result
    (else one SEC hiccup pins the company to an older filing via the 10-Q→10-K
    fallback). A successful parse — even a genuine empty — IS cached."""

    def _run(self, get_side_effect):
        import data.sec_filing_scraper as s
        from data import cache
        metas = {
            ("10-Q",): {"accession": "Q", "doc": "q.htm", "cik": 1, "date": "d", "form": "10-Q"},
            ("10-K",): {"accession": "K", "doc": "k.htm", "cik": 1, "date": "d", "form": "10-K"},
        }
        puts = []
        with mock.patch.object(s, "latest_filing", side_effect=lambda cik, forms: metas[forms]), \
             mock.patch.object(s, "_get", side_effect=get_side_effect), \
             mock.patch.object(cache, "get", return_value=None), \
             mock.patch.object(cache, "put", side_effect=lambda k, v: puts.append((k, v))):
            res = s.fair_value_for(1)
        return res, puts

    def test_transient_fetch_exception_not_cached(self):
        res, puts = self._run(OSError("boom"))
        self.assertIsNone(res)            # no data from either form
        self.assertEqual(puts, [])        # nothing cached → next load retries the 10-Q

    def test_successful_empty_parse_is_cached(self):
        # A successful fetch that yields no FV facts → extract {} → cached as the
        # valid "no rollup tagged" result (won't be needlessly re-fetched).
        res, puts = self._run(lambda url: b"<html></html>")
        self.assertIsNone(res)
        self.assertEqual([k for k, _ in puts], ["fair_value:v2:Q", "fair_value:v2:K"])
        self.assertTrue(all(v == {} for _, v in puts))


if __name__ == "__main__":
    unittest.main()
