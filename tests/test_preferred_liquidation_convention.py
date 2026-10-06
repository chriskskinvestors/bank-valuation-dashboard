"""(2026-10-06) A bank's OWN per-common-share figure may deduct preferred at
LIQUIDATION preference where our reconstruction deducts the carrying value.

BAFN 2Q26 (10-Q 0001649739-26-000061, release 8-K 0001649739-26-000058):
five series tagged per class — carrying (PreferredStockValue) 6,161 + 3,123
+ 6,446 + 37,254 + 37,254 = $90,238K; liquidation preference 6,395 + 3,210
+ 6,446 + 40,000 + 40,000 = $96,051K. Equity $115,901K, 4,106,905 shares,
$62K intangibles. The release: "Less: preferred stock liquidation
preference (96,051)" → common $19,850K → TCE $19,788K → $4.82 (book $4.83).
Ours at carrying: (115,901 − 90,238) ÷ 4,106.905 = $6.25. On a $19.9M common
base the $5.8M issuance discount is 29% per share — the ±15% gate called the
release a conflict and served $6.25, a figure the bank itself does not
print. Before the instance resolved BAFN's preferred (PR #290) there was
no reconstruction and $4.82 served unopposed.

Rule: when the release is gate-rejected and the instance carries a
liquidation preference above the carrying value, the release is re-gated
against the reconstruction restated at liquidation preference; a tie there
is the company's convention, served as its own figure. A release that ties
neither stays a conflict.

Run: python -m unittest tests.test_preferred_liquidation_convention
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

import analysis.valuation as va  # noqa: E402
from data import sec_client  # noqa: E402
from data import sec_facts_overlay as ov  # noqa: E402

END = (date.today() - timedelta(days=50)).isoformat()
CLASS = "StatementClassOfStockAxis"
EQ = "StatementEquityComponentsAxis"
PFD = "PreferredStockMember"
SERIES = {"A": (6_161e3, 6_395e3, 6395), "B": (3_123e3, 3_210e3, 3210),
          "C": (6_446e3, 6_446e3, 6446), "D": (37_254e3, 40_000e3, 4000),
          "E": (37_254e3, 40_000e3, 4000)}


def _e(concept, val, members=None, end=END):
    return {"concept": concept, "members": members or {}, "end": end,
            "val": float(val)}


def _bafn_entries():
    out = [_e("StockholdersEquity", 115_901e3)]
    for s, (car, liq, n) in SERIES.items():
        m = {CLASS: f"Series{s}PreferredStockMember"}
        out += [_e("PreferredStockValue", car, m),
                _e("PreferredStockLiquidationPreferenceValue", liq, m),
                _e("PreferredStockSharesOutstanding", n, m),
                _e("StockholdersEquity", car, {EQ: PFD, **m})]
    return out


class TestHandVerified(unittest.TestCase):
    def test_sums_and_per_share(self):
        self.assertEqual(sum(c for c, _, _ in SERIES.values()), 90_238e3)
        self.assertEqual(sum(l for _, l, _ in SERIES.values()), 96_051e3)
        self.assertAlmostEqual((115_901 - 96_051 - 62) / 4_106.905, 4.82, places=2)
        self.assertAlmostEqual((115_901 - 90_238) / 4_106.905, 6.25, places=2)


class TestInstanceRecordCarriesLiquidation(unittest.TestCase):
    def test_bafn_record(self):
        rec = ov.resolve_preferred_total(_bafn_entries(), END)
        self.assertEqual(rec["value"], 90_238e3)
        self.assertEqual(rec["liquidation"], 96_051e3)

    def test_no_liquidation_tag_is_none(self):
        ents = [e for e in _bafn_entries()
                if e["concept"] != "PreferredStockLiquidationPreferenceValue"]
        self.assertIsNone(ov.resolve_preferred_total(ents, END)["liquidation"])

    def test_liquidation_at_or_below_carrying_is_none(self):
        ents = [dict(e, val=e["val"] if e["concept"] != "PreferredStockLiquidationPreferenceValue"
                     else 1.0) for e in _bafn_entries()]
        self.assertIsNone(ov.resolve_preferred_total(ents, END)["liquidation"])

    def test_value_from_liquidation_concept_has_no_gap(self):
        # NPB-shape: only the liquidation concept is tagged per series.
        ents = [_e("StockholdersEquity", 611_648e3),
                _e("PreferredStockLiquidationPreferenceValue", 25_000e3,
                   {CLASS: "SeriesAPreferredStockMember"}),
                _e("PreferredStockSharesOutstanding", 1000,
                   {CLASS: "SeriesAPreferredStockMember"})]
        rec = ov.resolve_preferred_total(ents, END)
        self.assertEqual(rec["value"], 25_000e3)
        self.assertIsNone(rec["liquidation"])


def _pt(end, val, form="10-Q"):
    return {"end": end, "val": val, "form": form, "filed": date.today().isoformat()}


class TestFundamentalsExposeTheGap(unittest.TestCase):
    def _blob(self, with_rec=True):
        blob = {"facts": {"us-gaap": {
            "StockholdersEquity": {"units": {"USD": [_pt(END, 115_901_000)]}},
            "Assets": {"units": {"USD": [_pt(END, 1_134_925_000)]}},
            "IntangibleAssetsNetExcludingGoodwill": {"units": {"USD": [_pt(END, 62_000)]}},
            "CommonStockSharesOutstanding": {"units": {"shares": [_pt(END, 4_106_905)]}},
            "PreferredStockDividendsIncomeStatementImpact": {"units": {"USD": [
                {**_pt(END, 771_000), "start": "2026-01-01"}]}},
        }}}
        if with_rec:
            blob["_preferred_total"] = {"end": END, "value": 90_238_000.0,
                                        "liquidation": 96_051_000.0,
                                        "basis": "t", "accession": "x",
                                        "form": "10-Q"}
        return blob

    def test_gap_per_share(self):
        with patch.object(sec_client, "fetch_company_facts", return_value=self._blob()):
            r = sec_client.get_latest_fundamentals(1649739)
        self.assertEqual(r["preferred_stock"], 90_238_000.0)
        self.assertEqual(r["preferred_liquidation"], 96_051_000.0)
        self.assertAlmostEqual(r["tangible_book_value_per_share"],
                               (115_901_000 - 90_238_000 - 62_000) / 4_106_905,
                               places=6)
        self.assertAlmostEqual(r["book_value_per_share"], 6.25, places=2)
        self.assertAlmostEqual(va._liquidation_gap_per_share(r),
                               5_813_000 / 4_106_905, places=6)

    def test_no_record_no_gap(self):
        with patch.object(sec_client, "fetch_company_facts",
                          return_value=self._blob(with_rec=False)):
            r = sec_client.get_latest_fundamentals(1649739)
        self.assertIsNone(r["preferred_liquidation"])
        self.assertIsNone(va._liquidation_gap_per_share(r))


class TestResolverRetriesAtLiquidation(unittest.TestCase):
    GAP = 5_813_000 / 4_106_905          # 1.4154 per share

    def _run(self, answers, gap=GAP, bvps=6.2487):
        calls = []

        def fake(cik, reconstructed=None, bvps=None):
            calls.append((round(reconstructed, 4), bvps and round(bvps, 4)))
            return answers[len(calls) - 1]
        with patch("data.bank_mapping.get_cik", return_value=1649739), \
                patch.object(va, "_earnings_8k_predates", return_value=False), \
                patch("data.sec_earnings_8k.reported_tbvps_status", fake), \
                patch.object(va, "_otc_tbvps", return_value=None):
            out = va._resolve_tbvps("BAFN", 6.2487, bvps, sec_as_of=END,
                                    shares=4_106_905, liq_gap_ps=gap)
        return out, calls

    def test_bafn_release_ties_at_liquidation(self):
        out, calls = self._run([(None, "gate_rejected"), (4.82, "ok")])
        self.assertEqual(out, (4.82, "reported_8k", False))
        self.assertEqual(calls, [(6.2487, 6.2487), (4.8333, 4.8333)])

    def test_still_a_conflict_when_neither_ties(self):
        out, calls = self._run([(None, "gate_rejected"), (None, "gate_rejected")])
        self.assertEqual(out, (6.2487, "reconstructed", True))
        self.assertEqual(len(calls), 2)

    def test_no_gap_no_retry(self):
        out, calls = self._run([(None, "gate_rejected")], gap=None)
        self.assertEqual(out, (6.2487, "reconstructed", True))
        self.assertEqual(len(calls), 1)

    def test_not_disclosed_never_retries(self):
        out, calls = self._run([(None, "not_disclosed")])
        self.assertEqual(out, (6.2487, "reconstructed", False))
        self.assertEqual(len(calls), 1)

    def test_bvps_sibling(self):
        calls = []

        def fake(cik, reconstructed=None, tbvps=None):
            calls.append(round(reconstructed, 4))
            return [(None, "gate_rejected"), (4.83, "ok")][len(calls) - 1]
        with patch("data.bank_mapping.get_cik", return_value=1649739), \
                patch.object(va, "_earnings_8k_predates", return_value=False), \
                patch("data.sec_earnings_8k.reported_bvps_status", fake), \
                patch.object(va, "_otc_release_ps", return_value=None):
            out = va._resolve_bvps("BAFN", 6.2487, 4.82, sec_as_of=END,
                                   liq_gap_ps=self.GAP)
        self.assertEqual(out, (4.83, "reported_8k", False))
        self.assertEqual(calls, [6.2487, 4.8333])


if __name__ == "__main__":
    unittest.main()
