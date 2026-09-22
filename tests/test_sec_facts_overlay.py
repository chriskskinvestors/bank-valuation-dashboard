"""Filed-but-unpublished 10-Q overlay (data/sec_facts_overlay, 2026-09-22).

SEC companyfacts held nothing past Q1-2026 for ONB/FRME/HBAN/CCBG two
months after their Q2 10-Qs (full inline XBRL) were filed, and nothing past
FY2025 for Citi. Option B: complete the blob from the filing's own iXBRL so
the EXISTING derivation (TTM rules, share resolution, intangible adjustment,
staleness stamp) produces the current figures — no second formula.

Pins (hermetic: synthetic blob + synthetic instance facts, no network):
  1. Only NEW-period undimensioned facts of kept concepts are appended, in
     companyfacts' entry shape and the concept's existing unit bucket; the
     cached blob object is never mutated; "_overlay" records provenance.
  2. No-op when companyfacts is current, when the filing index is
     unavailable, and when the instance parse fails.
  3. End to end through sec_client.get_latest_fundamentals: sec_as_of moves
     to the filed quarter and BVPS/TBVPS are the hand-computed Q2 values.
  4. Card labels read "(co. 10-Q)" and the footnote names the filing.
"""
import copy
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text

from tests import _streamlit_stub

_streamlit_stub.install()

import data.cache as cache  # noqa: E402
import data.sec_facts_overlay as ov  # noqa: E402
import data.sec_client as sc  # noqa: E402
import ui.bank_detail as bd  # noqa: E402
from data.sec_filing_scraper import Fact  # noqa: E402


def _blob():
    """Q1-2026 companyfacts (as SEC served ONB on 2026-09-22), values
    chosen so the Q2 overlay is checkable by hand."""
    q = {"form": "10-Q", "filed": "2026-04-29", "accn": "0000707179-26-000030",
         "fy": 2026, "fp": "Q1"}
    k = {"form": "10-K", "filed": "2026-02-19", "accn": "0000707179-26-000010",
         "fy": 2025, "fp": "FY"}
    return {"cik": 707179, "entityName": "OLD NATIONAL BANCORP", "facts": {
        "us-gaap": {
            "StockholdersEquity": {"units": {"USD": [
                {"end": "2025-12-31", "val": 8_000_000_000.0, **k},
                {"end": "2026-03-31", "val": 8_200_000_000.0, **q}]}},
            "Assets": {"units": {"USD": [
                {"end": "2025-12-31", "val": 70e9, **k},
                {"end": "2026-03-31", "val": 71e9, **q}]}},
            "Goodwill": {"units": {"USD": [
                {"end": "2026-03-31", "val": 2_400_000_000.0, **q}]}},
            "IntangibleAssetsNetExcludingGoodwill": {"units": {"USD": [
                {"end": "2026-03-31", "val": 400_000_000.0, **q}]}},
            "PreferredStockValue": {"units": {"USD": [
                {"end": "2026-03-31", "val": 230_500_000.0, **q}]}},
            "CommonStockSharesOutstanding": {"units": {"shares": [
                {"end": "2026-03-31", "val": 380_000_000.0, **q}]}},
            "NetIncomeLoss": {"units": {"USD": [
                {"start": "2025-01-01", "end": "2025-12-31", "val": 600e6, **k},
                {"start": "2025-01-01", "end": "2025-09-30", "val": 440e6,
                 "form": "10-Q", "filed": "2025-10-29", "accn": "x", "fy": 2025, "fp": "Q3"},
                {"start": "2026-01-01", "end": "2026-03-31", "val": 170e6, **q}]}},
            "EarningsPerShareDiluted": {"units": {"USD/shares": [
                {"start": "2026-01-01", "end": "2026-03-31", "val": 0.45, **q}]}},
        },
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"end": "2026-04-24", "val": 381_000_000.0, **q}]}}},
    }}


_Q2 = "2026-06-30"
_FILING = {"accession": "000070717926000046", "doc": "onb-20260630.htm",
           "date": "2026-07-29", "form": "10-Q", "cik": 707179}
_PERIODIC = {"form": "10-Q", "date": "2026-07-29", "report_date": _Q2}


def _instance():
    """Undimensioned Q2 facts + a comparative + a dimensioned member + an
    unkept concept — only the first group may land in the blob."""
    def f(concept, val, end=_Q2, start=None, members=None, unit="usd"):
        return Fact(concept, val, end, start, members or {}, unit)
    return [
        f("us-gaap:StockholdersEquity", 8_500_000_000.0),
        f("us-gaap:Assets", 72e9),
        f("us-gaap:Goodwill", 2_400_000_000.0),
        f("us-gaap:IntangibleAssetsNetExcludingGoodwill", 380_000_000.0),
        f("us-gaap:PreferredStockValue", 230_500_000.0),
        f("us-gaap:CommonStockSharesOutstanding", 385_000_000.0, unit="shares"),
        f("us-gaap:NetIncomeLoss", 190e6, start="2026-04-01"),
        f("us-gaap:NetIncomeLoss", 360e6, start="2026-01-01"),
        f("us-gaap:EarningsPerShareDiluted", 0.49, start="2026-04-01", unit="usdPerShare"),
        f("dei:EntityCommonStockSharesOutstanding", 386_000_000.0, end="2026-07-24",
          unit="shares"),
        # comparatives / noise that must NOT be added
        f("us-gaap:StockholdersEquity", 8_000_000_000.0, end="2025-12-31"),
        f("us-gaap:StockholdersEquity", 1.0, members={"dim": "SegmentA"}),
        f("us-gaap:NotAKeptConceptXyz", 50e9),
        f("onb:SomeCustomTotal", 5.0),
    ]


class _IsolatedCache(unittest.TestCase):
    def setUp(self):
        eng = create_engine("sqlite://")
        with eng.begin() as conn:
            conn.execute(text(
                "CREATE TABLE cache (key VARCHAR(255) PRIMARY KEY, "
                "value TEXT NOT NULL, timestamp DOUBLE PRECISION NOT NULL)"))
        p = patch.object(cache, "_engine", eng)
        p.start()
        self.addCleanup(p.stop)

    def _overlay(self, blob, periodic=_PERIODIC, meta=_FILING, facts=None):
        with patch("data.sec_earnings_8k.latest_periodic_filing", return_value=periodic), \
             patch("data.sec_filing_scraper.latest_filing", return_value=dict(meta) if meta else None), \
             patch("data.sec_filing_scraper.instance_facts",
                   return_value=_instance() if facts is None else facts):
            return ov.overlay_lagging_filing(707179, blob)


class TestOverlayShape(_IsolatedCache):
    def test_new_period_facts_land_in_companyfacts_shape(self):
        blob = _blob()
        before = copy.deepcopy(blob)
        out = self._overlay(blob)
        self.assertEqual(blob, before)                      # cached object untouched
        ug = out["facts"]["us-gaap"]
        eq = ug["StockholdersEquity"]["units"]["USD"]
        self.assertEqual([e["end"] for e in eq], ["2025-12-31", "2026-03-31", _Q2])
        self.assertEqual(eq[-1], {"end": _Q2, "val": 8_500_000_000.0,
                                  "accn": "000070717926000046", "fy": 2026,
                                  "fp": "Q2", "form": "10-Q", "filed": "2026-07-29",
                                  "overlay": True})
        ni = ug["NetIncomeLoss"]["units"]["USD"]
        self.assertEqual([(e.get("start"), e["end"], e["val"]) for e in ni[-2:]],
                         [("2026-04-01", _Q2, 190e6), ("2026-01-01", _Q2, 360e6)])
        # per-share / share units follow the concept's existing bucket
        self.assertEqual(ug["EarningsPerShareDiluted"]["units"]["USD/shares"][-1]["val"], 0.49)
        self.assertEqual(ug["CommonStockSharesOutstanding"]["units"]["shares"][-1]["val"],
                         385_000_000.0)
        self.assertEqual(out["facts"]["dei"]["EntityCommonStockSharesOutstanding"]
                         ["units"]["shares"][-1]["end"], "2026-07-24")
        # nothing else crept in
        self.assertNotIn("NotAKeptConceptXyz", ug)
        self.assertNotIn("onb", out["facts"])
        self.assertEqual(len(eq), 3)                        # comparative + member skipped
        self.assertEqual(out["_overlay"], {
            "accession": "000070717926000046", "form": "10-Q",
            "report_date": _Q2, "filed": "2026-07-29", "n_facts": 10})

    def test_unit_bucket_for_a_concept_new_to_the_blob(self):
        blob = _blob()
        del blob["facts"]["us-gaap"]["EarningsPerShareDiluted"]
        del blob["facts"]["us-gaap"]["CommonStockSharesOutstanding"]
        out = self._overlay(blob)
        ug = out["facts"]["us-gaap"]
        self.assertIn("USD/shares", ug["EarningsPerShareDiluted"]["units"])
        self.assertIn("shares", ug["CommonStockSharesOutstanding"]["units"])

    def test_instance_parse_cached_by_accession(self):
        blob = _blob()
        with patch("data.sec_earnings_8k.latest_periodic_filing", return_value=_PERIODIC), \
             patch("data.sec_filing_scraper.latest_filing", return_value=dict(_FILING)), \
             patch("data.sec_filing_scraper.instance_facts",
                   return_value=_instance()) as inst:
            ov.overlay_lagging_filing(707179, blob)
            ov.overlay_lagging_filing(707179, blob)
        self.assertEqual(inst.call_count, 1)


class TestOverlayNoOps(_IsolatedCache):
    def test_current_blob_untouched(self):
        blob = _blob()
        out = self._overlay(blob, periodic={"form": "10-Q", "date": "2026-04-29",
                                            "report_date": "2026-03-31"})
        self.assertIs(out, blob)

    def test_index_unavailable_is_noop(self):
        blob = _blob()
        with patch("data.sec_earnings_8k.latest_periodic_filing",
                   side_effect=RuntimeError("EDGAR 503")):
            self.assertIs(ov.overlay_lagging_filing(707179, blob), blob)
        self.assertIs(self._overlay(blob, periodic=None), blob)

    def test_instance_failure_is_noop(self):
        blob = _blob()
        with patch("data.sec_earnings_8k.latest_periodic_filing", return_value=_PERIODIC), \
             patch("data.sec_filing_scraper.latest_filing", return_value=dict(_FILING)), \
             patch("data.sec_filing_scraper.instance_facts",
                   side_effect=RuntimeError("503")):
            self.assertIs(ov.overlay_lagging_filing(707179, blob), blob)
        self.assertIs(self._overlay(blob, facts=[]), blob)
        no_meta = _blob()
        self.assertIs(self._overlay(no_meta, meta=None), no_meta)


class TestEndToEnd(_IsolatedCache):
    def test_fundamentals_move_to_the_filed_quarter(self):
        overlaid = self._overlay(_blob())
        with patch.object(sc, "fetch_company_facts", return_value=overlaid):
            r = sc.get_latest_fundamentals(707179)
        self.assertEqual(r["sec_as_of"], _Q2)
        self.assertEqual(r["sec_facts_overlay"]["form"], "10-Q")
        self.assertEqual(r["book_value_total"], 8_500_000_000.0)
        # the EXISTING share-resolution rule picks the Q2 balance-sheet count
        # (the overlay adds facts; it never re-decides which one serves)
        self.assertEqual(r["shares_outstanding"], 385_000_000.0)
        common = 8_500_000_000.0 - 230_500_000.0
        self.assertAlmostEqual(r["book_value_per_share"], common / 385e6, places=9)
        self.assertAlmostEqual(r["tangible_book_value_per_share"],
                               (common - 2.4e9 - 380e6) / 385e6, places=9)

    def test_without_overlay_the_blob_stays_q1(self):
        with patch.object(sc, "fetch_company_facts", return_value=_blob()):
            r = sc.get_latest_fundamentals(707179)
        self.assertEqual(r["sec_as_of"], "2026-03-31")
        self.assertIsNone(r["sec_facts_overlay"])


class TestCardLabels(unittest.TestCase):
    _ROW = {"sec_facts_overlay": {"accession": "000070717926000046", "form": "10-Q",
                                  "report_date": _Q2, "filed": "2026-07-29"},
            "sec_facts_lag": False, "tbvps_source": "reconstructed",
            "bvps_source": "reconstructed", "eps_source": "reconstructed"}

    def test_overlaid_figures_say_which_filing(self):
        self.assertEqual(bd._ps_label(self._ROW, "TBV / Share", "tbvps_source"),
                         "TBV / Share (co. 10-Q)")
        self.assertEqual(bd._eps_label(self._ROW), "EPS (TTM, co. 10-Q)")
        self.assertEqual(
            bd._sec_lag_note(self._ROW),
            "HoldCo per-share figures above are from the Q2 2026 10-Q filed "
            "Jul 29, 2026 — read from the filing's own XBRL because SEC "
            "companyfacts has not yet published it.")

    def test_release_figures_still_win_the_label(self):
        row = {**self._ROW, "tbvps_source": "reported_8k", "eps_source": "release_ttm"}
        self.assertEqual(bd._ps_label(row, "TBV / Share", "tbvps_source"),
                         "TBV / Share (co. release)")
        self.assertEqual(bd._eps_label(row), "EPS (TTM, co. release)")


if __name__ == "__main__":
    unittest.main()
