"""Release-extractor fixes from the 2Q26 NIM / efficiency audit (2026-10-13).

Fixtures are the banks' real 2Q26 EX-99.1 text from EDGAR (verbatim cells
and sentences, tag attributes stripped; URL in each file's header comment):
tests/fixtures/release_basis_2q26/ (FHN, FCCO, CBFV, COFS, GBCI) plus the
existing EWBC (release_exhibit_3q26) and SBSI (release_followups_2q26)
excerpts. Every expected value was read off the release's own text.

  1. FHN — segment tables never supply consolidated metrics: the WHOLESALE
     page's "Net interest margin 2.73 / 2.68 / 2.67" is the segment's; the
     firm's is 3.49 / 3.52 / 3.40.
  2. EWBC — an in-body period row ("June 30, 2026 | June 30, 2025 |
     Yr-o-Yr") re-declares the columns: the six-month block's "Net interest
     margin 3.46 | 3.35 | 11" carries 3 values like the 3-period header but
     is 6M-2026 / 6M-2025 / bps change. Release text: "Net interest margin
     was 3.43% in the second quarter of 2026, a 6 basis-point decline from
     the prior quarter and an 8 basis-point increase from the second quarter
     of 2025" (3.49 / 3.35).
  3. Precision is the PRINTED digits: "3.90" is two decimals (float 3.9
     reprs as one, widening agreement to ±0.05 — GBCI's six-month 3.85 then
     agreed with, and out-precisioned, the quarter's 3.90).
  4. FCCO — a month row "June 30, | March 31, | June 30," over SIX year
     cells (each month spans 2026 | 2025) was zipped to Jun-26 / Mar-25 /
     Jun-26; unequal counts now refuse the header.
  5. Efficiency follows the NIM plain-vs-FTE rule: SBSI "Efficiency ratio
     54.42" vs "(FTE) (1) 52.96" (both refused → n/a); EWBC 36.73 vs 36.68.
  6. A prose NIM explicitly marked GAAP is plain-basis evidence: CBFV's
     "Net Interest Margin (NIM) (GAAP) increased to 3.68%" sat beside its
     FTE-only table's prior 3.88 / year-ago 3.55; COFS's "GAAP net interest
     margin was 3.59%" beside FTE 3.67 / 3.70. The FTE columns are n/a.
"""
import os
import re
import unittest

from data import release_metrics as rm

_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
Q, PQ, YQ = "2026-06-30", "2026-03-31", "2025-06-30"


def _fx(name, sub="release_basis_2q26"):
    with open(os.path.join(_DIR, sub, name), encoding="utf-8") as f:
        return f.read()


def _cols(html, key):
    return tuple(rm.extract_table_metrics(html, q).get(key) for q in (Q, PQ, YQ))


def _tables(html):
    return [rm._table_rows(t) for t in
            re.findall(r"(?is)<table[^>]*>(.*?)</table>", html)]


class TestSegmentTablesNeverConsolidated(unittest.TestCase):
    """Item 1 — FHN."""

    def test_fhn_nim_is_the_firms(self):
        html = _fx("FHN.html")
        self.assertEqual(_cols(html, "nim"), (3.49, 3.52, 3.40))
        self.assertEqual(_cols(html, "efficiency"), (59.88, 58.54, 59.20))

    def test_segment_table_alone_is_na(self):
        # Only the WHOLESALE page: its rows never stand in for the firm's.
        html = _fx("FHN.html")
        seg = html[html.index("<p>WHOLESALE"):]
        self.assertEqual(_cols(seg, "nim"), (None, None, None))

    def test_division_section_is_a_segment_block(self):
        rows = ("<tr><td></td><td>2Q26</td><td>1Q26</td></tr>"
                "<tr><td>Net interest margin</td><td>3.49 %</td><td>3.52 %</td></tr>"
                "<tr><td>Mortgage Division</td></tr>"
                "<tr><td>Net interest margin</td><td>2.73 %</td><td>2.68 %</td></tr>")
        self.assertEqual(rm.extract_table_metrics(f"<table>{rows}</table>", Q)
                         ["nim"], 3.49)


class TestInBodyPeriodRow(unittest.TestCase):
    """Item 2 — EWBC."""

    def setUp(self):
        self.html = _fx("EWBC_2026-07-21_ex99_1.htm", "release_exhibit_3q26")

    def test_ewbc_nim_three_columns(self):
        self.assertEqual(_cols(self.html, "nim"), (3.43, 3.49, 3.35))
        self.assertEqual(
            rm.extract_release_metrics(self.html, Q)["nim"], 3.43)

    def test_six_month_rows_closed_without_their_banner(self):
        # The same table with its "Six Months Ended (1)" banner text removed:
        # only the period row "June 30, 2026 | June 30, 2025 | Yr-o-Yr" is
        # left to mark the block. Pre-change: 3.43 vs 3.46 (current) and
        # 3.49 vs 3.35 (prior) disagreed → n/a, and the 6M rows read as
        # 2Q26 / 1Q26.
        self.assertEqual(self.html.count("Six Months Ended (1)"), 1)
        html = self.html.replace("Six Months Ended (1)", "")
        self.assertEqual(_cols(html, "nim"), (3.43, 3.49, 3.35))
        self.assertEqual(_cols(html, "roa"), (1.75, 1.79, 1.62))

    def test_header_set_redeclared_reopens_rows(self):
        rows = ("<tr><td></td><td>June 30, 2026</td><td>March 31, 2026</td></tr>"
                "<tr><td>Return on average assets</td><td>1.10 %</td><td>1.05 %</td></tr>"
                "<tr><td></td><td>June 30, 2026</td><td>June 30, 2025</td></tr>"
                "<tr><td>Net interest margin</td><td>3.46 %</td><td>3.35 %</td></tr>"
                "<tr><td></td><td>June 30, 2026</td><td>March 31, 2026</td></tr>"
                "<tr><td>Net interest margin</td><td>3.43 %</td><td>3.49 %</td></tr>")
        html = f"<table>{rows}</table>"
        self.assertEqual(rm.extract_table_metrics(html, Q)["nim"], 3.43)
        self.assertEqual(rm.extract_table_metrics(html, PQ)["nim"], 3.49)
        self.assertEqual(rm.extract_table_metrics(html, Q)["roa"], 1.10)


class TestPrintedPrecision(unittest.TestCase):
    """Item 3 — trailing zeros count."""

    def test_cells_keep_printed_decimals(self):
        vals = rm._row_values(["NIM", "3.90 %", "3.85", "(0.10)", "12"])
        self.assertEqual([rm._dec(v) for v in vals], [2, 2, 2, 0])
        self.assertEqual(vals, [3.9, 3.85, -0.1, 12.0])

    def test_gbci_six_month_cell_never_agrees_with_390(self):
        # GBCI's three-month Income Summary prints "Net interest margin
        # (tax-equivalent) 3.90 %"; its six-month table "3.85 %". With the
        # six-month banner removed (so the twin read is NOT refused by the
        # banner rule), the two tables disagree at two decimals → n/a.
        # Pre-change: 3.85 served (±0.05 "agreement", the more "precise").
        html = _fx("GBCI.html")
        self.assertEqual(rm.extract_table_metrics(html, Q)["nim"], 3.90)
        bare = html.replace("Six Months ended", "")
        self.assertIsNone(rm.extract_table_metrics(bare, Q)["nim"])

    def test_one_decimal_headline_still_agrees(self):
        # CFG-style: 61.1 (one decimal printed) vs 61.08 still agree.
        rows = "<tr><td></td><td>2Q26</td><td>1Q26</td></tr>" \
               "<tr><td>Efficiency ratio</td><td>{} %</td><td>60.00 %</td></tr>"
        html = (f"<table>{rows.format('61.1')}</table>"
                f"<table>{rows.format('61.08')}</table>")
        self.assertEqual(rm.extract_table_metrics(html, Q)["efficiency"], 61.08)
        # …but "61.10" is two decimals: 61.10 vs 61.08 → n/a.
        html = (f"<table>{rows.format('61.10')}</table>"
                f"<table>{rows.format('61.08')}</table>")
        self.assertIsNone(rm.extract_table_metrics(html, Q)["efficiency"])


class TestSplitHeaderNeverTruncated(unittest.TestCase):
    """Item 4 — FCCO."""

    def test_fcco_three_months_over_six_years_refused(self):
        html = _fx("FCCO.html")
        self.assertIsNone(rm._period_header(_tables(html)[0]))
        for key in ("nim", "efficiency", "roa", "roe"):
            self.assertEqual(_cols(html, key), (None, None, None), key)

    def test_equal_counts_still_pair(self):
        rows = ("<tr><td>June</td><td>March</td><td>June</td></tr>"
                "<tr><td>2026</td><td>2026</td><td>2025</td></tr>"
                "<tr><td>Net interest margin</td><td>3.50 %</td><td>3.35 %</td>"
                "<td>3.19 %</td></tr>")
        self.assertEqual(rm._period_header(rm._table_rows(rows))[0], [Q, PQ, YQ])


class TestPlainVsFteEfficiency(unittest.TestCase):
    """Item 5."""

    def test_sbsi_plain_efficiency_every_column(self):
        html = _fx("SBSI.html", "release_followups_2q26")
        # "Efficiency ratio 54.42 % 56.44 % … 55.67 %" vs "Efficiency ratio
        # (FTE) (1) 52.96 % 54.98 % … 53.70 %"; prose: "Our efficiency ratio
        # and tax-equivalent efficiency ratio (1) were 54.42% and 52.96%,
        # respectively … compared to 55.67% and 53.70%".
        self.assertEqual(_cols(html, "efficiency"), (54.42, 56.44, 55.67))
        self.assertEqual(rm.extract_release_metrics(html, Q)["efficiency"], 54.42)

    def test_ewbc_plain_efficiency(self):
        html = _fx("EWBC_2026-07-21_ex99_1.htm", "release_exhibit_3q26")
        self.assertEqual(_cols(html, "efficiency"), (36.73, 36.23, 36.41))

    def test_segment_row_is_no_basis_evidence(self):
        # Citi 3Q26 supplement: the firmwide "Efficiency ratio (total
        # operating expenses/total revenues, net) … 57.4% | 58.5%" must not
        # step aside for a Services-page "Efficiency ratio 44% | 45%".
        html = _fx("C_2026-10-13_ex99_2.htm", "release_exhibit_3q26")
        self.assertNotIn("efficiency", rm._plain_bases(html))
        self.assertEqual(rm.extract_table_metrics(html, "2026-09-30")
                         ["efficiency"], 58.5)

    def test_fte_only_release_keeps_fte(self):
        rows = ("<tr><td></td><td>June 30, 2026</td><td>March 31, 2026</td></tr>"
                "<tr><td>Efficiency ratio (FTE) (1)</td>"
                "<td>52.96 %</td><td>54.98 %</td></tr>")
        self.assertEqual(rm.extract_table_metrics(f"<table>{rows}</table>", Q)
                         ["efficiency"], 52.96)

    def test_te_efficiency_narration_yields_to_plain_row(self):
        rows = ("<tr><td></td><td>June 30, 2026</td><td>March 31, 2026</td></tr>"
                "<tr><td>Efficiency ratio</td><td>54.42 %</td><td>56.44 %</td></tr>"
                "<tr><td>Efficiency ratio (FTE)</td><td>52.96 %</td>"
                "<td>54.98 %</td></tr>")
        html = ("<p>The efficiency ratio on a tax-equivalent basis was "
                f"52.96% for the quarter.</p><table>{rows}</table>")
        self.assertEqual(rm.extract_release_metrics(html, Q)["efficiency"], 54.42)


class TestMixedBasisProseVsTable(unittest.TestCase):
    """Item 6 — GAAP-marked prose NIM vs an FTE-only table."""

    def test_cbfv(self):
        html = _fx("CBFV.html")
        self.assertEqual(rm.extract_release_metrics(html, Q)["nim"], 3.68)
        self.assertEqual(_cols(html, "nim"), (None, None, None))

    def test_cofs(self):
        html = _fx("COFS.html")
        self.assertEqual(rm.extract_release_metrics(html, Q)["nim"], 3.59)
        self.assertEqual(_cols(html, "nim"), (None, None, None))

    def test_table_alone_keeps_fte(self):
        # Without the GAAP narration the FTE rows are the release's basis.
        html = re.sub(r"(?s)<p>.*?</p>", "", _fx("CBFV.html"))
        self.assertEqual(_cols(html, "nim"), (3.73, 3.88, 3.55))

    def test_non_gaap_marker_is_not_plain(self):
        rows = ("<tr><td></td><td>June 30, 2026</td><td>March 31, 2026</td></tr>"
                "<tr><td>Net interest margin (FTE)</td>"
                "<td>3.73 %</td><td>3.88 %</td></tr>")
        html = ("<p>Net interest margin (non-GAAP) was 3.73% for the "
                f"quarter.</p><table>{rows}</table>")
        self.assertEqual(rm.extract_table_metrics(html, PQ)["nim"], 3.88)


class TestCorpusDiffCases(unittest.TestCase):
    """Cases the 313-release old/new diff surfaced, each value read off the
    release's own text."""

    def test_cban_division_table(self):
        # "Banking Division" / "Mortgage Banking Division" / "Small Business
        # Specialty Lending Division" sections, then "Total Consolidated":
        # revenue = 29,869 + 12,158 (pre-change: division rows disagreed →
        # n/a).
        html = _fx("CBAN.html")
        self.assertEqual(_cols(html, "rev_sum"), (42_027_000.0, 39_895_000.0,
                                                  32_483_000.0))

    def test_fsbc_yoy_block_never_read_as_prior(self):
        # Q/Q block "Net interest margin 3.63 % 3.70 %", then a Y/Y block
        # re-declared "June 30, 2026 | June 30, 2025": "3.63 % 3.53 %".
        # Prose: "contracted by seven basis points from 3.70% for the three
        # months ended March 31, 2026". Pre-change: 3.53 read as 1Q26 → n/a.
        html = _fx("FSBC.html")
        self.assertEqual(rm.extract_table_metrics(html, PQ)["nim"], 3.70)
        self.assertEqual(rm.extract_table_metrics(html, Q)["nim"], 3.63)

    def test_ffin_dec_sep_block_never_read_as_jun_mar(self):
        # Pre-change the Dec/Sep block's "Loans … 6.68" agreed (±0.05) with
        # June's "6.70" and out-precisioned it.
        html = _fx("FFIN.html")
        self.assertEqual(rm.extract_table_metrics(html, Q)["loan_yield"], 6.70)
        self.assertEqual(rm.extract_table_metrics(html, PQ)["loan_yield"], 6.67)

    def test_lnkb_five_date_block_closed(self):
        # Under "12/31/2025 | 9/30/2025 | 12/31/2024" the block re-declared
        # "12/31/2025 | 9/30/2025 | 6/30/2025 | 3/31/2025 | 12/31/2024":
        # pre-change the year-ago column read 6/30/2025's CET1 11.51 (its
        # 12/31/2024 value is 10.74).
        html = _fx("LNKB.html")
        self.assertIsNone(
            rm.extract_table_metrics(html, "2024-12-31")["cet1_ratio"])
        self.assertEqual(rm.extract_table_metrics(html, "2025-12-31")["nim"],
                         3.74)

    def test_indb_header_continuation_is_not_a_block(self):
        # "Jun 2026 vs. | Jun 2026 vs." over "Mar 2026 | Jun 2025" continues
        # the header's change cells — rows below still read.
        html = _fx("INDB.html")
        self.assertIsNotNone(rm._period_header(_tables(html)[0]))
        rows = _tables(html)[0]
        st = {"ytd": False, "seg": False, "var": False, "section": "",
              "outer": "", "qends": rm._period_header(rows)[0]}
        hdr_i = rm._period_header(rows)[1]
        for cells in rows[hdr_i + 1:]:
            lab = next((c for c in cells if c), None)
            if lab:
                rm._skip_row(cells, lab, st)
        self.assertFalse(st.get("sub", False))

    def test_care_fte_titled_table_rows_are_not_plain(self):
        # "NET INTEREST MARGIN (FTE) (QTD AVERAGES)" table: "Net Interest
        # Margin 4 | 3.40" is the FTE figure; GAAP rows 3.38 / 3.07 / 2.80
        # (prose: "Net interest margin was 3.38% … compared to 3.07% … and
        # 2.80%"). Pre-change: 3.38 only by the ±0.05 "3.40" artifact.
        html = _fx("CARE.html")
        self.assertEqual(_cols(html, "nim"), (3.38, 3.07, 2.80))
        self.assertEqual(rm.extract_release_metrics(html, Q)["nim"], 3.38)

    def test_te_marker_in_prose_above_a_table_is_no_title(self):
        # SBSI 2Q26's reconciliation table follows the sentence "… along with
        # the calculation of total revenue, adjusted noninterest expense,
        # efficiency ratio (FTE), net interest margin (FTE) and net interest
        # spread (FTE) for the applicable periods presented." — prose, not
        # an upper-case heading, even where a 100-char cut starts at "FTE)".
        sent = ("<p>along with the calculation of total revenue, adjusted "
                "noninterest expense, efficiency ratio (FTE), net interest "
                "margin (FTE) and net interest spread (FTE) for the applicable "
                "periods presented.</p>")
        rows = ("<tr><td></td><td>June 30, 2026</td><td>March 31, 2026</td></tr>"
                "<tr><td>Net interest margin</td><td>2.80 %</td><td>2.91 %</td></tr>"
                "<tr><td>Net interest margin (FTE) (1)</td><td>2.90 %</td>"
                "<td>3.01 %</td></tr>")
        for cut in range(0, 40, 3):
            html = sent[cut:] + f"<table>{rows}</table>"
            self.assertEqual(rm.extract_table_metrics(html, Q)["nim"], 2.80, cut)

    def test_wsbf_ytd_row_is_not_a_quarter(self):
        # "Net interest margin - QTD 3.03 %" vs "- YTD 3.00 %" (six months);
        # prose: "quarterly net interest margin hit 3.03%".
        html = _fx("WSBF.html")
        self.assertEqual(_cols(html, "nim"), (3.03, 2.97, 2.60))
        self.assertEqual(_cols(html, "roa"), (1.52, 1.10, 1.39))

    def test_sfnc_ytd_nco_row_is_not_a_quarter(self):
        # "Net charge-off ratio (annualized) 0.20" vs "… YTD (annualized)
        # 0.21"; pre-change the YTD 0.21 out-precisioned the "0.20" cell.
        self.assertEqual(rm.extract_table_metrics(_fx("SFNC.html"), Q)
                         ["nco_ratio"], 0.20)

    def test_esq_unpaired_month_year_header_refused(self):
        # "June 30, | March 31, | June 30, | June 30," over five years —
        # pairing is unprovable without colspans: refused (n/a), the cost
        # of item 4 (pre-change read positionally).
        html = _fx("ESQ.html")
        self.assertIsNone(rm._period_header(_tables(html)[0]))


if __name__ == "__main__":
    unittest.main()
