"""(2026-08-19) Share/equity COHERENCE guard — the FSUN shape.

FSUN's 2026 10-Qs tag NO share-count facts at all, so post-offering
2026-06-30 equity ($1,837M — the offering added ~19M shares, true count
~46.8M) was divided by a count frozen at 2025-12-31 (27.9M): TBVPS rendered
$58.97 against the release's $35.16 for three weeks. PR #41's freshness
guard prefers the freshest source, but when a filer stops tagging counts
entirely, "freshest" is still quarters old.

Invariant pinned here: a normal filer's newest share evidence is dated AT
the equity period end (balance-sheet tag) or AFTER it (cover page dated
"latest practicable date" ≈ filing date). Share evidence ending more than
_SHARE_COHERENCE_GRACE_DAYS before the equity date means NO source can be
coherent → shares_outstanding and every per-share derivative go None
(cardinal rule), display and provenance paths in parity. The grace must
stay well under a quarter: FSUN's gap was 117 days, so a 120-day threshold
would have missed it.

Downstream (pinned in tests.test_tbvps_conflict_signal +
tests.test_otc_valuation_wiring): reconstruction=None routes
analysis/valuation._resolve_tbvps to the bank's own released figure
(reported_8k / company_release), so FSUN serves its reported $35.16.

Run: python -m unittest tests.test_share_equity_coherence
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

TODAY = date.today()
EQ_END = (TODAY - timedelta(days=50)).isoformat()          # fresh quarter end
STALE = (TODAY - timedelta(days=50 + 182)).isoformat()     # two quarters back
FSUN_GAP_117 = (TODAY - timedelta(days=50 + 117)).isoformat()  # dei 10-K cover
WITHIN_GRACE = (TODAY - timedelta(days=50 + 20)).isoformat()
FILED = TODAY.isoformat()


def _pt(end, val, form="10-Q"):
    return {"end": end, "val": val, "form": form, "filed": FILED}


def _facts(us_gaap, dei=None):
    return {"facts": {"us-gaap": us_gaap, "dei": dei or {}}}


def _fundamentals(facts):
    with patch.object(sec_client, "fetch_company_facts", return_value=facts):
        return sec_client.get_latest_fundamentals(1)


def _provenance(facts):
    with patch.object(sec_client, "fetch_company_facts", return_value=facts):
        return sec_client.get_fundamentals_with_provenance(1)


def _fsun_like():
    """Fresh equity; every POINT-IN-TIME share tag (us-gaap + dei cover)
    frozen quarters back — FSUN's actual tag layout at Q2-2026, including
    the fresh Q2 WEIGHTED-AVERAGE the income statement still tags. That
    average defeated the guard's first implementation (it looked like fresh
    share evidence); it is NOT evidence a period-end count exists, and the
    stale point-in-time count must still be nulled."""
    return _facts(
        {
            "StockholdersEquity": {"units": {"USD": [
                _pt(STALE, 1_175_507_000), _pt(EQ_END, 1_837_392_000)]}},
            "CommonStockSharesOutstanding": {"units": {"shares": [
                _pt(STALE, 27_887_337, "10-K")]}},
            "CommonStockSharesIssued": {"units": {"shares": [
                _pt(STALE, 27_887_337, "10-K")]}},
            "WeightedAverageNumberOfSharesOutstandingBasic": {
                "units": {"shares": [_pt(EQ_END, 40_100_000)]}},
            "Goodwill": {"units": {"USD": [_pt(EQ_END, 102_536_000)]}},
        },
        dei={"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            _pt(FSUN_GAP_117, 27_923_333, "10-K")]}}},
    )


class TestIncoherentSharesGoNone(unittest.TestCase):
    def test_fsun_shape_nulls_shares_and_per_share_metrics(self):
        f = _fundamentals(_fsun_like())
        self.assertIsNone(
            f.get("shares_outstanding"),
            "a count quarters older than the equity date divided fresh "
            "equity — the exact FSUN plausible-wrong TBVPS")
        self.assertIsNone(f.get("book_value_per_share"))
        self.assertIsNone(f.get("tangible_book_value_per_share"))
        self.assertTrue(f.get("shares_asof_incoherent"))
        # equity itself is genuine and stays served
        self.assertEqual(f.get("book_value_total"), 1_837_392_000)

    def test_grace_days_must_stay_under_fsun_gap(self):
        """117 days was a REAL miss distance (a 120-day guard misses FSUN);
        pin the constant well under a quarter."""
        self.assertLess(sec_client._SHARE_COHERENCE_GRACE_DAYS, 90)

    def test_provenance_parity_and_reason(self):
        p = _provenance(_fsun_like())
        self.assertIsNone(p["shares_outstanding"]["value"])
        self.assertIn("INCOHERENT", p["shares_outstanding"]["source"].notes)
        self.assertIsNone(p["tangible_book_value_per_share"]["value"])


class TestCoherentSharesSurvive(unittest.TestCase):
    def test_same_date_balance_sheet_count_untouched(self):
        f = _fundamentals(_facts({
            "StockholdersEquity": {"units": {"USD": [_pt(EQ_END, 452_268_000)]}},
            "CommonStockSharesOutstanding": {"units": {"shares": [
                _pt(EQ_END, 9_468_467)]}},
        }))
        self.assertEqual(f.get("shares_outstanding"), 9_468_467)
        self.assertFalse(f.get("shares_asof_incoherent"))
        self.assertAlmostEqual(f["book_value_per_share"], 47.77, places=2)

    def test_fresh_cover_count_after_quarter_end_untouched(self):
        cover_end = (TODAY - timedelta(days=10)).isoformat()
        f = _fundamentals(_facts(
            {"StockholdersEquity": {"units": {"USD": [
                _pt(EQ_END, 374_598_000_000)]}},
             "CommonStockSharesOutstanding": {"units": {"shares": [
                 _pt(STALE, 2_696_200_000, "10-K")]}}},
            dei={"EntityCommonStockSharesOutstanding": {"units": {"shares": [
                _pt(cover_end, 2_658_186_195)]}}},
        ))
        # PR #41's freshness guard picks the fresher dei; coherence keeps it.
        self.assertEqual(f.get("shares_outstanding"), 2_658_186_195)
        self.assertFalse(f.get("shares_asof_incoherent"))

    def test_gap_inside_grace_untouched(self):
        f = _fundamentals(_facts({
            "StockholdersEquity": {"units": {"USD": [_pt(EQ_END, 1_000_000)]}},
            "CommonStockSharesOutstanding": {"units": {"shares": [
                _pt(WITHIN_GRACE, 100_000)]}},
        }))
        self.assertEqual(f.get("shares_outstanding"), 100_000)
        self.assertFalse(f.get("shares_asof_incoherent"))

    def test_served_fresh_weighted_average_survives_ancient_point_tags(self):
        """A bank whose ONLY usable count is the weighted-average fallback
        (point-in-time tags years dead) keeps it — the average-basis
        compromise the chain already accepts must not be nulled for the
        staleness of tags it didn't come from."""
        ancient = "2014-10-16"
        f = _fundamentals(_facts({
            "StockholdersEquity": {"units": {"USD": [
                _pt(EQ_END, 50_000_000)]}},
            "CommonStockSharesOutstanding": {"units": {"shares": [
                _pt(ancient, 1_000_000, "10-K")]}},
            "WeightedAverageNumberOfSharesOutstandingBasic": {
                "units": {"shares": [_pt(EQ_END, 6_100_000)]}},
        }))
        self.assertEqual(f.get("shares_outstanding"), 6_100_000)
        self.assertFalse(f.get("shares_asof_incoherent"))

    def test_no_share_tags_at_all_unchanged(self):
        """Multi-class filers (FCNCA/RBCAA/CBNA) tag counts only per-class
        (dimensioned → absent from companyfacts): already None, no flag."""
        f = _fundamentals(_facts({
            "StockholdersEquity": {"units": {"USD": [
                _pt(EQ_END, 1_000_000)]}},
        }))
        self.assertIsNone(f.get("shares_outstanding"))
        self.assertFalse(f.get("shares_asof_incoherent"))


if __name__ == "__main__":
    unittest.main()
