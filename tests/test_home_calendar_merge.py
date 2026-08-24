"""
Tests for the Home calendar pane's macro merge (docs/HOME-MACRO-PLAN.md,
"Macro half of Today's Calendar"): FRED-based print days + FOMC decision
dates from data/macro_calendar merged into the FMP economics-calendar rows.

Pins (pure helper ui.home._merge_macro_calendar unless noted):
  • FOMC: a macro_calendar FOMC entry lands in the merged rows — and in the
    rendered pane HTML (_af_calendar_table) — since FMP may not carry it
  • dedupe: a same-day same-print FMP+FRED pair collapses to ONE row, the
    FMP row (it carries consensus/prior), across the observed name skews
    ("CPI" vs "Core CPI YoY (May)", "FOMC Rate Decision" vs "Fed Interest
    Rate Decision", "Employment Situation (NFP)" vs "Nonfarm Payrolls")
  • no guessing: a different date, or a FRED name with no alias mapping,
    renders BOTH rows
  • non-blocking: macro_calendar raising inside _af_calendar_table leaves
    the FMP rows intact (pane renders, no exception)
  • the 10-macro-row cap applies AFTER the merge

All data seams are stubbed module-attribute style (the pattern
tests/test_render_smoke.py uses for this pane); no network, no Streamlit
runtime beyond the shared stub.
"""
import datetime as dt
import unittest

from tests import _streamlit_stub

_streamlit_stub.install()

import ui.home as home  # noqa: E402


def _fmp_row(date, name, mid="180K / 175K"):
    """A pane-schema FMP macro item as _af_calendar_table builds them."""
    return {"kind": "macro", "date": date, "ticker": None,
            "name": name, "mid": mid, "detail": "8:30 AM ET"}


def _fred(date, name, kind="print", time="8:30 ET"):
    """A data/macro_calendar entry (see its module docstring for the shape)."""
    return {"date": date, "name": name, "release_id": None,
            "kind": kind, "importance": "high", "time": time}


class TestMergeMacroCalendar(unittest.TestCase):
    """Pure-helper pins for ui.home._merge_macro_calendar."""

    def test_fomc_only_from_fred_is_appended(self):
        out = home._merge_macro_calendar(
            [], [_fred("2026-09-16", "FOMC Rate Decision", kind="fomc",
                       time="2:00 ET")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "FOMC Rate Decision")
        self.assertEqual(out[0]["kind"], "macro")   # pane row schema
        self.assertIsNone(out[0]["ticker"])
        self.assertEqual(out[0]["detail"], "2:00 ET")
        self.assertEqual(out[0]["mid"], "")         # no consensus from FRED

    def test_same_day_same_print_collapses_to_fmp_row(self):
        fmp = [_fmp_row("2026-09-10", "Core CPI YoY (May)", mid="3.2% / 3.1%")]
        out = home._merge_macro_calendar(
            fmp, [_fred("2026-09-10", "CPI")])
        self.assertEqual(out, fmp)  # one row, the FMP one (has cons./prior)

    def test_fomc_dedupes_against_fmp_rate_decision(self):
        fmp = [_fmp_row("2026-09-16", "Fed Interest Rate Decision",
                        mid="3.75% / 3.75%")]
        out = home._merge_macro_calendar(
            fmp, [_fred("2026-09-16", "FOMC Rate Decision", kind="fomc",
                        time="2:00 ET")])
        self.assertEqual(out, fmp)

    def test_nfp_dedupes_against_fmp_payrolls_and_unemployment(self):
        fmp = [_fmp_row("2026-09-04", "Nonfarm Payrolls (Aug)"),
               _fmp_row("2026-09-04", "Unemployment Rate (Aug)")]
        out = home._merge_macro_calendar(
            fmp, [_fred("2026-09-04", "Employment Situation (NFP)")])
        self.assertEqual(out, fmp)  # one release, FMP's per-series rows stand

    def test_different_date_keeps_both(self):
        fmp = [_fmp_row("2026-09-10", "Core CPI YoY (May)")]
        out = home._merge_macro_calendar(fmp, [_fred("2026-10-13", "CPI")])
        self.assertEqual(len(out), 2)
        self.assertEqual({r["date"] for r in out},
                         {"2026-09-10", "2026-10-13"})

    def test_unmapped_fred_name_renders_both_never_guesses(self):
        fmp = [_fmp_row("2026-09-10", "Wholesale Widget Index")]
        out = home._merge_macro_calendar(
            fmp, [_fred("2026-09-10", "Wholesale Widget Index")])
        self.assertEqual(len(out), 2)  # not in the alias map → both rows

    def test_dateless_fred_entry_skipped(self):
        out = home._merge_macro_calendar([], [_fred(None, "CPI")])
        self.assertEqual(out, [])


class TestCalendarPaneMacroMerge(unittest.TestCase):
    """_af_calendar_table with all data seams stubbed (module-attribute
    style, matching tests/test_render_smoke.py's calendar tests)."""

    def _render(self, fmp_events, macro_fn, watchlist=()):
        import data.estimates as est
        import data.econ_calendar as ec
        import data.earnings_call as ecall
        import data.macro_calendar as mc
        saved = (est.fetch_earnings_calendar, ec.get_upcoming_releases,
                 ecall.merged_call_info, ecall.earnings_timing_map,
                 mc.get_upcoming_prints)
        try:
            est.fetch_earnings_calendar = lambda w: []
            # Stubs must accept cache_only — the render passes it (the
            # 2026-08-24 render-path fix); a stub that rejects it raises into
            # the pane's `except Exception: pass` and silently drops the rows.
            ec.get_upcoming_releases = (lambda days=14, cache_only=False:
                                        list(fmp_events))
            ecall.merged_call_info = lambda: {}
            ecall.earnings_timing_map = lambda: {}
            mc.get_upcoming_prints = macro_fn
            return home._af_calendar_table(list(watchlist))
        finally:
            (est.fetch_earnings_calendar, ec.get_upcoming_releases,
             ecall.merged_call_info, ecall.earnings_timing_map,
             mc.get_upcoming_prints) = saved

    @staticmethod
    def _iso(days):
        return (dt.date.today() + dt.timedelta(days=days)).isoformat()

    def test_fomc_row_renders_in_pane(self):
        h = self._render(
            [], lambda days=7, cache_only=False: [
                _fred(self._iso(5), "FOMC Rate Decision",
                      kind="fomc", time="2:00 ET")])
        self.assertIn("FOMC Rate Decision", h)
        self.assertIn("2:00 ET", h)
        self.assertIn("background:#b45309", h)  # amber macro dot

    def test_same_day_pair_renders_once_with_fmp_consensus(self):
        d = self._iso(3)
        fmp = [{"date": d, "event": "Core CPI YoY (May)", "estimate": 3.2,
                "previous": 3.1, "unit": "%", "impact": "High",
                "datetime": d + " 12:30:00", "released": False}]
        h = self._render(fmp,
                         lambda days=7, cache_only=False: [_fred(d, "CPI")])
        self.assertEqual(h.count('class="erow a4 ed calrow"'), 1)
        self.assertIn("Core CPI YoY (May)", h)   # the FMP row won
        self.assertIn("3.2% / 3.1%", h)          # with its cons./prior
        self.assertNotIn(">CPI<", h)             # no bare FRED duplicate row

    def test_macro_calendar_raising_leaves_fmp_rows_intact(self):
        d = self._iso(2)
        fmp = [{"date": d, "event": "Nonfarm Payrolls (Aug)", "estimate": 180.0,
                "previous": 175.0, "unit": "K", "impact": "High",
                "datetime": d + " 12:30:00", "released": False}]

        def boom(days=7, cache_only=False):
            raise RuntimeError("FRED down")

        h = self._render(fmp, boom)                  # must not raise
        self.assertIn("Nonfarm Payrolls (Aug)", h)   # FMP leg intact
        self.assertIn("180K / 175K", h)

    def test_ten_macro_row_cap_applies_after_merge(self):
        # 8 FMP prints + 4 non-duplicate FRED days = 12 merged macro rows,
        # capped at 10 for the pane.
        fmp = [{"date": self._iso(i), "event": f"Initial Jobless Claims {i}",
                "estimate": 230.0, "previous": 235.0, "unit": "K",
                "impact": "High", "datetime": self._iso(i) + " 12:30:00",
                "released": False} for i in range(1, 9)]
        fred = [_fred(self._iso(9 + i), "FOMC Rate Decision", kind="fomc",
                      time="2:00 ET") for i in range(4)]
        h = self._render(fmp, lambda days=7, cache_only=False: list(fred))
        self.assertEqual(h.count('class="erow a4 ed calrow"'), 10)


if __name__ == "__main__":
    unittest.main()
