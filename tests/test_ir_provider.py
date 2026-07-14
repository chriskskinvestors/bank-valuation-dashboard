"""Tests for the IR earnings-release locator (data/ir_provider.py, increment 5a).

Pin the pure selection logic — finding the latest Item-2.02 8-K and choosing its
EX-99.1 exhibit — against fixtures. Network I/O (latest_earnings_release) is a
thin wrapper over these and isn't exercised here.
"""
import unittest

from data.ir_provider import (
    _latest_earnings_8k, _pick_ex99, _parse_index_html, _dash_accession,
    extract_capital_ratios, extract_pnl, _quarter_end_before,
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


class TestEarnings8kCandidates(unittest.TestCase):
    """9.01-only earnings 8-Ks (ASB, caught 2026-07-14): exhibit-bearing
    filings WITHOUT 2.02 are gated candidates; the scan stops at the first
    2.02 hit."""

    def test_gated_candidates_then_202_stop(self):
        from data.ir_provider import _earnings_8k_candidates
        subs = _subs(
            forms=["8-K", "8-K", "8-K", "8-K", "8-K"],
            items=["7.01,9.01", "5.07,8.01,9.01", "9.01", "2.02,9.01", "9.01"],
            dates=["2026-05-05", "2026-04-28", "2026-04-23", "2026-01-22",
                   "2025-10-20"],
            accs=["a-5", "a-4", "a-3", "a-2", "a-1"],
        )
        cands = _earnings_8k_candidates(subs)
        self.assertEqual([c["filed_date"] for c in cands],
                         ["2026-05-05", "2026-04-28", "2026-04-23",
                          "2026-01-22"])            # stops AT the 2.02
        self.assertEqual([c["gated"] for c in cands],
                         [True, True, True, False])

    def test_202_first_means_single_ungated_candidate(self):
        from data.ir_provider import _earnings_8k_candidates
        subs = _subs(["8-K"], ["2.02,9.01"], ["2026-07-14"], ["a-1"])
        cands = _earnings_8k_candidates(subs)
        self.assertEqual(len(cands), 1)
        self.assertFalse(cands[0]["gated"])

    def test_non_exhibit_8ks_never_candidate(self):
        from data.ir_provider import _earnings_8k_candidates
        subs = _subs(["8-K"], ["5.02"], ["2026-07-01"], ["a-1"])
        self.assertEqual(_earnings_8k_candidates(subs), [])


class TestEarningsHeadlineGate(unittest.TestCase):
    def test_real_earnings_headline_passes(self):
        from data.ir_provider import _is_earnings_headline
        self.assertTrue(_is_earnings_headline(
            "NEWS RELEASE Investor Contact: ... Associated Banc-Corp Reports "
            "First Quarter 2026 Net Income of $200 million"))
        self.assertTrue(_is_earnings_headline(
            "FFIN Reports Fourth Quarter and Full Year Earnings"))

    def test_annual_meeting_release_refused(self):
        # "Announces Results of Annual Meeting" has no quarter word (ASB
        # 4/28 — passed a looser two-signal gate during rollout).
        from data.ir_provider import _is_earnings_headline
        self.assertFalse(_is_earnings_headline(
            "Associated Banc-Corp Announces Results of Annual Meeting of "
            "Shareholders"))

    def test_investor_deck_refused(self):
        from data.ir_provider import _is_earnings_headline
        self.assertFalse(_is_earnings_headline(
            "Associated Banc-Corp 2026 Fixed Income Investor Presentation "
            "May 5, 2026 Important Disclosures Forward-looking statements"))

    def test_dividend_declaration_refused(self):
        from data.ir_provider import _is_earnings_headline
        self.assertFalse(_is_earnings_headline(
            "XYZ Bancorp Announces Quarterly Cash Dividend of $0.25 Per "
            "Share payable August 15"))


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


class TestQuarterEndBefore(unittest.TestCase):
    def test_q1_release(self):
        self.assertEqual(_quarter_end_before("2026-04-14"), "2026-03-31")

    def test_q4_release_january(self):
        self.assertEqual(_quarter_end_before("2026-01-23"), "2025-12-31")

    def test_q3_release(self):
        self.assertEqual(_quarter_end_before("2025-10-20"), "2025-09-30")

    def test_bad_date(self):
        self.assertIsNone(_quarter_end_before(""))


class TestDashAccession(unittest.TestCase):
    def test_inserts_dashes(self):
        self.assertEqual(_dash_accession("000162828026024990"), "0001628280-26-024990")

    def test_short_input_unchanged(self):
        self.assertEqual(_dash_accession("123"), "123")


class TestExtractPnl(unittest.TestCase):
    # Scoped to GAAP diluted EPS (net income was dropped — prose variant tar pit).
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

    def test_gaap_qualifier_in_label(self):
        self.assertEqual(
            extract_pnl("Diluted GAAP earnings per common share of $2.77")["diluted_eps"], 2.77)

    def test_word_order_diluted_common(self):
        self.assertEqual(
            extract_pnl("Earnings per diluted common share of $0.89.")["diluted_eps"], 0.89)

    def test_parenthetical_connector(self):
        # Short footnote/abbrev parenthetical between label and value is allowed.
        self.assertEqual(
            extract_pnl('fully diluted earnings per share ("EPS") was $0.82')["diluted_eps"], 0.82)

    def test_long_parenthetical_blocks_nongaap(self):
        # A LONG parenthetical (>8 chars, e.g. "(a non-GAAP measure)") must NOT be
        # bridged — it can hide a non-GAAP figure.
        self.assertIsNone(extract_pnl("diluted EPS (a non-GAAP measure) of $1.20")["diluted_eps"])

    def test_eps_change_not_grabbed(self):
        self.assertIsNone(extract_pnl("Diluted earnings per share increased $0.31")["diluted_eps"])

    def test_eps_accretion_impact_not_grabbed(self):
        self.assertIsNone(
            extract_pnl("was accretive to diluted earnings per share by $3.50")["diluted_eps"])

    def test_adjusted_eps_excluded(self):
        # Non-GAAP "adjusted" diluted EPS must not be surfaced as the GAAP level.
        self.assertIsNone(extract_pnl("adjusted diluted EPS of $0.67")["diluted_eps"])

    def test_core_eps_excluded(self):
        self.assertIsNone(extract_pnl("Core earnings per diluted share $1.32")["diluted_eps"])

    def test_gaap_kept_when_adjusted_also_present(self):
        # GAAP stated cleanly + adjusted stated separately → GAAP wins, adjusted dropped.
        txt = "Diluted EPS $0.52. Adjusted diluted EPS of $0.67."
        self.assertEqual(extract_pnl(txt)["diluted_eps"], 0.52)

    def test_disagreeing_candidates_return_na(self):
        # Current 2.15 and a prior-year 1.81 both read clean → ambiguous → n/a.
        txt = "diluted EPS was $2.15 this quarter; diluted EPS was $1.81 a year earlier."
        self.assertIsNone(extract_pnl(txt)["diluted_eps"])

    def test_agreeing_candidates_surface(self):
        # Same value in prose and table → confirmed.
        txt = "Diluted EPS of $1.13. ... Diluted earnings per share $1.13"
        self.assertEqual(extract_pnl(txt)["diluted_eps"], 1.13)

    def test_eps_reduction_impact_excluded(self):
        # "reduction in diluted EPS of $0.02" is an impact, not the level (GS).
        self.assertIsNone(extract_pnl("this was a reduction in diluted EPS of $0.02")["diluted_eps"])

    def test_per_share_impact_excluded(self):
        # "reduced earnings by $0.13 per diluted share" is an impact (LCNB).
        self.assertIsNone(
            extract_pnl("reduced after-tax earnings by $0.13 per diluted share")["diluted_eps"])

    def test_preferred_dividend_per_share_excluded(self):
        # "preferred dividends of $0.09 per diluted common share" is not EPS (BYFC).
        self.assertIsNone(
            extract_pnl("preferred dividends of $0.09 per diluted common share")["diluted_eps"])

    def test_trailing_excluding_qualifier(self):
        # GAAP was a LOSS (stated as "diluted loss per share"), and the positive
        # "diluted earnings per share of $0.59, excluding …" is the adjusted figure
        # — the trailing "excluding" marks it non-GAAP (BMRC).
        txt = ("diluted loss per share of $2.49 (diluted earnings per share of "
               "$0.59, excluding the goodwill charge)")
        self.assertIsNone(extract_pnl(txt)["diluted_eps"])

    def test_trailing_qualifier_only_same_clause(self):
        # GAAP $0.58 followed by "; adjusted net income …" is a NEW clause, so the
        # trailing check must NOT exclude $0.58 (it's not "$0.58 adjusted"). The
        # bug it fixes: $0.58 was wrongly dropped, leaving a stray $0.41 (BWB).
        self.assertEqual(
            extract_pnl("$0.58 per diluted common share; adjusted net income was higher"
                        )["diluted_eps"], 0.58)
        # And when a second clean value (different period, no qualifier) is also
        # present, the two disagree → n/a — never guess which is current.
        txt = ("net income of $17.4 million, or $0.58 per diluted common share; adjusted "
               "net income higher. Net income of $12.6 million, or $0.41 per diluted "
               "common share.")
        self.assertIsNone(extract_pnl(txt)["diluted_eps"])


class TestFreshCapitalGate(unittest.TestCase):
    """The freshest-wins gate on the preliminary capital callout: surface the
    release's ratios ONLY when it's genuinely ahead of the latest filing.
    Network deps (latest_earnings_release, the filing scraper) are monkeypatched;
    extraction itself is covered by TestExtractCapitalRatios."""
    # A release whose double-confirmed ratios extract cleanly (reuse the fixture).
    _REL = {"html": TestExtractCapitalRatios._DOC, "filed_date": "2026-04-20",
            "url": "http://example/release"}

    @staticmethod
    def _fact(period_end, members=()):
        from types import SimpleNamespace
        return SimpleNamespace(concept="us-gaap:Assets",
                               period_end=period_end, members=members)

    def setUp(self):
        import data.ir_provider as ip
        self.ip = ip
        self._orig_rel = ip.latest_earnings_release
        self._orig_pe = ip._latest_filing_period_end

    def tearDown(self):
        self.ip.latest_earnings_release = self._orig_rel
        self.ip._latest_filing_period_end = self._orig_pe

    def test_release_ahead_of_filing_surfaces_ratios(self):
        # Release covers Q1'26 (qend 2026-03-31); latest filing only through Q4'25.
        self.ip.latest_earnings_release = lambda cik: self._REL
        self.ip._latest_filing_period_end = lambda cik: "2025-12-31"
        out = self.ip._compute_fresh_capital(1)
        self.assertIsNotNone(out)
        self.assertEqual(out["quarter"], "2026-03-31")
        self.assertEqual(out["ratios"]["cet1_ratio"], 9.96)

    def test_filing_already_covers_quarter_suppressed(self):
        # Latest filing already reports through 2026-03-31 → no lead → n/a.
        self.ip.latest_earnings_release = lambda cik: self._REL
        self.ip._latest_filing_period_end = lambda cik: "2026-03-31"
        self.assertIsNone(self.ip._compute_fresh_capital(1))

    def test_unconfirmable_filing_period_suppressed(self):
        # Can't determine the latest filing's period → never show an unverifiable
        # "preliminary" ratio.
        self.ip.latest_earnings_release = lambda cik: self._REL
        self.ip._latest_filing_period_end = lambda cik: None
        self.assertIsNone(self.ip._compute_fresh_capital(1))

    def test_unconfirmed_cet1_suppressed(self):
        # No double-confirmed CET1 anchor in the release → nothing to surface
        # (filing period not even consulted).
        self.ip.latest_earnings_release = lambda cik: {
            "html": "<p>no ratios here</p>", "filed_date": "2026-04-20", "url": "u"}
        self.ip._latest_filing_period_end = lambda cik: "2025-12-31"
        self.assertIsNone(self.ip._compute_fresh_capital(1))


if __name__ == "__main__":
    unittest.main()
