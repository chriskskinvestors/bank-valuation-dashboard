"""(2026-09-30) Multi-class common share counts — the OCFC / FCNCA / CBC shape.

A filer with two common classes tags its balance-sheet share counts only per
class (us-gaap:StatementClassOfStockAxis), and companyfacts drops every
dimensioned fact, so the blob holds no period-end count and the share chain
fell back to something that is not the total:

  • OCFC (post-Flushing, 10-Q 0001004702-26-000120, 2026-06-30): voting
    96,604,195 outstanding + 1,812,000 non-voting common-equivalent. The
    chain served the voting-only dei cover count 96,645,219 → BVPS 24.95 /
    TBVPS 18.53 vs the release's 24.50 / 18.19 on 98,416,195 (8-K
    0001004702-26-000116 EX-99.1).
  • FCNCA (10-Q 0000798941-26-000031): Class A 10,385,222 + Class B
    1,005,185 = 11,390,407 (the count its release prints). The chain served
    the Q2 WEIGHTED AVERAGE 11,729,271 → BVPS 1,716.65 vs the release's
    1,767.79.
  • CBC (10-Q 0001628280-26-057081): Class A 318,247,550 issued, Class B 0
    issued, 78,742,417 treasury (undimensioned) → 239,505,133, the release's
    "Total shares of Class A common stock outstanding 239,505" (thousands).

data/sec_facts_overlay reads the per-class facts from the filing's own
iXBRL and sums them — or marks the count unresolved, and then per-share
metrics render n/a (no single-class / averaged fallback may stand in).

Hermetic: real per-class fact values, synthetic blobs, no network.
Run: python -m unittest tests.test_multiclass_shares
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from data import sec_client  # noqa: E402
from data import sec_facts_overlay as ov  # noqa: E402

TODAY = date.today()
EQ_END = (TODAY - timedelta(days=50)).isoformat()
PRIOR = (TODAY - timedelta(days=50 + 91)).isoformat()
COVER = (TODAY - timedelta(days=15)).isoformat()
FILED = (TODAY - timedelta(days=10)).isoformat()

_AXIS_A = "us-gaap:CommonClassAMember"
_AXIS_B = "us-gaap:CommonClassBMember"
_VOTING = "ocfc:VotingCommonStockMember"
_NVCE = "ocfc:NonvotingCommonEquivalentStockMember"


def _e(concept, cls, val, end=EQ_END):
    return {"concept": concept, "cls": cls, "end": end, "val": float(val)}


def _ocfc_entries(end=EQ_END):
    """OCFC's per-class facts as tagged in 10-Q 0001004702-26-000120: the
    NVCE carries only an ISSUED count; treasury is undimensioned."""
    return [
        _e("CommonStockSharesIssued", _VOTING, 102_258_357, end),
        _e("CommonStockSharesOutstanding", _VOTING, 96_604_195, end),
        _e("CommonStockSharesIssued", _NVCE, 1_812_000, end),
        _e("TreasuryStockCommonShares", None, 5_654_162, end),
        # prior-FY comparatives (voting only — the NVCE is new)
        _e("CommonStockSharesIssued", _VOTING, 62_942_427, PRIOR),
        _e("CommonStockSharesOutstanding", _VOTING, 57_390_569, PRIOR),
        _e("TreasuryStockCommonShares", None, 5_551_858, PRIOR),
    ]


def _fcnca_entries():
    """FCNCA 10-Q 0000798941-26-000031 (duplicates as the instance repeats
    them across the balance sheet and equity statement)."""
    return [
        _e("CommonStockSharesOutstanding", _AXIS_A, 10_385_222),
        _e("CommonStockSharesIssued", _AXIS_A, 10_385_222),
        _e("CommonStockSharesOutstanding", _AXIS_A, 10_385_222),
        _e("CommonStockSharesOutstanding", _AXIS_B, 1_005_185),
        _e("CommonStockSharesIssued", _AXIS_B, 1_005_185),
    ]


def _cbc_entries():
    """CBC 10-Q 0001628280-26-057081: Class B authorized, none issued."""
    return [
        _e("CommonStockSharesIssued", _AXIS_A, 318_247_550),
        _e("CommonStockSharesIssued", _AXIS_B, 0),
        _e("TreasuryStockCommonShares", None, 78_742_417),
    ]


class TestResolveClassShares(unittest.TestCase):
    def test_ocfc_nvce_issued_only_ties_through_treasury(self):
        # voting gap 102,258,357 − 96,604,195 = 5,654,162 = total treasury,
        # so none of it is NVCE: 96,604,195 + 1,812,000 = 98,416,195.
        rec = ov.resolve_class_shares(_ocfc_entries(), EQ_END)
        self.assertEqual(rec["status"], "resolved")
        self.assertEqual(rec["value"], 98_416_195)
        self.assertEqual(rec["classes"][_NVCE], 1_812_000)

    def test_fcnca_sums_both_classes_despite_duplicate_facts(self):
        rec = ov.resolve_class_shares(_fcnca_entries(), EQ_END)
        self.assertEqual(rec["value"], 11_390_407)

    def test_cbc_zero_issued_class_leaves_treasury_to_the_other(self):
        # 318,247,550 − 78,742,417 = 239,505,133
        rec = ov.resolve_class_shares(_cbc_entries(), EQ_END)
        self.assertEqual(rec["status"], "resolved")
        self.assertEqual(rec["value"], 239_505_133)

    def test_single_class_is_not_multiclass(self):
        ents = [_e("CommonStockSharesOutstanding", _AXIS_A, 1_000_000)]
        self.assertIsNone(ov.resolve_class_shares(ents, EQ_END))

    def test_residual_treasury_belongs_to_the_lone_issued_only_class(self):
        # 5,700,000 total − 5,654,162 voting = 45,838 NVCE treasury:
        # 96,604,195 + (1,812,000 − 45,838) = 98,370,357
        ents = _ocfc_entries()
        ents[3] = _e("TreasuryStockCommonShares", None, 5_700_000)
        self.assertEqual(ov.resolve_class_shares(ents, EQ_END)["value"],
                         98_370_357)

    def test_treasury_below_the_known_gap_is_unresolved(self):
        # total treasury can't be smaller than the voting class's own
        # issued − outstanding gap — the tags contradict each other.
        ents = _ocfc_entries()
        ents[3] = _e("TreasuryStockCommonShares", None, 5_600_000)
        rec = ov.resolve_class_shares(ents, EQ_END)
        self.assertEqual(rec["status"], "unresolved")
        self.assertIsNone(rec["value"])

    def test_missing_treasury_tag_is_unresolved(self):
        ents = [e for e in _ocfc_entries()
                if not (e["concept"] == "TreasuryStockCommonShares"
                        and e["end"] == EQ_END)]
        self.assertIsNone(ov.resolve_class_shares(ents, EQ_END)["value"])

    def test_two_issued_only_classes_need_zero_residual_treasury(self):
        ents = [_e("CommonStockSharesIssued", _AXIS_A, 5_000_000),
                _e("CommonStockSharesIssued", _AXIS_B, 1_000_000),
                _e("TreasuryStockCommonShares", None, 200_000)]
        self.assertIsNone(ov.resolve_class_shares(ents, EQ_END)["value"],
                          "200,000 treasury can't be split between A and B")
        ents[2] = _e("TreasuryStockCommonShares", None, 0)
        self.assertEqual(ov.resolve_class_shares(ents, EQ_END)["value"],
                         6_000_000)

    def test_class_tagged_only_in_the_prior_column_is_retired(self):
        """BANC 10-Q 0001628280-26-054876: its NVCE class is tagged at
        2025-12-31 only; the release: "no non-voting common stock equivalents
        outstanding as of June 30, 2026". 157,950,529 + 477,321 = 158,427,850
        → BVPS 2,911,630K / 158,427,850 = 18.378 (release $18.38)."""
        banc_nvce = "banc:NonVotingCommonStockEquivalentsMember"
        ents = [
            _e("CommonStockSharesIssued", "us-gaap:CommonStockMember", 157_955_199),
            _e("CommonStockSharesOutstanding", "us-gaap:CommonStockMember", 157_950_529),
            _e("CommonStockSharesIssued", "us-gaap:NonvotingCommonStockMember", 477_321),
            _e("CommonStockSharesOutstanding", "us-gaap:NonvotingCommonStockMember", 477_321),
            _e("CommonStockSharesIssued", banc_nvce, 5_017_064, PRIOR),
            _e("CommonStockSharesOutstanding", banc_nvce, 5_017_064, PRIOR),
        ]
        rec = ov.resolve_class_shares(ents, EQ_END)
        self.assertEqual(rec["value"], 158_427_850)
        self.assertNotIn(banc_nvce, rec["classes"])

    def test_no_class_count_at_the_date_is_unresolved(self):
        ents = [e for e in _ocfc_entries() if e["end"] == PRIOR] + [
            _e("CommonStockSharesIssued", _NVCE, 1_812_000, PRIOR)]
        rec = ov.resolve_class_shares(ents, EQ_END)
        self.assertEqual(rec["status"], "unresolved")
        self.assertIsNone(rec["value"])

    def test_outstanding_contradicting_issued_minus_treasury_is_unresolved(self):
        """CIA 10-Q 0000024090-26-000043: Class A "outstanding" 54,968,998 =
        issued, yet 4,327,810 Class A treasury (true 50,641,188; dei cover
        50,643,108). Summing the tag would overstate the count 10.5%."""
        ents = [
            _e("CommonStockSharesOutstanding", _AXIS_A, 54_968_998),
            _e("CommonStockSharesIssued", _AXIS_A, 54_968_998),
            _e("TreasuryStockCommonShares", _AXIS_A, 4_327_810),
            _e("CommonStockSharesOutstanding", _AXIS_B, 1_001_714),
            _e("CommonStockSharesIssued", _AXIS_B, 1_001_714),
            _e("TreasuryStockCommonShares", _AXIS_B, 1_001_714),
        ]
        rec = ov.resolve_class_shares(ents, EQ_END)
        self.assertEqual(rec["status"], "unresolved")
        self.assertIsNone(rec["value"])

    def test_consistent_class_treasury_resolves(self):
        """EQBK 10-Q 0001193125-26-340292: A 27,038,988 − 6,460,949 =
        20,578,039 (= its outstanding tag); B fully in treasury → 0."""
        ents = [
            _e("CommonStockSharesIssued", _AXIS_A, 27_038_988),
            _e("TreasuryStockCommonShares", _AXIS_A, 6_460_949),
            _e("CommonStockSharesOutstanding", _AXIS_A, 20_578_039),
            _e("CommonStockSharesIssued", _AXIS_B, 234_903),
            _e("TreasuryStockCommonShares", _AXIS_B, 234_903),
        ]
        rec = ov.resolve_class_shares(ents, EQ_END)
        self.assertEqual(rec["value"], 20_578_039)
        self.assertEqual(rec["classes"][_AXIS_B], 0)

    def test_preferred_member_under_common_concept_is_unresolved(self):
        """CFBK tags its Series D preferred with CommonStockSharesIssued."""
        ents = _fcnca_entries() + [
            _e("CommonStockSharesIssued", "us-gaap:SeriesDPreferredStockMember",
               16_000)]
        self.assertIsNone(ov.resolve_class_shares(ents, EQ_END)["value"])

    def test_conflicting_values_for_one_slot_are_unresolved(self):
        ents = _fcnca_entries() + [
            _e("CommonStockSharesOutstanding", _AXIS_B, 1_005_186)]
        self.assertIsNone(ov.resolve_class_shares(ents, EQ_END)["value"])


def _pt(end, val, form="10-Q"):
    return {"end": end, "val": val, "form": form, "filed": FILED}


def _ocfc_blob(class_shares=True):
    """OCFC's companyfacts at Q2-2026 as SEC serves it: 2026-06-30 equity,
    the undimensioned counts stop at the prior quarter, and the fresh dei
    cover is VOTING ONLY. Equity/intangibles chosen to reproduce the
    release: common equity 2,411,079,000; TBV 1,790,626,560."""
    blob = {"facts": {
        "us-gaap": {
            "StockholdersEquity": {"units": {"USD": [
                _pt(EQ_END, 2_411_079_000)]}},
            "Goodwill": {"units": {"USD": [_pt(EQ_END, 620_452_440)]}},
            "CommonStockSharesOutstanding": {"units": {"shares": [
                _pt(PRIOR, 57_600_069)]}},
            "CommonStockSharesIssued": {"units": {"shares": [
                _pt(PRIOR, 63_329_377)]}},
            "WeightedAverageNumberOfSharesOutstandingBasic": {
                "units": {"shares": [_pt(EQ_END, 63_630_000)]}},
        },
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            _pt(COVER, 96_645_219)]}}},
    }}
    if class_shares:
        rec = ov.resolve_class_shares(_ocfc_entries(), EQ_END)
        blob["_class_shares"] = {**rec, "accession": "000100470226000120",
                                 "form": "10-Q"}
    return blob


def _fundamentals(facts):
    with patch.object(sec_client, "fetch_company_facts", return_value=facts):
        return sec_client.get_latest_fundamentals(1)


def _provenance(facts):
    with patch.object(sec_client, "fetch_company_facts", return_value=facts):
        return sec_client.get_fundamentals_with_provenance(1)


class TestUndimensionedClassArbitratedByCover(unittest.TestCase):
    """OPHC 10-Q 0001493152-26-036825 (2026-10-06): the voting class is tagged
    UNDIMENSIONED (12,340,785) and the nonvoting class per member
    (11,458,351); the Aug-10 cover counts both classes (12,622,470 /
    11,458,351). Total 23,799,136 = the release's "fully diluted shares
    outstanding"; $134,380K ÷ it = $5.65, the printed TBVPS. Before, the
    voting-only count served ($10.89)."""
    _NV = "us-gaap:NonvotingCommonStockMember"
    _V = "us-gaap:CommonStockMember"

    def _ophc(self, undim=12_340_785, cover_v=12_622_470):
        return [
            _e("CommonStockSharesOutstanding", None, undim),
            _e("CommonStockSharesIssued", None, undim),
            _e("CommonStockSharesOutstanding", self._NV, 11_458_351),
            _e("CommonStockSharesIssued", self._NV, 11_458_351),
            _e("EntityCommonStockSharesOutstanding", self._V, cover_v, COVER),
            _e("EntityCommonStockSharesOutstanding", self._NV, 11_458_351, COVER),
        ]

    def test_hand_verified(self):
        self.assertEqual(12_340_785 + 11_458_351, 23_799_136)
        self.assertAlmostEqual(134_380e3 / 23_799_136, 5.65, places=2)

    def test_undimensioned_count_is_the_uncounted_cover_class(self):
        rec = ov.resolve_class_shares(self._ophc(), EQ_END)
        self.assertEqual(rec["status"], "resolved")
        self.assertEqual(rec["value"], 23_799_136)
        self.assertEqual(rec["classes"],
                         {self._V: 12_340_785, self._NV: 11_458_351})

    def test_undimensioned_total_is_left_alone(self):
        # A filer tagging the all-class TOTAL undimensioned beside a per-class
        # cover: nothing to do (None) — the chain's own count is right.
        self.assertIsNone(ov.resolve_class_shares(
            self._ophc(undim=24_080_821), EQ_END))

    def test_count_matching_neither_is_unresolved(self):
        rec = ov.resolve_class_shares(self._ophc(undim=18_000_000), EQ_END)
        self.assertEqual(rec["status"], "unresolved")
        self.assertIsNone(rec["value"])

    def test_single_class_cover_is_not_multiclass(self):
        ents = [_e("CommonStockSharesOutstanding", None, 12_340_785),
                _e("EntityCommonStockSharesOutstanding", self._V, 12_622_470, COVER)]
        self.assertIsNone(ov.resolve_class_shares(ents, EQ_END))

    def test_cover_lag_opens_the_gate(self):
        blob = _ocfc_blob(class_shares=False)
        blob["facts"]["us-gaap"]["CommonStockSharesOutstanding"]["units"][
            "shares"].append(_pt(EQ_END, 12_340_785))
        blob["facts"]["dei"] = {"EntityCommonStockSharesOutstanding": {"units": {
            "shares": [{"end": PRIOR, "val": 12_166_437, "filed": PRIOR}]}}}
        meta = {"accession": "000149315226036825", "doc": "form10-q.htm",
                "form": "10-Q", "date": FILED}
        with patch("data.sec_earnings_8k.latest_periodic_filing",
                   return_value={"form": "10-Q", "date": FILED,
                                 "report_date": EQ_END}),                 patch("data.sec_filing_scraper.latest_filing", return_value=meta),                 patch.object(ov, "class_share_entries",
                             return_value=self._ophc()):
            out = ov.overlay_class_shares(1288855, blob)
        self.assertEqual(out["_class_shares"]["value"], 23_799_136)
        # Cover filed WITH the latest filing: single-class, gate stays shut.
        blob["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"][
            "shares"][0]["filed"] = FILED
        with patch("data.sec_earnings_8k.latest_periodic_filing",
                   return_value={"form": "10-Q", "date": FILED,
                                 "report_date": EQ_END}),                 patch.object(ov, "class_share_entries",
                             side_effect=AssertionError("no fetch")):
            self.assertIs(ov.overlay_class_shares(1288855, blob), blob)


class TestShareChainUsesClassTotal(unittest.TestCase):
    def test_without_the_record_the_voting_only_cover_serves(self):
        """The failure being fixed: 2,411,079,000 / 96,645,219 = 24.948."""
        f = _fundamentals(_ocfc_blob(class_shares=False))
        self.assertEqual(f["shares_outstanding"], 96_645_219)
        self.assertAlmostEqual(f["book_value_per_share"], 24.948, places=3)

    def test_ocfc_per_share_ties_to_the_release(self):
        # 2,411,079,000 / 98,416,195 = 24.4988 → release $24.50
        # 1,790,626,560 / 98,416,195 = 18.1944 → release $18.19
        f = _fundamentals(_ocfc_blob())
        self.assertEqual(f["shares_outstanding"], 98_416_195)
        self.assertAlmostEqual(f["book_value_per_share"], 24.4988, places=4)
        self.assertAlmostEqual(f["tangible_book_value_per_share"], 18.1944,
                               places=4)
        # the voting-only cover is not comparable to the all-class total
        self.assertIsNone(f["shares_cover_divergence_pct"])
        self.assertFalse(f["shares_asof_incoherent"])

    def test_unresolved_multiclass_renders_na_not_a_fallback(self):
        """Neither the voting-only cover nor the weighted average may stand in
        for an unresolvable multi-class total (the FCNCA 3% miss)."""
        blob = _ocfc_blob(class_shares=False)
        blob["_class_shares"] = {"end": EQ_END, "value": None, "classes": {},
                                 "status": "unresolved", "reason": "test"}
        f = _fundamentals(blob)
        self.assertIsNone(f["shares_outstanding"])
        self.assertIsNone(f["book_value_per_share"])
        self.assertIsNone(f["tangible_book_value_per_share"])

    def test_record_for_another_date_is_ignored(self):
        blob = _ocfc_blob()
        blob["_class_shares"] = {**blob["_class_shares"], "end": PRIOR}
        self.assertEqual(_fundamentals(blob)["shares_outstanding"], 96_645_219)

    def test_provenance_parity(self):
        p = _provenance(_ocfc_blob())
        self.assertEqual(p["shares_outstanding"]["value"], 98_416_195)
        self.assertIn("NonvotingCommonEquivalentStockMember 1,812,000",
                      p["shares_outstanding"]["source"].notes)
        self.assertAlmostEqual(p["book_value_per_share"]["value"], 24.4988,
                               places=4)
        blob = _ocfc_blob(class_shares=False)
        blob["_class_shares"] = {"end": EQ_END, "value": None, "classes": {},
                                 "status": "unresolved", "reason": "test"}
        p = _provenance(blob)
        self.assertIsNone(p["shares_outstanding"]["value"])
        self.assertIsNone(p["tangible_book_value_per_share"]["value"])


class TestOverlayTrigger(unittest.TestCase):
    _META = {"accession": "000100470226000120", "doc": "ocfc-20260630.htm",
             "form": "10-Q", "date": FILED}

    def _run(self, blob, entries):
        with patch("data.sec_filing_scraper.latest_filing",
                   return_value=dict(self._META)), \
                patch.object(ov, "class_share_entries", return_value=entries):
            return ov.overlay_class_shares(1004702, blob)

    def test_attaches_record_when_no_undimensioned_count_at_date(self):
        blob = _ocfc_blob(class_shares=False)
        out = self._run(blob, _ocfc_entries())
        self.assertEqual(out["_class_shares"]["value"], 98_416_195)
        self.assertEqual(out["_class_shares"]["accession"], "000100470226000120")
        self.assertNotIn("_class_shares", blob, "cached blob never mutated")

    def test_undimensioned_count_at_date_skips_the_instance_fetch(self):
        blob = _ocfc_blob(class_shares=False)
        blob["facts"]["us-gaap"]["CommonStockSharesOutstanding"]["units"][
            "shares"].append(_pt(EQ_END, 98_416_195))

        def _boom(*a, **k):
            raise AssertionError("a filer tagging the total pays no fetch")
        # (The cover-lag check reads the 2h-cached submissions record the
        # per-share path loads anyway; stubbed: cover filed with the 10-Q.)
        with patch("data.sec_filing_scraper.latest_filing", _boom), \
                patch("data.sec_earnings_8k.latest_periodic_filing",
                      return_value={"form": "10-Q", "date": FILED,
                                    "report_date": EQ_END}), \
                patch.object(ov, "class_share_entries", _boom):
            self.assertIs(ov.overlay_class_shares(1004702, blob), blob)

    def test_single_class_filing_leaves_blob_unchanged(self):
        blob = _ocfc_blob(class_shares=False)
        ents = [_e("CommonStockSharesOutstanding", _VOTING, 96_604_195)]
        self.assertIs(self._run(blob, ents), blob)


if __name__ == "__main__":
    unittest.main()
