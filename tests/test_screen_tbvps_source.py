"""Pins the Screen & Compare TBV wiring for OTC (non-XBRL) banks.

Root cause being pinned (fixed in 3154e9f): config.py's "tbvps" screen column
was `source: "sec", sec_concept: "tangible_book_value_per_share"` — a field
that is None for the ~100 universe banks with no usable SEC XBRL — so the
screening table rendered "—" even when analysis/valuation._resolve_tbvps had
the bank's own release figure (source "company_release"). The column must read
the RESOLVED value (source "computed" → compute_all_valuations()["tbvps"]),
the same figure ptbv_ratio / fair_price / ptbv_discount price against.

Three layers, so the regression can't slip back in anywhere along the path:
  1. config: the tbvps / ptbv_ratio screen columns are source "computed".
  2. row build: build_bank_metrics carries a company_release tbvps into the
     screen row even with EMPTY sec_data (the OTC case).
  3. render: ui.generic_table renders that row's TBV/Sh as a value (never
     "—"), keeps the "TBV Src" provenance string, and a bank with tbvps=None
     still renders "—" (true absence stays deliberate).

Run: python -m unittest tests.test_screen_tbvps_source
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

# Order-independent streamlit stub (shared helper) — BEFORE project imports.
from tests import _streamlit_stub

_st = _streamlit_stub.install()


class TestScreenColumnWiring(unittest.TestCase):
    """Layer 1+2: the column definition and the row build."""

    def test_tbvps_screen_column_is_computed_not_sec(self):
        from config import METRICS
        m = next(m for m in METRICS if m["key"] == "tbvps")
        self.assertEqual(
            m["source"], "computed",
            'source "sec" reads sec_data["tangible_book_value_per_share"], '
            "which is None for every non-XBRL OTC bank — the screen must read "
            "the resolved value from compute_all_valuations")

    def test_ptbv_and_fair_value_columns_are_computed(self):
        from config import METRICS
        by_key = {m["key"]: m for m in METRICS}
        for key in ("ptbv_ratio", "fair_price", "ptbv_discount"):
            self.assertEqual(
                by_key[key]["source"], "computed",
                f"{key} must come from the valuation engine (priced off the "
                "resolved tbvps), never a per-source raw field")

    def test_build_row_carries_release_tbvps_with_empty_sec_data(self):
        """The OTC case end-to-end at the row-build seam: no SEC data at all,
        the engine resolves tbvps from the company release — the screen row
        must carry it (and its provenance) under the exact keys the screening
        table reads."""
        import analysis.metrics as am

        resolved = {"tbvps": 12.38, "tbvps_source": "company_release",
                    "ptbv_ratio": 1.10}
        with patch.object(am, "compute_all_valuations",
                          return_value=resolved) as mocked:
            row = am.build_bank_metrics("PBAM", {}, {}, {}, [])
        mocked.assert_called_once()
        self.assertEqual(row["tbvps"], 12.38)
        self.assertEqual(row["tbvps_source"], "company_release")
        self.assertEqual(row["ptbv_ratio"], 1.10)

    def test_build_row_true_absence_stays_none(self):
        import analysis.metrics as am

        resolved = {"tbvps": None, "tbvps_source": None, "ptbv_ratio": None}
        with patch.object(am, "compute_all_valuations", return_value=resolved):
            row = am.build_bank_metrics("SFST", {}, {}, {}, [])
        self.assertIsNone(row["tbvps"])
        self.assertIsNone(row["tbvps_source"])


class TestScreenTableRender(unittest.TestCase):
    """Layer 3: the screening table HTML itself (ui.generic_table)."""

    COLS = ["tbvps", "tbvps_source", "ptbv_ratio"]

    def setUp(self):
        # Capture the table HTML the renderer emits via st.markdown.
        self._orig_markdown = getattr(_st, "markdown", None)
        self._orig_warning = getattr(_st, "warning", None)
        self.markdown_calls: list[str] = []
        _st.markdown = lambda body, **k: self.markdown_calls.append(str(body))
        _st.warning = lambda *a, **k: None

    def tearDown(self):
        if self._orig_markdown is not None:
            _st.markdown = self._orig_markdown
        else:
            del _st.markdown
        if self._orig_warning is not None:
            _st.warning = self._orig_warning
        else:
            del _st.warning

    def _render_rows(self, rows):
        from ui.generic_table import render_generic_table
        render_generic_table(rows, self.COLS, table_key="tbv_pin")
        html = next((c for c in self.markdown_calls if "scrn-wrap" in c), None)
        self.assertIsNotNone(html, "renderer emitted no table HTML")
        return html

    def _row_for(self, html, ticker):
        for tr in re.findall(r"<tr>(.*?)</tr>", html, flags=re.DOTALL):
            if f">{ticker}<" in tr:
                return tr
        self.fail(f"no table row rendered for {ticker}")

    def test_company_release_tbvps_renders_value_not_dash(self):
        # PBAM: the canonical non-SEC filer — TBV from its own release.
        rows = [
            {"ticker": "PBAM", "tbvps": 49.57,
             "tbvps_source": "company_release", "ptbv_ratio": 1.21},
            {"ticker": "SFST", "tbvps": None,
             "tbvps_source": None, "ptbv_ratio": None},
        ]
        html = self._render_rows(rows)

        pbam = self._row_for(html, "PBAM")
        self.assertIn("$49.57", pbam,
                      "a resolved company_release TBV/Sh must render as the "
                      "value, never '—'")
        self.assertIn("1.21x", pbam, "P/TBV must render off the same figure")
        self.assertIn("company_release", pbam,
                      "the TBV Src column must keep the provenance string")
        self.assertNotIn("—", pbam)

    def test_true_absence_renders_dash(self):
        rows = [
            {"ticker": "PBAM", "tbvps": 49.57,
             "tbvps_source": "company_release", "ptbv_ratio": 1.21},
            {"ticker": "SFST", "tbvps": None,
             "tbvps_source": None, "ptbv_ratio": None},
        ]
        html = self._render_rows(rows)
        sfst = self._row_for(html, "SFST")
        # All three metric cells are deliberately n/a → "—" each.
        self.assertEqual(sfst.count("—"), len(self.COLS),
                         "a bank with no TBV anywhere must read as a "
                         "deliberate n/a, one '—' per metric cell")


if __name__ == "__main__":
    unittest.main()
