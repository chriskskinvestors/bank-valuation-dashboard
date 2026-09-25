"""UX-review P0 misleading empty/zero states (2026-09-25). Each test pins the
exact failure the review saw:

  UX-P0-08  trend charts drew bare axes for an all-null series (ROA / NIMY are
            deliberately None for multi-charter groups like JPM) — now the
            empty series is skipped and a chart with nothing left says
            "Not reported for this bank" (no axes, no trace).
  UX-P0-15  every Company Reported leaf rendered a blank page for a bank that
            doesn't file with the SEC (OTC ALBY) — now one explained absence
            at the dispatch, and the CR renderer is never reached.
  UX-P0-13  BSBK (CBLR filer: IDT1CER=0, RWAJ=0 from FDIC) showed
            "CET1 Ratio 0.00%" — a stale fdic_hist cache entry bypassed
            null_unreported_capital; load_fdic_hist now scrubs every path.
  UX-P0-12  the 13F cards presented a 26-filer EDGAR search sample as the
            whole institutional base (JPM "Top 5 Concentration 90%") — now
            "13F Filers Found" + "Sample Coverage" (sampled shares ÷ shares
            outstanding), n/a when shares outstanding is unknown.

Run: PYTHONIOENCODING=utf-8 python -m unittest tests.test_ux_p0_empty_states -v
"""
import unittest
from unittest.mock import MagicMock, patch

from tests import _streamlit_stub

_streamlit_stub.install()

import pandas as pd  # noqa: E402

from ui.charts import (NOT_REPORTED_TEXT, grouped_trend_chart,  # noqa: E402
                       metrics_trend_chart)


def _hist(roa):
    return pd.DataFrame({
        "REPDTE": pd.to_datetime(["2025-09-30", "2025-12-31", "2026-03-31"]),
        "ROA": roa,
        "NIMY": [3.5, 3.7, 3.9],
    })


def _annotation_texts(fig):
    return [a.text for a in (fig.layout.annotations or ())]


class TestTrendChartEmptySeries(unittest.TestCase):
    """UX-P0-08."""

    def test_metrics_trend_all_none_is_not_reported(self):
        fig = metrics_trend_chart(_hist([None, None, None]), ["roaa"], "ROAA")
        self.assertEqual(len(fig.data), 0)
        self.assertIn(NOT_REPORTED_TEXT, _annotation_texts(fig))
        self.assertEqual(NOT_REPORTED_TEXT, "Not reported for this bank")
        # Axes hidden — bare axes are what read as a broken/zero chart.
        self.assertIs(fig.layout.xaxis.visible, False)
        self.assertIs(fig.layout.yaxis.visible, False)
        # Same height as the normal chart so the grid doesn't jump.
        normal = metrics_trend_chart(_hist([1.1, 1.2, 1.0]), ["roaa"], "ROAA")
        self.assertEqual(fig.layout.height, normal.layout.height)

    def test_metrics_trend_nan_is_null_too(self):
        fig = metrics_trend_chart(_hist([float("nan")] * 3), ["roaa"], "ROAA")
        self.assertEqual(len(fig.data), 0)
        self.assertIn(NOT_REPORTED_TEXT, _annotation_texts(fig))

    def test_metrics_trend_mixed_keeps_only_real_series(self):
        fig = metrics_trend_chart(_hist([None, None, None]), ["roaa", "nim"], "R&M")
        self.assertEqual(len(fig.data), 1)
        self.assertEqual(list(fig.data[0].y), [3.5, 3.7, 3.9])
        self.assertNotIn(NOT_REPORTED_TEXT, _annotation_texts(fig))
        self.assertFalse(fig.layout.showlegend)   # no legend entry for ROA

    def test_metrics_trend_partial_nulls_still_plot(self):
        # One reported quarter is data — only ALL-null series are skipped.
        fig = metrics_trend_chart(_hist([None, 1.2, None]), ["roaa"], "ROAA")
        self.assertEqual(len(fig.data), 1)
        self.assertNotIn(NOT_REPORTED_TEXT, _annotation_texts(fig))

    def test_grouped_trend_all_none_is_not_reported(self):
        fig = grouped_trend_chart(_hist([None, None, None]), ["roaa"], "Returns")
        self.assertEqual(len(fig.data), 0)
        self.assertIn(NOT_REPORTED_TEXT, _annotation_texts(fig))
        self.assertIs(fig.layout.xaxis.visible, False)

    def test_grouped_trend_mixed_keeps_only_real_series(self):
        fig = grouped_trend_chart(_hist([None, None, None]), ["roaa", "nim"], "Returns")
        self.assertEqual(len(fig.data), 1)
        from config import METRICS_BY_KEY
        self.assertEqual(fig.data[0].name, METRICS_BY_KEY["nim"]["label"])
        self.assertEqual(list(fig.data[0].y), [3.5, 3.7, 3.9])
        self.assertNotIn(NOT_REPORTED_TEXT, _annotation_texts(fig))

    def test_grouped_all_null_secondary_family_gets_no_axis(self):
        # An all-null % series next to a $ series must not claim a y2 axis.
        df = pd.DataFrame({
            "REPDTE": pd.to_datetime(["2025-12-31", "2026-03-31"]),
            "LNLSNET": [11_000_000, 11_300_000],
            "LNLSDEPR": [None, None],
        })
        fig = grouped_trend_chart(df, ["total_loans", "loans_to_deposits"], "L/D")
        self.assertEqual(len(fig.data), 1)
        self.assertNotIn("yaxis2", fig.layout.to_plotly_json())

    def test_empty_df_is_not_reported(self):
        for fig in (metrics_trend_chart(pd.DataFrame(), ["roaa"], "X"),
                    grouped_trend_chart(pd.DataFrame(), ["roaa"], "X")):
            self.assertEqual(len(fig.data), 0)
            self.assertIn(NOT_REPORTED_TEXT, _annotation_texts(fig))


class TestCompanyReportedNonSecFiler(unittest.TestCase):
    """UX-P0-15."""

    def _dispatch(self, cik, leaf):
        import ui.company_nav as nav
        calls = {"title": [], "empty": []}
        sentinel = MagicMock(name="cr_renderer")
        with patch("data.bank_mapping.get_cik", return_value=cik), \
             patch("data.bank_mapping.get_name", return_value="Allegiance Test Bank"), \
             patch("ui.chrome.title_bar",
                   side_effect=lambda *a, **k: calls["title"].append(a)), \
             patch("ui.states.empty_state",
                   side_effect=lambda *a, **k: calls["empty"].append(a)), \
             patch.dict(nav._CR_RENDERERS, {leaf: sentinel}):
            ok = nav.render_company_subtab(leaf, "ALBY", {}, basis="Company Reported")
        return ok, sentinel, calls

    def test_every_cr_leaf_shows_explained_absence(self):
        from ui.company_nav import COMPANY_NAV
        leaves = COMPANY_NAV["Financials"]["Company Reported"]
        self.assertEqual(len(leaves), 12)
        for leaf in leaves:
            with self.subTest(leaf=leaf):
                ok, sentinel, calls = self._dispatch(None, leaf)
                self.assertTrue(ok)
                sentinel.assert_not_called()
                self.assertEqual(calls["title"], [
                    ("Allegiance Test Bank (ALBY)", f"{leaf} — Company Reported")])
                self.assertEqual(len(calls["empty"]), 1)
                title, hint = calls["empty"][0]
                self.assertEqual(title, "ALBY does not file with the SEC")
                self.assertIn("Templated", hint)

    def test_sec_filer_still_reaches_renderer(self):
        ok, sentinel, calls = self._dispatch(1234567, "Income Statement")
        self.assertTrue(ok)
        sentinel.assert_called_once_with("ALBY", {})
        self.assertEqual(calls["empty"], [])

    def test_templated_basis_never_checks_cik(self):
        import ui.company_nav as nav
        sentinel = MagicMock(name="tpl_renderer")
        with patch("data.bank_mapping.get_cik") as gc, \
             patch.dict(nav._RENDERERS, {"Income Statement": sentinel}):
            ok = nav.render_company_subtab("Income Statement", "ALBY", {},
                                           basis="Templated")
        self.assertTrue(ok)
        sentinel.assert_called_once()
        gc.assert_not_called()

    def test_unknown_cr_leaf_still_returns_false(self):
        import ui.company_nav as nav
        with patch("data.bank_mapping.get_cik", return_value=None):
            self.assertFalse(nav.render_company_subtab(
                "No Such Leaf", "ALBY", {}, basis="Company Reported"))


# BSBK 2026-06-30 as FDIC returns it (verified live): CBLR filer, no RWA.
_BSBK = {"REPDTE": "20260630", "IDT1CER": 0, "RWAJ": 0, "RBCRWAJ": 0,
         "RBCT1J": 134094}
_NORMAL = {"REPDTE": "20260630", "IDT1CER": 15.2, "RWAJ": 900000,
           "RBCRWAJ": 16.1, "RBCT1J": 134094}


class TestLoaderScrubsUnreportedCapital(unittest.TestCase):
    """UX-P0-13."""

    def test_stale_cache_hit_is_scrubbed(self):
        from data.loaders import load_fdic_latest
        cached = [dict(_BSBK)]
        with patch("data.cache.get", return_value=cached):
            rec = load_fdic_latest("BSBK")
        self.assertIsNone(rec["IDT1CER"])
        self.assertIsNone(rec["RBCRWAJ"])
        self.assertEqual(rec["RBCT1J"], 134094)
        # The shared cached object is never mutated.
        self.assertEqual(cached[0], _BSBK)

    def test_normal_record_unchanged(self):
        from data.loaders import load_fdic_latest
        with patch("data.cache.get", return_value=[dict(_NORMAL)]):
            rec = load_fdic_latest("XYZ")
        self.assertEqual(rec, _NORMAL)

    def test_no_cert_fallback_is_scrubbed(self):
        from data.loaders import load_fdic_hist
        with patch("data.cache.get", return_value=[dict(_BSBK)]), \
             patch("data.bank_mapping.get_fdic_cert", return_value=None):
            recs = load_fdic_hist("BSBK", min_quarters=8)
        self.assertIsNone(recs[0]["IDT1CER"])

    def test_live_fallback_is_scrubbed(self):
        from data.loaders import load_fdic_hist
        live = [dict(_BSBK)]
        with patch("data.cache.get", return_value=None), \
             patch("data.cache.put") as put, \
             patch("data.bank_mapping.get_fdic_cert", return_value=12345), \
             patch("data.cert_group.fetch_group_history", return_value=live):
            recs = load_fdic_hist("BSBK", min_quarters=1)
        self.assertIsNone(recs[0]["IDT1CER"])
        self.assertIsNone(recs[0]["RBCRWAJ"])
        put.assert_called_once()

    def test_deep_store_path_is_scrubbed(self):
        from data.loaders import load_fdic_hist
        deep = [dict(_BSBK, REPDTE=f"2026{q:02d}30") for q in range(1, 22)]
        with patch("data.fdic_history_store.deep_group_history", return_value=deep):
            recs = load_fdic_hist("BSBK", min_quarters=20, limit=40)
        self.assertEqual(len(recs), 21)
        self.assertTrue(all(r["IDT1CER"] is None for r in recs))


class TestSampleCoverage(unittest.TestCase):
    """UX-P0-12 — the pure helper, hand-computed."""

    def test_pct(self):
        from ui.ownership import sample_coverage_pct
        # JPM-like: 9,463,207 sampled of 2,700,000,000 → 0.350489...%
        self.assertAlmostEqual(sample_coverage_pct(9_463_207, 2_700_000_000),
                               0.35048915, places=6)
        self.assertEqual(sample_coverage_pct(250, 1000), 25.0)

    def test_missing_or_zero_shares_out_is_none(self):
        from ui.ownership import sample_coverage_pct
        self.assertIsNone(sample_coverage_pct(9_463_207, None))
        self.assertIsNone(sample_coverage_pct(9_463_207, 0))
        self.assertIsNone(sample_coverage_pct(9_463_207, -5))
        self.assertIsNone(sample_coverage_pct(None, 1000))


class TestOwnershipCards(unittest.TestCase):
    """UX-P0-12 — the rendered headline cards."""

    _HOLDERS = [
        {"filer_cik": "0000000001", "filer_name": "Alpha Capital", "shares": 600,
         "value_usd": 60_000, "date_filed": "2026-08-14", "change_status": "Added",
         "change_pct": 10.0},
        {"filer_cik": "0000000002", "filer_name": "Beta Advisors", "shares": 400,
         "value_usd": 40_000, "date_filed": "2026-08-13", "change_status": "New",
         "change_pct": None},
    ]

    def _render(self, cik, fundamentals):
        import ui.ownership as own
        captured = {}
        fake_st = MagicMock()
        with patch.object(own, "st", fake_st), \
             patch.object(own, "title_bar"), \
             patch.object(own, "table_export"), \
             patch.object(own, "get_name", return_value="Test Bancorp"), \
             patch.object(own, "fetch_institutional_holdings",
                          return_value=[dict(h) for h in self._HOLDERS]), \
             patch("data.bank_mapping.get_cik", return_value=cik), \
             patch("data.sec_client.get_latest_fundamentals",
                   return_value=fundamentals), \
             patch("ui.source_trace.render_traceable_cards",
                   side_effect=lambda cards, **k: captured.setdefault("cards", cards)):
            own.render_ownership("TEST")
        captions = " ".join(str(c.args[0]) for c in fake_st.caption.call_args_list)
        return {c["label"]: c for c in captured["cards"]}, captions

    def test_labels_and_coverage(self):
        cards, captions = self._render(42, {"shares_outstanding": 10_000})
        self.assertEqual(list(cards), ["13F Filers Found", "Shares Held (filers found)",
                                       "Value (filers found)", "Sample Coverage"])
        self.assertNotIn("Top 5 Concentration", cards)
        self.assertEqual(cards["13F Filers Found"]["value"], "2")
        self.assertIn("capped at 30", cards["13F Filers Found"]["calc"]["definition"])
        # 1,000 sampled shares ÷ 10,000 outstanding × 100 = 10.00%
        cov = cards["Sample Coverage"]
        self.assertEqual(cov["value"], "10.00%")
        self.assertEqual([t["val"] for t in cov["calc"]["terms"]], ["1,000", "10,000"])
        self.assertIn("found via SEC EDGAR full-text search", captions)
        self.assertIn("a sample, not the full holder list", captions)
        self.assertNotIn("Top institutional holders", captions)

    def test_coverage_na_without_shares_outstanding(self):
        cards, _ = self._render(42, {"shares_outstanding": None})
        self.assertEqual(cards["Sample Coverage"]["value"], "n/a")
        cards, _ = self._render(None, {"shares_outstanding": 10_000})   # no CIK
        self.assertEqual(cards["Sample Coverage"]["value"], "n/a")


if __name__ == "__main__":
    unittest.main()
