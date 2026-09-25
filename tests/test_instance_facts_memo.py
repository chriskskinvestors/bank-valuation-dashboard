"""
(REVIEW-2026-09-24 §1.4 / P1-2 b+c) In-process memos on the two SEC fetches
every Company Reported leaf repeated within ONE render:

  - instance_facts(meta): the ~7 MB filing was downloaded + iXBRL-parsed once
    PER EXTRACTOR for the same accession (~8x on a cold walk through the
    Company Reported basis; 2x for the holdco-capital walk alone, via
    _fye_month_for). Now an 8-entry LRU keyed by (cik, accession, doc, seam).
  - the submissions index behind latest_filing / _list_10k_filings /
    sec_statements._recent_*_metas: plain HTTP on every rerun (70% of a 3.4 s
    warm rerun). Now one fetch per CIK per _INDEX_TTL_S (15 min).

Pins:
  1. instance_facts twice for one accession -> one document fetch, the same
     list; a second accession -> a second fetch.
  2. a fetch that raises is NOT memoised (a transient 429 must never pin an
     empty fact list); the retry fetches.
  3. a rebound fetch seam (a job's rate-limit wrapper, a test's stub) is a
     memo miss — the previous seam's bytes are never served.
  4. latest_filing / _list_10k_filings / _recent_filing_metas /
     _recent_10q_metas within one TTL window -> ONE submissions fetch, every
     meta a fresh dict; the next window refetches.

No network: sec_filing_scraper._get is stubbed throughout.
"""
import json
import unittest
from unittest.mock import patch

import data.sec_filing_scraper as sfs
import data.sec_statements as ss
from tests.test_sec_filing_scraper import _IXBRL   # 4 facts, 2 contexts

META_A = {"cik": 4242, "accession": "000042424226000003", "doc": "k25.htm",
          "date": "2026-02-27", "form": "10-K"}
META_B = {**META_A, "accession": "000042424225000004", "doc": "k24.htm"}


def _doc_urls(calls):
    return [u for u in calls if u.endswith(".htm")]


def _doc_fetcher(calls):
    """A fetch seam serving the fixture for any .htm URL. The fixture has fewer
    facts than _MULTIDOC_FACT_THRESHOLD, so instance_facts also probes
    MetaLinks.json; that 404s here (-> single-document filing)."""
    def fake(url):
        calls.append(url)
        if url.endswith(".htm"):
            return _IXBRL
        raise RuntimeError(f"404 {url}")
    return fake


class _ClearMemos(unittest.TestCase):
    def setUp(self):
        for memo in (sfs._instance_facts_cached, sfs._recent_filings_cached):
            memo.cache_clear()
            self.addCleanup(memo.cache_clear)


class TestInstanceFactsMemo(_ClearMemos):

    def test_same_accession_fetched_and_parsed_once(self):
        calls = []
        with patch.object(sfs, "_get", _doc_fetcher(calls)):
            first = sfs.instance_facts(META_A)
            n_after_first = len(calls)
            second = sfs.instance_facts(META_A)
        self.assertEqual(len(_doc_urls(calls)), 1)
        self.assertEqual(len(calls), n_after_first)   # memo hit: no fetch at all
        self.assertIs(second, first)
        self.assertEqual(len(first), 4)
        self.assertEqual(first[0].concept, "us-gaap:CommonEquityTierOneCapital")

    def test_second_accession_is_a_second_fetch(self):
        calls = []
        with patch.object(sfs, "_get", _doc_fetcher(calls)):
            sfs.instance_facts(META_A)
            sfs.instance_facts(META_B)
            sfs.instance_facts(META_A)
            sfs.instance_facts(META_B)
        self.assertEqual(sorted(u.rsplit("/", 1)[1] for u in _doc_urls(calls)),
                         ["k24.htm", "k25.htm"])

    def test_fetch_exception_is_not_memoised(self):
        # Pin 2: first fetch raises (429 exhaustion); the retry under the SAME
        # seam must fetch again, not serve a memoised failure/empty list.
        calls = []
        state = {"fail": True}
        good = _doc_fetcher(calls)

        def flaky(url):
            if state["fail"]:
                state["fail"] = False
                calls.append(url)
                raise RuntimeError("SEC/EDGAR fetch exhausted by 429s")
            return good(url)

        with patch.object(sfs, "_get", flaky):
            with self.assertRaises(RuntimeError):
                sfs.instance_facts(META_A)
            facts = sfs.instance_facts(META_A)
        self.assertEqual(len(facts), 4)
        self.assertEqual(len(_doc_urls(calls)), 2)

    def test_rebound_fetch_seam_is_a_miss(self):
        # Pin 3: the seam is part of the key. TestFairValueCaching (which this
        # memo must not break) stubs _get per test for the SAME cik/accession.
        with patch.object(sfs, "_get", _doc_fetcher([])):
            self.assertEqual(len(sfs.instance_facts(META_A)), 4)
        with patch.object(sfs, "_get", lambda url: b"<html></html>"):
            self.assertEqual(sfs.instance_facts(META_A), [])


_SUBS = json.dumps({"filings": {"recent": {
    "form": ["10-Q", "8-K", "10-K", "10-Q", "10-K"],
    "accessionNumber": ["0000424242-26-000011", "0000424242-26-000008",
                        "0000424242-26-000003", "0000424242-25-000020",
                        "0000424242-25-000004"],
    "primaryDocument": ["q1.htm", "ex.htm", "k25.htm", "q3.htm", "k24.htm"],
    "filingDate": ["2026-05-01", "2026-04-20", "2026-02-27", "2025-11-05",
                   "2025-02-28"]}}})


class TestSubmissionsIndexMemo(_ClearMemos):

    def _index_fetcher(self, calls):
        def fake(url):
            calls.append(url)
            return _SUBS
        return fake

    def test_one_submissions_fetch_serves_every_lookup(self):
        calls = []
        with patch.object(sfs, "_index_bucket", return_value=1000), \
                patch.object(sfs, "_get", self._index_fetcher(calls)):
            k = sfs.latest_filing(4242, ("10-K",))
            newest = sfs.latest_filing(4242, ("10-Q", "10-K"))
            none = sfs.latest_filing(4242, ("S-1",))
            ks = sfs._list_10k_filings(4242, 5)
            qs = ss._recent_10q_metas(4242, 7)
            both = ss._recent_filing_metas(4242, ("10-K", "10-Q"), 3)
        self.assertEqual(calls,
                         ["https://data.sec.gov/submissions/CIK0000004242.json"])
        self.assertEqual(k, {"accession": "000042424226000003", "doc": "k25.htm",
                             "date": "2026-02-27", "form": "10-K", "cik": 4242})
        self.assertEqual(newest["accession"], "000042424226000011")
        self.assertIsNone(none)
        self.assertEqual([m["accession"] for m in ks],
                         ["000042424226000003", "000042424225000004"])
        self.assertEqual([m["doc"] for m in qs], ["q1.htm", "q3.htm"])
        self.assertEqual([m["form"] for m in both], ["10-Q", "10-K", "10-Q"])

    def test_metas_are_fresh_dicts(self):
        # sec_facts_overlay annotates the meta latest_filing hands it; a shared
        # memoised dict would leak that annotation into the next caller.
        with patch.object(sfs, "_index_bucket", return_value=1000), \
                patch.object(sfs, "_get", self._index_fetcher([])):
            a = sfs.latest_filing(4242, ("10-K",))
            a["report_date"] = "2025-12-31"
            b = sfs.latest_filing(4242, ("10-K",))
        self.assertIsNot(a, b)
        self.assertNotIn("report_date", b)

    def test_next_ttl_window_refetches(self):
        calls = []
        with patch.object(sfs, "_get", self._index_fetcher(calls)):
            with patch.object(sfs, "_index_bucket", return_value=1000):
                sfs.latest_filing(4242, ("10-K",))
                sfs.latest_filing(4242, ("10-K",))
            with patch.object(sfs, "_index_bucket", return_value=1001):
                sfs.latest_filing(4242, ("10-K",))
        self.assertEqual(len(calls), 2)

    def test_index_fetch_exception_is_not_memoised(self):
        state = {"fail": True}
        calls = []

        def flaky(url):
            calls.append(url)
            if state["fail"]:
                state["fail"] = False
                raise RuntimeError("SEC/EDGAR fetch exhausted by 429s")
            return _SUBS

        with patch.object(sfs, "_index_bucket", return_value=1000), \
                patch.object(sfs, "_get", flaky):
            with self.assertRaises(RuntimeError):
                sfs.latest_filing(4242, ("10-K",))
            self.assertEqual(sfs.latest_filing(4242, ("10-K",))["doc"], "k25.htm")
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
