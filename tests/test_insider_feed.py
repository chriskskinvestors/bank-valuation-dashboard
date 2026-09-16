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
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Order-independent streamlit stub (shared helper).
from tests import _streamlit_stub

_streamlit_stub.install()


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

    def test_empty_scan_keeps_last_good_aggregate(self):
        # A transient empty scan (per-CIK GCS reads briefly failing) must NOT
        # clobber a good aggregate — that blanked the feed's insider rows on
        # 2026-06-26. Keep last-known; report its count.
        from data import form4_client
        today = datetime.now().strftime("%Y-%m-%d")
        store: dict = {}
        good = {111: {"transactions": [_tx("P", today, "Buy")]}}

        with patch("data.cache.put", side_effect=lambda k, v: store.__setitem__(k, v)), \
             patch("data.cache.get", side_effect=lambda k: store.get(k)):
            with patch("data.form4_client.load_json",
                       side_effect=lambda p, n: good.get(int(n.split(".")[0]))):
                form4_client.build_open_market_universe_cache({"AAA": 111})
            # Now a run where the scan finds nothing.
            with patch("data.form4_client.load_json", return_value=None):
                n = form4_client.build_open_market_universe_cache({"AAA": 111})
            rows = form4_client.recent_open_market_universe()
        self.assertEqual(n, 1, "empty scan should report the retained count")
        self.assertEqual(len(rows), 1, "last-good aggregate must survive an empty scan")
        self.assertEqual(rows[0]["ticker"], "AAA")

    def test_empty_scan_seeds_empty_when_no_prior(self):
        # First-ever run that finds nothing: seed an empty row (so a real miss
        # still degrades to disclosures-only), not a crash.
        from data import form4_client
        store: dict = {}
        with patch("data.cache.put", side_effect=lambda k, v: store.__setitem__(k, v)), \
             patch("data.cache.get", side_effect=lambda k: store.get(k)), \
             patch("data.form4_client.load_json", return_value=None):
            n = form4_client.build_open_market_universe_cache({"AAA": 111})
            rows = form4_client.recent_open_market_universe()
        self.assertEqual(n, 0)
        self.assertEqual(rows, [])

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


class TestJointFilingDedupe(unittest.TestCase):
    """Co-owner Form 4 filings of the same economic transaction collapse to
    ONE row (SNL convention). Ground truth: AMAL's Workers United bloc files
    two Form 4s per group trade (EDGAR 0000902664-26-003825/-003826, filed
    2026-09-15) with identical date/code/shares/price/shares_after — each
    sale rendered twice in the feed and per-bank table until deduped."""

    W_STATES = "Western States Regional Joint Board, Workers United"
    W_UNITED = "Workers United"

    @staticmethod
    def _amal(insider, accession, date="2026-09-14"):
        # Real values from the 2026-09-14 AMAL group sale (EDGAR-verified).
        return {"form_type": "non-derivative", "date": date, "code": "S",
                "direction": "Sell", "shares": 82412.0, "price": 47.6873,
                "value_usd": 82412.0 * 47.6873, "shares_after": 6906314.93,
                "insider": insider, "role": "Insider", "accession": accession}

    def test_co_owner_filings_merge_to_one_row(self):
        from data.form4_client import dedupe_joint_filings
        a = self._amal(self.W_STATES, "0000902664-26-003826")
        b = self._amal(self.W_UNITED, "0000902664-26-003825")
        out = dedupe_joint_filings([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["insider"], f"{self.W_STATES} / {self.W_UNITED}")
        self.assertEqual(out[0]["insiders"], [self.W_STATES, self.W_UNITED])
        self.assertEqual(out[0]["role"], "Insider")   # identical roles collapse
        self.assertEqual(out[0]["shares"], 82412.0)   # value counted ONCE
        # First-seen filing's metadata survives.
        self.assertEqual(out[0]["accession"], "0000902664-26-003826")

    def test_same_filer_lot_sequence_never_merges(self):
        # IBCP's CEO legitimately sold ten 100-share lots in one day — ten
        # economic transactions (shares_after steps down 100 per lot).
        from data.form4_client import dedupe_joint_filings
        lots = [{"form_type": "non-derivative", "date": "2026-08-05",
                 "code": "S", "shares": 100.0, "price": 33.0,
                 "shares_after": 50000.0 - 100 * (i + 1),
                 "insider": "CEO Person", "role": "CEO"} for i in range(10)]
        out = dedupe_joint_filings(lots)
        self.assertEqual(len(out), 10)
        self.assertTrue(all("insiders" not in t for t in out))

    def test_identical_same_filer_rows_kept(self):
        # Even byte-identical rows from ONE filer stay separate — only a
        # DIFFERENT co-owner name marks a joint filing.
        from data.form4_client import dedupe_joint_filings
        a = self._amal(self.W_UNITED, "0000902664-26-003825")
        out = dedupe_joint_filings([a, dict(a)])
        self.assertEqual(len(out), 2)

    def test_different_shares_after_not_merged(self):
        # Two unrelated insiders, same size/price/day: their post-holdings
        # differ, so they are two economic transactions.
        from data.form4_client import dedupe_joint_filings
        a = self._amal("Insider A", "acc-1")
        b = self._amal("Insider B", "acc-2")
        b["shares_after"] = 12345.0
        self.assertEqual(len(dedupe_joint_filings([a, b])), 2)

    def test_co_owner_identical_lots_pair_lot_for_lot(self):
        # Two co-owners each report the same TWO identical lots → two merged
        # rows (two economic transactions), never one.
        from data.form4_client import dedupe_joint_filings
        rows = [self._amal(n, f"acc-{n}") for n in (self.W_STATES, self.W_UNITED)
                for _ in range(2)]
        out = dedupe_joint_filings(rows)
        self.assertEqual(len(out), 2)
        for t in out:
            self.assertEqual(t["insiders"], [self.W_STATES, self.W_UNITED])

    def test_feed_emits_one_row_for_joint_pair(self):
        # recent_open_market_transactions dedupes per bank before filtering.
        from data import form4_client
        today = datetime.now().strftime("%Y-%m-%d")
        cache = {111: {"transactions": [
            self._amal(self.W_STATES, "0000902664-26-003826", date=today),
            self._amal(self.W_UNITED, "0000902664-26-003825", date=today),
        ]}}
        with patch("data.form4_client.load_json",
                   side_effect=lambda p, n: cache.get(int(n.split(".")[0]))):
            out = form4_client.recent_open_market_transactions({"AMAL": 111})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["insider"], f"{self.W_STATES} / {self.W_UNITED}")

    def test_fresh_cache_read_is_deduped(self):
        # The per-bank ledger path (fetch_insider_trades on a fresh cache)
        # returns deduped rows; the cache object itself stays raw.
        from data import form4_client
        cache = {"cached_at": datetime.now().isoformat(), "transactions": [
            self._amal(self.W_STATES, "0000902664-26-003826"),
            self._amal(self.W_UNITED, "0000902664-26-003825"),
        ]}
        with patch("data.form4_client.load_json", return_value=cache):
            out = form4_client.fetch_insider_trades(999111)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(cache["transactions"]), 2)

    def test_roster_lists_each_co_owner(self):
        # People roster splits a merged row back into individual insiders.
        from data import people
        merged = self._amal(self.W_STATES, "0000902664-26-003826")
        merged["insider"] = f"{self.W_STATES} / {self.W_UNITED}"
        merged["insiders"] = [self.W_STATES, self.W_UNITED]
        with patch("data.form4_client.fetch_insider_trades",
                   return_value=[merged]):
            roster = people.get_insider_roster(999111)
        self.assertEqual({r["name"] for r in roster},
                         {self.W_STATES, self.W_UNITED})


if __name__ == "__main__":
    unittest.main()
