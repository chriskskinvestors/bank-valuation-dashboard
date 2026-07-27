"""
(AUDIT-2026-07-27, refresh-home-snapshot timeout incident) Modules that manage
their own cache freshness must read with max_age_s=None — cache.get()'s default
24h ceiling silently voided their by-design longer-lived entries:

  - data/sec_earnings_8k.py: accession-keyed extraction caches are immutable,
    yet expired daily → every metrics build re-fetched EX-99.1s for the whole
    universe; and _latest_earnings_8k did an UNCACHED submissions fetch per
    bank per build (~440+ live EDGAR calls each 30-min run). Combined, the
    build blew the job's 1800s task timeout every daytime run from 2026-07-26.
  - data/otc_release.py: a >24h gap dropped `prev`, forcing a full re-crawl
    + re-extraction per OTC bank.
  - M&A layer (ma_history/ma_summary/offerings/stake_filings/ma_announcements)
    and fdic_client RSSD lookups: 7-90d design TTLs died at 24h → the nightly
    deal-comps walk repeated its full EDGAR/EFTS load daily (AUDIT P2 #4).

Pins:
  1. _latest_earnings_8k caches its result (incl. the no-8-K case) and does
     not re-hit the submissions endpoint within the TTL; past the TTL it does.
  2. latest_earnings_8k_figures / reported_tbvps serve their accession-keyed
     payloads at ANY age without re-fetching.
  3. otc_release_metrics serves `prev` past 24h (same-URL re-stamp, no fetch).
  4. Structural: every listed call site reads with max_age_s=None.

All DB access runs on an isolated in-memory SQLite engine; no network.
"""
import re
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

from sqlalchemy import create_engine, text  # noqa: E402

import data.cache as cache  # noqa: E402
import data.sec_earnings_8k as se8k  # noqa: E402
import data.otc_release as otcr  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def _fresh_engine():
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE cache (key VARCHAR(255) PRIMARY KEY, "
            "value TEXT NOT NULL, timestamp DOUBLE PRECISION NOT NULL)"))
    return eng


class _IsolatedCache(unittest.TestCase):
    """Route data.cache at a private in-memory engine for the test's duration."""

    def setUp(self):
        self._eng = _fresh_engine()
        p = patch.object(cache, "_engine", self._eng)
        p.start()
        self.addCleanup(p.stop)

    def _age(self, key: str, seconds: float):
        with self._eng.begin() as conn:
            conn.execute(text("UPDATE cache SET timestamp = :t WHERE key = :k"),
                         {"t": time.time() - seconds, "k": key})


_SUBS_JSON = (
    '{"filings": {"recent": {'
    '"form": ["10-Q", "8-K", "8-K"], '
    '"items": ["", "7.01", "2.02,9.01"], '
    '"accessionNumber": ["a-1", "b-2", "0001234567-26-000042"], '
    '"filingDate": ["2026-07-01", "2026-07-10", "2026-07-21"]}}}'
)


class TestLatest8kResultCached(_IsolatedCache):
    """Pin 1: the per-bank submissions lookup is cached, incl. the no-8-K case."""

    def test_second_call_hits_cache_not_edgar(self):
        calls = []

        def fake_get(url):
            calls.append(url)
            return _SUBS_JSON

        with patch.object(se8k, "_get", side_effect=fake_get):
            first = se8k._latest_earnings_8k(1234567)
            second = se8k._latest_earnings_8k(1234567)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["accession_dash"], "0001234567-26-000042")
        self.assertEqual(first["accession"], "000123456726000042")
        self.assertEqual(second, first)

    def test_no_8k_result_is_negative_cached(self):
        calls = []

        def fake_get(url):
            calls.append(url)
            return '{"filings": {"recent": {"form": ["10-Q"], "items": [""], ' \
                   '"accessionNumber": ["a-1"], "filingDate": ["2026-07-01"]}}}'

        with patch.object(se8k, "_get", side_effect=fake_get):
            self.assertIsNone(se8k._latest_earnings_8k(55))
            self.assertIsNone(se8k._latest_earnings_8k(55))
        self.assertEqual(len(calls), 1)

    def test_past_ttl_refetches(self):
        calls = []

        def fake_get(url):
            calls.append(url)
            return _SUBS_JSON

        with patch.object(se8k, "_get", side_effect=fake_get):
            se8k._latest_earnings_8k(1234567)
            self._age("earnings_8k_latest:v1:1234567",
                      se8k._LATEST_8K_TTL_S + 60)
            se8k._latest_earnings_8k(1234567)
        self.assertEqual(len(calls), 2)


_F8K = {"accession_dash": "0001234567-26-000042",
        "accession": "000123456726000042", "date": "2026-07-21", "cik": 77}


def _boom(*a, **k):
    raise AssertionError("network path must not run — cache should serve")


class TestImmutableExtractionCachesAnyAge(_IsolatedCache):
    """Pin 2: accession-keyed payloads serve at ANY age, no re-fetch."""

    def test_figures_payload_served_past_24h(self):
        payload = {"figures": {"diluted_eps": 1.25}, "_preliminary": True,
                   "accession": _F8K["accession_dash"], "filed": _F8K["date"],
                   "period": "2026-03-31", "doc": "ex991.htm"}
        cache.put(f"earnings_8k:v1:{_F8K['accession']}", payload)
        self._age(f"earnings_8k:v1:{_F8K['accession']}", 72 * 3600)
        with patch.object(se8k, "_latest_earnings_8k", return_value=_F8K), \
                patch.object(se8k, "_ex991_document", _boom), \
                patch.object(se8k, "_get", _boom):
            out = se8k.latest_earnings_8k_figures(77)
        self.assertEqual(out["figures"]["diluted_eps"], 1.25)

    def test_reported_tbvps_served_past_24h(self):
        key = f"reported_tbvps:v2:{_F8K['accession']}:12.3456:15.0000"
        cache.put(key, {"value": 10.1})
        self._age(key, 72 * 3600)
        with patch.object(se8k, "_latest_earnings_8k", return_value=_F8K), \
                patch.object(se8k, "_fetch_ex991_html", _boom):
            out = se8k.reported_tbvps(77, reconstructed=12.3456, bvps=15.0)
        self.assertEqual(out, 10.1)


class TestOtcPrevSurvivesCeiling(_IsolatedCache):
    """Pin 3: a >24h-old OTC extraction still serves via the same-URL re-stamp."""

    def test_stale_prev_restamped_not_recrawled(self):
        stamped_at = (datetime.now() - timedelta(days=3)).isoformat()
        prev = {"url": "https://wire.example/q2", "eps": 0.52,
                "source": "company_release"}
        cache.put("otc_release:v6:TBNK", {"cached_at": stamped_at, "value": prev})
        self._age("otc_release:v6:TBNK", 72 * 3600)
        with patch.object(otcr, "_latest_earnings_pr",
                          return_value={"url": "https://wire.example/q2",
                                        "title": "T", "published_at": "2026-07-20"}), \
                patch.object(otcr, "_fetch_story", _boom):
            out = otcr.otc_release_metrics("tbnk")
        self.assertEqual(out["eps"], 0.52)


class TestCallSitesReadWithoutCeiling(unittest.TestCase):
    """Pin 4 (structural): each incident call site passes max_age_s=None so a
    drive-by revert to a bare cache.get(...) fails loudly here."""

    SITES = [
        ("data/sec_earnings_8k.py", r'ckey = f"earnings_8k:v1:'),
        ("data/sec_earnings_8k.py", r'ckey = f"reported_tbvps:v2:'),
        ("data/otc_release.py", r'key = f"otc_release:v6:'),
        ("data/release_metrics.py", r'key = f"release_metrics:v16:'),
        ("data/ma_history.py", r'key = f"ma_history:v9:'),
        ("data/ma_summary.py", r'key = f"ma_summary:v1:'),
        ("data/offerings.py", r'key = f"offerings:v1:'),
        ("data/stake_filings.py", r'key = f"stake_filings:v1:'),
        ("data/fdic_client.py", r'key = f"fdic_active:'),
        ("data/fdic_client.py", r'key = f"fdic_rssd:'),
        ("data/fdic_client.py", r'key = f"fdic_rssdhcr:'),
        ("data/ma_announcements.py", r'key = _doc_404_key\(cik, adsh, doc\)'),
    ]

    def test_each_site_passes_max_age_none(self):
        for rel, key_marker in self.SITES:
            src = (REPO / rel).read_text(encoding="utf-8")
            m = re.search(key_marker + r'.*?\n(?:.*\n){0,4}?'
                          r'.*cache\.get\((?:key|ckey), max_age_s=None\)', src)
            self.assertIsNotNone(
                m, f"{rel}: cache read for '{key_marker}' no longer passes "
                   f"max_age_s=None — the 24h ceiling is back (see module "
                   f"docstring for why that re-opens the timeout incident)")


if __name__ == "__main__":
    unittest.main()
