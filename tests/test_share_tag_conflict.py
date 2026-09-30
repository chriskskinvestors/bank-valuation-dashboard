"""(2026-09-30) Same-date share-tag conflict — the FGBI / BFST shape.

FGBI's Q2-2026 10-Q (0001408534-26-000072) tags its balance-sheet sentence
"16,539,094 and 15,793,433 shares issued and outstanding" (the 6/30/26 and
12/31/25 columns) with nested ix tags: CommonStockSharesIssued = 16,539,094
at BOTH dates and CommonStockSharesOutstanding = 15,793,433 at BOTH dates.
The primary count at 6/30/26 was therefore last year's: BVPS
(227,350K − 33,058K preferred) / 15,793,433 = 12.30 vs the release's 11.75
(= / 16,539,094). BFST did the same: outstanding 29,510,668 vs its release's
"End of Period Common Shares Outstanding 32,535,659" — reconstruction ~10%
high, inside the ±15% release gate, so no conflict ever fired.

A same-date outstanding ≠ issued − treasury is usually benign (13 of 15
universe cases on 2026-09-30: treasury simply isn't tagged — ASB, GS, WFC…),
so the filing's dei cover count arbitrates. Pinned here:
  1. cover backs issued − treasury → it replaces the mis-tagged primary
     (FGBI, BFST), display and provenance in parity;
  2. cover backs the primary → the primary stands (ASB: untagged treasury);
  3. cover backs neither → n/a, shares_tag_conflict + a validation warning,
     and no fallback refills the count;
  4. no cover witness → the direct tag stands.

Run: python -m unittest tests.test_share_tag_conflict
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

from data import sec_client, validation  # noqa: E402

TODAY = date.today()
EQ_END = (TODAY - timedelta(days=50)).isoformat()
PRIOR = (TODAY - timedelta(days=50 + 182)).isoformat()
COVER = (TODAY - timedelta(days=4)).isoformat()      # 46 days after EQ_END
FILED = (TODAY - timedelta(days=3)).isoformat()


def _pt(end, val, form="10-Q"):
    return {"end": end, "val": val, "form": form, "filed": FILED}


def _blob(outstanding, issued, cover=None, treasury=None,
          equity=227_350_000, preferred=33_058_000, intangibles=2_218_000):
    """FGBI's Q2-2026 companyfacts shape: both share tags carry the same
    value at BOTH dates (the nested-ix mis-tag)."""
    ug = {
        "StockholdersEquity": {"units": {"USD": [_pt(EQ_END, equity)]}},
        "PreferredStockValue": {"units": {"USD": [_pt(EQ_END, preferred)]}},
        "IntangibleAssetsNetExcludingGoodwill": {"units": {"USD": [
            _pt(EQ_END, intangibles)]}},
        "CommonStockSharesOutstanding": {"units": {"shares": [
            _pt(PRIOR, outstanding), _pt(EQ_END, outstanding)]}},
        "CommonStockSharesIssued": {"units": {"shares": [
            _pt(PRIOR, issued), _pt(EQ_END, issued)]}},
        "WeightedAverageNumberOfSharesOutstandingBasic": {"units": {"shares": [
            _pt(EQ_END, 16_062_514)]}},
    }
    if treasury is not None:
        ug["TreasuryStockCommonShares"] = {"units": {"shares": [
            _pt(EQ_END, treasury)]}}
    dei = {}
    if cover is not None:
        dei = {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            _pt(cover[1], cover[0])]}}}
    return {"facts": {"us-gaap": ug, "dei": dei}}


def _fundamentals(facts):
    with patch.object(sec_client, "fetch_company_facts", return_value=facts):
        return sec_client.get_latest_fundamentals(1)


def _provenance(facts):
    with patch.object(sec_client, "fetch_company_facts", return_value=facts):
        return sec_client.get_fundamentals_with_provenance(1)


FGBI = dict(outstanding=15_793_433, issued=16_539_094,
            cover=(16_539_094, COVER))


class TestCoverBacksIssuedMinusTreasury(unittest.TestCase):
    def test_fgbi_mis_tagged_primary_is_replaced(self):
        # (227,350,000 − 33,058,000) / 16,539,094 = 11.7474 → release $11.75
        # (227,350,000 − 33,058,000 − 2,218,000) / 16,539,094 = 11.6133
        f = _fundamentals(_blob(**FGBI))
        self.assertEqual(f["shares_outstanding"], 16_539_094)
        self.assertAlmostEqual(f["book_value_per_share"], 11.7474, places=4)
        self.assertAlmostEqual(f["tangible_book_value_per_share"], 11.6133,
                               places=4)
        self.assertFalse(f["shares_tag_conflict"])

    def test_without_the_guard_the_stale_count_served(self):
        """The failure being fixed: 194,292,000 / 15,793,433 = 12.302."""
        with patch.object(sec_client, "_reconcile_same_date_shares",
                          lambda facts, eq, sh: (sh, "")):
            f = _fundamentals(_blob(**FGBI))
        self.assertAlmostEqual(f["book_value_per_share"], 12.302, places=3)

    def test_bfst_shape(self):
        # BFST: outstanding 29,510,668; issued 32,535,659; cover 32,549,883
        f = _fundamentals(_blob(29_510_668, 32_535_659,
                                cover=(32_549_883, COVER)))
        self.assertEqual(f["shares_outstanding"], 32_535_659)

    def test_provenance_parity(self):
        p = _provenance(_blob(**FGBI))
        self.assertEqual(p["shares_outstanding"]["value"], 16_539_094)
        self.assertEqual(p["shares_outstanding"]["source"].concept,
                         "CommonStockSharesIssued − TreasuryStockCommonShares")
        self.assertAlmostEqual(p["book_value_per_share"]["value"], 11.7474,
                               places=4)


class TestCoverBacksThePrimary(unittest.TestCase):
    def test_untagged_treasury_keeps_the_primary(self):
        """ASB: outstanding 188,718,072, issued 211,991,791, no treasury
        tag, cover 188,849,659 — benign; the primary stands."""
        f = _fundamentals(_blob(188_718_072, 211_991_791,
                                cover=(188_849_659, COVER)))
        self.assertEqual(f["shares_outstanding"], 188_718_072)

    def test_tags_that_agree_are_untouched(self):
        f = _fundamentals(_blob(16_000_000, 16_500_000, treasury=500_000,
                                cover=(12_000_000, COVER)))
        self.assertEqual(f["shares_outstanding"], 16_000_000)


class TestNoArbiter(unittest.TestCase):
    def test_cover_backing_neither_is_na_and_flagged(self):
        blob = _blob(15_793_433, 16_539_094, cover=(17_500_000, COVER))
        f = _fundamentals(blob)
        self.assertIsNone(f["shares_outstanding"],
                          "no dei / weighted-average fallback may refill it")
        self.assertIsNone(f["book_value_per_share"])
        self.assertTrue(f["shares_tag_conflict"])
        p = _provenance(blob)
        self.assertIsNone(p["shares_outstanding"]["value"])
        self.assertIn("CONFLICT", p["shares_outstanding"]["source"].notes)
        findings = validation.check_coherence_flags(None, f)
        self.assertTrue(any(x.field == "shares_outstanding"
                            and "cover-page count backs neither" in x.message
                            for x in findings))

    def test_no_cover_witness_keeps_the_primary(self):
        f = _fundamentals(_blob(15_793_433, 16_539_094))
        self.assertEqual(f["shares_outstanding"], 15_793_433)

    def test_stale_cover_is_no_witness(self):
        old = (TODAY - timedelta(days=50 + 200)).isoformat()
        f = _fundamentals(_blob(15_793_433, 16_539_094,
                                cover=(16_539_094, old)))
        self.assertEqual(f["shares_outstanding"], 15_793_433)


if __name__ == "__main__":
    unittest.main()
