"""Release-table period headers the shared parser could not place (2026-10-12).

Only 13 board banks could confirm a revenue basis because
data/release_metrics._period_header refused these real 2Q26 EX-99.1 shapes:

  HBAN  "2026 | 2025" over "Second | First | Fourth | Third | Second |
        Change (%)" over "Quarter ×5 | LQ | YOY"  (years ABOVE bare ordinals)
  MSBI  "June 30, | March 31, | …" over "(dollars in thousands…) | 2026 |
        2026 | 2025 …"                            (caption cell on a split row)
  CUBB  "Q2 | Q1 | Q4 | Q3 | Q2 | Six Months Ended June 30," over seven
        years                                     (quarters + a YTD block)
  RF    "6/30/2026 | 3/31/2026 | 6/30/2025 | 2Q26 vs. 1Q26 | 2Q26 vs. 2Q25"
        (numeric dates; doubled comparison columns read as periods)

Fixtures in tests/fixtures/release_headers_2q26/ are the banks' real tables
(EDGAR, verbatim cell text, tag attributes stripped). Every expected value
below was checked against the release's own text. All positive tests fail on
the pre-change parser (every value None / no revenue figure); the negative
tests pin that a change column, a year-to-date block or an unprovable split
is never read as a quarter.
"""
import os
import unittest

from data.release_metrics import (_period_header, _table_rows,
                                  extract_table_metrics, revenue_basis_facts)

_FIX = os.path.join(os.path.dirname(__file__), "fixtures",
                    "release_headers_2q26")
Q, PQ, YQ = "2026-06-30", "2026-03-31", "2025-06-30"


def _fixture(t):
    with open(os.path.join(_FIX, f"{t}.html"), encoding="utf-8") as f:
        return f.read()


def _tables(html):
    import re
    return [_table_rows(t) for t in
            re.findall(r"(?is)<table[^>]*>(.*?)</table>", html)]


def tr(cells):
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def table(*rows):
    return "<table>" + "".join(tr(r) for r in rows) + "</table>"


class TestHBANYearsOverBareOrdinals(unittest.TestCase):
    """Years row above "Second | First | Fourth | Third | Second", the word
    "Quarter" on the row beneath, LQ/YOY change columns."""

    def setUp(self):
        self.html = _fixture("HBAN")

    def test_header_placed_with_change_columns(self):
        rows = _tables(self.html)[1]          # Table 3 – NII and total revenue
        qends, hdr_i, _span, n_change = _period_header(rows)
        self.assertEqual(qends, ["2026-06-30", "2026-03-31", "2025-12-31",
                                 "2025-09-30", "2025-06-30"])
        self.assertEqual(n_change, 2)         # LQ | YOY

    def test_current_prior_and_year_ago_columns(self):
        cur = extract_table_metrics(self.html, Q)
        self.assertEqual(cur["eps_diluted"], 0.33)
        self.assertEqual(cur["tbv_ps"], 9.65)
        self.assertEqual(cur["nim"], 3.21)
        self.assertEqual(cur["rotce"], 15.1)
        self.assertEqual(extract_table_metrics(self.html, PQ)["eps_diluted"],
                         0.25)
        yoy = extract_table_metrics(self.html, YQ)
        self.assertEqual(yoy["eps_diluted"], 0.34)
        self.assertEqual(yoy["tbv_ps"], 9.13)

    def test_revenue_lines(self):
        # "Total revenue - FTE (1) $ 2,857" and NII 2,052 + noninterest
        # income 785 = 2,837 (GAAP).
        f = revenue_basis_facts(self.html, Q)
        self.assertEqual(f["reported"], [2_857_000_000.0, 2_837_000_000.0])


class TestMSBICaptionOnSplitRow(unittest.TestCase):
    """"June 30, | March 31, | …" over "(dollars in thousands, except per
    share data) | 2026 | 2026 | 2025 | 2025 | 2025"."""

    def setUp(self):
        self.html = _fixture("MSBI")

    def test_eps_columns(self):
        cur = extract_table_metrics(self.html, Q)
        self.assertEqual(cur["eps_diluted"], 0.82)
        self.assertEqual(cur["eps_adj"], 0.82)
        self.assertEqual(cur["tbv_ps"], 21.41)
        self.assertEqual(cur["efficiency"], 60.61)
        prior = extract_table_metrics(self.html, PQ)
        self.assertEqual(prior["eps_diluted"], 0.74)
        self.assertEqual(prior["eps_adj"], 0.79)
        self.assertEqual(extract_table_metrics(self.html, YQ)["eps_adj"], 0.48)

    def test_adjusted_revenue(self):
        # "Adjusted total revenue $ 83,740 $ 81,100 …" (in thousands)
        self.assertEqual(revenue_basis_facts(self.html, Q)["adjusted"],
                         83_740_000.0)


class TestCUBBQuartersPlusYtdBlock(unittest.TestCase):
    """"Q2 | Q1 | Q4 | Q3 | Q2 | Six Months Ended June 30," over seven
    years: five quarter columns + a two-column YTD block that is kept for
    alignment and never read."""

    def setUp(self):
        self.html = _fixture("CUBB")

    def test_header_marks_ytd_columns(self):
        qends, *_ = _period_header(_tables(self.html)[0])
        self.assertEqual(qends, ["2026-06-30", "2026-03-31", "2025-12-31",
                                 "2025-09-30", "2025-06-30", "YTD", "YTD"])

    def test_quarter_values_never_the_ytd_block(self):
        cur = extract_table_metrics(self.html, Q)
        self.assertEqual(cur["eps_adj"], 2.05)     # H1 core EPS is 4.01
        self.assertEqual(cur["eps_diluted"], 2.05)
        self.assertEqual(cur["tbv_ps"], 65.2)
        self.assertEqual(extract_table_metrics(self.html, YQ)["eps_adj"], 1.8)

    def test_reported_revenue(self):
        # NII 193,366 + non-interest income 34,043 (in thousands)
        f = revenue_basis_facts(self.html, Q)
        self.assertEqual(f["reported"], [227_409_000.0])


class TestRFComparisonColumns(unittest.TestCase):
    """"2Q26 vs. 1Q26" cells are change columns: each carries a "$ change |
    % change" pair, aligned only by arithmetic proof (1,907 − 1,873 = 34)."""

    def setUp(self):
        self.html = _fixture("RF")

    def test_comparison_cells_are_not_periods(self):
        qends, _h, _s, n_change = _period_header(_tables(self.html)[1])
        self.assertEqual(qends, [Q, PQ, YQ])     # was [Q, Q]: refused
        self.assertEqual(n_change, 2)

    def test_total_revenue_columns(self):
        self.assertEqual(extract_table_metrics(self.html, Q)["total_revenue"],
                         1_907_000_000.0)
        self.assertEqual(extract_table_metrics(self.html, PQ)["total_revenue"],
                         1_873_000_000.0)
        self.assertEqual(extract_table_metrics(self.html, YQ)["total_revenue"],
                         1_905_000_000.0)

    def test_adjusted_revenue(self):
        f = revenue_basis_facts(self.html, Q)
        self.assertEqual(f["adjusted"], 1_947_000_000.0)
        self.assertEqual(f["reported"], [1_907_000_000.0, 1_907_000_000.0])


class TestNeverAGuess(unittest.TestCase):
    """Negative pins: change columns, YTD blocks and unprovable splits."""

    def test_doubled_change_pair_must_reproduce_the_difference(self):
        # Same RF layout, but the "$ change" cells don't equal the period
        # differences → alignment unproven → no value.
        h = table(["Quarter Ended"],
                  ["($ amounts in millions)", "6/30/2026", "3/31/2026",
                   "6/30/2025", "2Q26 vs. 1Q26", "2Q26 vs. 2Q25"],
                  ["Total revenue", "$ 1,907", "$ 1,873", "$ 1,905", "$ 50",
                   "1.8 %", "$ 2", "0.1 %"])
        self.assertIsNone(extract_table_metrics(h, Q)["total_revenue"])

    def test_change_column_never_read_as_a_period(self):
        # Only a comparison cell names 1Q26 → the prior quarter is absent.
        h = table(["($ amounts in millions)", "6/30/2026", "6/30/2025",
                   "2Q26 vs. 1Q26"],
                  ["Total revenue", "$ 1,907", "$ 1,905", "$ 34"])
        self.assertEqual(_period_header(_table_rows(h))[0], [Q, YQ])
        self.assertIsNone(extract_table_metrics(h, PQ)["total_revenue"])

    def test_bare_ordinals_without_quarter_row_refused(self):
        h = table(["2026", "2025"],
                  ["Second", "First", "Fourth"],
                  ["Diluted earnings per common share", "$ 0.33", "$ 0.25",
                   "$ 0.30"])
        self.assertIsNone(extract_table_metrics(h, Q)["eps_diluted"])

    def test_inconsistent_year_runs_refused(self):
        # Three ascending quarters under two years: no descending-run proof.
        h = table(["2026", "2025"],
                  ["1st Quarter", "2nd Quarter", "3rd Quarter"],
                  ["Diluted earnings per common share", "$ 0.33", "$ 0.25",
                   "$ 0.30"])
        self.assertIsNone(_period_header(_table_rows(h)))

    def test_ytd_only_numeric_header_refused(self):
        # KEY 2Q26 "Six months ended" over "6/30/2026 | 6/30/2025": H1 ROTCE
        # 12.96 must never stand as the quarter's.
        h = table(["(Dollars in millions, except per share amounts)"],
                  ["Six months ended"],
                  ["6/30/2026", "6/30/2025"],
                  ["Return on average tangible common equity (b)", "12.96 %",
                   "11.16 %"])
        self.assertIsNone(extract_table_metrics(h, Q)["rotce"])

    def test_ytd_block_on_split_header_never_read(self):
        # CUBB shape: asking for the YTD column's date reads the QUARTER
        # column only; the H1 4.01 is never a candidate.
        h = table(["(Dollars in thousands, except per share data)", "Q2",
                   "Q1", "Six Months Ended June 30,"],
                  ["2026", "2026", "2026", "2025"],
                  ["Diluted earnings per share", "$ 2.05", "$ 1.97",
                   "$ 4.01", "$ 3.33"])
        self.assertEqual(extract_table_metrics(h, Q)["eps_diluted"], 2.05)
        self.assertIsNone(extract_table_metrics(h, YQ)["eps_diluted"])

    def test_interleaved_change_columns_refused(self):
        # AUB 2Q26 shape: QoQ cells BETWEEN period columns — a blank change
        # cell would shift the year-ago column.
        h = table(["6/30/2026", "3/31/2026", "QoQ", "QoQ % change",
                   "6/30/2025", "YoY", "YoY % change"],
                  ["Efficiency ratio", "58.1 %", "60.2 %", "", "", "57.0 %",
                   "", ""])
        self.assertIsNone(_period_header(_table_rows(h)))

    def test_fiscal_q_label_is_not_a_calendar_quarter(self):
        h = table(["", "Fiscal Q4 2026", "Fiscal Q3 2026"],
                  ["Diluted earnings per share", "$ 0.08", "$ 0.06"])
        self.assertIsNone(_period_header(_table_rows(h)))

    def test_non_gaap_caption_refuses_split_header(self):
        # OCFC 2Q26: a "Core Ratios" section heading in the caption slot —
        # its "Diluted earnings per share" row is core EPS (0.43), not GAAP.
        h = table(["June 30,", "March 31,", "June 30,"],
                  ["Core Ratios 2 (Annualized):", "2026", "2026", "2025"],
                  ["Diluted earnings per share", "$ 0.43", "$ 0.43",
                   "$ 0.31"])
        self.assertIsNone(extract_table_metrics(h, PQ)["eps_diluted"])

    def test_excluding_row_is_a_variant(self):
        # FMNB 2Q26: the old reader served these "excluding …" rows as GAAP
        # ROA 1.37% / EPS 0.41 (GAAP: 1.29% / 0.39).
        h = table(["For the Three Months Ended"],
                  ["June 30,", "March 31,"],
                  ["2026", "2026"],
                  ["Diluted EPS excluding merger and certain items", "$0.41",
                   "$0.44"],
                  ["Return on Average Assets excluding system conversion, "
                   "merger and certain items (Annualized)", "1.37%", "1.37%"])
        m = extract_table_metrics(h, Q)
        self.assertIsNone(m["eps_diluted"])
        self.assertIsNone(m["roa"])


if __name__ == "__main__":
    unittest.main()
