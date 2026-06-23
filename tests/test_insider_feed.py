"""
Tests for the Home feed's universe-wide open-market insider aggregator
(data.form4_client.recent_open_market_transactions).

Pins:
  • Only NON-derivative, code P/S rows pass — grants (A), option exercises
    (M), tax (F), gifts (G) are excluded (the SNL "open-market" convention).
  • Date window filters out anything older than `days`; results are newest
    -first and capped at `limit`.
  • Reads cache only — a missing/None cache or a None CIK is skipped, never
    fetched live (no SEC calls here at all).

Run:  python -m unittest tests.test_insider_feed
"""
from __future__ import annotations
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)


def _tx(code, date, direction, form_type="non-derivative", insider="VP Smith"):
    return {"code": code, "date": date, "direction": direction,
            "form_type": form_type, "insider": insider, "role": "Officer",
            "shares": 100.0, "value_usd": 1000.0}


class TestRecentOpenMarket(unittest.TestCase):

    def _run(self, cache_by_cik, **kw):
        from data import form4_client
        def fake_load(prefix, name):
            cik = int(name.split(".")[0])
            return cache_by_cik.get(cik)
        with patch("data.form4_client.load_json", side_effect=fake_load):
            return form4_client.recent_open_market_transactions(kw.pop("ticker_ciks"), **kw)

    def test_only_open_market_ps_pass(self):
        today = datetime.now().strftime("%Y-%m-%d")
        cache = {111: {"transactions": [
            _tx("P", today, "Buy"),
            _tx("S", today, "Sell"),
            _tx("A", today, "Buy"),                 # grant — excluded
            _tx("M", today, "Exercise", "derivative"),  # exercise — excluded
            _tx("F", today, "Sell"),                # tax — excluded
        ]}}
        out = self._run(cache, ticker_ciks={"AAA": 111})
        self.assertEqual({r["code"] for r in out}, {"P", "S"})
        self.assertEqual(len(out), 2)

    def test_date_window_and_sort_and_limit(self):
        today = datetime.now()
        recent = today.strftime("%Y-%m-%d")
        older = (today - timedelta(days=5)).strftime("%Y-%m-%d")
        stale = (today - timedelta(days=90)).strftime("%Y-%m-%d")
        cache = {111: {"transactions": [
            _tx("P", older, "Buy"),
            _tx("S", recent, "Sell"),
            _tx("P", stale, "Buy"),                 # outside 30d window
        ]}}
        out = self._run(cache, ticker_ciks={"AAA": 111}, days=30, limit=10)
        self.assertEqual([r["date"] for r in out], [recent, older])  # newest first
        self.assertTrue(all(r["date"] != stale for r in out))

    def test_limit_caps_results(self):
        today = datetime.now().strftime("%Y-%m-%d")
        cache = {111: {"transactions": [_tx("P", today, "Buy") for _ in range(20)]}}
        out = self._run(cache, ticker_ciks={"AAA": 111}, limit=5)
        self.assertEqual(len(out), 5)

    def test_missing_cache_or_cik_skipped(self):
        today = datetime.now().strftime("%Y-%m-%d")
        cache = {111: {"transactions": [_tx("P", today, "Buy")]}}  # only 111 cached
        out = self._run(cache, ticker_ciks={"AAA": 111, "BBB": 222, "CCC": None})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ticker"], "AAA")


class TestUniverseAggregate(unittest.TestCase):
    """The pre-built aggregate (build_* writes one cache row; the feed reads it
    via recent_open_market_universe) — the fix that keeps the render thread off
    the per-CIK Form-4 fan-out."""

    def test_build_then_read_roundtrip(self):
        from data import form4_client
        today = datetime.now().strftime("%Y-%m-%d")
        cache_by_cik = {111: {"transactions": [_tx("P", today, "Buy")]}}
        store: dict = {}

        def fake_load(prefix, name):
            return cache_by_cik.get(int(name.split(".")[0]))

        with patch("data.form4_client.load_json", side_effect=fake_load), \
             patch("data.cache.put", side_effect=lambda k, v: store.__setitem__(k, v)), \
             patch("data.cache.get", side_effect=lambda k: store.get(k)):
            n = form4_client.build_open_market_universe_cache({"AAA": 111}, days=14)
            self.assertEqual(n, 1)
            rows = form4_client.recent_open_market_universe(limit=40)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "AAA")
        self.assertEqual(rows[0]["cik"], 111)

    def test_read_missing_returns_empty_not_fanout(self):
        # No aggregate built yet → [] (feed shows disclosures-only), never a
        # per-CIK scan. Pins the render-thread-safe contract.
        from data import form4_client
        with patch("data.cache.get", return_value=None):
            self.assertEqual(form4_client.recent_open_market_universe(), [])

    def test_warming_job_dedupes_by_cik(self):
        # The universe-span + dedup-by-CIK (multi-class names like BPOP/BPOPM
        # share one CIK) moved from the render path into the warming job. Pin
        # that the job feeds the builder a deduped, universe-wide CIK map.
        import jobs.refresh_home_snapshot as job
        captured = {}

        def fake_build(ciks, days=14, limit=60):
            captured["ciks"] = dict(ciks)
            return len(ciks)

        cikmap = {"NWBI": "100", "BPOP": "200", "BPOPM": "200", "WAL": "300"}
        with patch("data.bank_mapping.get_cik", side_effect=lambda t: cikmap.get(t)), \
             patch("data.form4_client.build_open_market_universe_cache",
                   side_effect=fake_build):
            job._warm_feed_insider_aggregate(["NWBI", "BPOP", "BPOPM", "WAL"])
        ciks = captured["ciks"]
        self.assertIn("NWBI", ciks)
        self.assertIn("WAL", ciks)
        self.assertNotIn("BPOPM", ciks)     # deduped — shares CIK 200 with BPOP
        self.assertEqual(ciks["BPOP"], "200")


if __name__ == "__main__":
    unittest.main()
