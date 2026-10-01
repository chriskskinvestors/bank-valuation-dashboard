"""(2026-10-01) A header YEAR is not a value column when it heads one (BHB).

BHB's Q2-2026 EX-99.1 (8-K 0001104659-26-085415) centres each period's year
in a colspan-2 header cell — "(in thousands) | … | 2026 (colspan 2) | …" —
whose FIRST column is the '$' column of the data rows below, the value sitting
in its second. _table_rows counted every column with a number in ANY row as a
value column, so the '$' column became the first "value" column and every row
of the table read [None, 23.43, None, 22.71, …]: TBVPS and everything else in
it rendered n/a. Same shape in 124 of the 320 sweep releases.

Hand-verified (BHB balance sheet, same release): total shareholders' equity
549,552K − goodwill 141,819K − other intangibles 15,242K = 392,491K ÷ 16,754K
common shares = 23.427 → the printed $23.43 (no preferred).

Pins:
  1. A year whose colspan covers a data column heads that column — rows read
     [23.43, 22.71, …] and TBVPS extracts.
  2. Audit P3 kept: a year over a column NO data row fills is still a column,
     so an all-blank latest quarter reads None → n/a, never the prior period.
  3. A year in its own cell beside the data column (no span over it) is also
     still kept — the conservative pre-fix behavior.
  4. Book-value path ONLY: the headline figures keep the old columns. There
     the fix exposed rows the release-wide scale can't vet (BHB NII read as
     $37.9B; UCB/CSBB net income ×1000; FCAP deposits −20%), so those
     tables stay n/a for extract_earnings_figures.

Run: python -m unittest tests.test_table_header_years
"""
import unittest

from data.sec_earnings_8k import (
    _book_value_rows, _table_rows, extract_earnings_figures,
    extract_reported_tbvps_status,
)

ZW = "&#8203;"     # BHB's zero-width-space spacer cells


def _td(text, span=None):
    return f'<td colspan="{span}">{text}</td>' if span else f"<td>{text}</td>"


def _tr(*cells):
    return "<tr>" + "".join(cells) + "</tr>"


def _doc(*rows):
    return ("<html><body><table>" + "".join(rows)
            + "</table></body></html>").encode("utf-8")


# BHB's header: label, three spacers, then per period [year (colspan 2), ZW].
_HEADER = _tr(_td("(in thousands)"), _td(ZW), _td(ZW), _td(ZW),
              *[c for y in ("2026", "2026", "2025", "2025", "2025")
                for c in (_td(y, 2), _td(ZW))])


def _data(label, ref, vals, dollar=False):
    """BHB data row: label, '', formula ref, ZW, then per period
    ['$' or '', value, ZW]."""
    return _tr(_td(label), _td(""), _td(ref), _td(ZW),
               *[c for v in vals
                 for c in (_td("$" if dollar else ""), _td(v), _td(ZW))])


BHB = _doc(
    _HEADER,
    _data("Common shares outstanding, period-end", "(K)",
          ("16,754", "16,742", "16,702", "16,689", "15,322")),
    _data("Core earnings per share, diluted (2)", "(A/L)",
          ("0.92", "0.88", "0.93", "0.95", "0.70"), dollar=True),
    _data("Tangible book value per share, period-end (2)", "(I/K)",
          ("23.43", "22.71", "22.41", "21.70", "22.58")),
)


class TestBhbHeaderYears(unittest.TestCase):
    def test_rows_read_from_the_value_columns(self):
        rows = dict(_book_value_rows(BHB))
        self.assertEqual(rows["tangible book value per share, period-end (2)"],
                         [23.43, 22.71, 22.41, 21.70, 22.58])
        self.assertEqual(rows["common shares outstanding, period-end"],
                         [16754.0, 16742.0, 16702.0, 16689.0, 15322.0])
        # The '$' row is unchanged (its decoration cell was always skipped).
        self.assertEqual(rows["core earnings per share, diluted (2)"],
                         [0.92, 0.88, 0.93, 0.95, 0.70])

    def test_header_year_row_is_not_a_data_row(self):
        self.assertNotIn("(in thousands)", dict(_book_value_rows(BHB)))

    def test_tbvps_extracts(self):
        # SEC reconstruction 2026-09-30: TBVPS 23.43, BVPS 32.80.
        self.assertEqual(extract_reported_tbvps_status(
            BHB, reconstructed=23.43, bvps=32.80), (23.43, "ok"))

    def test_blank_latest_column_under_its_own_year_stays_none(self):
        """Audit P3: the latest-quarter column exists only in the header (no
        data row fills it). The year keeps the column, so the row reads
        [None, …] and extracts n/a — never 22.71 as the current quarter."""
        doc = _doc(
            _tr(_td("(in thousands)"), _td("2026"), _td("2026"), _td("2025")),
            _tr(_td("Tangible book value per share"), _td(""), _td("22.71"),
                _td("22.41")),
            _tr(_td("Book value per share"), _td(""), _td("31.10"), _td("30.90")))
        self.assertEqual(dict(_book_value_rows(doc))["tangible book value per share"],
                         [None, 22.71, 22.41])
        self.assertEqual(extract_reported_tbvps_status(
            doc, reconstructed=23.43, bvps=32.80), (None, "not_disclosed"))

    def test_year_beside_the_data_column_without_a_span_is_kept(self):
        """A year in its own one-column cell next to (not over) the value
        column: nothing ties it to that column, so it stays a column exactly
        as before the fix (n/a, the safe direction)."""
        doc = _doc(
            _tr(_td("(in thousands)"), _td("2026"), _td(""), _td("2025"), _td("")),
            _tr(_td("Tangible book value per share"), _td(""), _td("23.43"),
                _td(""), _td("22.58")))
        self.assertEqual(dict(_book_value_rows(doc))["tangible book value per share"],
                         [None, 23.43, None, 22.58])


class TestHeadlineFiguresUnchanged(unittest.TestCase):
    def test_headline_path_keeps_the_old_columns(self):
        """BHB's real mix: a $-thousands statement row in the header-year
        shape. The headline reader must still see [None, …] there — the fix
        is scoped to per-share book values."""
        doc = _doc(
            _HEADER,
            _data("Net interest income", "(B)",
                  ("37,919", "36,812", "36,544", "35,770", "30,101"), dollar=True),
            _data("Total assets", "(F)",
                  ("4,742,502", "4,676,228", "4,683,891", "4,717,252", "4,112,005")))
        self.assertEqual(dict(_table_rows(doc))["total assets"][0], None)
        out = extract_earnings_figures(doc, {"total_assets": 4_742_502_000.0})
        self.assertIsNone(out["total_assets"])
        self.assertIsNone(out["net_interest_income"])
        self.assertEqual(dict(_book_value_rows(doc))["total assets"][0], 4742502.0)


if __name__ == "__main__":
    unittest.main()
