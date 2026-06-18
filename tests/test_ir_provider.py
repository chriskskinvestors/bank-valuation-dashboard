"""Tests for the IR earnings-release locator (data/ir_provider.py, increment 5a).

Pin the pure selection logic — finding the latest Item-2.02 8-K and choosing its
EX-99.1 exhibit — against fixtures. Network I/O (latest_earnings_release) is a
thin wrapper over these and isn't exercised here.
"""
import unittest

from data.ir_provider import (
    _latest_earnings_8k, _pick_ex99, _parse_index_html, _dash_accession,
    extract_capital_ratios, extract_pnl,
)


def _subs(forms, items, dates, accs, primaries=None):
    return {"filings": {"recent": {
        "form": forms, "items": items, "filingDate": dates,
        "accessionNumber": accs,
        "primaryDocument": primaries or ["d.htm"] * len(forms),
    }}}


class TestLatestEarnings8K(unittest.TestCase):
    def test_picks_first_8k_with_item_202_newest_first(self):
        subs = _subs(
            forms=["8-K", "8-K", "10-Q"],
            items=["7.01", "2.02,9.01", "2.02"],   # newest first; 2nd is the earnings 8-K
            dates=["2026-02-01", "2026-01-22", "2025-12-31"],
            accs=["0000000000-26-000002", "0000000000-26-000001", "x"],
        )
        got = _latest_earnings_8k(subs)
        self.assertEqual(got["accession"], "000000000026000001")
        self.assertEqual(got["filed_date"], "2026-01-22")

    def test_skips_8k_without_202(self):
        subs = _subs(["8-K"], ["7.01,9.01"], ["2026-01-22"], ["a-1"])
        self.assertIsNone(_latest_earnings_8k(subs))

    def test_ignores_10q_even_if_listed(self):
        # A 10-Q is not an 8-K; the 2.02 item filter must not match it.
        subs = _subs(["10-Q"], ["2.02"], ["2026-01-22"], ["a-1"])
        self.assertIsNone(_latest_earnings_8k(subs))

    def test_semicolon_separated_items(self):
        subs = _subs(["8-K"], ["2.02; 9.01"], ["2026-01-22"], ["a-1"])
        self.assertIsNotNone(_latest_earnings_8k(subs))

    def test_8k_amendment_counts(self):
        subs = _subs(["8-K/A"], ["2.02"], ["2026-01-22"], ["a-1"])
        self.assertIsNotNone(_latest_earnings_8k(subs))

    def test_no_filings_returns_none(self):
        self.assertIsNone(_latest_earnings_8k({}))

    def test_strips_dashes_from_accession(self):
        subs = _subs(["8-K"], ["2.02"], ["2026-01-22"], ["0001193125-26-000123"])
        self.assertEqual(_latest_earnings_8k(subs)["accession"], "000119312526000123")


class TestPickEx99(unittest.TestCase):
    def test_prefers_ex99_1_over_ex99_2(self):
        items = [
            {"name": "d8k.htm", "type": "8-K"},
            {"name": "slides.htm", "type": "EX-99.2"},
            {"name": "press.htm", "type": "EX-99.1"},
        ]
        self.assertEqual(_pick_ex99(items), "press.htm")

    def test_lowest_ex99_when_no_dot1(self):
        items = [{"name": "a.htm", "type": "EX-99.3"},
                 {"name": "b.htm", "type": "EX-99.2"}]
        self.assertEqual(_pick_ex99(items), "b.htm")

    def test_filename_fallback_when_types_missing(self):
        items = [{"name": "d8k.htm", "type": ""},
                 {"name": "dex991.htm", "type": ""}]
        self.assertEqual(_pick_ex99(items), "dex991.htm")

    def test_none_when_no_ex99(self):
        items = [{"name": "d8k.htm", "type": "8-K"},
                 {"name": "g.jpg", "type": "GRAPHIC"}]
        self.assertIsNone(_pick_ex99(items))

    def test_handles_ex99_without_dash(self):
        items = [{"name": "p.htm", "type": "EX99.1"}]
        self.assertEqual(_pick_ex99(items), "p.htm")

    def test_empty_list(self):
        self.assertIsNone(_pick_ex99([]))


class TestParseIndexHtml(unittest.TestCase):
    # A trimmed real JPMorgan -index.htm document table: the press release
    # (EX-99.1) and the data supplement (EX-99.2). The picker must prefer .1.
    _JPM_INDEX = '''
    <table class="tableFile" summary="Document Format Files">
    <tr><td>4</td><td>Press Release</td>
      <td><a href="/Archives/edgar/data/19617/000162828026024990/a1q26erfexhibit991narrative.htm">a1q26erfexhibit991narrative.htm</a></td>
      <td>EX-99.1</td><td>123456</td></tr>
    <tr><td>5</td><td>Financial Supplement</td>
      <td><a href="/Archives/edgar/data/19617/000162828026024990/a1q26erfex992supplement.htm">a1q26erfex992supplement.htm</a></td>
      <td>EX-99.2</td><td>654321</td></tr>
    </table>'''

    def test_extracts_ex99_rows_with_types(self):
        got = _parse_index_html(self._JPM_INDEX)
        names = {d["name"]: d["type"] for d in got}
        self.assertEqual(names["a1q26erfexhibit991narrative.htm"], "EX-99.1")
        self.assertEqual(names["a1q26erfex992supplement.htm"], "EX-99.2")

    def test_picker_selects_the_press_release_not_the_supplement(self):
        # The end-to-end fix: parse the index table, then pick EX-99.1.
        self.assertEqual(
            _pick_ex99(_parse_index_html(self._JPM_INDEX)),
            "a1q26erfexhibit991narrative.htm")

    def test_no_ex99_rows_returns_empty(self):
        html = '<tr><td><a href="/x/jpm-10q.htm">jpm-10q.htm</a></td><td>10-Q</td></tr>'
        self.assertEqual(_parse_index_html(html), [])


class TestExtractCapitalRatios(unittest.TestCase):
    # A ratios table (newest column first) PLUS prose restating each headline
    # (Standardized) ratio — the prose value is what gets surfaced, corroborated
    # by a matching table cell.
    _DOC = """
    <table>
      <tr><td>CET1 capital</td><td>9.96 %</td><td>10.81 %</td><td>10.43 %</td></tr>
      <tr><td>Tier 1 risk-based capital</td><td>10.86 %</td><td>11.87 %</td></tr>
      <tr><td>Total risk-based capital</td><td>12.56 %</td><td>13.50 %</td></tr>
      <tr><td>Tier 1 leverage</td><td>10.20 %</td><td>9.80 %</td></tr>
    </table>
    <p>CET1 capital ratio of 9.96%; Tier 1 capital ratio was 10.86%; total
    capital ratio of 12.56%; Tier 1 leverage ratio of 10.20%.</p>"""

    def test_all_four_prose_confirmed(self):
        r = extract_capital_ratios(self._DOC)
        self.assertEqual(r["cet1_ratio"], 9.96)
        self.assertEqual(r["t1_ratio"], 10.86)
        self.assertEqual(r["total_ratio"], 12.56)
        self.assertEqual(r["lev_ratio"], 10.20)

    def test_picks_standardized_via_prose_when_table_has_two_approaches(self):
        # Advanced T1 row (16.8) listed BEFORE Standardized (16.9); prose narrates
        # the Standardized 16.9, so that's what surfaces — not the first row.
        doc = """
        <table>
          <tr><td>CET1 capital</td><td>15.1 %</td></tr>
          <tr><td>Tier 1 capital - Advanced</td><td>16.8 %</td></tr>
          <tr><td>Tier 1 capital - Standardized</td><td>16.9 %</td></tr>
        </table>
        <p>CET1 capital ratio was 15.1% and the Tier 1 capital ratio was 16.9%.</p>"""
        r = extract_capital_ratios(doc)
        self.assertEqual(r["cet1_ratio"], 15.1)
        self.assertEqual(r["t1_ratio"], 16.9)   # Standardized, from prose

    def test_prose_value_not_in_table_is_dropped(self):
        # Prose says T1 11.50 but no table cell corroborates → T1 n/a (CET1 kept).
        doc = self._DOC.replace("Tier 1 capital ratio was 10.86%",
                                "Tier 1 capital ratio was 11.50%")
        r = extract_capital_ratios(doc)
        self.assertEqual(r["cet1_ratio"], 9.96)
        self.assertIsNone(r["t1_ratio"])

    def test_metric_only_in_table_not_prose_is_na(self):
        # Total is in the table but NOT narrated → n/a (we don't guess approach).
        doc = self._DOC.replace("total\n    capital ratio of 12.56%; ", "")
        self.assertIsNone(extract_capital_ratios(doc)["total_ratio"])

    def test_no_cet1_returns_all_none(self):
        doc = self._DOC.replace("CET1 capital ratio of 9.96%; ", "")
        self.assertTrue(all(v is None for v in extract_capital_ratios(doc).values()))

    def test_out_of_band_cet1_unconfirmed(self):
        doc = "<table><tr><td>CET1 capital</td><td>95.0 %</td></tr></table><p>CET1 ratio of 95.0%</p>"
        self.assertIsNone(extract_capital_ratios(doc)["cet1_ratio"])


class TestDashAccession(unittest.TestCase):
    def test_inserts_dashes(self):
        self.assertEqual(_dash_accession("000162828026024990"), "0001628280-26-024990")

    def test_short_input_unchanged(self):
        self.assertEqual(_dash_accession("123"), "123")


class TestExtractPnl(unittest.TestCase):
    def test_net_income_billion(self):
        self.assertEqual(extract_pnl("Net Income of $16.5 billion.")["net_income"], 16.5e9)

    def test_net_income_million(self):
        self.assertEqual(extract_pnl("Net income of $517 million")["net_income"], 517e6)

    def test_net_income_totaled(self):
        self.assertEqual(extract_pnl("net income totaled $189.2 million")["net_income"], 189.2e6)

    def test_available_to_common_excluded(self):
        # "available to common" is after-preferred — NOT total net income.
        r = extract_pnl("net income available to common shareholders of $128 million")
        self.assertIsNone(r["net_income"])

    def test_net_income_out_of_band(self):
        self.assertIsNone(extract_pnl("net income of $0.2 million")["net_income"])

    def test_diluted_eps_leading(self):
        self.assertEqual(extract_pnl("Diluted earnings per common share $1.65")["diluted_eps"], 1.65)

    def test_diluted_eps_trailing(self):
        # JPM/PNC house style: "Earnings per share - diluted $5.94".
        self.assertEqual(extract_pnl("Earnings per share - diluted $ 5.94")["diluted_eps"], 5.94)

    def test_diluted_eps_per_share_phrase(self):
        self.assertEqual(extract_pnl("$4.32 per diluted share")["diluted_eps"], 4.32)

    def test_basic_eps_not_grabbed(self):
        # No "diluted" anywhere → diluted EPS must be None (don't show basic).
        self.assertIsNone(extract_pnl("Earnings per common share: Basic $1.61")["diluted_eps"])

    def test_both_present(self):
        r = extract_pnl("Net Income of $1.8 billion, or Diluted EPS $4.32")
        self.assertEqual(r["net_income"], 1.8e9)
        self.assertEqual(r["diluted_eps"], 4.32)

    def test_net_income_max_over_segments(self):
        # Multi-segment bank: each segment says "Net income of $X"; the
        # consolidated total is the largest.
        txt = ("Net income of $3.1 billion in Consumer. Net income of $2.1 billion "
               "in Markets. Net income of $8.6 billion for the firm.")
        self.assertEqual(extract_pnl(txt)["net_income"], 8.6e9)

    def test_net_income_excludes_prior_period_comparison(self):
        # The higher prior-year figure is a comparison — must not win the max.
        txt = "net income of $5.0 billion, compared to net income of $9.0 billion a year ago"
        self.assertEqual(extract_pnl(txt)["net_income"], 5.0e9)

    def test_eps_change_not_grabbed(self):
        self.assertIsNone(extract_pnl("Diluted earnings per share increased $0.31")["diluted_eps"])

    def test_eps_accretion_impact_not_grabbed(self):
        self.assertIsNone(
            extract_pnl("was accretive to diluted earnings per share by $3.50")["diluted_eps"])


if __name__ == "__main__":
    unittest.main()
