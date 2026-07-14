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
        # Quarter + year-to-date columns both headed "June 30, 2026" → skip.
        h = _tbl(["", "June 30, 2026", "March 31, 2026", "June 30, 2026"],
                 ["Return on average assets", "1.20 %", "1.15 %", "1.18 %"])
        self.assertIsNone(extract_table_metrics(h, "2026-06-30")["roa"])

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
