"""EDGAR full-text search failures are retried and counted, never read as
"no holders" (2026-10-04 refresh-13f warm pass: bursts of 500s → 65/597 banks
with holders, reported as "0 errors", exit 0)."""
import unittest
from unittest import mock

import requests

from tests import _streamlit_stub

_streamlit_stub.install()

from data import form13f_client as f13  # noqa: E402
from data import http as H  # noqa: E402


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error")

    def json(self):
        return self._payload


def _seq(*resps):
    it = iter(resps)
    return lambda *a, **k: next(it)


class TestRetry5xx(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(H.time, "sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)

    def test_opt_in_retries_a_500_then_succeeds(self):
        with mock.patch.object(H.requests, "get", _seq(_Resp(500), _Resp(200, {"ok": 1}))):
            r = H.get_with_retry("u", retry_5xx=True)
        self.assertEqual(r.json(), {"ok": 1})

    def test_default_policy_unchanged_500_raises(self):
        with mock.patch.object(H.requests, "get", _seq(_Resp(500))):
            with self.assertRaises(requests.HTTPError):
                H.get_with_retry("u")

    def test_all_attempts_5xx_returns_none(self):
        with mock.patch.object(H.requests, "get", _seq(*[_Resp(503)] * 4)):
            self.assertIsNone(H.get_with_retry("u", max_attempts=4, retry_5xx=True))


class TestSearchFailureCounted(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(H.time, "sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)
        f13.SEARCH_FAILURES[0] = 0

    def test_outage_counts_a_failure_and_returns_no_candidates(self):
        with mock.patch.object(H.requests, "get", lambda *a, **k: _Resp(500)):
            self.assertEqual(f13._search_13f_for_ticker("Old National Bancorp"), [])
        self.assertEqual(f13.SEARCH_FAILURES[0], 1)

    def test_genuinely_empty_search_is_not_a_failure(self):
        empty = _Resp(200, {"hits": {"hits": []}})
        with mock.patch.object(H.requests, "get", lambda *a, **k: empty):
            self.assertEqual(f13._search_13f_for_ticker("Tiny Bancorp"), [])
        self.assertEqual(f13.SEARCH_FAILURES[0], 0)

    def test_transient_500_recovers(self):
        ok = _Resp(200, {"hits": {"hits": [{
            "_id": "0000000001-26-000001:x.xml",
            "_source": {"ciks": ["0000000001"], "display_names": ["A FUND  (CIK 0000000001)"],
                        "file_date": "2026-08-10", "period_ending": "2026-06-30",
                        "form": "13F-HR"}}]}})
        with mock.patch.object(H.requests, "get", _seq(_Resp(500), ok)):
            rows = f13._search_13f_for_ticker("Old National Bancorp")
        self.assertEqual([r["filer_name"] for r in rows], ["A FUND"])
        self.assertEqual(f13.SEARCH_FAILURES[0], 0)


def _failing_search(*a, **k):
    f13.SEARCH_FAILURES[0] += 1
    return []


class TestFailedSearchNeverOverwrites(unittest.TestCase):
    """2026-10-05: SEC 403s (fair-access block) made ~450 searches fail; each
    bank's snapshot was overwritten with holders=[] and the task retry resumed
    past them as "already done" — 115/597 with holders, exit 0."""

    GOOD = {"ticker": "ONB", "cached_at": "2026-09-30T00:00:00",
            "holders": [{"filer_cik": "1", "filer_name": "A FUND"}], "complete": True}

    def setUp(self):
        f13.SEARCH_FAILURES[0] = 0
        self.saved = []
        for target, val in (("save_json", lambda *a, **k: self.saved.append(a)),
                            ("load_json", lambda *a, **k: dict(self.GOOD)),
                            ("_search_13f_for_ticker", _failing_search)):
            p = mock.patch.object(f13, target, val)
            p.start()
            self.addCleanup(p.stop)

    def test_job_path_raises_and_keeps_the_snapshot(self):
        with self.assertRaises(RuntimeError):
            f13.fetch_institutional_holdings("ONB", "Old National Bancorp", force=True)
        self.assertEqual(self.saved, [])

    def test_page_path_returns_last_good_and_writes_nothing(self):
        got = f13.fetch_institutional_holdings("ONB", "Old National Bancorp")
        self.assertEqual([h["filer_name"] for h in got], ["A FUND"])
        self.assertEqual(self.saved, [])

    def test_successful_build_is_marked_complete(self):
        ok = mock.patch.object(f13, "_search_13f_for_ticker", lambda *a, **k: [])
        with ok:
            f13.fetch_institutional_holdings("ONB", "Old National Bancorp",
                                             force=True, with_changes=False)
        self.assertTrue(self.saved and self.saved[0][2]["complete"])

    def test_backfill_quarter_stores_nothing_on_a_failed_search(self):
        with mock.patch.object(f13, "load_json", lambda *a, **k: {}),                 mock.patch.object(f13, "_save_quarter_snapshots",
                                  lambda *a, **k: self.saved.append(a)):
            with self.assertRaises(RuntimeError):
                f13.backfill_quarter("ONB", "Old National Bancorp", "2026Q1")
        self.assertEqual(self.saved, [])


class TestWarmJobResumesOnlyCompleteSnapshots(unittest.TestCase):
    def _run(self, snapshot):
        from datetime import datetime
        import jobs.refresh_13f as job
        fetched = []
        snap = dict(snapshot, cached_at=datetime.now().isoformat())
        with mock.patch("data.bank_universe.get_universe_tickers", lambda: ["ONB"]),                 mock.patch("data.bank_mapping.get_name", lambda t: "Old National"),                 mock.patch("data.cloud_storage.load_json", lambda *a, **k: snap),                 mock.patch.object(f13, "fetch_institutional_holdings",
                                  lambda t, n, force=False: fetched.append(t) or [{"x": 1}]),                 mock.patch.object(job.time, "sleep", lambda s: None):
            f13.SEARCH_FAILURES[0] = 0
            job.main()
        return fetched

    def test_outage_era_empty_snapshot_is_refetched(self):
        self.assertEqual(self._run({"holders": []}), ["ONB"])

    def test_complete_snapshot_is_resumed(self):
        self.assertEqual(self._run({"holders": [], "complete": True}), [])


if __name__ == "__main__":
    unittest.main()
