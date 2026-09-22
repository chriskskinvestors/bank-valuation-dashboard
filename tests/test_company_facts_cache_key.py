"""One owner for the companyfacts blob cache key — and the nightly job
actually force-refetches it.

The slim companyfacts blob is stored under sec_client's versioned key
(``sec_facts:{_SLIM_VER}:{cik}``). Until 2026-09-22 three other sites built
their own spelling: refresh-universe invalidated only ``sec:{ticker}`` (so
whether a bank's XBRL was re-downloaded nightly rode on the blob's 24h TTL
expiring on seconds of run-to-run jitter — the same coin flip as the gate
baseline), poll-events invalidated the pre-version ``sec_facts:{cik}`` on a
new 10-K/10-Q (never hit), and the Financial Highlights freshness caption
read the age of that same never-written key (never rendered).

Pins:
  1. company_facts_cache_key is the key fetch_company_facts serves from (no
     download when the blob is present);
  2. refresh_one drops that blob before fetching — the nightly force-refetch;
  3. poll-events' new-filing invalidation drops that blob;
  4. structural: the key literal exists only in sec_client.

All DB access runs on an isolated in-memory SQLite engine; no network.
Run: python -m unittest tests.test_company_facts_cache_key
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

import data.cache as cache  # noqa: E402
from data import sec_client  # noqa: E402
from tests.test_cache_stale_fallback import _IsolatedCache  # noqa: E402

CIK = 1423869
BLOB = {"cik": CIK, "entityName": "PCB BANCORP",
        "facts": {"us-gaap": {}, "dei": {}, "ecd": {}}}


def _no_network(*a, **k):
    raise AssertionError("companyfacts download must not run")


class TestKeyIsTheOneServed(_IsolatedCache):
    def test_fetch_serves_blob_under_helper_key(self):
        cache.put(sec_client.company_facts_cache_key(CIK), BLOB)
        with patch.object(sec_client, "_download_company_facts", _no_network):
            self.assertEqual(sec_client.fetch_company_facts(CIK), BLOB)

    def test_key_is_versioned_and_int_normalised(self):
        k = sec_client.company_facts_cache_key("1423869")
        self.assertEqual(k, f"sec_facts:{sec_client._SLIM_VER}:1423869")
        self.assertEqual(k, sec_client.company_facts_cache_key(CIK))


class TestNightlyForceRefetch(_IsolatedCache):
    def test_refresh_one_drops_the_blob(self):
        from jobs import refresh_universe as ru
        from data import bank_mapping
        key = sec_client.company_facts_cache_key(CIK)
        cache.put(key, BLOB)
        with patch.object(bank_mapping, "get_cik", return_value=CIK), \
                patch.object(bank_mapping, "get_fdic_cert", return_value=None), \
                patch.object(sec_client, "get_latest_fundamentals",
                             return_value={}) as glf:
            ru.refresh_one("PCB")
        glf.assert_called_once_with(CIK)
        self.assertIsNone(cache.get(key, max_age_s=None),
                          "the nightly refresh must drop the companyfacts blob")


class TestPollEventsInvalidation(_IsolatedCache):
    def test_new_periodic_filing_drops_the_blob(self):
        from jobs.poll_events import _invalidate_fundamentals_for_filings
        key = sec_client.company_facts_cache_key(CIK)
        cache.put(key, BLOB)
        ev = SimpleNamespace(ticker="PCB", raw={"form": "10-Q", "cik": str(CIK)})
        _invalidate_fundamentals_for_filings([ev])
        self.assertIsNone(cache.get(key, max_age_s=None))

    def test_non_periodic_filing_leaves_the_blob(self):
        from jobs.poll_events import _invalidate_fundamentals_for_filings
        key = sec_client.company_facts_cache_key(CIK)
        cache.put(key, BLOB)
        ev = SimpleNamespace(ticker="PCB", raw={"form": "8-K", "cik": str(CIK)})
        _invalidate_fundamentals_for_filings([ev])
        self.assertEqual(cache.get(key, max_age_s=None), BLOB)


class TestSingleOwner(unittest.TestCase):
    def test_key_literal_lives_only_in_sec_client(self):
        offenders = []
        for p in REPO.rglob("*.py"):
            rel = p.relative_to(REPO).as_posix()
            if rel.startswith(("tests/", ".claude/", "venv", ".venv")):
                continue
            if rel == "data/sec_client.py":
                continue
            if re.search(r"sec_facts:", p.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(rel)
        self.assertEqual(offenders, [],
                         "build the companyfacts key via sec_client.company_facts_cache_key")


if __name__ == "__main__":
    unittest.main()
