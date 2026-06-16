"""
Tests for live Treasury yields (data.live_rates).

Pins the render-safety contract:
  • live_yields() is READ-ONLY — it reads the persisted snapshot and NEVER
    builds (no yfinance at render); absent or stale (> max_age) → {} so the
    Rates pane falls back to FRED daily (no stale yield shown as live).
  • refresh() builds via _build() and persists {_ts, _v} (the job's job).

cache + _build mocked. Run:  python -m unittest tests.test_live_rates
"""
from __future__ import annotations
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

SAMPLE = {"10Y": [4.45, 4.43, 4.40], "2Y": [3.84, 3.82, 3.80]}


class TestLiveYields(unittest.TestCase):

    def test_refresh_builds_and_persists(self):
        from data import live_rates
        stored = {}
        with patch.object(live_rates, "_build", return_value=SAMPLE), \
             patch("data.cache.put", side_effect=lambda k, v: stored.update({k: v})), \
             patch("data.cache.get", return_value=None):
            out = live_rates.refresh()
        self.assertEqual(out, SAMPLE)
        self.assertIn(live_rates._SNAP_KEY, stored)
        self.assertEqual(stored[live_rates._SNAP_KEY]["_v"], SAMPLE)
        self.assertIn("_ts", stored[live_rates._SNAP_KEY])

    def test_read_fresh_snapshot(self):
        from data import live_rates
        snap = {"_ts": time.time(), "_v": SAMPLE}
        with patch("data.cache.get", return_value=snap):
            self.assertEqual(live_rates.live_yields(), SAMPLE)

    def test_absent_snapshot_returns_empty(self):
        from data import live_rates
        with patch("data.cache.get", return_value=None):
            self.assertEqual(live_rates.live_yields(), {})

    def test_stale_snapshot_returns_empty(self):
        """Older than max_age → {} so the pane uses FRED, not a stale 'live'."""
        from data import live_rates
        snap = {"_ts": time.time() - 9999, "_v": SAMPLE}
        with patch("data.cache.get", return_value=snap):
            self.assertEqual(live_rates.live_yields(max_age_s=600), {})

    def test_read_never_builds(self):
        """The render path must not call _build (no yfinance at render)."""
        from data import live_rates
        with patch("data.cache.get", return_value=None), \
             patch.object(live_rates, "_build",
                          side_effect=AssertionError("render must not build")):
            self.assertEqual(live_rates.live_yields(), {})


if __name__ == "__main__":
    unittest.main()
