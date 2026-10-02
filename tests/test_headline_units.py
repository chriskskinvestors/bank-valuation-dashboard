"""(2026-10-02) Headline release figures: per-row dollar units + prior-quarter guards.

extract_earnings_figures used ONE dollar scale per release, anchored on its
first "Total assets" row. Releases are not one unit — $-millions summaries
sit beside $-thousands statements — so production served, e.g., BPOP Q2-2026
net interest income as $693.4 BILLION (10-Q: $693,419K) and BKU net income as
$70,700 (10-Q: $70,663K). Measured over the 320 releases of the 2026-09-30
sweep against each bank's same-quarter 10-Q XBRL: 57 of 959 served dollar /
EPS figures were wrong; after this change 19 (all pre-existing row/column
semantics) of 1,101, none newly wrong.

Each pin is a real release's shape with its real numbers (anchor = the prior
quarter's 10-Q, as production passes it):
  1. Each row takes the unit caption governing it — its table's caption row,
     else the caption printed just before the table (BPOP, BKU); a share-count
     unit or release prose is not a dollar caption; two units → ambiguous.
  2. An uncaptioned row takes the unit its own table anchors to (BSBK), else
     the one scale its prior-quarter flow band admits (CMTV).
  3. Flows must land in a band of the prior quarter: NII 0.6–1.6× prior NII,
     net income 0.001–5× prior NII (FNWB: prior net income was $6K); a 10-K
     anchor supplies FY ÷ 4 (CLBK); an annual first column fails (BCTF).
  4. Balance-sheet rows under an "average balance(s)" heading are skipped
     (HBAN), a "weighted average rate" column is not one (CLBK).
  5. A value equal to the prior quarter-end's tagged total is the prior
     column (ASRV); deposits ÷ assets must hold the prior ratio (BPOP's
     segment deposits); a printed figure coarser than 0.5% is n/a (BHB "15").

Run: python -m unittest tests.test_headline_units
"""
import unittest

from data.sec_earnings_8k import (
    _quarter_flow, _too_coarse, _unit_scale, extract_earnings_figures,
)
from data.sec_filing_scraper import Fact


def _table(*rows, caption=None):
    head = f"<tr><td>{caption}</td><td>2026</td><td>2026</td></tr>" if caption else ""
    body = "".join(f"<tr><td>{lbl}</td>" + "".join(f"<td>{c}</td>" for c in cells)
                   + "</tr>" for lbl, *cells in rows)
    return f"<table>{head}{body}</table>"


def _doc(*parts):
    return ("<html><body>" + "".join(parts) + "</body></html>").encode("utf-8")


BPOP_ANCHOR = {"total_assets": 76_131_018_000.0, "total_deposits": 67_611_316_000.0,
               "prior_net_income": 245_674_000.0,
               "prior_net_interest_income": 670_180_000.0}


class TestUnitCaptions(unittest.TestCase):
    def test_caption_grammar(self):
        self.assertEqual(_unit_scale("(Dollars in thousands, except per share data)"), 1e3)
        self.assertEqual(_unit_scale("($ in millions)"), 1e6)
        self.assertEqual(_unit_scale("(dollars in 000s, except per share data)"), 1e3)
        self.assertEqual(_unit_scale("($000s)"), 1e3)
        # PNFP: a share-count unit is not the dollar unit.
        self.assertEqual(_unit_scale(
            "(in millions, except per share data, share count in thousands)"), 1e6)
        # Release prose is not a caption.
        self.assertIsNone(_unit_scale("Net income was $15.2 million for the quarter"))
        # PNC: one table, two units → ambiguous, never a guess.
        self.assertEqual(_unit_scale("In millions … In billions"), "ambiguous")

    def test_bpop_mixed_units_each_row_at_its_own_scale(self):
        """BPOP Q2-2026: the summary is "(In millions)" (total assets 78,972),
        the income statement "(In thousands)" (NII 693,419). One release-wide
        scale (anchored on total assets → ×1e6) served NII as $693.4B."""
        html = _doc(
            "<p>(In millions)</p>", _table(("Total assets", "78,972", "76,131")),
            "<p>(In thousands)</p>",
            _table(("Net interest income", "693,419", "670,180")))
        out = extract_earnings_figures(html, BPOP_ANCHOR)
        self.assertEqual(out["total_assets"], 78_972_000_000.0)
        self.assertEqual(out["net_interest_income"], 693_419_000.0)   # 10-Q: $693,419K

    def test_bku_caption_row_inside_the_table(self):
        """BKU Q2-2026: net income 70.7 in a "($ in millions)" table, the
        balance sheet in thousands — read as $70,700 before (10-Q: $70,663K)."""
        html = _doc(
            _table(("Total assets", "34,882,242", "35,358,613"),
                   caption="(in thousands)"),
            _table(("Net income", "70.7", "61.9"), caption="($ in millions)"))
        out = extract_earnings_figures(html, {
            "total_assets": 35_358_613_000.0, "prior_net_income": 61_875_000.0,
            "prior_net_interest_income": 248_987_000.0})
        self.assertAlmostEqual(out["net_income"], 70_700_000.0)

    def test_ambiguous_caption_is_na(self):
        html = _doc(_table(("Total assets", "78,972", "76,131"),
                           ("Net interest income", "693,419", "670,180"),
                           caption="In millions / In billions"))
        out = extract_earnings_figures(html, BPOP_ANCHOR)
        self.assertIsNone(out["total_assets"])
        self.assertIsNone(out["net_interest_income"])


class TestUncaptionedRows(unittest.TestCase):
    def test_bsbk_table_anchored_raw_dollars(self):
        """BSBK prints a raw-dollar summary with no caption beside a captioned
        $-thousands table. The summary's own total assets anchors at ×1, so
        its net income is $747,522 (10-Q: $747,522)."""
        html = _doc(
            _table(("Total assets", "874,987,124", "877,245,065"),
                   ("Net income", "747,522", "705,946")),
            _table(("Total loans", "612,004", "600,113"), caption="(in thousands)"))
        out = extract_earnings_figures(html, {
            "total_assets": 877_245_065.0, "prior_net_income": 705_946.0,
            "prior_net_interest_income": 4_426_491.0})
        self.assertEqual(out["total_assets"], 874_987_124.0)
        self.assertEqual(out["net_income"], 747_522.0)

    def test_cmtv_scale_from_the_prior_quarter_band(self):
        """CMTV: uncaptioned raw-dollar statements (total assets anchors at ×1)
        beside a captioned $-thousands table, so the release has no single
        unit and the income statement's own table anchors nothing. Only ×1
        lands NII $11,247,806 in 0.6–1.6× the prior quarter's $10,947,880."""
        html = _doc(
            _table(("Total assets", "1,172,748,714", "1,235,284,772")),
            _table(("Total loans", "903,112", "899,540"), caption="(in thousands)"),
            _table(("Net interest income", "11,247,806", "10,947,880")))
        out = extract_earnings_figures(html, {
            "total_assets": 1_235_284_772.0, "prior_net_income": 4_369_102.0,
            "prior_net_interest_income": 10_947_880.0})
        self.assertEqual(out["net_interest_income"], 11_247_806.0)


class TestFlowBands(unittest.TestCase):
    def test_fnwb_net_income_bounded_by_prior_nii(self):
        """FNWB earned $6K in Q1 and $308K in Q2 (10-Q). A band on prior net
        income (51×) would reject it; prior NII ($14.44M) bounds it."""
        html = _doc(_table(("Total assets", "2,124,835", "2,133,443"),
                           ("Net income", "308", "6"), caption="(in thousands)"))
        out = extract_earnings_figures(html, {
            "total_assets": 2_133_443_000.0, "prior_net_income": 6_000.0,
            "prior_net_interest_income": 14_440_000.0})
        self.assertEqual(out["net_income"], 308_000.0)

    def test_no_prior_flows_no_flows(self):
        html = _doc(_table(("Total assets", "2,124,835", "2,133,443"),
                           ("Net income", "308", "6"), caption="(in thousands)"))
        out = extract_earnings_figures(html, {"total_assets": 2_133_443_000.0})
        self.assertIsNotNone(out["total_assets"])
        self.assertIsNone(out["net_income"])

    def test_bctf_annual_first_column_rejected(self):
        """BCTF's table runs "Year Ended | Quarter Ended": NII 15,175 (FY) where
        the quarter was 3,584. Its anchor is a 10-K (FY NII ÷ 4 = $3,793,750);
        the annual figure is 4× → out of the 0.6–1.6 band → n/a."""
        html = _doc(_table(("Total assets", "581,300", "581,265"),
                           ("Net interest income", "15,175", "18,447", "3,584"),
                           caption="(dollars in 000s, except per share data)"))
        out = extract_earnings_figures(html, {
            "total_assets": 581_265_000.0, "prior_net_income": -850_500.0,
            "prior_net_interest_income": 3_793_750.0})
        self.assertIsNone(out["net_interest_income"])

    def test_segment_release_without_an_anchored_balance_sheet_serves_no_flows(self):
        """C / RJF: when no total-assets / deposits row anchors to the 10-Q the
        release is segment-first, and its first "Net income" is a segment's."""
        html = _doc(_table(("Total assets", "37,341", "36,900"),       # segment
                           ("Net income", "2,584", "2,410"), caption="($ in millions)"))
        out = extract_earnings_figures(html, {
            "total_assets": 2_620_000_000_000.0, "prior_net_income": 4_100_000_000.0,
            "prior_net_interest_income": 14_000_000_000.0})
        self.assertIsNone(out["total_assets"])
        self.assertIsNone(out["net_income"])

    def test_quarter_flow_falls_back_to_fy_over_4(self):
        """A 10-K tags no quarter: CLBK's Q1 release is anchored on it. FY NII
        $221,634K ÷ 4 = $55,408.5K (the value its anchor carries)."""
        facts = [Fact("us-gaap:InterestIncomeExpenseNet", 221_634_000.0,
                      "2025-12-31", "2025-01-01", {}, "USD")]
        self.assertEqual(_quarter_flow(facts, ("InterestIncomeExpenseNet",),
                                       "2025-12-31"), 55_408_500.0)
        facts.append(Fact("us-gaap:InterestIncomeExpenseNet", 57_100_000.0,
                          "2025-12-31", "2025-10-01", {}, "USD"))
        self.assertEqual(_quarter_flow(facts, ("InterestIncomeExpenseNet",),
                                       "2025-12-31"), 57_100_000.0)


class TestRowSemantics(unittest.TestCase):
    def test_hban_average_balances_skipped(self):
        """HBAN's first "Total deposits" ($223.4B) is under "Table 6 – Average
        Liabilities"; the period-end figure is $222.5B (10-Q $222,466M)."""
        html = _doc(
            "<p>Table 6 – Average Liabilities</p>",
            _table(("Total deposits", "223.4", "204.6"), caption="($ in billions)"),
            "<p>Consolidated Balance Sheets</p>",
            _table(("Total deposits", "222,466", "223,482"), caption="(dollars in millions)"))
        out = extract_earnings_figures(html, {
            "total_assets": 285_372_000_000.0, "total_deposits": 223_482_000_000.0})
        self.assertEqual(out["total_deposits"], 222_466_000_000.0)

    def test_average_balance_header_row(self):
        """MCHB / AFBI head their average tables "AverageBalance" in one cell."""
        html = _doc(
            _table(("(dollars in thousands)", "AverageBalance", "Interest"),
                   ("Total assets", "21,318,253", "")),
            _table(("Total assets", "21,230,839", "21,388,955"),
                   caption="(dollars in thousands)"))
        out = extract_earnings_figures(html, {"total_assets": 21_388_955_000.0})
        self.assertEqual(out["total_assets"], 21_230_839_000.0)

    def test_clbk_weighted_average_rate_column_is_not_an_average_table(self):
        html = _doc(_table(("(In thousands)", "Balance", "Weighted Average Rate"),
                           ("Total assets", "11,010,507", ""),
                           ("Total deposits", "8,372,014", "2.16")))
        out = extract_earnings_figures(html, {
            "total_assets": 11_018_793_000.0, "total_deposits": 8_444_079_000.0})
        self.assertEqual(out["total_deposits"], 8_372_014_000.0)

    def test_asrv_prior_quarter_column(self):
        """ASRV's table runs "1QTR | 2QTR": its first total assets, $1,472,654K,
        is exactly the Q1 10-Q's — the prior quarter, not the release's."""
        html = _doc(_table(("Total assets", "1,472,654", "1,460,481"),
                           caption="(Dollars in thousands)"))
        out = extract_earnings_figures(html, {"total_assets": 1_472_654_000.0})
        self.assertIsNone(out["total_assets"])

    def test_bpop_segment_deposits_break_the_deposit_ratio(self):
        """BPOP's first "Total deposits" is Banco Popular de PR's $58.7B (the
        10-Q: $70.2B). Inside the ±band alone, but deposits ÷ assets drops
        0.888 → 0.743 — both figures go n/a rather than one wrong one."""
        html = _doc(
            "<p>(In millions)</p>", _table(("Total assets", "78,972", "76,131")),
            "<p>(In thousands)</p>", _table(("Total deposits", "58,669,964", "57,100,000")))
        out = extract_earnings_figures(html, BPOP_ANCHOR)
        self.assertIsNone(out["total_assets"])
        self.assertIsNone(out["total_deposits"])

    def test_bhb_coarse_millions_figure(self):
        """BHB's $-millions summary prints net income "15" (10-Q $15,221K):
        ±$0.5M is 3.3% — shown as "$15.0M" it reads as precise. n/a."""
        self.assertTrue(_too_coarse(15.0))
        self.assertFalse(_too_coarse(14.3))
        self.assertFalse(_too_coarse(37_919.0))
        html = _doc(_table(("Total assets", "4,742.5", "4,676.2"),
                           ("Net income", "15", "13"), caption="(in millions)"))
        out = extract_earnings_figures(html, {
            "total_assets": 4_676_228_000.0, "prior_net_income": 12_942_000.0,
            "prior_net_interest_income": 36_812_000.0})
        self.assertIsNone(out["net_income"])


if __name__ == "__main__":
    unittest.main()
