"""The process-level universe memo follows the persisted snapshot
(docs/REVIEW-2026-09-24-code.md P1-7).

Pre-fix, bank_universe._UNIVERSE_CACHE (and every memo derived from it:
_NONCOMMON_*, bank_mapping._SNAPSHOT_MAP, release_metrics._CIK_TICKER,
wire_base._NAME_INDEX) was built once per process and never invalidated, so a
Cloud Run instance that outlived one nightly refresh-universe run never saw
the banks it added — a bank added tonight stayed unresolvable until restart.

Pins: a newer snapshot (checked at most every _STAMP_CHECK_S, via the row's
write timestamp only) reloads the memo; an unchanged stamp never reloads; the
derived memos all resolve the new bank after the reload; a directly assigned
memo (the AppTest stub convention) is never replaced; get_universe_tickers
carries exactly one st.cache_data layer.
"""
from tests import _streamlit_stub
_streamlit_stub.install()

import ast
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import data.bank_universe as bu
import data.bank_mapping as bm
import data.release_metrics as rm
from data import cache
from data.events import wire_base as wb


def _bank(name, cik, cert, share_class=""):
    return {"name": name, "cik": cik, "fdic_cert": cert, "exchange": "NASDAQ",
            "share_class": share_class}


# Fake tickers (in neither BANK_MAP nor the resolved JSON) so every lookup
# resolves through the snapshot tier under test, never a curated file.
SNAP_A = {
    "QZXA": _bank("Quuxford Alpha Bancorp", 9990001, 99901, "common"),
    "QZXB": _bank("Quuxford Beta Bancshares", 9990002, 99902, "common"),
}
Z_CIK = 9990003
SNAP_B = {
    **SNAP_A,
    "QZZC": _bank("Zenithvale Quuxbridge Bancorp", Z_CIK, 99903, "common"),
    "QZZCP": _bank("Zenithvale Quuxbridge Bancorp", Z_CIK, 99903, "preferred"),
}
Z_HEADLINE = "Zenithvale Quuxbridge Bancorp reports record third quarter earnings"


class _SnapshotFixture(unittest.TestCase):
    """Drives the snapshot the loaders see (A, then B) plus the monotonic
    clock, with every network/DB seam stubbed."""

    def setUp(self):
        saved = {}
        for mod, names in (
                (bu, ("_UNIVERSE_CACHE", "_UNIVERSE_LOADED", "_UNIVERSE_LOADED_AT",
                      "_UNIVERSE_CHECKED_AT", "_UNIVERSE_GEN",
                      "_NONCOMMON_CACHE", "_NONCOMMON_GEN",
                      "_NONCOMMON_PRIMARY_CACHE", "_NONCOMMON_PRIMARY_GEN")),
                (bm, ("_SNAPSHOT_MAP", "_SNAPSHOT_MAP_GEN")),
                (rm, ("_CIK_TICKER", "_CIK_TICKER_GEN")),
                (wb, ("_NAME_INDEX", "_AMBIGUOUS_INDEX", "_NAME_LEADING_TOKENS",
                      "_NAME_INDEX_BUILT"))):
            for n in names:
                if hasattr(mod, n):
                    saved[(mod, n)] = getattr(mod, n)
        self.addCleanup(lambda: [setattr(m, n, v) for (m, n), v in saved.items()])

        bu._UNIVERSE_CACHE = None
        bu._NONCOMMON_CACHE = None
        bu._NONCOMMON_PRIMARY_CACHE = None
        bm._SNAPSHOT_MAP = None
        rm._CIK_TICKER = {}
        wb._NAME_INDEX, wb._AMBIGUOUS_INDEX, wb._NAME_LEADING_TOKENS = [], {}, None

        self.universe = SNAP_A
        self.written_at = time.time() - 3600   # snapshot A: written before load
        self.loads = 0
        self.stamp_reads = 0
        self.mono = 1_000_000.0

        def load_lastgood():
            self.loads += 1
            return dict(self.universe), True

        def cache_get(key, max_age_s=None):
            if key == "bank_universe_lastgood":
                return {"cached_at": "x", "universe": dict(self.universe)}
            return None

        def get_age(key):
            self.stamp_reads += 1
            return time.time() - self.written_at

        for p in (patch.object(bu, "_load_lastgood", load_lastgood),
                  patch.object(cache, "get", cache_get),
                  patch.object(cache, "get_age", get_age),
                  patch.object(bu.time, "monotonic", lambda: self.mono),
                  patch.object(bm, "resolve_ticker", lambda t: {})):
            p.start()
            self.addCleanup(p.stop)

    def publish_b(self):
        """The nightly job writes snapshot B. +1s: Windows' coarse clock can
        otherwise stamp the write in the same tick as the load."""
        self.universe = SNAP_B
        self.written_at = time.time() + 1

    def advance(self, seconds):
        self.mono += seconds


class TestUniverseMemoFollowsSnapshot(_SnapshotFixture):

    def test_newer_snapshot_reloads_after_the_throttle_window(self):
        self.assertEqual(set(bu.get_universe()), set(SNAP_A))
        self.assertEqual(self.loads, 1)

        self.publish_b()
        self.advance(60)                           # inside the window
        self.assertEqual(set(bu.get_universe()), set(SNAP_A))
        self.assertEqual(self.stamp_reads, 0,
                         "the stamp is read at most once per window, not per call")

        self.advance(bu._STAMP_CHECK_S)            # window elapsed
        self.assertEqual(set(bu.get_universe()), set(SNAP_B),
                         "a newer snapshot must reach a long-lived process")
        self.assertEqual(self.loads, 2)

    def test_unchanged_stamp_never_reloads(self):
        bu.get_universe()
        for _ in range(3):
            self.advance(bu._STAMP_CHECK_S + 1)
            bu.get_universe()
        self.assertEqual(self.loads, 1)
        self.assertEqual(self.stamp_reads, 3)

    def test_failed_reload_keeps_serving_the_memo(self):
        bu.get_universe()
        self.publish_b()
        self.advance(bu._STAMP_CHECK_S + 1)
        with patch.object(bu, "_load_lastgood", side_effect=RuntimeError("db")):
            self.assertEqual(set(bu.get_universe()), set(SNAP_A))

    def test_directly_assigned_memo_is_never_replaced(self):
        pinned = {"PINX": _bank("Pinned Test Bancorp", 1, 1)}
        bu._UNIVERSE_CACHE = pinned
        self.publish_b()
        self.advance(bu._STAMP_CHECK_S + 1)
        self.assertIs(bu.get_universe(), pinned)
        self.assertEqual(self.loads, 0)
        self.assertEqual(self.stamp_reads, 0)

    def test_derived_memos_resolve_the_new_bank_after_reload(self):
        # Build every derived memo against A.
        bu.get_universe()
        self.assertEqual(bu.get_noncommon_tickers(), set())
        self.assertEqual(bu.get_noncommon_primary_map(), {})
        self.assertNotIn("QZZC", bm._universe_snapshot_map())
        self.assertIsNone(rm._ticker_for_cik(Z_CIK))
        self.assertNotIn("QZZC", wb.match_tickers(Z_HEADLINE))

        self.publish_b()
        self.advance(bu._STAMP_CHECK_S + 1)
        self.assertIn("QZZC", bu.get_universe())

        self.assertEqual(bu.get_noncommon_tickers(), {"QZZCP"})
        self.assertEqual(bu.get_noncommon_primary_map(), {"QZZCP": "QZZC"})
        self.assertEqual(bm._universe_snapshot_map()["QZZC"]["cik"], Z_CIK)
        self.assertEqual(bm.get_cik("QZZC"), Z_CIK)
        self.assertEqual(rm._ticker_for_cik(Z_CIK), "QZZC")
        self.assertIn("QZZC", wb.match_tickers(Z_HEADLINE))

    def test_directly_assigned_name_index_survives_a_reload(self):
        # The wire_base suites assign _NAME_INDEX by hand; a universe reload
        # elsewhere in the process must not rebuild over it.
        bu.get_universe()
        wb.match_tickers("warmup")                   # builder-built index
        self.publish_b()
        self.advance(bu._STAMP_CHECK_S + 1)
        bu.get_universe()                            # generation bumps
        with patch.object(wb, "_NAME_INDEX", [("ZENITHVALE", "HANDX")]):
            self.assertEqual(wb.match_tickers("Zenithvale Holding expands"),
                             ["HANDX"])


class TestSnapshotStampHelper(unittest.TestCase):
    def test_written_at_is_the_row_timestamp_and_none_when_absent(self):
        with patch.object(cache, "get_age", lambda k: 30.0):
            self.assertAlmostEqual(bu._snapshot_written_at(), time.time() - 30,
                                   delta=1)
        with patch.object(cache, "get_age", lambda k: None):
            self.assertIsNone(bu._snapshot_written_at())
        with patch.object(cache, "get_age", side_effect=RuntimeError("db")):
            self.assertIsNone(bu._snapshot_written_at())


class TestGetUniverseTickersSingleCacheLayer(unittest.TestCase):
    def test_exactly_one_cache_data_decorator(self):
        # Pre-fix: @st.cache_data(ttl=86400) stacked over ttl=3600 — the outer
        # 24h memo made the inner 1h one dead.
        src = Path(bu.__file__).read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "get_universe_tickers")
        layers = [d for d in fn.decorator_list if "cache_data" in ast.unparse(d)]
        self.assertEqual(len(layers), 1, [ast.unparse(d) for d in layers])


if __name__ == "__main__":
    unittest.main()
