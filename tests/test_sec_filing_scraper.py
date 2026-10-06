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
    parse_inline_xbrl, extract_holdco_capital, extract_fair_value,
    extract_securities, extract_credit_quality, extract_performance,
    extract_financial_highlights, extract_segments, extract_rate_risk,
    extract_npl_nco_by_year, extract_nim_by_year, extract_asset_quality_nim,
    extract_nim_prose,
    Fact)


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

    def test_percent_tagged_ratio_normalized(self):
        # Some filers (NBHC/UBSI) tag the ratio as the PERCENTAGE number ('14.9')
        # not the decimal (0.149) — normalize so it isn't read as 1490% and so the
        # RWA reconstruction stays correct.
        out = extract_holdco_capital([
            _f("us-gaap:CommonEquityTierOneCapitalRatio", 14.9),
            _f("us-gaap:CommonEquityTierOneCapital", 1.1e9),
        ])["2025-12-31"]
        self.assertAlmostEqual(out["cet1_ratio"], 0.149)
        self.assertAlmostEqual(out["rwa"], 1.1e9 / 0.149, delta=1e6)

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

    # Citi FY2025 10-K (acc 0000831001-26-000011), holdco facts as tagged: the
    # ratios carry no methodology member; RWA / total capital carry both.
    _MX = "us-gaap:RiskWeightedAssetsCalculationMethodologyAxis"
    _STD, _ADV = "us-gaap:StandardizedApproachMember", "us-gaap:AdvancedApproachMember"
    _PARENT = {_AX: "us-gaap:ParentCompanyMember"}

    def _citi(self):
        P, MX = self._PARENT, self._MX
        return [
            _f("c:TierOneCommonCapital", 157_099e6, P),
            _f("c:TierOneCommonCapitalRatio", 0.1318, P),
            _f("us-gaap:TierOneRiskBasedCapital", 179_675e6, P),
            _f("us-gaap:TierOneRiskBasedCapitalToRiskWeightedAssets", 0.1365, P),
            _f("us-gaap:CapitalToRiskWeightedAssets", 0.1566, P),
            _f("us-gaap:Capital", 216_468e6, {**P, MX: self._STD}),
            _f("us-gaap:Capital", 206_170e6, {**P, MX: self._ADV}),
            _f("us-gaap:RiskWeightedAssets", 1_192_174e6, {**P, MX: self._STD}),
            _f("us-gaap:RiskWeightedAssets", 1_316_371e6, {**P, MX: self._ADV}),
            _f("us-gaap:TierOneLeverageCapitalToAverageAssets", 0.0669, P),
        ]

    def test_tier_one_common_is_cet1_not_cblr(self):
        # Unmatched, Citi's CET1 read n/a and the leverage-only rule flagged a
        # $2.6T bank as a Community Bank Leverage Ratio electee.
        out = extract_holdco_capital(self._citi(), anchor_cet1=14.33)["2025-12-31"]
        self.assertAlmostEqual(out["cet1_ratio"], 0.1318)
        self.assertAlmostEqual(out["cet1_cap"], 157_099e6)
        self.assertFalse(out["_cblr"])

    def test_advanced_basis_ratio_rederived_on_standardized(self):
        # 10-K Standardized column (hand): T1 179,675 / 1,192,174 = 15.07%,
        # Total 216,468 / 1,192,174 = 18.16%. The tagged 13.65% / 15.66% tie to
        # the Advanced RWA 1,316,371 (179,675 and 206,170 / 1,316,371).
        out = extract_holdco_capital(self._citi(), anchor_cet1=14.33)["2025-12-31"]
        self.assertAlmostEqual(out["rwa"], 1_192_174e6)
        self.assertAlmostEqual(out["t1_ratio"], 179_675 / 1_192_174)
        self.assertAlmostEqual(out["total_ratio"], 216_468 / 1_192_174)
        self.assertEqual(round(out["t1_ratio"] * 100, 2), 15.07)
        self.assertEqual(round(out["total_ratio"] * 100, 2), 18.16)
        self.assertAlmostEqual(out["cet1_ratio"], 0.1318)       # already Standardized
        self.assertEqual(sorted(out["_rederived"]), ["t1_ratio", "total_ratio"])

    def test_unexplained_ratio_mismatch_left_as_tagged(self):
        # A mismatch the Advanced RWA does NOT explain is not "corrected".
        P, MX = self._PARENT, self._MX
        facts = [
            _f("us-gaap:CommonEquityTier1CapitalToRiskWeightedAssets", 0.12, P),
            _f("us-gaap:TierOneRiskBasedCapital", 130e6, P),
            _f("us-gaap:TierOneRiskBasedCapitalToRiskWeightedAssets", 0.125, P),
            _f("us-gaap:RiskWeightedAssets", 1_000e6, {**P, MX: self._STD}),
            _f("us-gaap:RiskWeightedAssets", 1_100e6, {**P, MX: self._ADV}),
        ]
        out = extract_holdco_capital(facts)["2025-12-31"]
        self.assertAlmostEqual(out["t1_ratio"], 0.125)
        self.assertNotIn("_rederived", out)

    def test_custom_methodology_axis_and_tier_one_common_tag(self):
        # STT FY2025 10-K (acc 0000093751-26-000124): custom axis + members
        # "BaselIII{standardized,Advanced}ApproachMember", CET1 as
        # stt:TierOneCommonRiskBasedCapitaltoTotalRiskWeightedAssets, RWA as
        # stt:TotalRiskWeightedAssets. Previously: CET1 n/a, Tier 1 / Total the
        # ADVANCED 16.1% / 17.7%. 10-K Standardized: 11.6% / 14.4% / 16.1%.
        AX = "stt:RegulatoryCapitalrequirementsforBankingOrganizationsbyImplementationApproachesAxis"
        std, adv = {AX: "stt:BaselIIIstandardizedApproachMember"}, {AX: "stt:BaselIIIAdvancedApproachMember"}
        facts = [
            _f("stt:TierOneCommonRiskBasedCapitaltoTotalRiskWeightedAssets", 0.130, adv),
            _f("stt:TierOneCommonRiskBasedCapitaltoTotalRiskWeightedAssets", 0.116, std),
            _f("us-gaap:TierOneRiskBasedCapitalToRiskWeightedAssets", 0.161, adv),
            _f("us-gaap:TierOneRiskBasedCapitalToRiskWeightedAssets", 0.144, std),
            _f("us-gaap:CapitalToRiskWeightedAssets", 0.177, adv),
            _f("us-gaap:CapitalToRiskWeightedAssets", 0.161, std),
            _f("stt:TotalRiskWeightedAssets", 114_357e6, adv),
            _f("stt:TotalRiskWeightedAssets", 127_263e6, std),
            _f("us-gaap:TierOneLeverageCapitalToAverageAssets", 0.055, std),
        ]
        out = extract_holdco_capital(facts, anchor_cet1=14.72)["2025-12-31"]
        self.assertAlmostEqual(out["cet1_ratio"], 0.116)
        self.assertAlmostEqual(out["t1_ratio"], 0.144)
        self.assertAlmostEqual(out["total_ratio"], 0.161)
        self.assertAlmostEqual(out["rwa"], 127_263e6)
        self.assertFalse(out["_cblr"])

    def test_other_lines_follow_cet1_entity_member(self):
        # MTB FY2025 10-K (acc 0000036270-26-000010): the regulatory MINIMUMS
        # sit under the actual-ratio concepts with ParentCompanyMember; the
        # actuals under "MAndTMember". Previously Tier 1 / Total read 6% / 8%.
        P, MT = {_AX: "us-gaap:ParentCompanyMember"}, {_AX: "mtb:MAndTMember"}
        facts = [
            _f("mtb:CommonEquityTierOneRiskBasedCapitalToRiskWeightedAssets", 0.045, P),
            _f("us-gaap:TierOneRiskBasedCapitalToRiskWeightedAssets", 0.06, P),
            _f("us-gaap:CapitalToRiskWeightedAssets", 0.08, P),
            _f("us-gaap:CommonEquityTierOneCapitalRatio", 0.1084, MT),
            _f("us-gaap:TierOneRiskBasedCapitalToRiskWeightedAssets", 0.1259, MT),
            _f("us-gaap:CapitalToRiskWeightedAssets", 0.1444, MT),
            _f("us-gaap:TierOneLeverageCapitalToAverageAssets", 0.0998, MT),
        ]
        out = extract_holdco_capital(facts, anchor_cet1=11.81)["2025-12-31"]
        self.assertAlmostEqual(out["cet1_ratio"], 0.1084)
        self.assertAlmostEqual(out["t1_ratio"], 0.1259)
        self.assertAlmostEqual(out["total_ratio"], 0.1444)
        self.assertAlmostEqual(out["lev_ratio"], 0.0998)

    def test_consolidated_cet1_beats_nearer_subsidiary_member(self):
        # WTFC FY2025: consolidated (undimensioned) 10.3% vs a charter member
        # read as parent-like ("…BankCorporation") at 11.8%, nearer the 12.25%
        # FDIC anchor. COF: consolidated 14.3% vs "CapitalOneN.A.Member" 13.4%
        # (anchor 13.21) — which put a bank-sub CET1 beside holdco Tier 1 15.3%.
        for anchor, cons, sub_member, sub in (
                (12.25, 0.103, "wtfc:MacatawaBankCorporationMember", 0.118),
                (13.21, 0.143, "cof:CapitalOneN.A.Member", 0.134)):
            facts = [
                _f("us-gaap:CommonEquityTierOneCapitalRatio", cons),
                _f("us-gaap:CommonEquityTierOneCapitalRatio", sub, {_LE: sub_member}),
            ]
            out = extract_holdco_capital(facts, anchor_cet1=anchor)["2025-12-31"]
            self.assertAlmostEqual(out["cet1_ratio"], cons, msg=sub_member)

    def test_amounts_from_another_entity_are_na(self):
        # WTFC FY2025: consolidated ratios (undimensioned) 10.3 / 11.0 / 12.4%;
        # the only $ amounts are a charter's ($355.5M CET1 = T1, $382.2M total;
        # implied RWA 3.45 / 3.23 / 3.08B disagree) — shown as holdco capital.
        facts = [
            _f("us-gaap:CommonEquityTierOneCapitalRatio", 0.103),
            _f("us-gaap:TierOneRiskBasedCapitalToRiskWeightedAssets", 0.110),
            _f("us-gaap:CapitalToRiskWeightedAssets", 0.124),
            _f("us-gaap:CommonEquityTierOneCapital", 355.5e6, {_LE: "wtfc:MacatawaBankCorporationMember"}),
            _f("us-gaap:TierOneRiskBasedCapital", 355.5e6, {_LE: "wtfc:MacatawaBankCorporationMember"}),
            _f("us-gaap:Capital", 382.2e6, {_LE: "wtfc:MacatawaBankCorporationMember"}),
        ]
        out = extract_holdco_capital(facts, anchor_cet1=12.25)["2025-12-31"]
        self.assertAlmostEqual(out["cet1_ratio"], 0.103)
        self.assertAlmostEqual(out["total_ratio"], 0.124)
        for k in ("cet1_cap", "t1_cap", "total_cap", "tier2_cap", "rwa"):
            self.assertNotIn(k, out, k)
        self.assertEqual(out["_suspect"], ["amounts"])

    def test_consistent_amounts_kept_at_rounded_ratios(self):
        # Ratios rounded to 0.1% move capital ÷ ratio < 1%: not "inconsistent".
        facts = [
            _f("us-gaap:CommonEquityTierOneCapitalRatio", 0.103),
            _f("us-gaap:TierOneRiskBasedCapitalToRiskWeightedAssets", 0.110),
            _f("us-gaap:CommonEquityTierOneCapital", 5_150e6),     # RWA 50.0B
            _f("us-gaap:TierOneRiskBasedCapital", 5_520e6),        # RWA 50.2B
        ]
        out = extract_holdco_capital(facts)["2025-12-31"]
        self.assertAlmostEqual(out["cet1_cap"], 5_150e6)
        self.assertNotIn("_suspect", out)

    def test_capital_stack_violation_blanks_both_sides(self):
        # MTB's FY2022 10-K tags FY2021 CET1 13.11% / Tier 1 11.42% (swapped vs
        # its FY2021 10-K). The filing can't say which is wrong: both n/a.
        facts = [
            _f("us-gaap:CommonEquityTierOneCapitalRatio", 0.1311, period="2021-12-31"),
            _f("us-gaap:TierOneRiskBasedCapitalToRiskWeightedAssets", 0.1142, period="2021-12-31"),
            _f("us-gaap:CapitalToRiskWeightedAssets", 0.1533, period="2021-12-31"),
            _f("us-gaap:TierOneLeverageCapitalToAverageAssets", 0.0870, period="2021-12-31"),
        ]
        out = extract_holdco_capital(facts)["2021-12-31"]
        self.assertNotIn("cet1_ratio", out)
        self.assertNotIn("t1_ratio", out)
        self.assertAlmostEqual(out["total_ratio"], 0.1533)
        self.assertEqual(out["_suspect"], ["cet1_ratio", "t1_ratio"])
        self.assertFalse(out["_cblr"])                   # a blanked CET1 is not CBLR

    def test_cet1_equal_to_tier1_is_not_a_violation(self):
        # No preferred → CET1 = Tier 1 (FFIN 19.99 / 19.99); a derived Tier 1 a
        # rounding hair below CET1 is not a stack break.
        facts = [
            _f("us-gaap:CommonEquityTierOneCapitalRatio", 0.1122),
            _f("us-gaap:TierOneRiskBasedCapitalToRiskWeightedAssets", 0.11219),
            _f("us-gaap:CapitalToRiskWeightedAssets", 0.1386),
        ]
        out = extract_holdco_capital(facts)["2025-12-31"]
        self.assertAlmostEqual(out["t1_ratio"], 0.11219)
        self.assertNotIn("_suspect", out)

    def test_range_minimum_member_is_a_threshold(self):
        # PFIS FY2025: minimums on RangeAxis MinimumMember outranked the parent
        # actuals (percent-tagged 11.28 / 14.31).
        R = {"srt:RangeAxis": "srt:MinimumMember"}
        C = {_AX: "us-gaap:ConsolidatedEntityExcludingVariableInterestEntitiesVIEMember"}
        facts = [
            _f("us-gaap:TierOneRiskBasedCapitalToRiskWeightedAssets", 0.06, R),
            _f("us-gaap:CapitalToRiskWeightedAssets", 0.08, R),
            _f("us-gaap:TierOneRiskBasedCapitalToRiskWeightedAssets", 11.28, C),
            _f("us-gaap:CapitalToRiskWeightedAssets", 14.31, C),
        ]
        out = extract_holdco_capital(facts)["2025-12-31"]
        self.assertAlmostEqual(out["t1_ratio"], 0.1128)
        self.assertAlmostEqual(out["total_ratio"], 0.1431)

    def test_entity_named_on_a_misused_axis(self):
        # GSBC splits holdco / bank on PropertyPlantAndEquipmentByTypeAxis; the
        # bank's 12.6% sat nearer the FDIC anchor than the holdco's 12.3%. CCBG
        # tags a $15M trust-preferred note as "Tier 1 capital".
        PX = "us-gaap:PropertyPlantAndEquipmentByTypeAxis"
        hc, bk = {PX: "gsbc:GreatSouthernBancorpInc.Member"}, {PX: "gsbc:GreatSouthernBankMember"}
        facts = [
            _f("us-gaap:CommonEquityTierOneCapitalRatio", 0.123, hc),
            _f("us-gaap:CommonEquityTierOneCapitalRatio", 0.126, bk),
            _f("us-gaap:CommonEquityTierOneCapital", 643_639e3, hc),
            _f("us-gaap:CommonEquityTierOneCapital", 660_411e3, bk),
        ]
        out = extract_holdco_capital(facts, anchor_cet1=13.32)["2025-12-31"]
        self.assertAlmostEqual(out["cet1_ratio"], 0.123)
        self.assertAlmostEqual(out["cet1_cap"], 643_639e3)
        trust = {"us-gaap:RelatedPartyTransactionsByRelatedPartyAxis": "ccbg:CCBGCapitalTrustIMember"}
        facts = [
            _f("us-gaap:TierOneRiskBasedCapital", 15e6, trust),
            _f("us-gaap:TierOneRiskBasedCapital", 505_340e3, {_LE: "ccbg:CCBGMember"}),
            _f("us-gaap:TierOneRiskBasedCapitalToRiskWeightedAssets", 0.202, {_LE: "ccbg:CCBGMember"}),
        ]
        out = extract_holdco_capital(facts)["2025-12-31"]
        self.assertAlmostEqual(out["t1_cap"], 505_340e3)
        # KFFB (CollateralAxis): an UNHINTED member on that axis is a charter
        # ("…SavingsAndLoanAssociationOfHazard"), not the consolidated fact.
        CX = "us-gaap:CollateralAxis"
        facts = [
            _f("us-gaap:Capital", 50_423e3, {CX: "kffb:KentuckyFirstFederalBancorpMember"}),
            _f("us-gaap:TierOneLeverageCapitalToAverageAssets", 0.136,
               {CX: "kffb:KentuckyFirstFederalBancorpMember"}),
            _f("us-gaap:Capital", 18_247e3,
               {CX: "kffb:FirstFederalSavingsAndLoanAssociationOfHazardMember"}),
            _f("us-gaap:TierOneLeverageCapitalToAverageAssets", 0.216,
               {CX: "kffb:FirstFederalSavingsAndLoanAssociationOfHazardMember"}),
        ]
        out = extract_holdco_capital(facts)["2025-12-31"]
        self.assertAlmostEqual(out["total_cap"], 50_423e3)
        self.assertAlmostEqual(out["lev_ratio"], 0.136)

    def test_amounts_scaled_x1000_vs_total_assets_are_na(self):
        # MTB FY2022 10-K tags FY2022 amounts x1000: CET1 $15,562B / RWA
        # $149,016B (consistent with the 10.44% ratio, so the identity can't
        # see it) beside ~$200B of total assets. Ratios stay; amounts n/a.
        P = "2022-12-31"
        facts = [
            _f("us-gaap:CommonEquityTierOneCapitalRatio", 0.1044, period=P),
            _f("us-gaap:CommonEquityTierOneCapital", 15_562_037e6, period=P),
            _f("us-gaap:TierOneRiskBasedCapital", 17_572_586e6, period=P),
            _f("us-gaap:TierOneRiskBasedCapitalToRiskWeightedAssets", 0.1179, period=P),
            _f("us-gaap:Assets", 200_730e6, period=P),
        ]
        out = extract_holdco_capital(facts)[P]
        self.assertAlmostEqual(out["cet1_ratio"], 0.1044)
        self.assertAlmostEqual(out["t1_ratio"], 0.1179)
        for k in ("cet1_cap", "t1_cap", "rwa"):
            self.assertNotIn(k, out, k)
        self.assertIn("scale", out["_suspect"])

    def test_amounts_in_band_vs_total_assets_kept(self):
        # Custody-bank density (RWA ~0.36x assets, STT) is inside the band.
        facts = [
            _f("us-gaap:CommonEquityTierOneCapitalRatio", 0.116),
            _f("us-gaap:CommonEquityTierOneCapital", 14_810e6),     # RWA 127.7B
            _f("us-gaap:Assets", 353_000e6),
        ]
        out = extract_holdco_capital(facts)["2025-12-31"]
        self.assertAlmostEqual(out["cet1_cap"], 14_810e6)
        self.assertNotIn("_suspect", out)

    def test_consistent_ratio_untouched(self):
        # Ratio ties to Standardized capital / RWA → nothing to do.
        P, MX = self._PARENT, self._MX
        facts = [
            _f("us-gaap:CommonEquityTier1CapitalToRiskWeightedAssets", 0.12, P),
            _f("us-gaap:TierOneRiskBasedCapital", 130e6, P),
            _f("us-gaap:TierOneRiskBasedCapitalToRiskWeightedAssets", 0.13, P),
            _f("us-gaap:RiskWeightedAssets", 1_000e6, {**P, MX: self._STD}),
            _f("us-gaap:RiskWeightedAssets", 1_100e6, {**P, MX: self._ADV}),
        ]
        out = extract_holdco_capital(facts)["2025-12-31"]
        self.assertAlmostEqual(out["t1_ratio"], 0.13)
        self.assertNotIn("_rederived", out)


class TestHoldcoStitchPrefersCleanPeriod(unittest.TestCase):
    """holdco_capital_for: newest filing wins a period — unless its figures
    broke the capital stack (_suspect) and an older filing's are clean."""

    def test_older_clean_fy_replaces_newer_suspect(self):
        import data.sec_filing_scraper as S
        k24 = {"accession": "a24", "form": "10-K", "date": "2024-02-21"}
        k23 = {"accession": "a23", "form": "10-K", "date": "2023-02-22"}
        k22 = {"accession": "a22", "form": "10-K", "date": "2022-02-16"}
        caps = {
            "a24": {"2023-12-31": {"cet1_ratio": 0.1098, "t1_ratio": 0.1229}},
            "a23": {"2022-12-31": {"cet1_ratio": 0.1044, "t1_ratio": 0.1179},
                    "2021-12-31": {"total_ratio": 0.1533,
                                   "_suspect": ["cet1_ratio", "t1_ratio"]}},
            "a22": {"2021-12-31": {"cet1_ratio": 0.1142, "t1_ratio": 0.1311,
                                   "total_ratio": 0.1533}},
        }
        with mock.patch.object(S, "_fdic_cet1", lambda cert: None), \
             mock.patch.object(S, "latest_filing",
                               lambda cik, forms: k24 if forms == ("10-K",) else None), \
             mock.patch.object(S, "_holdco_capital_extract_cached",
                               lambda meta, anchor: caps[meta["accession"]]), \
             mock.patch.object(S, "_fye_month_for", lambda meta: "12"), \
             mock.patch.object(S, "_list_10k_filings", lambda cik, n: [k24, k23, k22]):
            res = S.holdco_capital_for(1)
        self.assertEqual(res["capital"]["2021-12-31"]["cet1_ratio"], 0.1142)
        self.assertEqual(res["capital"]["2021-12-31"]["t1_ratio"], 0.1311)
        self.assertEqual(res["capital"]["2022-12-31"]["cet1_ratio"], 0.1044)

    def test_full_window_still_walks_back_for_a_suspect_year_only(self):
        # 5 years already covered, FY2021 suspect: the walk continues to the
        # FY2021 10-K to replace it, but adds no FY2020 sixth column.
        import data.sec_filing_scraper as S
        metas = [{"accession": f"a{y}", "form": "10-K", "date": f"{y}-02-20"}
                 for y in (2026, 2025, 2024, 2023, 2022)]
        clean = lambda v: {"cet1_ratio": v, "t1_ratio": v + 0.01}
        caps = {
            "a2026": {"2025-12-31": clean(0.108), "2024-12-31": clean(0.116)},
            "a2025": {"2024-12-31": clean(0.116), "2023-12-31": clean(0.109)},
            "a2024": {"2023-12-31": clean(0.109), "2022-12-31": clean(0.104)},
            "a2023": {"2022-12-31": clean(0.104),
                      "2021-12-31": {"total_ratio": 0.15, "_suspect": ["cet1_ratio"]}},
            "a2022": {"2021-12-31": clean(0.114), "2020-12-31": clean(0.100)},
        }
        with mock.patch.object(S, "_fdic_cet1", lambda cert: None),              mock.patch.object(S, "latest_filing",
                               lambda cik, forms: metas[0] if forms == ("10-K",) else None),              mock.patch.object(S, "_holdco_capital_extract_cached",
                               lambda meta, anchor: caps[meta["accession"]]),              mock.patch.object(S, "_fye_month_for", lambda meta: "12"),              mock.patch.object(S, "_list_10k_filings", lambda cik, n: metas):
            res = S.holdco_capital_for(1)
        self.assertEqual(res["capital"]["2021-12-31"]["cet1_ratio"], 0.114)
        self.assertNotIn("2020-12-31", res["capital"])

    def test_better_rule(self):
        from data.sec_filing_scraper import _better
        clean, sus = {"cet1_ratio": 0.1}, {"_suspect": ["t1_ratio"]}
        self.assertTrue(_better(clean, None))
        self.assertTrue(_better(clean, sus))
        self.assertFalse(_better(sus, clean))      # newer clean stays
        self.assertFalse(_better(clean, clean))    # newest filing wins a tie
        self.assertFalse(_better(sus, sus))
        self.assertFalse(_better(None, None))


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

    def test_two_table_conflation_under_one_concept_yields_na(self):
        # HWBK-shape: the SAME concept tags BOTH the ASC 820 recurring table
        # (L1 5, L2 200, grand 205) AND the ASC 825 fair-value-of-financial-
        # instruments disclosure table (L1 111, L2 6, L3 1462 = the loan book).
        # Both pass _fv_clean_total, producing materially-different duplicate
        # level facts the engine can't disambiguate → n/a, never the $1.5B "L3".
        facts = [
            self._fv("us-gaap:AssetsFairValueDisclosure", 5, "1"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 200, "2"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 205, None),
            self._fv("us-gaap:AssetsFairValueDisclosure", 111, "1"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 6, "2"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 1462, "3"),
        ]
        self.assertEqual(extract_fair_value(facts), {})

    def test_grand_far_below_level_sum_yields_na(self):
        # Single candidate per level but the tagged grand (205) is a fraction of
        # the level sum (1667) — not recurring netting, a different table tagged
        # under one concept → n/a (guard 2).
        facts = [
            self._fv("us-gaap:AssetsFairValueDisclosure", 5, "1"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 200, "2"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 1462, "3"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 205, None),
        ]
        self.assertEqual(extract_fair_value(facts), {})

    def test_disclosure_table_near_balance_sheet_yields_na(self):
        # NWBI-liab-shape: levels sum to ~14,835 (deposits/debt at fair value) with
        # NO grand — but total Liabilities is 12,700, so the FV "total" exceeds the
        # whole balance sheet → the ASC 825 disclosure table, not recurring → n/a.
        facts = [
            self._fv("us-gaap:LiabilitiesFairValueDisclosure", 11598, "1"),
            self._fv("us-gaap:LiabilitiesFairValueDisclosure", 139, "2"),
            self._fv("us-gaap:LiabilitiesFairValueDisclosure", 3098, "3"),
            _f("us-gaap:Liabilities", 12700 * self.M),
        ]
        self.assertEqual(extract_fair_value(facts), {})

    def test_near_equal_duplicate_level_collapses_and_renders(self):
        # JPM-shape: the recurring L3 is tagged twice within rounding jitter
        # (28,043 and 28,000) — the SAME measurement, not a second table. It
        # collapses to one value and the side still renders with netting.
        facts = [
            self._fv("us-gaap:AssetsFairValueDisclosure", 100, "1"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 200, "2"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 28043, "3"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 28000, "3"),
            self._fv("us-gaap:AssetsFairValueDisclosure", 28300, None),
        ]
        a = extract_fair_value(facts)["2025-12-31"]["assets"]
        self.assertAlmostEqual(a["l3"], 28043 * self.M)
        self.assertAlmostEqual(a["total"], (100 + 200 + 28043) * self.M)
        self.assertTrue(a["_reconciles"])


class TestSecuritiesPortfolio(unittest.TestCase):
    """The AFS/HTM debt-securities amortized-cost → fair-value bridge
    (extract_securities). Each case mirrors a real-filer shape (FITB/WFC/TFC):
    the gross gain/loss split renders only when it ties the bridge, the net is
    always the directly-tagged fair value − amortized cost, and a missing
    amortized cost or fair value yields n/a, never a guessed number."""

    AFS_AC = "us-gaap:DebtSecuritiesAvailableForSaleAmortizedCostExcludingAccruedInterestAfterAllowanceForCreditLoss"
    AFS_FV = "us-gaap:DebtSecuritiesAvailableForSaleExcludingAccruedInterest"
    AFS_UG = "us-gaap:AvailableForSaleDebtSecuritiesAccumulatedGrossUnrealizedGainBeforeTax"
    AFS_UL = "us-gaap:AvailableForSaleDebtSecuritiesAccumulatedGrossUnrealizedLossBeforeTax"
    HTM_AC = "us-gaap:DebtSecuritiesHeldToMaturityExcludingAccruedInterestAfterAllowanceForCreditLoss"
    HTM_FV = "us-gaap:HeldToMaturitySecuritiesFairValue"
    B = 1e9

    def test_afs_bridge_reconciles_split_shown(self):
        # FITB-shape: AC 39.11, UG 0.03, UL 2.97 → FV 36.17 (bridge ties).
        facts = [
            _f(self.AFS_AC, 39.11 * self.B), _f(self.AFS_UG, 0.03 * self.B),
            _f(self.AFS_UL, 2.97 * self.B), _f(self.AFS_FV, 36.17 * self.B),
        ]
        a = extract_securities(facts)["2025-12-31"]["afs"]
        self.assertAlmostEqual(a["amortized_cost"], 39.11 * self.B)
        self.assertAlmostEqual(a["fair_value"], 36.17 * self.B)
        self.assertAlmostEqual(a["net_unrealized"], (36.17 - 39.11) * self.B, places=2)
        self.assertAlmostEqual(a["unrealized_gain"], 0.03 * self.B)
        self.assertAlmostEqual(a["unrealized_loss"], 2.97 * self.B)
        self.assertTrue(a["_reconciles"])
        self.assertAlmostEqual(a["underwater_pct"], (36.17 - 39.11) / 39.11, places=4)

    def test_split_suppressed_when_bridge_does_not_tie(self):
        # AC 70.0, FV 65.4 (net −4.6) but tagged UG/UL don't tie the bridge →
        # gain/loss dropped, net (directly tagged FV − AC) still shown.
        facts = [
            _f(self.AFS_AC, 70.0 * self.B), _f(self.AFS_FV, 65.4 * self.B),
            _f(self.AFS_UG, 0.1 * self.B), _f(self.AFS_UL, 1.0 * self.B),  # 70+0.1-1=69.1 ≠ 65.4
        ]
        a = extract_securities(facts)["2025-12-31"]["afs"]
        self.assertIsNone(a["unrealized_gain"])
        self.assertIsNone(a["unrealized_loss"])
        self.assertAlmostEqual(a["net_unrealized"], (65.4 - 70.0) * self.B, places=2)
        self.assertFalse(a["_reconciles"])

    def test_missing_fair_value_yields_na(self):
        # Amortized cost tagged but no fair value → no AFS entry (can't compute net).
        facts = [_f(self.AFS_AC, 50.0 * self.B)]
        self.assertEqual(extract_securities(facts), {})

    def test_htm_underwater_net_only(self):
        # WFC-shape HTM: AC 204.1, FV 171.3, no gain/loss split tagged → net only.
        facts = [_f(self.HTM_AC, 204.1 * self.B), _f(self.HTM_FV, 171.3 * self.B)]
        h = extract_securities(facts)["2025-12-31"]["htm"]
        self.assertAlmostEqual(h["net_unrealized"], (171.3 - 204.1) * self.B, places=2)
        self.assertIsNone(h["unrealized_gain"])
        self.assertLess(h["underwater_pct"], -0.15)

    def test_amortized_cost_fragment_rejected_for_bridging_candidate(self):
        # FMCB-shape: a higher-priority HTM amortized-cost concept tags a FRAGMENT
        # ($69.5M, +737% bridge) while the real book (HeldToMaturitySecurities
        # $708.3M) bridges to FV $581.5M at −18%. The fragment is rejected.
        AC_FRAG = "us-gaap:DebtSecuritiesHeldToMaturityAmortizedCostAfterAllowanceForCreditLoss"
        AC_REAL = "us-gaap:HeldToMaturitySecurities"
        facts = [_f(AC_FRAG, 69.5e6), _f(AC_REAL, 708.3e6), _f(self.HTM_FV, 581.5e6)]
        h = extract_securities(facts)["2025-12-31"]["htm"]
        self.assertAlmostEqual(h["amortized_cost"], 708.3e6)
        self.assertAlmostEqual(h["underwater_pct"], (581.5 - 708.3) / 708.3, places=3)

    def test_no_plausible_amortized_cost_yields_na(self):
        # TFIN-shape: the only amortized cost gives an implausible +70% bridge with
        # no gain/loss split to validate it → n/a, never the wrong number.
        facts = [_f(self.HTM_AC, 1.0e6), _f(self.HTM_FV, 1.7e6)]
        self.assertEqual(extract_securities(facts), {})


class TestSecuritiesMultiyear(unittest.TestCase):
    """securities_multiyear_for stitches FY-end-only bridges across recent 10-Ks:
    NEWEST-FIRST periods, FY-ends only (stub quarters dropped), de-duplicated so a
    period shared by two filings keeps the value from the NEWER one. No live
    fetch — _recent_10k_metas and the per-filing extract are stubbed."""

    def _meta(self, date, acc):
        return {"accession": acc, "doc": f"d-{acc}.htm", "date": date, "cik": 99}

    def _run(self, metas, extracts, n_years=5, fye="12"):
        from data import sec_filing_scraper as S
        with mock.patch("data.sec_statements._recent_10k_metas",
                        return_value=metas), \
             mock.patch.object(S, "_fye_month_for", return_value=fye), \
             mock.patch.object(S, "_securities_extract_cached",
                               side_effect=lambda m: extracts[m["accession"]]):
            return S.securities_multiyear_for(99, n_years=n_years)

    def test_stitch_fy_ends_newest_first_dedup(self):
        # Two 10-Ks: the FY2025 10-K tags FY2025+FY2024; the FY2024 10-K tags
        # FY2024+FY2023. FY2024 shared → newer filing's value wins.
        metas = [self._meta("2026-02-26", "A"), self._meta("2025-02-28", "B")]
        extracts = {
            "A": {"2025-12-31": {"afs": {"amortized_cost": 100.0}},
                  "2024-12-31": {"afs": {"amortized_cost": 80.0}}},   # newer FY2024
            "B": {"2024-12-31": {"afs": {"amortized_cost": 79.0}},    # older FY2024
                  "2023-12-31": {"afs": {"amortized_cost": 60.0}}},
        }
        res = self._run(metas, extracts)
        sec = res["securities"]
        self.assertEqual(sorted(sec, reverse=True),
                         ["2025-12-31", "2024-12-31", "2023-12-31"])
        self.assertEqual(sec["2024-12-31"]["afs"]["amortized_cost"], 80.0)  # newer wins
        self.assertEqual([f["accession"] for f in res["filings"]], ["A", "B"])
        self.assertEqual(res["meta"]["accession"], "A")

    def test_stub_quarter_periods_dropped(self):
        # A 10-Q-style mid-year period must never enter a FY-end stitch.
        metas = [self._meta("2026-02-26", "A")]
        extracts = {"A": {"2025-12-31": {"afs": {"amortized_cost": 100.0}},
                          "2025-09-30": {"afs": {"amortized_cost": 95.0}}}}
        res = self._run(metas, extracts)
        self.assertEqual(list(res["securities"]), ["2025-12-31"])

    def test_no_fy_ends_returns_none(self):
        metas = [self._meta("2026-02-26", "A")]
        extracts = {"A": {"2025-09-30": {"afs": {"amortized_cost": 95.0}}}}
        self.assertIsNone(self._run(metas, extracts))

    def test_non_december_fye_accepted(self):
        # A June fiscal-year-end filer (AX) tags FY-ends 06-30; the multi-year
        # stitch must accept them (not assume December) and DROP the off-cycle
        # December stub. Regression for the hardcoded period[5:7]=="12" gate.
        metas = [self._meta("2025-08-21", "A")]
        extracts = {"A": {"2025-06-30": {"afs": {"amortized_cost": 100.0}},
                          "2024-06-30": {"afs": {"amortized_cost": 80.0}},
                          "2024-12-31": {"afs": {"amortized_cost": 88.0}}}}  # stub
        res = self._run(metas, extracts, fye="06")
        self.assertEqual(sorted(res["securities"], reverse=True),
                         ["2025-06-30", "2024-06-30"])
        self.assertNotIn("2024-12-31", res["securities"])   # non-FYE stub dropped

    def test_september_fye_accepted(self):
        # WAFD/CASH (Sep-30 FYE).
        metas = [self._meta("2025-11-20", "A")]
        extracts = {"A": {"2025-09-30": {"afs": {"amortized_cost": 50.0}},
                          "2024-09-30": {"afs": {"amortized_cost": 45.0}}}}
        res = self._run(metas, extracts, fye="09")
        self.assertEqual(list(res["securities"]), ["2025-09-30", "2024-09-30"])

    def test_no_filings_returns_none(self):
        self.assertIsNone(self._run([], {}))


class TestSegmentsMultiyear(unittest.TestCase):
    """segments_multiyear_for stitches the per-filing segment breakdown FY-end-only
    across recent 10-Ks. extract_segments reports a SINGLE FY-end per filing, so
    each 10-K contributes one fiscal year; periods are NEWEST-FIRST, FY-ends only
    (any stub dropped), de-duplicated so a year shared by two filings keeps the
    NEWER filing's value. No live fetch — _recent_10k_metas and the per-filing
    extract are stubbed."""

    def _meta(self, date, acc):
        return {"accession": acc, "doc": f"d-{acc}.htm", "date": date, "cik": 99}

    def _yr(self, consol, segs, measure="NetIncomeLoss"):
        # segs: list of (label, ni). residual = consolidated - Σ reportable ni.
        segments = [{"label": l, "net_income": ni, "revenue": None, "assets": None}
                    for l, ni in segs]
        return {"segments": segments, "consolidated_net_income": consol,
                "reconciling_residual": consol - sum(ni for _, ni in segs),
                "ni_measure": measure}

    def _run(self, metas, extracts, n_years=5, fye="12"):
        from data import sec_filing_scraper as S
        with mock.patch("data.sec_statements._recent_10k_metas",
                        return_value=metas), \
             mock.patch.object(S, "_fye_month_for", return_value=fye), \
             mock.patch.object(S, "_segments_extract_cached",
                               side_effect=lambda m: extracts[m["accession"]]):
            return S.segments_multiyear_for(99, n_years=n_years)

    def test_stitch_fy_ends_newest_first_dedup(self):
        # FY2025 10-K tags FY2025; FY2024 10-K tags FY2024; FY2023 10-K tags FY2023.
        metas = [self._meta("2026-02-26", "A"), self._meta("2025-02-28", "B"),
                 self._meta("2024-02-28", "C")]
        extracts = {
            "A": {"2025-12-31": self._yr(110.0, [("Bank", 90.0), ("Wealth", 15.0)])},
            "B": {"2024-12-31": self._yr(100.0, [("Bank", 82.0), ("Wealth", 13.0)])},
            "C": {"2023-12-31": self._yr(90.0, [("Bank", 75.0), ("Wealth", 11.0)])},
        }
        res = self._run(metas, extracts)
        seg = res["segments"]
        self.assertEqual(sorted(seg, reverse=True),
                         ["2025-12-31", "2024-12-31", "2023-12-31"])
        self.assertEqual([f["accession"] for f in res["filings"]], ["A", "B", "C"])
        self.assertEqual(res["meta"]["accession"], "A")
        # Each surviving period reconciles: Σ reportable + residual == consolidated.
        for p, d in seg.items():
            self.assertAlmostEqual(
                sum(s["net_income"] for s in d["segments"]) + d["reconciling_residual"],
                d["consolidated_net_income"])

    def test_shared_year_keeps_newer_filing(self):
        # Two filings both tag FY2024 (overlapping comparative) → newer wins.
        metas = [self._meta("2026-02-26", "A"), self._meta("2025-02-28", "B")]
        extracts = {
            "A": {"2024-12-31": self._yr(100.0, [("Bank", 85.0), ("Wealth", 13.0)])},
            "B": {"2024-12-31": self._yr(99.0, [("Bank", 84.0), ("Wealth", 12.0)])},
        }
        res = self._run(metas, extracts)
        self.assertEqual(list(res["segments"]), ["2024-12-31"])
        self.assertEqual(res["segments"]["2024-12-31"]["consolidated_net_income"], 100.0)
        # Only the contributing (newer) filing is recorded.
        self.assertEqual([f["accession"] for f in res["filings"]], ["A"])

    def test_stub_period_dropped(self):
        # A non-December period must never enter a FY-end stitch.
        metas = [self._meta("2026-02-26", "A")]
        extracts = {"A": {"2025-12-31": self._yr(110.0, [("Bank", 90.0), ("Wealth", 15.0)]),
                          "2025-09-30": self._yr(80.0, [("Bank", 65.0), ("Wealth", 10.0)])}}
        res = self._run(metas, extracts)
        self.assertEqual(list(res["segments"]), ["2025-12-31"])

    def test_single_segment_filing_yields_none(self):
        # extract_segments returns {} for a single-segment filer → no breakdown.
        metas = [self._meta("2026-02-26", "A")]
        res = self._run(metas, {"A": {}})
        self.assertIsNone(res)

    def test_non_december_fye_accepted(self):
        # A non-December FYE segment breakdown (e.g. a Sep-30 filer) must be kept.
        metas = [self._meta("2025-11-20", "A")]
        extracts = {"A": {"2025-09-30": self._yr(110.0, [("Bank", 90.0), ("Wealth", 15.0)])}}
        res = self._run(metas, extracts, fye="09")
        self.assertEqual(list(res["segments"]), ["2025-09-30"])

    def test_no_filings_returns_none(self):
        self.assertIsNone(self._run([], {}))


class TestFairValueMultiyear(unittest.TestCase):
    """fair_value_multiyear_for stitches the FY-end-only ASC 820 hierarchy across
    recent 10-Ks: NEWEST-FIRST periods, FY-ends only (stub quarters dropped),
    de-duplicated so a period shared by two filings keeps the value from the NEWER
    one, and per-period gated — a side with no clean level total is dropped, a
    period with no surviving side is dropped (never carried forward). No live fetch
    — _recent_10k_metas and the per-filing extract are stubbed."""

    def _meta(self, date, acc):
        return {"accession": acc, "doc": f"d-{acc}.htm", "date": date, "cik": 99}

    def _side(self, l1, l2, l3, grand=None):
        total = sum(v for v in (l1, l2, l3) if v is not None)
        recon = grand is None or abs(grand - total) <= max(abs(total) * 0.01, 5e6)
        return {"l1": l1, "l2": l2, "l3": l3, "total": total, "grand": grand,
                "l3_pct": (l3 / total) if (total and l3 is not None) else None,
                "netting": None if grand is None else grand - total,
                "_reconciles": recon}

    def _run(self, metas, extracts, n_years=5, fye="12"):
        from data import sec_filing_scraper as S
        with mock.patch("data.sec_statements._recent_10k_metas",
                        return_value=metas), \
             mock.patch.object(S, "_fye_month_for", return_value=fye), \
             mock.patch.object(S, "_fair_value_extract_cached",
                               side_effect=lambda m: extracts[m["accession"]]):
            return S.fair_value_multiyear_for(99, n_years=n_years)

    def test_stitch_fy_ends_newest_first_dedup(self):
        # FY2025 10-K tags FY2025+FY2024; FY2024 10-K tags FY2024+FY2023.
        # FY2024 shared → newer filing's value wins.
        metas = [self._meta("2026-02-26", "A"), self._meta("2025-02-28", "B")]
        extracts = {
            "A": {"2025-12-31": {"assets": self._side(100.0, 50.0, 1.0)},
                  "2024-12-31": {"assets": self._side(80.0, 40.0, 1.0)}},   # newer FY2024
            "B": {"2024-12-31": {"assets": self._side(79.0, 39.0, 1.0)},    # older FY2024
                  "2023-12-31": {"assets": self._side(60.0, 30.0, 1.0)}},
        }
        res = self._run(metas, extracts)
        fv = res["fair_value"]
        self.assertEqual(sorted(fv, reverse=True),
                         ["2025-12-31", "2024-12-31", "2023-12-31"])
        self.assertEqual(fv["2024-12-31"]["assets"]["l1"], 80.0)   # newer wins
        self.assertEqual([f["accession"] for f in res["filings"]], ["A", "B"])
        self.assertEqual(res["meta"]["accession"], "A")
        # L1+L2+L3 == total for every surviving side.
        for p in fv:
            for s in fv[p].values():
                self.assertAlmostEqual(
                    sum(v for v in (s["l1"], s["l2"], s["l3"]) if v is not None),
                    s["total"])

    def test_stub_quarter_periods_dropped(self):
        metas = [self._meta("2026-02-26", "A")]
        extracts = {"A": {"2025-12-31": {"assets": self._side(100.0, 50.0, 1.0)},
                          "2025-09-30": {"assets": self._side(95.0, 48.0, 1.0)}}}
        res = self._run(metas, extracts)
        self.assertEqual(list(res["fair_value"]), ["2025-12-31"])

    def test_side_without_total_dropped(self):
        # A side missing a clean level total is dropped; a period with no surviving
        # side is dropped entirely (n/a, never a guess).
        metas = [self._meta("2026-02-26", "A")]
        bad = {"l1": None, "l2": None, "l3": None, "total": None, "grand": None,
               "l3_pct": None, "netting": None, "_reconciles": True}
        extracts = {"A": {"2025-12-31": {"assets": self._side(100.0, 50.0, 1.0),
                                         "liabilities": bad},
                          "2024-12-31": {"liabilities": bad}}}
        res = self._run(metas, extracts)
        fv = res["fair_value"]
        self.assertEqual(list(fv), ["2025-12-31"])            # FY2024 all-bad → dropped
        self.assertIn("assets", fv["2025-12-31"])
        self.assertNotIn("liabilities", fv["2025-12-31"])     # bad side dropped

    def test_netting_side_kept(self):
        # A side whose tagged grand ≠ level sum (counterparty/collateral netting)
        # is a valid reconciling case — kept, not dropped, with _reconciles False.
        metas = [self._meta("2026-02-26", "A")]
        M = 1e6
        # grand 1200M vs level sum 1510M → 310M gap ≫ tolerance → netting case.
        extracts = {"A": {"2025-12-31":
                          {"assets": self._side(1000 * M, 500 * M, 10 * M,
                                                grand=1200 * M)}}}
        res = self._run(metas, extracts)
        s = res["fair_value"]["2025-12-31"]["assets"]
        self.assertFalse(s["_reconciles"])
        self.assertEqual(s["total"], 1510 * M)
        self.assertEqual(s["netting"], 1200 * M - 1510 * M)

    def test_no_fy_ends_returns_none(self):
        metas = [self._meta("2026-02-26", "A")]
        extracts = {"A": {"2025-09-30": {"assets": self._side(95.0, 48.0, 1.0)}}}
        self.assertIsNone(self._run(metas, extracts))

    def test_non_december_fye_accepted(self):
        # A Sep-30 FYE filer's hierarchy must be kept (not dropped as a "stub").
        metas = [self._meta("2025-11-20", "A")]
        extracts = {"A": {"2025-09-30": {"assets": self._side(100.0, 50.0, 1.0)},
                          "2024-09-30": {"assets": self._side(80.0, 40.0, 1.0)}}}
        res = self._run(metas, extracts, fye="09")
        self.assertEqual(sorted(res["fair_value"], reverse=True),
                         ["2025-09-30", "2024-09-30"])

    def test_no_filings_returns_none(self):
        self.assertIsNone(self._run([], {}))


class TestCreditQuality(unittest.TestCase):
    """The CECL allowance & asset-quality summary (extract_credit_quality). Pins the
    loans/ACL reconcile gate, the net-charge-off derivation, and — critically —
    that only the filing's CURRENT period is used (no stale prior-year fallback)."""

    ACL = "us-gaap:FinancingReceivableAllowanceForCreditLossExcludingAccruedInterest"
    GROSS = "us-gaap:FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss"
    NET = "us-gaap:FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss"
    NACC = "us-gaap:FinancingReceivableExcludingAccruedInterestNonaccrual"
    WO = "us-gaap:FinancingReceivableExcludingAccruedInterestAllowanceForCreditLossWriteoff"
    RECOV = "us-gaap:FinancingReceivableExcludingAccruedInterestAllowanceForCreditLossRecovery"
    M = 1e6

    def test_reconciling_acl_and_loans(self):
        # FITB-shape: ACL 2253, gross 122651, net 120398 (net+ACL ties gross),
        # nonaccrual 797 → ratios + coverage.
        facts = [_f(self.ACL, 2253 * self.M), _f(self.GROSS, 122651 * self.M),
                 _f(self.NET, 120398 * self.M), _f(self.NACC, 797 * self.M)]
        d = extract_credit_quality(facts)["2025-12-31"]
        self.assertAlmostEqual(d["acl"], 2253 * self.M)
        self.assertAlmostEqual(d["acl_to_loans"], 2253 / 122651, places=5)
        self.assertAlmostEqual(d["acl_coverage_nonaccrual"], 2253 / 797, places=4)
        self.assertTrue(d["_reconciles"])

    def test_mismatched_net_rejects_trio(self):
        # net + ACL doesn't tie gross → a wrong concept in the trio → n/a.
        facts = [_f(self.ACL, 2000 * self.M), _f(self.GROSS, 120000 * self.M),
                 _f(self.NET, 100000 * self.M)]  # 100000 + 2000 ≠ 120000
        self.assertEqual(extract_credit_quality(facts), {})

    def test_nco_from_writeoff_minus_recovery(self):
        facts = [_f(self.ACL, 2253 * self.M), _f(self.GROSS, 122651 * self.M),
                 _f(self.WO, 925 * self.M), _f(self.RECOV, 187 * self.M)]
        d = extract_credit_quality(facts)["2025-12-31"]
        self.assertAlmostEqual(d["nco"], (925 - 187) * self.M)

    def test_only_current_period_no_stale_fallback(self):
        # ACL+gross fully tagged at an OLD period but only ACL at the current (max)
        # period → n/a, never the stale prior-period figures (the JPM-2023 bug).
        facts = [_f(self.ACL, 2000 * self.M, period="2023-12-31"),
                 _f(self.GROSS, 100000 * self.M, period="2023-12-31"),
                 _f(self.ACL, 2300 * self.M, period="2025-12-31")]  # current: no gross
        self.assertEqual(extract_credit_quality(facts), {})

    def test_missing_gross_loans_yields_na(self):
        self.assertEqual(extract_credit_quality([_f(self.ACL, 2000 * self.M)]), {})

    def test_cecl_split_acl_and_bad_net_ignored(self):
        # FFIN-shape: no undimensioned ACL (only the per-segment CECL split), gross
        # tagged undimensioned, plus a tiny wrong-concept "net" ($56M vs $8.2B) that
        # must be ignored rather than reject the bank. ACL = Σ split leaves.
        SEG = "us-gaap:FinancingReceivablePortfolioSegmentAxis"
        COLL = "us-gaap:LoansAndLeasesReceivableCollectivelyEvaluatedForAllowance"
        INDIV = "us-gaap:LoansAndLeasesReceivableIndividuallyEvaluatedForAllowance"

        def seg(concept, val, mem):
            return Fact(concept, val, "2025-12-31", None, {SEG: mem}, "usd")
        facts = [
            _f(self.GROSS, 8158 * self.M),
            seg(COLL, 50 * self.M, "x:CommercialMember"),
            seg(COLL, 30 * self.M, "x:ConsumerMember"),
            seg(INDIV, 26 * self.M, "x:CommercialMember"),
            _f("us-gaap:NotesReceivableNet", 56 * self.M),
        ]
        d = extract_credit_quality(facts)["2025-12-31"]
        self.assertAlmostEqual(d["acl"], 106 * self.M)          # 50 + 30 + 26
        self.assertAlmostEqual(d["loans_gross"], 8158 * self.M)
        self.assertAlmostEqual(d["acl_to_loans"], 106 / 8158, places=4)
        self.assertFalse(d["_reconciles"])

    def test_dimensional_gross_from_composition_total(self):
        # A filer tagging loans only by segment (no undimensioned gross) renders via
        # the reconcile-gated composition total passed as comp_loan_total.
        SEG = "us-gaap:FinancingReceivablePortfolioSegmentAxis"
        COLL = "us-gaap:LoansAndLeasesReceivableCollectivelyEvaluatedForAllowance"
        facts = [
            _f("us-gaap:Assets", 10000 * self.M),
            Fact(COLL, 80 * self.M, "2025-12-31", None, {SEG: "x:CommercialMember"}, "usd"),
            Fact(COLL, 26 * self.M, "2025-12-31", None, {SEG: "x:ConsumerMember"}, "usd"),
        ]
        d = extract_credit_quality(facts, comp_loan_total=8158 * self.M)["2025-12-31"]
        self.assertAlmostEqual(d["loans_gross"], 8158 * self.M)
        self.assertAlmostEqual(d["acl"], 106 * self.M)


class TestAssetQualityNim(unittest.TestCase):
    """Multi-year company-reported NPL/loans, NCO/loans and NIM (the inputs the
    Financial-Highlights tab fills from the bank's OWN 10-K, never FDIC). Pins the
    ABCB FY2025 shape: nonaccrual ÷ gross loans, the charge-off TOTAL chosen over
    the per-segment rows, and the MD&A average-balance NIM-row parse."""

    GROSS = "us-gaap:FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss"
    NACC = "us-gaap:FinancingReceivableRecordedInvestmentNonaccrualStatus"
    WO = "us-gaap:FinancingReceivableExcludingAccruedInterestAllowanceForCreditLossWriteoff"
    RECOV = "us-gaap:FinancingReceivableAllowanceForCreditLossesRecovery"
    SEG = "us-gaap:FinancingReceivablePortfolioSegmentAxis"
    M = 1e6

    def _instant(self, c, v, period):
        return Fact(c, v, period, None, {}, "usd")

    def _dur(self, c, v, year, members=None):
        return Fact(c, v, f"{year}-12-31", f"{year}-01-01", members or {}, "usd")

    def test_npl_per_year(self):
        # nonaccrual ÷ gross loans at each balance-sheet date.
        facts = [
            self._instant(self.NACC, 109.058 * self.M, "2025-12-31"),
            self._instant(self.GROSS, 21513.522 * self.M, "2025-12-31"),
            self._instant(self.NACC, 102.218 * self.M, "2024-12-31"),
            self._instant(self.GROSS, 20739.906 * self.M, "2024-12-31"),
        ]
        out = extract_npl_nco_by_year(facts)
        self.assertAlmostEqual(out[2025]["npl_loans"], 109.058 / 21513.522, places=5)
        self.assertAlmostEqual(out[2024]["npl_loans"], 102.218 / 20739.906, places=5)

    def test_nco_uses_total_member_not_segment_sum(self):
        # ABCB FY2025: charge-offs tagged per segment PLUS a TotalLoansMember total;
        # the extractor must use the 62.816 total, never the 125.632 segment double-sum.
        facts = [
            self._instant(self.GROSS, 21513.522 * self.M, "2025-12-31"),
            self._dur(self.WO, 42.023 * self.M, 2025, {self.SEG: "us-gaap:CommercialPortfolioSegmentMember"}),
            self._dur(self.WO, 20.793 * self.M, 2025, {self.SEG: "us-gaap:ConsumerPortfolioSegmentMember"}),
            self._dur(self.WO, 62.816 * self.M, 2025, {self.SEG: "us-gaap:TotalLoansMember"}),
            self._dur(self.RECOV, 25.467 * self.M, 2025),
        ]
        out = extract_npl_nco_by_year(facts)
        self.assertAlmostEqual(out[2025]["nco_loans"], (62.816 - 25.467) / 21513.522, places=5)

    def test_nco_prefers_undimensioned_total(self):
        facts = [
            self._instant(self.GROSS, 20739.906 * self.M, "2024-12-31"),
            self._dur(self.WO, 68.114 * self.M, 2024),       # undimensioned
            self._dur(self.RECOV, 29.257 * self.M, 2024),
        ]
        out = extract_npl_nco_by_year(facts)
        self.assertAlmostEqual(out[2024]["nco_loans"], (68.114 - 29.257) / 20739.906, places=5)

    def test_no_loan_total_yields_na(self):
        # Charge-offs tagged ONLY by segment (no total) → NCO is n/a, never a guess.
        facts = [
            self._instant(self.GROSS, 21000 * self.M, "2025-12-31"),
            self._dur(self.WO, 40 * self.M, 2025, {self.SEG: "us-gaap:CommercialPortfolioSegmentMember"}),
            self._dur(self.RECOV, 25 * self.M, 2025),
        ]
        out = extract_npl_nco_by_year(facts)
        self.assertNotIn("nco_loans", out.get(2025, {}))

    _NIM_HTML = b"""<table>
      <tr><td></td><td></td><td>Year Ended December 31,</td></tr>
      <tr><td></td><td>2025</td><td>2024</td><td>2023</td></tr>
      <tr><td>Total interest-earning assets</td><td>24,836,731</td><td>1,398,314</td><td>5.63</td>
          <td>23,968,054</td><td>1,382,114</td><td>5.77</td>
          <td>23,259,072</td><td>1,284,215</td><td>5.52</td></tr>
      <tr><td>Net interest income</td><td>940,712</td><td>853,020</td><td>838,824</td></tr>
      <tr><td>Net interest margin</td><td>3.79</td><td>%</td><td>3.56</td><td>%</td><td>3.61</td><td>%</td></tr>
    </table>"""

    def test_nim_row_parsed_per_year(self):
        out = extract_nim_by_year(self._NIM_HTML)
        self.assertAlmostEqual(out[2025], 0.0379)
        self.assertAlmostEqual(out[2024], 0.0356)
        self.assertAlmostEqual(out[2023], 0.0361)

    def test_nim_computed_when_row_absent(self):
        # The table mentions net interest margin (caption) but has NO parseable
        # margin ROW → compute NII ÷ avg earning assets from the same table.
        html = self._NIM_HTML.replace(
            b"<tr><td>Net interest margin</td><td>3.79</td><td>%</td>"
            b"<td>3.56</td><td>%</td><td>3.61</td><td>%</td></tr>",
            b"<tr><td>Yield and net interest margin summary</td></tr>")
        out = extract_nim_by_year(html)
        self.assertAlmostEqual(out[2025], 940712 / 24836731, places=5)
        self.assertAlmostEqual(out[2024], 853020 / 23968054, places=5)

    def test_no_nim_table_yields_empty(self):
        self.assertEqual(extract_nim_by_year(b"<table><tr><td>nothing</td></tr></table>"), {})

    def test_latest_year_captured_with_change_column(self):
        # ZION: the year header carries a leading label AND a '2025/2024 Change'
        # column alongside the years, and the NIM cell glues the percent ('3.21%').
        # The old pure-years header check rejected this row, so the latest FY's NIM
        # landed n/a (or was read off the prior year). Detect the header by year
        # count and align the 3 NIM values to the 3 years.
        html = (b"<table>"
                b"<tr><td>(Dollar amounts in millions)</td><td>2025/2024 Change</td>"
                b"<td>2025</td><td>2024</td><td>2023</td></tr>"
                b"<tr><td>Net interest margin</td><td></td>"
                b"<td>3.21%</td><td>3.00%</td><td>3.02%</td></tr>"
                b"</table>")
        out = extract_nim_by_year(html)
        self.assertAlmostEqual(out[2025], 0.0321)
        self.assertAlmostEqual(out[2024], 0.0300)
        self.assertAlmostEqual(out[2023], 0.0302)

    def test_latest_year_captured_when_values_not_column_aligned(self):
        # WSFS/FITB: header years sit in early columns but each NIM value is pushed
        # far right by colspans, so positional column lookup fails — an ordered zip
        # of the NIM row's numeric values to the years (counts match) is what lands
        # the latest FY. Suffix '(FTE)' on the label must still match.
        html = (b"<table>"
                b"<tr><td>For the years ended</td><td>2025</td><td>2024</td><td>2023</td></tr>"
                b"<tr><td>Net interest margin (FTE)</td>"
                b"<td>3.11</td><td>%</td><td></td><td>2.90</td><td>%</td>"
                b"<td></td><td>3.05</td><td>%</td></tr>"
                b"</table>")
        out = extract_nim_by_year(html)
        self.assertAlmostEqual(out[2025], 0.0311)
        self.assertAlmostEqual(out[2024], 0.0290)
        self.assertAlmostEqual(out[2023], 0.0305)

    def test_net_yield_on_earning_assets_synonym(self):
        # CBSH states NIM as 'Net yield on interest earning assets' in a table that
        # never says 'net interest margin'. That synonym is the same ratio and must
        # be captured (a 5-year selected-data row).
        html = (b"<table>"
                b"<tr><td></td><td>2025</td><td>2024</td><td>2023</td></tr>"
                b"<tr><td>Net yield on interest earning assets (tax equivalent)</td>"
                b"<td>3.63%</td><td>3.47%</td><td>3.16%</td></tr>"
                b"</table>")
        out = extract_nim_by_year(html)
        self.assertAlmostEqual(out[2025], 0.0363)
        self.assertAlmostEqual(out[2024], 0.0347)
        self.assertAlmostEqual(out[2023], 0.0316)

    def test_lookalike_percentage_change_row_not_taken_as_nim(self):
        # 'Percentage increase (decrease) … in net interest margin' is a DELTA row,
        # not the margin — the leading-anchor matcher must skip it and find the real
        # NIM row below.
        html = (b"<table>"
                b"<tr><td></td><td>2025</td><td>2024</td></tr>"
                b"<tr><td>Percentage increase (decrease) in net interest margin "
                b"compared to the prior year</td><td>6.86%</td><td>4.25%</td></tr>"
                b"<tr><td>Net interest margin (FTE)</td><td>3.63%</td><td>3.47%</td></tr>"
                b"</table>")
        out = extract_nim_by_year(html)
        self.assertAlmostEqual(out[2025], 0.0363)
        self.assertAlmostEqual(out[2024], 0.0347)

    def test_off_count_nim_row_yields_na_not_misaligned(self):
        # If the NIM row's numeric-value count doesn't match the year count, an
        # ordered zip would misalign — so it's left n/a rather than shipping a
        # wrong number (cardinal rule). Here 2 years but 3 numeric values.
        html = (b"<table>"
                b"<tr><td></td><td>2025</td><td>2024</td></tr>"
                b"<tr><td>Net interest margin</td>"
                b"<td>3.21%</td><td>3.00%</td><td>2.95%</td></tr>"
                b"</table>")
        # Falls through to the NII/earning-assets compute path, which is absent
        # here → empty, never a guessed alignment.
        self.assertEqual(extract_nim_by_year(html), {})

    def test_merge_newest_filing_wins_and_truncates(self):
        # Two filings overlapping on 2024: the newer (first) filing's value wins.
        newer_facts = [
            self._instant(self.NACC, 100 * self.M, "2025-12-31"),
            self._instant(self.GROSS, 20000 * self.M, "2025-12-31"),
            self._instant(self.NACC, 90 * self.M, "2024-12-31"),
            self._instant(self.GROSS, 19000 * self.M, "2024-12-31"),
        ]
        older_facts = [
            self._instant(self.NACC, 999 * self.M, "2024-12-31"),   # restated, must lose
            self._instant(self.GROSS, 19000 * self.M, "2024-12-31"),
            self._instant(self.NACC, 80 * self.M, "2023-12-31"),
            self._instant(self.GROSS, 18000 * self.M, "2023-12-31"),
        ]
        merged = extract_asset_quality_nim([
            ({}, newer_facts, None), ({}, older_facts, None)])
        self.assertAlmostEqual(merged[2024]["npl_loans"], 90 / 19000, places=5)  # newer wins
        self.assertAlmostEqual(merged[2023]["npl_loans"], 80 / 18000, places=5)
        self.assertEqual(set(merged), {2025, 2024, 2023})


class TestNimProseFallback(unittest.TestCase):
    """Filers that state NIM only in MD&A narrative prose (CFR/RF/ASB/COLB/KEY),
    never in a year-headed table row, are read by extract_nim_prose. The value must
    bind to the margin phrase AND to an explicit fiscal year and fall in the
    1.00%-6.00% band; deltas, spreads, out-of-band and untied values are dropped."""

    def _html(self, sentence: str) -> bytes:
        return ("<p>" + sentence + "</p>").encode()

    def _assertNim(self, out, expected):
        """Compare {year: fraction} with float tolerance and an exact key set."""
        self.assertEqual(set(out), set(expected))
        for y, v in expected.items():
            self.assertAlmostEqual(out[y], v, places=6)

    def test_value_then_year(self):
        # RF / ASB / COLB phrasing: '… was/of X.XX% … in/for YYYY'.
        self._assertNim(
            extract_nim_prose(self._html(
                "The net interest margin (taxable-equivalent basis) was 3.61 "
                "percent in 2025, reflecting a 7 basis point increase from 2024.")),
            {2025: 0.0361})
        # The current year binds via 'of'; the prior year's 2.78% sits behind 'from'
        # (a delta clause, no binding verb) so it is deliberately NOT extracted —
        # the comparative comes from that year's own current-year statement instead.
        self._assertNim(
            extract_nim_prose(self._html(
                "Net interest margin of 3.03% in 2025 increased 25 bp from 2.78% "
                "in 2024.")),
            {2025: 0.0303})
        # COLB: only the value bound to the (single) 'net interest margin' phrase is
        # taken — 3.83% for FY2025. The comparative 3.57% hangs off 'compared to',
        # not a fresh margin phrase, so it is not attributed here (it lands from that
        # year's own statement). 'for the year ended December 31, 2025' year-tie.
        self._assertNim(
            extract_nim_prose(self._html(
                "Net interest margin, on a tax equivalent basis, was 3.83% for the "
                "year ended December 31, 2025, compared to 3.57% for the year ended "
                "December 31, 2024.")),
            {2025: 0.0383})

    def test_year_before_value(self):
        # KEY phrasing: year named first, value after.
        self._assertNim(
            extract_nim_prose(self._html(
                "Net interest income (TE) for 2025 was $4.7 billion, and the net "
                "interest margin was 2.69%. Compared to 2024, net interest income "
                "increased.")),
            {2025: 0.0269})

    def test_change_delta_form_takes_bound_value_not_prior(self):
        # CFR: '… increased 13 basis points from 3.53% during 2024 to 3.66% during
        # 2025'. The 3.66% binds via 'to' and ties to 2025; 3.53% has no binding
        # verb so the prior-year value is NOT (mis)attributed, and '13 basis points'
        # is never read as a margin.
        out = extract_nim_prose(self._html(
            "As a result, the taxable-equivalent net interest margin increased 13 "
            "basis points from 3.53% during 2024 to 3.66% during 2025."))
        self._assertNim(out, {2025: 0.0366})

    def test_net_interest_spread_rejected(self):
        # A 'net interest spread' sentence must never be read as the margin.
        self.assertEqual(
            extract_nim_prose(self._html(
                "The net interest spread was 2.66% in 2024.")),
            {})

    def test_net_interest_income_between_phrase_and_value_rejected(self):
        # A value owned by 'net interest income' (different metric) sitting between
        # the margin phrase and the number must not be misread as the margin.
        self.assertEqual(
            extract_nim_prose(self._html(
                "Net interest margin remained stable while net interest income "
                "was 3.20% higher in 2025.")),
            {})

    def test_out_of_band_value_rejected(self):
        # A value outside 1.00%-6.00% can't be a NIM (parse error) → dropped.
        self.assertEqual(
            extract_nim_prose(self._html(
                "Net interest margin was 0.45% in 2025.")),
            {})
        self.assertEqual(
            extract_nim_prose(self._html(
                "Net interest margin was 8.50% in 2025.")),
            {})

    def test_value_with_no_year_dropped(self):
        # No explicit fiscal year tied to the value → n/a (never assume the FY).
        self.assertEqual(
            extract_nim_prose(self._html(
                "Net interest margin was 3.40% during the period.")),
            {})


class TestKeyStyleAssetQuality(unittest.TestCase):
    """KEY does not tag the standard nonaccrual concept; it states the nonaccrual
    TOTAL as the gross-loans concept sliced by the performance-status axis, and tags
    its rollforward charge-off under the 'WriteOffs' (capital-O) casing. Both must be
    recognized so KEY's npl_loans/nco_loans land, without loosening the sanity gates."""

    GROSS = "us-gaap:FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss"
    PERF_AXIS = "us-gaap:FinancialInstrumentPerformanceStatusAxis"
    NONPERF = "us-gaap:NonperformingFinancingReceivableMember"
    SEG = "us-gaap:FinancingReceivablePortfolioSegmentAxis"
    WO_KEY = "us-gaap:FinancingReceivableAllowanceForCreditLossesWriteOffs"  # capital O
    RECOV = "us-gaap:FinancingReceivableAllowanceForCreditLossesRecovery"
    B = 1e9
    M = 1e6

    def _instant(self, c, v, period, members=None):
        return Fact(c, v, period, None, members or {}, "usd")

    def _dur(self, c, v, year, members=None):
        return Fact(c, v, f"{year}-12-31", f"{year}-01-01", members or {}, "usd")

    def test_nonaccrual_via_performance_status_member(self):
        # KEY FY2025: nonaccrual total = gross-loans concept sliced ONLY by the
        # performance-status axis (Nonperforming member); the per-segment slices must
        # be ignored (they'd double-count). npl = 0.615B / 106.541B ~= 0.577%.
        facts = [
            self._instant(self.GROSS, 106.541 * self.B, "2025-12-31"),
            self._instant(self.GROSS, 0.615 * self.B, "2025-12-31",
                          {self.PERF_AXIS: self.NONPERF}),                       # total
            self._instant(self.GROSS, 0.420 * self.B, "2025-12-31",
                          {self.SEG: "us-gaap:CommercialPortfolioSegmentMember",
                           self.PERF_AXIS: self.NONPERF}),                       # segment
            self._instant(self.GROSS, 0.195 * self.B, "2025-12-31",
                          {self.SEG: "us-gaap:ConsumerPortfolioSegmentMember",
                           self.PERF_AXIS: self.NONPERF}),                       # segment
        ]
        out = extract_npl_nco_by_year(facts)
        self.assertAlmostEqual(out[2025]["npl_loans"], 0.615 / 106.541, places=5)

    def test_writeoffs_capital_o_casing_recognized(self):
        # KEY tags the charge-off as 'WriteOffs' (capital O) — the singular-lowercase
        # concept alone left NCO None. nco = (0.517B - 0.087B) / 106.541B ~= 0.404%.
        facts = [
            self._instant(self.GROSS, 106.541 * self.B, "2025-12-31"),
            self._dur(self.WO_KEY, 0.517 * self.B, 2025),
            self._dur(self.RECOV, 0.087 * self.B, 2025),
        ]
        out = extract_npl_nco_by_year(facts)
        self.assertAlmostEqual(out[2025]["nco_loans"], (0.517 - 0.087) / 106.541, places=5)

    def test_perf_status_total_ambiguous_yields_na(self):
        # Two competing performance-status totals (no single unambiguous total) →
        # never guess which is the nonaccrual total; npl_loans is n/a.
        facts = [
            self._instant(self.GROSS, 100 * self.B, "2025-12-31"),
            self._instant(self.GROSS, 0.6 * self.B, "2025-12-31",
                          {self.PERF_AXIS: self.NONPERF}),
            self._instant(self.GROSS, 0.7 * self.B, "2025-12-31",
                          {self.PERF_AXIS: "us-gaap:NonaccrualMember"}),
        ]
        out = extract_npl_nco_by_year(facts)
        self.assertNotIn("npl_loans", out.get(2025, {}))

    def test_standard_nonaccrual_concept_still_preferred(self):
        # The dedicated nonaccrual concept, when present, must still drive npl and the
        # perf-status fallback must not override it.
        facts = [
            self._instant(self.GROSS, 100 * self.B, "2025-12-31"),
            self._instant("us-gaap:FinancingReceivableRecordedInvestmentNonaccrualStatus",
                          0.5 * self.B, "2025-12-31"),
            self._instant(self.GROSS, 0.9 * self.B, "2025-12-31",
                          {self.PERF_AXIS: self.NONPERF}),   # would-be fallback, must lose
        ]
        out = extract_npl_nco_by_year(facts)
        self.assertAlmostEqual(out[2025]["npl_loans"], 0.5 / 100, places=6)


class TestPerformance(unittest.TestCase):
    """The as-reported full-year profitability summary (extract_performance). Pins
    the revenue/PPNR/efficiency combinations, the income-walk reconcile flag, the
    (begin+end)/2 average for ROA/ROE, and the annual-only period rule."""

    Y0, Y1, PY = "2025-01-01", "2025-12-31", "2024-12-31"
    M = 1e6

    def _dur(self, concept, val):
        return Fact(concept, val, self.Y1, self.Y0, {}, "usd")

    def _inst(self, concept, val, end):
        return Fact(concept, val, end, None, {}, "usd")

    def test_full_year_profitability(self):
        # FITB FY2025 shape — the income walk ties net income exactly.
        facts = [
            self._dur("us-gaap:InterestIncomeExpenseNet", 5982 * self.M),
            self._dur("us-gaap:NoninterestIncome", 3035 * self.M),
            self._dur("us-gaap:NoninterestExpense", 5144 * self.M),
            self._dur("us-gaap:ProvisionForLoanLeaseAndOtherLosses", 662 * self.M),
            self._dur("us-gaap:IncomeTaxExpenseBenefit", 689 * self.M),
            self._dur("us-gaap:NetIncomeLoss", 2522 * self.M),
            self._dur("us-gaap:EarningsPerShareDiluted", 3.53),
            self._inst("us-gaap:Assets", 210000 * self.M, self.Y1),
            self._inst("us-gaap:Assets", 200000 * self.M, self.PY),
            self._inst("us-gaap:StockholdersEquity", 21000 * self.M, self.Y1),
            self._inst("us-gaap:StockholdersEquity", 20000 * self.M, self.PY),
        ]
        d = extract_performance(facts)["2025-12-31"]
        self.assertAlmostEqual(d["revenue"], (5982 + 3035) * self.M)
        self.assertAlmostEqual(d["ppnr"], (5982 + 3035 - 5144) * self.M)
        self.assertAlmostEqual(d["efficiency"], 5144 / (5982 + 3035), places=5)
        self.assertTrue(d["_reconciles"])
        self.assertAlmostEqual(d["roa"], 2522 / 205000, places=6)   # avg(210k,200k)
        self.assertAlmostEqual(d["roe"], 2522 / 20500, places=6)
        self.assertTrue(d["_avg_computed"])

    def test_quarterly_only_yields_na(self):
        # Only a 3-month period tagged → no full year → n/a.
        facts = [Fact("us-gaap:NetIncomeLoss", 600 * self.M, "2025-03-31", "2025-01-01", {}, "usd"),
                 Fact("us-gaap:InterestIncomeExpenseNet", 1500 * self.M, "2025-03-31", "2025-01-01", {}, "usd")]
        self.assertEqual(extract_performance(facts), {})

    def test_missing_core_line_yields_na(self):
        facts = [self._dur("us-gaap:NetIncomeLoss", 2522 * self.M),
                 self._dur("us-gaap:InterestIncomeExpenseNet", 5982 * self.M)]
        self.assertEqual(extract_performance(facts), {})

    def test_no_prior_year_balance_yields_na_roa_roe(self):
        # AUDIT-2026-07-02 #23 — only the ENDING balance is tagged (no prior
        # year-end instant), so no real (begin+end)/2 average can be formed.
        # ROA/ROE must be n/a, never computed on the single period-end balance
        # and flagged identically to a true average.
        facts = [
            self._dur("us-gaap:InterestIncomeExpenseNet", 5982 * self.M),
            self._dur("us-gaap:NoninterestIncome", 3035 * self.M),
            self._dur("us-gaap:NoninterestExpense", 5144 * self.M),
            self._dur("us-gaap:ProvisionForLoanLeaseAndOtherLosses", 662 * self.M),
            self._dur("us-gaap:IncomeTaxExpenseBenefit", 689 * self.M),
            self._dur("us-gaap:NetIncomeLoss", 2522 * self.M),
            # Ending balances only — prior-year (2024-12-31) instants absent.
            self._inst("us-gaap:Assets", 210000 * self.M, self.Y1),
            self._inst("us-gaap:StockholdersEquity", 21000 * self.M, self.Y1),
        ]
        d = extract_performance(facts)["2025-12-31"]
        self.assertIsNone(d["roa"], "ROA on a single period-end balance is not "
                                    "an average — must be n/a")
        self.assertIsNone(d["roe"])
        # The faithfully-tagged income lines still render.
        self.assertAlmostEqual(d["revenue"], (5982 + 3035) * self.M)


class TestFinancialHighlights(unittest.TestCase):
    """The one-page snapshot (extract_financial_highlights) composes the balance-
    sheet totals with the already-tested profitability/credit extractors. Pins the
    composition and the assets-required n/a rule."""

    Y0, Y1, PY = "2025-01-01", "2025-12-31", "2024-12-31"
    M = 1e6

    def _dur(self, c, v):
        return Fact(c, v, self.Y1, self.Y0, {}, "usd")

    def _inst(self, c, v, end):
        return Fact(c, v, end, None, {}, "usd")

    def test_highlights_compose(self):
        facts = [
            self._inst("us-gaap:Assets", 210000 * self.M, self.Y1),
            self._inst("us-gaap:Assets", 200000 * self.M, self.PY),
            self._inst("us-gaap:Deposits", 170000 * self.M, self.Y1),
            self._inst("us-gaap:StockholdersEquity", 21000 * self.M, self.Y1),
            self._inst("us-gaap:StockholdersEquity", 20000 * self.M, self.PY),
            self._dur("us-gaap:InterestIncomeExpenseNet", 5982 * self.M),
            self._dur("us-gaap:NoninterestIncome", 3035 * self.M),
            self._dur("us-gaap:NoninterestExpense", 5144 * self.M),
            self._dur("us-gaap:NetIncomeLoss", 2522 * self.M),
            self._dur("us-gaap:EarningsPerShareDiluted", 3.53),
            self._inst("us-gaap:FinancingReceivableAllowanceForCreditLossExcludingAccruedInterest", 2253 * self.M, self.Y1),
            self._inst("us-gaap:FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss", 122651 * self.M, self.Y1),
        ]
        h = extract_financial_highlights(facts)
        self.assertAlmostEqual(h["assets"], 210000 * self.M)
        self.assertAlmostEqual(h["deposits"], 170000 * self.M)
        self.assertAlmostEqual(h["loans"], 122651 * self.M)
        self.assertAlmostEqual(h["net_income"], 2522 * self.M)
        self.assertAlmostEqual(h["acl_to_loans"], 2253 / 122651, places=5)
        self.assertAlmostEqual(h["roa"], 2522 / 205000, places=6)

    def test_no_assets_yields_na(self):
        self.assertEqual(
            extract_financial_highlights([self._dur("us-gaap:NetIncomeLoss", 100 * self.M)]), {})


class TestFyeMonth(unittest.TestCase):
    """_fye_month_from_facts derives the filer's fiscal-year-end month from its own
    annual-duration facts (the modal ~one-year period-end month) — so off-cycle
    filers (Jun/Sep) are no longer dropped by a hardcoded-December FY-end gate."""

    def _ann(self, end, start):
        return Fact("us-gaap:NetIncomeLoss", 1.0, end, start, {}, "usd")

    def test_june_fye(self):
        from data.sec_filing_scraper import _fye_month_from_facts
        facts = [self._ann("2025-06-30", "2024-07-01"),
                 self._ann("2024-06-30", "2023-07-01")]
        self.assertEqual(_fye_month_from_facts(facts), "06")

    def test_september_fye(self):
        from data.sec_filing_scraper import _fye_month_from_facts
        facts = [self._ann("2025-09-30", "2024-10-01")]
        self.assertEqual(_fye_month_from_facts(facts), "09")

    def test_modal_month_ignores_stub_quarter(self):
        # Three Dec annuals + one 3-month stub: modal month is December.
        from data.sec_filing_scraper import _fye_month_from_facts
        facts = [self._ann("2025-12-31", "2025-01-01"),
                 self._ann("2024-12-31", "2024-01-01"),
                 self._ann("2023-12-31", "2023-01-01"),
                 Fact("us-gaap:NetIncomeLoss", 1.0, "2025-09-30", "2025-07-01", {}, "usd")]
        self.assertEqual(_fye_month_from_facts(facts), "12")

    def test_no_annual_duration_returns_none(self):
        from data.sec_filing_scraper import _fye_month_from_facts
        facts = [Fact("us-gaap:Assets", 1.0, "2025-12-31", None, {}, "usd")]  # instant only
        self.assertIsNone(_fye_month_from_facts(facts))


class TestSegments(unittest.TestCase):
    """Business-segment reconstruction (extract_segments). Pins the OperatingSegments
    member filter (eliminations/totals excluded), the consolidated reconciling
    residual, and the ≥2-segment rule."""

    Y0, Y1, M = "2025-01-01", "2025-12-31", 1e6
    SEG = "us-gaap:StatementBusinessSegmentsAxis"
    CON = "us-gaap:ConsolidationItemsAxis"
    OPSEG = "us-gaap:OperatingSegmentsMember"
    ELIM = "us-gaap:IntersegmentEliminationMember"

    def _seg(self, concept, val, segmem, consol=None):
        mem = {self.SEG: segmem}
        if consol:
            mem[self.CON] = consol
        return Fact(concept, val, self.Y1, self.Y0, mem, "usd")

    def _cons(self, concept, val):
        return Fact(concept, val, self.Y1, self.Y0, {}, "usd")

    def test_two_segments_with_residual(self):
        facts = [
            self._cons("us-gaap:NetIncomeLoss", 7000 * self.M),
            self._seg("us-gaap:NetIncomeLoss", 5000 * self.M, "x:RetailBankingSegmentMember", self.OPSEG),
            self._seg("us-gaap:NetIncomeLoss", 4000 * self.M, "x:CorporateBankingSegmentMember", self.OPSEG),
        ]
        d = extract_segments(facts)["2025-12-31"]
        self.assertEqual(len(d["segments"]), 2)
        self.assertAlmostEqual(d["consolidated_net_income"], 7000 * self.M)
        self.assertAlmostEqual(d["reconciling_residual"], (7000 - 9000) * self.M)
        self.assertIn("Retail Banking", {s["label"] for s in d["segments"]})

    def test_eliminations_excluded(self):
        facts = [
            self._cons("us-gaap:NetIncomeLoss", 7000 * self.M),
            self._seg("us-gaap:NetIncomeLoss", 5000 * self.M, "x:RetailBankingSegmentMember", self.OPSEG),
            self._seg("us-gaap:NetIncomeLoss", 4000 * self.M, "x:CorporateBankingSegmentMember", self.OPSEG),
            self._seg("us-gaap:NetIncomeLoss", -100 * self.M, "x:RetailBankingSegmentMember", self.ELIM),
        ]
        d = extract_segments(facts)["2025-12-31"]
        self.assertEqual(len(d["segments"]), 2)

    def test_single_segment_yields_na(self):
        facts = [self._cons("us-gaap:NetIncomeLoss", 7000 * self.M),
                 self._seg("us-gaap:NetIncomeLoss", 7000 * self.M, "x:RetailBankingSegmentMember", self.OPSEG)]
        self.assertEqual(extract_segments(facts), {})

    def test_large_residual_rejected(self):
        # CTBI shape: a "Corporate" segment equal to the consolidated total
        # double-counts → residual (−100) exceeds consolidated (98) → n/a.
        facts = [self._cons("us-gaap:NetIncomeLoss", 98 * self.M),
                 self._seg("us-gaap:NetIncomeLoss", 100 * self.M, "x:CommunityBankingMember", self.OPSEG),
                 self._seg("us-gaap:NetIncomeLoss", 98 * self.M, "x:CorporateMember", self.OPSEG)]
        self.assertEqual(extract_segments(facts), {})

    # ── disclosed-measure fallback (ZION/VLY: no per-segment NI tagged) ──
    PRETAX = "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic"

    def test_disclosed_pretax_recovered_when_no_segment_ni(self):
        # ZION shape: no per-segment NetIncomeLoss, but per-segment PRETAX income
        # IS tagged and reconciles. The table is surfaced on pretax, labelled as
        # the disclosed measure (ni_measure None), residual = consol − Σ segments.
        facts = [
            self._cons("us-gaap:NetIncomeLoss", 900 * self.M),     # consolidated NI (no seg NI)
            self._cons(self.PRETAX, 1175 * self.M),
            self._seg(self.PRETAX, 344 * self.M, "x:ZionsBankSegmentMember", self.OPSEG),
            self._seg(self.PRETAX, 287 * self.M, "x:CaliforniaBankSegmentMember", self.OPSEG),
            self._seg(self.PRETAX, 281 * self.M, "x:AmegySegmentMember", self.OPSEG),
        ]
        d = extract_segments(facts)["2025-12-31"]
        self.assertIsNone(d["ni_measure"])                          # NOT relabelled net income
        self.assertEqual(d["consolidated_net_income"], None)
        self.assertEqual(d["disclosed_label"], "Pre-tax income ($)")
        self.assertAlmostEqual(d["disclosed_consolidated"], 1175 * self.M)
        self.assertAlmostEqual(d["disclosed_residual"], (1175 - (344 + 287 + 281)) * self.M)
        self.assertEqual(len(d["segments"]), 3)
        # Σ reportable + residual ties the disclosed consolidated total.
        self.assertAlmostEqual(
            sum(s["disclosed"] for s in d["segments"]) + d["disclosed_residual"],
            d["disclosed_consolidated"])

    def test_disclosed_fallback_excludes_eliminations_no_double_count(self):
        # A total/elimination row on the segment axis must NOT enter the sum — only
        # OperatingSegments members do (via _seg_of), so no double counting.
        facts = [
            self._cons(self.PRETAX, 600 * self.M),
            self._seg(self.PRETAX, 400 * self.M, "x:ASegmentMember", self.OPSEG),
            self._seg(self.PRETAX, 250 * self.M, "x:BSegmentMember", self.OPSEG),
            self._seg(self.PRETAX, -50 * self.M, "x:ASegmentMember", self.ELIM),   # elimination
            self._seg(self.PRETAX, 600 * self.M, "x:TotalSegmentMember", "us-gaap:CorporateNonSegmentMember"),
        ]
        d = extract_segments(facts)["2025-12-31"]
        self.assertEqual(len(d["segments"]), 2)                     # only A, B
        self.assertAlmostEqual(sum(s["disclosed"] for s in d["segments"]), 650 * self.M)
        self.assertAlmostEqual(d["disclosed_residual"], (600 - 650) * self.M)

    def test_segment_ni_preferred_over_disclosed(self):
        # When BOTH per-segment NI and per-segment pretax are tagged, NI wins
        # (ni_measure set, no disclosed_* keys).
        facts = [
            self._cons("us-gaap:NetIncomeLoss", 7000 * self.M),
            self._seg("us-gaap:NetIncomeLoss", 5000 * self.M, "x:RetailBankingSegmentMember", self.OPSEG),
            self._seg("us-gaap:NetIncomeLoss", 4000 * self.M, "x:CorporateBankingSegmentMember", self.OPSEG),
            self._cons(self.PRETAX, 9000 * self.M),
            self._seg(self.PRETAX, 6000 * self.M, "x:RetailBankingSegmentMember", self.OPSEG),
            self._seg(self.PRETAX, 5000 * self.M, "x:CorporateBankingSegmentMember", self.OPSEG),
        ]
        d = extract_segments(facts)["2025-12-31"]
        self.assertEqual(d["ni_measure"], "NetIncomeLoss")
        self.assertNotIn("disclosed_label", d)

    def test_disclosed_revenue_fallback_when_no_pretax(self):
        # No NI, no pretax — but per-segment Revenues tagged → revenue surfaced.
        facts = [
            self._cons("us-gaap:Revenues", 662 * self.M),
            self._seg("us-gaap:Revenues", 300 * self.M, "x:ASegmentMember", self.OPSEG),
            self._seg("us-gaap:Revenues", 250 * self.M, "x:BSegmentMember", self.OPSEG),
        ]
        d = extract_segments(facts)["2025-12-31"]
        self.assertIsNone(d["ni_measure"])
        self.assertEqual(d["disclosed_label"], "Total revenue ($)")
        self.assertAlmostEqual(d["disclosed_consolidated"], 662 * self.M)

    def test_disclosed_large_residual_rejected(self):
        # Disclosed-measure path obeys the SAME reconcile gate: residual exceeding
        # the consolidated total → n/a, never a misleading breakdown.
        facts = [
            self._cons(self.PRETAX, 98 * self.M),
            self._seg(self.PRETAX, 100 * self.M, "x:ASegmentMember", self.OPSEG),
            self._seg(self.PRETAX, 98 * self.M, "x:BSegmentMember", self.OPSEG),
        ]
        self.assertEqual(extract_segments(facts), {})


class TestRateRisk(unittest.TestCase):
    """Embedded interest-rate risk (extract_rate_risk): AFS/HTM unrealized marks vs
    equity, composed from the securities extractor. Pins the equity ratio and the
    equity-required n/a rule."""

    AC_A = "us-gaap:DebtSecuritiesAvailableForSaleAmortizedCostExcludingAccruedInterestAfterAllowanceForCreditLoss"
    FV_A = "us-gaap:DebtSecuritiesAvailableForSaleExcludingAccruedInterest"
    AC_H = "us-gaap:DebtSecuritiesHeldToMaturityExcludingAccruedInterestAfterAllowanceForCreditLoss"
    FV_H = "us-gaap:HeldToMaturitySecuritiesFairValue"
    B = 1e9

    def test_embedded_rate_risk(self):
        # WFC-ish: AFS net −3.5B, HTM net −32.8B, equity 178B.
        facts = [_f(self.AC_A, 230 * self.B), _f(self.FV_A, 226.5 * self.B),
                 _f(self.AC_H, 204 * self.B), _f(self.FV_H, 171.2 * self.B),
                 _f("us-gaap:StockholdersEquity", 178 * self.B)]
        d = extract_rate_risk(facts)["2025-12-31"]
        self.assertAlmostEqual(d["total_unrealized"], (-3.5 - 32.8) * self.B, delta=1e7)
        self.assertAlmostEqual(d["unrealized_to_equity"], (-3.5 - 32.8) / 178, places=4)

    def test_no_equity_yields_na(self):
        facts = [_f(self.AC_A, 230 * self.B), _f(self.FV_A, 226.5 * self.B)]
        self.assertEqual(extract_rate_risk(facts), {})


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


class TestHoldcoCapitalMultiyear(unittest.TestCase):
    """holdco_capital_for stitches the FY-end regulatory-capital table across the
    recent 10-Ks so the Financial-Highlights / Capital-Adequacy rows fill the whole
    _MAX_YEARS window — not just the latest filing's 1-2 years (the ABCB FY2021-2023
    blank bug). The freshest filing's periods are kept as-is (including a 10-Q
    quarter-end); older FY-ends fill newest-first, de-duplicated so a year two
    filings both tag keeps the NEWER one. No live fetch — latest_filing,
    _list_10k_filings, the per-filing extract, _fdic_cet1 and _fye_month_for are
    stubbed."""

    html_tables: dict = {}       # accession -> extract_untagged_capital_tables

    def _meta(self, date, acc, form):
        return {"accession": acc, "doc": f"d-{acc}.htm", "date": date,
                "cik": 99, "form": form}

    def _cap(self, cet1, total=None, lev=None):
        d = {"cet1_ratio": cet1}
        if total is not None:
            d["total_ratio"] = total
        if lev is not None:
            d["lev_ratio"] = lev
        return d

    def _run(self, latest_q, latest_k, tenk_list, extracts, fye="12", n=5):
        import data.sec_filing_scraper as S

        def _latest(cik, forms):
            return {("10-Q",): latest_q, ("10-K",): latest_k}[forms]

        with mock.patch.object(S, "_fdic_cet1", return_value=None), \
             mock.patch.object(S, "latest_filing", side_effect=_latest), \
             mock.patch.object(S, "_list_10k_filings",
                               side_effect=lambda cik, limit: tenk_list[:limit]), \
             mock.patch.object(S, "_fye_month_for", return_value=fye), \
             mock.patch.object(S, "_holdco_capital_extract_cached",
                               side_effect=lambda m, anchor: extracts[m["accession"]]), \
             mock.patch.object(S, "_untagged_capital_cached",
                               side_effect=lambda m: self.html_tables.get(m["accession"], {})):
            return S.holdco_capital_for(99)

    def test_stitches_five_fiscal_years_from_10ks(self):
        # Each ABCB-style 10-K tags only its FY-end + the prior FY-end. The latest
        # 10-K alone yields 2 years; the stitch must reach back through the 10-K set
        # to fill FY2021-FY2025 (the exact bug: FY2021-2023 were blank).
        ks = [self._meta("2026-02-26", "K25", "10-K"),
              self._meta("2025-02-28", "K24", "10-K"),
              self._meta("2024-02-28", "K23", "10-K"),
              self._meta("2023-02-28", "K22", "10-K"),
              self._meta("2022-02-28", "K21", "10-K")]
        extracts = {
            "K25": {"2025-12-31": self._cap(0.1317), "2024-12-31": self._cap(0.1265)},
            "K24": {"2024-12-31": self._cap(0.1265), "2023-12-31": self._cap(0.1123)},
            "K23": {"2023-12-31": self._cap(0.1123), "2022-12-31": self._cap(0.0986)},
            "K22": {"2022-12-31": self._cap(0.0986), "2021-12-31": self._cap(0.1046)},
            "K21": {"2021-12-31": self._cap(0.1046), "2020-12-31": self._cap(0.1114)},
        }
        # No 10-Q with capital → falls back to the latest 10-K for the freshest period.
        res = self._run(None, ks[0], ks, extracts)
        cap = res["capital"]
        self.assertEqual(sorted(cap, reverse=True),
                         ["2025-12-31", "2024-12-31", "2023-12-31",
                          "2022-12-31", "2021-12-31"])
        self.assertAlmostEqual(cap["2025-12-31"]["cet1_ratio"], 0.1317)
        self.assertAlmostEqual(cap["2021-12-31"]["cet1_ratio"], 0.1046)  # the recovered tail
        self.assertEqual(res["meta"]["accession"], "K25")

    def test_latest_10q_quarter_kept_and_fy_history_stitched(self):
        # A 10-Q tags the full table → its quarter-end is the timeliest period and
        # is kept as-is; the FY history still fills from the 10-Ks.
        q = self._meta("2026-05-01", "Q1", "10-Q")
        ks = [self._meta("2026-02-26", "K25", "10-K"),
              self._meta("2025-02-28", "K24", "10-K")]
        extracts = {
            "Q1": {"2026-03-31": self._cap(0.1407)},
            "K25": {"2025-12-31": self._cap(0.1317), "2024-12-31": self._cap(0.1265)},
            "K24": {"2024-12-31": self._cap(0.1265), "2023-12-31": self._cap(0.1123)},
        }
        res = self._run(q, ks[0], ks, extracts)
        cap = res["capital"]
        self.assertIn("2026-03-31", cap)                       # 10-Q quarter retained
        self.assertEqual(sorted(cap, reverse=True)[:2],
                         ["2026-03-31", "2025-12-31"])
        self.assertAlmostEqual(cap["2023-12-31"]["cet1_ratio"], 0.1123)  # stitched FY

    def test_shared_year_keeps_newer_filing(self):
        # Two 10-Ks both tag FY2024 (a restated comparative). The newer filing's
        # value wins — the stitch never overwrites an already-supplied period.
        ks = [self._meta("2026-02-26", "K25", "10-K"),
              self._meta("2025-02-28", "K24", "10-K")]
        extracts = {
            "K25": {"2025-12-31": self._cap(0.1317), "2024-12-31": self._cap(0.1265)},
            "K24": {"2024-12-31": self._cap(0.9999), "2023-12-31": self._cap(0.1123)},  # restated
        }
        res = self._run(None, ks[0], ks, extracts)
        self.assertAlmostEqual(res["capital"]["2024-12-31"]["cet1_ratio"], 0.1265)  # newer

    def test_non_december_fye_drops_stub_quarter(self):
        # A Sep-30 FYE filer: only the 09-30 FY-ends enter the stitch; an off-cycle
        # December period a 10-K happens to tag is a stub, not a fiscal year.
        ks = [self._meta("2025-11-20", "K25", "10-K")]
        extracts = {"K25": {"2025-09-30": self._cap(0.1100),
                            "2024-09-30": self._cap(0.1050),
                            "2024-12-31": self._cap(0.1080)}}  # stub
        res = self._run(None, ks[0], ks, extracts, fye="09")
        self.assertEqual(sorted(res["capital"], reverse=True),
                         ["2025-09-30", "2024-09-30"])
        self.assertNotIn("2024-12-31", res["capital"])

    def test_no_capital_anywhere_returns_none(self):
        ks = [self._meta("2026-02-26", "K25", "10-K")]
        # _has_capital is False for an empty dict → no filing qualifies.
        res = self._run(None, ks[0], ks, {"K25": {}})
        self.assertIsNone(res)

    def test_newer_10q_without_capital_tags_is_reported_untagged(self):
        # P2-1 (review 2026-09-24): ONB's Q2-2026 10-Q (filed 2026-07-29) tags
        # no regulatory-capital facts in its iXBRL — the table (CET1 11.09%) is
        # untagged MD&A text — so the table stays on the 10-K (filed 02-19,
        # FY2025 CET1 11.08%). The FY values are unchanged; the newer 10-Q is
        # surfaced as "untagged" so the page says why Q2 is n/a instead of
        # implying the 10-K is the latest filing.
        q = self._meta("2026-07-29", "Q2", "10-Q")
        ks = [self._meta("2026-02-19", "K25", "10-K")]
        extracts = {"Q2": {}, "K25": {"2025-12-31": self._cap(0.1108),
                                      "2024-12-31": self._cap(0.1138)}}
        res = self._run(q, ks[0], ks, extracts)
        self.assertEqual(res["meta"]["accession"], "K25")
        self.assertEqual(sorted(res["capital"]), ["2024-12-31", "2025-12-31"])
        self.assertAlmostEqual(res["capital"]["2025-12-31"]["cet1_ratio"], 0.1108)
        self.assertEqual(res["untagged"]["accession"], "Q2")
        self.assertEqual(res["untagged"]["date"], "2026-07-29")

    def test_tagging_10q_or_older_10q_is_not_untagged(self):
        # A 10-Q that tags the table IS the source (JPM) → nothing untagged.
        q = self._meta("2026-08-06", "Q2", "10-Q")
        ks = [self._meta("2026-02-13", "K25", "10-K")]
        res = self._run(q, ks[0], ks, {"Q2": {"2026-06-30": self._cap(0.142)},
                                       "K25": {"2025-12-31": self._cap(0.146)}})
        self.assertEqual(res["meta"]["accession"], "Q2")
        self.assertIsNone(res["untagged"])
        # An untagged 10-Q OLDER than the 10-K (Q3 10-Q in Nov, 10-K in Feb)
        # is not a newer filing — never reported.
        q_old = self._meta("2025-11-04", "Q3", "10-Q")
        res = self._run(q_old, ks[0], ks, {"Q3": {},
                                           "K25": {"2025-12-31": self._cap(0.146)}})
        self.assertEqual(res["meta"]["accession"], "K25")
        self.assertIsNone(res["untagged"])

    def test_quarterly_reports_newest_untagged_filing(self):
        import data.sec_filing_scraper as S
        import data.sec_statements as SS
        metas = [self._meta("2026-07-29", "Q2", "10-Q"),
                 self._meta("2026-04-29", "Q1", "10-Q"),
                 self._meta("2026-02-19", "K25", "10-K")]
        extracts = {"Q2": {}, "Q1": {},
                    "K25": {"2025-12-31": self._cap(0.1108)}}
        with mock.patch.object(S, "_fdic_cet1", return_value=None), \
             mock.patch.object(SS, "_recent_filing_metas",
                               side_effect=lambda cik, forms, n: metas), \
             mock.patch.object(S, "_holdco_capital_extract_cached",
                               side_effect=lambda m, anchor: extracts[m["accession"]]), \
             mock.patch.object(S, "_untagged_capital_cached", return_value={}):
            res = S.holdco_capital_quarterly_for(99)
        self.assertEqual(res["meta"]["accession"], "K25")
        self.assertEqual(res["untagged"]["accession"], "Q2")
        self.assertEqual(list(res["capital"]), ["2025-12-31"])


class TestUntaggedTenQCapitalTable(unittest.TestCase):
    """Owner decision 2026-10-06: a 10-Q whose capital table is untagged HTML
    (ONB / C Q2-2026) contributes its quarter ONLY from the column dated the
    period end — never a minimum / well-capitalized / required column — and
    only when the same table's prior-year-end column reproduces the 10-K's
    extracted figures to 0.01pt, CET1 ≤ Tier 1 ≤ Total, and CET1 sits in the
    FDIC anchor band. Otherwise n/a, as before. Fixtures are the real 10-Q
    tables (tests/fixtures_untagged_capital.py); no network."""

    # FY2025 as extract_holdco_capital returns it from each bank's 10-K (ONB:
    # tagged ratios; C: Tier 1 / Total re-derived Standardized = capital / RWA).
    ONB_FY25 = {"cet1_ratio": 0.1108, "t1_ratio": 0.1153, "total_ratio": 0.1285,
                "lev_ratio": 0.089, "_confidence": "default"}
    C_FY25 = {"cet1_ratio": 0.1318, "t1_ratio": 179_675 / 1_192_174,
              "total_ratio": 216_468 / 1_192_174, "lev_ratio": 0.0669}
    ONB_ANCHOR, C_ANCHOR = 11.34, 13.79          # FDIC bank-sub CET1 (IDT1CER)

    @classmethod
    def setUpClass(cls):
        from data.sec_filing_scraper import extract_untagged_capital_tables
        from tests.fixtures_untagged_capital import ONB_Q2_2026, C_Q2_2026
        cls.onb = extract_untagged_capital_tables(ONB_Q2_2026.encode("utf-8"))
        cls.c = extract_untagged_capital_tables(C_Q2_2026.encode("utf-8"))

    def _gate(self, parsed, fy, anchor, fy_period="2025-12-31"):
        from data.sec_filing_scraper import _untagged_quarter
        return _untagged_quarter(parsed, {fy_period: dict(fy)}, anchor)

    def test_onb_reads_the_june_column_never_the_minimum(self):
        self.assertEqual(self.onb["period"], "2026-06-30")
        q, why = self._gate(self.onb, self.ONB_FY25, self.ONB_ANCHOR)
        self.assertEqual(why, "ok")
        # "Common equity Tier 1 capital to risk-weighted total assets
        #  | 7.00 | N/A | 11.09 | 11.08" — the 11.09 June 30, 2026 column.
        self.assertAlmostEqual(q["cet1_ratio"], 0.1109)
        self.assertAlmostEqual(q["t1_ratio"], 0.1153)
        self.assertAlmostEqual(q["total_ratio"], 0.1365)
        self.assertAlmostEqual(q["lev_ratio"], 0.0895)
        self.assertNotAlmostEqual(q["cet1_ratio"], 0.07)       # the minimum
        self.assertTrue(q["_untagged_table"])
        self.assertFalse(q["_cblr"])
        self.assertNotIn("cet1_cap", q)                       # ratios only

    def test_onb_selected_data_table_is_not_a_capital_table(self):
        # The five-quarter highlights table ("Tier 1 common equity | 11.09 |
        # 11.11 | ..." under split "June 30," / "2026" header rows) carries no
        # ratio wording on CET1 and no whole-cell dates: never parsed as one.
        self.assertEqual(len(self.onb["tables"]), 2)          # holdco + bank

    def test_onb_bank_subsidiary_table_alone_is_rejected(self):
        bank_only = {"period": "2026-06-30", "tables": [self.onb["tables"][1]]}
        q, why = self._gate(bank_only, self.ONB_FY25, self.ONB_ANCHOR)
        self.assertIsNone(q)                    # Dec 11.05 ≠ 10-K holdco 11.08
        self.assertIn("does not match the 10-K", why)

    def test_dec_column_disagreeing_with_the_10k_is_na(self):
        fy = dict(self.ONB_FY25, cet1_ratio=0.1100)   # 10-K says 11.00, table 11.08
        q, why = self._gate(self.onb, fy, self.ONB_ANCHOR)
        self.assertIsNone(q)
        self.assertIn("cet1_ratio 2025-12-31: table 11.08 vs 10-K 11.00", why)

    def test_no_10k_comparative_is_na(self):
        q, why = self._gate(self.onb, self.ONB_FY25, self.ONB_ANCHOR,
                            fy_period="2024-12-31")
        self.assertIsNone(q)
        self.assertIn("no comparative column", why)

    def test_citi_standardized_column_of_the_june_block(self):
        # One table, a "June 30, 2026" block then a "December 31, 2025" block,
        # columns Required Ratios | Standardized | Required Ratios | Advanced.
        # "CET1 Capital ratio | 11.6 | 12.78 | 10.5 | 11.85" → 12.78.
        self.assertEqual(self.c["period"], "2026-06-30")
        q, why = self._gate(self.c, self.C_FY25, self.C_ANCHOR)
        self.assertEqual(why, "ok")
        self.assertAlmostEqual(q["cet1_ratio"], 0.1278)
        self.assertAlmostEqual(q["t1_ratio"], 0.1468)
        self.assertAlmostEqual(q["total_ratio"], 0.1766)
        self.assertAlmostEqual(q["lev_ratio"], 0.0624)
        for wrong in (0.1185, 0.116, 0.105):    # Advanced, required, required
            self.assertNotAlmostEqual(q["cet1_ratio"], wrong)

    def test_citibank_tables_and_requirement_table_never_used(self):
        # Citibank's June table (CET1 14.68%) has no December column; its
        # December table has no June column; the buffer table's "CET1 Capital
        # ratio requirement 11.6" is a threshold row.
        bank = {"period": "2026-06-30", "tables": self.c["tables"][1:]}
        q, why = self._gate(bank, self.C_FY25, self.C_ANCHOR)
        self.assertIsNone(q)
        from data.sec_filing_scraper import _ut_line
        self.assertIsNone(_ut_line("CET1 Capital ratio requirement"))
        self.assertIsNone(_ut_line("Supplementary Leverage ratio(6)"))
        self.assertIsNone(_ut_line("Common Equity Tier 1 Capital"))   # $ amount
        self.assertEqual(_ut_line("CET1 Capital ratio(1)"), "cet1_ratio")

    def test_zion_summary_table(self):
        # "Total risk-based ratio | 14.0 % | 13.8 % | 13.4 %" (no "capital"
        # wording) is the Total line; "Average equity to average assets" is
        # NOT leverage (it made the leverage line ambiguous before).
        from data.sec_filing_scraper import extract_untagged_capital_tables
        from tests.fixtures_untagged_capital import ZION_Q2_2026
        parsed = extract_untagged_capital_tables(ZION_Q2_2026.encode("utf-8"))
        fy = {"cet1_ratio": 0.115, "t1_ratio": 0.116, "total_ratio": 0.138,
              "lev_ratio": 0.090}                    # ZION FY2025 10-K
        q, why = self._gate(parsed, fy, 10.9)
        self.assertEqual(why, "ok")
        self.assertAlmostEqual(q["cet1_ratio"], 0.118)
        self.assertAlmostEqual(q["t1_ratio"], 0.119)
        self.assertAlmostEqual(q["total_ratio"], 0.140)
        self.assertAlmostEqual(q["lev_ratio"], 0.094)

    def test_row_labels_from_the_universe_diff(self):
        from data.sec_filing_scraper import _ut_line
        # EBC Q2-2026 selected-data table
        self.assertEqual(_ut_line("Tier 1 capital (to average assets) leverage"), "lev_ratio")
        self.assertIsNone(_ut_line("Average equity to average assets (1)"))
        self.assertEqual(_ut_line("Total regulatory capital (to risk-weighted assets)"),
                         "total_ratio")
        # CCNE / ZION wording
        self.assertEqual(_ut_line("Common equity tier 1 ratio"), "cet1_ratio")
        self.assertEqual(_ut_line("Tier 1 risk-based ratio"), "t1_ratio")
        self.assertEqual(_ut_line("Total risk-based ratio (1)"), "total_ratio")
        self.assertIsNone(_ut_line("Total risk-weighted assets"))

    def test_threshold_only_dated_column_is_na(self):
        from data.sec_filing_scraper import extract_untagged_capital_tables
        html = ("<p><ix:nonNumeric name='dei:DocumentPeriodEndDate'>June 30, 2026"
                "</ix:nonNumeric></p><table>"
                "<tr><td></td><td>Minimum required at June 30, 2026</td>"
                "<td>December 31, 2025</td></tr>"
                "<tr><td>CET1 capital ratio</td><td>7.00</td><td>11.08</td></tr>"
                "<tr><td>Tier 1 capital ratio</td><td>8.50</td><td>11.53</td></tr>"
                "<tr><td>Total capital ratio</td><td>10.50</td><td>12.85</td></tr>"
                "</table>")
        parsed = extract_untagged_capital_tables(html.encode())
        self.assertNotIn("2026-06-30", parsed["tables"][0])
        q, why = self._gate(parsed, self.ONB_FY25, self.ONB_ANCHOR)
        self.assertIsNone(q)
        self.assertIn("no CET1 in a column dated the period end", why)

    def test_date_split_across_header_rows_joins_only_same_span_cells(self):
        # AUB Q2-2026: "​ | June 30, | December 31, | June 30," over
        # "​ | 2026 | 2025 | 2025" (U+200B padding), one grid column each.
        from data.sec_filing_scraper import extract_untagged_capital_tables
        head = ("<p><ix:nonNumeric name='dei:DocumentPeriodEndDate'>June 30, 2026"
                "</ix:nonNumeric></p><table>")
        same = (head + "<tr><td>​</td><td>June 30,</td><td>December 31,</td></tr>"
                "<tr><td>​</td><td>2026</td><td>2025</td></tr>"
                "<tr><td>Capital ratios:</td><td>​</td><td>​</td></tr>"
                "<tr><td>CET1 capital ratio</td><td>11.09%</td><td>11.08%</td></tr>"
                "</table>")
        self.assertEqual(extract_untagged_capital_tables(same.encode())["tables"],
                         [{"2026-06-30": {"cet1_ratio": 11.09},
                           "2025-12-31": {"cet1_ratio": 11.08}}])
        # Different widths (a misaligned year row) are never stitched.
        skew = (head + "<tr><td></td><td colspan='2'>June 30,</td>"
                "<td colspan='2'>December 31,</td></tr>"
                "<tr><td></td><td>2026</td><td>2025</td><td>2025</td><td></td></tr>"
                "<tr><td>CET1 capital ratio</td><td>11.09</td><td></td>"
                "<td>11.08</td><td></td></tr></table>")
        self.assertEqual(extract_untagged_capital_tables(skew.encode())["tables"], [])

    def test_stack_and_anchor_gates(self):
        import copy
        broken = copy.deepcopy(self.onb)
        broken["tables"][0]["2026-06-30"]["t1_ratio"] = 11.00   # < CET1 11.09
        q, why = self._gate(broken, self.ONB_FY25, self.ONB_ANCHOR)
        self.assertIsNone(q)
        self.assertIn("capital stack broken", why)
        q, why = self._gate(self.onb, self.ONB_FY25, 4.0)     # 11.09 vs 4.0 > 6pt
        self.assertIsNone(q)
        self.assertIn("anchor band", why)

    def test_leverage_absent_still_accepted(self):
        import copy
        nolev = copy.deepcopy(self.onb)
        for d in nolev["tables"][0].values():
            d.pop("lev_ratio")
        q, why = self._gate(nolev, self.ONB_FY25, self.ONB_ANCHOR)
        self.assertEqual(why, "ok")
        self.assertNotIn("lev_ratio", q)
        self.assertAlmostEqual(q["cet1_ratio"], 0.1109)

    # ── wired through holdco_capital_for / holdco_capital_quarterly_for ──
    def _meta(self, date, acc, form):
        return {"accession": acc, "doc": f"d-{acc}.htm", "date": date,
                "cik": 707179, "form": form}

    def _annual(self, fy25):
        import data.sec_filing_scraper as S
        q = self._meta("2026-07-29", "Q2", "10-Q")
        k = self._meta("2026-02-19", "K25", "10-K")
        extracts = {"Q2": {}, "K25": {"2025-12-31": dict(fy25)}}
        with mock.patch.object(S, "_fdic_cet1", return_value=self.ONB_ANCHOR), \
             mock.patch.object(S, "latest_filing",
                               side_effect=lambda cik, forms: q if forms == ("10-Q",) else k), \
             mock.patch.object(S, "_list_10k_filings", side_effect=lambda cik, n: [k]), \
             mock.patch.object(S, "_fye_month_for", return_value="12"), \
             mock.patch.object(S, "_holdco_capital_extract_cached",
                               side_effect=lambda m, anchor: extracts[m["accession"]]), \
             mock.patch.object(S, "_untagged_capital_cached",
                               side_effect=lambda m: self.onb if m["accession"] == "Q2" else {}):
            return S.holdco_capital_for(707179, 3832)

    def test_annual_fills_the_quarter_and_names_the_table(self):
        res = self._annual(self.ONB_FY25)
        self.assertEqual(sorted(res["capital"]), ["2025-12-31", "2026-06-30"])
        self.assertAlmostEqual(res["capital"]["2026-06-30"]["cet1_ratio"], 0.1109)
        self.assertEqual(res["meta"]["accession"], "K25")       # tagged source
        self.assertIsNone(res["untagged"])                      # quarter now shown
        self.assertEqual([(m["accession"], m["period"]) for m in res["untagged_table"]],
                         [("Q2", "2026-06-30")])

    def test_annual_unverified_table_keeps_todays_na(self):
        res = self._annual(dict(self.ONB_FY25, cet1_ratio=0.1100))
        self.assertEqual(sorted(res["capital"]), ["2025-12-31"])
        self.assertEqual(res["untagged"]["accession"], "Q2")    # labeled n/a
        self.assertEqual(res["untagged_table"], [])

    def test_quarterly_fills_verified_quarters_only(self):
        import data.sec_filing_scraper as S
        import data.sec_statements as SS
        metas = [self._meta("2026-07-29", "Q2", "10-Q"),
                 self._meta("2026-04-29", "Q1", "10-Q"),
                 self._meta("2026-02-19", "K25", "10-K")]
        extracts = {"Q2": {}, "Q1": {}, "K25": {"2025-12-31": dict(self.ONB_FY25)}}
        with mock.patch.object(S, "_fdic_cet1", return_value=self.ONB_ANCHOR), \
             mock.patch.object(SS, "_recent_filing_metas",
                               side_effect=lambda cik, forms, n: metas), \
             mock.patch.object(S, "_holdco_capital_extract_cached",
                               side_effect=lambda m, anchor: extracts[m["accession"]]), \
             mock.patch.object(S, "_untagged_capital_cached",
                               side_effect=lambda m: self.onb if m["accession"] == "Q2" else {}):
            res = S.holdco_capital_quarterly_for(707179, 3832)
        self.assertEqual(sorted(res["capital"]), ["2025-12-31", "2026-06-30"])
        self.assertEqual(res["untagged"]["accession"], "Q1")    # still n/a, named
        self.assertEqual([m["accession"] for m in res["untagged_table"]], ["Q2"])


class TestAssetQualityNimWindow(unittest.TestCase):
    """company_asset_quality_nim must fetch a deep enough 10-K window that the
    OLDEST in-window fiscal year's NPL/NCO is recovered (the ABCB FY2021 drop) —
    each 10-K tags only ~2 comparative years, so a 3-filing window left FY-4 blank.
    A year disclosed by NO filing genuinely stays None (cardinal rule). No live
    fetch — _list_10k_filings and the per-filing fetch/parse are stubbed."""

    GROSS = "us-gaap:FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss"
    NACC = "us-gaap:FinancingReceivableRecordedInvestmentNonaccrualStatus"
    WO = "us-gaap:FinancingReceivableExcludingAccruedInterestAllowanceForCreditLossWriteoff"
    RECOV = "us-gaap:FinancingReceivableAllowanceForCreditLossesRecovery"
    M = 1e6

    def _year_facts(self, year, npl_bp, nco_bp):
        # A single year's nonaccrual instant + charge-off rollforward, sized so
        # npl_loans≈npl_bp/10000 and nco_loans≈nco_bp/10000 against a 20,000M book.
        gross = 20000 * self.M
        facts = [Fact(self.GROSS, gross, f"{year}-12-31", None, {}, "usd"),
                 Fact(self.NACC, gross * npl_bp / 10000.0, f"{year}-12-31", None, {}, "usd"),
                 Fact(self.WO, gross * nco_bp / 10000.0, f"{year}-12-31",
                      f"{year}-01-01", {}, "usd"),
                 Fact(self.RECOV, 0.0, f"{year}-12-31", f"{year}-01-01", {}, "usd")]
        return facts

    def _filing(self, fy):
        return {"accession": f"K{fy}", "doc": f"d-{fy}.htm",
                "date": f"{fy + 1}-02-26", "cik": 99}

    def test_oldest_year_recovered_from_deep_window(self):
        # Five FY 10-Ks, each tagging only its own year here. With the OLD 3-filing
        # window FY2021 (the 5th-oldest) was never fetched → blank. The deepened
        # window reaches it.
        filings = [self._filing(fy) for fy in (2025, 2024, 2023, 2022, 2021)]
        facts = {f"K{fy}": self._year_facts(fy, npl_bp=50, nco_bp=10)
                 for fy in (2025, 2024, 2023, 2022, 2021)}
        res = company_asset_quality_nim_via(self, filings, facts)
        by = res["by_year"]
        self.assertEqual(set(by), {2025, 2024, 2023, 2022, 2021})
        for yr in (2025, 2021):
            self.assertAlmostEqual(by[yr]["npl_loans"], 0.0050, places=4)
            self.assertAlmostEqual(by[yr]["nco_loans"], 0.0010, places=4)

    def test_year_disclosed_by_no_filing_stays_none(self):
        # FY2021's facts are tagged by NO filing (CFR-style: older 10-Ks dropped the
        # nonaccrual/charge-off XBRL). FY2021 NPL/NCO genuinely stays None — never
        # fabricated — while the disclosed years populate.
        filings = [self._filing(fy) for fy in (2025, 2024, 2023, 2022, 2021)]
        facts = {f"K{fy}": self._year_facts(fy, npl_bp=50, nco_bp=10)
                 for fy in (2025, 2024)}
        facts.update({f"K{fy}": [] for fy in (2023, 2022, 2021)})  # nothing tagged
        res = company_asset_quality_nim_via(self, filings, facts)
        by = res["by_year"]
        self.assertAlmostEqual(by[2025]["npl_loans"], 0.0050, places=4)
        self.assertIsNone(by.get(2021, {}).get("npl_loans"))
        self.assertIsNone(by.get(2021, {}).get("nco_loans"))


def company_asset_quality_nim_via(test, filings, facts_by_acc):
    """Drive company_asset_quality_nim with a stubbed filing list + per-filing facts.

    _get returns real HTML bytes carrying the accession (so the MD&A NIM-table
    parser, which runs lxml over them, gets a valid empty document); parse_inline_xbrl
    and instance_facts map a filing back to its stubbed facts list. The facts lists
    are short, so instance_facts is consulted — both paths return the same facts."""
    import data.sec_filing_scraper as S
    from data import cache

    def _acc_for_url(url):
        for f in filings:
            if f["doc"] in url:
                return f["accession"]
        return None

    def _get_html(url):
        acc = _acc_for_url(url)
        return f"<html><!--{acc}--></html>".encode()

    def _parse(html):
        import re as _re
        m = _re.search(rb"<!--(.*?)-->", html)
        acc = m.group(1).decode() if m else None
        return facts_by_acc.get(acc, [])

    with mock.patch.object(S, "_list_10k_filings",
                           side_effect=lambda cik, n: filings[:n]), \
         mock.patch.object(S, "_get", side_effect=_get_html), \
         mock.patch.object(S, "parse_inline_xbrl", side_effect=_parse), \
         mock.patch.object(S, "instance_facts",
                           side_effect=lambda m: facts_by_acc[m["accession"]]), \
         mock.patch.object(cache, "get", return_value=None), \
         mock.patch.object(cache, "put", return_value=None):
        return S.company_asset_quality_nim(99)


if __name__ == "__main__":
    unittest.main()
