"""Pin the release-metrics extractors (data/release_metrics.py, 2026-07-06).

Cardinal-rule tests on realistic earnings-release prose: happy paths per
metric, prior-period comparisons never captured (first-% discipline + before-
label qualifiers), non-GAAP variants excluded, disagreement → None, bands,
denominator pinning for credit ratios, and the ROE/ROTCE label separation.

Run: python -m unittest tests.test_release_metrics
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.release_metrics import extract_release_metrics, extract_table_metrics


def x(html: str) -> dict:
    return extract_release_metrics(html)


def _tbl(header_cells, *data_rows):
    """Build a fixture <table> from a header cell list + data-row cell lists."""
    def tr(cells):
        return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    return "<table>" + tr(header_cells) + "".join(tr(r) for r in data_rows) + "</table>"


class TestPercentMetrics(unittest.TestCase):
    def test_nim_with_trailing_comparison_not_captured(self):
        m = x("<p>Net interest margin of 3.42%, compared with 3.38% in the "
              "prior quarter and 3.19% a year ago.</p>")
        self.assertEqual(m["nim"], 3.42)

    def test_prior_period_restatement_before_label_excluded(self):
        m = x("<p>Net interest margin expanded to 3.42%, up from a net "
              "interest margin of 3.19% in the year-ago quarter.</p>")
        # "up from a …" disqualifies the second candidate; only 3.42 survives.
        self.assertEqual(m["nim"], 3.42)

    def test_two_clean_disagreeing_candidates_yield_none(self):
        m = x("<p>Net interest margin was 3.42%. Net interest margin was "
              "3.38%.</p>")
        self.assertIsNone(m["nim"])

    def test_adjusted_variant_excluded_gaap_survives(self):
        m = x("<p>The efficiency ratio was 55.3%; the adjusted efficiency "
              "ratio was 52.1%.</p>")
        self.assertEqual(m["efficiency"], 55.3)

    def test_roa_happy(self):
        self.assertEqual(x("<p>Return on average assets of 1.15%.</p>")["roa"],
                         1.15)

    def test_respectively_pair_maps_values_by_order(self):
        """CCFN 2026-07-21: 'Return on average assets and return on average
        equity were 1.70% and 14.65%, respectively' — first-%-after-label
        handed ROE the ROA value. Each label takes ITS position's value."""
        m = x("<p>Return on average assets and return on average equity were "
              "1.70% and 14.65%, respectively, for the second quarter.</p>")
        self.assertEqual(m["roa"], 1.70)
        self.assertEqual(m["roe"], 14.65)

    def test_roe_and_rotce_do_not_cross_match(self):
        m = x("<p>Return on average common equity was 12.4% and return on "
              "average tangible common equity was 15.2%.</p>")
        self.assertEqual(m["roe"], 12.4)
        self.assertEqual(m["rotce"], 15.2)

    def test_rotce_alone_never_fills_roe(self):
        m = x("<p>Return on average tangible common equity of 15.2%.</p>")
        self.assertEqual(m["rotce"], 15.2)
        self.assertIsNone(m["roe"])

    def test_nco_dollar_then_ratio_form(self):
        m = x("<p>Net charge-offs were $12.3 million, or 0.25% of average "
              "loans, annualized.</p>")
        self.assertEqual(m["nco_ratio"], 0.25)

    def test_rounded_duplicates_across_tables_agree(self):
        """CFG 2026-07-16: efficiency printed 61.1 in the headline table and
        61.08 in the detail table — the bank's own rounding must corroborate
        (most precise value wins), never poison the key to None."""
        from data.release_metrics import extract_table_metrics
        t1 = ("<table><tr><td>x</td><td>2Q26</td><td>1Q26</td><td>2Q25</td></tr>"
              "<tr><td>Efficiency ratio</td><td>61.1</td><td>63.6</td>"
              "<td>64.8</td></tr></table>")
        t2 = ("<table><tr><td>x</td><td>2Q26</td><td>1Q26</td><td>2Q25</td></tr>"
              "<tr><td>Efficiency ratio</td><td>61.08 %</td><td>63.55 %</td>"
              "<td>64.76 %</td></tr></table>")
        m = extract_table_metrics(t1 + t2, "2026-06-30")
        self.assertEqual(m["efficiency"], 61.08)

    def test_same_precision_conflict_still_refuses(self):
        from data.release_metrics import extract_table_metrics
        t1 = ("<table><tr><td>x</td><td>2Q26</td><td>1Q26</td></tr>"
              "<tr><td>Net interest margin</td><td>3.17 %</td><td>3.22 %</td>"
              "</tr></table>")
        t2 = ("<table><tr><td>x</td><td>2Q26</td><td>1Q26</td></tr>"
              "<tr><td>Net interest margin</td><td>3.22 %</td><td>3.17 %</td>"
              "</tr></table>")
        m = extract_table_metrics(t1 + t2, "2026-06-30")
        self.assertIsNone(m["nim"])

    def test_point_first_decimals_parse(self):
        """CBSH 2026-07-16: ratios printed WITHOUT a leading zero (".19%") —
        the leading-digit-only pattern was blind to the bank's entire number
        style (NCO current blank while the release stated it plainly)."""
        m = x("<p>The ratio of annualized net loan charge-offs to average "
              "loans was .19% in the current quarter.</p>")
        self.assertEqual(m["nco_ratio"], 0.19)

    def test_point_first_table_cells_parse(self):
        from data.release_metrics import extract_table_metrics
        t = ("<table><tr><td>x</td><td>2Q26</td><td>1Q26</td></tr>"
             "<tr><td>Net charge-off ratio</td><td>.19 %</td><td>.30 %</td>"
             "</tr></table>")
        self.assertEqual(extract_table_metrics(t, "2026-06-30")["nco_ratio"],
                         0.19)

    def test_bps_narrated_level_is_parsed(self):
        """CFG live 2026-07-16: 'net charge-offs of 37 bps, down 2 bps QoQ ■
        Strong ACL coverage of 1.48%' shipped NCOs of 1.48% — the connector
        walked past the (unparsed) bps figure and a bullet into the NEXT
        metric's percent. The bps form is the value; the % form must not
        cross a bps token or a clause boundary."""
        m = x("<p>Continuing favorable credit trends; net charge-offs of "
              "37 bps, down 2 bps QoQ ■ Strong ACL coverage of 1.48%</p>")
        self.assertEqual(m["nco_ratio"], 0.37)

    def test_connector_never_crosses_sentence_or_bullet(self):
        m = x("<p>Net charge-offs were flat. ACL coverage of 1.48% shown.</p>")
        self.assertIsNone(m["nco_ratio"])
        m2 = x("<p>Net charge-offs improved ■ efficiency ratio of 61.1%</p>")
        self.assertIsNone(m2["nco_ratio"])

    def test_segment_qualified_figure_never_captured(self):
        # JPM: "Card Services net charge-off rate of 3.47%" is a SEGMENT rate,
        # not firmwide (2026-07-06 ground-truth catch).
        m = x("<p>Card Services net charge-off rate of 3.47%.</p>")
        self.assertIsNone(m["nco_ratio"])

    def test_firmwide_survives_next_to_segment_figure(self):
        m = x("<p>Net charge-offs were 0.62% of average loans. Card Services "
              "net charge-off rate of 3.47%.</p>")
        self.assertEqual(m["nco_ratio"], 0.62)

    def test_band_rejects_implausible(self):
        self.assertIsNone(x("<p>Net interest margin of 34.2%.</p>")["nim"])

    def test_absent_metric_is_none(self):
        self.assertIsNone(x("<p>Strong quarter across the board.</p>")["nim"])


class TestTableRowRejection(unittest.TestCase):
    """Flattened multi-period table rows must NEVER be captured — which period
    the first cell holds is column order, i.e. luck (2026-07-06 spot-check)."""

    def test_pct_table_row_rejected(self):
        m = x("<p>Net interest margin 3.71 % 3.69 % 3.66 %</p>")
        self.assertIsNone(m["nim"])

    def test_bare_label_value_without_verb_rejected(self):
        m = x("<p>Net interest margin (NIM) 2.95 2.84 2.78</p>")
        self.assertIsNone(m["nim"])

    def test_dollar_table_row_rejected(self):
        m = x("<p>Tangible book value per share (non-GAAP) (1) $14.87 $14.60 "
              "$14.13 $13.44</p>")
        self.assertIsNone(m["tbv_ps"])
        m2 = x("<p>Dividends per share $0.21 $0.21 $0.20 $0.20</p>")
        self.assertIsNone(m2["div_ps"])

    def test_verb_never_matches_inside_a_word(self):
        # "of" inside "charge-offs" must not act as the verb before 208.
        m = x("<p>Total net loan charge-offs 208 162 205</p>")
        self.assertIsNone(m["nco_ratio"])

    def test_adjusted_between_label_and_value_rejected(self):
        m = x("<p>The efficiency ratio, as adjusted, was 52.1%.</p>")
        self.assertIsNone(m["efficiency"])

    def test_prose_with_comparison_still_extracts(self):
        # The guards must not break normal prose (connective text separates values).
        m = x("<p>Net interest margin was 3.42%, compared with 3.38% for the "
              "prior quarter.</p>")
        self.assertEqual(m["nim"], 3.42)


class TestPinnedCreditRatios(unittest.TestCase):
    def test_npa_value_then_denominator(self):
        m = x("<p>Nonperforming assets were $45.2 million, or 0.42% of total "
              "assets.</p>")
        self.assertEqual(m["npa_assets"], 0.42)

    def test_npa_wrong_denominator_never_qualifies(self):
        m = x("<p>Nonperforming assets were 0.42% of total loans.</p>")
        self.assertIsNone(m["npa_assets"])

    def test_acl_label_form(self):
        m = x("<p>The allowance for credit losses to total loans was "
              "1.21%.</p>")
        self.assertEqual(m["acl_loans"], 1.21)

    def test_acl_value_then_denominator_form(self):
        m = x("<p>The allowance for credit losses was $210 million, or 1.21% "
              "of total loans held for investment.</p>")
        self.assertEqual(m["acl_loans"], 1.21)


class TestPerShare(unittest.TestCase):
    def test_tbv_plain(self):
        m = x("<p>Tangible book value per share of $23.45.</p>")
        self.assertEqual(m["tbv_ps"], 23.45)

    def test_tbv_growth_then_value_captures_value_not_prior(self):
        m = x("<p>Tangible book value per common share increased 8.2% to "
              "$23.45 from $21.67.</p>")
        self.assertEqual(m["tbv_ps"], 23.45)

    def test_tbv_prior_value_alone_not_captured(self):
        m = x("<p>Tangible book value per share was up 8.2% from $21.67.</p>")
        self.assertIsNone(m["tbv_ps"])

    def test_dividend_declared(self):
        m = x("<p>The Board declared a quarterly cash dividend of $0.23 per "
              "common share.</p>")
        self.assertEqual(m["div_ps"], 0.23)

    def test_dividend_raised_to_form(self):
        m = x("<p>The Board increased the quarterly dividend to $0.24 per "
              "share.</p>")
        self.assertEqual(m["div_ps"], 0.24)

    def test_special_dividend_excluded(self):
        m = x("<p>The Board declared a special dividend of $1.00 per "
              "share.</p>")
        self.assertIsNone(m["div_ps"])

    def test_regular_and_special_disagree_safely(self):
        m = x("<p>A quarterly cash dividend of $0.23 per share and a special "
              "dividend of $1.00 per share.</p>")
        self.assertEqual(m["div_ps"], 0.23)   # special is excluded, not merged


class TestTableExtraction(unittest.TestCase):
    Q = "2026-03-31"

    def test_quarter_token_headers_pick_current_column(self):
        h = _tbl(["", "1Q26", "4Q25", "1Q25"],
                 ["Net interest margin", "3.71 %", "3.69 %", "3.66 %"],
                 ["Efficiency ratio (1)", "58.3", "55.1", "60.5"])
        m = extract_table_metrics(h, self.Q)
        self.assertEqual(m["nim"], 3.71)
        # Ratio rows without % signs extract (TFC omits them entirely —
        # the specific label + band disambiguate; policy changed 2026-07-13).
        self.assertEqual(m["efficiency"], 58.3)

    def test_ratio_label_on_dollar_row_still_refused(self):
        # The $ guard that replaced the % requirement: an explicit $ marks
        # a dollar line — a ratio spec must never read it.
        h = _tbl(["", "1Q26", "4Q25"],
                 ["Efficiency ratio (1)", "$58.3", "$55.1"])
        m = extract_table_metrics(h, self.Q)
        self.assertIsNone(m["efficiency"])

    def test_full_date_headers_current_not_first_column(self):
        # Oldest-first column order: header mapping, not position, must decide.
        h = _tbl(["", "March 31, 2025", "December 31, 2025", "March 31, 2026"],
                 ["Tangible book value per share (non-GAAP)", "$13.15", "$14.60",
                  "$14.87"],
                 ["Dividends per share", "$0.195", "$0.20", "$0.21"])
        m = extract_table_metrics(h, self.Q)
        self.assertEqual(m["tbv_ps"], 14.87)
        self.assertEqual(m["div_ps"], 0.21)

    def test_expected_quarter_absent_skips_table(self):
        h = _tbl(["", "4Q25", "3Q25"], ["Net interest margin", "3.69 %", "3.60 %"])
        self.assertIsNone(extract_table_metrics(h, self.Q)["nim"])

    def test_duplicate_period_column_is_ambiguous(self):
        # Quarter + year-to-date columns both headed "June 30, 2026" → skip
        # (no span row above to prove which occurrence is the quarter).
        h = _tbl(["", "June 30, 2026", "March 31, 2026", "June 30, 2026"],
                 ["Return on average assets", "1.20 %", "1.15 %", "1.18 %"])
        self.assertIsNone(extract_table_metrics(h, "2026-06-30")["roa"])

    def test_combined_3m_6m_header_first_occurrence_is_quarter(self):
        # NPB 2026-07-21: income statement headed "Three Months Ended | Six
        # Months Ended" repeats the quarter-end under both spans. The span
        # row proves the quarter block leads → FIRST occurrence is the
        # quarter column; the refusal here left EPS to FMP's junk $0.09.
        h = _tbl(["", "Three Months Ended", "", "Six Months Ended"],
                 ["", "June 30, 2026", "Mar 31, 2026", "June 30, 2025",
                  "June 30, 2026"],
                 ["Diluted Earnings Per Share", "$0.60", "$0.62", "$0.51",
                  "$1.22"])
        m = extract_table_metrics(h, "2026-06-30")
        self.assertEqual(m["eps_diluted"], 0.60)
        # Non-colliding columns in the same table keep working.
        self.assertEqual(extract_table_metrics(h, "2026-03-31")["eps_diluted"],
                         0.62)

    def test_ytd_first_span_order_stays_refused(self):
        # YTD block leading is not the proven layout — never guess.
        h = _tbl(["", "Six Months Ended", "", "Three Months Ended"],
                 ["", "June 30, 2026", "June 30, 2026", "Mar 31, 2026"],
                 ["Diluted Earnings Per Share", "$1.22", "$0.60", "$0.62"])
        self.assertIsNone(
            extract_table_metrics(h, "2026-06-30")["eps_diluted"])

    def test_value_count_mismatch_skips_row(self):
        h = _tbl(["", "1Q26", "4Q25", "1Q25"],
                 ["Net interest margin", "3.71 %", "3.69 %"])   # one cell short
        self.assertIsNone(extract_table_metrics(h, self.Q)["nim"])

    def test_footnote_cells_do_not_break_alignment(self):
        h = _tbl(["", "1Q26", "4Q25", "1Q25"],
                 ["Efficiency ratio", "(1)", "58.3 %", "55.1 %", "60.5 %"])
        self.assertEqual(extract_table_metrics(h, self.Q)["efficiency"], 58.3)

    def test_adjusted_label_row_skipped(self):
        h = _tbl(["", "1Q26", "4Q25"],
                 ["Efficiency ratio, as adjusted", "52.1 %", "50.0 %"],
                 ["Efficiency ratio", "58.3 %", "55.1 %"])
        self.assertEqual(extract_table_metrics(h, self.Q)["efficiency"], 58.3)

    def test_disagreeing_tables_yield_none(self):
        h = (_tbl(["", "1Q26", "4Q25"], ["Net interest margin", "3.71 %", "3.69 %"])
             + _tbl(["", "1Q26", "4Q25"], ["Net interest margin", "3.55 %", "3.50 %"]))
        self.assertIsNone(extract_table_metrics(h, self.Q)["nim"])

    def test_roe_row_never_matches_rotce_row(self):
        h = _tbl(["", "1Q26", "4Q25"],
                 ["Return on average tangible common equity", "15.20 %", "14.80 %"],
                 ["Return on average common equity", "12.40 %", "12.10 %"])
        m = extract_table_metrics(h, self.Q)
        self.assertEqual(m["rotce"], 15.2)
        self.assertEqual(m["roe"], 12.4)

    def test_prose_wins_table_fills_gaps(self):
        html = ("<p>Net interest margin of 3.42%.</p>"
                + _tbl(["", "1Q26", "4Q25"],
                       ["Net interest margin", "3.71 %", "3.69 %"],
                       ["Return on average assets", "1.15 %", "1.10 %"]))
        m = extract_release_metrics(html, expected_qend=self.Q)
        self.assertEqual(m["nim"], 3.42)     # prose stays authoritative
        self.assertEqual(m["roa"], 1.15)     # table fills the prose gap

    def test_no_expected_qend_means_no_table_extraction(self):
        html = _tbl(["", "1Q26", "4Q25"],
                    ["Net interest margin", "3.71 %", "3.69 %"])
        self.assertIsNone(extract_release_metrics(html)["nim"])


class TestRealisticComposite(unittest.TestCase):
    RELEASE = """
    <h1>Bancorp Reports Second Quarter 2026 Results</h1>
    <p>Net income of $52.3 million, or $1.31 per diluted share. Return on
    average assets of 1.24%, return on average common equity of 11.8% and
    return on average tangible common equity of 14.6%, compared with 1.19%,
    11.2% and 13.9%, respectively, for the first quarter of 2026.</p>
    <p>Net interest margin of 3.55%, up from 3.47% in the linked quarter.
    The efficiency ratio improved to 54.2%.</p>
    <p>Nonperforming assets were $31.0 million, or 0.28% of total assets.
    Net charge-offs were $4.1 million, or 0.11% of average loans. The
    allowance for credit losses to total loans was 1.18%.</p>
    <p>Tangible book value per share increased 9.1% to $27.83. The Board
    declared a quarterly cash dividend of $0.27 per share.</p>
    """

    def test_full_extraction(self):
        m = x(self.RELEASE)
        self.assertEqual(m["roa"], 1.24)
        self.assertEqual(m["roe"], 11.8)
        self.assertEqual(m["rotce"], 14.6)
        self.assertEqual(m["nim"], 3.55)
        self.assertEqual(m["efficiency"], 54.2)
        self.assertEqual(m["npa_assets"], 0.28)
        self.assertEqual(m["nco_ratio"], 0.11)
        self.assertEqual(m["acl_loans"], 1.18)
        self.assertEqual(m["tbv_ps"], 27.83)
        self.assertEqual(m["div_ps"], 0.27)


class TestBlankCurrentCellNeverShifts(unittest.TestCase):
    """(2026-07-10, sec_earnings_8k P3 twin — verified GUARDED here) A blank
    current-quarter cell must never let the prior period's value serve as
    current. extract_table_metrics' `len(vals) != len(qends)` alignment guard
    already covers it (row skipped → None); this pins that guard."""

    def test_blank_current_cell_yields_none_not_prior(self):
        html = _tbl(["", "March 31, 2026", "March 31, 2025"],
                    ["Net interest margin", "", "3.10%"])
        m = extract_table_metrics(html, "2026-03-31")
        self.assertIsNone(m.get("nim"))          # never 3.10 (the prior period)

    def test_populated_current_cell_still_extracts(self):
        html = _tbl(["", "March 31, 2026", "March 31, 2025"],
                    ["Net interest margin", "3.42%", "3.10%"])
        m = extract_table_metrics(html, "2026-03-31")
        self.assertEqual(m.get("nim"), 3.42)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestMonthYearHeaders(unittest.TestCase):
    """FBK-style column headers ('Jun 2026') — caught live 2026-07-13 when
    every table in FB Financial's release was skipped."""

    def test_quarter_end_month_year(self):
        from data.release_metrics import _period_qend
        self.assertEqual(_period_qend("Jun 2026"), "2026-06-30")
        self.assertEqual(_period_qend("Mar 2026"), "2026-03-31")
        self.assertEqual(_period_qend("September 2025"), "2025-09-30")
        self.assertEqual(_period_qend("Dec 2025"), "2025-12-31")

    def test_non_quarter_end_month_is_not_a_period(self):
        from data.release_metrics import _period_qend
        self.assertIsNone(_period_qend("May 2026"))
        self.assertIsNone(_period_qend("Jan 2026"))

    def test_full_date_still_wins(self):
        from data.release_metrics import _period_qend
        self.assertEqual(_period_qend("March 31, 2026"), "2026-03-31")

    def test_fbk_shaped_table_extracts(self):
        html = _tbl(["(dollars in thousands, except per share data)",
                     "Jun 2026", "Mar 2026", "Jun 2025"],
                    ["Efficiency ratio", "52.3%", "55.2%", "105.7%"],
                    ["Return on average shareholders’ equity",
                     "11.8%", "11.9%", "0.74%"],
                    ["Nonperforming assets as a percentage of total assets",
                     "1.14%", "0.98%", "0.92%"])
        m = extract_table_metrics(html, "2026-06-30")
        self.assertEqual(m.get("efficiency"), 52.3)
        self.assertEqual(m.get("roe"), 11.8)          # curly apostrophe
        self.assertEqual(m.get("npa_assets"), 1.14)   # "as a percentage of"
        p = extract_table_metrics(html, "2026-03-31")
        self.assertEqual(p.get("efficiency"), 55.2)   # prior column, same doc


class TestEpsRevenueSpecs(unittest.TestCase):
    HDR = ["(dollars in thousands, except per share data)",
           "Jun 2026", "Mar 2026"]

    def test_gaap_and_adjusted_eps(self):
        html = _tbl(self.HDR,
                    ["Diluted earnings per common share", "$1.13", "$1.10"],
                    ["Adjusted diluted earnings per common share*",
                     "$1.14", "$1.12"])
        m = extract_table_metrics(html, "2026-06-30")
        self.assertEqual(m.get("eps_diluted"), 1.13)
        self.assertEqual(m.get("eps_adj"), 1.14)   # adjusted opt-in row

    def test_adjusted_row_still_refused_for_normal_specs(self):
        html = _tbl(self.HDR,
                    ["Adjusted efficiency ratio*", "52.0%", "54.3%"])
        m = extract_table_metrics(html, "2026-06-30")
        self.assertIsNone(m.get("efficiency"))     # never the adjusted variant

    def test_revenue_scaled_by_stated_thousands(self):
        html = _tbl(self.HDR,
                    ["Total revenue", "$174,752", "$172,340"])
        m = extract_table_metrics(html, "2026-06-30")
        self.assertEqual(m.get("total_revenue"), 174_752_000.0)

    def test_revenue_refused_without_stated_unit(self):
        html = _tbl(["", "Jun 2026", "Mar 2026"],
                    ["Total revenue", "$174,752", "$172,340"])
        m = extract_table_metrics(html, "2026-06-30")
        self.assertIsNone(m.get("total_revenue"))  # magnitude never guessed


class TestPriorQuarterEnd(unittest.TestCase):
    def test_transitions(self):
        from data.release_metrics import _prior_quarter_end
        self.assertEqual(_prior_quarter_end("2026-06-30"), "2026-03-31")
        self.assertEqual(_prior_quarter_end("2026-03-31"), "2025-12-31")
        self.assertEqual(_prior_quarter_end("2025-12-31"), "2025-09-30")
        self.assertEqual(_prior_quarter_end("2025-09-30"), "2025-06-30")
        self.assertIsNone(_prior_quarter_end(None))
        self.assertIsNone(_prior_quarter_end("garbage"))


class TestRegionalTableShapes(unittest.TestCase):
    """TFC/FITB shapes from the 2026-07-13 pre-season sweep."""

    def test_billions_scale_and_te_variant_excluded(self):
        # TFC: dollars in billions; the taxable-equivalent revenue row must
        # not merge with (and kill) the GAAP one via the disagreement guard.
        html = _tbl(["(Dollars in billions, except per share data)",
                     "1Q26", "4Q25", "1Q25"],
                    ["Total revenue", "5.15", "5.25", "4.90"],
                    ["Total revenue - TE (1)", "5.20", "5.30", "4.95"],
                    ["Diluted EPS", "$1.09", "$1.00", "$0.87"])
        m = extract_table_metrics(html, "2026-03-31")
        self.assertEqual(m.get("total_revenue"), 5.15e9)
        self.assertEqual(m.get("eps_diluted"), 1.09)   # "Diluted EPS" abbrev

    def test_footnoted_revenue_label_still_matches(self):
        html = _tbl(["(dollars in thousands)", "1Q26", "4Q25"],
                    ["Total revenue (2)", "174,752", "172,340"])
        m = extract_table_metrics(html, "2026-03-31")
        self.assertEqual(m.get("total_revenue"), 174_752_000.0)

    def test_split_month_year_header_with_change_cols(self):
        # FITB: months on one row, years + Seq/Yr/Yr change cols on the next;
        # data rows carry two trailing change values that must be trimmed.
        def tr(cells):
            return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
        html = ("<table>"
                + tr(["($ in millions, except per share)", "", "", ""])
                + tr(["", "March", "December", "March", "", ""])
                + tr(["", "2026", "2025", "2025", "Seq", "Yr/Yr"])
                + tr(["Net interest margin (a)", "3.30", "3.13", "3.03",
                      "17", "27"])
                + "</table>")
        m = extract_table_metrics(html, "2026-03-31")
        self.assertEqual(m.get("nim"), 3.30)
        p = extract_table_metrics(html, "2025-12-31")
        self.assertEqual(p.get("nim"), 3.13)

    def test_split_header_with_non_change_extras_refused(self):
        # Extra year-row cells that are NOT change tokens → pairing unproven.
        def tr(cells):
            return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
        html = ("<table>"
                + tr(["", "March", "December", ""])
                + tr(["", "2026", "2025", "Outlook"])
                + tr(["Net interest margin", "3.30", "3.13", "3.40"])
                + "</table>")
        m = extract_table_metrics(html, "2026-03-31")
        self.assertIsNone(m.get("nim"))


class TestMegaCapTableShapes(unittest.TestCase):
    """JPM/C shapes caught live on the 2026-07-14 report morning — every
    table in both releases was skipped (JPM extracted 1/13, C 1/13)."""

    def test_curly_quote_quarter_token(self):
        from data.release_metrics import _period_qend
        self.assertEqual(_period_qend("2Q’26"), "2026-06-30")
        self.assertEqual(_period_qend("1Q'26"), "2026-03-31")

    # JPM "Results for JPM": a caption/banner row carrying the change-vs
    # periods sits ABOVE the real header; change columns are "$ O/(U)" /
    # "O/(U) %"; scale reads "($ millions, …)" with no "in"; EPS label is
    # the postfix form; revenue is "Net revenue - reported" (GAAP) next to
    # "- managed" (which must never match).
    JPM = ("<table>"
           "<tr><td>Results for JPM</td><td></td><td></td><td>1Q26</td>"
           "<td></td><td>2Q25</td></tr>"
           "<tr><td>($ millions, except per share data)</td><td>2Q26</td>"
           "<td>1Q26</td><td>2Q25</td><td>$ O/(U)</td><td>O/(U) %</td>"
           "<td>$ O/(U)</td><td>O/(U) %</td></tr>"
           "<tr><td>Net revenue - reported</td><td>$</td><td>57,347</td>"
           "<td>$</td><td>49,836</td><td>$</td><td>44,912</td><td>$</td>"
           "<td>7,511</td><td>15</td><td>%</td><td>$</td><td>12,435</td>"
           "<td>28</td><td>%</td></tr>"
           "<tr><td>Net revenue - managed</td><td>58,022</td><td>50,536</td>"
           "<td>45,680</td><td>7,486</td><td>15</td><td>12,342</td>"
           "<td>27</td></tr>"
           "<tr><td>Earnings per share - diluted</td><td>$</td><td>7.70</td>"
           "<td>$</td><td>5.94</td><td>$</td><td>5.24</td><td>$</td>"
           "<td>1.76</td><td>30</td><td>%</td><td>$</td><td>2.46</td>"
           "<td>47</td><td>%</td></tr>"
           "<tr><td>Return on common equity</td><td>24</td><td>%</td>"
           "<td>19</td><td>%</td><td>18</td><td>%</td></tr>"
           "<tr><td>Return on tangible common equity</td><td>29</td>"
           "<td>23</td><td>21</td></tr>"
           "</table>")

    def test_jpm_banner_row_does_not_hijack_header(self):
        m = extract_table_metrics(self.JPM, "2026-06-30")
        self.assertEqual(m.get("eps_diluted"), 7.70)
        self.assertEqual(m.get("roe"), 24.0)
        self.assertEqual(m.get("rotce"), 29.0)
        self.assertEqual(m.get("total_revenue"), 57_347_000_000.0)

    def test_jpm_prior_and_yoy_columns(self):
        p = extract_table_metrics(self.JPM, "2026-03-31")
        self.assertEqual(p.get("eps_diluted"), 5.94)
        self.assertEqual(p.get("total_revenue"), 49_836_000_000.0)
        y = extract_table_metrics(self.JPM, "2025-06-30")
        self.assertEqual(y.get("eps_diluted"), 5.24)
        self.assertEqual(y.get("roe"), 18.0)

    def test_managed_basis_row_never_merges(self):
        # If "- managed" (58,022) ever matched the revenue spec, the
        # disagreement guard would kill total_revenue instead of 57,347.
        m = extract_table_metrics(self.JPM, "2026-06-30")
        self.assertEqual(m.get("total_revenue"), 57_347_000_000.0)

    @staticmethod
    def _c_tbl(caption, *rows):
        zw = "​"
        def tr(cells):
            return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
        out = tr([caption, zw, "2Q’26", zw, "1Q’26", zw, "2Q’25", zw,
                  "QoQ%", zw, "YoY%"])
        return "<table>" + out + "".join(tr(r) for r in rows) + "</table>"

    def test_c_zero_width_cells_and_bps_change_cols(self):
        zw = "​"
        html = self._c_tbl(
            "Citigroup ($ in millions, except per share amounts)",
            ["Return on average common equity (RoE)", zw, "11.4%", zw,
             "11.5%", zw, "7.7%", zw, "(10) bps", zw, "370 bps"],
            ["Tangible book value per share (c)", zw, "$", "100.89", zw,
             "$", "99.01", zw, "$", "94.16", zw, "2%", zw, "7%"])
        m = extract_table_metrics(html, "2026-06-30")
        self.assertEqual(m.get("roe"), 11.4)
        self.assertEqual(m.get("tbv_ps"), 100.89)
        p = extract_table_metrics(html, "2026-03-31")
        self.assertEqual(p.get("roe"), 11.5)
        self.assertEqual(p.get("tbv_ps"), 99.01)

    def test_c_segment_revenue_never_ships_as_firmwide(self):
        # The firmwide "…, net of interest expense" row must CANDIDATE (not
        # be invisible): alone it extracts; next to a segment table's plain
        # "Total revenues" row the disagreement guard refuses — a lone
        # segment figure must never ship as the firmwide number.
        zw = "​"
        firmwide = self._c_tbl(
            "Citigroup ($ in millions, except per share amounts)",
            ["Total revenues, net of interest expense", zw, "24,766", zw,
             "24,633", zw, "21,668", zw, "1%", zw, "14%"])
        m = extract_table_metrics(firmwide, "2026-06-30")
        self.assertEqual(m.get("total_revenue"), 24_766_000_000.0)
        segment = self._c_tbl(
            "All Other (Managed Basis) ($ in millions)",
            ["Total revenues", zw, "1,737", zw, "1,682", zw, "1,716", zw,
             "3%", zw, "1%"])
        m = extract_table_metrics(firmwide + segment, "2026-06-30")
        self.assertIsNone(m.get("total_revenue"))


class TestOrdinalQuarterHeaders(unittest.TestCase):
    """CFR/TCBI shapes from the 2026-07-14 week-ahead sweep (CFR extracted
    0/13, TCBI 2/13): ordinal-quarter header cells with the year in the
    same cell, on the next row, or on the PREVIOUS row."""

    def test_single_cell_ordinal_quarter(self):
        from data.release_metrics import _period_qend
        self.assertEqual(_period_qend("1st Quarter 2026"), "2026-03-31")
        self.assertEqual(_period_qend("4th Qtr 2025"), "2025-12-31")
        self.assertEqual(_period_qend("First Quarter 2026"), "2026-03-31")

    def test_tcbi_ordinals_over_years_with_units_label(self):
        # Years row leads with the units cell — ignored as the label slot.
        def tr(cells):
            return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
        html = ("<table>"
                + tr(["", "1st Quarter", "", "4th Quarter", "", "1st Quarter"])
                + tr(["(dollars in thousands except per share data)",
                      "2026", "", "2025", "", "2025"])
                + tr(["Net interest margin", "3.43", "%", "3.38", "%",
                      "3.19", "%"])
                + "</table>")
        m = extract_table_metrics(html, "2026-03-31")
        self.assertEqual(m.get("nim"), 3.43)
        p = extract_table_metrics(html, "2025-12-31")
        self.assertEqual(p.get("nim"), 3.38)

    def test_cfr_years_over_descending_ordinals(self):
        # "2026 | 2025" above "1st Qtr | 4th | 3rd | 2nd | 1st Qtr": one
        # strictly-descending ordinal run per year proves the assignment.
        def tr(cells):
            return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
        html = ("<table>"
                + tr(["(In thousands, except per share data)", "", ""])
                + tr(["", "2026", "", "2025"])
                + tr(["", "1st Qtr", "", "4th Qtr", "", "3rd Qtr", "",
                      "2nd Qtr", "", "1st Qtr"])
                + tr(["Return on average common equity", "15.15", "14.80",
                      "16.72", "15.64", "15.54"])
                + "</table>")
        m = extract_table_metrics(html, "2026-03-31")
        self.assertEqual(m.get("roe"), 15.15)      # 1st Qtr under 2026
        p = extract_table_metrics(html, "2025-12-31")
        self.assertEqual(p.get("roe"), 14.80)      # 4th Qtr under 2025
        y = extract_table_metrics(html, "2025-03-31")
        self.assertEqual(y.get("roe"), 15.54)      # trailing 1st Qtr = 2025

    def test_ascending_ordinals_refused(self):
        # Oldest-first ordinals don't form one descending run per year —
        # the assignment is unprovable and the table must be skipped.
        def tr(cells):
            return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
        html = ("<table>"
                + tr(["", "2025", "", "2026"])
                + tr(["", "1st Qtr", "", "2nd Qtr", "", "3rd Qtr", "",
                      "4th Qtr", "", "1st Qtr"])
                + tr(["Return on average common equity", "15.54", "15.64",
                      "16.72", "14.80", "15.15"])
                + "</table>")
        m = extract_table_metrics(html, "2026-03-31")
        self.assertIsNone(m.get("roe"))


class TestProseEps(unittest.TestCase):
    """Prose diluted-EPS (2026-07-14 pm): BAC renders its highlights table as
    positioned <div>s (zero <table> markup) and GS uses single-period KPI
    stacks — for both, prose is the only deterministic EPS source."""

    def test_bac_label_form_with_trailing_comparison(self):
        m = x("<p>Net income of $9.1 billion compared to $7.2 billion, up 27% "
              "– Diluted earnings per share (EPS) of $1.21 compared to "
              "$0.90</p>")
        self.assertEqual(m.get("eps_diluted"), 1.21)

    def test_gs_headline_per_common_share(self):
        m = x("<p>Goldman Sachs Reports Second Quarter Earnings Per Common "
              "Share of $ 20.98 and Increases the Quarterly Dividend</p>")
        self.assertEqual(m.get("eps_diluted"), 20.98)

    def test_ms_bare_eps_headline(self):
        m = x("<p>Morgan Stanley Reports Net Revenues of $20.6 Billion, EPS "
              "of $3.43 and ROTCE of 27.1%. Net income applicable to Morgan "
              "Stanley was $5.6 billion, or $3.43 per diluted share, compared "
              "with $4.3 billion, or $2.60 per diluted share, a year ago.</p>")
        self.assertEqual(m.get("eps_diluted"), 3.43)

    def test_value_led_comparison_clause_never_captured(self):
        # A release narrating per-share ONLY inside the year-ago comparison
        # clause must extract NOTHING — the value-led "or $X.XX per diluted
        # share" form is deliberately unsupported (its comparison marker sits
        # outside the before-label window).
        m = x("<p>Net income was $5.6 billion, compared with $4.3 billion, "
              "or $2.60 per diluted share, a year ago.</p>")
        self.assertIsNone(m.get("eps_diluted"))

    def test_adjusted_eps_never_captured(self):
        m = x("<p>Adjusted EPS of $1.14 improved on strong fees.</p>")
        self.assertIsNone(m.get("eps_diluted"))

    def test_fbk_gaap_and_adjusted_headline_refuses_prose_table_fills(self):
        # "Q2 Diluted EPS of $1.13, Adjusted Diluted EPS* of $1.14" (FBK,
        # live 2026-07-14 pm): the bare-EPS pattern anchored at the TAIL of
        # "Adjusted Diluted EPS" and 1.14 shipped as GAAP. Prose must refuse
        # BOTH (the honest 1.13 trails into the next label's "Adjusted");
        # the table supplies the GAAP figure.
        headline = ("<p>Reports Q2 Diluted EPS of $1.13, Adjusted Diluted "
                    "EPS* of $1.14</p>")
        self.assertIsNone(x(headline).get("eps_diluted"))
        html = headline + _tbl(
            ["(dollars in thousands, except per share data)",
             "Jun 2026", "Mar 2026"],
            ["Diluted earnings per common share", "$1.13", "$1.10"])
        m = extract_release_metrics(html, expected_qend="2026-06-30")
        self.assertEqual(m.get("eps_diluted"), 1.13)

    def test_disagreeing_clean_candidates_refused(self):
        m = x("<p>Diluted earnings per share of $1.21 grew strongly. Later "
              "the firm noted diluted earnings per share of $1.35.</p>")
        self.assertIsNone(m.get("eps_diluted"))

    def test_ms_mid_form_table_label(self):
        html = _tbl(["Firm ($ millions, except per share data)",
                     "1Q 2026", "1Q 2025"],
                    ["Earnings per diluted share 1", "$3.43", "$2.60"])
        m = extract_table_metrics(html, "2026-03-31")
        self.assertEqual(m.get("eps_diluted"), 3.43)
        y = extract_table_metrics(html, "2025-03-31")
        self.assertEqual(y.get("eps_diluted"), 2.60)


class TestFifteenMinuteRecheck(unittest.TestCase):
    def test_ttl_is_900(self):
        # Report-morning freshness: a fetch minutes before the 8-K lands
        # re-stamps LAST quarter's release — at 1h C served its April
        # release until 9:39 on Jul-14. Pin the shorter re-check window.
        import inspect
        from data import release_metrics as rm
        src = inspect.getsource(rm.release_metrics)
        self.assertIn("is_fresh(cached, 900)", src)
        self.assertNotIn("is_fresh(cached, 3600)", src)
