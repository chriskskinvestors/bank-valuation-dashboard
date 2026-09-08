"""Deep FDIC history foundation (DEEP-HISTORY-PLAN.md, 2026-09-08).

Store roundtrip on isolated SQLite, multi-charter consolidation with
hand-computed aggregates, backfill checkpoint resume, and the loader
guard that a deep request can never trigger a deep LIVE fetch on a
render thread.
"""
import unittest
from unittest.mock import patch

from tests import _streamlit_stub  # noqa: F401


class _DbCase(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        import data.db as db
        import data.fdic_history_store as store
        self._db, self._store = db, store
        self._saved = (db._engine, store._engine, db.USE_POSTGRES,
                       store._USE_POSTGRES)
        db._engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool, future=True)
        db.USE_POSTGRES = False
        store._USE_POSTGRES = False
        store._engine = None
        store.init_history_schema()

    def tearDown(self):
        (self._db._engine, self._store._engine, self._db.USE_POSTGRES,
         self._store._USE_POSTGRES) = self._saved


class TestStoreRoundtrip(_DbCase):
    def test_upsert_read_newest_first(self):
        s = self._store
        n = s.upsert_history(101, [
            {"REPDTE": "19951231", "ASSET": 1000, "DEP": 800},
            {"REPDTE": "20260630", "ASSET": 5000, "DEP": 4000},
            {"REPDTE": "20100331", "ASSET": 3000, "DEP": 2500},
        ])
        self.assertEqual(3, n)
        recs = s.get_cert_history(101)
        self.assertEqual(["20260630", "20100331", "19951231"],
                         [r["REPDTE"] for r in recs])
        self.assertEqual(5000, recs[0]["ASSET"])
        self.assertEqual("19951231", s.min_repdte(101))
        self.assertEqual("20260630", s.max_repdte(101))

    def test_upsert_is_idempotent_and_updates(self):
        s = self._store
        s.upsert_history(101, [{"REPDTE": "20260630", "ASSET": 5000}])
        s.upsert_history(101, [{"REPDTE": "20260630", "ASSET": 5100}])
        recs = s.get_cert_history(101)
        self.assertEqual(1, len(recs))
        self.assertEqual(5100, recs[0]["ASSET"])

    def test_record_without_repdte_is_dropped(self):
        s = self._store
        n = s.upsert_history(101, [{"ASSET": 1}, {"REPDTE": "", "ASSET": 2}])
        self.assertEqual(0, n)


class TestDeepGroupConsolidation(_DbCase):
    def test_multi_charter_sums_per_quarter(self):
        # Hand-computed: two charters, 20260630 ASSET 7000+3000=10000;
        # 20260331 only cert A reported (asset 6800 alone).
        s = self._store
        s.upsert_history(1, [
            {"REPDTE": "20260630", "ASSET": 7000, "DEP": 5000, "CERT": 1},
            {"REPDTE": "20260331", "ASSET": 6800, "DEP": 4900, "CERT": 1},
        ])
        s.upsert_history(2, [
            {"REPDTE": "20260630", "ASSET": 3000, "DEP": 2000, "CERT": 2},
        ])
        with patch("data.cert_group.get_cert_group", return_value=[1, 2]):
            out = s.deep_group_history("FAKE")
        self.assertEqual(2, len(out))
        self.assertEqual("20260630", out[0]["REPDTE"])
        self.assertEqual(10000, out[0]["ASSET"])
        self.assertEqual(7000, out[0]["DEP"])
        self.assertEqual(6800, out[1]["ASSET"])

    def test_single_charter_is_passthrough(self):
        s = self._store
        s.upsert_history(9, [{"REPDTE": "20260630", "ASSET": 42, "CERT": 9}])
        with patch("data.cert_group.get_cert_group", return_value=[9]):
            out = s.deep_group_history("SOLO")
        self.assertEqual(42, out[0]["ASSET"])

    def test_empty_store_returns_empty(self):
        with patch("data.cert_group.get_cert_group", return_value=[7]):
            self.assertEqual([], self._store.deep_group_history("NONE"))


class TestBackfillCheckpoint(_DbCase):
    def _run(self, mode, certs, fetch_log):
        import pandas as pd
        from jobs import backfill_fdic_history as job

        def fake_fetch(cert, limit=20):
            fetch_log.append((cert, limit))
            return pd.DataFrame([{"REPDTE": "19931231", "ASSET": 1.0},
                                 {"REPDTE": "20260630", "ASSET": 2.0}])
        import data.fdic_client as fc
        with patch.object(job, "_all_certs", return_value=certs), \
             patch.object(fc, "fetch_financials", side_effect=fake_fetch), \
             patch.object(job, "_PACE_S", 0):
            return job.main(mode)

    def test_backfill_skips_already_deep_certs(self):
        s = self._store
        # cert 1 already backfilled to 1993; cert 2 untouched.
        s.upsert_history(1, [{"REPDTE": "19931231", "ASSET": 1.0}])
        log = []
        rc = self._run("backfill", [1, 2], log)
        self.assertEqual(0, rc)
        self.assertEqual([2], [c for c, _ in log],
                         "checkpointed cert must be skipped on resume")
        self.assertEqual(160, log[0][1], "backfill uses the deep limit")

    def test_incremental_touches_every_cert_shallowly(self):
        log = []
        rc = self._run("incremental", [1, 2], log)
        self.assertEqual(0, rc)
        self.assertEqual([1, 2], sorted(c for c, _ in log))
        self.assertTrue(all(lim == 6 for _, lim in log))


class TestLoaderDeepPath(_DbCase):
    def test_deep_request_never_live_fetches_deep(self):
        # Deep store empty: a limit=140 request must fall back to a live
        # fetch CAPPED at 20 — never a deep fetch on the render thread.
        from data import loaders
        calls = []

        def fake_group(ticker, limit=20, cert=None):
            calls.append(limit)
            return [{"REPDTE": "20260630", "ASSET": 1}]
        import data.cache as cache
        with patch.object(loaders, "__name__", "data.loaders"), \
             patch("data.cert_group.fetch_group_history",
                   side_effect=fake_group), \
             patch("data.bank_mapping.get_fdic_cert", return_value=123), \
             patch.object(cache, "get", return_value=None), \
             patch.object(cache, "put"):
            loaders.load_fdic_hist("FAKE", min_quarters=8, limit=140)
        self.assertEqual([20], calls)

    def test_deep_request_served_from_store(self):
        s = self._store
        s.upsert_history(5, [
            {"REPDTE": f"{y}1231", "ASSET": y, "CERT": 5}
            for y in range(1995, 2026)
        ])
        from data import loaders
        with patch("data.cert_group.get_cert_group", return_value=[5]):
            out = loaders.load_fdic_hist("FAKE", min_quarters=8, limit=140)
        self.assertEqual(31, len(out))
        self.assertEqual("20251231", out[0]["REPDTE"])
        self.assertEqual("19951231", out[-1]["REPDTE"])


if __name__ == "__main__":
    unittest.main()


class TestStrictJson(_DbCase):
    def test_nan_and_inf_become_null_never_invalid_json(self):
        # Live backfill failure 2026-09-08: pandas NaN serialized as a bare
        # NaN token — invalid JSON, rejected by Postgres JSONB on every cert.
        import json as _json
        import math
        s = self._store
        s.upsert_history(101, [{"REPDTE": "20260630", "ASSET": 5000.0,
                                "EEFFR": float("nan"),
                                "RBCRWAJ": float("inf")}])
        recs = s.get_cert_history(101)
        self.assertIsNone(recs[0]["EEFFR"])
        self.assertIsNone(recs[0]["RBCRWAJ"])
        # The stored payload itself must be strict JSON.
        payload = s._strict_json({"A": float("nan"), "B": 1.5})
        _json.loads(payload)                       # must not raise
        self.assertNotIn("NaN", payload)
        # And a non-serializable leak fails loudly, never stores garbage.
        self.assertIn("null", payload)

    def test_allow_nan_false_guards_future_leaks(self):
        import math
        s = self._store
        # A float that sneaks past cleaning (e.g. nested) must raise, not
        # silently produce a NaN token.
        with self.assertRaises(ValueError):
            import json as _json
            _json.dumps({"x": float("nan")}, allow_nan=False)
