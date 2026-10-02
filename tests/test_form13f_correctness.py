"""
Pins the 13F ground-truth review fixes (2026-10-02) in data/form13f_client.py
and the Ownership Detailed label in ui/ownership.py. Fixtures are shaped like
the real EDGAR cases the review found (JPM / ONB / WTFC / LARK, Aug-2026
filings). All EDGAR access is faked at requests.get or at the module seams;
no live network.

  P0-1  options (putCall rows) and non-common instruments (CDs, ETNs, notes,
        a "Pref" class) were summed into common shares — CTC LLC showed
        92,017 JPM shares (common 81,497 + Call 4,552 + Put 5,968).
  P0-2  catch-up filings for old periods (Hamilton 2023Q4…, RAFFA 2019Q2)
        were shown and stored as the current quarter.
  P0-3  Δ QoQ compared against the previous FILING (Manulife LARK: the Q2
        original vs its Q2 amendment → "Unchanged"; true vs Q1 → −1.18%).
        No prior-quarter 13F at all → "n/a", never "New".
  P1-1  info tables whose filename lacks "info" (13f_Filer.xml) were dropped.
  P1-3  Detailed tab called a holder "New" when merely absent from the
        stored prior-quarter sample.
"""
import contextlib
import json
import unittest
from unittest.mock import patch

# Order-independent streamlit stub (shared helper).
from tests import _streamlit_stub

_streamlit_stub.install()

from data import form13f_client as f13  # noqa: E402

PREFIX = f13.FORM13F_CACHE_PREFIX
JPM_COMMON = "46625H100"
WTFC_COMMON = "97650W108"
LARK_COMMON = "51504L107"


# ── fakes ──────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, text="", payload=None, status=200):
        self.text = text
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _router(routes: dict):
    """requests.get fake: first route whose key is a substring of the URL."""
    def _get(url, *a, **k):
        for key, resp in routes.items():
            if key in url:
                return resp
        raise AssertionError(f"unexpected URL in test: {url}")
    return _get


def _row(name, cls, cusip, value, shares, put_call=None):
    pc = f"<putCall>{put_call}</putCall>" if put_call else ""
    return (f"<infoTable><nameOfIssuer>{name}</nameOfIssuer>"
            f"<titleOfClass>{cls}</titleOfClass><cusip>{cusip}</cusip>"
            f"<value>{value}</value><shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt>"
            f"<sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>{pc}</infoTable>")


def _info_xml(*rows):
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<informationTable xmlns="http://www.sec.gov/edgar/document/'
            'thirteenf/informationtable">' + "".join(rows) + "</informationTable>")


def _index(*names):
    return _Resp(payload={"directory": {"item": [{"name": n} for n in names]}})


class _MemStore:
    def __init__(self):
        self.files = {}

    def save_json(self, prefix, filename, data):
        self.files[(prefix, filename)] = json.loads(json.dumps(data, default=str))
        return True

    def load_json(self, prefix, filename):
        return self.files.get((prefix, filename))

    def list_files(self, prefix, pattern="*.json"):
        import fnmatch
        return sorted(n for (p, n) in self.files
                      if p == prefix and fnmatch.fnmatch(n, pattern))


class _StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = _MemStore()
        for fn in ("save_json", "load_json", "list_files"):
            p = patch.object(f13, fn, getattr(self.store, fn))
            p.start()
            self.addCleanup(p.stop)


# ── P0-1: options + non-common instruments ─────────────────────────────────

CTC_TABLE = _info_xml(
    _row("JPMORGAN CHASE &amp; CO", "COM", JPM_COMMON, 26_700_000, 81_497),
    _row("JPMORGAN CHASE &amp; CO", "COM", JPM_COMMON, 150_000_000, 4_552, "Call"),
    _row("JPMORGAN CHASE &amp; CO", "COM", JPM_COMMON, 194_300_000, 5_968, "Put"),
)


class TestOptionRowsExcluded(unittest.TestCase):

    def test_ctc_put_call_rows_not_summed_into_common(self):
        routes = {"/index.json": _index("primary_doc.xml", "infotable.xml"),
                  "/infotable.xml": _Resp(CTC_TABLE)}
        with patch.object(f13.requests, "get", _router(routes)):
            rows = f13._fetch_13f_info_table("1", "0000000001-26-000001",
                                             "JPMorgan Chase &")
        self.assertEqual([r["shares"] for r in rows], [81_497.0])
        self.assertEqual(rows[0]["value_thousands"], 26_700_000.0)

    def test_pref_class_at_end_of_string_excluded(self):
        # IAT Reinsurance: 240,000 WTFC preferred under class "Pref" — the
        # old "PREF " keyword needed a trailing space and missed it.
        table = _info_xml(
            _row("WINTRUST FINL CORP", "Pref", "97650W702", 6_000_000, 240_000),
            _row("WINTRUST FINL CORP", "COM", WTFC_COMMON, 1_300_000, 10_000))
        routes = {"/index.json": _index("primary_doc.xml", "infotable.xml"),
                  "/infotable.xml": _Resp(table)}
        with patch.object(f13.requests, "get", _router(routes)):
            rows = f13._fetch_13f_info_table("1", "0000000001-26-000002",
                                             "WINTRUST FINL CORP")
        self.assertEqual([r["cusip"] for r in rows], [WTFC_COMMON])


class TestCommonCusipRestriction(unittest.TestCase):
    """_holders_from_candidates keeps only the common CUSIP (the one held by
    the most distinct filers): RAFFA's JPMorgan Chase Bank NA CD and LYNX's
    Alerian ETN are dropped; a filer holding common + ETN keeps common only."""

    TABLES = {
        "A": [{"issuer": "JPMORGAN CHASE & CO", "cusip": JPM_COMMON,
               "class": "COM", "shares": 1_000.0, "value_thousands": 300_000.0}],
        "RAFFA": [{"issuer": "JPMORGAN CHASE & CO", "cusip": "48128HJF6",
                   "class": "CD", "shares": 100_000.0, "value_thousands": 100_000.0}],
        "B": [{"issuer": "JPMORGAN CHASE & CO", "cusip": JPM_COMMON,
               "class": "COM", "shares": 2_000.0, "value_thousands": 600_000.0}],
        "LYNX": [{"issuer": "JPMORGAN CHASE & CO", "cusip": "46625H365",
                  "class": "ALERIAN ML ETN", "shares": 50_000.0,
                  "value_thousands": 1_200_000.0}],
        "C": [{"issuer": "JPMORGAN CHASE & CO", "cusip": "46625h100 ",
               "class": "COM", "shares": 3_000.0, "value_thousands": 900_000.0}],
        "D": [{"issuer": "JPMORGAN CHASE & CO", "cusip": JPM_COMMON,
               "class": "COM", "shares": 4_000.0, "value_thousands": 1_200_000.0},
              {"issuer": "JPMORGAN CHASE & CO", "cusip": "48133Q309",
               "class": "NT", "shares": 9_999.0, "value_thousands": 9_999.0}],
    }

    def _cands(self, order):
        return [{"cik": str(1000 + i), "accession": f"acc-{k}", "filer_name": k,
                 "date_filed": "2026-08-14", "period_ending": "2026-06-30"}
                for i, k in enumerate(order)]

    def _run(self, order, max_filers):
        fake = lambda cik, acc, term: [dict(p) for p in self.TABLES[acc[4:]]]  # noqa: E731
        with patch.object(f13, "_fetch_13f_info_table", fake):
            return f13._holders_from_candidates(self._cands(order), "JPM",
                                                max_filers)

    def test_non_common_cusips_dropped_and_topped_up(self):
        # Phase 1 sees A, RAFFA, B, LYNX (common wins 2–1–1); RAFFA/LYNX drop
        # out and C, D top the list back up to max_filers.
        holders = self._run(["A", "RAFFA", "B", "LYNX", "C", "D"], max_filers=4)
        by = {h["filer_name"]: h for h in holders}
        self.assertEqual(set(by), {"A", "B", "C", "D"})
        self.assertEqual(by["D"]["shares"], 4_000.0)      # note row excluded
        self.assertEqual(by["D"]["cusip"], JPM_COMMON)
        self.assertEqual(by["C"]["shares"], 3_000.0)      # cusip normalised
        self.assertEqual(by["A"]["period_ending"], "2026-06-30")

    def test_tie_keeps_current_behaviour(self):
        holders = self._run(["A", "RAFFA"], max_filers=5)
        self.assertEqual({h["filer_name"] for h in holders}, {"A", "RAFFA"})
        self.assertIsNone(holders[0]["cusip"])

    def test_common_cusip_votes_once_per_filer(self):
        lists = [[{"cusip": "X"}, {"cusip": "X"}, {"cusip": "X"}],
                 [{"cusip": "Y"}], [{"cusip": "Y"}]]
        self.assertEqual(f13._common_cusip(lists), "Y")
        self.assertIsNone(f13._common_cusip([[{"cusip": ""}]]))


# ── P1-1: info-table filename without "info" ───────────────────────────────

class TestPriorTableMatchesByCusip(unittest.TestCase):
    """Quadrant Capital's Q1-2026 table names ONB "OLD NATL BANCORP IND"
    (32,892 sh, CUSIP 680033107) while its Q2 table spells it out — a name
    match on "Old National Bancorp" read Q1 as 0 → a false "New"."""

    TABLE = _info_xml(
        _row("OLD NATL BANCORP IND", "COM", "680033107", 800_000, 32_892),
        _row("OLD NATL BANCORP IND", "COM", "680033107", 50_000, 2_000, "Call"),
        _row("OLD NATL BANCORP IND", "DEP SHS PFD", "680033206", 90_000, 4_000))

    def _rows(self, cusip):
        routes = {"/index.json": _index("primary_doc.xml", "cwa13f2q26.xml"),
                  "/cwa13f2q26.xml": _Resp(self.TABLE)}
        with patch.object(f13.requests, "get", _router(routes)):
            return f13._fetch_13f_info_table("1650717", "0001650717-26-000002",
                                             "Old National Bancorp", cusip)

    def test_name_match_misses_abbreviated_issuer(self):
        self.assertEqual(self._rows(None), [])

    def test_cusip_match_finds_common_only(self):
        rows = self._rows("680033107")
        self.assertEqual([r["shares"] for r in rows], [32_892.0])


class TestInfoTableFileSelection(unittest.TestCase):
    TABLE = _info_xml(_row("LARK SAVINGS", "COM", LARK_COMMON, 2_000_000, 50_000))

    def test_filename_without_info_is_read(self):
        # ATLAS CAPITAL's real filing: 13f_Filer.xml + primary_doc.xml.
        routes = {"/index.json": _index("0001434165-26-000004-index.html",
                                        "primary_doc.xml", "13f_Filer.xml"),
                  "/13f_Filer.xml": _Resp(self.TABLE)}
        with patch.object(f13.requests, "get", _router(routes)):
            rows = f13._fetch_13f_info_table("1434165", "0001434165-26-000004",
                                             "LARK")
        self.assertEqual([r["shares"] for r in rows], [50_000.0])

    def test_several_xmls_pick_information_table_root(self):
        routes = {"/index.json": _index("primary_doc.xml", "aaa_cover.xml",
                                        "57123.xml"),
                  "/aaa_cover.xml": _Resp('<edgarSubmission xmlns="x"/>'),
                  "/57123.xml": _Resp(self.TABLE)}
        with patch.object(f13.requests, "get", _router(routes)):
            rows = f13._fetch_13f_info_table("1", "0000000001-26-000003", "LARK")
        self.assertEqual([r["cusip"] for r in rows], [LARK_COMMON])

    def test_unreadable_table_is_none_not_empty(self):
        routes = {"/index.json": _Resp(status=503)}
        with patch.object(f13.requests, "get", _router(routes)):
            self.assertIsNone(f13._fetch_13f_info_table("1", "0-26-1", "LARK"))
        routes = {"/index.json": _index("primary_doc.xml")}
        with patch.object(f13.requests, "get", _router(routes)):
            self.assertIsNone(f13._fetch_13f_info_table("1", "0-26-1", "LARK"))


# ── P0-2: stale-quarter filings ────────────────────────────────────────────

def _hit(cik, adsh, name, filed, period, form="13F-HR"):
    return {"_id": f"{adsh}:infotable.xml",
            "_source": {"ciks": [cik], "display_names": [f"{name}  (CIK {cik})"],
                        "file_date": filed, "period_ending": period,
                        "form": form}}


HAMILTON = "0002141005"
FTS_HITS = (
    [_hit("0000000001", "0000000001-26-000001", "Current A", "2026-08-10", "2026-06-30")]
    + [_hit(HAMILTON, f"0001213900-26-0896{i:02d}", "Hamilton Capital Partners Inc.",
            "2026-08-14", p)
       for i, p in enumerate(["2023-12-31", "2024-03-31", "2025-09-30",
                              "2026-06-30", "2026-03-31"])]
    + [_hit("0000000002", "0000000002-26-000001", "RAFFA", "2026-08-01", "2019-06-30"),
       _hit("0001616667", "0000894189-26-017939", "Pacer Advisors, Inc.",
            "2026-06-18", "2026-03-31", "13F-HR/A"),
       _hit("0001616667", "0000894189-26-022265", "Pacer Advisors, Inc.",
            "2026-08-13", "2026-06-30"),
       # ATLAS: original + same-day RESTATEMENT amendment → amendment wins
       _hit("0001434165", "0001434165-26-000005", "ATLAS CAPITAL ADVISORS INC.",
            "2026-08-13", "2026-06-30", "13F-HR/A"),
       _hit("0001434165", "0001434165-26-000004", "ATLAS CAPITAL ADVISORS INC.",
            "2026-08-13", "2026-06-30"),
       # NH Corp: original + later NEW HOLDINGS amendment → original kept
       _hit("0000000003", "0000000003-26-000009", "NH Corp", "2026-08-30",
            "2026-06-30", "13F-HR/A"),
       _hit("0000000003", "0000000003-26-000002", "NH Corp", "2026-08-12",
            "2026-06-30"),
       # Amendment-only filer → its amendment is the position
       _hit("0000000004", "0000000004-26-000003", "Amend Only LLC", "2026-08-20",
            "2026-06-30", "13F-HR/A"),
       _hit("0000000005", "0000000005-26-000001", "No Period LLC", "2026-08-05", None),
       _hit("0000000006", "0000000006-26-000001", "Current B", "2026-07-15",
            "2026-06-30")]
)

PRIMARY_DOC = ("<edgarSubmission><formData><coverPage><amendmentInfo>"
               "<amendmentType>{}</amendmentType></amendmentInfo></coverPage>"
               "</formData></edgarSubmission>")


def _fts_routes(hits):
    return {"efts.sec.gov": _Resp(payload={"hits": {"hits": hits}}),
            "/000143416526000005/primary_doc.xml":
                _Resp(PRIMARY_DOC.format("RESTATEMENT")),
            "/000000000326000009/primary_doc.xml":
                _Resp(PRIMARY_DOC.format("NEW HOLDINGS"))}


class TestCurrentPeriodSearch(unittest.TestCase):

    def _search(self, **kw):
        with patch.object(f13.requests, "get", _router(_fts_routes(FTS_HITS))):
            return f13._search_13f_for_ticker("Wintrust Financial", **kw)

    def test_only_target_period_one_filing_per_filer(self):
        out = self._search(limit=50)
        by = {c["cik"]: c for c in out}
        self.assertTrue(all(c["period_ending"] == "2026-06-30" for c in out))
        self.assertEqual(len(by), len(out))                       # 1 per filer
        self.assertNotIn("0000000002", by)                         # RAFFA 2019Q2
        self.assertNotIn("0000000005", by)                         # no period
        self.assertEqual(by[HAMILTON]["accession"], "0001213900-26-089603")
        self.assertEqual(by["0001616667"]["accession"], "0000894189-26-022265")
        self.assertEqual(by["0001434165"]["accession"], "0001434165-26-000005")
        self.assertEqual(by["0000000003"]["accession"], "0000000003-26-000002")
        self.assertEqual(by["0000000004"]["accession"], "0000000004-26-000003")
        # rank order = first hit per filer
        self.assertEqual(out[0]["cik"], "0000000001")
        self.assertEqual(out[1]["cik"], HAMILTON)

    def test_limit_applies_after_period_filter(self):
        out = self._search(limit=2)
        self.assertEqual([c["cik"] for c in out], ["0000000001", HAMILTON])

    def test_explicit_quarter_for_backfill(self):
        out = self._search(limit=50, quarter="2026Q1")
        self.assertEqual({c["cik"] for c in out}, {HAMILTON, "0001616667"})
        self.assertTrue(all(c["period_ending"] == "2026-03-31" for c in out))

    def test_no_period_anywhere_is_legacy_passthrough(self):
        hits = [_hit("1", "1-26-1", "X", "2026-08-01", None),
                _hit("2", "2-26-1", "Y", "2026-08-02", None)]
        with patch.object(f13.requests, "get", _router(_fts_routes(hits))):
            out = f13._search_13f_for_ticker("X", limit=10)
        self.assertEqual([c["cik"] for c in out], ["1", "2"])

    def test_backfill_passes_its_quarter(self):
        with patch.object(f13, "load_json", return_value=None), \
             patch.object(f13, "_search_13f_for_ticker", return_value=[]) as s:
            f13.backfill_quarter("WTFC", "Wintrust Financial", "2025q4")
        self.assertEqual(s.call_args.kwargs.get("quarter"), "2025Q4")


class TestSnapshotRoutedByPeriod(_StoreTest):

    def test_period_ending_wins_over_filing_date(self):
        f13._save_quarter_snapshots("WTFC", [
            {"filer_cik": "1", "filer_name": "Late", "date_filed": "2026-08-14",
             "period_ending": "2025-12-31", "shares": 1.0, "value_usd": 1.0},
            {"filer_cik": "2", "filer_name": "Legacy", "date_filed": "2026-08-14",
             "shares": 2.0, "value_usd": 2.0},
        ])
        q4 = self.store.files[(PREFIX, "WTFC_2025Q4.json")]["holders"]
        q2 = self.store.files[(PREFIX, "WTFC_2026Q2.json")]["holders"]
        self.assertEqual([h["filer_cik"] for h in q4], ["1"])
        self.assertEqual([h["filer_cik"] for h in q2], ["2"])   # date fallback

    def test_end_to_end_stale_filings_never_reach_current_snapshot(self):
        table = {"common": [{"issuer": "WINTRUST FINL CORP", "cusip": WTFC_COMMON,
                             "class": "COM", "shares": 10.0,
                             "value_thousands": 1_300.0}]}
        fake = lambda cik, acc, term: [dict(p) for p in table["common"]]  # noqa: E731
        with patch.object(f13.requests, "get", _router(_fts_routes(FTS_HITS))), \
             patch.object(f13, "_fetch_13f_info_table", fake):
            holders = f13.fetch_institutional_holdings(
                "WTFC", "Wintrust Financial", max_filers=30, with_changes=False,
                force=True)
        names = {h["filer_name"] for h in holders}
        self.assertNotIn("RAFFA", names)
        self.assertNotIn("No Period LLC", names)
        snaps = {n for (_, n) in self.store.files if n.startswith("WTFC_")}
        self.assertEqual(snaps, {"WTFC_2026Q2.json"})


# ── P0-3: Δ QoQ vs the previous QUARTER ────────────────────────────────────

def _submissions(rows):
    """rows: (form, accession, filingDate, reportDate) newest first."""
    return _Resp(payload={"filings": {"recent": {
        "form": [r[0] for r in rows], "accessionNumber": [r[1] for r in rows],
        "filingDate": [r[2] for r in rows], "reportDate": [r[3] for r in rows]}}})


MANULIFE = "0000911191"
MANULIFE_SUBS = [
    ("13F-HR/A", "m-q2-amend", "2026-08-20", "2026-06-30"),
    ("13F-HR", "m-q2-orig", "2026-08-13", "2026-06-30"),
    ("8-K", "m-8k", "2026-06-01", "2026-05-29"),
    ("13F-HR/A", "m-q1-amend", "2026-05-30", "2026-03-31"),
    ("13F-HR", "m-q1-orig", "2026-05-14", "2026-03-31"),
    ("13F-HR", "m-q4", "2026-02-12", "2025-12-31"),
]


def _lark(shares, cusip=LARK_COMMON):
    return [{"issuer": "LARK SAVINGS", "cusip": cusip, "class": "COM",
             "shares": shares, "value_thousands": shares * 30.0}]


class TestPriorQuarterShares(unittest.TestCase):

    TABLES = {"m-q2-amend": _lark(298_870.0), "m-q2-orig": _lark(298_870.0),
              "m-q1-orig": _lark(302_438.0) + _lark(7_000.0, "51504L909"),
              "m-q1-amend": _lark(1.0), "m-q4": _lark(290_000.0)}

    def _prior(self, subs, quarter="2026Q2", tables=None,
               q1_amend_type="NEW HOLDINGS"):
        tables = tables or self.TABLES
        fetched = []
        routes = {"data.sec.gov/submissions": _submissions(subs),
                  "/mq1amend/primary_doc.xml":      # accession, hyphens dropped
                      _Resp(PRIMARY_DOC.format(q1_amend_type))}

        def fake(cik, acc, term, cusip=None):
            fetched.append(acc)
            self.assertEqual(cusip, LARK_COMMON)   # prior matches by CUSIP
            t = tables.get(acc)
            return None if t is None else [dict(p) for p in t
                                           if p["cusip"] == cusip]

        with patch.object(f13.requests, "get", _router(routes)), \
             patch.object(f13, "_fetch_13f_info_table", fake):
            return f13._prior_quarter_shares(MANULIFE, quarter, "LARK",
                                             LARK_COMMON), fetched

    def test_manulife_compares_q2_to_q1_original(self):
        prior, fetched = self._prior(MANULIFE_SUBS)
        self.assertEqual(fetched, ["m-q1-orig"])   # not the Q2 original
        self.assertEqual(prior, 302_438.0)         # other-CUSIP row excluded
        chg = f13._classify_change(298_870.0, prior)
        self.assertEqual(chg["change_status"], "Trimmed")
        self.assertAlmostEqual(chg["change_pct"], -1.1797, places=3)

    def test_restatement_supersedes_prior_original(self):
        # JPMorgan's own Q1-2026 13F: the original (-26-000212) carries no JPM
        # rows, the same-day RESTATEMENT (-26-000225) does. Preferring the
        # original turned a +1.44% add into a false "New".
        prior, fetched = self._prior(MANULIFE_SUBS, q1_amend_type="RESTATEMENT")
        self.assertEqual(fetched, ["m-q1-amend"])
        self.assertEqual(prior, 1.0)

    def test_amendment_used_only_without_original(self):
        subs = [r for r in MANULIFE_SUBS if r[1] != "m-q1-orig"]
        _, fetched = self._prior(subs)
        self.assertEqual(fetched, ["m-q1-amend"])

    def test_no_prior_quarter_filing_is_none(self):
        subs = [r for r in MANULIFE_SUBS if "q1" not in r[1]]
        prior, fetched = self._prior(subs)
        self.assertIsNone(prior)
        self.assertEqual(fetched, [])
        self.assertEqual(f13._classify_change(298_870.0, prior),
                         {"change_status": "n/a", "change_pct": None})

    def test_prior_filed_without_stock_is_zero_and_new(self):
        prior, _ = self._prior(MANULIFE_SUBS, tables={"m-q1-orig": []})
        self.assertEqual(prior, 0.0)
        self.assertEqual(f13._classify_change(10.0, prior)["change_status"], "New")

    def test_unreadable_prior_table_raises(self):
        with self.assertRaises(RuntimeError):
            self._prior(MANULIFE_SUBS, tables={"m-q2-orig": _lark(1.0)})

    def test_q1_wraps_to_prior_year_q4(self):
        prior, fetched = self._prior(MANULIFE_SUBS, quarter="2026Q1")
        self.assertEqual(fetched, ["m-q4"])
        self.assertEqual(prior, 290_000.0)


class TestChangeStatusesEndToEnd(_StoreTest):

    def test_statuses(self):
        ciks = {"Manulife": "911191", "NoPrior": "2", "Newbie": "3", "Broken": "4"}
        cands = [{"cik": cik, "accession": f"{c}-q2", "filer_name": c,
                  "date_filed": "2026-08-13", "period_ending": "2026-06-30"}
                 for c, cik in ciks.items()]
        current = {f"{c}-q2": _lark(s) for c, s in
                   (("Manulife", 298_870.0), ("NoPrior", 5_000.0),
                    ("Newbie", 7_000.0), ("Broken", 9_000.0))}
        prior_tables = {"Manulife-q1": _lark(302_438.0), "Newbie-q1": [],
                        "Broken-q1": None}
        histories = {
            "Manulife": [("Manulife-q2", "2026-08-13", "2026-06-30", "13F-HR"),
                         ("Manulife-q1", "2026-05-14", "2026-03-31", "13F-HR")],
            "NoPrior": [("NoPrior-q2", "2026-08-13", "2026-06-30", "13F-HR")],
            "Newbie": [("Newbie-q2", "2026-08-13", "2026-06-30", "13F-HR"),
                       ("Newbie-q1", "2026-05-14", "2026-03-31", "13F-HR")],
            "Broken": [("Broken-q2", "2026-08-13", "2026-06-30", "13F-HR"),
                       ("Broken-q1", "2026-05-14", "2026-03-31", "13F-HR")],
        }

        def fetch(cik, acc, term, cusip=None):
            t = current.get(acc, prior_tables.get(acc))
            return None if t is None else [dict(p) for p in t]

        with patch.object(f13, "_search_13f_for_ticker", return_value=cands), \
             patch.object(f13, "_fetch_13f_info_table", fetch), \
             patch.object(f13, "_filer_13f_history",
                          lambda cik: histories[{v: k for k, v in ciks.items()}[cik]]):
            holders = f13.fetch_institutional_holdings("LARK", max_filers=10,
                                                       force=True)
        by = {h["filer_name"]: h for h in holders}
        self.assertEqual(by["Manulife"]["change_status"], "Trimmed")
        self.assertAlmostEqual(by["Manulife"]["change_pct"], -1.1797, places=3)
        self.assertEqual(by["NoPrior"]["change_status"], "n/a")
        self.assertIsNone(by["NoPrior"]["prior_shares"])
        self.assertEqual(by["Newbie"]["change_status"], "New")
        self.assertEqual(by["Broken"]["change_status"], "Unknown")

    def test_filer_history_reads_report_date(self):
        with patch.object(f13.requests, "get",
                          _router({"data.sec.gov/submissions":
                                   _submissions(MANULIFE_SUBS)})):
            hist = f13._filer_13f_history(MANULIFE)
        self.assertEqual(hist[0], ("m-q2-amend", "2026-08-20", "2026-06-30",
                                   "13F-HR/A"))
        self.assertNotIn("m-8k", [h[0] for h in hist])


class TestQuarterHelpers(unittest.TestCase):
    def test_period_and_prev_quarter(self):
        self.assertEqual(f13._period_quarter("2026-06-30"), "2026Q2")
        self.assertEqual(f13._period_quarter("2025-12-31"), "2025Q4")
        self.assertIsNone(f13._period_quarter(None))
        self.assertIsNone(f13._period_quarter("06-30-2026"))
        self.assertEqual(f13._prev_quarter("2026Q1"), "2025Q4")
        self.assertEqual(f13._prev_quarter("2026Q3"), "2026Q2")
        self.assertIsNone(f13._prev_quarter("bad"))


# ── P1-3: Detailed tab label ───────────────────────────────────────────────

class TestDetailedNotInPriorSampleLabel(unittest.TestCase):

    def test_absent_from_prior_sample_is_not_labelled_new(self):
        from ui import ownership as own
        holders = [
            {"filer_name": "Alpha LLC", "filer_cik": "1", "accession": "a-1",
             "shares": 1_000.0, "value_usd": 9.0, "date_filed": "2026-08-14"},
            {"filer_name": "Beta LP", "filer_cik": "2", "accession": "b-1",
             "shares": 500.0, "value_usd": 5.0, "date_filed": "2026-08-14"},
        ]
        hist = {"Alpha LLC": {"2026Q2": {"shares": 1_000.0},
                              "2026Q1": {"shares": 800.0}},
                "Beta LP": {"2026Q2": {"shares": 500.0}}}
        html = []
        with patch.object(own, "fetch_institutional_holdings", return_value=holders), \
             patch.object(own, "get_name", return_value="Lark"), \
             patch.object(own, "title_bar"), \
             patch.object(own, "table_export"), \
             patch.object(own, "_skeleton", contextlib.nullcontext), \
             patch.object(own.st, "markdown",
                          lambda s, *a, **k: html.append(s), create=True), \
             patch.object(own.st, "caption", lambda *a, **k: None, create=True), \
             patch("data.form13f_client.get_holder_history", return_value=hist), \
             patch("data.fmp_client.get_quote", return_value={"price": 30.0}):
            own.render_ownership_detailed("LARK", {"shares_outstanding": 1e6})
        table = next(h for h in html if "<table>" in h)
        self.assertIn("n/a (not in prior sample)", table)
        self.assertNotIn(">New<", table)
        self.assertIn("+200", table)            # Alpha's real delta still shown


if __name__ == "__main__":
    unittest.main()
