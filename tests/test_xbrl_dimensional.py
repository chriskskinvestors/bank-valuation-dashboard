"""
Offline tests for data/xbrl_dimensional.py — no network.

The fixture is a hand-built XBRL instance trimmed from Banner Corporation's
real FY2025 10-K extracted instance (banr-20251231_htm.xml): same namespaces,
same axis/member QNames (InternalCreditAssessmentAxis x
FinancingReceivableRecordedInvestmentByClassOfFinancingReceivableAxis), with
small hand-computed values so every expected number below is exact.

Run:  PYTHONIOENCODING=utf-8 python -m unittest tests.test_xbrl_dimensional
"""

import unittest

from data.xbrl_dimensional import extract_credit_quality, parse_instance

_GRADE_AXIS = "us-gaap:InternalCreditAssessmentAxis"
_CLASS_AXIS = ("us-gaap:FinancingReceivableRecordedInvestment"
               "ByClassOfFinancingReceivableAxis")
_OO = "banr:OwnerOccupiedCommercialRealEstateMember"
_AG = "banr:AgriculturalBusinessMember"


def _ctx(ctx_id: str, members: str = "", period: str = "") -> str:
    seg = f"<segment>{members}</segment>" if members else ""
    period = period or "<instant>2025-12-31</instant>"
    return (f'<context id="{ctx_id}">'
            f'<entity><identifier scheme="http://www.sec.gov/CIK">'
            f'0000946673</identifier>{seg}</entity>'
            f'<period>{period}</period></context>')


def _dim(axis: str, member: str) -> str:
    return (f'<xbrldi:explicitMember dimension="{axis}">'
            f'{member}</xbrldi:explicitMember>')


def _graded(ctx_id: str, grade: str, cls: str | None) -> str:
    members = _dim(_GRADE_AXIS, grade)
    if cls:
        members += _dim(_CLASS_AXIS, cls)
    return _ctx(ctx_id, members)


# One fact (EPS) is deliberately placed BEFORE the contexts/units block to pin
# that resolution is order-independent (contexts resolved after the pass).
FIXTURE_XML = ("""<?xml version="1.0" encoding="utf-8"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
      xmlns:us-gaap="http://fasb.org/us-gaap/2025"
      xmlns:dei="http://xbrl.sec.gov/dei/2025"
      xmlns:banr="http://www.bannerbank.com/20251231">
<us-gaap:EarningsPerShareBasic contextRef="c-dur" unitRef="u-pershare" decimals="2">2.5</us-gaap:EarningsPerShareBasic>
"""
    + _ctx("c-inst")
    + _ctx("c-dur", period="<startDate>2025-01-01</startDate>"
                           "<endDate>2025-12-31</endDate>")
    + _graded("c-pass-oo", "us-gaap:PassMember", _OO)
    + _graded("c-sm-oo", "us-gaap:SpecialMentionMember", _OO)
    + _graded("c-sub-oo", "us-gaap:SubstandardMember", _OO)
    + _graded("c-pass-ag", "us-gaap:PassMember", _AG)
    + _graded("c-sm-ag", "us-gaap:SpecialMentionMember", _AG)
    + _graded("c-sub-ag", "us-gaap:SubstandardMember", _AG)
    + _graded("c-watch-ag", "banr:WatchMember", _AG)
    # substandard + class + EXTRA explicit axis (geography) -> must be skipped
    + _ctx("c-sub-geo",
           _dim(_GRADE_AXIS, "us-gaap:SubstandardMember")
           + _dim(_CLASS_AXIS, _OO)
           + _dim("us-gaap:StatementGeographicalAxis", "banr:WashingtonMember"))
    # substandard + TYPED axis only: without typedMember parsing this fact
    # would masquerade as a grade-only total row
    + _ctx("c-sub-typed",
           _dim(_GRADE_AXIS, "us-gaap:SubstandardMember")
           + '<xbrldi:typedMember dimension="banr:LoanIdentifierAxis">'
             '<banr:LoanId>L-42</banr:LoanId></xbrldi:typedMember>')
    + """
<unit id="u-usd"><measure>iso4217:USD</measure></unit>
<unit id="u-shares"><measure>xbrli:shares</measure></unit>
<unit id="u-pershare"><divide><unitNumerator><measure>iso4217:USD</measure>
</unitNumerator><unitDenominator><measure>xbrli:shares</measure>
</unitDenominator></divide></unit>
<us-gaap:NotesReceivableGross contextRef="c-pass-oo" unitRef="u-usd" decimals="-3">1000</us-gaap:NotesReceivableGross>
<us-gaap:NotesReceivableGross contextRef="c-sm-oo" unitRef="u-usd" decimals="-3">100</us-gaap:NotesReceivableGross>
<us-gaap:NotesReceivableGross contextRef="c-sub-oo" unitRef="u-usd" decimals="-3">200</us-gaap:NotesReceivableGross>
<us-gaap:NotesReceivableGross contextRef="c-pass-ag" unitRef="u-usd" decimals="-3">500</us-gaap:NotesReceivableGross>
<us-gaap:NotesReceivableGross contextRef="c-sm-ag" unitRef="u-usd" decimals="-3">50</us-gaap:NotesReceivableGross>
<us-gaap:NotesReceivableGross contextRef="c-sub-ag" unitRef="u-usd" decimals="-3">80</us-gaap:NotesReceivableGross>
<us-gaap:NotesReceivableGross contextRef="c-watch-ag" unitRef="u-usd" decimals="-3">7</us-gaap:NotesReceivableGross>
<us-gaap:NotesReceivableGross contextRef="c-sub-geo" unitRef="u-usd" decimals="-3">999</us-gaap:NotesReceivableGross>
<us-gaap:NotesReceivableGross contextRef="c-sub-typed" unitRef="u-usd" decimals="-3">333</us-gaap:NotesReceivableGross>
<us-gaap:NotesReceivableGross contextRef="c-inst" unitRef="u-usd" decimals="-3">1937</us-gaap:NotesReceivableGross>
<us-gaap:FinancingReceivableOriginatedInCurrentFiscalYear contextRef="c-sm-oo" unitRef="u-usd" decimals="-3">60</us-gaap:FinancingReceivableOriginatedInCurrentFiscalYear>
<us-gaap:NetIncomeLoss contextRef="c-dur" unitRef="u-usd" decimals="-3">-1234</us-gaap:NetIncomeLoss>
<dei:EntityCommonStockSharesOutstanding contextRef="c-inst" unitRef="u-shares" decimals="0">5000</dei:EntityCommonStockSharesOutstanding>
<us-gaap:OtherAssets contextRef="c-inst" unitRef="u-missing" decimals="-3">11</us-gaap:OtherAssets>
<us-gaap:LoansAndLeasesReceivableNetReportedAmount contextRef="c-orphan" unitRef="u-usd" decimals="-3">42</us-gaap:LoansAndLeasesReceivableNetReportedAmount>
<us-gaap:AccountsReceivableTextBlock contextRef="c-inst">Some narrative text, not numeric.</us-gaap:AccountsReceivableTextBlock>
</xbrl>
""")


def _entry(facts, concept, value):
    """The unique entry for `concept` with `value` (fails the test if absent)."""
    matches = [e for e in facts[concept] if e["value"] == value]
    assert len(matches) == 1, f"{concept}={value}: {len(matches)} matches"
    return matches[0]


class TestParseInstance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts = parse_instance(FIXTURE_XML.encode("utf-8"))

    def test_parses(self):
        self.assertIsInstance(self.facts, dict)

    def test_dimensional_context_resolution_preserves_qnames(self):
        e = _entry(self.facts, "us-gaap:NotesReceivableGross", 100.0)
        self.assertEqual(e["dimensions"], {
            _GRADE_AXIS: "us-gaap:SpecialMentionMember",
            _CLASS_AXIS: _OO,
        })

    def test_numeric_only_text_block_dropped(self):
        self.assertNotIn("us-gaap:AccountsReceivableTextBlock", self.facts)

    def test_negative_value_kept(self):
        e = _entry(self.facts, "us-gaap:NetIncomeLoss", -1234.0)
        self.assertEqual(e["unit"], "USD")

    def test_unit_handling(self):
        usd = _entry(self.facts, "us-gaap:NotesReceivableGross", 1937.0)
        self.assertEqual(usd["unit"], "USD")
        shares = _entry(self.facts, "dei:EntityCommonStockSharesOutstanding",
                        5000.0)
        self.assertEqual(shares["unit"], "shares")
        per_share = _entry(self.facts, "us-gaap:EarningsPerShareBasic", 2.5)
        self.assertEqual(per_share["unit"], "USD/shares")
        unknown = _entry(self.facts, "us-gaap:OtherAssets", 11.0)
        self.assertIsNone(unknown["unit"])  # unitRef points at no unit

    def test_period_extraction(self):
        instant = _entry(self.facts, "us-gaap:NotesReceivableGross", 1937.0)
        self.assertIsNone(instant["period_start"])
        self.assertEqual(instant["period_end"], "2025-12-31")
        duration = _entry(self.facts, "us-gaap:NetIncomeLoss", -1234.0)
        self.assertEqual(duration["period_start"], "2025-01-01")
        self.assertEqual(duration["period_end"], "2025-12-31")

    def test_fact_before_contexts_still_resolves(self):
        # EPS fact appears in the document BEFORE its context and unit
        e = _entry(self.facts, "us-gaap:EarningsPerShareBasic", 2.5)
        self.assertEqual(e["period_end"], "2025-12-31")

    def test_orphan_context_fact_dropped(self):
        self.assertNotIn(
            "us-gaap:LoansAndLeasesReceivableNetReportedAmount", self.facts)

    def test_typed_member_axis_visible(self):
        e = _entry(self.facts, "us-gaap:NotesReceivableGross", 333.0)
        self.assertEqual(e["dimensions"][_GRADE_AXIS],
                         "us-gaap:SubstandardMember")
        self.assertEqual(e["dimensions"]["banr:LoanIdentifierAxis"],
                         "typed:LoanId=L-42")

    def test_malformed_xml_returns_none(self):
        self.assertIsNone(parse_instance(b"<xbrl><unclosed></xbrl>"))

    def test_no_numeric_facts_returns_none(self):
        self.assertIsNone(parse_instance(
            b'<?xml version="1.0"?>'
            b'<xbrl xmlns="http://www.xbrl.org/2003/instance"></xbrl>'))


class TestExtractCreditQuality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts = parse_instance(FIXTURE_XML.encode("utf-8"))
        cls.bd = extract_credit_quality(cls.facts)

    def test_breakdown_exists(self):
        self.assertIsNotNone(self.bd)
        self.assertEqual(self.bd["as_of"], "2025-12-31")
        self.assertEqual(self.bd["concept"], "us-gaap:NotesReceivableGross")
        self.assertEqual(self.bd["totals_source"], "summed_across_classes")

    def test_totals_by_grade_hand_computed(self):
        # pass 1000+500; SM 100+50; substandard 200+80 — the geography-sliced
        # 999 and typed-axis 333 facts MUST be excluded (extra-axis skip), and
        # the vintage concept (60) excluded at concept level.
        self.assertEqual(self.bd["total_by_grade"]["pass"], 1500.0)
        self.assertEqual(self.bd["total_by_grade"]["special_mention"], 150.0)
        self.assertEqual(self.bd["total_by_grade"]["substandard"], 280.0)

    def test_unrecognised_grade_kept_raw_never_bucketed(self):
        self.assertEqual(self.bd["total_by_grade"]["banr:WatchMember"], 7.0)

    def test_grade_members_preserve_raw_qnames(self):
        self.assertEqual(self.bd["grade_members"]["special_mention"],
                         ["us-gaap:SpecialMentionMember"])
        self.assertEqual(self.bd["grade_members"]["substandard"],
                         ["us-gaap:SubstandardMember"])
        self.assertEqual(self.bd["grade_members"]["pass"],
                         ["us-gaap:PassMember"])
        self.assertEqual(self.bd["grade_members"]["banr:WatchMember"],
                         ["banr:WatchMember"])

    def test_by_class_raw_member_keys(self):
        self.assertEqual(self.bd["by_class"][_OO],
                         {"pass": 1000.0, "special_mention": 100.0,
                          "substandard": 200.0})
        self.assertEqual(self.bd["by_class"][_AG],
                         {"pass": 500.0, "special_mention": 50.0,
                          "substandard": 80.0, "banr:WatchMember": 7.0})

    def test_classified_criticized_snl_conventions(self):
        self.assertEqual(self.bd["classified"], 280.0)   # sub + doubtful + loss
        self.assertEqual(self.bd["criticized"], 430.0)   # + special mention

    def test_direct_totals_preferred_over_class_sum(self):
        facts = {"us-gaap:NotesReceivableGross": [
            {"value": 150.0, "unit": "USD", "period_start": None,
             "period_end": "2025-12-31",
             "dimensions": {_GRADE_AXIS: "us-gaap:SpecialMentionMember"}},
            {"value": 90.0, "unit": "USD", "period_start": None,
             "period_end": "2025-12-31",
             "dimensions": {_GRADE_AXIS: "us-gaap:SpecialMentionMember",
                            _CLASS_AXIS: _OO}},
        ]}
        bd = extract_credit_quality(facts)
        self.assertEqual(bd["totals_source"], "direct")
        self.assertEqual(bd["total_by_grade"], {"special_mention": 150.0})
        self.assertIsNone(bd["classified"])   # no substandard/doubtful/loss
        self.assertIsNone(bd["criticized"])   # tagged -> n/a, never guessed

    def test_non_usd_facts_ignored(self):
        facts = {"us-gaap:NotesReceivableGross": [
            {"value": 5.0, "unit": "shares", "period_start": None,
             "period_end": "2025-12-31",
             "dimensions": {_GRADE_AXIS: "us-gaap:PassMember"}},
        ]}
        self.assertIsNone(extract_credit_quality(facts))

    def test_no_graded_facts_returns_none(self):
        self.assertIsNone(extract_credit_quality({}))
        facts = {"us-gaap:NotesReceivableGross": [
            {"value": 1937.0, "unit": "USD", "period_start": None,
             "period_end": "2025-12-31", "dimensions": {}},
        ]}
        self.assertIsNone(extract_credit_quality(facts))

    def test_candidates_without_period_end_return_none(self):
        facts = {"us-gaap:NotesReceivableGross": [
            {"value": 100.0, "unit": "USD", "period_start": None,
             "period_end": None,
             "dimensions": {_GRADE_AXIS: "us-gaap:SpecialMentionMember"}},
        ]}
        self.assertIsNone(extract_credit_quality(facts))  # must not raise


if __name__ == "__main__":
    unittest.main()
