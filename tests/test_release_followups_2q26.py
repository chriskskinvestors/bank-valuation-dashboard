"""Release-extractor follow-ups to the split-header work (2026-10-12).

Fixtures in tests/fixtures/release_followups_2q26/ are the banks' real 2Q26
EX-99.1 tables (EDGAR, verbatim cell text, tag attributes stripped) and, where
named, verbatim prose sentences. Every expected value was checked against the
release's own text; each class below names the value the pre-change module
served (all of them fail on it).

  1. TCBX prose "net income … or $1.25 and $1.08 per basic and diluted share,
     respectively" / "Basic and diluted earnings per common share were $1.25
     per share and $1.08 per share, respectively": diluted EPS is $1.08 — the
     prose reader served the BASIC $1.25.
  2. Old-form date headers under a lone "Six Months Ended" banner were read
     as quarter columns: NKSH six-month diluted EPS $1.57 vs the quarter's
     $0.79 (and six-month dividends $0.75 served as the quarter's), OPBK
     six-month efficiency 60.65 served as 2Q25's (59.25), GBCI six-month NIM
     3.85% served as the quarter's 3.90% (rounding-aware agreement with the
     "3.90" cell).
  3. Same-label disambiguation: KEY ROTCE "from continuing operations" 12.89%
     vs "consolidated" 12.91% (both refused → n/a); SBSI "Net interest margin"
     2.80% vs "Net interest margin (FTE)" 2.90% (both refused → n/a).
"""
import os
import re
import unittest

from data.release_metrics import (_period_header, _table_rows,
                                  extract_release_metrics,
                                  extract_table_metrics)

_FIX = os.path.join(os.path.dirname(__file__), "fixtures",
                    "release_followups_2q26")
Q, PQ, YQ = "2026-06-30", "2026-03-31", "2025-06-30"


def _fixture(t):
    with open(os.path.join(_FIX, f"{t}.html"), encoding="utf-8") as f:
        return f.read()


def _tables(html):
    return [_table_rows(t) for t in
            re.findall(r"(?is)<table[^>]*>(.*?)</table>", html)]


def _prose(html):
    """The fixture's prose sentences only (no tables)."""
    return re.sub(r"(?is)<table[^>]*>.*?</table>", " ", html)


class TestBasicDilutedPairOrder(unittest.TestCase):
    """Item 1 — map "basic and diluted … respectively" by stated order."""

    def test_tcbx_prose_reads_the_diluted_position(self):
        # Prose alone (pre-change: 1.25, the basic figure).
        self.assertEqual(
            extract_release_metrics(_prose(_fixture("TCBX")))["eps_diluted"],
            1.08)

    def test_tcbx_release_and_table_agree(self):
        html = _fixture("TCBX")
        self.assertEqual(extract_release_metrics(html, Q)["eps_diluted"], 1.08)
        # Table row "Diluted earnings per share $ 1.08 $ 0.88 … $ 0.96".
        self.assertEqual(extract_table_metrics(html, Q)["eps_diluted"], 1.08)
        self.assertEqual(extract_table_metrics(html, PQ)["eps_diluted"], 0.88)
        self.assertEqual(extract_table_metrics(html, YQ)["eps_diluted"], 0.96)

    def test_diluted_and_basic_order(self):
        text = ("<p>Diluted and basic earnings per common share were $1.08 "
                "and $1.25, respectively, in the second quarter.</p>")
        self.assertEqual(extract_release_metrics(text)["eps_diluted"], 1.08)

    def test_unanchored_pair_reads_stated_order(self):
        # No "respectively" still maps by the stated order (2026-10-13: NEWT
        # states EPS only as "Basic and Diluted EPS of $0.48 and $0.47"; the
        # unsafe readings — two PERIODS — are refused by the period-list and
        # dilution guards, tests/test_release_eps_pairs). Never the first value.
        text = ("<p>Basic and diluted earnings per common share were $1.25 "
                "per share and $1.08 per share in the second quarter.</p>")
        self.assertEqual(extract_release_metrics(text)["eps_diluted"], 1.08)

    def test_pair_tail_other_than_respectively_is_na(self):
        text = ("<p>Basic and diluted earnings per common share were $1.25 "
                "and $1.08 for the quarter and the year-to-date period, "
                "respectively.</p>")
        self.assertIsNone(extract_release_metrics(text)["eps_diluted"])

    def test_basic_label_is_never_diluted(self):
        for s in ("Basic EPS of $1.25 for the quarter.",
                  "Basic earnings per common share was $1.25 for the quarter."):
            self.assertIsNone(
                extract_release_metrics(f"<p>{s}</p>")["eps_diluted"], s)

    def test_single_value_for_both_still_reads(self):
        text = ("<p>Basic and diluted earnings per common share were $0.50 "
                "for the quarter.</p>")
        self.assertEqual(extract_release_metrics(text)["eps_diluted"], 0.50)


class TestLoneYtdBannerOldForms(unittest.TestCase):
    """Item 2 — a legacy date / 2Q26 header under a lone YTD banner."""

    def test_nksh_six_month_table_refused(self):
        t = _tables(_fixture("NKSH"))
        # "Six Months Ended" over "June 30, 2026 | June 30, 2025".
        self.assertIsNone(_period_header(t[1]))
        # "Three Months Ended" over "June 30, | March 31, | June 30," stays.
        self.assertEqual(_period_header(t[0])[0], [Q, PQ, YQ])

    def test_nksh_quarter_eps_and_no_six_month_dividend(self):
        html = _fixture("NKSH")
        # Pre-change: EPS None (1.57 vs 0.79 disagreed), div_ps 0.75 (H1).
        cur = extract_release_metrics(html, Q)
        self.assertEqual(cur["eps_diluted"], 0.79)
        # v31: the THREE-month table's own row "Dividends declared per common
        # share | $0.75 | $ - | $0.73" states 2Q26 = $0.75 (a semi-annual
        # payer: 1Q26 "-"). It used to read None only because the "-" cell
        # broke the row width; the six-month table stays refused (above).
        self.assertEqual(cur["div_ps"], 0.75)
        self.assertIsNone(extract_table_metrics(html, PQ)["div_ps"])
        self.assertEqual(extract_table_metrics(html, YQ)["eps_diluted"], 0.36)

    def test_opbk_2q_tokens_under_six_month_banner(self):
        html = _fixture("OPBK")
        # "For the Six Months Ended" over "2Q2026 | 2Q2025 | Change".
        self.assertIsNone(_period_header(_tables(html)[1]))
        self.assertEqual(extract_table_metrics(html, Q)["efficiency"], 57.64)
        # Pre-change: 60.65 (the six-month 2025 ratio) as 2Q25's.
        self.assertNotEqual(extract_table_metrics(html, YQ)["efficiency"],
                            60.65)

    def test_gbci_quarter_nim_not_the_six_month(self):
        html = _fixture("GBCI")
        # Pre-change 3.85 — the six-month NIM out-precisioned the "3.90" cell.
        self.assertEqual(extract_table_metrics(html, Q)["nim"], 3.9)
        self.assertEqual(extract_release_metrics(html, Q)["nim"], 3.9)

    def test_combined_quarter_and_ytd_header_still_read(self):
        # TCBX "Three Months Ended | Six Months Ended" — quarter block leads.
        self.assertIsNotNone(_period_header(_tables(_fixture("TCBX"))[0]))
        self.assertEqual(extract_table_metrics(_fixture("SBSI"), PQ)["nim"],
                         2.91)

    def test_ytd_banner_over_trailing_columns_keeps_quarters(self):
        # FNB "For the Six Months Ended June 30," over "2Q26 | 1Q26 | 2Q25 |
        # 2026 | 2025": the banner owns the trailing year columns.
        fnb = _fixture("FNB")
        self.assertEqual(_period_header(_tables(fnb)[0])[0], [Q, PQ, YQ])
        self.assertEqual(extract_table_metrics(fnb, Q)["bv_ps"], 19.34)
        # CTBI "2Q 2026 | 1Q 2026 | 2Q 2025 | YTD 2026 | YTD 2025".
        ctbi = _fixture("CTBI")
        self.assertEqual(extract_table_metrics(ctbi, PQ)["bv_ps"], 47.99)


class TestScopedSameLabel(unittest.TestCase):
    """Item 3a — KEY: consolidated beats continuing operations, explicitly."""

    def setUp(self):
        self.html = _fixture("KEY")

    def test_rotce_and_roe_consolidated(self):
        cur = extract_table_metrics(self.html, Q)
        # "Return on average tangible common equity consolidated (non-GAAP)
        # (B/C) 12.91 %" and the highlights' "From consolidated operations:"
        # section; continuing operations print 12.89.
        self.assertEqual(cur["rotce"], 12.91)
        self.assertEqual(cur["roe"], 10.87)
        yoy = extract_table_metrics(self.html, YQ)
        self.assertEqual(yoy["rotce"], 11.15)
        self.assertEqual(yoy["roe"], 9.31)
        # Both scopes print 13.02 / 11.02 for 1Q26.
        self.assertEqual(extract_table_metrics(self.html, PQ)["rotce"], 13.02)

    def test_continuing_only_still_serves(self):
        # Only the continuing-operations rows (KEY's page-1 highlights,
        # table 0): no consolidated row → nothing steps aside.
        only = re.findall(r"(?is)<table[^>]*>.*?</table>", self.html)[0]
        self.assertEqual(extract_table_metrics(only, Q)["rotce"], 12.89)

    def test_unscoped_disagreement_still_refused(self):
        rows = ("<tr><td></td><td>June 30, 2026</td><td>March 31, 2026</td></tr>"
                "<tr><td>Return on average tangible common equity</td>"
                "<td>12.89 %</td><td>13.02 %</td></tr>")
        other = rows.replace("12.89", "12.91")
        html = f"<table>{rows}</table><table>{other}</table>"
        self.assertIsNone(extract_table_metrics(html, Q)["rotce"])


class TestPlainVsTaxEquivalentNim(unittest.TestCase):
    """Item 3b — the NIM rule tells the plain row from the TE/FTE row."""

    def test_sbsi_plain_nim_every_column(self):
        html = _fixture("SBSI")
        # "Net interest margin 2.80 % 2.91 % … 2.82 %" vs "(FTE) (1) 2.90 %
        # 3.01 % … 2.95 %"; prose: "net interest margin and tax-equivalent
        # net interest margin (1) decreased to 2.80% and 2.90%, respectively".
        self.assertEqual(extract_release_metrics(html, Q)["nim"], 2.8)
        self.assertEqual(extract_table_metrics(html, PQ)["nim"], 2.91)
        self.assertEqual(extract_table_metrics(html, YQ)["nim"], 2.82)

    def test_te_narration_yields_to_plain_row(self):
        # FRME "Net interest margin on a fully taxable equivalent basis 1 of
        # 3.38%" — its GAAP row is 3.25% (prior 3.21, year-ago 3.10).
        html = _fixture("FRME")
        self.assertEqual(extract_release_metrics(html, Q)["nim"], 3.25)
        self.assertEqual(extract_table_metrics(html, PQ)["nim"], 3.21)

    def test_unmarked_narration_equal_to_te_row(self):
        # WTFC quote "Net interest margin … at 3.52%" = its FTE row; GAAP
        # row 3.50% (prior 3.54%).
        html = _fixture("WTFC")
        self.assertEqual(extract_release_metrics(html, Q)["nim"], 3.5)
        self.assertEqual(extract_table_metrics(html, PQ)["nim"], 3.54)

    def test_variant_row_is_not_plain_evidence(self):
        # TCBK prints only "(FTE)" rows and "Net interest margin less effect
        # of acquired loan discount accretion" — no plain row, so the FTE
        # basis stands and the variant never serves alone.
        html = _fixture("TCBK")
        self.assertEqual(extract_release_metrics(html, Q)["nim"], 4.11)
        self.assertIsNone(extract_table_metrics(html, PQ)["nim"])

    def test_te_only_release_unchanged(self):
        rows = ("<tr><td></td><td>June 30, 2026</td><td>March 31, 2026</td></tr>"
                "<tr><td>Net interest margin (FTE)</td>"
                "<td>3.01 %</td><td>2.98 %</td></tr>")
        self.assertEqual(extract_table_metrics(f"<table>{rows}</table>", Q)
                         ["nim"], 3.01)


if __name__ == "__main__":
    unittest.main()
