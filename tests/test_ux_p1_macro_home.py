"""
UX-review P1 regressions (2026-09-30) — Market & Macro + Home rates tables.

Pins:
  • UX-P1-01: every sparkline "Trend" header is pinned to the sparkline width
    (+ cell padding) and sits in an overflow-x:auto wrapper — Key indicators
    board, Rates & curve board, Credit spreads, Funding & Deposits — so a
    narrow column scrolls instead of cutting it to "Tre"/"Trenc". Home's
    Rates · Credit grid gives Level % / YTD bp the width their headers need
    and lets the header row wrap instead of ellipsizing ("LEVE…", "YTD …").
  • UX-P1-02: Home's computed spread rows (3M − 5Y, 10Y − 30Y, FF − 2Y) take
    every anchor AND range from the bundle's aligned a−b entry when present
    (range bars instead of "—"); "Fed Funds − 2Y" is shortened to "FF − 2Y"
    with the full name as its hover title.
  • UX-P1-03: release names are one line (ellipsis + title tooltip) and the
    two calendars sit side by side in a row BELOW the board/chart band — no
    third narrow column.
  • UX-P1-04: a row whose consensus sits ~1000× off its actual/prior under
    one K/M/B/T label is put on one scale (New Home Sales 684 / 0.62 / 607
    "M" → 684K / 620K / 607K, surprise +64K); surprises carry the row's unit
    (pp for a % release); an unresolvable mismatch drops the surprise.
  • UX-P1-06: the yield-curve legend is pinned to the figure's bottom edge
    below the "Maturity" axis title, with the bottom margin to hold both.

Run:  python -m unittest tests.test_ux_p1_macro_home -v
"""
from __future__ import annotations
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Order-independent streamlit stub (shared helper).
from tests import _streamlit_stub

_streamlit_stub.install()

import pandas as pd  # noqa: E402


def _st_mock():
    st = MagicMock()
    st.columns.side_effect = lambda spec, *a, **k: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))]
    return st


def _markdowns(st) -> list[str]:
    return [str(c.args[0]) for c in st.markdown.call_args_list if c.args]


def _heads(html: str) -> list[str]:
    return re.findall(r"<th[^>]*>([^<]*)</th>", html)


def _trend_th_ok(test, html, label, spark_w):
    w = spark_w + 16
    test.assertIn(f'<th style="text-align:center;width:{w}px;min-width:{w}px;">'
                  f'{label}</th>', html)
    test.assertIn('<div class="ksk-grid" style="overflow-x:auto;">', html)
    test.assertIn(label, _heads(html))          # full header text in markup


# ── UX-P1-01 ────────────────────────────────────────────────────────────

class TestTrendColumnWidth(unittest.TestCase):

    def test_trend_th_is_sparkline_width_plus_padding(self):
        from ui import macro
        self.assertEqual(
            macro._trend_th("Trend"),
            '<th style="text-align:center;width:108px;min-width:108px;">Trend</th>')
        self.assertIn("width:166px;min-width:166px;", macro._trend_th("T", 150))

    def test_key_indicators_board(self):
        from ui import macro
        row = {"theme": "Inflation", "label": "CPI", "basis": "yoy_pct",
               "latest": 3.4, "prior": 3.3, "delta": 0.1, "favorable": "down",
               "zscore": 0.2, "spark": [1.0, 2.0, 3.0],
               "as_of": pd.Timestamp("2026-08-31"), "freq": "M"}
        html = macro._board_table([row])
        _trend_th_ok(self, html, "Trend", 92)
        self.assertEqual(_heads(html), ["Indicator", "Latest", "Prior", "Δ",
                                        "vs hist", "Trend", "As of"])

    def test_rates_board(self):
        from ui import macro
        row = {"group": "Treasury", "label": "10-Year", "unit": "%",
               "latest": 4.5, "d1w": 3.0, "d3m": 10.0, "z": 1.1,
               "spark": [4.0, 4.5], "as_of": pd.Timestamp("2026-09-24")}
        html = macro._rates_board_table([row])
        _trend_th_ok(self, html, "Trend (3Y)", 150)
        self.assertIn('width="150"', html)   # the sparkline the th is sized for

    def test_credit_spreads(self):
        from ui import macro
        d = {"latest": 1.0, "d3m": 5.0, "d1y": -3.0, "pctile": 40,
             "spark": [1.0, 1.1, 0.9], "as_of": pd.Timestamp("2026-09-23")}
        data = {sid: dict(d) for _g, sid, _l in macro._CREDIT_LADDER}
        st = _st_mock()
        with patch.object(macro, "st", st), \
             patch.object(macro, "_credit_oas_data", return_value=data), \
             patch.object(macro, "fetch_series",
                          return_value=pd.DataFrame(columns=["date", "value"])):
            macro._render_credit_spreads()
        html = [m for m in _markdowns(st) if "Trend (5Y)" in m][0]
        _trend_th_ok(self, html, "Trend (5Y)", 130)
        # table column widened out of the right spacer; the charts keep 1.0 each
        specs = [c.args[0] for c in st.columns.call_args_list]
        self.assertIn([1.3, 1.0, 1.0, 0.74], specs)

    def test_funding_deposits(self):
        from ui import macro
        st = _st_mock()
        rates = {"asof": "2026-09-15",
                 "savings": {"rate_pct": 0.40, "cap_pct": 1.15}}
        hist = [{"asof": "2026-08-15", "savings": {"rate_pct": 0.39}},
                {"asof": "2026-09-15", "savings": {"rate_pct": 0.40}}]
        with patch.object(macro, "st", st), \
             patch.object(macro, "latest_value", return_value=3.88), \
             patch.object(macro, "latest_date",
                          return_value=pd.Timestamp("2026-09-24")), \
             patch.object(macro, "fetch_series",
                          return_value=pd.DataFrame(columns=["date", "value"])), \
             patch("data.national_rates.get_national_rates", return_value=rates), \
             patch("data.national_rates.get_national_rate_history",
                   return_value=hist), \
             patch("ui.chrome.table_export"):
            macro._render_funding_deposits()
        html = [m for m in _markdowns(st) if "Trend (5Y)" in m][0]
        _trend_th_ok(self, html, "Trend (5Y)", 92)
        specs = [c.args[0] for c in st.columns.call_args_list]
        self.assertIn([1.35, 1.6, 0.8], specs)


class TestHomeRatesGridHeaders(unittest.TestCase):

    def test_r10_column_budget_and_header_wrap(self):
        from ui import home
        self.assertIn(".afwrap .erow.r10{grid-template-columns:.92fr .66fr .4fr "
                      ".44fr .52fr .44fr .52fr .56fr .52fr .5fr;column-gap:10px;",
                      home._AF_CSS)
        # the header row may wrap (2 lines fit the 1.3125rem row) — never "LEVE…"
        self.assertIn(".afwrap .erow.r10.eh>*{white-space:normal;line-height:1.05;}",
                      home._AF_CSS)

    def test_header_labels_full_in_markup(self):
        from ui import home
        with patch("data.live_rates.live_yields", return_value={}), \
             patch.object(home, "_rates_bundle", return_value={}):
            html = home._af_rates_table()
        head = html.split('<div class="rsec">', 1)[0]
        for lbl in ("Instrument", "Level %", "1D bp", "1W bp", "1M bp",
                    "YTD bp", "52wk"):
            self.assertIn(f">{lbl}</span>", head)
        self.assertEqual(head.count(">range</span>"), 3)


# ── UX-P1-02 ────────────────────────────────────────────────────────────

def _full(level, **kw):
    base = {"level": level, "d1": level - 0.01, "w1": level - 0.05,
            "m1": level - 0.10, "ytd": level - 0.20, "lo": level - 0.50,
            "hi": level + 0.50, "w_lo": level - 0.06, "w_hi": level + 0.01,
            "m_lo": level - 0.12, "m_hi": level + 0.02, "y_lo": level - 0.30,
            "y_hi": level + 0.10}
    base.update(kw)
    return base


class TestCalcSpreadRanges(unittest.TestCase):

    def test_calc_reads_aligned_diff_entry(self):
        from ui import home
        # legs would give level 4.00 − 4.40 = −0.40; the aligned a−b history
        # (both legs on the same date) says −0.38 — the diff entry wins whole.
        bundle = {"DGS3MO": _full(4.00), "DGS5": _full(4.40),
                  "DGS3MO-DGS5": _full(-0.38)}
        an, is_live = home._af_row_anchors("calc", "DGS3MO", "DGS5", bundle, {})
        self.assertFalse(is_live)
        self.assertEqual(an, _full(-0.38))
        for k in ("lo", "hi", "w_lo", "w_hi", "m_lo", "m_hi", "y_lo", "y_hi"):
            self.assertIsNotNone(an[k], k)

    def test_calc_without_diff_entry_keeps_na_ranges(self):
        from ui import home
        bundle = {"DGS3MO": _full(4.00), "DGS5": _full(4.40),
                  "DGS3MO-DGS5": None}   # warm job failed this pair
        an, _ = home._af_row_anchors("calc", "DGS3MO", "DGS5", bundle, {})
        self.assertAlmostEqual(an["level"], -0.40)
        for k in ("lo", "hi", "w_lo", "w_hi", "m_lo", "m_hi", "y_lo", "y_hi"):
            self.assertIsNone(an[k], k)

    def _row(self, html, label):
        m = re.search(r'<span class="nm"[^>]*>' + re.escape(label)
                      + r'</span>(.*?)</div></a>', html)
        self.assertIsNotNone(m, label)
        return m.group(1)

    def test_board_calc_rows_render_range_bars(self):
        from ui import home
        bundle = {"DGS3MO-DGS5": _full(-0.38), "DGS10-DGS30": _full(-0.30),
                  "DFF-DGS2": _full(-0.83)}
        with patch("data.live_rates.live_yields", return_value={}), \
             patch.object(home, "_rates_bundle", return_value=bundle):
            html = home._af_rates_table()
        for label in ("3M − 5Y", "10Y − 30Y", "FF − 2Y"):
            cells = self._row(html, label)
            self.assertEqual(cells.count('class="rng"'), 4, label)  # 1W/1M/YTD/52wk
            self.assertNotIn('class="num mut">—', cells)
        self.assertIn('<span class="nm" title="Fed Funds − 2Y">FF − 2Y</span>', html)
        self.assertNotIn(">Fed Funds − 2Y<", html)

    def test_board_calc_rows_without_diff_show_dash_ranges(self):
        from ui import home
        bundle = {"DFF": _full(3.88), "DGS2": _full(4.71)}
        with patch("data.live_rates.live_yields", return_value={}), \
             patch.object(home, "_rates_bundle", return_value=bundle):
            html = home._af_rates_table()
        cells = self._row(html, "FF − 2Y")
        self.assertIn('>-0.83</span>', cells)            # level still leg-diffed
        self.assertEqual(cells.count('class="rng"'), 0)   # never a guessed range


# ── UX-P1-03 ────────────────────────────────────────────────────────────

class TestCalendarLayout(unittest.TestCase):

    def test_release_cell_one_line_with_tooltip(self):
        from ui import macro
        cell = macro._release_cell({"event": 'Jobless Claims 4-Week "Avg" (Sep/19)',
                                    "impact": "High"})
        self.assertIn('title="Jobless Claims 4-Week &quot;Avg&quot; (Sep/19)"', cell)
        for rule in ("white-space:nowrap;", "overflow:hidden;",
                     "text-overflow:ellipsis;", "max-width:280px;"):
            self.assertIn(rule, cell)
        self.assertNotIn("white-space:normal", cell)
        self.assertIn(macro._ECON_IMPACT_TAG["High"], cell)

    def test_calendars_in_a_row_below_the_band(self):
        from ui import macro
        st = _st_mock()
        recent = [{"date": "2026-09-24", "datetime": "2026-09-24 14:00:00",
                   "event": "New Home Sales (Aug)", "actual": 684,
                   "estimate": 620.0, "previous": 607, "unit": "K",
                   "surprise": 64.0, "impact": "High"}]
        up = [{"date": "2026-09-26", "datetime": "2026-09-26 12:30:00",
               "event": "PCE Price Index", "previous": 2.6, "estimate": 2.7,
               "unit": "%", "impact": "Medium"}]
        order = []
        with patch.object(macro, "st", st), \
             patch("data.econ_calendar.get_recent_releases", return_value=recent), \
             patch("data.econ_calendar.get_upcoming_releases", return_value=up), \
             patch.object(macro, "_cached_print_board", return_value=[]), \
             patch.object(macro, "_render_macro_grid",
                          side_effect=lambda: order.append("grid")), \
             patch.object(macro, "_render_indicator_explorer"), \
             patch("ui.chrome.table_export"):
            st.markdown.side_effect = lambda *a, **k: order.append(str(a[0])[:40])
            macro._render_economy_calendar()
        specs = [c.args[0] for c in st.columns.call_args_list]
        self.assertEqual(specs, [[1.36, 2.3], 2])   # no third calendar column
        g = order.index("grid")
        self.assertGreater(order.index("**Latest releases & surprises**"), g)
        self.assertGreater(order.index("**Upcoming releases**"), g)


# ── UX-P1-04 ────────────────────────────────────────────────────────────

def _fmp(event, actual, estimate, previous, unit, date="2026-09-24 14:00:00"):
    return {"date": date, "country": "US", "event": event, "actual": actual,
            "estimate": estimate, "previous": previous, "impact": "High",
            "unit": unit}


class TestReleaseUnits(unittest.TestCase):

    def test_new_home_sales_live_row(self):
        from data.econ_calendar import parse_event
        from ui import macro
        p = parse_event(_fmp("New Home Sales (Aug)", 684, 0.62, 607, "M"))
        self.assertEqual(p["unit"], "K")
        self.assertEqual(p["estimate"], 620.0)          # 0.62M = 620K
        self.assertEqual(p["actual"], 684)
        self.assertEqual(p["previous"], 607)
        self.assertEqual(p["surprise"], 64.0)           # 684 − 620
        self.assertAlmostEqual(p["surprise_pct"], 64 / 620 * 100)
        self.assertEqual(macro._fmt_econ_val(p["actual"], p["unit"]), "684K")
        self.assertEqual(macro._fmt_econ_val(p["estimate"], p["unit"]), "620K")
        self.assertEqual(macro._fmt_econ_val(p["previous"], p["unit"]), "607K")
        self.assertIn(">+64K</span>", macro._econ_surprise_html(p))
        html = macro._recent_releases_table([p])
        self.assertNotIn("684M", html)
        self.assertNotIn("683.38", html)

    def test_consistent_rows_untouched_and_surprise_carries_unit(self):
        from data.econ_calendar import parse_event
        from ui import macro
        hs = parse_event(_fmp("Housing Starts (Aug)", 1.275, 1.31, 1.309, "M"))
        self.assertEqual((hs["unit"], hs["estimate"]), ("M", 1.31))
        self.assertIn(">-0.035M</span>", macro._econ_surprise_html(hs))
        jc = parse_event(_fmp("Jobless Claims 4-Week Average (Sep/19)",
                              202.25, 203, 204, "K"))
        self.assertIn(">-0.75K</span>", macro._econ_surprise_html(jc))
        pct = parse_event(_fmp("Building Permits MoM (Aug)", -2.1, -2.7, 4.3, "%"))
        self.assertIn(">+0.6pp</span>", macro._econ_surprise_html(pct))
        pts = parse_event(_fmp("S&P Global Manufacturing PMI (Sep)",
                               57, 53.6, 53.9, "Points"))
        self.assertIn(">+3.4 Points</span>", macro._econ_surprise_html(pts))

    def test_consensus_above_printed_scale(self):
        from data.econ_calendar import normalize_scale
        ev = {"actual": 0.684, "estimate": 620, "previous": 0.607, "unit": "K",
              "surprise": 0.684 - 620, "surprise_pct": None}
        out = normalize_scale(ev)
        self.assertEqual((out["unit"], out["estimate"]), ("M", 0.62))
        self.assertAlmostEqual(out["surprise"], 0.064)

    def test_upcoming_row_uses_previous(self):
        from data.econ_calendar import parse_event
        p = parse_event(_fmp("New Home Sales (Sep)", None, 0.65, 684, "M"))
        self.assertEqual((p["unit"], p["estimate"], p["previous"]), ("K", 650.0, 684))
        self.assertIsNone(p["surprise"])

    def test_idempotent(self):
        from data.econ_calendar import normalize_scale, parse_event
        p = parse_event(_fmp("New Home Sales (Aug)", 684, 0.62, 607, "M"))
        self.assertEqual(normalize_scale(p), p)

    def test_unresolvable_mismatch_drops_surprise(self):
        from data.econ_calendar import parse_event
        # off the K/M/B/T ladder: can't relabel → no cross-scale surprise
        p = parse_event(_fmp("X", 684, 0.62, 607, "%"))
        self.assertIsNone(p["surprise"])
        self.assertEqual((p["unit"], p["estimate"]), ("%", 0.62))
        # below the ladder's bottom rung
        self.assertIsNone(parse_event(_fmp("X", 684, 0.62, 607, "K"))["surprise"])
        # actual on another scale than previous (which matches the consensus)
        q = parse_event(_fmp("X", 684, 0.62, 0.607, "M"))
        self.assertIsNone(q["surprise"])
        self.assertEqual((q["unit"], q["estimate"]), ("M", 0.62))

    def test_cached_rows_normalized_on_read(self):
        """A payload cached before normalize_scale existed is served (any age
        on the render path) already on one scale."""
        from data import econ_calendar as ec
        stale = {"datetime": "2026-09-24 14:00:00", "date": "2026-09-24",
                 "event": "New Home Sales (Aug)", "actual": 684.0,
                 "estimate": 0.62, "previous": 607.0, "surprise": 683.38,
                 "surprise_pct": 110222.58, "impact": "High", "unit": "M",
                 "released": True}
        cached = {"cached_at": "2026-09-24T15:00:00", "events": [stale]}
        with patch("data.cache.get", return_value=cached):
            for only in (True, False):
                with patch("data.freshness.is_fresh", return_value=not only):
                    ev = ec.get_us_calendar(cache_only=only)[0]
                    self.assertEqual((ev["unit"], ev["estimate"], ev["surprise"]),
                                     ("K", 620.0, 64.0))


# ── UX-P1-06 ────────────────────────────────────────────────────────────

class TestYieldCurveLegend(unittest.TestCase):

    def test_legend_below_maturity_title(self):
        from ui import macro
        s = pd.DataFrame({"date": pd.date_range("2025-01-01", "2026-09-24", freq="D")})
        s["value"] = 4.0
        with patch.object(macro, "fetch_series", return_value=s), \
             patch("data.treasury_live.live_yields", return_value={}):
            fig = macro._fig_yield_curve()
        lay = fig.layout
        self.assertEqual(lay.xaxis.title.text, "Maturity")
        self.assertEqual(lay.legend.yref, "container")   # figure bottom edge,
        self.assertEqual(lay.legend.y, 0)                # not paper y=-0.18
        self.assertEqual(lay.legend.yanchor, "bottom")
        self.assertEqual(lay.legend.orientation, "h")
        self.assertGreaterEqual(lay.margin.b, 78)


if __name__ == "__main__":
    unittest.main()
