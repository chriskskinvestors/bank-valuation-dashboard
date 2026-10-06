"""
Render-path cache TTLs for Company › Ownership (review P1-10 / §1.3).

The 13F and Form 4 renders served the per-bank cache file only while it was
<24h old, but the warming jobs run quarterly (13F) and weekday mornings
(Form 4) — so the first view of a bank most days crawled EDGAR on the render
thread (86 s / 30 s cold). Fix: render reads use a longer file TTL (13F 7d,
Form 4 4d) and the jobs call with force=True so they still refresh.

Pins (all storage + EDGAR seams mocked; no network):
  • 13F: 3-day-old file served with zero crawl calls; 8-day-old file crawls;
    force=True on a 1-hour-old file crawls and rewrites cached_at
  • Form 4: 30-hour-old file (weekend gap) served with zero fetches; 5-day-old
    file fetches; force=True on a fresh file fetches and rewrites cached_at
  • jobs/refresh_13f.main and jobs/refresh_insider.main pass force=True

Run:  python -m unittest tests.test_ownership_render_cache -v
"""
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

# Order-independent streamlit stub (shared helper).
from tests import _streamlit_stub

_streamlit_stub.install()

from data import form13f_client as f13  # noqa: E402
from data import form4_client as f4  # noqa: E402


def _stamp(age: timedelta) -> str:
    return (datetime.now() - age).isoformat()


class _MemStore:
    """In-memory stand-in for data.cloud_storage save/load/list."""

    def __init__(self):
        self.files: dict[tuple[str, str], dict] = {}

    def save_json(self, prefix, filename, data) -> bool:
        self.files[(prefix, filename)] = json.loads(json.dumps(data, default=str))
        return True

    def load_json(self, prefix, filename):
        return self.files.get((prefix, filename))

    def list_files(self, prefix, pattern="*.json"):
        return sorted(n for (p, n) in self.files if p == prefix)


class Test13FRenderCache(unittest.TestCase):

    TICKER = "ZZTB"
    CACHED = [{"filer_cik": "0000000009", "filer_name": "Cached Holder",
               "shares": 1.0, "value_usd": 1.0, "date_filed": "2026-08-10"}]

    def setUp(self):
        self.store = _MemStore()
        self.calls = {"search": 0, "holders": 0, "prior": 0}

        def _search(term, limit=40, *a, **k):
            self.calls["search"] += 1
            return [{"cik": "0000000001"}]

        def _holders(candidates, term, max_filers):
            self.calls["holders"] += 1
            return [{"filer_cik": "0000000001", "filer_name": "Fresh Holder",
                     "shares": 100.0, "value_usd": 5000.0,
                     "date_filed": "2026-08-12"}]

        def _prior(cik, quarter, term, cusip=None):
            self.calls["prior"] += 1
            return 80.0

        for name, fn in (("save_json", self.store.save_json),
                         ("load_json", self.store.load_json),
                         ("list_files", self.store.list_files),
                         ("_search_13f_for_ticker", _search),
                         ("_holders_from_candidates", _holders),
                         ("_prior_quarter_shares", _prior)):
            p = patch.object(f13, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def _seed(self, age):
        self.store.files[(f13.FORM13F_CACHE_PREFIX, f"{self.TICKER}.json")] = {
            "ticker": self.TICKER, "cached_at": _stamp(age),
            "holders": self.CACHED}

    def _crawls(self):
        return self.calls["search"] + self.calls["holders"] + self.calls["prior"]

    def test_three_day_old_file_served_without_crawl(self):
        self._seed(timedelta(days=3))
        out = f13.fetch_institutional_holdings(self.TICKER, "Zz Bank")
        self.assertEqual(out, self.CACHED)
        self.assertEqual(self._crawls(), 0,
                         "a 3-day-old 13F file must not trigger an EDGAR crawl")

    def test_eight_day_old_file_crawls(self):
        self._seed(timedelta(days=8))
        out = f13.fetch_institutional_holdings(self.TICKER, "Zz Bank")
        self.assertEqual(out[0]["filer_name"], "Fresh Holder")
        self.assertEqual(self.calls["search"], 1)
        self.assertEqual(self.calls["holders"], 1)
        self.assertEqual(self.calls["prior"], 1)

    def test_force_recrawls_fresh_file_and_rewrites(self):
        self._seed(timedelta(hours=1))
        before = datetime.now()
        out = f13.fetch_institutional_holdings(self.TICKER, "Zz Bank",
                                               force=True)
        self.assertEqual(out[0]["filer_name"], "Fresh Holder")
        self.assertEqual(self.calls["search"], 1)
        saved = self.store.files[(f13.FORM13F_CACHE_PREFIX,
                                  f"{self.TICKER}.json")]
        self.assertEqual(saved["holders"][0]["filer_name"], "Fresh Holder")
        self.assertGreaterEqual(datetime.fromisoformat(saved["cached_at"]),
                                before)


class TestForm4RenderCache(unittest.TestCase):

    CIK = 987650001
    CACHED = [{"insider": "Cached Insider", "date": "2026-09-01",
               "code": "P", "form_type": "non-derivative", "accession": "c-1"}]

    def setUp(self):
        # Real st.cache_data (when streamlit was imported first) is a memo —
        # clear it so each test reaches the file-TTL logic.
        clear = getattr(f4.fetch_insider_history, "clear", None)
        if clear:
            clear()
            self.addCleanup(clear)
        self.store = _MemStore()
        self.calls = {"submissions": 0, "xml": 0}
        today = datetime.now().strftime("%Y-%m-%d")
        test = self

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"filings": {"recent": {
                    "form": ["4"], "accessionNumber": ["0000000000-26-000001"],
                    "filingDate": [today], "reportDate": [today],
                    "acceptanceDateTime": [f"{today}T16:00:00.000Z"]}}}

        def _get(url, *a, **k):
            test.calls["submissions"] += 1
            return _Resp()

        def _xml(accession, cik, primary_doc=None):
            test.calls["xml"] += 1
            return "<ownershipDocument/>"

        def _parse(xml):
            return [{"insider": "Fresh Insider", "date": today, "code": "P",
                     "form_type": "non-derivative"}]

        for target, name, fn in ((f4, "save_json", self.store.save_json),
                                 (f4, "load_json", self.store.load_json),
                                 (f4.requests, "get", _get),
                                 (f4, "_fetch_form4_xml", _xml),
                                 (f4, "_parse_form4", _parse)):
            p = patch.object(target, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def _seed(self, age):
        self.store.files[(f4.FORM4_CACHE_PREFIX, f"{self.CIK}.json")] = {
            "cik": self.CIK, "cached_at": _stamp(age),
            "transactions": self.CACHED}

    def test_thirty_hour_old_file_served_without_fetch(self):
        # Friday's 04:30 job file read on Saturday morning — the weekend gap.
        self._seed(timedelta(hours=30))
        out = f4.fetch_insider_trades(self.CIK)
        self.assertEqual([t["insider"] for t in out], ["Cached Insider"])
        self.assertEqual(self.calls, {"submissions": 0, "xml": 0},
                         "a 30-hour-old Form 4 file must not trigger an EDGAR crawl")

    def test_five_day_old_file_fetches(self):
        self._seed(timedelta(days=5))
        out = f4.fetch_insider_trades(self.CIK)
        self.assertEqual([t["insider"] for t in out], ["Fresh Insider"])
        self.assertEqual(self.calls, {"submissions": 1, "xml": 1})

    def test_force_refetches_fresh_file_and_rewrites(self):
        self._seed(timedelta(hours=1))
        before = datetime.now()
        out = f4.fetch_insider_trades(self.CIK, force=True)
        self.assertEqual([t["insider"] for t in out], ["Fresh Insider"])
        self.assertEqual(self.calls, {"submissions": 1, "xml": 1})
        saved = self.store.files[(f4.FORM4_CACHE_PREFIX, f"{self.CIK}.json")]
        self.assertEqual(saved["transactions"][0]["insider"], "Fresh Insider")
        self.assertGreaterEqual(datetime.fromisoformat(saved["cached_at"]),
                                before)


class TestJobsForceRefresh(unittest.TestCase):
    """The warming jobs call the render function — without force=True they'd
    get a fresh file handed back and never refresh it."""

    def test_refresh_13f_main_forces(self):
        from jobs import refresh_13f
        seen = []

        def _fetch(t, name="", *a, **k):
            seen.append((t, k.get("force")))
            return [{"filer_cik": "1"}]

        with patch("data.bank_universe.get_universe_tickers",
                   return_value=["AAA", "BBB"]), \
             patch("data.bank_mapping.get_name", return_value="A Bank"), \
             patch("data.form13f_client.fetch_institutional_holdings", _fetch), \
             patch("data.cloud_storage.load_json", return_value=None), \
             patch.object(refresh_13f.time, "sleep"):
            rc = refresh_13f.main()
        self.assertEqual(rc, 0)
        self.assertEqual(seen, [("AAA", True), ("BBB", True)])

    def test_refresh_13f_retry_resumes_past_banks_done_this_run(self):
        # The Cloud Run retry after a 5400s timeout must not recrawl banks the
        # first attempt finished (2h-old file), but must force a 13h-old one.
        from jobs import refresh_13f
        # Files the job writes carry complete=True (every search succeeded).
        files = {"AAA.json": {"cached_at": _stamp(timedelta(hours=2)),
                              "holders": [{"filer_cik": "1"}], "complete": True},
                 "BBB.json": {"cached_at": _stamp(timedelta(hours=13)),
                              "holders": [{"filer_cik": "2"}], "complete": True}}
        seen = []

        def _fetch(t, name="", *a, **k):
            seen.append((t, k.get("force")))
            return [{"filer_cik": "9"}]

        with patch("data.bank_universe.get_universe_tickers",
                   return_value=["AAA", "BBB", "CCC"]), \
             patch("data.bank_mapping.get_name", return_value="A Bank"), \
             patch("data.form13f_client.fetch_institutional_holdings", _fetch), \
             patch("data.cloud_storage.load_json",
                   side_effect=lambda prefix, fname: files.get(fname)), \
             patch.object(refresh_13f.time, "sleep"):
            rc = refresh_13f.main()
        self.assertEqual(rc, 0)
        self.assertEqual(seen, [("BBB", True), ("CCC", True)])

    def test_refresh_insider_main_forces(self):
        from jobs import refresh_insider
        seen = []

        def _fetch(cik, *a, **k):
            seen.append((cik, k.get("force")))
            return []

        with patch("data.bank_universe.get_universe",
                   return_value={"AAA": {}, "BBB": {}}), \
             patch("config.DEFAULT_WATCHLIST", []), \
             patch("data.bank_mapping.get_cik",
                   side_effect=lambda t: {"AAA": 11, "BBB": 22}[t]), \
             patch("data.form4_client.fetch_insider_trades", _fetch), \
             patch("data.cloud_storage.load_json", return_value=None), \
             patch.object(refresh_insider.time, "sleep"):
            rc = refresh_insider.main()
        self.assertEqual(rc, 0)
        self.assertEqual(seen, [(11, True), (22, True)])

    def test_refresh_insider_rerun_resumes_past_ciks_done_today(self):
        from jobs import refresh_insider
        files = {"11.json": {"cached_at": _stamp(timedelta(hours=1)),
                             "transactions": []},
                 "22.json": {"cached_at": _stamp(timedelta(hours=23, minutes=50)),
                             "transactions": []}}
        seen = []

        def _fetch(cik, *a, **k):
            seen.append((cik, k.get("force")))
            return []

        with patch("data.bank_universe.get_universe",
                   return_value={"AAA": {}, "BBB": {}}), \
             patch("config.DEFAULT_WATCHLIST", []), \
             patch("data.bank_mapping.get_cik",
                   side_effect=lambda t: {"AAA": 11, "BBB": 22}[t]), \
             patch("data.form4_client.fetch_insider_trades", _fetch), \
             patch("data.cloud_storage.load_json",
                   side_effect=lambda prefix, fname: files.get(fname)), \
             patch.object(refresh_insider.time, "sleep"):
            rc = refresh_insider.main()
        self.assertEqual(rc, 0)
        # CIK 11 was swept an hour ago (skipped, still counted); CIK 22 is
        # yesterday's file — the 23h50m case the old 24h check skipped.
        self.assertEqual(seen, [(22, True)])


if __name__ == "__main__":
    unittest.main()
