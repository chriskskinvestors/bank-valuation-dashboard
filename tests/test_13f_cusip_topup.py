"""CUSIP top-up of the 13F holder list (data/form13f_client._cusip_top_up).

The name search finds only filers who spell the issuer the same way: for
FCBC 2026Q2, "First Community Bankshares" hit 16 13F-HRs (14 holders shown)
while most filers write "FIRST CMNTY BANKSHARES" and CUSIP 31983A103 hit 181.
The top-up searches the common CUSIP in the same quarter and matches the added
filers' rows by CUSIP. Within one table, a row under a second spelling that
shares a name-matched row's CUSIP is kept too (Russell). All EDGAR access
is faked at the module seams.
"""
import unittest
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

from data import form13f_client as f13  # noqa: E402

FCBC = "31983A103"
TERM = "First Community Bankshares"


def _cand(cik, period="2026-06-30"):
    return {"cik": cik, "accession": f"acc-{cik}", "filer_name": f"Filer {cik}",
            "date_filed": "2026-08-10", "period_ending": period, "form": "13F-HR"}


def _holder(cik, value, cusip=FCBC, period="2026-06-30"):
    return {"filer_cik": cik, "filer_name": f"Filer {cik}", "shares": 10.0,
            "value_usd": value, "cusip": cusip, "period_ending": period,
            "date_filed": "2026-08-10", "accession": f"acc-{cik}"}


def _rows(shares, value):
    return [{"issuer": "FIRST CMNTY BANKSHARES INC NEV", "cusip": FCBC,
             "class": "COM", "shares": shares, "value_thousands": value}]


class TestCusipTopUp(unittest.TestCase):

    def setUp(self):
        self.searches = []
        self.fetches = []

    def _run(self, holders, hits, tables, max_filers=5, **window):
        def search(term, limit=40, startdt=None, enddt=None, quarter=None):
            self.searches.append((term, startdt, enddt, quarter))
            return hits

        def fetch(cik, acc, term, cusip=None):
            self.fetches.append((cik, term, cusip))
            return tables.get(cik, [])

        with patch.object(f13, "_search_13f_for_ticker", search), \
             patch.object(f13, "_fetch_13f_info_table", fetch):
            return f13._cusip_top_up(holders, TERM, max_filers, **window)

    def test_adds_cusip_only_filers_same_quarter_sorted(self):
        holders = [_holder("1", 500_000.0), _holder("2", 1_000.0)]
        hits = [_cand("2"), _cand("3"), _cand("4")]
        tables = {"3": _rows(1_000, 30_000.0), "4": _rows(2_000, 90_000.0)}
        out = self._run(holders, hits, tables)
        self.assertEqual(self.searches, [(FCBC, None, None, "2026Q2")])
        # Already-shown filer 2 is not re-fetched; rows match by CUSIP.
        self.assertEqual(self.fetches, [("3", TERM, FCBC), ("4", TERM, FCBC)])
        self.assertEqual([h["filer_cik"] for h in out], ["1", "4", "3", "2"])
        added = {h["filer_cik"]: h for h in out}
        self.assertEqual(added["4"]["shares"], 2_000)
        self.assertEqual(added["4"]["value_usd"], 90_000.0)   # post-2023: raw $
        self.assertEqual(added["4"]["cusip"], FCBC)

    def test_stops_at_max_filers(self):
        holders = [_holder("1", 500.0)]
        hits = [_cand(str(i)) for i in range(2, 10)]
        tables = {str(i): _rows(10, 100.0) for i in range(2, 10)}
        out = self._run(holders, hits, tables, max_filers=3)
        self.assertEqual(len(out), 3)
        self.assertEqual(len(self.fetches), 2)

    def test_filer_without_the_cusip_or_unreadable_is_skipped(self):
        holders = [_holder("1", 500.0)]
        hits = [_cand("2"), _cand("3")]
        tables = {"2": None, "3": []}
        out = self._run(holders, hits, tables)
        self.assertEqual([h["filer_cik"] for h in out], ["1"])

    def test_no_top_up_when_full_ambiguous_or_quarterless(self):
        cases = {
            "already full": [_holder(str(i), 1.0) for i in range(5)],
            "ambiguous cusip": [_holder("1", 1.0, cusip=None)],
            "no period": [_holder("1", 1.0, period=None)],
            "no holders": [],
        }
        for label, holders in cases.items():
            self.searches.clear()
            out = self._run(holders, [_cand("9")], {"9": _rows(1, 1.0)})
            self.assertEqual(out, holders, label)
            self.assertEqual(self.searches, [], label)

    def test_backfill_window_is_passed_through(self):
        holders = [_holder("1", 500.0, period="2025-12-31")]
        self._run(holders, [], {}, startdt="2026-01-01", enddt="2026-03-16")
        self.assertEqual(self.searches,
                         [(FCBC, "2026-01-01", "2026-03-16", "2025Q4")])


class TestSameTableSecondSpelling(unittest.TestCase):
    """Russell Investments 2026Q2 lists FCBC twice under one CUSIP:
    "First Community Bankshares Inc" 8,837 sh + "FIRST CMNTY BANCSHARES INC N"
    94 sh. The name match kept only the first (8,837 shown, 8,931 filed)."""

    def test_row_sharing_a_matched_cusip_is_kept(self):
        from tests.test_form13f_correctness import (_Resp, _index, _info_xml,
                                                    _router, _row)
        table = _info_xml(
            _row("FIRST CMNTY BANCSHARES INC N", "Common stock", FCBC, 4_175, 94),
            _row("First Community Bankshares Inc", "Common Stock", FCBC, 392_539, 8_837),
            _row("FIRST HORIZON CORP", "COM", "320517105", 9_999, 500))
        routes = {"/index.json": _index("primary_doc.xml", "infotable.xml"),
                  "/infotable.xml": _Resp(table)}
        with patch.object(f13.requests, "get", _router(routes)):
            rows = f13._fetch_13f_info_table("1692234", "0001193125-26-336961", TERM)
        self.assertEqual(sum(r["shares"] for r in rows), 8_931)
        self.assertEqual(sum(r["value_thousands"] for r in rows), 396_714)
        self.assertEqual({r["cusip"] for r in rows}, {FCBC})


class TestFetchHoldingsUsesTopUp(unittest.TestCase):

    def test_live_path_tops_up_from_cusip_search(self):
        def search(term, limit=40, startdt=None, enddt=None, quarter=None):
            return [_cand("1")] if term == TERM else [_cand("1"), _cand("2")]

        def fetch(cik, acc, term, cusip=None):
            if cusip is None and cik != "1":
                return []           # abbreviated spelling: name match misses
            return _rows(1_000, 40_000.0)

        store = {}
        with patch.object(f13, "_search_13f_for_ticker", search), \
             patch.object(f13, "_fetch_13f_info_table", fetch), \
             patch.object(f13, "load_json", lambda p, n: store.get((p, n))), \
             patch.object(f13, "save_json",
                          lambda p, n, d: store.__setitem__((p, n), d)):
            holders = f13.fetch_institutional_holdings(
                "FCBC", "First Community Bankshares", max_filers=30,
                with_changes=False, force=True)
        self.assertEqual(sorted(h["filer_cik"] for h in holders), ["1", "2"])
        self.assertTrue(all(h["cusip"] == FCBC for h in holders))


if __name__ == "__main__":
    unittest.main()
