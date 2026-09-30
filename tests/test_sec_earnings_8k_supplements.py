"""Book value per share from SUPPLEMENTARY exhibits (EX-99.2+) of the earnings
8-K — data/sec_earnings_8k.

The 2026-09-30 universe sweep found releases whose EX-99.1 prints no tangible
book value while another exhibit of the SAME 8-K does. Fixtures reproduce the
real row structures (colspans, '$' decoration cells, formula-reference cells):

  RBCAA 2Q26 (8-K 0001104659-26-086487, rbcaa-20260724xex99d2.htm, EX-99.2):
    "Selected Data and Ratios" — "Book value per share (3)" $58.92,
    "Tangible book value per share (3)" 56.44 under "Jun. 30, 2026"; the
    reconciliation repeats them as "Book value per share - GAAP (a/e)" /
    "Tangible book value per share - Non-GAAP (c/e)". Hand-check:
    (1,156,797 − 40,516 goodwill − 6,783 MSR − 1,343 CDI) / 19,635 = 56.437;
    1,156,797 / 19,635 = 58.915. The release deducts MSRs, so our
    reconstruction (56.85) legitimately differs — release-first.
  FCNCA 2Q26 (8-K 0000798941-26-000027, ex_993financialsupplement-.htm,
    EX-99.3): "Book value per share | x/dd | $1,767.79" and "Tangible book
    value per common share (non-GAAP) | aa/dd | 1,722.35" under
    "June 30,2026"; 1,722.35 × 11,390,407 = $19,618.2M = its TCE row 19,618.

A supplementary exhibit's column order is NOT assumed: a row is read only when
the header over its latest-quarter column names the release quarter-end.

Run: python -m unittest tests.test_sec_earnings_8k_supplements
"""
import unittest
from unittest.mock import patch

from data import sec_earnings_8k as s8k
from data.sec_earnings_8k import (
    extract_reported_bvps_status, extract_reported_tbvps_status,
    _periods, _release_quarter_end, _strip_trailing_qualifiers,
    _match_bvps_label, _match_tbvps_label, _supplement_rows,
)

Q2_26 = (2026, 6)


def _tr(*cells):
    """<tr> from (text, colspan) pairs or bare text."""
    tds = []
    for c in cells:
        text, span = c if isinstance(c, tuple) else (c, 1)
        tds.append(f'<td colspan="{span}">{text}</td>' if span > 1
                   else f"<td>{text}</td>")
    return "<tr>" + "".join(tds) + "</tr>"


def _doc(*tables):
    return ("<html><body>" + "".join(f"<table>{t}</table>" for t in tables)
            + "</body></html>").encode("utf-8")


def _dollar_row(label, *vals):
    """Workiva '$ | value' pairs (RBCAA's book-value row)."""
    cells = [label]
    for v in vals:
        cells += ["$", v]
    return _tr(*cells)


def _plain_row(label, *vals):
    """Blank decoration cell + value (RBCAA's tangible row under the '$' row)."""
    cells = [label]
    for v in vals:
        cells += ["", v]
    return _tr(*cells)


def _rbcaa_selected_data():
    return (_tr(("Selected Data and Ratios", 1))
            + _tr("", ("As of and for the Three Months Ended", 10),
                  ("As of and for the Six Months Ended", 4))
            + _tr("", ("Jun. 30, 2026", 2), ("Mar. 31, 2026", 2),
                  ("Dec. 31, 2025", 2), ("Sep. 30, 2025", 2),
                  ("Jun. 30, 2025", 2), ("Jun. 30, 2026", 2),
                  ("Jun. 30, 2025", 2))
            + _tr("Per Share Data:")
            + _dollar_row("Book value per share (3)", "58.92", "57.78",
                          "56.41", "55.51", "54.29", "58.92", "54.29")
            + _plain_row("Tangible book value per share (3)", "56.44", "55.30",
                         "53.91", "53.01", "51.78", "56.44", "51.78"))


def _rbcaa_reconciliation():
    return (_tr(("As of", 11))
            + _tr("(dollars in thousands, except per share data)",
                  ("Jun. 30, 2026", 2), ("Mar. 31, 2026", 2),
                  ("Dec. 31, 2025", 2), ("Sep. 30, 2025", 2),
                  ("Jun. 30, 2025", 2))
            + _dollar_row("Total stockholders' equity - GAAP (a)", "1,156,797",
                          "1,133,387", "1,102,293", "1,084,520", "1,060,106")
            + _plain_row("Less: Goodwill", "40,516", "40,516", "40,516",
                         "40,516", "40,516")
            + _dollar_row("Book value per share - GAAP (a/e)", "58.92",
                          "57.78", "56.41", "55.51", "54.29")
            + _plain_row("Tangible book value per share - Non-GAAP (c/e)",
                         "56.44", "55.30", "53.91", "53.01", "51.78"))


def _fcnca_supplement():
    per = ("June 30,<br/>2026", "March 31,<br/>2026", "December 31, 2025",
           "September 30, 2025", "June 30,<br/>2025")
    return (_tr(("", 3), ("", 3), ("Three Months Ended", 10))
            + _tr(("dollars in millions, except share and per share data", 3),
                  ("", 3), *[(p, 2) for p in per])
            + _tr(("Book value and tangible book value per common share at "
                   "period end", 3))
            + _tr(("Common shares outstanding at period end", 3), ("dd", 3),
                  ("11,390,407", 2), ("11,689,314", 2), ("12,139,159", 2),
                  ("12,618,629", 2), ("13,075,979", 2))
            + _tr(("Book value per share", 3), ("x/dd", 3),
                  "$", "1,767.79", "$", "1,735.18", "$", "1,718.71",
                  "$", "1,672.54", "$", "1,637.72")
            + _tr(("Tangible book value per common share (non-GAAP)", 3),
                  ("aa/dd", 3), ("1,722.35", 2), ("1,689.96", 2),
                  ("1,674.11", 2), ("1,628.64", 2), ("1,594.38", 2)))


class TestHandVerifiedInputs(unittest.TestCase):
    def test_rbcaa_tbvps_deducts_msrs(self):
        self.assertAlmostEqual(
            (1_156_797 - 40_516 - 6_783 - 1_343) / 19_635, 56.44, places=2)
        self.assertAlmostEqual(1_156_797 / 19_635, 58.92, places=2)

    def test_fcnca_tbvps_ties_to_tce(self):
        self.assertAlmostEqual(1_722.35 * 11_390_407 / 1e6, 19_618, delta=0.5)


class TestPeriodTokens(unittest.TestCase):
    def test_header_shapes(self):
        for text in ("Jun. 30, 2026", "June 30,2026", "June 30, 2026",
                     "6/30/2026", "6/30/26", "2Q26", "2Q 2026", "2Q'26",
                     "Q2 2026", "Q2'26", "Second Quarter 2026",
                     "three months ended june 30, 2026"):
            self.assertEqual(_periods(text), [Q2_26], text)

    def test_bare_year_and_words_name_no_period(self):
        for text in ("2026", "As of and for the Three Months Ended",
                     "dollars in millions", "Book value per share x/dd",
                     "summary 30, 2026"):
            self.assertEqual(_periods(text), [], text)

    def test_order_preserved(self):
        self.assertEqual(_periods("June 30, 2026 compared to March 31, 2026"),
                         [(2026, 6), (2026, 3)])


class TestReleaseQuarterEnd(unittest.TestCase):
    def test_latest_quarter_end_strictly_before_filing(self):
        self.assertEqual(_release_quarter_end("2026-07-24"), (2026, 6))   # RBCAA
        self.assertEqual(_release_quarter_end("2026-07-23"), (2026, 6))   # FCNCA
        self.assertEqual(_release_quarter_end("2026-06-30"), (2026, 3))
        self.assertEqual(_release_quarter_end("2026-01-05"), (2025, 12))
        self.assertEqual(_release_quarter_end("2026-03-31"), (2025, 12))
        self.assertEqual(_release_quarter_end("2026-04-01"), (2026, 3))

    def test_unparseable(self):
        for d in ("", None, "2026-07", "July 24, 2026"):
            self.assertIsNone(_release_quarter_end(d), d)


class TestFormulaSuffixLabels(unittest.TestCase):
    def test_suffixes_peeled_exactly(self):
        cases = {
            "book value per share - gaap (a/e)": "book value per share",
            "tangible book value per share - non-gaap (c/e)":
                "tangible book value per share",
            "book value per share x/dd": "book value per share",
            "tangible book value per common share (non-gaap) aa/dd":
                "tangible book value per common share",
        }
        for label, core in cases.items():
            self.assertEqual(_strip_trailing_qualifiers(label), core, label)
        self.assertTrue(_match_bvps_label("book value per share - gaap (a/e)"))
        self.assertTrue(_match_tbvps_label(
            "tangible book value per common share (non-gaap) aa/dd"))

    def test_nothing_else_is_peeled(self):
        for label in ("tangible book value per share",
                      "book value per share 1/2",       # digits: not a formula ref
                      "book value per share abc/dd",    # >2 letters
                      "tangible common equity to tangible assets"):
            self.assertEqual(_strip_trailing_qualifiers(label), label, label)


class TestSupplementRows(unittest.TestCase):
    def test_rbcaa_selected_data_verified(self):
        html = _doc(_rbcaa_selected_data())
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=56.85, bvps=None, period_end=Q2_26),
            (56.44, "ok"))
        self.assertEqual(extract_reported_bvps_status(
            html, reconstructed=None, tbvps=56.44, period_end=Q2_26),
            (58.92, "ok"))

    def test_rbcaa_reconciliation_alone_matches_via_suffixes(self):
        html = _doc(_rbcaa_reconciliation())
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=56.85, period_end=Q2_26), (56.44, "ok"))
        self.assertEqual(extract_reported_bvps_status(
            html, reconstructed=58.9, period_end=Q2_26), (58.92, "ok"))

    def test_fcnca_supplement_verified_and_in_release_anchored(self):
        # No reconstruction: the exhibit's own book value per share anchors
        # the tangible figure (tangible < book), as in EX-99.1.
        html = _doc(_fcnca_supplement())
        self.assertEqual(extract_reported_tbvps_status(
            html, period_end=Q2_26), (1722.35, "ok"))
        self.assertEqual(extract_reported_bvps_status(
            html, period_end=Q2_26), (1767.79, "ok"))

    def test_other_quarter_end_reads_nothing(self):
        for html in (_doc(_rbcaa_selected_data()), _doc(_fcnca_supplement())):
            self.assertEqual(_supplement_rows(html, (2026, 3)), [])
            self.assertEqual(extract_reported_tbvps_status(
                html, reconstructed=56.85, period_end=(2026, 3)),
                (None, "not_disclosed"))

    def test_oldest_first_deck_is_not_read(self):
        # The hazard the guard exists for: nums[0] here is 2Q25's 51.78, and
        # |51.78 − 56.85| / 56.85 = 8.9% clears the ±15% band — the release
        # reader WOULD serve it as the current quarter.
        table = (_tr("", ("2Q25", 2), ("3Q25", 2), ("4Q25", 2), ("1Q26", 2),
                     ("2Q26", 2))
                 + _plain_row("Tangible book value per share", "51.78",
                              "53.01", "53.91", "55.30", "56.44"))
        html = _doc(table)
        self.assertEqual(extract_reported_tbvps_status(html, reconstructed=56.85),
                         (51.78, "ok"))                 # EX-99.1 reader: wrong
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=56.85, period_end=Q2_26),
            (None, "not_disclosed"))

    def test_prior_year_table_dropped_current_table_read(self):
        # RBCAA's segment tables sit under "June 30, 2025" headers; an earlier
        # prior-year row must neither decide nor block the verified one.
        prior = (_tr("", ("Three Months Ended June 30, 2025", 4))
                 + _plain_row("Tangible book value per share", "51.78"))
        html = _doc(prior, _rbcaa_selected_data())
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=56.85, period_end=Q2_26), (56.44, "ok"))

    def test_tfc_year_header_row_completes_the_date(self):
        # TFC 2Q26 EX-99.2 (ex992-qpsx2q26.htm): "June 30" over a
        # "(Dollars in millions…) | 2026" line whose bare years parse as
        # numbers — it must join the header, not cut it. Hand-check:
        # 40,797 / 1,221,626K = 33.396; 58,684 / 1,221,626K = 48.04.
        table = (_tr("", ("June 30", 3), ("March 31", 3))
                 + _tr("(Dollars in millions, except per share data, shares "
                       "in thousands)", ("2026", 3), ("2026", 3))
                 + _tr("Calculations of Tangible Common Equity and Related "
                       "Measures:(1)")
                 + _tr("Tangible common equity", "$", "40,797", "", "$",
                       "41,351", "")
                 + _tr("Outstanding shares at end of period", "1,221,626", "",
                       "", "1,236,005", "", "")
                 + _tr("Tangible common equity per common share", "$", "33.40",
                       "", "$", "33.19", ""))
        self.assertAlmostEqual(40_797 / 1_221.626, 33.40, places=2)
        self.assertEqual(extract_reported_tbvps_status(
            _doc(table), reconstructed=33.09, period_end=Q2_26), (33.40, "ok"))

    def test_cfg_quarter_label_header_with_year_cells(self):
        # CFG 2Q26 EX-99.3 (q226financialsupplement.htm): "2Q26 | 1Q26 | …
        # | 2026 | 2025" — quarter labels and six-month bare years on one
        # line. Hand-check: 24,072M / 422,677,660 = 56.951.
        table = (_tr("", ("QUARTERLY TRENDS", 4), ("FOR THE SIX MONTHS ENDED "
                                                   "JUNE 30,", 2))
                 + _tr("", ("2Q26", 1), ("1Q26", 1), ("4Q25", 1), ("3Q25", 1),
                       ("2026", 1), ("2025", 1))
                 + _tr("Book value per common share", "56.95", "56.48",
                       "56.39", "54.97", "56.95", "53.43"))
        self.assertAlmostEqual(24_072e6 / 422_677_660, 56.95, places=2)
        self.assertEqual(extract_reported_bvps_status(
            _doc(table), reconstructed=56.95, period_end=Q2_26), (56.95, "ok"))

    def test_stt_oldest_first_reconciliation_rejected(self):
        # STT 2Q26 EX-99.2 (exhibit992-2q26earningsrel.htm) runs 1Q25 → 2Q26:
        # the first column is 1Q25's 80.13 / 51.23, and STT has NO
        # reconstruction — the in-release book value alone anchors tangible
        # < book, so the release reader would serve a year-old pair.
        table = (_tr("", ("Quarters", 5))
                 + _tr("", "1Q25", "2Q25", "3Q25", "4Q25", "1Q26", "2Q26")
                 + _tr("Book value per common share", "80.13", "83.16",
                       "85.33", "86.01", "86.90", "88.02")
                 + _tr("Tangible book value per common share - non-GAAP",
                       "51.23", "", "53.56", "54.10", "55.02", "56.20"))
        self.assertEqual(extract_reported_tbvps_status(_doc(table)),
                         (51.23, "ok"))                 # release reader: wrong
        self.assertEqual(extract_reported_tbvps_status(
            _doc(table), period_end=Q2_26), (None, "not_disclosed"))
        self.assertEqual(extract_reported_bvps_status(
            _doc(table), period_end=Q2_26), (None, "not_disclosed"))

    def test_year_valued_data_row_is_not_a_header(self):
        from data.sec_earnings_8k import _is_year_header
        self.assertTrue(_is_year_header(["(dollars)", "2026", "", "2025"]))
        self.assertFalse(_is_year_header(["Total assets", "2,026", "2,025"]))
        self.assertFalse(_is_year_header(["Book value per share", "58.92"]))
        self.assertFalse(_is_year_header(["Per Share Data:", "", ""]))

    def test_mid_table_reheader_governs_rows_beneath(self):
        table = (_tr("", ("June 30, 2026", 2), ("March 31, 2026", 2))
                 + _plain_row("Net interest margin", "3.84", "3.80")
                 + _tr("", ("December 31, 2025", 2), ("December 31, 2024", 2))
                 + _plain_row("Tangible book value per share", "53.91", "49.10"))
        self.assertEqual(extract_reported_tbvps_status(
            _doc(table), reconstructed=56.85, period_end=Q2_26),
            (None, "not_disclosed"))

    def test_mixed_period_header_rejected(self):
        table = (_tr("", ("June 30, 2026 vs. March 31, 2026", 2))
                 + _plain_row("Tangible book value per share", "56.44"))
        self.assertEqual(_supplement_rows(_doc(table), Q2_26), [])

    def test_headerless_table_rejected(self):
        table = _plain_row("Tangible book value per share", "56.44", "55.30")
        self.assertEqual(_supplement_rows(_doc(table), Q2_26), [])

    def test_blank_latest_cell_still_na(self):
        # Verified column, blank cell: n/a — never the prior column (audit P3).
        table = (_tr("", ("Jun. 30, 2026", 2), ("Mar. 31, 2026", 2))
                 + _plain_row("Tangible book value per share", "", "55.30")
                 + _plain_row("Book value per share", "58.92", "57.78"))
        self.assertEqual(extract_reported_tbvps_status(
            _doc(table), reconstructed=56.85, period_end=Q2_26),
            (None, "not_disclosed"))

    def test_text_layer_and_prose_are_off_in_supplements(self):
        # FCNCA's EX-99.2 deck is page images + a text layer (no tables); a
        # CEO letter can state TBVPS in prose. Neither has columns to verify.
        deck = ("<html><body><div>2Q26 1Q26 4Q25</div><div>Book value per share x/dd "
                "1,767.79 1,735.18 1,718.71 Tangible book value per common "
                "share (non-GAAP) aa/dd 1,722.35 1,689.96 1,674.11 Common "
                "shares outstanding at period end dd 11,390,407 11,689,314 "
                "12,139,159</div></body></html>").encode("utf-8")
        prose = (b"<html><body><p>Highlights. Tangible book value per share "
                 b"of $56.44 at quarter end.</p></body></html>")
        # The EX-99.1 reader reads both (the formula suffixes now peel in the
        # text layer too) ...
        self.assertEqual(extract_reported_tbvps_status(deck, reconstructed=1700.0),
                         (1722.35, "ok"))
        self.assertEqual(extract_reported_tbvps_status(prose, reconstructed=56.85),
                         (56.44, "ok"))
        # ... the supplementary reader reads neither.
        for html, recon in ((deck, 1700.0), (prose, 56.85)):
            self.assertEqual(extract_reported_tbvps_status(
                html, reconstructed=recon, period_end=Q2_26),
                (None, "not_disclosed"))


_INDEX = (
    "<html><body><table><tr><th>Seq</th><th>Description</th><th>Document</th>"
    "<th>Type</th><th>Size</th></tr>"
    "<tr><td>1</td><td>8-K</td><td><a href='/x/rbcaa-8k.htm'>rbcaa-8k.htm</a>"
    " iXBRL</td><td>8-K</td><td>1</td></tr>"
    "<tr><td>4</td><td>EX-99.3</td><td><a href='/x/ex993.htm'>ex993.htm</a>"
    "</td><td>EX-99.3</td><td>1</td></tr>"
    "<tr><td>2</td><td>EX-99.1</td><td><a href='/x/ex991.htm'>ex991.htm</a>"
    "</td><td>EX-99.1</td><td>1</td></tr>"
    "<tr><td>3</td><td>EX-99.2</td><td><a href='/x/ex992.htm'>ex992.htm</a>"
    "</td><td>EX-99.2</td><td>1</td></tr>"
    "<tr><td>5</td><td></td><td><a href='/x/ex992001.jpg'>ex992001.jpg</a>"
    "</td><td>GRAPHIC</td><td>1</td></tr>"
    "</table></body></html>").encode("utf-8")

_TODAY = __import__("datetime").date(2026, 9, 30)
_F8K = {"accession_dash": "0001104659-26-086487",
        "accession": "000110465926086487", "date": "2026-07-24", "cik": 921557}


class TestExhibitWalk(unittest.TestCase):
    def test_index_types_ordered_by_exhibit_number(self):
        with patch.object(s8k, "_get", return_value=_INDEX):
            self.assertEqual(s8k._ex99_documents(921557, _F8K["accession_dash"]),
                             [(1, "ex991.htm"), (2, "ex992.htm"), (3, "ex993.htm")])
            self.assertEqual(s8k._ex991_document(921557, _F8K["accession_dash"]),
                             "ex991.htm")

    def _walk(self, results, date="2026-07-24"):
        """Run _exhibit_status over 3 exhibits whose extractor returns
        `results[n]`; returns (answer, [(n, period_end) extracted])."""
        seen = []
        docs = [(1, "ex991.htm"), (2, "ex992.htm"), (3, "ex993.htm")]

        def extract(html, pe):
            n = int(html.decode())
            seen.append((n, pe))
            return results[n]

        def get(url):
            return url.rsplit("ex99", 1)[1].split(".")[0][0].encode()

        with patch.object(s8k, "_ex99_documents", return_value=docs), \
                patch.object(s8k, "_get", side_effect=get):
            out = s8k._exhibit_status(dict(_F8K, date=date), extract,
                                      today=_TODAY)
        return out, seen

    def test_ex991_answer_is_never_second_guessed(self):
        for ans in ((25.0, "ok"), (None, "gate_rejected")):
            out, seen = self._walk({1: ans, 2: (99.0, "ok"), 3: (98.0, "ok")})
            self.assertEqual(out, ans)
            self.assertEqual(seen, [(1, None)])

    def test_falls_through_to_first_disclosing_supplement(self):
        nd = (None, "not_disclosed")
        out, seen = self._walk({1: nd, 2: nd, 3: (1722.35, "ok")})
        self.assertEqual(out, (1722.35, "ok"))
        self.assertEqual(seen, [(1, None), (2, Q2_26), (3, Q2_26)])

    def test_nothing_anywhere(self):
        nd = (None, "not_disclosed")
        self.assertEqual(self._walk({1: nd, 2: nd, 3: nd})[0], nd)

    def test_no_release_quarter_no_supplements(self):
        nd = (None, "not_disclosed")
        out, seen = self._walk({1: nd, 2: (56.44, "ok"), 3: nd}, date="")
        self.assertEqual(out, nd)
        self.assertEqual(seen, [(1, None)])

    def test_stale_release_no_supplements(self):
        # UBOH: latest earnings 8-K 2023-01-19 → 4Q22 figures, 1,369 days old
        # on 2026-09-30. EX-99.1 is still read exactly as before.
        nd = (None, "not_disclosed")
        out, seen = self._walk({1: nd, 2: (17.04, "ok"), 3: nd},
                               date="2023-01-19")
        self.assertEqual(out, nd)
        self.assertEqual(seen, [(1, None)])

    def test_staleness_boundary(self):
        # 2026-03-31 quarter-end: 183 days on 2026-09-30 (fresh); 2025-12-31:
        # 273 days (stale).
        self.assertEqual((_TODAY - __import__("datetime").date(2026, 3, 31)).days, 183)
        nd = (None, "not_disclosed")
        self.assertEqual(self._walk({1: nd, 2: (9.0, "ok"), 3: nd},
                                    date="2026-04-20")[0], (9.0, "ok"))
        self.assertEqual(self._walk({1: nd, 2: (9.0, "ok"), 3: nd},
                                    date="2026-01-29")[0], nd)


if __name__ == "__main__":
    unittest.main()
