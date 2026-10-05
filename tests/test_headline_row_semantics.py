"""(2026-10-05) Headline release figures: WHICH row and column is the quarter's.

After per-row units (#234) the headline banner still served 15 wrong values
over the 320 releases of the 2026-09-30 sweep (vs each bank's same-quarter
10-Q XBRL) — all first-match picks of the wrong row or column. Each pin below
is one real release's shape and numbers; the harness now scores 1 wrong of
1,099 (SFBC's release prints NII $9,506K everywhere; its 10-Q later says
$9,591K — an as-released revision, not an extraction error).

  1. A year-to-date first column is not the quarter (BBT/BRBS six-month
     income statements, FNLC six-months-first, MNSB "Year-to-Date | Three
     Months Ended"); "1QTR" ahead of "2QTR" is the prior quarter (ASRV).
  2. A table whose column header names a non-GAAP measure is skipped (OCFC
     "Core Ratios"); a mid-table "Adjusted …" subheading is not (GBFH, SSB:
     a reconciliation lists the GAAP row under it).
  3. Segment tables are skipped: a "segment" title (NRIM), or segment columns
     beside a "Total" (FSBW).
  4. Balance-sheet rows under an average-balance / yield header split over two
     rows (FCBC, PFIS) or "(average volume …)" (WABC) are skipped.
  5. Net income attributable to the company beats consolidated net income
     (FHN, CFFI) — only beside a plain "Net income" in its own table and
     within 20% of it (KEY segment tables, AMP's investment-entity NCI).
  6. Tax-equivalent NII is skipped when its value is the FTE row's and a GAAP
     value exists (SBCF); kept when FTE == GAAP (SFBS).

Run: python -m unittest tests.test_headline_row_semantics
"""
import unittest

from data.sec_earnings_8k import _next_quarter_end, extract_earnings_figures


def _doc(*tables):
    return ("<html><body>" + "".join(tables) + "</body></html>").encode("utf-8")


def _tbl(*rows, title=""):
    """rows: lists of cells (header rows are rows whose cells hold no numbers)."""
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return (f"<p>{title}</p>" if title else "") + f"<table>{body}</table>"


# The release quarter is the one after the anchor's balance-sheet date.
def _anchor(**kw):
    a = {"as_of": "2026-03-31"}
    a.update(kw)
    return a


class TestColumnPeriod(unittest.TestCase):
    def test_next_quarter_end(self):
        self.assertEqual(_next_quarter_end("2026-03-31"), (2026, 6))
        self.assertEqual(_next_quarter_end("2025-12-31"), (2026, 3))
        self.assertIsNone(_next_quarter_end(None))

    def test_bbt_six_month_income_statement(self):
        """BBT prints only "Six Months Ended June 30" (net income $110,643K;
        Q2 alone was $64,426K)."""
        html = _doc(_tbl(["(In Thousands)", "Six Months Ended June 30,", ""],
                         ["", "2026", "2025"],
                         ["Total assets", "21,500,000", "21,000,000"],
                         ["Net income", "110,643", "41,126"]))
        out = extract_earnings_figures(html, _anchor(
            total_assets=21_000_000_000.0, prior_net_income=46_217_000.0,
            prior_net_interest_income=150_000_000.0))
        self.assertIsNone(out["net_income"])

    def test_mnsb_year_to_date_first_column(self):
        """MNSB leads with "Year-to-Date" (net income $8,768K) before "Three
        Months Ended" ($4,668K = the 10-Q)."""
        html = _doc(_tbl(["(In thousands)", "Year-to-Date", "", "Three Months Ended", ""],
                         ["", "June 30, 2026", "June 30, 2025", "June 30, 2026", "March 31, 2026"],
                         ["Total assets", "3,100,000", "3,050,000", "3,100,000", "3,080,000"],
                         ["Net income", "8,768", "7,043", "4,668", "4,100"]))
        out = extract_earnings_figures(html, _anchor(
            total_assets=3_080_000_000.0, prior_net_income=4_100_000.0,
            prior_net_interest_income=20_000_000.0))
        self.assertIsNone(out["net_income"])

    def test_fnlc_six_months_table_then_quarter_table(self):
        """FNLC's first table is "As of and for the six months" ($18,553K /
        EPS $1.65); its quarterly table follows ($9,560K / $0.85 = 10-Q)."""
        html = _doc(
            _tbl(["Dollars in thousands", "As of and for the six months ended", ""],
                 ["", "6/30/2026", "6/30/2025"],
                 ["Net income", "18,553", "15,140"],
                 ["Diluted earnings per share", "1.65", "1.35"]),
            _tbl(["Dollars in thousands", "For the three months ended", ""],
                 ["", "6/30/2026", "3/31/2026"],
                 ["Total assets", "3,350,000", "3,300,000"],
                 ["Net income", "9,560", "8,993"],
                 ["Diluted earnings per share", "0.85", "0.80"]))
        out = extract_earnings_figures(html, _anchor(
            total_assets=3_300_000_000.0, prior_net_income=8_993_000.0,
            prior_net_interest_income=20_500_000.0))
        self.assertEqual(out["net_income"], 9_560_000.0)
        self.assertEqual(out["diluted_eps"], 0.85)

    def test_asrv_first_quarter_column(self):
        """ASRV's income statement runs "1QTR | 2QTR | YEAR TO DATE": NII
        10,828 is Q1 (Q2 was 11,336)."""
        html = _doc(_tbl(["(Dollars in thousands)", "2026", "", ""],
                         ["", "1QTR", "2QTR", "YEAR TO DATE"],
                         ["Total assets", "1,472,654", "1,462,143", ""],
                         ["Net interest income", "10,828", "11,336", "22,164"]))
        out = extract_earnings_figures(html, _anchor(
            total_assets=1_460_000_000.0, prior_net_income=1_794_000.0,
            prior_net_interest_income=10_611_000.0))
        self.assertIsNone(out["net_interest_income"])


class TestNonGaapAndSegments(unittest.TestCase):
    def test_ocfc_core_ratios_table(self):
        """OCFC: "Core Ratios (Annualized)" heads EPS $0.43; the GAAP
        statement's diluted EPS is $(0.04) (10-Q)."""
        html = _doc(
            _tbl(["Core Ratios2 (Annualized):", "2026", "2026"],
                 ["Diluted earnings per share", "0.43", "0.43"]),
            _tbl(["(in thousands)", "June 30, 2026", "March 31, 2026"],
                 ["Diluted earnings per share", "(0.04)", "0.36"]))
        self.assertEqual(extract_earnings_figures(html, _anchor())["diluted_eps"], -0.04)

    def test_gbfh_adjusted_subheading_does_not_taint_gaap_rows(self):
        """GBFH's reconciliation lists GAAP "Diluted Earnings Per Share" $0.38
        under a mid-table "Adjusted Diluted Earnings Per Share…" heading."""
        html = _doc(_tbl(["(in thousands)", "June 30, 2026", "March 31, 2026"],
                         ["Net income", "5,512", "1,301"],
                         ["Adjusted Diluted Earnings Per Share Excluding Unusual Items", "", ""],
                         ["Diluted Earnings Per Share", "0.38", "0.09"]))
        self.assertEqual(extract_earnings_figures(html, _anchor())["diluted_eps"], 0.38)

    def test_nrim_segment_titled_table(self):
        """NRIM's first NII ($33,236K) is "the Community Banking segment";
        consolidated NII was $37,136K (10-Q)."""
        html = _doc(
            _tbl(["(Dollars in thousands)", "June 30, 2026", "March 31, 2026"],
                 ["Net interest income", "33,236", "31,840"],
                 title="The following table provides highlights of the Community Banking segment of Northrim:"),
            _tbl(["(Dollars in thousands)", "June 30, 2026", "March 31, 2026"],
                 ["Total assets", "3,400,000", "3,350,000"],
                 ["Net interest income", "37,136", "35,000"]))
        out = extract_earnings_figures(html, _anchor(
            total_assets=3_350_000_000.0, prior_net_income=9_000_000.0,
            prior_net_interest_income=35_000_000.0))
        self.assertEqual(out["net_interest_income"], 37_136_000.0)

    def test_fsbw_segment_columns_beside_total(self):
        """FSBW: "Commercial and Consumer Banking | Home Lending | Total" —
        the first value column ($6,793K) is a segment; Q2 net income $7,936K."""
        html = _doc(
            _tbl(["(dollars in thousands)", "June 30, 2026", "March 31, 2026"],
                 ["Total assets", "3,179,080", "3,203,515"]),
            _tbl(["(dollars in thousands)", "At or For the Three Months Ended June 30, 2026", "", ""],
                 ["Condensed income statement:", "Commercial and Consumer Banking", "Home Lending", "Total"],
                 ["Net income", "6,793", "1,143", "7,936"]))
        out = extract_earnings_figures(html, _anchor(
            total_assets=3_200_000_000.0, prior_net_income=7_830_000.0,
            prior_net_interest_income=32_545_000.0))
        self.assertIsNone(out["net_income"])


class TestAverageBalances(unittest.TestCase):
    def test_fcbc_split_average_yield_header(self):
        """FCBC heads its average table "Average | Average Yield/" over
        "Balance | Interest | Rate": total assets $3,626,118K is the quarter's
        average; period-end was $3,607,720K."""
        html = _doc(
            _tbl(["Three Months Ended June 30,", "2026", "", ""],
                 ["", "Average", "", "Average Yield/"],
                 ["(Amounts in thousands)", "Balance", "Interest", "Rate"],
                 ["Total assets", "3,626,118", "", ""]),
            _tbl(["(Amounts in thousands)", "June 30, 2026", "March 31, 2026"],
                 ["Total assets", "3,607,720", "3,644,947"]))
        out = extract_earnings_figures(html, _anchor(total_assets=3_644_947_000.0))
        self.assertEqual(out["total_assets"], 3_607_720_000.0)

    def test_wabc_average_volume(self):
        html = _doc(
            _tbl(["(average volume, dollars in thousands)", "Q2'2026", "Q2'2025"],
                 ["Total Assets", "5,967,886", "6,042,100"]),
            _tbl(["(dollars in thousands)", "6/30/26", "6/30/25"],
                 ["Total Assets", "5,805,061", "5,825,069"]))
        out = extract_earnings_figures(html, _anchor(total_assets=5_864_450_000.0))
        self.assertEqual(out["total_assets"], 5_805_061_000.0)

    def test_weighted_average_rate_column_still_period_end(self):
        html = _doc(_tbl(["(In thousands)", "Balance", "Weighted Average Yield"],
                         ["Total assets", "11,010,507", ""]))
        out = extract_earnings_figures(html, _anchor(total_assets=11_018_793_000.0))
        self.assertEqual(out["total_assets"], 11_010_507_000.0)


class TestNetIncomeAndNii(unittest.TestCase):
    def test_fhn_attributable_net_income(self):
        """FHN: "Net income" $274M includes $4M non-controlling interest; net
        income attributable to controlling interest $271M (10-Q $270M)."""
        html = _doc(_tbl(["($ in millions)", "2Q26", "1Q26"],
                         ["Total assets", "84,437", "83,000"],
                         ["Net income", "274", "266"],
                         ["Net income attributable to noncontrolling interest", "4", "3"],
                         ["Net income attributable to controlling interest", "271", "263"]))
        out = extract_earnings_figures(html, _anchor(
            total_assets=83_000_000_000.0, prior_net_income=263_000_000.0,
            prior_net_interest_income=668_000_000.0))
        self.assertEqual(out["net_income"], 271_000_000.0)

    def test_key_segment_attributable_row_not_preferred(self):
        """KEY's "Consumer Bank" table prints only "Net income (loss)
        attributable to Key" for the segment ($203M); consolidated net income
        ($509M) comes from the statement."""
        html = _doc(
            _tbl(["", "Consumer Bank", ""], ["(dollars in millions)", "2Q26", "1Q26"],
                 ["Net income (loss) attributable to Key", "203", "174"]),
            _tbl(["(dollars in millions)", "2Q26", "1Q26"],
                 ["Total assets", "188,562", "185,906"],
                 ["Net income", "509", "522"]))
        out = extract_earnings_figures(html, _anchor(
            total_assets=185_906_000_000.0, prior_net_income=522_000_000.0,
            prior_net_interest_income=1_222_000_000.0))
        self.assertEqual(out["net_income"], 509_000_000.0)

    def test_amp_investment_entity_line_is_not_the_company(self):
        html = _doc(_tbl(["($ in millions)", "2Q26", "1Q26"],
                         ["Total assets", "180,000", "178,000"],
                         ["Net income", "1,113", "1,060"],
                         ["Net income (loss) attributable to consolidated investment entities", "(2)", "1"]))
        out = extract_earnings_figures(html, _anchor(
            total_assets=178_000_000_000.0, prior_net_income=1_060_000_000.0,
            prior_net_interest_income=900_000_000.0))
        self.assertEqual(out["net_income"], 1_113_000_000.0)

    def test_sbcf_tax_equivalent_nii_skipped(self):
        """SBCF's highlights "Net interest income²" (footnote: fully taxable
        equivalent) is $182,150K = its "Net interest income including FTE
        adjustment"; GAAP NII $180,395K (10-Q)."""
        html = _doc(_tbl(["(Amounts in thousands)", "2Q'26", "1Q'26"],
                         ["Total assets", "21,360,072", "21,000,000"],
                         ["Net interest income2", "182,150", "178,154"],
                         ["Net Interest Income", "180,395", "176,470"],
                         ["Net interest income including FTE adjustment", "182,150", "178,154"]))
        out = extract_earnings_figures(html, _anchor(
            total_assets=21_000_000_000.0, prior_net_income=31_895_000.0,
            prior_net_interest_income=176_470_000.0))
        self.assertEqual(out["net_interest_income"], 180_395_000.0)

    def test_sfbs_fte_equal_to_gaap_kept(self):
        html = _doc(_tbl(["(In thousands)", "June 30, 2026", "March 31, 2026"],
                         ["Total assets", "17,500,000", "17,200,000"],
                         ["Net interest income", "155,637", "148,148"],
                         ["Net interest income - fully taxable equivalent", "155,637", "148,148"]))
        out = extract_earnings_figures(html, _anchor(
            total_assets=17_200_000_000.0, prior_net_income=60_000_000.0,
            prior_net_interest_income=148_148_000.0))
        self.assertEqual(out["net_interest_income"], 155_637_000.0)


if __name__ == "__main__":
    unittest.main()
