"""
Tests for the Unusual Volume plumbing: average-volume storage + nightly job.

Pins:
  • price_cache_store.upsert_avg_volumes is UPDATE-only — never creates a
    row (so a volume-only refresh can't fabricate a price row / freshness)
    and never touches price/updated_at.
  • get_prices exposes avg_volume and derives rel_volume = volume / avg_volume
    (None when either is missing — never a divide-by-zero or a guess).
  • jobs.refresh_avg_volume._avg_volume = 63-day mean of the history volume
    column; main() writes computed averages and exits 0 at ≥80% coverage.

DB tests run on an isolated in-memory SQLite engine; FMP/history mocked.

Run:  python -m unittest tests.test_avg_volume
"""
from __future__ import annotations
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Stub streamlit before importing data modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)


class _DbCase(unittest.TestCase):
    """Isolated in-memory SQLite (mirrors tests/test_refresh_prices.py)."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        import data.db as db
        import data.price_cache_store as store

        self._db = db
        self._store = store
        self._saved_db_engine = db._engine
        self._saved_store_engine = store._engine
        db._engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool, future=True)
        store._engine = None
        store.init_price_cache_schema()

    def tearDown(self):
        self._db._engine.dispose()
        self._db._engine = self._saved_db_engine
        self._store._engine = self._saved_store_engine


class TestAvgVolumeStore(_DbCase):

    def test_update_only_never_creates_row(self):
        """A ticker with no price row is skipped (UPDATE-only) — relative
        volume needs a live volume anyway, so a volume-only row is useless
        and would falsely imply the ticker is priced."""
        store = self._store
        store.upsert_prices({"AAA": {"price": 10.0, "volume": 2000}})
        n = store.upsert_avg_volumes({"AAA": 1000.0, "ZZZ": 5000.0})
        self.assertEqual(n, 1)                       # only AAA existed
        got = store.get_prices(["AAA", "ZZZ"])
        self.assertIn("AAA", got)
        self.assertNotIn("ZZZ", got)                 # never fabricated

    def test_rel_volume_derived_on_read(self):
        store = self._store
        store.upsert_prices({"AAA": {"price": 10.0, "volume": 3000}})
        store.upsert_avg_volumes({"AAA": 1000.0})
        row = store.get_prices(["AAA"])["AAA"]
        self.assertEqual(row["avg_volume"], 1000.0)
        self.assertAlmostEqual(row["rel_volume"], 3.0)

    def test_rel_volume_none_without_avg(self):
        """No avg yet (job hasn't run) → rel_volume is None, never a guess."""
        store = self._store
        store.upsert_prices({"AAA": {"price": 10.0, "volume": 3000}})
        row = store.get_prices(["AAA"])["AAA"]
        self.assertIsNone(row["avg_volume"])
        self.assertIsNone(row["rel_volume"])

    def test_avg_volume_refresh_preserves_price_and_stamp(self):
        """The nightly avg-volume write must not touch price or updated_at."""
        from sqlalchemy import text
        store = self._store
        store.upsert_prices({"AAA": {"price": 10.0, "volume": 2000}})
        before = store.get_prices(["AAA"])["AAA"]["updated_at"]
        store.upsert_avg_volumes({"AAA": 1234.0})
        after = store.get_prices(["AAA"])["AAA"]
        self.assertEqual(after["price"], 10.0)
        self.assertEqual(after["updated_at"], before)   # stamp untouched


class TestAvgVolumeCompute(unittest.TestCase):

    def test_mean_of_last_63(self):
        import pandas as pd
        from jobs import refresh_avg_volume as job
        # 100 days: last 63 are all 1000 → mean 1000; earlier days differ.
        vols = [9999] * 37 + [1000] * 63
        df = pd.DataFrame({"volume": vols})
        with patch("data.fmp_client.get_history", return_value=df):
            self.assertAlmostEqual(job._avg_volume("AAA"), 1000.0)

    def test_empty_history_returns_none(self):
        import pandas as pd
        from jobs import refresh_avg_volume as job
        with patch("data.fmp_client.get_history", return_value=pd.DataFrame()):
            self.assertIsNone(job._avg_volume("AAA"))


class TestAvgVolumeJob(_DbCase):

    def test_main_writes_and_exits_zero(self):
        from jobs import refresh_avg_volume as job
        universe = {t: {} for t in ("AAA", "BBB", "CCC", "DDD", "EEE")}
        # Pre-create price rows so the UPDATE-only write lands.
        self._store.upsert_prices(
            {t: {"price": 10.0, "volume": 2000} for t in universe})
        with patch("data.fmp_client._has_key", return_value=True), \
             patch("jobs.refresh_avg_volume._avg_volume", return_value=1500.0), \
             patch("data.bank_universe.get_universe", return_value=universe), \
             patch("config.DEFAULT_WATCHLIST", []), \
             patch("config.MARKET_BENCHMARKS", []), \
             patch("time.sleep"):
            code = job.main()
        self.assertEqual(code, 0)
        self.assertEqual(self._store.get_prices(["AAA"])["AAA"]["avg_volume"], 1500.0)


if __name__ == "__main__":
    unittest.main()
