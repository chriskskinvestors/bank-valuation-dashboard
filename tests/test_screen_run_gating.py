"""Screen "build, then run" — behavioral guard via Streamlit AppTest.

Pins the owner's core requirement (2026-09-24): editing the builder changes
NOTHING on screen until **Run screen** is pressed — the old toolbar re-ran the
full-universe evaluation on every dropdown change. Also pins the saved-screen
loop end to end: Save from results → launcher lists it → opening it loads the
draft AND runs it, producing the same result set.

Hermetic: a 2-bank universe is injected into the module-level universe cache
(the same technique as tests/test_nav_renders.py), the metrics snapshot is
served from a stub so no SEC/FDIC/price fetch happens, and saved screens live
in an in-memory dict (nothing is written under saved_screens/).

AppTest suites skip under `unittest discover` by design (the package-level
streamlit stub replaces the runtime). Run as a script:
    python tests/test_screen_run_gating.py
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

_STUB_UNIVERSE = {
    "AMAL": {"cik": 1823608, "fdic_cert": 622, "share_class": "common",
             "bank_name": "Amalgamated Financial Corp."},
    "JPM": {"cik": 19617, "fdic_cert": 628, "share_class": "common",
            "bank_name": "JPMorgan Chase & Co."},
}

_STUB_METRICS = [
    {"ticker": "AMAL", "ptbv_ratio": 0.9, "cet1_ratio": 12.0, "roatce": 14.0,
     "market_cap": 900_000_000, "price": 30.0, "total_assets": 8_000_000_000},
    {"ticker": "JPM", "ptbv_ratio": 2.5, "cet1_ratio": 15.0, "roatce": 20.0,
     "market_cap": 600_000_000_000, "price": 200.0, "total_assets": 4_000_000_000_000},
]


def _filter_index(label: str) -> int:
    """Index of `label` in the builder's Metric dropdown (mirrors app.py)."""
    from config import METRICS
    fmts = ("pct", "ratio", "currency", "number", "millions", "billions", "dollars_auto")
    fk = sorted([(m["key"], m["label"]) for m in METRICS if m.get("format") in fmts],
                key=lambda x: x[1])
    return 1 + [lbl for _, lbl in fk].index(label)


class _MemScreens:
    """In-memory stand-in for data.saved_screens."""
    def __init__(self):
        self.store: dict[str, dict] = {}

    def save_screen(self, name, cfg):
        prev = self.store.get(name)
        self.store[name] = {"name": name, "config": cfg,
                            "version": (prev["version"] + 1) if prev else 1,
                            "saved_at": datetime.now().isoformat()}
        return True

    def load_screen(self, name, version=None):
        d = self.store.get(name)
        return (d or {}).get("config")

    def list_screens(self):
        return [{"name": n, "saved_at": d["saved_at"], "version": d["version"],
                 "tab": d["config"].get("tab_key"),
                 "filter_count": len(d["config"].get("filters", [])),
                 "filename": f"{n.replace(' ', '_')}.json"}
                for n, d in self.store.items()]

    def screen_versions(self, name):
        d = self.store.get(name)
        return [{"version": d["version"], "saved_at": d["saved_at"], "current": True}] if d else []

    def delete_screen(self, name):
        self.store.pop(name, None)
        return True


class TestScreenRunGating(unittest.TestCase):
    def setUp(self):
        try:
            from streamlit.testing.v1 import AppTest  # noqa: F401
        except Exception as e:  # stubbed / very old streamlit — skip, not fail
            self.skipTest(f"AppTest unavailable: {e}")
        import data.bank_universe as bu
        from data import cache
        self._bu = bu
        self._saved = (bu._UNIVERSE_CACHE, bu._NONCOMMON_CACHE, bu._NONCOMMON_PRIMARY_CACHE)
        bu._UNIVERSE_CACHE = dict(_STUB_UNIVERSE)
        bu._NONCOMMON_CACHE = None
        bu._NONCOMMON_PRIMARY_CACHE = None

        _real_get = cache.get
        snap = {"cached_at": datetime.now().isoformat(), "n_tickers": 2,
                "metrics": [dict(m) for m in _STUB_METRICS]}

        def _get(key, *a, **k):
            if key == "watchlist_metrics_snap":
                return snap
            if key == "watchlist_metrics_last":
                return snap["metrics"]
            return _real_get(key, *a, **k)

        def _put(key, *a, **k):
            if key.startswith("watchlist_metrics"):
                return None
            return None

        self._patches = [mock.patch.object(cache, "get", _get),
                         mock.patch.object(cache, "put", _put)]
        import data.saved_screens as ssm
        self.mem = _MemScreens()
        for fn in ("save_screen", "load_screen", "list_screens", "screen_versions",
                   "delete_screen"):
            self._patches.append(mock.patch.object(ssm, fn, getattr(self.mem, fn)))
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        (self._bu._UNIVERSE_CACHE, self._bu._NONCOMMON_CACHE,
         self._bu._NONCOMMON_PRIMARY_CACHE) = self._saved

    # ── helpers ────────────────────────────────────────────────────────
    def _open_new_screen(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(Path(__file__).parent.parent / "app.py"),
                               default_timeout=180)
        at.query_params["s"] = "Screen & Compare"
        at.run()
        self.assertFalse(at.exception, f"app raised: {[e.value for e in at.exception]}")
        at.button(key="screen_new_btn").click().run()
        self.assertFalse(at.exception, f"app raised: {[e.value for e in at.exception]}")
        return at

    @staticmethod
    def _result_tickers(at):
        res = at.session_state["_screen_result"]
        return [m["ticker"] for m in res["metrics"]] if res else None

    @staticmethod
    def _status_line(at):
        return next((m.value for m in at.markdown if "banks" in m.value
                     and "fundamentals" in m.value), None)

    # ── tests ──────────────────────────────────────────────────────────
    def test_edits_do_not_change_results_until_run(self):
        at = self._open_new_screen()
        # Fresh screen: builder up, nothing evaluated, no results.
        self.assertIsNone(at.session_state["_screen_applied"])
        self.assertIsNone(self._status_line(at))
        self.assertTrue(at.button(key="btn_run_valuation"))

        # Build: P/TBV < 1.5 → only AMAL passes. Still nothing until Run.
        at.button(key="filt_add_valuation").click().run()
        at.selectbox(key="filt_metric_valuation_0").set_value(_filter_index("P/TBV"))
        at.selectbox(key="filt_op_valuation_0").set_value("<")
        at.number_input(key="filt_val_valuation_0").set_value(1.5)
        at.run()
        self.assertFalse(at.exception, f"app raised: {[e.value for e in at.exception]}")
        self.assertIsNone(at.session_state["_screen_applied"])
        self.assertIsNone(self._status_line(at), "results rendered before Run")

        at.button(key="btn_run_valuation").click().run()
        self.assertFalse(at.exception, f"app raised: {[e.value for e in at.exception]}")
        self.assertEqual(self._result_tickers(at), ["AMAL"])
        self.assertIn("1 banks", self._status_line(at))
        self.assertEqual(at.session_state["_screen_applied"]["filters"],
                         [{"kind": "absolute", "metric": "ptbv_ratio", "op": "<", "value": 1.5}])

        # Edit the threshold so both would pass — results must NOT move.
        at.number_input(key="filt_val_valuation_0").set_value(3.0)
        at.run()
        self.assertEqual(self._result_tickers(at), ["AMAL"])
        self.assertIn("1 banks", self._status_line(at))
        self.assertEqual(at.session_state["_screen_applied"]["filters"][0]["value"], 1.5)
        self.assertTrue(any("Changed since the last run" in c.value for c in at.caption),
                        "unrun edits should be flagged next to Run")

        # Run applies the edit.
        at.button(key="btn_run_valuation").click().run()
        self.assertEqual(self._result_tickers(at), ["AMAL", "JPM"])
        self.assertIn("2 banks", self._status_line(at))

    def test_save_then_open_from_launcher_reruns_identically(self):
        at = self._open_new_screen()
        at.button(key="filt_add_valuation").click().run()
        at.selectbox(key="filt_metric_valuation_0").set_value(_filter_index("CET1"))
        at.selectbox(key="filt_op_valuation_0").set_value(">")
        at.number_input(key="filt_val_valuation_0").set_value(13.0)
        at.selectbox(key="sort_valuation").set_value(1)
        at.button(key="btn_run_valuation").click().run()
        self.assertEqual(self._result_tickers(at), ["JPM"])

        at.text_input(key="new_screen_name_valuation").set_value("Well capitalized")
        at.button(key="save_screen_btn_valuation").click().run()
        self.assertFalse(at.exception, f"app raised: {[e.value for e in at.exception]}")
        cfg = self.mem.store["Well capitalized"]["config"]
        self.assertEqual(cfg["filters"], [{"kind": "absolute", "metric_key": "cet1_ratio",
                                           "op": ">", "value": 13.0}])
        self.assertEqual((cfg["tab_key"], cfg["sort_idx"], cfg["sort_order"]),
                         ("valuation", 1, "Desc"))

        # Back to the launcher: the saved row is listed; opening it loads + runs.
        at.button(key="btn_close_valuation").click().run()
        self.assertFalse(at.session_state["_screen_open"])
        at.button(key="open_Well_capitalized.json").click().run()
        self.assertFalse(at.exception, f"app raised: {[e.value for e in at.exception]}")
        self.assertEqual(self._result_tickers(at), ["JPM"])
        self.assertEqual(at.session_state["_screen_applied"]["filters"],
                         [{"kind": "absolute", "metric": "cet1_ratio", "op": ">", "value": 13.0}])
        self.assertEqual(at.session_state["_screen_name"], "Well capitalized")
        # Recent (this session) recorded the run under the saved name.
        self.assertEqual(at.session_state["_screen_recent"][0]["name"], "Well capitalized")


if __name__ == "__main__":
    unittest.main(verbosity=2)
