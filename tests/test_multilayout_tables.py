"""(2026-10-07) One <table>, several sections with different column grids.

SYBT's Q2-2026 EX-99.1 is ONE table: a five-quarter section with values from
column 2, then a "June 30, 2026 | June 30, 2025" balance-sheet section with
values in columns 10 / 14. Value columns are table-wide, so the second
section's rows read [None, None, None, None, 31.39, …] — a blank "latest
quarter" — and the audit-P3 rule correctly refused to slide 31.39 left: its
TBVPS rendered n/a. UBSI's "EOP Share Data" (columns 7/11/15/19, headed one
column left, over the '$' column) alike.

_rebase_offset_blocks re-bases a block (consecutive data rows) to its first
populated column only when it has ≥2 rows, NO row in it has a value further
left, and the header governing it names a period over that column (or the
one left of it) but NONE over the columns it skips. Hand-verified values
(each release's own reconciliation):
  SYBT TBVPS $31.39 / BVPS $40.12 (= SEC reconstruction)
  UBSI TBVPS $25.29 = TCE $3,463,098K ÷ 136,942,149 EOP shares;
       BVPS $40.24 = equity $5,510,537K ÷ 136,942,149

Run: python -m unittest tests.test_multilayout_tables
"""
import unittest

from data.sec_earnings_8k import _book_value_rows, extract_reported_tbvps_status


def _doc(*rows, width=16):
    """rows: {col: text} dicts (or None for a blank row) in one table."""
    trs = []
    for r in rows:
        cells = [""] * width
        for c, t in (r or {}).items():
            cells[c] = t
        trs.append("<tr>" + "".join(f"<td>{t}</td>" for t in cells) + "</tr>")
    return ("<html><body><table>" + "".join(trs) + "</table></body></html>").encode()


# SYBT shape: five-quarter section (cols 2,4,6), then a two-date section in
# cols 10/12 under its own "June 30," / "2026 2025" header.
SYBT = _doc(
    {2: "June 30,", 4: "March 31,", 6: "June 30,"},
    {2: "2026", 4: "2026", 6: "2025"},
    {0: "Net income per share, diluted", 2: "1.31", 4: "1.24", 6: "1.15"},
    {0: "Cash dividends declared per share", 2: "0.32", 4: "0.32", 6: "0.31"},
    None,
    {10: "June 30,"},
    {0: "Balance Sheet Data", 10: "2026", 12: "2025"},
    None,
    {0: "Total shares outstanding", 10: "31,068", 12: "29,473"},
    {0: "Book value per share (3)", 10: "$", 11: "40.12", 12: "$", 13: "34.12"},
    {0: "Tangible common equity per share (3)", 10: "31.39", 12: "27.06"},
)


class TestOffsetSections(unittest.TestCase):
    def test_sybt_two_date_section(self):
        rows = dict(_book_value_rows(SYBT))
        self.assertEqual(rows["tangible common equity per share (3)"][0], 31.39)
        self.assertEqual(rows["book value per share (3)"][0], 40.12)
        # SEC reconstruction 2026-10-06: TBVPS 31.3945, BVPS 40.1182.
        self.assertEqual(extract_reported_tbvps_status(
            SYBT, reconstructed=31.3945, bvps=40.1182), (31.39, "ok"))

    def test_rbkb_block_under_a_header_that_also_heads_the_skipped_columns(self):
        """RBKB: ratios in cols 2/5/8/10; "Other Data:" book values in cols
        8/10/12 under the table's ONE top header, which also names periods
        over the skipped cols 2/5 ("Three Months Ended June 30, 2026 /
        2025"). Nothing distinguishes that from a genuinely blank latest
        column, so it is NOT re-based (audit P3) — RBKB keeps the
        reconstruction, identical here ($12.28)."""
        doc = _doc(
            {2: "Three Months Ended", 8: "Six Months Ended", 12: "Year Ended"},
            {2: "June 30,", 5: "June 30,", 8: "June 30,", 12: "December 31,"},
            {2: "2026", 5: "2025", 8: "2026", 10: "2025", 12: "2025"},
            {0: "Return on average assets", 2: "0.79", 5: "0.88", 8: "0.75", 10: "0.80"},
            {0: "Net interest margin", 2: "3.78", 5: "3.97", 8: "3.77", 10: "3.88"},
            {0: "Other Data:"},
            {0: "Book value per common share", 8: "$ 12.49", 10: "$ 11.61", 12: "$ 12.28"},
            {0: "Tangible book value per common share(7)", 8: "$ 12.28", 10: "$ 11.40", 12: "$ 12.07"})
        rows = dict(_book_value_rows(doc))
        self.assertIsNone(rows["tangible book value per common share(7)"][0])
        self.assertEqual(extract_reported_tbvps_status(
            doc, reconstructed=12.28, bvps=12.49), (None, "not_disclosed"))

    def test_ubsi_dollar_layout_headed_one_column_left(self):
        """UBSI: "EOP Share Data" headed "June 30 | March 31 …" in cols 6/10
        with values in 7/11 (after each '$'); the earnings section above
        starts at col 3."""
        doc = _doc(
            {2: "Three Months Ended"},
            {2: "June", 6: "March"},
            {2: "2026", 6: "2026"},
            {0: "Diluted", 2: "$", 3: "0.95", 6: "$", 7: "0.89"},
            {0: "Common Dividends", 2: "$", 3: "0.38", 6: "$", 7: "0.38"},
            None,
            {6: "June 30", 10: "March 31"},
            {6: "2026", 10: "2026"},
            {0: "EOP Share Data:"},
            {0: "Book Value Per Share", 6: "$", 7: "40.24", 10: "$", 11: "39.65"},
            {0: "Tangible Book Value Per Share (non-GAAP) (1)", 6: "$", 7: "25.29",
             10: "$", 11: "24.84"})
        rows = dict(_book_value_rows(doc))
        self.assertEqual(rows["tangible book value per share (non-gaap) (1)"][0], 25.29)
        self.assertEqual(rows["book value per share"][0], 40.24)


class TestAuditP3Kept(unittest.TestCase):
    def test_lone_row_with_blank_latest_cell_stays_none(self):
        """A single offset row is not a block: its blank latest cell is still
        None, never the prior quarter's value."""
        doc = _doc(
            {2: "June 30, 2026", 4: "March 31, 2026"},
            {0: "Book value per share", 2: "40.12", 4: "39.50"},
            None,
            {0: "Tangible book value per share", 4: "30.41"},
            None)
        self.assertEqual(dict(_book_value_rows(doc))["tangible book value per share"],
                         [None, 30.41])

    def test_block_with_a_value_in_the_latest_column_is_not_rebased(self):
        doc = _doc(
            {2: "June 30, 2026", 4: "March 31, 2026"},
            {0: "Book value per share", 2: "40.12", 4: "39.50"},
            {0: "Tangible book value per share", 4: "30.41"})
        self.assertEqual(dict(_book_value_rows(doc))["tangible book value per share"],
                         [None, 30.41])

    def test_offset_block_without_a_period_header_is_not_rebased(self):
        doc = _doc(
            {2: "June 30, 2026", 4: "March 31, 2026"},
            {0: "Book value per share", 2: "40.12", 4: "39.50"},
            None,
            {0: "Other Data:"},
            {0: "Tangible book value per share", 6: "31.39", 8: "30.41"},
            {0: "Market value per share", 6: "76.47", 8: "70.10"})
        self.assertEqual(dict(_book_value_rows(doc))["tangible book value per share"][0],
                         None)


if __name__ == "__main__":
    unittest.main()
