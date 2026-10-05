"""UX-review P2 polish on Company › Capital Adequacy (docs/REVIEW-2026-09-24-ux.md).

UX-P2-19  The Capital Generation waterfall's long one-line category labels
          ("Capital Returned (Dividends + Buybacks + AOCI)") were auto-rotated
          by Plotly and overlapped in the half-width 2×2 tile. Now short
          two-line labels, horizontal (tickangle 0), all four bars kept; the
          composition moved to the hover.
UX-P2-20  The FDIC statement iframe was sized 96 + 23/row, leaving a 150-300 px
          blank band under the Capital Adequacy table. Now 60 + 21/row.

Pure / stubbed — no network, no Streamlit server; read-only on app code.
Run: python -m unittest tests.test_ux_p2_capital -v
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

import pandas as pd  # noqa: E402

import ui.capital_dynamics as cd  # noqa: E402
import ui.financials_statements as FS  # noqa: E402


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _columns(spec, *a, **k):
    n = spec if isinstance(spec, int) else len(list(spec))
    return [_Ctx() for _ in range(n)]


class TestCapitalWaterfallLabels(unittest.TestCase):
    """UX-P2-19: the waterfall renders through render_capital_dynamics with a
    fixture summary; the figure is captured from the module's own st."""

    # $K: prior equity 1,000,000 → current 1,020,000; NI 30,000; capital
    # returned = NI − ΔEquity = 30,000 − 20,000 = 10,000.
    DATES = [pd.Timestamp("2026-03-31"), pd.Timestamp("2026-06-30")]
    TIMELINE = pd.DataFrame({
        "date": DATES,
        "cet1_pct": [11.0, 11.2],
        "tbv_per_share": [30.0, 30.5],
        "net_income_k_qtr": [28_000.0, 30_000.0],
        "capital_returned_k": [9_000.0, 10_000.0],
        "equity_k": [1_000_000.0, 1_020_000.0],
    })

    @classmethod
    def setUpClass(cls):
        latest = {"date": cls.DATES[-1], "equity_k": 1_020_000.0,
                  "net_income_k_qtr": 30_000.0, "capital_returned_k": 10_000.0}
        summary = {"timeline": cls.TIMELINE, "alerts": [], "latest": latest,
                   "buyback_capacity": {}}
        st = MagicMock()
        st.columns.side_effect = _columns
        with mock.patch.object(cd, "st", st), \
             mock.patch.object(cd, "_load_hist", lambda t: [{"REPDTE": "20260630"}]), \
             mock.patch.object(cd, "_load_shares", lambda t: 1e8), \
             mock.patch.object(cd, "_load_peer_cet1_median", lambda w: None), \
             mock.patch.object(cd, "summarize_bank_capital", lambda *a, **k: summary), \
             mock.patch.object(cd, "title_bar", lambda *a, **k: None), \
             mock.patch.object(cd, "get_name", lambda t: "Test Bancorp"), \
             mock.patch.object(cd, "range_picker", lambda *a, **k: "5Y"), \
             mock.patch.object(cd, "chart_timeline",
                               lambda ticker, rng, timeline, *a, **k: (timeline, None)), \
             mock.patch.object(cd, "holdco_capital_pointer_html", lambda t: ""), \
             mock.patch.object(cd, "_render_rcr_capital_walk", lambda *a, **k: None), \
             mock.patch.object(cd, "_render_capital_return_attribution",
                               lambda *a, **k: None), \
             mock.patch.object(FS, "render_capital_adequacy", lambda *a, **k: None):
            cd.render_capital_dynamics("TBNK")
        figs = {c.kwargs.get("key"): c.args[0] for c in st.plotly_chart.call_args_list}
        cls.fig = figs.get("cap_f4_TBNK")

    def test_waterfall_rendered(self):
        self.assertIsNotNone(self.fig, "Capital Generation waterfall not rendered")

    def test_short_two_line_labels(self):
        x = list(self.fig.data[0].x)
        self.assertEqual(x, ["Start<br>Equity", "+ Net<br>Income",
                             "− Capital<br>Returned", "End<br>Equity"])
        for lab in x:
            lines = lab.split("<br>")
            self.assertEqual(len(lines), 2, lab)
            self.assertTrue(all(len(s) <= 10 for s in lines), lab)

    def test_horizontal_ticks(self):
        self.assertEqual(self.fig.layout.xaxis.tickangle, 0)

    def test_all_four_bars_kept_with_fixture_values(self):
        tr = self.fig.data[0]
        self.assertEqual(list(tr.measure),
                         ["absolute", "relative", "relative", "total"])
        # $K × 1000 / 1e9 → $B: 1.00 start, +0.03 NI, −0.01 returned, 1.02 end.
        for got, want in zip(tr.y, [1.00, 0.03, -0.01, 1.02]):
            self.assertAlmostEqual(got, want, places=9)
        self.assertEqual(len(tr.y), 4)
        # The composition of "Capital Returned" lives in the hover now.
        self.assertIn("dividends + buybacks + AOCI", tr.hovertext[2])


class TestStatementIframeHeight(unittest.TestCase):
    """UX-P2-20: render_statement sizes the iframe 60 + 21 per row, where rows
    = data rows + section-header rows + the column-header row."""

    def _height(self, spec, hist):
        heights = []
        st = types.SimpleNamespace(
            radio=lambda *a, **k: "Annual",
            markdown=lambda *a, **k: None, caption=lambda *a, **k: None,
            warning=lambda *a, **k: None, info=lambda *a, **k: None,
            spinner=lambda *a, **k: _Ctx(), container=lambda *a, **k: _Ctx(),
            columns=_columns)
        comps = types.SimpleNamespace(
            html=lambda h, **k: heights.append(k.get("height")))
        import data.loaders as loaders
        with mock.patch.object(FS, "st", st), \
             mock.patch.object(FS, "components", comps), \
             mock.patch.object(FS, "get_bank_info",
                               lambda t: {"name": "Test Bancorp",
                                          "fdic_cert": 4242, "cik": None}), \
             mock.patch.object(FS, "_render_statement_trends", lambda *a, **k: None), \
             mock.patch.object(FS, "_cr_export", lambda *a, **k: None), \
             mock.patch.object(loaders, "load_fdic_hist_df", lambda t, q: hist.copy()):
            FS.render_statement("TBNK", "stmt", "Statement", spec, trends=[])
        self.assertEqual(len(heights), 1, heights)
        return heights[0]

    HIST = pd.DataFrame([{"REPDTE": "2025-12-31", "ASSET": 4_091_390_000,
                          "DEP": 2_500_000_000, "EQTOT": 350_000_000,
                          "LNLSNET": 1_900_000_000}])

    def test_three_sections_one_row_each(self):
        spec = [("Assets", [("Total assets", "dollar", "ASSET")]),
                ("Liabilities", [("Total deposits", "dollar", "DEP")]),
                ("Equity", [("Total equity", "dollar", "EQTOT")])]
        # 3 data + 3 section + 1 header = 7 rows → 60 + 147 (was 96 + 161 = 257)
        self.assertEqual(self._height(spec, self.HIST), 207)

    def test_two_sections_four_rows(self):
        spec = [("Assets", [("Total assets", "dollar", "ASSET"),
                            ("Net loans", "dollar", "LNLSNET")]),
                ("Funding", [("Total deposits", "dollar", "DEP"),
                             ("Total equity", "dollar", "EQTOT")])]
        # 4 data + 2 section + 1 header = 7 rows → 207; one more data row
        # adds exactly 21 px.
        h7 = self._height(spec, self.HIST)
        spec[1][1].append(("Equity again", "dollar", "EQTOT"))
        h8 = self._height(spec, self.HIST)
        self.assertEqual((h7, h8), (207, 228))


if __name__ == "__main__":
    unittest.main()
