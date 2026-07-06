"""
Pins two AUDIT-2026-07-02 fixes in ui/earnings.py:

  #13 — the Biggest Beats/Misses markdown lines carry two pre-formatted
       dollar amounts ("$3.10 → $3.30"); the $…$ pair must be \\$-escaped
       or Streamlit renders the span as LaTeX (house gotcha).
  #14 — the surprise heat-map used to derive columns from exact announcement
       DATES universe-wide (sorted(all_dates)[:8] ≈ 8 days of one season;
       each bank joined by exact date so most banks filled one cell, and
       several date columns collapsed onto the same quarter label). Columns
       must be FISCAL-QUARTER buckets and banks must join by bucket.

Run: python -m unittest tests.test_earnings_fixes
"""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Stub streamlit before importing ui modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
sys.modules.setdefault("streamlit", _st)
_c = types.ModuleType("streamlit.components")
_c1 = types.ModuleType("streamlit.components.v1")
_c.v1 = _c1
_st.components = _c
sys.modules.setdefault("streamlit.components", _c)
sys.modules.setdefault("streamlit.components.v1", _c1)

from ui.earnings import _surprise_line, _quarter_label, _heatmap_columns  # noqa: E402


class TestSurpriseLineEscapesDollars(unittest.TestCase):
    """Finding #13 — beats/misses lines must not leave a bare $…$ pair."""

    def test_two_dollar_amounts_escaped(self):
        line = _surprise_line({
            "Ticker": "JPM", "Metric": "EPS", "Surprise %": 6.5,
            "Consensus": "$3.10", "Actual": "$3.30",
        })
        self.assertIn("\\$3.10", line)
        self.assertIn("\\$3.30", line)
        # No unescaped $ anywhere — one survivor re-arms the LaTeX span.
        self.assertNotIn("$", line.replace("\\$", ""))
        # Content intact around the escaping.
        self.assertIn("**JPM** EPS: +6.5%", line)

    def test_dash_placeholders_untouched(self):
        # Missing estimate renders "—"; nothing to escape, line stays clean.
        line = _surprise_line({
            "Ticker": "CMA", "Metric": "EPS", "Surprise %": -2.0,
            "Consensus": "—", "Actual": "$1.20",
        })
        self.assertIn("(— → \\$1.20)", line)


class TestHeatmapQuarterBuckets(unittest.TestCase):
    """Finding #14 — columns are fiscal-quarter buckets, not exact dates."""

    def _row(self, date, surprise, actual=1.0, est=0.9):
        return {"date": date, "surprise_pct": surprise,
                "eps_actual": actual, "eps_estimate": est}

    def test_three_banks_two_quarters_five_dates(self):
        # 2 fiscal quarters (Q4-2025 announced Jan-2026, Q1-2026 announced
        # Apr-2026) spread over 5 distinct announcement dates.
        bank_data = {
            "AAA": [self._row("2026-04-15", 5.0), self._row("2026-01-14", 1.0)],
            "BBB": [self._row("2026-04-17", -3.0), self._row("2026-01-20", 2.0)],
            "CCC": [self._row("2026-04-21", 0.5), self._row("2026-01-14", -4.0)],
        }
        col_labels, placed = _heatmap_columns(bank_data)

        # Columns = the 2 quarter labels, chronological — NOT 5 date columns.
        self.assertEqual(col_labels, ["2025Q4", "2026Q1"])

        # Every bank lands in the right bucket with its own row.
        self.assertEqual(placed["AAA"]["2025Q4"]["surprise_pct"], 1.0)
        self.assertEqual(placed["AAA"]["2026Q1"]["surprise_pct"], 5.0)
        self.assertEqual(placed["BBB"]["2025Q4"]["surprise_pct"], 2.0)
        self.assertEqual(placed["BBB"]["2026Q1"]["surprise_pct"], -3.0)
        self.assertEqual(placed["CCC"]["2025Q4"]["surprise_pct"], -4.0)
        self.assertEqual(placed["CCC"]["2026Q1"]["surprise_pct"], 0.5)

    def test_latest_announcement_wins_within_bucket(self):
        # Two rows in the same fiscal-quarter bucket (e.g. a revision):
        # the later announcement date is the one shown.
        bank_data = {
            "AAA": [self._row("2026-01-14", 1.0), self._row("2026-02-02", 9.0)],
        }
        col_labels, placed = _heatmap_columns(bank_data)
        self.assertEqual(col_labels, ["2025Q4"])
        self.assertEqual(placed["AAA"]["2025Q4"]["surprise_pct"], 9.0)

    def test_keeps_only_eight_most_recent_quarters(self):
        rows = [self._row(f"20{20 + i // 4}-{(i % 4) * 3 + 1:02d}-15", float(i))
                for i in range(12)]  # 12 consecutive quarters, one bank
        col_labels, placed = _heatmap_columns({"AAA": rows})
        self.assertEqual(len(col_labels), 8)
        self.assertEqual(col_labels, sorted(col_labels))  # chronological
        # Oldest 4 buckets dropped, newest kept.
        self.assertEqual(len(placed["AAA"]), 8)

    def test_undated_rows_skipped_not_guessed(self):
        bank_data = {"AAA": [self._row(None, 1.0), self._row("", 2.0)]}
        col_labels, placed = _heatmap_columns(bank_data)
        self.assertEqual(col_labels, [])
        self.assertEqual(placed["AAA"], {})

    def test_quarter_label_fiscal_convention(self):
        # Jan announcement covers the prior calendar year's Q4.
        self.assertEqual(_quarter_label("2026-01-14"), "2025Q4")
        self.assertEqual(_quarter_label("2026-04-15"), "2026Q1")
        self.assertEqual(_quarter_label(None), "—")


if __name__ == "__main__":
    unittest.main()
