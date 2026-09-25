"""
Accession-keyed extraction caches are served at ANY age (REVIEW-2026-09-24 P1-2a).

Every Company Reported / holdco-capital extraction is keyed by the filing's
accession (plus a spec version that is bumped on purpose), so its payload is
immutable. They were read through cache.get()'s default 24 h ceiling, so the
first view of each bank each day re-downloaded and re-parsed the ~7 MB filing on
the render thread (Capital Adequacy measured 17–18 s cold; Company Reported
Financial Highlights 21.7 s). tests/test_cache_read_ceilings.py fixed the same
class for earnings_8k / reported_tbvps / otc_release; this extends it to the
scraper families.

Pins:
  1. Structural: every family below reads with max_age_s=None (version-agnostic
     markers — a spec bump must not look like a regression).
  2. Behavioural, one per module: a payload aged 72 h is served and the
     download seam is never touched (it raises if called).

All DB access runs on an isolated in-memory SQLite engine; no network.
"""
import re
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

from sqlalchemy import create_engine, text  # noqa: E402

import data.cache as cache  # noqa: E402
import data.sec_filing_scraper as sfs  # noqa: E402
import data.sec_composition as scomp  # noqa: E402
import data.xbrl_dimensional as xd  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
_72H = 72 * 3600

META = {"cik": 4242, "accession": "000042424226000003", "doc": "k25.htm",
        "date": "2026-02-27", "form": "10-K"}


def _fresh_engine():
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE cache (key VARCHAR(255) PRIMARY KEY, "
            "value TEXT NOT NULL, timestamp DOUBLE PRECISION NOT NULL)"))
    return eng


def _no_network(*a, **k):
    raise AssertionError("download seam called — the cached payload should have been served")


class _IsolatedCache(unittest.TestCase):
    def setUp(self):
        self._eng = _fresh_engine()
        p = patch.object(cache, "_engine", self._eng)
        p.start()
        self.addCleanup(p.stop)
        for memo in (sfs._instance_facts_cached, sfs._recent_filings_cached):
            memo.cache_clear()
            self.addCleanup(memo.cache_clear)

    def _put_aged(self, key, value, seconds=_72H):
        cache.put(key, value)
        with self._eng.begin() as conn:
            conn.execute(text("UPDATE cache SET timestamp = :t WHERE key = :k"),
                         {"t": time.time() - seconds, "k": key})


class TestScraperPayloadsServedPast24h(_IsolatedCache):
    def test_holdco_capital(self):
        cap = {"2025-12-31": {"cet1_ratio": 12.5}}
        self._put_aged(f"holdco_cap:v3:{META['accession']}", cap)
        with patch.object(sfs, "_get", _no_network):
            self.assertEqual(sfs._holdco_capital_extract_cached(META, None), cap)

    def test_fye_month(self):
        self._put_aged(f"fyemonth:v1:{META['accession']}", "06")
        with patch.object(sfs, "_get", _no_network):
            self.assertEqual(sfs._fye_month_for(META), "06")

    def test_fair_value(self):
        fv = {"2025-12-31": {"level1": 1.0}}
        self._put_aged(f"fair_value:v2:{META['accession']}", fv)
        with patch.object(sfs, "_get", _no_network):
            self.assertEqual(sfs._fair_value_extract_cached(META), fv)

    def test_securities(self):
        sec = {"2025-12-31": {"afs": 2.0}}
        self._put_aged(f"securities:v1:{META['accession']}", sec)
        with patch.object(sfs, "_get", _no_network):
            self.assertEqual(sfs._securities_extract_cached(META), sec)

    def test_segments(self):
        seg = {"2025-12-31": {"Consumer": 3.0}}
        self._put_aged(f"segments:v1:{META['accession']}", seg)
        with patch.object(sfs, "_get", _no_network):
            self.assertEqual(sfs._segments_extract_cached(META), seg)


class TestCompositionPayloadServedPast24h(_IsolatedCache):
    def test_compositions_filing(self):
        comp = {"loans": [["CRE", 1.0, "us-gaap:CommercialRealEstateMember"]]}
        self._put_aged(f"compositions_filing:v2:{META['accession']}", comp)
        with patch.object(scomp, "_fetch_meta", _no_network):
            self.assertEqual(scomp._compositions_extract_cached(META), comp)


class TestDimensionalFactsServedPast24h(_IsolatedCache):
    def test_xbrl_dim_within_its_own_30d_policy(self):
        from datetime import datetime
        payload = {"cached_at": datetime.now().isoformat(),
                   "instance_url": "x", "accession": META["accession"], "facts": {}}
        # Row timestamp 72 h old; the payload's own cached_at is inside the 30 d
        # policy, so the module's is_fresh must decide — not the 24 h ceiling.
        self._put_aged(f"xbrl_dim:4242:{META['accession']}", payload)
        with patch.object(xd, "_locate_instance", _no_network):
            got = xd.fetch_dimensional_facts(4242, META["accession"])
        self.assertEqual(got["accession"], META["accession"])


class TestAccessionReadsWithoutCeiling(unittest.TestCase):
    """Structural: each accession-keyed family reads with max_age_s=None."""

    SITES = [
        ("data/sec_filing_scraper.py", r'ckey = f"fyemonth:v\d+:'),
        ("data/sec_filing_scraper.py", r'ckey = f"holdco_cap:v\d+:'),
        ("data/sec_filing_scraper.py", r'ckey = f"fair_value:v\d+:'),
        ("data/sec_filing_scraper.py", r'ckey = f"securities:v\d+:'),
        ("data/sec_filing_scraper.py", r'ckey = f"credit_quality:v\d+:'),
        ("data/sec_filing_scraper.py", r'ckey = f"asset_quality_nim:v\d+:'),
        ("data/sec_filing_scraper.py", r'ckey = f"performance:v\d+:'),
        ("data/sec_filing_scraper.py", r'ckey = f"highlights:v\d+:'),
        ("data/sec_filing_scraper.py", r'ckey = f"segments:v\d+:'),
        ("data/sec_filing_scraper.py", r'ckey = f"rate_risk:v\d+:'),
        ("data/sec_statements.py", r'ckey = f"asreported_mq:v\d+:'),
        ("data/sec_statements.py", r'ckey = f"asreported_my:v\d+:'),
        ("data/sec_composition.py", r'ckey = f"compositions_filing:v\d+:'),
        ("ui/financials_statements.py", r'ckey = f"compositions:v\d+:'),
        ("data/xbrl_dimensional.py", r'key = f"xbrl_dim:'),
        ("data/xbrl_dimensional.py", r'key = \(f"crit_hist:v\d+:'),
    ]

    def test_each_family_reads_without_ceiling(self):
        for rel, marker in self.SITES:
            src = (REPO / rel).read_text(encoding="utf-8")
            hits = [m.start() for m in re.finditer(marker, src)]
            self.assertTrue(hits, f"{rel}: key marker {marker!r} not found")
            for pos in hits:
                window = src[pos:pos + 400]
                read = re.search(r"cache\.get\((?:key|ckey)(?:, max_age_s=None)?\)", window)
                self.assertIsNotNone(read, f"{rel}: no cache read after {marker!r}")
                self.assertIn("max_age_s=None", read.group(0),
                              f"{rel}: {marker!r} is read through the 24 h ceiling "
                              f"again — immutable per-accession payload re-fetched daily")


if __name__ == "__main__":
    unittest.main()
