"""ui/valuation_model.py table exports on the shared .xlsx exporter
(ui/export.py; owner directive 2026-09-22: every data table gets an Export
built from RAW numbers, never display strings).

The seven sites: Projected FCFE & Terminal Value, the CoE × terminal-growth
DCF grid, the ROATCE × CoE warranted-P/TBV grid, Bull / Base / Bear, the
tornado table, Peer Warranted P/TBV, and Model vs Consensus (metric-in-rows:
the upload's unit varies per row, so ``row_formats`` is keyed by the Metric
label and a Unit column carries the unit).

Expectations are HAND-COMPUTED from the model's formulas (analysis/dcf
docstrings), not read back from the engine. Fixture model (every number
below derives from it):

    base EPS $4.00, EPS growth 5%/yr, loans/share $300 growing 4%/yr,
    target CET1 10%, CoE 10%, terminal growth 2.5%, ROATCE 14%, price $50.

    EPS_t   = 4.00 × 1.05^t          → 4.20, 4.41, 4.6305, 4.862025, 5.10512625
    Δloans  = 300 × 1.04^(t−1) × .04 → 12, 12.48, 12.9792, 13.498368, 14.03830272
    FCFE_t  = EPS_t − Δloans × 0.10  → 3.00, 3.162, 3.33258, 3.5121882, 3.701295978
    PV(5y)  = Σ FCFE_t / 1.10^t      = 12.541397941
    payout* = 1 − 0.025 / 0.14       = 0.821428571 (terminal, sustainable-growth)
    EPS_6   = 5.10512625 × 1.025     = 5.23275440625
    TV      = EPS_6 × payout* / (0.10 − 0.025) = 57.3111196875
    PV(TV)  = TV / 1.10^5            = 35.585696262
    FV      = 12.541397941 + 35.585696262 = 48.127094203 ($/sh)

Stub discipline: the render modules' own ``st`` bindings are patched with a
PRIVATE stub per test (mock.patch.object); widgets return their default
``value`` (or a per-label override), the tab radio returns the pane under
test. Nothing here touches sys.modules or the shared package stub.

Run: PYTHONIOENCODING=utf-8 python -m unittest tests.test_export_sites_valuation
"""
from __future__ import annotations

import io
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from openpyxl import load_workbook  # noqa: E402

USD2, PCT, PCT1, X = "$#,##0.00", '0.00"%"', '0.0"%"', '0.00"x"'


def _noop(*a, **k):
    return None


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        return _noop


class _StubSt:
    """Private streamlit stand-in: inputs return their default value (or a
    per-label override in ``slider_values``), the lazy-tabs radio returns
    ``tab``, the consensus checkbox is off, everything else is a no-op."""

    session_state: dict = {}
    query_params: dict = {}
    column_config = types.SimpleNamespace(LinkColumn=lambda *a, **k: None)

    def __init__(self, tab=None, slider_values=None):
        self.tab = tab
        self.slider_values = slider_values or {}

    def __getattr__(self, name):
        return _noop

    def columns(self, spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Ctx() for _ in range(n)]

    def container(self, *a, **k):
        return _Ctx()

    expander = spinner = popover = empty = container

    def number_input(self, label, value=None, **k):
        return value

    def slider(self, label, min_value=None, max_value=None, value=None, step=None, **k):
        return self.slider_values.get(label, value)

    def radio(self, label, options, index=0, **k):
        return self.tab if self.tab is not None else options[index]

    def checkbox(self, *a, **k):
        return False

    def selectbox(self, label, options=None, **k):
        return options[0] if options else None


HIST = [{"REPDTE": "20260630", "EQTOT": 1_000_000, "INTAN": 200_000,
         "INTANGW": 150_000, "NETINC": 30_000, "LNLSNET": 5_000_000,
         "NIMY": 3.40, "EEFFR": 58.0, "ROA": 1.10, "NCLNLSR": 0.40}]
SEC = {"eps": 4.00, "shares_outstanding": 40_000_000}
DEFAULTS = {"base_eps": 4.00, "roatce_pct": 14.0, "tbvps": 40.0,
            "loan_growth_trailing_pct": 4.0, "payout_ratio": 0.40,
            "loans_per_share": 300.0, "shares": 40_000_000}
BASE_PARAMS = {
    "base_eps": 4.00, "eps_growth_rates": [0.05] * 5, "payout_ratio": 0.40,
    "loan_growth_rates": [0.04] * 5, "starting_loans_per_share": 300.0,
    "target_cet1_pct": 10.0, "cost_of_equity_pct": 10.0,
    "terminal_growth_pct": 2.5, "roatce_pct": 14.0,
}
PRICE = 50.0
FV = 48.12709420338897
PV_EXPLICIT = 12.54139794102489
PV_TERMINAL = 35.58569626236408
TV = 57.311119687499996
TERMINAL_EPS = 5.23275440625


class _ExportSite(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import ui.chrome, ui.export, ui.states, ui.tables  # noqa: E401
        import ui.valuation_model
        cls.V, cls.X = ui.valuation_model, ui.export
        cls._st_modules = (ui.valuation_model, ui.chrome, ui.states, ui.tables)

    def _bind(self, stub):
        self.calls = []
        fake_export_st = types.SimpleNamespace(
            download_button=lambda label, data, **k: self.calls.append((label, data, k)),
            container=lambda *a, **k: _Ctx())
        for mod, obj in [(m, stub) for m in self._st_modules] + [(self.X, fake_export_st)]:
            p = mock.patch.object(mod, "st", obj)
            p.start()
            self.addCleanup(p.stop)

    def _render_page(self, tab=None, slider_values=None, price=PRICE):
        """Drive render_valuation_model with the fixture model; the headline
        cards (ui.source_trace, not a table) are stubbed out."""
        self._bind(_StubSt(tab=tab, slider_values=slider_values))
        V = self.V
        with mock.patch.object(V, "_load_hist", lambda t: HIST), \
                mock.patch.object(V, "_load_sec", lambda t: SEC), \
                mock.patch.object(V, "_load_price", lambda t: price), \
                mock.patch.object(V, "_derive_defaults", lambda t, h, s: dict(DEFAULTS)), \
                mock.patch.object(V, "list_consensus", lambda t: []), \
                mock.patch.object(V, "get_name", lambda t: f"{t} Bancorp"), \
                mock.patch.object(V, "_render_valuation_headline", _noop), \
                mock.patch("data.consensus.list_consensus", lambda t: []):
            V.render_valuation_model("AAA")

    def _book(self, i=-1, n=None):
        if n is not None:
            self.assertEqual(len(self.calls), n, "unexpected number of Export controls")
        label, data, kw = self.calls[i]
        self.assertEqual(label, "Export")
        self.assertTrue(callable(data), "workbook must build lazily, on click")
        wb = load_workbook(io.BytesIO(data()))
        return wb, wb.worksheets[0], kw

    @staticmethod
    def _grid(ws):
        return [[c.value for c in r] for r in ws.iter_rows()]

    @staticmethod
    def _source(wb):
        return dict((r[0].value, r[1].value) for r in wb["Source"].iter_rows())

    def _assert_na(self, ws, coord):
        self.assertEqual(ws[coord].value, "n/a")
        self.assertTrue(ws[coord].font.italic)

    def _assert_model_provenance(self, src, table):
        """The shared assumption rows every model export must carry."""
        self.assertEqual(src["Page"],
                         f"Company Analysis › Valuation › Valuation Model › {table}")
        self.assertEqual(src["Ticker"], "AAA")
        self.assertEqual(src["Company"], "AAA Bancorp")
        self.assertIn("SEC companyfacts", src["Source"])
        self.assertIn("FDIC", src["Source"])
        self.assertIn("market data", src["Source"])
        self.assertEqual(src["FDIC data as of (REPDTE)"], "20260630")
        self.assertEqual(src["Price ($)"], PRICE)
        self.assertEqual(src["Base EPS ($, annual)"], 4.0)
        self.assertAlmostEqual(src["EPS growth (avg %, 5-yr)"], 5.0, places=9)
        self.assertAlmostEqual(src["Loan growth (avg %, 5-yr)"], 4.0, places=9)
        self.assertAlmostEqual(src["Payout ratio (%)"], 40.0, places=9)
        self.assertEqual(src["Starting loans / share ($)"], 300.0)
        self.assertEqual(src["Target CET1 (%)"], 10.0)
        self.assertEqual(src["Terminal growth (%)"], 2.5)
        self.assertEqual(src["ROATCE (normalized, %)"], 14.0)


class TestProjectedFcfe(_ExportSite):
    """Site 1: Projected FCFE & Terminal Value — always rendered, first
    export on the page (the default pane's grid export follows it)."""

    def test_per_share_projections_and_terminal_row(self):
        self._render_page()
        wb, ws, kw = self._book(i=0, n=2)
        self.assertEqual(kw["file_name"], "AAA_valuation_fcfe.xlsx")
        self.assertEqual(kw["key"], "exp_val_fcfe_AAA")
        g = self._grid(ws)
        self.assertEqual(g[0], ["Year", "Projected EPS ($/sh)", "FCFE / share ($/sh)",
                                "Terminal value ($/sh)"])
        self.assertEqual([r[0] for r in g[1:]], ["Y1", "Y2", "Y3", "Y4", "Y5", "Terminal"])
        for r, eps, fcfe in zip(g[1:6],
                                [4.2, 4.41, 4.6305, 4.862025, 5.10512625],
                                [3.0, 3.162, 3.33258, 3.5121882, 3.701295978]):
            self.assertAlmostEqual(r[1], eps, places=9)
            self.assertAlmostEqual(r[2], fcfe, places=9)
        for rr in range(2, 7):
            self._assert_na(ws, f"D{rr}")              # no TV on explicit years
        self.assertAlmostEqual(g[6][1], TERMINAL_EPS, places=9)
        self._assert_na(ws, "C7")                      # terminal FCFE not an output
        self.assertAlmostEqual(g[6][3], TV, places=9)
        for coord in ("B2", "C2", "B7", "D7"):
            self.assertEqual(ws[coord].number_format, USD2)
        src = self._source(wb)
        self._assert_model_provenance(src, "Projected FCFE & Terminal Value")
        self.assertEqual(src["Cost of equity (%)"], 10.0)
        self.assertAlmostEqual(src["Terminal payout ratio used (%)"], 82.14285714285714, places=9)
        self.assertAlmostEqual(src["PV of 5-yr FCFE ($/sh)"], PV_EXPLICIT, places=9)
        self.assertAlmostEqual(src["PV of terminal value ($/sh)"], PV_TERMINAL, places=9)
        self.assertAlmostEqual(src["Terminal / total (%)"], PV_TERMINAL / FV * 100, places=9)
        self.assertAlmostEqual(src["DCF fair value ($/sh)"], FV, places=9)
        self.assertIn("whole US dollars", src["Units"])

    def test_no_price_drops_the_price_row_never_a_zero(self):
        self._render_page(price=None)
        wb, ws, kw = self._book(i=0, n=2)
        self.assertNotIn("Price ($)", self._source(wb))


class TestDcfSensitivityGrid(_ExportSite):
    """Site 2: CoE × terminal growth. CoE slider forced to 6% so the grid
    spans CoE 4–8% × g 1.0–4.0% and the (CoE 4, g 4) cell is undefined."""

    def test_axis_column_cells_and_na_where_coe_le_g(self):
        self._render_page(tab="CoE × Terminal Growth",
                          slider_values={"Cost of equity (%)": 6.0})
        wb, ws, kw = self._book(n=2)
        self.assertEqual(kw["file_name"], "AAA_valuation_dcf_sensitivity.xlsx")
        self.assertEqual(kw["key"], "exp_val_dcf_sens_AAA")
        g = self._grid(ws)
        self.assertEqual(g[0], ["Cost of equity (%)", "FV @ g 1.0% ($/sh)",
                                "FV @ g 1.8% ($/sh)", "FV @ g 2.5% ($/sh)",
                                "FV @ g 3.2% ($/sh)", "FV @ g 4.0% ($/sh)"])
        self.assertEqual([r[0] for r in g[1:]], [4.0, 5.0, 6.0, 7.0, 8.0])
        # CoE 8%, g 4%: PV(5y) = Σ FCFE_t/1.08^t = 13.25290...; payout* =
        # 1 − .04/.14 = 0.714285714; TV = 5.10512625×1.04×0.714285714/(.08−.04)
        # = 94.79...; PV(TV) = TV/1.08^5 → FV = 77.760539715 (formula-derived).
        self.assertAlmostEqual(g[5][5], 77.76053971474751, places=9)
        self._assert_na(ws, "F2")                      # CoE 4% ≤ g 4% → undefined
        self.assertEqual(ws["A2"].number_format, PCT1)
        self.assertEqual(ws["B2"].number_format, USD2)
        self.assertEqual(ws["F6"].number_format, USD2)
        self.assertEqual(ws.freeze_panes, "B2")
        src = self._source(wb)
        self._assert_model_provenance(src, "CoE × Terminal Growth")
        self.assertEqual(src["Cost of equity (%)"], 6.0)
        self.assertEqual(src["Grid rows"], "cost of equity (%)")
        self.assertIn("percent units", src["Units"])

    def test_base_case_cell_equals_headline_fair_value(self):
        self._render_page(tab="CoE × Terminal Growth")
        wb, ws, kw = self._book(n=2)
        g = self._grid(ws)
        self.assertEqual(g[3][0], 10.0)
        self.assertAlmostEqual(g[3][3], FV, places=9)   # CoE 10%, g 2.5%


class TestWarrantedGrid(_ExportSite):
    """Site 3: ROATCE × CoE warranted price = (ROATCE − g)/(CoE − g) × TBV/sh.
    Also pins that this pane renders ALONE: its cell-color helper used to be
    defined inside pane 0, so the lazy tab raised UnboundLocalError."""

    def test_axis_and_cells(self):
        self._render_page(tab="ROATCE × CoE (Warranted P/TBV)")
        wb, ws, kw = self._book(n=2)
        self.assertEqual(kw["file_name"], "AAA_valuation_ptbv_sensitivity.xlsx")
        self.assertEqual(kw["key"], "exp_val_ptbv_sens_AAA")
        g = self._grid(ws)
        self.assertEqual(g[0], ["ROATCE (%)", "FV @ CoE 8.0% ($/sh)", "FV @ CoE 9.0% ($/sh)",
                                "FV @ CoE 10.0% ($/sh)", "FV @ CoE 11.0% ($/sh)",
                                "FV @ CoE 12.0% ($/sh)"])
        self.assertEqual([r[0] for r in g[1:]], [10.0, 12.0, 14.0, 16.0, 18.0])
        # ROATCE 14, CoE 10: (14−2.5)/(10−2.5) × 40 = 1.53333 × 40 = 61.3333
        self.assertAlmostEqual(g[3][3], 11.5 / 7.5 * 40, places=9)
        # ROATCE 10, CoE 12: 7.5/9.5 × 40 = 31.5789
        self.assertAlmostEqual(g[1][5], 7.5 / 9.5 * 40, places=9)
        # ROATCE 18, CoE 8: 15.5/5.5 × 40 = 112.7273
        self.assertAlmostEqual(g[5][1], 15.5 / 5.5 * 40, places=9)
        self.assertEqual(ws["A2"].number_format, PCT1)
        self.assertEqual(ws["D4"].number_format, USD2)
        src = self._source(wb)
        self._assert_model_provenance(src, "ROATCE × CoE (Warranted P/TBV)")
        self.assertEqual(src["TBV / share ($)"], 40.0)
        self.assertIn("(ROATCE − g) ÷ (CoE − g)", src["Cell"])


class TestScenarios(_ExportSite):
    """Site 4: Bull / Base / Bear with the slider-default adjustments
    (bull: EPS growth +2pp, CoE −1pp; bear: EPS growth −2pp, CoE +1.5pp)."""

    def test_scenario_rows_raw(self):
        self._render_page(tab="Bull / Base / Bear")
        wb, ws, kw = self._book(n=2)
        self.assertEqual(kw["file_name"], "AAA_valuation_scenarios.xlsx")
        self.assertEqual(kw["key"], "exp_val_scenarios_AAA")
        g = self._grid(ws)
        self.assertEqual(g[0], ["Scenario", "Fair Value ($/sh)", "Upside vs Price (%)",
                                "PV Explicit ($/sh)", "PV Terminal ($/sh)"])
        self.assertEqual([r[0] for r in g[1:]], ["Bull", "Base", "Bear"])
        # Base: the fixture model; upside = 48.1271/50 − 1 = −3.7458%
        self.assertAlmostEqual(g[2][1], FV, places=9)
        self.assertAlmostEqual(g[2][2], (FV / PRICE - 1) * 100, places=9)
        self.assertAlmostEqual(g[2][3], PV_EXPLICIT, places=9)
        self.assertAlmostEqual(g[2][4], PV_TERMINAL, places=9)
        # Bull (EPS g 7%, CoE 9%): PV(5y) 13.903430173, PV(TV) 47.230952441,
        # FV 61.134382613, upside 22.2688%
        self.assertAlmostEqual(g[1][1], 61.1343826132169, places=9)
        self.assertAlmostEqual(g[1][2], 22.268765226433796, places=9)
        self.assertAlmostEqual(g[1][3], 13.903430172572829, places=9)
        self.assertAlmostEqual(g[1][4], 47.23095244064407, places=9)
        # Bear (EPS g 3%, CoE 11.5%): PV(5y) 11.160826361, PV(TV) 25.172302295,
        # FV 36.333128656, upside −27.3337%
        self.assertAlmostEqual(g[3][1], 36.33312865626924, places=9)
        self.assertAlmostEqual(g[3][2], -27.333742687461527, places=9)
        self.assertAlmostEqual(g[3][3], 11.160826361421512, places=9)
        self.assertAlmostEqual(g[3][4], 25.172302294847725, places=9)
        self.assertEqual(ws["B2"].number_format, USD2)
        self.assertEqual(ws["C2"].number_format, PCT1)
        self.assertEqual(ws["D2"].number_format, USD2)
        self.assertEqual(ws["E2"].number_format, USD2)
        src = self._source(wb)
        self._assert_model_provenance(src, "Bull / Base / Bear")
        self.assertEqual(src["Bull EPS growth adjustment (pp)"], 2.0)
        self.assertEqual(src["Bull CoE adjustment (pp)"], -1.0)
        self.assertEqual(src["Bear EPS growth adjustment (pp)"], -2.0)
        self.assertEqual(src["Bear CoE adjustment (pp)"], 1.5)

    def test_no_price_means_upside_na(self):
        self._render_page(tab="Bull / Base / Bear", price=None)
        wb, ws, kw = self._book(n=2)
        for rr in (2, 3, 4):
            self._assert_na(ws, f"C{rr}")
        self.assertAlmostEqual(ws["B3"].value, FV, places=9)


class TestTornado(_ExportSite):
    """Site 5: the tornado table (driven directly with the fixture params)."""

    def test_rows_sorted_by_range_with_formula_checked_cases(self):
        self._bind(_StubSt())
        prov = self.V._model_provenance("AAA", "AAA Bancorp", PRICE, "20260630",
                                        BASE_PARAMS, "Tornado: Input Sensitivity")
        self.V._render_tornado_and_irr(BASE_PARAMS, PRICE, ticker="AAA", prov=prov)
        wb, ws, kw = self._book(n=1)
        self.assertEqual(kw["file_name"], "AAA_valuation_tornado.xlsx")
        self.assertEqual(kw["key"], "exp_val_tornado_AAA")
        g = self._grid(ws)
        self.assertEqual(g[0], ["Input", "Low-case FV ($/sh)", "Δ Low (%)",
                                "High-case FV ($/sh)", "Δ High (%)", "Range ($/sh)"])
        rows = {r[0]: r[1:] for r in g[1:]}
        self.assertEqual(len(rows), 6)
        ranges = [r[5] for r in g[1:]]
        self.assertEqual(ranges, sorted(ranges, reverse=True))
        # Cost of equity ±1pp: same FCFE stream discounted at 11% / 9%
        #   → 42.225956388 / 55.859562496; Δ vs base 48.127094203 =
        #   −12.2616% / +16.0668%; range 13.633606108
        coe = rows["Cost of equity (±1pp)"]
        self.assertAlmostEqual(coe[0], 42.22595638793234, places=9)
        self.assertAlmostEqual(coe[1], -12.261570978122938, places=9)
        self.assertAlmostEqual(coe[2], 55.859562495648525, places=9)
        self.assertAlmostEqual(coe[3], 16.066767421239938, places=9)
        self.assertAlmostEqual(coe[4], 13.633606107716183, places=9)
        # Terminal growth ±0.5pp → 47.183677017 / 49.189112407
        tg = rows["Terminal growth (±0.5pp)"]
        self.assertAlmostEqual(tg[0], 47.183677017218145, places=9)
        self.assertAlmostEqual(tg[2], 49.18911240724987, places=9)
        self.assertAlmostEqual(tg[4], 2.005435390031728, places=9)
        # Payout ratio does not enter FCFE → both cases equal the base, range 0
        po = rows["Payout ratio (±15pp)"]
        self.assertAlmostEqual(po[0], FV, places=9)
        self.assertAlmostEqual(po[2], FV, places=9)
        self.assertEqual(po[1], 0.0)
        self.assertEqual(po[3], 0.0)
        self.assertEqual(po[4], 0.0)
        self.assertEqual(ws["B2"].number_format, USD2)
        self.assertEqual(ws["C2"].number_format, PCT1)
        self.assertEqual(ws["F2"].number_format, USD2)
        src = self._source(wb)
        self._assert_model_provenance(src, "Tornado: Input Sensitivity")
        self.assertAlmostEqual(src["Base fair value ($/sh)"], FV, places=9)
        # IRR brackets: FV(9%) 55.86 > $50 > FV(10%) 48.13 → solves in (9, 10)
        self.assertGreater(src["Implied IRR at price (%)"], 9.0)
        self.assertLess(src["Implied IRR at price (%)"], 10.0)


class TestPeerWarranted(_ExportSite):
    """Site 6: peers ranked at CoE 10 / g 2.5 from the universe metrics cache."""

    PEERS = [
        {"ticker": "BBB", "roatce": 10.0, "ptbv_ratio": None, "tbvps": 20.0, "price": None},
        {"ticker": "AAA", "roatce": 14.0, "ptbv_ratio": 1.5, "tbvps": 40.0, "price": 60.0},
        {"ticker": "CCC", "roatce": None, "ptbv_ratio": 1.0, "tbvps": 10.0, "price": 10.0},
    ]

    def test_ratios_prices_na_and_subject_flag(self):
        self._bind(_StubSt())
        with mock.patch("data.cache.get", lambda k, *a, **kw: self.PEERS), \
                mock.patch("data.bank_mapping.get_name", lambda t: f"{t} Bancorp Holdings"):
            self.V._render_peer_warranted("AAA", 10.0, 2.5)
        wb, ws, kw = self._book(n=1)
        self.assertEqual(kw["file_name"], "AAA_valuation_peer_warranted.xlsx")
        self.assertEqual(kw["key"], "exp_val_peer_warranted_AAA")
        g = self._grid(ws)
        self.assertEqual(g[0], ["Subject bank", "Ticker", "Bank", "ROATCE (%)",
                                "Actual P/TBV (x)", "Warranted P/TBV (x)", "Price ($)",
                                "Fair Price ($/sh)", "Upside (%)"])
        # CCC has no ROATCE → excluded; AAA (has upside) ranks above BBB (none).
        self.assertEqual(len(g), 3)
        # AAA: (14−2.5)/(10−2.5) = 1.53333x; × $40 = $61.3333; vs $60 → +2.2222%
        self.assertEqual(g[1][:4], ["Yes", "AAA", "AAA Bancorp Holdings", 14.0])
        self.assertEqual(g[1][4], 1.5)
        self.assertAlmostEqual(g[1][5], 11.5 / 7.5, places=9)
        self.assertEqual(g[1][6], 60.0)
        self.assertAlmostEqual(g[1][7], 11.5 / 7.5 * 40, places=9)
        self.assertAlmostEqual(g[1][8], 2.2222222222222365, places=9)
        # BBB: 7.5/7.5 = 1.00x → $20 fair; no actual P/TBV, price or upside
        self.assertEqual(g[2][:4], ["No", "BBB", "BBB Bancorp Holdings", 10.0])
        self._assert_na(ws, "E3")
        self.assertEqual(g[2][5], 1.0)
        self._assert_na(ws, "G3")
        self.assertEqual(g[2][7], 20.0)
        self._assert_na(ws, "I3")
        self.assertEqual(ws["D2"].number_format, PCT1)
        self.assertEqual(ws["E2"].number_format, X)
        self.assertEqual(ws["F2"].number_format, X)
        self.assertEqual(ws["G2"].number_format, USD2)
        self.assertEqual(ws["H2"].number_format, USD2)
        self.assertEqual(ws["I2"].number_format, PCT1)
        self.assertEqual(ws.freeze_panes, "C2")
        src = self._source(wb)
        self.assertIn("Peer Warranted P/TBV", src["Page"])
        self.assertEqual(src["Ticker"], "AAA")
        self.assertEqual(src["Company"], "AAA Bancorp Holdings")
        self.assertIn("SEC companyfacts", src["Source"])
        self.assertIn("market data", src["Source"])
        self.assertEqual(src["Cost of equity (%)"], 10.0)
        self.assertEqual(src["Terminal growth (%)"], 2.5)
        self.assertEqual(src["Banks ranked"], 2)
        self.assertIn("raw ratio", src["Units"])


class TestModelVsConsensus(_ExportSite):
    """Site 7 — metric-in-rows: the row label picks the number format
    ($ → usd2, % → pct), the Unit column names it, Δ (%) is a column format."""

    AVAILABLE = [{"period": "2026Q2", "source": "F", "metric_count": 5}]
    CONSENSUS = {"metrics": [
        {"key": "eps", "name": "EPS", "value": 1.00, "unit": "$"},
        {"key": "nim", "name": "NIM", "value": 3.50, "unit": "%"},
        {"key": "nii", "name": "NII", "value": 150.0, "unit": "$M"},        # no counterpart
        {"key": "roatce", "name": "ROATCE", "value": 13.0, "unit": "%"},
        {"key": "npl_ratio", "name": "NPL ratio", "value": 0.0, "unit": "%"},
    ]}

    def test_row_formats_units_and_basis(self):
        self._bind(_StubSt())
        with mock.patch("data.consensus.list_consensus", lambda t: self.AVAILABLE), \
                mock.patch("data.consensus.compile_consensus", lambda t, p: self.CONSENSUS), \
                mock.patch.object(self.V, "get_name", lambda t: f"{t} Bancorp"):
            self.V._render_consensus_vs_model("AAA", [4.2, 4.41, 4.6305, 4.862025, 5.10512625],
                                               HIST[0])
        wb, ws, kw = self._book(n=1)
        self.assertEqual(kw["file_name"], "AAA_valuation_vs_consensus.xlsx")
        self.assertEqual(kw["key"], "exp_val_vs_consensus_AAA")
        g = self._grid(ws)
        self.assertEqual(g[0], ["Metric", "Unit", "Consensus", "Model / Actual", "Δ",
                                "Δ (%)", "Basis", "Verdict"])
        self.assertEqual([r[0] for r in g[1:]], ["EPS", "NIM", "ROATCE", "NPL ratio"])
        # EPS: model Y1 $4.20 annual ÷ 4 (quarterly period) = $1.05 vs $1.00
        #   → Δ +0.05, +5.0% → Above consensus
        self.assertEqual(g[1][1:4], ["$", 1.0, 1.05])
        self.assertAlmostEqual(g[1][4], 0.05, places=9)
        self.assertAlmostEqual(g[1][5], 5.0, places=9)
        self.assertIn("Model Year-1 EPS", g[1][6])
        self.assertEqual(g[1][7], "Above consensus")
        # NIM: FDIC NIMY 3.40 vs 3.50 → Δ −0.10, −2.857% → Below consensus
        self.assertEqual(g[2][1:4], ["%", 3.5, 3.4])
        self.assertAlmostEqual(g[2][4], -0.10, places=9)
        self.assertAlmostEqual(g[2][5], -2.857142857142857, places=9)
        self.assertIn("FDIC latest quarter", g[2][6])
        self.assertEqual(g[2][7], "Below consensus")
        # ROATCE actual = 2 × 30,000 (Q2 YTD annualized) ÷ (1,000,000 − 200,000)
        #   = 7.5% vs 13.0 → Δ −5.5, −42.308%
        self.assertEqual(g[3][1:4], ["%", 13.0, 7.5])
        self.assertAlmostEqual(g[3][4], -5.5, places=9)
        self.assertAlmostEqual(g[3][5], -42.30769230769231, places=9)
        # NPL: consensus 0 → Δ (%) undefined → n/a, never 0
        self.assertEqual(g[4][1:4], ["%", 0.0, 0.4])
        self.assertAlmostEqual(g[4][4], 0.4, places=9)
        self._assert_na(ws, "F5")
        # Formats: the row label drives C/D/E; the Δ (%) column format wins.
        for coord in ("C2", "D2", "E2"):
            self.assertEqual(ws[coord].number_format, USD2)
        for coord in ("C3", "D3", "E3", "C4"):
            self.assertEqual(ws[coord].number_format, PCT)
        self.assertEqual(ws["F2"].number_format, PCT1)
        self.assertEqual(ws["F3"].number_format, PCT1)
        self.assertEqual(ws["B2"].number_format, "General")     # Unit is text
        src = self._source(wb)
        self.assertIn("Model vs Consensus", src["Page"])
        self.assertEqual(src["Ticker"], "AAA")
        self.assertEqual(src["Company"], "AAA Bancorp")
        self.assertEqual(src["Consensus period"], "2026Q2")
        self.assertEqual(src["FDIC data as of (REPDTE)"], "20260630")
        self.assertEqual(src["Model Year-1 EPS ($, annual)"], 4.2)
        self.assertEqual(src["Period annualizer"], 4.0)
        self.assertIn("EQTOT − INTAN", src["Source"])
        self.assertIn("$M = millions", src["Value units"])


if __name__ == "__main__":
    unittest.main()
