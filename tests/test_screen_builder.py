"""Screen builder — "build, then run" (owner design 2026-09-24).

Hermetic pins on the Screen & Compare › Screen rewrite:

  1. Saved-screen round trip: a saved screen (current format AND the pre-kind
     format that stored only metric_key/op/value) restores into the builder's
     draft keys and re-serializes to exactly the same filter specs — nothing a
     user saved is lost or mis-mapped.
  2. Per-type spec serialization: each filter type (Absolute / Peer-relative /
     Change / Trend) round-trips draft keys → engine spec → saved cfg → draft.
  3. Filter-row removal shifts the later rows down with EVERY suffix, so a
     Change row keeps its basis/op/Δ after the row above it is removed.
  4. Structure: the four dialogs are gone, the builder has a primary
     "Run screen" button, and the results block reads only the applied
     snapshot + stored result (never the draft) — the gating invariant.

The helpers live in app.py (a Streamlit script), so they are lifted out by
name from its AST and executed against a fake `st` — no Streamlit runtime,
no data load. Run: python -m unittest tests.test_screen_builder
"""
from __future__ import annotations

import ast
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

APP = Path(__file__).parent.parent / "app.py"
SRC = APP.read_text(encoding="utf-8")


class _FakeSt:
    def __init__(self):
        self.session_state = {}


def _lift(names: set[str]) -> dict:
    """Exec the named top-level assignments and (possibly nested) function
    defs from app.py into a namespace seeded with config + a fake st."""
    from config import METRICS, METRICS_BY_KEY, TABS
    tree = ast.parse(SRC)
    picked = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            picked.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in names for t in node.targets):
            picked.append(node)
    found = {n.name if isinstance(n, ast.FunctionDef) else n.targets[0].id
             for n in picked}
    missing = names - found
    assert not missing, f"not found in app.py: {missing}"
    mod = ast.Module(body=picked, type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {"METRICS": METRICS, "METRICS_BY_KEY": METRICS_BY_KEY, "TABS": TABS,
          "st": _FakeSt(), "tab_key": "valuation"}
    exec(compile(mod, str(APP), "exec"), ns)
    return ns


_NAMES = {"_SCREEN_FILTER_FMTS", "_SCREEN_MAX_FILTERS", "_SCREEN_FILTER_SUFFIXES",
          "_SCREEN_SCOPE_SUFFIXES", "_screen_legacy_sort_key",
          "_screen_formulas", "_screen_filter_options", "_screen_valid_dyn",
          "_screen_clear_filters", "_screen_restore_cfg",
          "_filter_specs_from_state", "_specs_to_cfg_filters", "_remove_filter",
          "_add_filter"}


def _ns():
    ns = _lift(_NAMES)
    # The branch-local names _filter_specs_from_state closes over.
    fk = sorted([(m["key"], m["label"]) for m in ns["METRICS"]
                 if m.get("format") in ns["_SCREEN_FILTER_FMTS"]], key=lambda x: x[1])
    ns["filter_keys"] = [None] + [k for k, _ in fk]
    ns["filter_label"] = dict(fk)
    return ns


def _cfg_filters(specs):
    return [{**{k: v for k, v in s.items() if k != "metric"}, "metric_key": s["metric"]}
            for s in specs]


class TestSavedScreenRoundTrip(unittest.TestCase):
    def _restore_and_read(self, cfg, tk="valuation"):
        ns = _ns()
        ss = ns["st"].session_state
        ns["_screen_clear_filters"](ss, tk)
        ns["_screen_restore_cfg"](ss, cfg, tk)
        return ns, ss, ns["_filter_specs_from_state"]()

    def test_current_format_round_trips_every_type(self):
        cfg = {
            "tab_key": "valuation", "sort_idx": 2, "sort_order": "Asc",
            "filters": [
                {"kind": "absolute", "metric_key": "ptbv_ratio", "op": "<", "value": 1.2},
                {"kind": "peer_relative", "metric_key": "roatce", "band": "Top", "pct": 20.0},
                {"kind": "change", "metric_key": "nim", "basis": "YoY", "op": "≥", "value": 0.15},
                {"kind": "trend", "metric_key": "npl_ratio", "direction": "up", "quarters": 4},
            ],
            "columns": ["ptbv_ratio", "roatce", "cet1_ratio"],
        }
        ns, ss, specs = self._restore_and_read(cfg)
        self.assertEqual(specs, [
            {"kind": "absolute", "metric": "ptbv_ratio", "op": "<", "value": 1.2},
            {"kind": "peer_relative", "metric": "roatce", "band": "Top", "pct": 20.0},
            {"kind": "change", "metric": "nim", "basis": "YoY", "op": "≥", "value": 0.15},
            {"kind": "trend", "metric": "npl_ratio", "direction": "up", "quarters": 4},
        ])
        # Re-serialized for Save: identical to what was loaded.
        self.assertEqual(ns["_specs_to_cfg_filters"](specs), cfg["filters"])
        self.assertEqual(ss["num_filters_valuation"], 4)
        # Legacy saves stored Sort as an INDEX into [Default] + the table's
        # registry columns; restore maps it to the KEY once (valuation[1]).
        from config import TABS, METRICS_BY_KEY
        val_cols = [c for c in next(t for t in TABS if t["key"] == "valuation")["columns"]
                    if METRICS_BY_KEY.get(c)]
        self.assertEqual(ss["sort_valuation"], val_cols[1])
        self.assertEqual(ss["order_valuation"], "Asc")
        self.assertEqual(ss["custom_cols_valuation"], cfg["columns"])

    def test_pre_kind_saved_screen_loads_as_absolute(self):
        """Saves from before filter kinds existed carry only metric_key/op/value."""
        cfg = {"tab_key": "valuation", "sort_idx": 0, "sort_order": "Desc",
               "filters": [{"metric_key": "cet1_ratio", "op": ">", "value": 11.0}]}
        _, ss, specs = self._restore_and_read(cfg)
        self.assertEqual(specs, [{"kind": "absolute", "metric": "cet1_ratio",
                                  "op": ">", "value": 11.0}])
        self.assertEqual(ss["filt_kind_valuation_0"], "Absolute")

    def test_unknown_metric_key_is_skipped_never_mismapped(self):
        cfg = {"filters": [{"kind": "absolute", "metric_key": "no_such_metric",
                            "op": "<", "value": 1.0},
                           {"kind": "absolute", "metric_key": "ptbv_ratio",
                            "op": "<", "value": 1.0}]}
        _, ss, specs = self._restore_and_read(cfg)
        self.assertEqual([s["metric"] for s in specs], ["ptbv_ratio"])
        self.assertEqual(ss["num_filters_valuation"], 1)

    def test_recent_entry_restores_scope_and_asof(self):
        """This session's Recent entries also carry the scope selection + as-of."""
        cfg = {"filters": [], "scope_keys": {"scope_type": "Manual", "manual": ["JPM", "BAC"]},
               "asof": "Q4 2023"}
        _, ss, _ = self._restore_and_read(cfg)
        self.assertEqual(ss["screen_valuation_scope_type"], "Manual")
        self.assertEqual(ss["screen_valuation_manual"], ["JPM", "BAC"])
        self.assertEqual(ss["asof_valuation"], "Q4 2023")

    def test_clear_resets_the_whole_draft(self):
        ns = _ns()
        ss = ns["st"].session_state
        ss.update({"filt_kind_valuation_0": "Change", "filt_basis_valuation_0": "YoY",
                   "num_filters_valuation": 1, "screen_valuation_scope_type": "Manual",
                   "screen_valuation_manual": ["JPM"], "asof_valuation": "Q1 2024",
                   "sort_valuation": 3, "order_valuation": "Asc",
                   "custom_cols_valuation": ["roatce"]})
        ns["_screen_clear_filters"](ss, "valuation")
        self.assertEqual(ss["num_filters_valuation"], 0)
        self.assertNotIn("filt_kind_valuation_0", ss)
        self.assertNotIn("filt_basis_valuation_0", ss)
        self.assertEqual(ss["screen_valuation_scope_type"], "All banks")
        self.assertNotIn("screen_valuation_manual", ss)
        self.assertEqual(ss["asof_valuation"], "Latest (live)")
        self.assertEqual((ss["sort_valuation"], ss["order_valuation"]), ("", "Desc"))
        self.assertNotIn("custom_cols_valuation", ss)
        self.assertEqual(ns["_filter_specs_from_state"](), [])


class TestKeyBasedState(unittest.TestCase):
    """Review 2026-10-02: filter/sort widgets stored list POSITIONS, so a
    change to the option list (another screen's call-report fields, a removed
    column) silently re-pointed them at a different metric. They now store
    the metric KEY."""

    def test_restore_writes_keys_and_sort_key_wins(self):
        ns = _ns()
        ss = ns["st"].session_state
        ns["_screen_clear_filters"](ss, "valuation")
        ns["_screen_restore_cfg"](ss, {
            "sort_key": "cet1_ratio", "sort_idx": 3,
            "filters": [{"kind": "absolute", "metric_key": "nim", "op": ">", "value": 3.0}]},
            "valuation")
        self.assertEqual(ss["filt_metric_valuation_0"], "nim")
        self.assertEqual(ss["sort_valuation"], "cet1_ratio", "sort_key wins over legacy sort_idx")

    def test_restore_keeps_dynamic_filter_by_key(self):
        ns = _ns()
        ss = ns["st"].session_state
        ns["_screen_clear_filters"](ss, "valuation")
        ns["_screen_restore_cfg"](ss, {
            "dyn": [{"key": "fdic:CD3LES"}],
            "filters": [{"kind": "absolute", "metric_key": "fdic:CD3LES", "op": ">", "value": 1.0}]},
            "valuation")
        self.assertEqual(ss["screen_dyn"], [{"key": "fdic:CD3LES"}])
        self.assertEqual(ss["filt_metric_valuation_0"], "fdic:CD3LES")

    def test_legacy_sort_index_out_of_range_is_default(self):
        ns = _ns()
        self.assertIsNone(ns["_screen_legacy_sort_key"]("valuation", 999))
        self.assertIsNone(ns["_screen_legacy_sort_key"]("valuation", 0))
        self.assertIsNone(ns["_screen_legacy_sort_key"]("no_such_tab", 1))

    def test_default_options_are_empty_string_not_none(self):
        """None renders as an EMPTY selectbox ("Choose an option"), not as
        "Default"/"—" (seen in prod 2026-10-03) — the first option is ""."""
        self.assertIn('filter_keys = [""] + [k for k, _ in filterable]', SRC)
        self.assertIn('sort_keys = [""]', SRC)
        self.assertNotIn('filter_keys = [None]', SRC)
        self.assertNotIn('sort_keys = [None]', SRC)

    def test_widgets_are_key_based_in_source(self):
        blk = SRC[SRC.index('elif section == "Screen & Compare" and sc_sub == "Screen" and screening_tab:'):]
        self.assertIn('"Metric", options=filter_keys,', blk)
        self.assertIn('"Sort", options=sort_keys,', blk)
        self.assertNotIn("options=list(range(len(filter_labels)))", SRC)
        self.assertNotIn("options=list(range(len(sort_labels)))", SRC)


class TestFilterRows(unittest.TestCase):
    def test_remove_shifts_every_suffix_down(self):
        ns = _ns()
        ss = ns["st"].session_state
        ss.update({
            "num_filters_valuation": 3,
            "filt_kind_valuation_0": "Absolute", "filt_metric_valuation_0": "ptbv_ratio",
            "filt_op_valuation_0": "<", "filt_val_valuation_0": 1.0,
            "filt_kind_valuation_1": "Change", "filt_metric_valuation_1": "nim",
            "filt_basis_valuation_1": "YoY", "filt_chop_valuation_1": "≥",
            "filt_chval_valuation_1": 0.25,
            "filt_kind_valuation_2": "Trend", "filt_metric_valuation_2": "npl_ratio",
            "filt_dir_valuation_2": "Rising", "filt_q_valuation_2": 4,
        })
        ns["_remove_filter"](0)
        self.assertEqual(ss["num_filters_valuation"], 2)
        self.assertEqual(ns["_filter_specs_from_state"](), [
            {"kind": "change", "metric": "nim", "basis": "YoY", "op": "≥", "value": 0.25},
            {"kind": "trend", "metric": "npl_ratio", "direction": "up", "quarters": 4},
        ])
        # Row 0's absolute-only keys must not linger under the shifted Change row.
        self.assertNotIn("filt_op_valuation_0", ss)
        self.assertNotIn("filt_val_valuation_0", ss)
        # The vacated last slot is fully cleared.
        self.assertFalse([k for k in ss if k.endswith("_valuation_2")])

    def test_add_caps_at_max(self):
        ns = _ns()
        ss = ns["st"].session_state
        for _ in range(ns["_SCREEN_MAX_FILTERS"] + 2):
            ns["_add_filter"]()
        self.assertEqual(ss["num_filters_valuation"], ns["_SCREEN_MAX_FILTERS"])

    def test_empty_metric_row_is_ignored(self):
        ns = _ns()
        ss = ns["st"].session_state
        ss.update({"num_filters_valuation": 1, "filt_kind_valuation_0": "Absolute",
                   "filt_metric_valuation_0": "", "filt_op_valuation_0": ">",
                   "filt_val_valuation_0": 5.0})
        self.assertEqual(ns["_filter_specs_from_state"](), [])


class TestStructure(unittest.TestCase):
    def _screen_block(self):
        a = SRC.index('elif section == "Screen & Compare" and sc_sub == "Screen" and screening_tab:')
        b = SRC.index('elif section == "Company":', a)
        return SRC[a:b]

    def test_dialogs_are_gone_and_run_button_exists(self):
        blk = self._screen_block()
        for gone in ('@st.dialog("Metric filters"', '@st.dialog("Columns"',
                     '@st.dialog("Export results"', '@st.dialog("Saved screens"'):
            self.assertNotIn(gone, SRC, f"{gone} must not come back — the builder is inline")
        self.assertIn('st.button("Run screen", type="primary"', blk)
        self.assertIn('key=f"btn_run_{tab_key}"', blk)
        self.assertIn('st.button("+ Add filter"', blk)

    def test_results_block_reads_only_the_applied_snapshot(self):
        """The gating invariant: after Run, the results block must not touch
        the draft names (display_metrics / filter_specs / display_cols_draft /
        sort_idx / sort_order) — only _applied and _res."""
        blk = self._screen_block()
        start = blk.index("# ── Results (read ONLY the applied spec")
        results = blk[start:]
        import re
        for draft_name in ("display_metrics", "filter_specs", "display_cols_draft",
                           "display_tickers", "_draft", "sort_idx", "sort_order"):
            # As a NAME (identifier), not as a quoted dict key like "sort_idx".
            self.assertIsNone(
                re.search(r"(?<![\w\x22\x27])" + re.escape(draft_name) + r"\b", results),
                f"results block reads draft name {draft_name!r}")
        self.assertIn('_applied["filters"]', results)
        self.assertIn('_res["metrics"]', results)

    def test_export_goes_through_the_shared_exporter(self):
        blk = self._screen_block()
        self.assertIn("from ui.export import table_export", blk)
        self.assertNotIn("st.download_button(\n            \"Download Excel\"", blk)


if __name__ == "__main__":
    unittest.main(verbosity=2)
