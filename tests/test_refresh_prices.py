"""
Tests for the warm-price job's FMP Starter-plan quote-denial fallback
(jobs/refresh_prices.py + data/fmp_client.get_eod_close_batch).

Root cause pinned here: the Starter plan 403s the /quote endpoints, so
get_quote_batch returns all-empty quotes, upsert_prices skips every
null-price row, and the cache silently goes stale. The fix falls back to
the (plan-allowed) EOD chart endpoint and stamps rows with the close's
REAL trading date — never now() — so the Home staleness badge stays honest.

All HTTP is mocked; DB tests run on an isolated in-memory SQLite engine.

  • _get_eod_close: endpoint/params, latest-row selection, prev-close +
    change math, single-row and failure shapes
  • job quote path: unchanged happy case (no EOD call), NOW()-stamped
  • denial signature (<20% priced) and quote-batch raise both trigger the
    EOD fallback
  • EOD rows are stamped 20:00 UTC on the EOD date (clamped to now), and
    undated rows are never written
  • coverage math drives the tiered exit codes on both paths

Run:  python -m unittest tests.test_refresh_prices
"""
from __future__ import annotations
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Stub streamlit before importing data modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)


def _quote(price):
    return {"price": price, "close": price, "change": 0.0,
            "change_pct": 0.0, "volume": 1000}


def _eod(price, date, close=None):
    return {"price": price, "close": close, "date": date,
            "change": None, "change_pct": None, "volume": 500}


# ──────────────────────────────────────────────────────────────────────────
# fmp_client EOD close fetch (HTTP mocked)
# ──────────────────────────────────────────────────────────────────────────

class TestGetEodClose(unittest.TestCase):

    def setUp(self):
        self.env = patch.dict("os.environ", {"FMP_API_KEY": "test-key"})
        self.env.start()
        # Neutralize the Postgres/SQLite-backed TTL cache.
        self.cget = patch("data.fmp_client._cache_get", return_value=None)
        self.cput = patch("data.fmp_client._cache_put")
        self.cget.start()
        self.cput.start()

    def tearDown(self):
        self.env.stop()
        self.cget.stop()
        self.cput.stop()

    @staticmethod
    def _resp(payload):
        r = MagicMock()
        r.json.return_value = payload
        return r

    def test_latest_close_and_prev_close(self):
        """Newest-first FMP payload: price = latest close, close = prior
        session's close, date = latest trading date, change math correct."""
        from data import fmp_client
        payload = [
            {"date": "2026-06-11", "close": 52.0, "volume": 900},
            {"date": "2026-06-10", "close": 50.0, "volume": 800},
            {"date": "2026-06-09", "close": 49.0, "volume": 700},
        ]
        with patch("data.http.get_with_retry",
                   return_value=self._resp(payload)) as mock_get:
            out = fmp_client._get_eod_close("jpm")
        self.assertEqual(out["price"], 52.0)
        self.assertEqual(out["close"], 50.0)        # prev session
        self.assertEqual(out["date"], "2026-06-11")
        self.assertAlmostEqual(out["change"], 2.0)
        self.assertAlmostEqual(out["change_pct"], 4.0)  # 2/50*100
        self.assertEqual(out["volume"], 900)
        url = mock_get.call_args[0][0]
        params = mock_get.call_args[1]["params"]
        self.assertIn("historical-price-eod/full", url)
        self.assertNotIn("/quote", url)             # the denied endpoint
        self.assertEqual(params["symbol"], "JPM")
        self.assertIn("from", params)
        self.assertIn("to", params)

    def test_single_row_has_no_change(self):
        from data import fmp_client
        payload = [{"date": "2026-06-11", "close": 52.0, "volume": 900}]
        with patch("data.http.get_with_retry", return_value=self._resp(payload)):
            out = fmp_client._get_eod_close("JPM")
        self.assertEqual(out["price"], 52.0)
        self.assertIsNone(out["close"])
        self.assertIsNone(out["change"])
        self.assertIsNone(out["change_pct"])
        self.assertEqual(out["date"], "2026-06-11")

    def test_http_failure_returns_empty_shape(self):
        """A 403 surfaces as None from _get (logged) → all-None EOD dict."""
        import requests
        from data import fmp_client
        with patch("data.http.get_with_retry",
                   side_effect=requests.HTTPError("403 Forbidden")):
            out = fmp_client._get_eod_close("JPM")
        self.assertEqual(out, {"price": None, "close": None, "date": None,
                               "change": None, "change_pct": None,
                               "volume": None})

    def test_batch_fans_out_per_ticker(self):
        from data import fmp_client
        def fake(t):
            return _eod(10.0, "2026-06-11") if t == "AAA" else fmp_client._empty_eod()
        with patch("data.fmp_client._get_eod_close", side_effect=fake):
            out = fmp_client.get_eod_close_batch(["aaa", "bbb"], max_per_min=None)
        self.assertEqual(set(out), {"AAA", "BBB"})
        self.assertEqual(out["AAA"]["price"], 10.0)
        self.assertIsNone(out["BBB"]["price"])


# ──────────────────────────────────────────────────────────────────────────
# Job paths (DB = isolated in-memory SQLite; FMP batch calls mocked)
# ──────────────────────────────────────────────────────────────────────────

UNIVERSE = {t: {} for t in ("AAA", "BBB", "CCC", "DDD", "EEE",
                            "FFF", "GGG", "HHH", "III", "JJJ")}


class _JobDbCase(unittest.TestCase):
    """Shared in-memory-SQLite harness (mirrors tests/test_ri_store.py)."""

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
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        store._engine = None  # force re-init (schema create) on first use

    def tearDown(self):
        self._db._engine.dispose()
        self._db._engine = self._saved_db_engine
        self._store._engine = self._saved_store_engine

    def _rows(self):
        from sqlalchemy import text
        with self._db._engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT ticker, price, prev_close, updated_at "
                "FROM price_cache")).fetchall()
        return {r.ticker: r for r in rows}

    def _run_main(self, quote_result, eod_result):
        """Run jobs.refresh_prices.main with the FMP batches mocked.
        quote_result / eod_result: dict → returned, Exception → raised.
        Returns (exit_code, quote_mock, eod_mock)."""
        import jobs.refresh_prices as job

        def _effect(v):
            return {"side_effect": v} if isinstance(v, Exception) \
                else {"return_value": v}

        with patch("data.fmp_client.get_quote_batch", **_effect(quote_result)) as mq, \
             patch("data.fmp_client.get_eod_close_batch", **_effect(eod_result)) as me, \
             patch("data.fmp_client._has_key", return_value=True), \
             patch("data.bank_universe.get_universe", return_value=UNIVERSE), \
             patch("config.DEFAULT_WATCHLIST", []), \
             patch("config.MARKET_BENCHMARKS", []):
            code = job.main()
        return code, mq, me


class TestQuotePath(_JobDbCase):

    def test_happy_quote_path_unchanged(self):
        """Full quote coverage → exit 0, EOD endpoint never touched, rows
        carry the quote prices with a fresh (NOW) timestamp."""
        quotes = {t: _quote(10.0 + i) for i, t in enumerate(sorted(UNIVERSE))}
        code, _, me = self._run_main(quotes, {})
        self.assertEqual(code, 0)
        me.assert_not_called()
        rows = self._rows()
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows["AAA"].price, 10.0)
        # Quote path keeps the store's own NOW() stamp (fresh, ~today).
        stamp = str(rows["AAA"].updated_at)[:10]
        self.assertEqual(stamp, datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    def test_partial_quote_coverage_above_denial_threshold(self):
        """60% priced is throttling, not denial: stay on the quote path,
        exit 1 (partial)."""
        quotes = {t: (_quote(10.0) if i < 6 else _quote(None))
                  for i, t in enumerate(sorted(UNIVERSE))}
        code, _, me = self._run_main(quotes, {})
        self.assertEqual(code, 1)
        me.assert_not_called()
        self.assertEqual(len(self._rows()), 6)


class TestEodFallback(_JobDbCase):

    YDAY = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    def test_denial_signature_triggers_eod_fallback(self):
        """0% priced quotes (the Starter-plan 403 signature) → EOD batch is
        called and its closes land in the cache → exit 0."""
        empty_quotes = {t: _quote(None) for t in UNIVERSE}
        eod = {t: _eod(20.0 + i, self.YDAY, close=19.0 + i)
               for i, t in enumerate(sorted(UNIVERSE))}
        code, _, me = self._run_main(empty_quotes, eod)
        self.assertEqual(code, 0)
        me.assert_called_once()
        rows = self._rows()
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows["AAA"].price, 20.0)
        self.assertEqual(rows["AAA"].prev_close, 19.0)

    def test_quote_batch_raise_triggers_eod_fallback(self):
        eod = {t: _eod(20.0, self.YDAY) for t in UNIVERSE}
        code, _, me = self._run_main(RuntimeError("boom"), eod)
        self.assertEqual(code, 0)
        me.assert_called_once()
        self.assertEqual(len(self._rows()), 10)

    def test_eod_rows_stamped_with_eod_date_not_now(self):
        """The honesty pin: updated_at = 20:00 UTC on the EOD trading date,
        NOT the job's run time."""
        empty_quotes = {t: _quote(None) for t in UNIVERSE}
        eod = {t: _eod(20.0, self.YDAY) for t in UNIVERSE}
        self._run_main(empty_quotes, eod)
        for r in self._rows().values():
            self.assertEqual(str(r.updated_at)[:19], f"{self.YDAY} 20:00:00")

    def test_eod_rows_without_date_are_never_written(self):
        """No real date → no write (never stamp a guess); coverage suffers
        honestly (4/10 → exit 1)."""
        empty_quotes = {t: _quote(None) for t in UNIVERSE}
        eod = {t: (_eod(20.0, self.YDAY) if i < 4 else _eod(20.0, None))
               for i, t in enumerate(sorted(UNIVERSE))}
        code, _, _ = self._run_main(empty_quotes, eod)
        self.assertEqual(code, 1)
        self.assertEqual(len(self._rows()), 4)

    def test_eod_total_failure_exits_1(self):
        empty_quotes = {t: _quote(None) for t in UNIVERSE}
        eod = {t: _eod(None, None) for t in UNIVERSE}
        code, _, _ = self._run_main(empty_quotes, eod)
        self.assertEqual(code, 1)
        self.assertEqual(len(self._rows()), 0)


class TestEodTimestamp(unittest.TestCase):

    def test_past_date_stamped_at_2000_utc(self):
        from jobs.refresh_prices import _eod_timestamp
        now = datetime(2026, 6, 12, 15, 0, tzinfo=timezone.utc)
        ts = _eod_timestamp("2026-06-11", now)
        self.assertEqual(ts, datetime(2026, 6, 11, 20, 0, tzinfo=timezone.utc))

    def test_same_day_clamped_to_now_never_future(self):
        """A same-day EOD row seen before 20:00 UTC must not be stamped in
        the future — clamp to now."""
        from jobs.refresh_prices import _eod_timestamp
        now = datetime(2026, 6, 12, 15, 0, tzinfo=timezone.utc)
        ts = _eod_timestamp("2026-06-12", now)
        self.assertEqual(ts, now)

    def test_bad_date_raises_so_caller_drops_row(self):
        from jobs.refresh_prices import _eod_timestamp
        now = datetime.now(timezone.utc)
        with self.assertRaises((TypeError, ValueError)):
            _eod_timestamp(None, now)
        with self.assertRaises((TypeError, ValueError)):
            _eod_timestamp("garbage", now)


if __name__ == "__main__":
    unittest.main()
