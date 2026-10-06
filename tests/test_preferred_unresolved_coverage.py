"""(2026-10-06) The coverage audit's "preferred outstanding, value unresolved
(n/a by rule)" class — 11 SEC filers with no P/TBV: FRME, LKFN, STT, NTRS,
BOH, WSBC, NEWT, BCBP, BYFC, OPHC, PNBK. Root causes, each hand-verified
against the 10-Q inline XBRL and the earnings release:

Preferred EVIDENCE false positives (data/sec_client._resolve_preferred_stock):
  OPHC  ProceedsFromIssuanceOfPreferredStockAndPreferenceStock = −$1,000
        (Q1-2026 YTD): a negative rounding artifact was truthy. No preferred:
        the release's "Total Stockholders' (GAAP) and Tangible Common Equity"
        $134,380K equals StockholdersEquity.
  LKFN  PaymentsOfDividendsPreferredStockAndPreferenceStock = $13,000 in
        EVERY window since 2023 (6M, 9M, FY all 13,000 — not a flow), against
        $773,285K equity; the 10-Q equity statement has no preferred column.
  Rule: evidence must be positive, and imply a carrying value of at least
  0.2% of equity (dividends annualized ÷ a 4% coupon floor; proceeds as
  is). FRME, the smallest real one: $938K/6M → $1.876M/yr ÷ 4% = $46.9M
  = 1.7% of $2,697M — stays present (its Series A is $25.125M).

Preferred tagged only PER SERIES (companyfacts drops dimensioned facts —
data/sec_facts_overlay.resolve_preferred_total reads the 10-Q instance):
  STT   PreferredStockValue on StatementClassOfStockAxis: Series G 493 +
        I 1,481 + J 842 + K 743 = $3,559M; equity statement PreferredStock-
        Member column $3,559M. (10-Q 0000093751-26-000397)
  NTRS  Series D 493.5 + E 391.4 = $884.9M = equity-statement column.
  BOH   Series A 180,000 + B 165,000 = $345,000K (both shapes).
  FRME  cumulative 125 + Series A 25,000 = $25,125K (deck: "Less: Preferred
        Stock (25,125)").
  WSBC  Series B $224,187K (= "Less: preferred shareholders' equity").
  NEWT  Series B $48,181K (= "Deduct: Preferred stock (GAAP)").
  BYFC  Series C $150,000K, class axis only — single shape, 150,000 shares
        ($1,000 each: plausible as a total).
  BCBP  AdditionalPaidInCapitalPreferredStock $25,243K undimensioned (the
        balance-sheet preferred line itself is "-"), 2,548 shares at
        $10,000 liquidation (= "Less: preferred stock 25,243").
  PNBK  90,832 PreferredStockSharesOutstanding, NO value anywhere in the
        Q2-2026 instance (the 2025 equity statement had a $5,099K preferred
        column; the 2026 one has none) and page-image releases → stays n/a.

Hermetic: real fact values in synthetic entries/blobs, no network.
Run: python -m unittest tests.test_preferred_unresolved_coverage
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
from data.sec_client import (  # noqa: E402
    _annualized, _preferred_evidence_material, _resolve_preferred_stock)

TODAY = date.today()
END = (TODAY - timedelta(days=50)).isoformat()          # balance-sheet date
START_6M = (date.fromisoformat(END) - timedelta(days=181)).isoformat()
START_3M = (date.fromisoformat(END) - timedelta(days=91)).isoformat()
FILED = TODAY.isoformat()

CLASS = "StatementClassOfStockAxis"
EQ = "StatementEquityComponentsAxis"
PFD = "PreferredStockMember"


def _pt(end, val, form="10-Q", start=None):
    e = {"end": end, "val": val, "form": form, "filed": FILED}
    if start:
        e["start"] = start
    return e


def _facts(us_gaap, extra=None):
    blob = {"facts": {"us-gaap": us_gaap}}
    if extra:
        blob.update(extra)
    return blob


def _e(concept, val, members=None, end=END):
    return {"concept": concept, "members": members or {}, "end": end,
            "val": float(val)}


# ── Evidence materiality ─────────────────────────────────────────────────────
class TestEvidenceMateriality(unittest.TestCase):
    def test_ophc_negative_proceeds_is_no_evidence(self):
        facts = _facts({
            "StockholdersEquity": {"units": {"USD": [_pt(END, 134_380_000)]}},
            "ProceedsFromIssuanceOfPreferredStockAndPreferenceStock": {
                "units": {"USD": [_pt(END, -1_000, start=START_3M)]}},
        })
        self.assertEqual(_resolve_preferred_stock(facts), (0.0, False))

    def test_lkfn_constant_13k_dividend_is_immaterial(self):
        facts = _facts({
            "StockholdersEquity": {"units": {"USD": [_pt(END, 773_285_000)]}},
            "PaymentsOfDividendsPreferredStockAndPreferenceStock": {
                "units": {"USD": [_pt(END, 13_000, start=START_6M)]}},
        })
        # 13,000 × 365/181 ÷ 0.04 = $655K = 0.085% of equity < 0.2%.
        self.assertEqual(_resolve_preferred_stock(facts), (0.0, False))

    def test_frme_small_real_preferred_stays_present(self):
        facts = _facts({
            "StockholdersEquity": {"units": {"USD": [_pt(END, 2_697_177_000)]}},
            "PaymentsOfDividendsPreferredStockAndPreferenceStock": {
                "units": {"USD": [_pt(END, 938_000, start=START_6M)]}},
        })
        # 938,000 × 365/181 ÷ 0.04 = $47.3M = 1.75% of equity ≥ 0.2%.
        self.assertEqual(_resolve_preferred_stock(facts), (None, True))

    def test_proceeds_compared_directly(self):
        eq = {"StockholdersEquity": {"units": {"USD": [_pt(END, 100_000_000)]}}}
        small = _facts({**eq, "ProceedsFromIssuanceOfPreferredStockAndPreferenceStock": {
            "units": {"USD": [_pt(END, 150_000, start=START_6M)]}}})
        real = _facts({**eq, "ProceedsFromIssuanceOfPreferredStockAndPreferenceStock": {
            "units": {"USD": [_pt(END, 1_932_000, start=START_6M)]}}})
        self.assertEqual(_resolve_preferred_stock(small), (0.0, False))   # 0.15%
        self.assertEqual(_resolve_preferred_stock(real), (None, True))    # 1.9%

    def test_no_equity_total_any_positive_evidence_counts(self):
        # The 2026-08-19 BAFN/MBIN fixtures carry no equity: unchanged.
        facts = _facts({"PreferredStockDividendsIncomeStatementImpact": {
            "units": {"USD": [_pt(END, 13_000, start=START_6M)]}}})
        self.assertEqual(_resolve_preferred_stock(facts), (None, True))

    def test_annualized_uses_the_facts_own_window(self):
        self.assertAlmostEqual(
            _annualized({"val": 13_000, "start": START_6M, "end": END}),
            13_000 * 365 / 181, places=3)
        # No start date → one quarter (×4): the side that keeps it present.
        self.assertEqual(_annualized({"val": 10.0, "end": END}), 40.0)
        self.assertFalse(_preferred_evidence_material(
            {"val": 0, "start": START_6M, "end": END},
            "DividendsPreferredStock", 1e9))


# ── The 10-Q instance's preferred total ──────────────────────────────────────
def _stt():
    return [
        _e("PreferredStockValue", 493e6, {CLASS: "SeriesGPreferredStockMember"}),
        _e("PreferredStockValue", 1481e6, {CLASS: "SeriesIPreferredStockMember"}),
        _e("PreferredStockValue", 842e6, {CLASS: "SeriesJPreferredStockMember"}),
        _e("PreferredStockValue", 743e6, {CLASS: "SeriesKPreferredStockMember"}),
        _e("PreferredStockSharesOutstanding", 5000, {CLASS: "SeriesGPreferredStockMember"}),
        _e("PreferredStockSharesOutstanding", 15000, {CLASS: "SeriesIPreferredStockMember"}),
        _e("PreferredStockSharesOutstanding", 8500, {CLASS: "SeriesJPreferredStockMember"}),
        _e("PreferredStockSharesOutstanding", 7500, {CLASS: "SeriesKPreferredStockMember"}),
        _e("StockholdersEquity", 3559e6, {EQ: PFD}),
        _e("StockholdersEquity", 504e6, {EQ: "CommonStockMember"}),
        _e("StockholdersEquity", 28268e6),
        # the regulatory-capital table repeats the total on another axis
        _e("PreferredStockValue", 3559e6, {
            "RegulatoryCapitalrequirementsforBankingOrganizationsbyImplementationApproachesAxis":
            "BaselIIIAdvancedApproachMember"}),
    ]


def _ntrs():
    return [
        _e("PreferredStockValue", 493.5e6, {CLASS: "SeriesDPreferredStockMember"}),
        _e("PreferredStockValue", 391.4e6, {CLASS: "SeriesEPreferredStockMember"}),
        _e("StockholdersEquity", 884.9e6, {EQ: PFD}),
        _e("StockholdersEquity", 493.5e6, {EQ: PFD, CLASS: "SeriesDPreferredStockMember"}),
        _e("StockholdersEquity", 391.4e6, {EQ: PFD, CLASS: "SeriesEPreferredStockMember"}),
        # depositary-share members carry counts only, never values
        _e("PreferredStockSharesOutstanding", 500_000,
           {CLASS: "SeriesDPreferredStockDepositarySharesMember"}),
        _e("PreferredStockSharesOutstanding", 16_000_000,
           {CLASS: "SeriesEPreferredStockDepositarySharesMember"}),
        _e("PreferredStockSharesOutstanding", 5000, {CLASS: "SeriesDPreferredStockMember"}),
        _e("PreferredStockSharesOutstanding", 16000, {CLASS: "SeriesEPreferredStockMember"}),
        _e("PreferredStockLiquidationPreferenceValue", 100_000,
           {CLASS: "SeriesDPreferredStockMember"}),
        _e("StockholdersEquity", 13_401.6e6),
    ]


def _boh():
    return [
        _e("PreferredStockValue", 180e6, {CLASS: "SeriesAPreferredStockMember"}),
        _e("PreferredStockValue", 165e6, {CLASS: "SeriesBPreferredStockMember"}),
        _e("PreferredStockSharesOutstanding", 180_000, {CLASS: "SeriesAPreferredStockMember"}),
        _e("PreferredStockSharesOutstanding", 165_000, {CLASS: "SeriesBPreferredStockMember"}),
        _e("StockholdersEquity", 180e6, {EQ: PFD, CLASS: "SeriesAPreferredStockMember"}),
        _e("StockholdersEquity", 165e6, {EQ: PFD, CLASS: "SeriesBPreferredStockMember"}),
        _e("StockholdersEquity", 1_875_467e3),
    ]


def _frme():
    return [
        _e("PreferredStockValue", 125e3, {CLASS: "CumulativePreferredStockMember"}),
        _e("PreferredStockValue", 25_000e3, {CLASS: "SeriesAPreferredStockMember"}),
        _e("PreferredStockSharesOutstanding", 125, {CLASS: "CumulativePreferredStockMember"}),
        _e("PreferredStockSharesOutstanding", 10_000, {CLASS: "SeriesAPreferredStockMember"}),
        _e("StockholdersEquity", 125e3, {EQ: PFD, CLASS: "CumulativePreferredStockMember"}),
        _e("StockholdersEquity", 25_000e3, {EQ: PFD, CLASS: "NoncumulativePreferredStockMember"}),
        _e("StockholdersEquity", 2_697_177e3),
    ]


def _byfc():
    return [
        _e("PreferredStockValue", 150e6, {CLASS: "SeriesCPreferredStockMember"}),
        _e("PreferredStockSharesOutstanding", 150_000, {CLASS: "SeriesCPreferredStockMember"}),
        _e("PreferredStockLiquidationPreference", 1000, {CLASS: "SeriesCPreferredStockMember"}),
        _e("StockholdersEquity", 262_304e3),
    ]


def _bcbp():
    return [
        _e("AdditionalPaidInCapitalPreferredStock", 25_243e3),
        _e("PreferredStockSharesOutstanding", 2548),
        _e("PreferredStockSharesIssued", 2548),
        _e("StockholdersEquity", 291_919e3),
        _e("StockholdersEquity", 229_694e3, {EQ: "AdditionalPaidInCapitalMember"}),
    ]


def _pnbk():
    return [
        _e("PreferredStockSharesOutstanding", 90_832),
        _e("PreferredStockSharesIssued", 90_832),
        _e("StockholdersEquity", 88_935e3),
        _e("StockholdersEquity", 206_557e3, {EQ: "AdditionalPaidInCapitalMember"}),
    ]


class TestResolvePreferredTotal(unittest.TestCase):
    def _v(self, entries):
        rec = ov.resolve_preferred_total(entries, END)
        return rec and rec["value"]

    def test_hand_verified_sums(self):
        self.assertEqual(493 + 1481 + 842 + 743, 3559)
        self.assertAlmostEqual(493.5 + 391.4, 884.9)
        self.assertEqual(180_000 + 165_000, 345_000)
        self.assertEqual(125 + 25_000, 25_125)
        self.assertAlmostEqual(25_243e3 / 2548, 9907.0, places=0)

    def test_stt_column_and_series_agree(self):
        rec = ov.resolve_preferred_total(_stt(), END)
        self.assertEqual(rec["value"], 3559e6)
        self.assertIn("equity statement preferred column", rec["basis"])
        self.assertIn("PreferredStockValue summed over series", rec["basis"])

    def test_stt_other_axis_member_is_not_a_series(self):
        # Only the Basel-axis repeat: no class member, no column → None.
        ents = [e for e in _stt() if "RegulatoryCapital" in str(e["members"])
                or not e["members"]]
        self.assertIsNone(ov.resolve_preferred_total(ents, END))

    def test_ntrs_three_shapes_agree(self):
        self.assertAlmostEqual(self._v(_ntrs()), 884.9e6)

    def test_boh_per_series_column_and_values(self):
        self.assertEqual(self._v(_boh()), 345e6)

    def test_frme_differently_named_series_still_sum(self):
        self.assertEqual(self._v(_frme()), 25_125e3)

    def test_byfc_single_shape_plausible_over_count(self):
        self.assertEqual(self._v(_byfc()), 150e6)

    def test_bcbp_apic_preferred(self):
        rec = ov.resolve_preferred_total(_bcbp(), END)
        self.assertEqual(rec["value"], 25_243e3)
        self.assertEqual(rec["basis"], "APIC-preferred + par")

    def test_pnbk_count_without_any_value_is_none(self):
        self.assertIsNone(ov.resolve_preferred_total(_pnbk(), END))

    def test_disagreeing_shapes_refuse(self):
        ents = _stt()
        ents.append(_e("StockholdersEquity", 3000e6, {EQ: PFD}))
        ents = [e for e in ents if not (e["members"] == {EQ: PFD} and e["val"] == 3559e6)]
        self.assertIsNone(ov.resolve_preferred_total(ents, END))

    def test_single_shape_par_only_refuses(self):
        # $0.01 par × 150,000 shares = $1,500 tagged per series: not a total.
        ents = [_e("PreferredStockValue", 1500, {CLASS: "SeriesCPreferredStockMember"}),
                _e("PreferredStockSharesOutstanding", 150_000,
                   {CLASS: "SeriesCPreferredStockMember"}),
                _e("StockholdersEquity", 262_304e3)]
        self.assertIsNone(ov.resolve_preferred_total(ents, END))

    def test_single_shape_without_a_count_refuses(self):
        ents = [_e("PreferredStockValue", 150e6, {CLASS: "SeriesCPreferredStockMember"}),
                _e("StockholdersEquity", 262_304e3)]
        self.assertIsNone(ov.resolve_preferred_total(ents, END))

    def test_one_series_with_two_values_refuses(self):
        ents = _byfc() + [_e("PreferredStockValue", 140e6,
                             {CLASS: "SeriesCPreferredStockMember"})]
        self.assertIsNone(ov.resolve_preferred_total(ents, END))

    def test_value_not_below_equity_refuses(self):
        ents = [_e("StockholdersEquity", 300e6, {EQ: PFD}),
                _e("PreferredStockValue", 300e6, {CLASS: "SeriesAPreferredStockMember"}),
                _e("StockholdersEquity", 262_304e3)]
        self.assertIsNone(ov.resolve_preferred_total(ents, END))

    def test_other_date_reads_nothing(self):
        self.assertIsNone(ov.resolve_preferred_total(_stt(), "2020-12-31"))

    def test_common_member_under_preferred_concept_ignored(self):
        ents = [_e("PreferredStockValue", 6_544e3, {CLASS: "CommonStockMember"}),
                _e("PreferredStockSharesOutstanding", 5_082_676, {CLASS: "CommonStockMember"}),
                _e("StockholdersEquity", 60e6)]
        self.assertIsNone(ov.resolve_preferred_total(ents, END))


# ── The resolver consumes the record ─────────────────────────────────────────
def _frme_blob(rec=True, end=END):
    us_gaap = {
        "StockholdersEquity": {"units": {"USD": [_pt(END, 2_697_177_000)]}},
        "Assets": {"units": {"USD": [_pt(END, 20_000_000_000)]}},
        "PreferredStockValue": {"units": {"USD": [_pt("2022-03-31", 125_000)]}},
        "PaymentsOfDividendsPreferredStockAndPreferenceStock": {
            "units": {"USD": [_pt(END, 938_000, start=START_6M)]}},
    }
    extra = {"_preferred_total": {"end": end, "value": 25_125_000.0,
                                  "basis": "test", "accession": "x",
                                  "form": "10-Q"}} if rec else None
    return _facts(us_gaap, extra)


class TestResolverUsesFilingTotal(unittest.TestCase):
    def test_record_at_the_balance_sheet_date_resolves(self):
        self.assertEqual(_resolve_preferred_stock(_frme_blob()),
                         (25_125_000.0, True))

    def test_without_the_record_stays_unresolved(self):
        self.assertEqual(_resolve_preferred_stock(_frme_blob(rec=False)),
                         (None, True))

    def test_record_for_another_date_is_ignored(self):
        self.assertEqual(_resolve_preferred_stock(_frme_blob(end="2025-12-31")),
                         (None, True))

    def test_as_of_at_the_date_resolves_earlier_never(self):
        # Financial Highlights' current column asks as_of=END: same date,
        # same answer. An earlier column: the record is not for it (and the
        # only evidence here ends at END, so that date reads none).
        self.assertEqual(_resolve_preferred_stock(_frme_blob(), as_of=END),
                         (25_125_000.0, True))
        earlier = (date.fromisoformat(END) - timedelta(days=91)).isoformat()
        self.assertEqual(_resolve_preferred_stock(_frme_blob(), as_of=earlier),
                         (0.0, False))

    def test_ladder_value_still_wins(self):
        blob = _frme_blob()
        blob["facts"]["us-gaap"]["PreferredStockValue"]["units"]["USD"].append(
            _pt(END, 25_125_000))
        self.assertEqual(_resolve_preferred_stock(blob), (25_125_000, True))

    def test_fresh_zero_count_supersedes(self):
        blob = _frme_blob()
        blob["facts"]["us-gaap"]["PreferredStockSharesOutstanding"] = {
            "units": {"shares": [_pt(END, 0)]}}
        # zero_end == END: dividend evidence ending AT it no longer counts
        self.assertEqual(_resolve_preferred_stock(blob), (0.0, False))


class TestOverlayTrigger(unittest.TestCase):
    _META = {"accession": "000071253426000059", "doc": "frme-20260630.htm",
             "form": "10-Q", "date": FILED, "cik": 712534}

    def _run(self, blob, entries):
        with patch("data.sec_filing_scraper.latest_filing",
                   return_value=dict(self._META)), \
                patch.object(ov, "preferred_entries", return_value=entries):
            return ov.overlay_preferred_total(712534, blob)

    def test_unresolved_present_attaches_record(self):
        blob = _frme_blob(rec=False)
        out = self._run(blob, _frme())
        self.assertEqual(out["_preferred_total"]["value"], 25_125e3)
        self.assertEqual(out["_preferred_total"]["accession"],
                         "000071253426000059")
        self.assertNotIn("_preferred_total", blob, "cached blob never mutated")
        self.assertEqual(_resolve_preferred_stock(out), (25_125e3, True))

    def test_resolved_or_absent_preferred_pays_no_fetch(self):
        def _boom(*a, **k):
            raise AssertionError("no instance fetch")
        resolved = _frme_blob(rec=False)
        resolved["facts"]["us-gaap"]["PreferredStockValue"]["units"]["USD"].append(
            _pt(END, 25_125_000))
        none = _facts({"StockholdersEquity": {"units": {"USD": [_pt(END, 1e9)]}}})
        with patch("data.sec_filing_scraper.latest_filing", _boom), \
                patch.object(ov, "preferred_entries", _boom):
            self.assertIs(ov.overlay_preferred_total(712534, resolved), resolved)
            self.assertIs(ov.overlay_preferred_total(712534, none), none)

    def test_instance_without_a_total_leaves_blob_unchanged(self):
        blob = _frme_blob(rec=False)
        self.assertIs(self._run(blob, _pnbk()), blob)


class TestFundamentalsEndToEnd(unittest.TestCase):
    def test_boh_tangible_book_ties_the_release(self):
        """BOH 2Q26: $1,875,467K − $345,000K − $31,517K goodwill =
        $1,498,950K TCE (the release's figure) ÷ 39,439,677 = $38.01."""
        us_gaap = {
            "StockholdersEquity": {"units": {"USD": [_pt(END, 1_875_467_000)]}},
            "Assets": {"units": {"USD": [_pt(END, 23_842_940_000)]}},
            "Goodwill": {"units": {"USD": [_pt(END, 31_517_000)]}},
            "CommonStockSharesOutstanding": {"units": {"shares": [_pt(END, 39_439_677)]}},
            "PaymentsOfDividendsPreferredStockAndPreferenceStock": {
                "units": {"USD": [_pt(END, 10_538_000, start=START_6M)]}},
        }
        blob = _facts(us_gaap, {"_preferred_total": {
            "end": END, "value": 345_000_000.0, "basis": "t",
            "accession": "x", "form": "10-Q"}})
        with patch.object(sec_client, "fetch_company_facts", return_value=blob):
            r = sec_client.get_latest_fundamentals(46195)
        self.assertEqual(r["preferred_stock"], 345_000_000.0)
        self.assertAlmostEqual(r["tangible_book_value_per_share"], 38.01, places=2)
        self.assertAlmostEqual(r["book_value_per_share"], 38.81, places=2)


if __name__ == "__main__":
    unittest.main()
