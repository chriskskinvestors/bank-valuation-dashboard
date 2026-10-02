"""
FRED series bundle: refresh-macro writes every series as ONE row; renders read
it once instead of one cloud-storage read per series (2026-10-02 — prod cold
Market & Macro render: 5,301 ms of 6,387 ms was 28 per-series reads).

Pins:
  1. A fresh bundle serves the series with NO per-series read.
  2. A stale bundle (> CACHE_TTL_SECONDS) or a series missing from it falls
     back to the per-series file exactly as before.
  3. The bundle is read from the store ONCE per process for many series
     (module memo — st.cache_data would deep-copy ~6 MB per call).
  4. The job path (use_bundle=False) never reads the bundle — otherwise the
     job would rebuild the bundle from itself and the data would never refresh.
  5. write_series_bundle round-trips values (None stays missing).
  6. jobs/refresh_macro warms with use_bundle=False and writes the bundle.

Isolated in-memory SQLite store; no network.
"""
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

import pandas as pd  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

import data.cache as cache  # noqa: E402
import data.fred_client as fc  # noqa: E402


def _engine():
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE cache (key VARCHAR(255) PRIMARY KEY, "
                          "value TEXT NOT NULL, timestamp DOUBLE PRECISION NOT NULL)"))
    return eng


def _bundle(series, age_s=60):
    return {"cached_at": (datetime.now() - timedelta(seconds=age_s)).isoformat(),
            "series": series}


A = {"d": ["2025-01-31", "2025-02-28", "2025-03-31"], "v": [1.5, None, 2.5]}


def _file(series_id):
    return {"series_id": series_id, "cached_at": datetime.now().isoformat(),
            "records": [{"date": "2025-01-31", "value": 9.0}]}


class _Store(unittest.TestCase):
    def setUp(self):
        p = patch.object(cache, "_engine", _engine())
        p.start()
        self.addCleanup(p.stop)
        fc._BUNDLE_MEMO.update(loaded_at=0.0, series=None)
        self.addCleanup(fc._BUNDLE_MEMO.update, loaded_at=0.0, series=None)
        self.file_reads = []
        lp = patch.object(fc, "load_json",
                          lambda prefix, fname: self.file_reads.append(fname) or _file(fname[:-5]))
        lp.start()
        self.addCleanup(lp.stop)


class TestRenderReadsBundle(_Store):
    def test_fresh_bundle_serves_without_file_read(self):
        cache.put(fc._BUNDLE_KEY, _bundle({"AAA": A}))
        df = fc._series_full("AAA")
        self.assertEqual([], self.file_reads)
        self.assertEqual(list(pd.to_datetime(A["d"])), list(df["date"]))
        self.assertEqual(1.5, df["value"].iloc[0])
        self.assertTrue(pd.isna(df["value"].iloc[1]))      # None stays missing
        self.assertEqual(2.5, df["value"].iloc[2])

    def test_stale_bundle_falls_back_to_file(self):
        cache.put(fc._BUNDLE_KEY, _bundle({"AAA": A}, age_s=fc.CACHE_TTL_SECONDS + 60))
        df = fc._series_full("AAA")
        self.assertEqual(["AAA.json"], self.file_reads)
        self.assertEqual([9.0], list(df["value"]))

    def test_series_missing_from_bundle_falls_back_to_file(self):
        cache.put(fc._BUNDLE_KEY, _bundle({"AAA": A}))
        fc._series_full("BBB")
        self.assertEqual(["BBB.json"], self.file_reads)

    def test_bundle_read_once_for_many_series(self):
        cache.put(fc._BUNDLE_KEY, _bundle({"AAA": A, "BBB": A, "CCC": A}))
        calls = []
        real_get = cache.get
        with patch.object(cache, "get", lambda k, **kw: calls.append(k) or real_get(k, **kw)):
            for sid in ("AAA", "BBB", "CCC"):
                fc._series_full(sid)
        self.assertEqual([fc._BUNDLE_KEY], calls)

    def test_fetch_series_slices_bundle_data(self):
        now = datetime.now()
        d = [(now - timedelta(days=n)).strftime("%Y-%m-%d") for n in (900, 30)]
        cache.put(fc._BUNDLE_KEY, _bundle({"AAA": {"d": d, "v": [1.0, 2.0]}}))
        self.assertEqual([2.0], list(fc.fetch_series("AAA", years=1)["value"]))
        self.assertEqual([], self.file_reads)


class TestJobBypassesBundle(_Store):
    def test_use_bundle_false_reads_the_file(self):
        cache.put(fc._BUNDLE_KEY, _bundle({"AAA": A}))
        df = fc._series_full("AAA", use_bundle=False)
        self.assertEqual(["AAA.json"], self.file_reads)
        self.assertEqual([9.0], list(df["value"]))

    def test_write_series_bundle_round_trip(self):
        n = fc.write_series_bundle(["AAA", "BBB"])
        self.assertEqual(2, n)
        snap = cache.get(fc._BUNDLE_KEY, max_age_s=None)
        self.assertEqual({"d": ["2025-01-31"], "v": [9.0]}, snap["series"]["AAA"])
        age = (datetime.now() - datetime.fromisoformat(snap["cached_at"])).total_seconds()
        self.assertLess(age, 60)

    def test_refresh_macro_job_warms_without_bundle_and_writes_it(self):
        from jobs import refresh_macro
        seen = []

        def fake_full(sid, use_bundle=True):
            seen.append((sid, use_bundle))
            return pd.DataFrame({"date": [pd.Timestamp("2025-01-31")], "value": [1.0]})

        with patch.object(refresh_macro, "_series_to_warm", return_value=["AAA", "BBB"]), \
                patch.object(fc, "_series_full", fake_full), \
                patch("data.fred_client.get_macro_snapshot", return_value={}), \
                patch("data.fred_client.recession_probability", return_value={}), \
                patch("data.econ_calendar.get_us_calendar", return_value=[]), \
                patch("data.macro_calendar.get_upcoming_prints", return_value=[]):
            refresh_macro.main()
        self.assertTrue(seen)
        self.assertTrue(all(ub is False for _sid, ub in seen), seen)
        snap = cache.get(fc._BUNDLE_KEY, max_age_s=None)
        self.assertEqual({"AAA", "BBB"}, set(snap["series"]))


if __name__ == "__main__":
    unittest.main()
