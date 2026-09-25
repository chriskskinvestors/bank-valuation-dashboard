"""
UX-review P0 regressions (2026-09-25) — rates provenance + econ-calendar layout.

Pins:
  • UX-P0-02: Home's 2Y is FRED DGS2, never the CME 2Y yield FUTURE (2YY=F,
    a persistent 30–40 bp below the cash yield). LIVE_YIELD_SYMBOLS has no 2Y,
    and even a pre-change snapshot still carrying a "2Y" key cannot overlay
    the 2Y row or the 2Y − 10Y spread (which falls back to −T10Y2Y, pure FRED).
  • UX-P0-03: Funding & Deposits (caption level + as-of, "vs Fed Funds" spread,
    chart line) and Regime › Fed Path read DAILY DFF, not monthly FEDFUNDS.
  • UX-P0-01: the econ-calendar tables carry no separate Impact column (tag is
    inline after the release name) and sit in an overflow-x:auto wrapper, so
    they can never paint under the Key-indicators board.

Run:  python -m unittest tests.test_ux_p0_rates -v
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


def _an(level, **kw):
    base = {"level": level, "d1": level - 0.01, "w1": level - 0.05,
            "m1": level - 0.10, "ytd": level - 0.20, "lo": level - 0.50,
            "hi": level + 0.50}
    base.update(kw)
    return base


# FRED bundle: DGS2 4.71, DGS10 5.06, T10Y2Y 0.35 (all daily FRED).
_BUNDLE = {"DGS2": _an(4.71), "DGS10": _an(5.06), "T10Y2Y": _an(0.35)}
# A snapshot written BEFORE the fix: 2Y from 2YY=F (4.45, ~26 bp low).
_STALE_LY = {"10Y": [4.80, 4.78, 4.70], "2Y": [4.45, 4.44, 4.40]}


class TestHome2YFromFred(unittest.TestCase):

    def test_live_symbols_have_no_2y_future(self):
        from data import live_rates
        self.assertNotIn("2Y", live_rates.LIVE_YIELD_SYMBOLS)
        self.assertNotIn("2YY=F", live_rates.LIVE_YIELD_SYMBOLS.values())

    def test_tenor_row_without_live_2y_resolves_dgs2(self):
        from ui import home
        an, is_live = home._af_row_anchors("tenor", "2Y", None, _BUNDLE,
                                           {"10Y": [4.80, 4.78, 4.70]})
        self.assertFalse(is_live)
        self.assertAlmostEqual(an["level"], 4.71)

    def test_spread_without_live_2y_is_pure_fred_t10y2y(self):
        from ui import home
        an, is_live = home._af_row_anchors("spread", "T10Y2Y", None, _BUNDLE,
                                           {"10Y": [4.80, 4.78, 4.70]})
        self.assertFalse(is_live)
        self.assertAlmostEqual(an["level"], -0.35)   # −T10Y2Y, no live 10Y mixed in
        self.assertAlmostEqual(an["d1"], -0.34)

    def test_board_ignores_stale_2y_in_snapshot(self):
        """End to end: a snapshot still carrying the 2YY=F 2Y must not reach
        the 2Y row or the 2Y − 10Y spread; the live 10Y still overlays."""
        from ui import home
        with patch("data.live_rates.live_yields", return_value=_STALE_LY), \
             patch.object(home, "_rates_bundle", return_value=_BUNDLE):
            html = home._af_rates_table()
        live_dot = 'title="live ~15m"></span>'
        self.assertIn('>2Y</span><span class="num">4.71</span>', html)
        self.assertNotIn(live_dot + "2Y</span>", html)
        self.assertNotIn(">4.45<", html)
        self.assertIn('>2Y − 10Y</span><span class="num">-0.35</span>', html)
        self.assertNotIn(live_dot + "2Y − 10Y</span>", html)
        # control: the live 10Y still overlays (dot + intraday level)
        self.assertIn(live_dot + '10Y</span><span class="num">4.80</span>', html)


def _series(vals_by_days_ago: dict) -> pd.DataFrame:
    end = pd.Timestamp("2026-09-24")
    rows = sorted((end - pd.Timedelta(days=d), v) for d, v in vals_by_days_ago.items())
    return pd.DataFrame({"date": [r[0] for r in rows], "value": [r[1] for r in rows]})


# DFF = daily effective (post 2026-09-17 hike); FEDFUNDS = monthly average.
_FRED = {
    "DFF": _series({200: 3.63, 7: 3.63, 0: 3.88}),
    "FEDFUNDS": _series({200: 3.63, 54: 3.63}),
}
_LATEST = {"DFF": 3.88, "FEDFUNDS": 3.63, "T10Y2Y": 0.35, "T10Y3M": 0.10,
           "BAMLH0A0HYM2": 3.10}


def _st_mock():
    st = MagicMock()
    st.columns.side_effect = lambda spec, *a, **k: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))]
    return st


def _rendered_text(st) -> str:
    out = []
    for name in ("markdown", "caption"):
        for c in getattr(st, name).call_args_list:
            if c.args:
                out.append(str(c.args[0]))
    return "\n".join(out)


class TestFedFundsIsDailyDff(unittest.TestCase):

    def _fred_patches(self, macro, seen):
        def fs(sid, years=5):
            seen.append(sid)
            return _FRED.get(sid, pd.DataFrame(columns=["date", "value"])).copy()

        def lv(sid):
            seen.append(sid)
            return _LATEST.get(sid)

        def ld(sid):
            seen.append(sid)
            df = _FRED.get(sid)
            return df["date"].iloc[-1] if df is not None else None

        return (patch.object(macro, "fetch_series", side_effect=fs),
                patch.object(macro, "latest_value", side_effect=lv),
                # create=True: pre-fix ui.macro had no latest_date import, so on
                # the old code this test fails on the FEDFUNDS assert, not setup.
                patch.object(macro, "latest_date", side_effect=ld, create=True))

    def test_funding_deposits_uses_dff(self):
        from ui import macro
        seen, st = [], _st_mock()
        rates = {"asof": "2026-09-15",
                 "savings": {"rate_pct": 0.40, "cap_pct": 1.15}}
        hist = [{"asof": "2026-09-15", "savings": {"rate_pct": 0.40}}]
        p1, p2, p3 = self._fred_patches(macro, seen)
        with p1, p2, p3, patch.object(macro, "st", st), \
             patch("data.national_rates.get_national_rates", return_value=rates), \
             patch("data.national_rates.get_national_rate_history", return_value=hist), \
             patch("ui.chrome.table_export"):
            macro._render_funding_deposits()
        self.assertNotIn("FEDFUNDS", seen)
        self.assertIn("DFF", seen)
        text = _rendered_text(st)
        self.assertIn("3.88% as of 2026-09-24", text)       # level + as-of date
        self.assertNotIn("3.63", text)
        self.assertIn("-3.48pp", text)                        # 0.40 − 3.88
        # chart's Fed Funds line is the DFF series (last point 3.88)
        fig = st.plotly_chart.call_args.args[0]
        ff_trace = [t for t in fig.data if t.name == "Fed Funds"][0]
        self.assertAlmostEqual(list(ff_trace.y)[-1], 3.88)

    def test_regime_fed_path_uses_dff(self):
        from ui import macro
        seen, st = [], _st_mock()
        rec = {"level": "low", "score": 10, "factors": []}
        p1, p2, p3 = self._fred_patches(macro, seen)
        with p1, p2, p3, patch.object(macro, "st", st), \
             patch.object(macro, "recession_probability", return_value=rec):
            macro._render_regime()
        self.assertNotIn("FEDFUNDS", seen)
        self.assertIn("DFF", seen)
        # 6-month change off DFF: 3.88 − 3.63 (the obs ≤ 180 days back)
        self.assertIn("Fed Funds 3.88% · +0.25pp / 6mo", _rendered_text(st))


_RECENT = [{"date": "2026-09-24", "event": "Initial Jobless Claims", "actual": 231,
            "estimate": 225, "previous": 228, "unit": "K", "surprise": 6,
            "impact": "High"}]
_UP = [{"date": "2026-09-26", "datetime": "2026-09-26 12:30:00",
        "event": "PCE Price Index", "previous": 2.6, "estimate": 2.7, "unit": "%",
        "impact": "Medium"}]


class TestEconCalendarLayout(unittest.TestCase):

    def _check(self, html, n_cols, event, tag):
        from ui import macro
        self.assertTrue(html.startswith('<div class="ksk-grid" style="overflow-x:auto;">'))
        heads = re.findall(r"<th[^>]*>([^<]*)</th>", html)
        self.assertNotIn("Impact", heads)
        self.assertEqual(len(heads), n_cols)
        cells = re.findall(r"<td[^>]*>.*?</td>", html.split("<tbody>", 1)[1])
        self.assertEqual(len(cells), n_cols)                  # cells == headers
        rel = [c for c in cells if event in c]
        self.assertEqual(len(rel), 1)
        self.assertIn(macro._ECON_IMPACT_TAG[tag], rel[0])   # tag inline in Release

    def test_recent_table(self):
        from ui import macro
        self._check(macro._recent_releases_table(_RECENT), 6,
                    "Initial Jobless Claims", "High")

    def test_upcoming_table(self):
        from ui import macro
        html = macro._upcoming_releases_table(_UP, "2026-09-26")
        self._check(html, 5, "PCE Price Index", "Medium")
        self.assertIn("rgba(30,64,175,0.04)", html)        # today tint kept

    def test_missing_impact_renders_no_tag(self):
        from ui import macro
        cell = macro._release_cell({"event": "A & B", "impact": None})
        self.assertIn("A &amp; B</td>", cell)


if __name__ == "__main__":
    unittest.main()
