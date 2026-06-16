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


if __name__ == "__main__":
    unittest.main()
