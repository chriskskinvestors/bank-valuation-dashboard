"""
One stored-series read per FRED series, shared by every `years` window
(2026-10-02).

fetch_series was memoised on (series_id, years) with the cache read inside,
and callers use ten different `years` values — a cold Market & Macro render
read 28 series 44 times, each a cloud-storage round trip on Cloud Run (prod
cold macro.render 9.8 s after the YoY fix). The read now lives in
_series_full(series_id); fetch_series only slices.

The house streamlit stub makes st.cache_data a pass-through, so memo sharing
is pinned by its invariants: the read function is keyed on series_id alone,
fetch_series never reads storage itself, and slicing is unchanged.
"""
import ast
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

import data.fred_client as fc  # noqa: E402


def _stored(days_back_list, value=1.0):
    now = datetime.now()
    return {"series_id": "TEST", "cached_at": now.isoformat(),
            "records": [{"date": (now - timedelta(days=d)).strftime("%Y-%m-%d"),
                         "value": value + i} for i, d in enumerate(days_back_list)]}


class TestFetchSeriesSlicesOneRead(unittest.TestCase):
    def test_years_window_slices_the_full_history(self):
        # Observations 10, 400, 800 and 2,000 days back.
        payload = _stored([10, 400, 800, 2000])
        with patch.object(fc, "load_json", return_value=payload):
            one = fc.fetch_series("TEST", years=1)
            three = fc.fetch_series("TEST", years=3)
            ten = fc.fetch_series("TEST", years=10)
        self.assertEqual(1, len(one))      # 10 d
        self.assertEqual(3, len(three))    # 10, 400, 800 d (≤ 1,095)
        self.assertEqual(4, len(ten))
        self.assertEqual([1.0], list(one["value"]))

    def test_stale_store_falls_back_to_live_fetch_and_persists(self):
        stale = _stored([10])
        stale["cached_at"] = (datetime.now() - timedelta(hours=2)).isoformat()
        import pandas as pd
        live = pd.DataFrame({"date": [pd.Timestamp.now().normalize()], "value": [9.0]})
        saved = {}
        with patch.object(fc, "load_json", return_value=stale), \
                patch.object(fc, "_fetch_csv", return_value=live), \
                patch.object(fc, "FRED_API_KEY", ""), \
                patch.object(fc, "save_json", lambda p, f, d: saved.setdefault(f, d)):
            got = fc.fetch_series("TEST", years=1)
        self.assertEqual([9.0], list(got["value"]))
        self.assertIn("TEST.json", saved)

    def test_io_stats_count_reads_and_distinct_series(self):
        fc.reset_io_stats()
        with patch.object(fc, "load_json", return_value=_stored([10])):
            fc._series_full("A")
            fc._series_full("B")
        stats = fc.io_stats()
        self.assertEqual(2, stats["reads"])
        self.assertEqual(2, stats["series"])


class TestReadKeyedOnSeriesOnly(unittest.TestCase):
    SRC = (Path(__file__).resolve().parent.parent / "data" / "fred_client.py"
           ).read_text(encoding="utf-8")

    def _fn(self, name):
        return next(n for n in ast.walk(ast.parse(self.SRC))
                    if isinstance(n, ast.FunctionDef) and n.name == name)

    def test_series_full_is_memoised_on_series_id_alone(self):
        fn = self._fn("_series_full")
        self.assertEqual(["series_id"], [a.arg for a in fn.args.args])
        self.assertIn("st.cache_data", ast.unparse(fn.decorator_list[0]))

    def test_fetch_series_does_not_read_storage_itself(self):
        body = ast.unparse(self._fn("fetch_series"))
        self.assertNotIn("load_json", body)
        self.assertIn("_series_full(series_id)", body)


if __name__ == "__main__":
    unittest.main()
