"""Absent input → displayed 0 / crash pins (REVIEW-2026-09-24 P0 SUSPICIOUS
table + side findings). Each test reproduces the exact pre-fix failure:

1. compute_yoy_growth summed a GAPPED 4-row window (no cadence check) into a
   YoY dividend/buyback/DPS growth %.
2. _derive_defaults treated absent EQTOT as 0 → NEGATIVE TBV/share seed.
3. Capital-return chart drew an unknown quarter's dividends as a $0 bar.
4. ui.charts._b turned an absent FDIC field into 0.0, shifting its value into
   the composition donut's residual slice.
5. Earnings surprise chart labeled a quarter with no surprise data "+0.0%".
6. present-None values into format specs (current NIM; one-known shareholder
   yield component) raised TypeError and blanked the section.

FDIC zeros are REAL values — only None/NaN is unknown; the clean-path tests
pin that a reported 0 still renders as 0.
"""
import contextlib
import unittest
from unittest import mock

from tests import _streamlit_stub
_streamlit_stub.install()

import pandas as pd  # noqa: E402


def _st_fake():
    st = mock.MagicMock()
    st.columns.side_effect = lambda spec, **k: [
        mock.MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))]
    return st


# ── 1. compute_yoy_growth: gapped window → None ─────────────────────────────
class TestYoyGrowthGapRule(unittest.TestCase):
    CLEAN = ["2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30",
             "2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30"]
    # Last 4 rows skip 2025-06-30: 2025-03-31 → 2025-09-30 is 183 days.
    GAPPED = ["2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30",
              "2024-12-31", "2025-03-31", "2025-09-30", "2025-12-31"]

    def _tl(self, ends):
        return pd.DataFrame({
            "end": ends,
            "dividends_q": [100.0] * 4 + [110.0] * 4,
            "buybacks_q": [50.0] * 4 + [30.0] * 4,
            "dps_declared": [0.25] * 4 + [0.30] * 4,
        })

    def test_clean_series_hand_computed(self):
        from analysis.capital_return import compute_yoy_growth
        g = compute_yoy_growth(self._tl(self.CLEAN))
        # dividends 440 / 400 − 1 = +10%; buybacks 120 / 200 − 1 = −40%;
        # total (440+120)/(400+200) − 1 = 560/600 − 1 = −6.667%;
        # DPS 1.20 / 1.00 − 1 = +20%.
        self.assertAlmostEqual(g["dividends_yoy_pct"], 10.0)
        self.assertAlmostEqual(g["buybacks_yoy_pct"], -40.0)
        self.assertAlmostEqual(g["total_return_yoy_pct"], -20.0 / 3)
        self.assertAlmostEqual(g["dps_yoy_pct"], 20.0)

    def test_gapped_window_yields_none(self):
        from analysis.capital_return import compute_yoy_growth
        g = compute_yoy_growth(self._tl(self.GAPPED))
        for k in ("dividends_yoy_pct", "buybacks_yoy_pct",
                  "total_return_yoy_pct", "dps_yoy_pct"):
            self.assertIsNone(g[k], k)


# ── 2. _derive_defaults: absent EQTOT → tbvps None ──────────────────────────
class TestDeriveDefaultsTbvpsEqtot(unittest.TestCase):
    """No SEC TBV/share -> None, whatever FDIC carries: the FDIC bank-sub /
    holdco-shares fallback is gone (REVIEW 2026-10-02 P0-1) — absent EQTOT
    can no longer produce a negative TBV, present EQTOT no longer a guess."""

    def setUp(self):
        from tests.test_tbv_conventions import _passthrough_resolvers
        _passthrough_resolvers(self)

    def test_absent_eqtot_is_none_not_negative(self):
        from ui.valuation_model import _derive_defaults
        rec = {"REPDTE": "20250630", "INTAN": 50_000}
        d = _derive_defaults("X", [rec], {"shares_outstanding": 1e6})
        self.assertIsNone(d["tbvps"])

    def test_present_eqtot_without_sec_tbvps_is_none(self):
        from ui.valuation_model import _derive_defaults
        rec = {"REPDTE": "20250630", "EQTOT": 1_000_000, "INTAN": 50_000}
        d = _derive_defaults("X", [rec], {"shares_outstanding": 1e6})
        self.assertIsNone(d["tbvps"])


# ── 3 + 6b. capital-return attribution render ───────────────────────────────
class TestCapitalReturnAttributionRender(unittest.TestCase):
    def _run(self, timeline, yld):
        import ui.capital_dynamics as cd
        st = _st_fake()
        ledgers = []
        result = {"timeline": timeline, "ttm": {}, "growth": {}, "yield": yld,
                  "dividend_source": "common-specific"}
        with mock.patch.object(cd, "st", st), \
                mock.patch.object(cd, "_skeleton", contextlib.nullcontext), \
                mock.patch.object(cd, "ledger",
                                  lambda title, rows, *a, **k: ledgers.append((title, rows))), \
                mock.patch.object(cd, "table_export", lambda *a, **k: None), \
                mock.patch.object(cd, "get_name", lambda t: "Test Bank"), \
                mock.patch("data.bank_mapping.get_cik", return_value=1), \
                mock.patch("data.cache.get", return_value=None), \
                mock.patch("analysis.capital_return.summarize_capital_return",
                           return_value=result):
            cd._render_capital_return_attribution("X")
        figs = [c.args[0] for c in st.plotly_chart.call_args_list]
        return figs, ledgers

    def _timeline(self):
        ends = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
        return pd.DataFrame({
            "date": pd.to_datetime(ends), "end": ends,
            "year": [2025] * 4, "quarter": [1, 2, 3, 4],
            "net_income_q": [1e9, 1e9, 1e9, 1e9],
            # Q2 dividends unknown (buybacks known); Q4 buybacks a REAL 0.
            "dividends_q": [4e8, None, 4e8, 4e8],
            "buybacks_q": [2e8, 2e8, 2e8, 0.0],
            "total_returned_q": [6e8, 2e8, 6e8, 4e8],
            "payout_ratio_q": [0.4, None, 0.4, 0.4],
            "buyback_ratio_q": [0.2, 0.2, 0.2, 0.0],
            "total_return_ratio_q": [0.6, 0.2, 0.6, 0.4],
            "shares_outstanding": [1e8] * 4,
            "share_change_pct": [None, 0.0, 0.0, 0.0],
        })

    def test_unknown_dividend_quarter_draws_no_bar(self):
        figs, _ = self._run(self._timeline(), {})
        bars = {t.name: list(t.y) for t in figs[0].data}
        div = bars["Dividends"]
        self.assertTrue(pd.isna(div[1]), f"unknown Q2 dividend plotted as {div[1]}")
        self.assertEqual([div[0], div[2], div[3]], [0.4, 0.4, 0.4])  # $B
        # A reported $0 buyback quarter stays a real 0 bar.
        self.assertEqual(bars["Buybacks"], [0.2, 0.2, 0.2, 0.0])

    def test_one_known_yield_component_renders_na(self):
        yld = {"total_shareholder_yield_pct": 2.5, "dividend_yield_pct": None,
               "buyback_yield_pct": 2.5}
        _, ledgers = self._run(self._timeline(), yld)
        rows = dict(dict(ledgers)["Capital Return — TTM"])
        cell = rows["Shareholder Yield"]
        self.assertIn("2.50%", cell)
        self.assertIn("n/a div + 2.5% bb", cell)


# ── 4. ui.charts composition donuts ─────────────────────────────────────────
class TestChartsAbsentField(unittest.TestCase):
    BASE = {"REPDTE": "20250630", "ASSET": 10_000_000, "LNLSNET": 6_000_000,
            "SC": 2_000_000, "CHBAL": 1_000_000}

    def test_b_absent_is_none_real_zero_is_zero(self):
        from ui.charts import _b
        self.assertIsNone(_b(None))
        self.assertIsNone(_b(float("nan")))
        self.assertIsNone(_b("x"))
        self.assertEqual(_b(0), 0.0)
        self.assertEqual(_b(2_000_000), 2.0)

    def test_absent_securities_not_shifted_into_other(self):
        from ui.charts import asset_composition_chart
        rec = {k: v for k, v in self.BASE.items() if k != "SC"}
        fig = asset_composition_chart(pd.DataFrame([rec]))
        labels = [l for t in fig.data for l in (t.labels or [])]
        # Pre-fix: securities → 0, "Other" = 10 − 6 − 0 − 1 = $3B (absorbs $2B
        # of unknown securities). Unknown part → no composition at all.
        self.assertNotIn("Other", labels)
        self.assertIn("no data", fig.layout.title.text)

    def test_complete_record_hand_computed(self):
        from ui.charts import asset_composition_chart
        fig = asset_composition_chart(pd.DataFrame([self.BASE]))
        pie = fig.data[0]
        # Other = 10 − 6 − 2 − 1 = $1B
        self.assertEqual(dict(zip(pie.labels, pie.values)),
                         {"Net loans": 6.0, "Securities": 2.0,
                          "Cash & balances": 1.0, "Other": 1.0})

    def test_funding_mix_absent_nib_not_all_interest_bearing(self):
        from ui.charts import funding_mix_chart
        rec = {"REPDTE": "20250630", "DEP": 8_000_000, "EQTOT": 1_000_000,
               "LIAB": 9_000_000}  # DEPNIDOM absent
        fig = funding_mix_chart(pd.DataFrame([rec]))
        labels = [l for t in fig.data for l in (t.labels or [])]
        self.assertNotIn("Interest-bearing deposits", labels)


# ── 5. earnings surprise chart ──────────────────────────────────────────────
class TestEarningsSurpriseAbsent(unittest.TestCase):
    def test_absent_surprise_unlabeled_neutral(self):
        import ui.earnings as ue
        from utils.chart_style import COLOR_NEUTRAL, COLOR_SUCCESS, COLOR_DANGER
        st = _st_fake()
        history = [  # newest first
            {"date": "2025-10-15", "eps_estimate": 1.00, "eps_actual": 1.05,
             "surprise_pct": 5.0},
            {"date": "2025-07-15", "eps_estimate": 1.00, "eps_actual": 0.98},
            {"date": "2025-04-15", "eps_estimate": 1.00, "eps_actual": 0.90,
             "surprise_pct": -10.0},
        ]
        with mock.patch.object(ue, "st", st), \
                mock.patch.object(ue, "_render_surprise_history_grid",
                                  lambda *a, **k: None):
            ue._render_earnings_history_chart("X", {"earnings_history": history})
        fig = st.plotly_chart.call_args.args[0]
        self.assertEqual(list(fig.data[0].marker.color),
                         [COLOR_DANGER, COLOR_NEUTRAL, COLOR_SUCCESS])
        texts = [a.text for a in fig.layout.annotations]
        self.assertEqual(texts, ["-10.0%", "+5.0%"])  # no fabricated "+0.0%"


# ── 6a. rate-sensitivity phased inputs: NIM None ────────────────────────────
class TestRateSensitivityNimNone(unittest.TestCase):
    def _cards(self, nim):
        import ui.rate_sensitivity as rs
        captured = []
        inputs = {"current_nim_pct": nim, "earning_assets_usd": 9e9,
                  "securities_share": 0.25, "loans_share": 0.70}
        with mock.patch("data.bank_mapping.get_fdic_cert", return_value=None), \
                mock.patch("data.bank_mapping.get_name", return_value="Test Bank"), \
                mock.patch("ui.source_trace.render_traceable_cards",
                           lambda cards, **k: captured.extend(cards)):
            rs._render_phased_inputs("X", {"REPDTE": "20250630"}, inputs, 0.21)
        return {c["label"]: c["value"] for c in captured}

    def test_nim_none_renders_dash(self):
        self.assertEqual(self._cards(None)["Current NIM"], "—")

    def test_nim_present(self):
        self.assertEqual(self._cards(3.456)["Current NIM"], "3.46%")


if __name__ == "__main__":
    unittest.main()
