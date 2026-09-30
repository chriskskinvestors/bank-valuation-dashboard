"""UX-review P1 fixes on the Company pages (docs/REVIEW-2026-09-24-ux.md).

UX-P1-13  Corporate Profile showed three "current" prices ($335.73 Market
          Data / $335.56 Valuation / $337.20 chart header) and two day
          changes (-0.53% / -0.58%) in one render. Every block now reads ONE
          quote, fetched once per render.
UX-P1-28  Financial Highlights printed "$0.87B" for a sub-$1B bank.
UX-P1-18  Price Targets by Window: "Last month · 0 · $0.00 · n/a".
UX-P1-16  Governance / People empty states leaked pipeline vocabulary.
UX-P1-15  Corporate Structure rendered JPM's whole tree (18,600 px).
UX-P1-23  Projected FCFE table was a plain st.dataframe.

Run: python -m unittest tests.test_ux_p1_company
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

import pandas as pd  # noqa: E402

import ui.bank_detail as bd  # noqa: E402
import ui.analyst_coverage as ac  # noqa: E402
import ui.corporate_structure as cs  # noqa: E402
import ui.corporate_governance as cg  # noqa: E402
import ui.people_summary as ps  # noqa: E402
import ui.valuation_model as vm  # noqa: E402
from utils.formatting import usd_compact_from_thousands  # noqa: E402


def _fake_st():
    """MagicMock streamlit whose columns() unpack and whose widgets return
    real values; every markdown (st or column) lands on fake.markdown."""
    fake = MagicMock()
    fake.columns.side_effect = lambda spec, **k: [fake] * (spec if isinstance(spec, int) else len(spec))
    fake.segmented_control.return_value = "1Y"
    fake.text_input.return_value = ""
    fake.toggle.return_value = False
    return fake


def _md(fake) -> list[str]:
    return [c.args[0] for c in fake.markdown.call_args_list if c.args]


# JPM as the review captured it: live quote $335.73 (-0.53%), metrics-row
# cached price $335.56 (-0.58%), chart's last bar $337.20.
_QUOTE = {"price": 335.73, "change": -1.79, "change_pct": -0.53,
          "close": 337.52, "open": 337.0, "high": 338.1, "low": 334.9,
          "volume": 5_000_000, "timestamp": 1790000000}
_HIST = pd.DataFrame({"date": pd.to_datetime(["2025-09-30", "2026-03-31", "2026-09-23"]),
                      "close": [300.0, 320.0, 337.20],
                      "high": [301.0, 321.0, 338.0], "low": [299.0, 319.0, 336.0],
                      "volume": [1e6, 1e6, 1e6]})


def _metrics_df(price=335.56, change_pct=-0.58):
    return pd.DataFrame([{"ticker": "JPM", "price": price, "change_pct": change_pct,
                          "market_cap": 9.2e11, "pe_ratio": 15.0, "eps": 22.0,
                          "ptbv_ratio": 2.9, "tbvps": 115.0, "pb_ratio": 2.4,
                          "bvps": 140.0, "dividend_yield": 1.8, "volume": 4e6}])


class TestOneQuotePerCorporateProfile(unittest.TestCase):
    """UX-P1-13: stub the quote source; every block on the Corporate Profile
    renders the same price and day change."""

    def _render(self, quote):
        fake = _fake_st()
        get_quote = MagicMock(return_value=quote)
        with patch.object(bd, "st", fake), \
             patch("data.fmp_client.get_quote", get_quote), \
             patch("data.fmp_client.get_history", return_value=_HIST), \
             patch.object(bd, "get_bank_info",
                          return_value={"name": "JPMorgan Chase", "cik": None,
                                        "fdic_cert": None}), \
             patch.object(bd, "get_ir_url", return_value=None), \
             patch.object(bd, "_prefetch_profile_data"), \
             patch.object(bd, "_render_valuation_panel"), \
             patch.object(bd, "_render_latest_activity"), \
             patch.object(bd, "price_chart", return_value=None), \
             patch("ui.chrome.title_bar"), \
             patch("ui.states.group_ratio_note", return_value=None):
            bd.render_corporate_profile("JPM", _metrics_df())
        return _md(fake), get_quote

    def _blocks(self, htmls):
        ledgers = [h for h in htmls if "ksk-ledger" in h]
        market = next(h for h in ledgers if "Market Data" in h)
        valuation = next(h for h in ledgers if ">Valuation<" in h)
        header = next(h for h in htmls if h.startswith("<div class='ovp-readout'>"))
        return market, valuation, header

    def test_every_block_shows_the_one_quote(self):
        htmls, get_quote = self._render(_QUOTE)
        self.assertEqual(get_quote.call_count, 1, "one quote fetch per render")
        market, valuation, header = self._blocks(htmls)
        for name, block in (("Market Data", market), ("Valuation", valuation),
                            ("price header", header)):
            self.assertIn("$335.73", block, name)
        for block in (market, valuation):
            self.assertIn("-0.53%", block)
        page = "".join(htmls)
        for stale in ("$335.56", "$337.20", "-0.58%"):
            self.assertNotIn(stale, page)
        # Header's period move is first bar → the quote: 335.73/300 - 1.
        self.assertIn(f"{(335.73 / 300.0 - 1) * 100:+.2f}%", header)
        self.assertTrue(any("quote as of" in h for h in htmls),
                        "the quote's time is stamped on the Sources line")

    def test_no_quote_falls_back_to_row_everywhere(self):
        # Frozen/empty quote → every block uses the metrics row, never a mix.
        htmls, _ = self._render({"price": None, "change": None, "change_pct": None,
                                 "timestamp": None})
        market, valuation, header = self._blocks(htmls)
        for block in (market, valuation, header):
            self.assertIn("$335.56", block)
        for block in (market, valuation):
            self.assertIn("-0.58%", block)
        page = "".join(htmls)
        self.assertNotIn("$335.73", page)
        self.assertNotIn("$337.20", page)
        self.assertFalse(any("quote as of" in h for h in htmls))

    def test_header_without_page_price_is_the_dated_last_bar(self):
        h = bd._price_header_html(_HIST, "JPM", None)
        self.assertIn("$337.20", h)
        self.assertIn("close Sep 23", h)

    def test_header_with_string_dates_from_cache(self):
        # Cached history round-trips dates as strings.
        hist = _HIST.assign(date=_HIST["date"].dt.strftime("%Y-%m-%d"))
        self.assertIn("$335.73", bd._price_header_html(hist, "JPM", 335.73))
        self.assertIn("close Sep 23", bd._price_header_html(hist, "JPM", None))

    def test_quote_time_label(self):
        # 2026-09-29 20:00 UTC = 4:00 PM EDT, not today → dated.
        ts = pd.Timestamp("2026-09-29 20:00", tz="UTC").timestamp()
        lbl = bd._quote_time_label({"price": 1.0, "timestamp": ts})
        self.assertIn("4:00 PM ET", lbl)
        self.assertIsNone(bd._quote_time_label({"price": None, "timestamp": ts}))
        self.assertIsNone(bd._quote_time_label({"price": 1.0, "timestamp": None}))


class TestFinancialHighlightsScaling(unittest.TestCase):
    """UX-P1-28: a sub-$1B bank's balances auto-scale to $M."""

    def test_sub_billion_reads_millions(self):
        dates = pd.date_range("2025-06-30", periods=5, freq="QE")
        hist = pd.DataFrame({"REPDTE": dates,
                             "ASSET": [850_000, 860_000, 865_000, 870_000, 871_800],
                             "DEP": [250_000] * 4 + [262_000],
                             "LNLSNET": [30_000] * 4 + [31_000],
                             "EQTOT": [90_000] * 5})
        fake = _fake_st()
        with patch.object(bd, "st", fake), \
             patch("data.loaders.load_fdic_hist_df", return_value=hist):
            bd._render_financial_highlights_table("BSBK", {"fdic_cert": 1})
        html = "".join(_md(fake))
        self.assertIn(usd_compact_from_thousands(871_800), html)
        self.assertTrue(usd_compact_from_thousands(871_800).endswith("M"))
        for bad in ("$0.87B", "$0.26B", "$0.03B"):
            self.assertNotIn(bad, html)


class TestPriceTargetWindows(unittest.TestCase):
    """UX-P1-18: a window with 0 targets has no average."""

    def test_zero_count_window_is_dashes(self):
        rows = ac._window_rows({"last_month_count": 0, "last_month_avg": 0.0,
                                "last_quarter_count": 3, "last_quarter_avg": 330.0,
                                "last_year_count": None, "last_year_avg": None,
                                "all_time_count": 10, "all_time_avg": 320.0}, 300.0)
        self.assertEqual(rows[0], ("Last month", "0", "—", "—"))
        self.assertEqual(rows[1][:3], ("Last quarter", "3", "$330.00"))
        self.assertIn("+10.0%", rows[1][3])        # 330/300 - 1
        self.assertEqual(rows[2], ("Last year", "—", "—", "—"))
        for r in rows:
            self.assertNotIn("$0.00", r)
            self.assertNotIn("n/a", r)

    def test_no_price_vs_price_is_dash(self):
        rows = ac._window_rows({"all_time_count": 10, "all_time_avg": 320.0}, None)
        self.assertEqual(rows[3], ("All time", "10", "$320.00", "—"))


class TestProxyEmptyStates(unittest.TestCase):
    """UX-P1-16: user-facing wording, no pipeline vocabulary."""
    MSG = "No proxy statement has been processed for this company yet."

    def test_governance(self):
        fake = _fake_st()
        with patch.object(cg, "st", fake), patch.object(cg, "title_bar"), \
             patch.object(cg, "get_cik", return_value=19617), \
             patch("data.governance.get_governance_provisions", return_value=None), \
             patch("data.sec_client.get_filing_info", return_value={}):
            cg.render_corporate_governance("JPM")
        infos = [c.args[0] for c in fake.info.call_args_list]
        self.assertIn(self.MSG, infos)
        self.assertFalse(any("summarizer" in i or "DEF 14A" in i for i in infos))

    def test_people_summary(self):
        fake = _fake_st()
        empty = MagicMock()
        with patch.object(ps, "st", fake), patch.object(ps, "title_bar"), \
             patch.object(ps, "get_cik", return_value=19617), \
             patch("data.people.get_proxy_people", return_value=None), \
             patch("data.people.get_insider_roster", return_value=[]), \
             patch("ui.states.empty_state", empty):
            ps.render_people_summary("JPM")
        first = empty.call_args_list[0]
        self.assertEqual(first.args[0], self.MSG)
        self.assertIn("Section 16", first.args[1])


def _rows(spec):
    """[(depth, name, is_subject)] → _flatten-shaped rows."""
    return [{"depth": d, "name": n, "type": "T", "location": "L",
             "ownership_pct": 100.0 if d else None, "relationship": None,
             "is_subject": s} for d, n, s in spec]


_TREE_ROWS = _rows([
    (0, "TOP HOLDCO", False),
    (1, "BANK NA", True),
    (2, "BANK SUB A", False),
    (3, "DEEP TRUST ALPHA", False),
    (3, "DEEP TRUST BETA", False),
    (1, "OTHER HOLDING", False),
    (2, "OTHER SUB", False),
    (3, "OTHER DEEP", False),
])


class TestCorporateStructureCollapse(unittest.TestCase):
    """UX-P1-15: collapsed below depth 2 by default, expand-all, name filter."""

    def names(self, rows):
        return [r["name"] for r in rows]

    def test_default_hides_below_depth_two(self):
        got = cs._visible_rows(_TREE_ROWS)
        self.assertEqual(self.names(got), ["TOP HOLDCO", "BANK NA", "BANK SUB A",
                                           "OTHER HOLDING", "OTHER SUB"])

    def test_expand_all_shows_everything(self):
        self.assertEqual(cs._visible_rows(_TREE_ROWS, expand_all=True), _TREE_ROWS)

    def test_filter_searches_whole_tree_with_ancestors(self):
        got = cs._visible_rows(_TREE_ROWS, query="  beta ")
        self.assertEqual(self.names(got), ["TOP HOLDCO", "BANK NA", "BANK SUB A",
                                           "DEEP TRUST BETA"])

    def test_filter_no_match_is_empty(self):
        self.assertEqual(cs._visible_rows(_TREE_ROWS, query="zzz"), [])

    def test_deep_subject_stays_visible_with_its_chain(self):
        rows = _rows([(0, "TOP", False), (1, "MID", False), (2, "LOW", False),
                      (3, "THE BANK", True), (1, "SIBLING", False)])
        self.assertEqual(self.names(cs._visible_rows(rows)),
                         ["TOP", "MID", "LOW", "THE BANK", "SIBLING"])

    def test_render_collapses_table_but_exports_full_tree(self):
        tree = {"entity": {"name": "TOP HOLDCO", "rssd": 1}, "as_of": "2026-06-30",
                "children": [{"entity": {"name": "BANK NA", "rssd": 2},
                              "ownership_pct": 100.0, "relationship": "Controlled",
                              "children": [{"entity": {"name": "SUB", "rssd": 3},
                                            "ownership_pct": 100.0, "children": [
                                                {"entity": {"name": "DEEP", "rssd": 4},
                                                 "ownership_pct": 100.0,
                                                 "children": []}]}]}]}
        fake = _fake_st()
        export = MagicMock()
        with patch.object(cs, "st", fake), patch.object(cs, "title_bar"), \
             patch.object(cs, "table_export", export), \
             patch.object(cs, "get_fdic_cert", return_value=628), \
             patch("data.fdic_client.get_rssd_for_cert", return_value=2), \
             patch("data.nic_client.get_parent", return_value=None), \
             patch("data.nic_client.get_org_hierarchy", return_value=tree):
            cs.render_corporate_structure("JPM")
        table = next(h for h in _md(fake) if "<table>" in h)
        self.assertEqual(table.count("<tr>") - 1, 3)   # header row excluded
        self.assertNotIn("DEEP", table)
        self.assertEqual(len(export.call_args.args[0]), 4)
        self.assertIn("DEEP", export.call_args.args[0]["Entity"].tolist())


class TestProjectedFcfeTable(unittest.TestCase):
    """UX-P1-23: house table; an absent terminal EPS is "—", not $0.00."""

    def test_display_rows(self):
        df = vm._fcfe_display_df([10.0, 11.0], [5.0, 5.5], None, 120.0)
        self.assertEqual(df.columns.tolist(), ["Year", "Projected EPS", "FCFE / share"])
        self.assertEqual(df["Year"].tolist(), ["Y1", "Y2", "Terminal"])
        self.assertEqual(df.iloc[0].tolist(), ["Y1", "$10.00", "$5.00"])
        self.assertEqual(df.iloc[2].tolist(), ["Terminal", "—", "$120.00"])

    def test_render_uses_ksk_table_not_dataframe(self):
        src = Path(vm.__file__).read_text(encoding="utf-8")
        start = src.index("#### Projected FCFE & Terminal Value")
        block = src[start:src.index("table_export(", start)]
        self.assertIn("ksk_table(df_cf)", block)
        self.assertNotIn("st.dataframe", block)


if __name__ == "__main__":
    unittest.main()
