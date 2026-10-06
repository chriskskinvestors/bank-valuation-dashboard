"""(2026-10-06) MSRs bundled inside the intangibles rollup — the Citi shape.

TCE convention keeps mortgage servicing rights IN tangible equity, so the
intangible resolvers net a same-date MSR out of the
IntangibleAssetsNetExcludingGoodwill rollup — but only an MSR companyfacts
carries. Citi (10-Q 0000831001-26-000045, 2026-06-30) tags its rollup
$5,004M ("Intangible assets (including MSRs of $788)") and its MSRs only as
c:MortgageServicingRightsMSR and fair-value-axis members, so the full rollup
was deducted: TBVPS 100.42 vs the release's 100.89; ROATCE 9.77% vs 9.72%.

data/sec_facts_overlay.overlay_msr reads the MSR from the filing's instance
and nets it ONLY when the filing also tags the ex-MSR remainder itself
(c:IntangibleAssetsExcludingMortgageServicingRights = 4,216 = 5,004 − 788).

Citi hand values ($M): common equity 212,015 − 19,550 preferred = 192,465;
deduction 19,012 goodwill + 4,216 = 23,228; TCE 169,237;
TBVPS 169,237,000,000 / 1,677,436,783 = 100.8902 (release $100.89).

Run: python -m unittest tests.test_msr_in_rollup
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

M = 1e6
TODAY = date.today()
EQ_END = (TODAY - timedelta(days=50)).isoformat()
FILED = (TODAY - timedelta(days=10)).isoformat()


def _e(concept, val, end=EQ_END, recurring=False):
    return {"concept": concept, "recurring": recurring, "end": end, "val": val}


def _citi_entries():
    """Citi's 6/30/26 instance facts as msr_entries keeps them."""
    return [
        _e("c:MortgageServicingRightsMSR", 788 * M),
        _e("c:MortgageServicingRightsMSRGrossCarryingAmount", 788 * M),
        _e("us-gaap:ServicingAssetAtFairValueAmount", 788 * M, recurring=True),
        _e("us-gaap:IntangibleAssetsNetExcludingGoodwill", 5_004 * M),
        _e("c:IntangibleAssetsIncludingMortgageServicingRights", 5_004 * M),
        _e("c:IntangibleAssetsExcludingMortgageServicingRights", 4_216 * M),
    ]


class TestResolveMsrInRollup(unittest.TestCase):
    def test_citi_msr_ties_through_its_tagged_remainder(self):
        self.assertEqual(
            ov.resolve_msr_in_rollup(_citi_entries(), EQ_END, 5_004 * M),
            788 * M)

    def test_msr_reported_beside_the_rollup_never_nets(self):
        """No tagged remainder = rollup − MSR → the rollup is not proven to
        hold the MSR (FITB-style separate MSR line) → None."""
        ents = [e for e in _citi_entries()
                if "ExcludingMortgageServicingRights" not in e["concept"]]
        self.assertIsNone(ov.resolve_msr_in_rollup(ents, EQ_END, 5_004 * M))

    def test_remainder_off_by_another_item_is_no_proof(self):
        """PEBO 10-Q 2026-06-30 ($K): rollup 26,764 = 25,761 acquisition
        intangibles + 995 servicing rights + 8 non-compete. Its tagged
        remainder 25,761 ≠ 26,764 − 995 = 25,769 → not proven → None."""
        ents = [_e("us-gaap:IntangibleAssetsNetExcludingGoodwill", 26_764_000.0),
                _e("pebo:IntangibleAssetsNetExcludingGoodwillAndServicingRights",
                   25_761_000.0),
                _e("us-gaap:ServicingAsset", 995_000.0)]
        self.assertIsNone(ov.resolve_msr_in_rollup(ents, EQ_END, 26_764_000.0))

    def test_msr_at_another_date_never_nets(self):
        ents = [dict(e, end="2025-12-31") if "Servicing" in e["concept"]
                and "Intangible" not in e["concept"] else e
                for e in _citi_entries()]
        self.assertIsNone(ov.resolve_msr_in_rollup(ents, EQ_END, 5_004 * M))

    def test_two_tying_candidates_are_ambiguous(self):
        ents = _citi_entries() + [
            _e("us-gaap:ServicingAsset", 1_004 * M),
            _e("c:OtherIntangiblesSubtotal", 4_000 * M)]
        self.assertIsNone(ov.resolve_msr_in_rollup(ents, EQ_END, 5_004 * M))

    def test_msr_not_smaller_than_rollup_is_ignored(self):
        ents = [_e("us-gaap:ServicingAsset", 6_000 * M),
                _e("c:IntangibleAssetsExcluding", -996 * M)]
        self.assertIsNone(ov.resolve_msr_in_rollup(ents, EQ_END, 5_004 * M))


def _pt(end, val):
    return {"end": end, "val": val, "form": "10-Q", "filed": FILED}


def _citi_blob(with_record=True):
    blob = {"facts": {
        "us-gaap": {
            "StockholdersEquity": {"units": {"USD": [_pt(EQ_END, 212_015 * M)]}},
            "PreferredStockValue": {"units": {"USD": [_pt(EQ_END, 19_550 * M)]}},
            "Goodwill": {"units": {"USD": [_pt(EQ_END, 19_012 * M)]}},
            "IntangibleAssetsNetExcludingGoodwill": {"units": {"USD": [
                _pt(EQ_END, 5_004 * M)]}},
            "CommonStockSharesOutstanding": {"units": {"shares": [
                _pt(EQ_END, 1_677_436_783)]}},
        },
        "dei": {},
    }}
    if with_record:
        blob["_msr_in_rollup"] = {"end": EQ_END, "value": 788 * M,
                                  "accession": "000083100126000045", "form": "10-Q"}
    return blob


def _fundamentals(facts):
    with patch.object(sec_client, "fetch_company_facts", return_value=facts):
        return sec_client.get_latest_fundamentals(1)


class TestResolversNetTheMsr(unittest.TestCase):
    def test_without_the_record_the_full_rollup_is_deducted(self):
        """The failure being fixed: 19,012 + 5,004 = 24,016 deducted → TCE
        168,449M / 1,677,436,783 = 100.4205."""
        f = _fundamentals(_citi_blob(with_record=False))
        self.assertEqual(f["intangible_adjustment"], 24_016 * M)
        self.assertAlmostEqual(f["tangible_book_value_per_share"], 100.4205,
                               places=4)

    def test_citi_ties_to_its_release(self):
        f = _fundamentals(_citi_blob())
        self.assertEqual(f["intangible_adjustment"], 23_228 * M)
        self.assertAlmostEqual(f["tangible_book_value_per_share"], 100.8902,
                               places=4)

    def test_history_twin_agrees_at_the_record_date(self):
        adj, _ = sec_client._intangible_adjustment_at(_citi_blob(), EQ_END)
        self.assertEqual(adj, 23_228 * M)

    def test_record_for_another_date_is_ignored_by_the_twin(self):
        blob = _citi_blob()
        blob["_msr_in_rollup"]["end"] = "2025-12-31"
        adj, _ = sec_client._intangible_adjustment_at(blob, EQ_END)
        self.assertEqual(adj, 24_016 * M)


class TestOverlayTrigger(unittest.TestCase):
    _META = {"accession": "000083100126000045", "doc": "c-20260630.htm",
             "form": "10-Q", "date": FILED}

    def _run(self, blob, entries):
        with patch("data.sec_filing_scraper.latest_filing",
                   return_value=dict(self._META)), \
                patch.object(ov, "msr_entries", return_value=entries):
            return ov.overlay_msr(831001, blob)

    def test_attaches_record(self):
        blob = _citi_blob(with_record=False)
        out = self._run(blob, _citi_entries())
        self.assertEqual(out["_msr_in_rollup"]["value"], 788 * M)
        self.assertNotIn("_msr_in_rollup", blob, "cached blob never mutated")

    def test_blob_msr_at_the_date_skips_the_instance_fetch(self):
        blob = _citi_blob(with_record=False)
        blob["facts"]["us-gaap"]["ServicingAssetAtFairValueAmount"] = {
            "units": {"USD": [_pt(EQ_END, 788 * M)]}}

        def _boom(*a, **k):
            raise AssertionError("an MSR the blob carries needs no fetch")
        with patch("data.sec_filing_scraper.latest_filing", _boom), \
                patch.object(ov, "msr_entries", _boom):
            self.assertIs(ov.overlay_msr(831001, blob), blob)


if __name__ == "__main__":
    unittest.main()
