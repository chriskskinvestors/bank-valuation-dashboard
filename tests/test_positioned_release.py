"""(2026-09-30) Table-less releases built from absolutely-positioned text (FBP).

FBP's Q1-2026 EX-99.1 (8-K 0001057706-26-000010) has NO <table>: every word
run is its own <div style="position:absolute; left:…px; top:…px">, one
position:relative container per page. The page-1 highlights print
"Tangible book value per share" + "(1)" (4px superscript) + "$ 12.45 $ 12.29
$ 10.64" as eight separate divs at top 422–425px, with the left-hand body
prose ("buybacks and dividends. …") on the same line. _table_rows found
nothing and _text_layer_rows saw each div as its own block, so the release's
TBVPS read as not-disclosed. Same shape: USCB (reconciliation rows with '-'
zero cells), CCBG (TBV per DILUTED share only — correctly not matched).

Hand-verified against FBP's own non-GAAP reconciliation (page 15):
  tangible common equity 1,925,388 ÷ 154,694K common shares = 12.4466 → $12.45
  (1,925,388 + goodwill 38,611 + intangibles 3,240) ÷ 154,694 = 12.717 → $12.72
USCB (page 9): tangible stockholders' equity 233,238 ÷ 18,459.470K = 12.635
  → $12.64 (no intangibles: '-' rows; no preferred).

Pins (hermetic, the real FBP/USCB div coordinates and fonts):
  1. The coordinate rows carry the per-share lines; TBVPS/BVPS extract and gate
     exactly as a table release would.
  2. Column alignment (audit P3): a blank latest-quarter cell stays None → n/a,
     never the prior quarter's value.
  3. A value off the page's column grid → [None] (unaligned), still first-match.
  4. A small-font superscript digit is not a value; '-' zero cells are blanks.
  5. Pages are separate coordinate spaces (the same top on two pages never
     merges rows).
  6. Per-DILUTED-share tangible book (CCBG) is not matched.

Run: python -m unittest tests.test_positioned_release
"""
import unittest

from data.sec_earnings_8k import (
    _book_value_rows, _positioned_rows, _table_rows,
    extract_reported_bvps_status, extract_reported_tbvps_status,
)
from lxml import html as lhtml


def _div(left, top, text, font=10.08):
    return (f'<div style="position:absolute;font-family:\'Times New Roman\';'
            f'font-size:{font}px;left:{left}px;top:{top}px;">{text} </div>')


def _page(*divs):
    return ('<div style="background-color:RGB(255, 255, 255);position:relative;'
            'width:816px;height:1056px;">' + "".join(divs) + '</div>')


def _doc(*pages):
    return ("<html><body>" + "".join(pages) + "</body></html>").encode("utf-8")


# FBP page 1, right-hand highlights (real coordinates), with the left-hand
# body-prose fragments that share the TBVPS/BVPS lines.
def _fbp_highlights(tbvps_latest=True):
    tb = [_div(575.81, 422, "12.45", 10.72)] if tbvps_latest else []
    return _page(
        _div(387.45, 359, "Net interest margin"),
        _div(576.29, 358, "4.75%", 10.72), _div(656.45, 358, "4.68%", 10.72),
        _div(736.64, 358, "4.52%", 10.72),
        _div(387.45, 375, "Efficiency ratio"),
        _div(570.85, 374, "49.14%", 10.72), _div(651.01, 374, "49.33%", 10.72),
        _div(731.2, 374, "49.58%", 10.72),
        _div(387.45, 391, "Diluted earnings per share"),
        _div(530.21, 391, "$"), _div(581.09, 390, "0.57", 10.72),
        _div(610.37, 391, "$"), _div(661.28, 390, "0.55", 10.72),
        _div(690.56, 391, "$"), _div(741.44, 390, "0.47", 10.72),
        _div(52, 402, "consumers. Our thoughtful and consistent approach to "
                      "capital deployment"),
        _div(387.45, 407, "Book value per share"),
        _div(530.21, 407, "$"), _div(575.81, 406, "12.72", 10.72),
        _div(610.37, 407, "$"), _div(655.97, 406, "12.56", 10.72),
        _div(690.56, 407, "$"), _div(736.16, 406, "10.91", 10.72),
        _div(52, 422, "buybacks and dividends. Our disciplined approach to "
                      "capital allocation,"),
        _div(387.45, 423, "Tangible book value per share"),
        _div(508.73, 425, "(1)", 4),
        _div(530.21, 423, "$"), *tb,
        _div(610.37, 423, "$"), _div(655.97, 422, "12.29", 10.72),
        _div(690.56, 423, "$"), _div(736.16, 422, "10.64", 10.72),
        _div(387.45, 439, "Return on average equity"),
        _div(570.85, 438, "17.92%", 10.72), _div(651.01, 438, "17.84%", 10.72),
        _div(731.2, 438, "17.90%", 10.72),
    )


FBP = _doc(_fbp_highlights())

# USCB page 9 reconciliation (real coordinates; numbers carry no font-size).
_USCB = _page(
    _div(378.49, 127, "6/30/2026"), _div(460.57, 127, "3/31/2026"),
    _div(539.81, 127, "12/31/2025"), _div(624.61, 127, "9/30/2025"),
    _div(706.56, 127, "6/30/2025"),
    _div(48, 141, "Tangible book value per common share (at period-end):"),
    _div(302.79, 141, "(1)(4)", 6.72),
    *(d.replace("font-size:10.08px;", "") for d in (
        _div(66.08, 156, "Total stockholders' equity"),
        _div(366.79, 156, "$"), _div(401.37, 156, "233,238"),
        _div(448.89, 156, "$"), _div(483.45, 156, "223,246"),
        _div(530.85, 156, "$"), _div(565.41, 156, "217,183"),
        _div(612.93, 156, "$"), _div(647.49, 156, "209,095"),
        _div(694.88, 156, "$"), _div(729.44, 156, "231,583"),
        _div(66.08, 171, "Less: Intangible assets"),
        _div(432.57, 171, "-"), _div(514.65, 171, "-"),
        _div(596.61, 171, "-"), _div(678.72, 171, "-"), _div(760.64, 171, "-"),
        _div(75.07, 186, "Tangible stockholders' equity"),
        _div(366.79, 186, "$"), _div(401.37, 186, "233,238"),
        _div(448.89, 186, "$"), _div(483.45, 186, "223,246"),
        _div(530.85, 186, "$"), _div(565.41, 186, "217,183"),
        _div(612.93, 186, "$"), _div(647.49, 186, "209,095"),
        _div(694.88, 186, "$"), _div(729.44, 186, "231,583"),
        _div(66.08, 230, "Total common shares issued and outstanding"),
        _div(388.09, 230, "18,459,470"), _div(470.17, 230, "18,257,400"),
        _div(552.13, 230, "18,137,885"), _div(634.21, 230, "18,107,385"),
        _div(716.16, 230, "20,078,385"),
        _div(75.07, 248, "Tangible book value per common share"),
        _div(366.79, 248, "$"), _div(412.09, 248, "12.64"),
        _div(448.89, 248, "$"), _div(494.17, 248, "12.23"),
        _div(526.85, 248, "$"), _div(576.13, 248, "11.97"),
        _div(608.93, 248, "$"), _div(658.21, 248, "11.55"),
        _div(690.88, 248, "$"), _div(740.16, 248, "11.53"),
    )),
    _div(244.23, 248, "(2)", 6.72),
)
USCB = _doc(_USCB)


class TestFbpPositioned(unittest.TestCase):
    def test_table_rows_see_nothing(self):
        """The failure being fixed: no <table>, no rows."""
        self.assertEqual(_table_rows(FBP), [])

    def test_rows_rebuilt_from_coordinates(self):
        rows = dict(_book_value_rows(FBP))
        self.assertEqual(rows["tangible book value per share"], [12.45, 12.29, 10.64])
        self.assertEqual(rows["book value per share"], [12.72, 12.56, 10.91])
        self.assertEqual(rows["diluted earnings per share"], [0.57, 0.55, 0.47])
        self.assertEqual(rows["net interest margin"], [4.75, 4.68, 4.52])

    def test_prose_on_the_same_line_is_not_the_label(self):
        labels = [cl for cl, _ in _book_value_rows(FBP)]
        self.assertFalse(any(cl.startswith("buybacks") for cl in labels), labels)

    def test_tbvps_and_bvps_extract(self):
        # In-release anchors only (tangible < book): the same gates as a table.
        self.assertEqual(extract_reported_tbvps_status(FBP), (12.45, "ok"))
        self.assertEqual(extract_reported_bvps_status(FBP), (12.72, "ok"))
        # Production anchors (SEC reconstruction 2026-09-30: 12.68 / 12.95).
        self.assertEqual(extract_reported_tbvps_status(
            FBP, reconstructed=12.68, bvps=12.95), (12.45, "ok"))
        self.assertEqual(extract_reported_bvps_status(
            FBP, reconstructed=12.95, tbvps=12.45), (12.72, "ok"))

    def test_blank_latest_cell_is_na_never_the_prior_quarter(self):
        """Audit P3: drop the 12.45 div — the row must read [None, 12.29, …]
        and extract n/a, never 12.29 as the current quarter."""
        doc = _doc(_fbp_highlights(tbvps_latest=False))
        rows = dict(_book_value_rows(doc))
        self.assertEqual(rows["tangible book value per share"], [None, 12.29, 10.64])
        self.assertEqual(extract_reported_tbvps_status(
            doc, reconstructed=12.68, bvps=12.95), (None, "not_disclosed"))

    def test_value_off_the_column_grid_is_unaligned(self):
        page = _fbp_highlights().replace(
            "left:575.81px;top:422px", "left:540.0px;top:422px")
        rows = dict(_book_value_rows(_doc(page)))
        self.assertEqual(rows["tangible book value per share"], [None])
        self.assertEqual(extract_reported_tbvps_status(
            _doc(page), reconstructed=12.68, bvps=12.95), (None, "not_disclosed"))

    def test_superscript_digit_is_not_a_value(self):
        """A bare small-font '1' footnote between label and values (no parens)
        must not become nums[0]."""
        page = _fbp_highlights().replace(
            _div(508.73, 425, "(1)", 4), _div(508.73, 423, "1", 6.5))
        rows = dict(_book_value_rows(_doc(page)))
        self.assertEqual(rows["tangible book value per share"], [12.45, 12.29, 10.64])

    def test_pages_are_separate_coordinate_spaces(self):
        """Page 2 prints a different figure at the SAME top/left; the rows must
        not merge (each page is its own position:relative container)."""
        p2 = _fbp_highlights().replace("12.45", "99.99").replace(
            "Tangible book value per share", "Tangible book value per share X")
        rows = _positioned_rows(lhtml.fromstring(_doc(_fbp_highlights(), p2)))
        tb = [n for cl, n in rows if cl == "tangible book value per share"]
        self.assertEqual(tb, [[12.45, 12.29, 10.64]])

    def test_tables_still_win(self):
        """A document WITH a table is parsed exactly as before."""
        doc = FBP.replace(b"</body>", b"<table><tr><td>Book value per share</td>"
                                      b"<td>1.00</td></tr></table></body>")
        self.assertEqual(_book_value_rows(doc), _table_rows(doc))


class TestUscbPositioned(unittest.TestCase):
    def test_reconciliation_rows(self):
        rows = dict(_book_value_rows(USCB))
        self.assertEqual(rows["tangible book value per common share"],
                         [12.64, 12.23, 11.97, 11.55, 11.53])
        self.assertEqual(rows["total common shares issued and outstanding"],
                         [18459470.0, 18257400.0, 18137885.0, 18107385.0,
                          20078385.0])
        # '-' zero cells are blanks, never a value or a label.
        self.assertNotIn("less: intangible assets", rows)
        self.assertNotIn("-", rows)

    def test_tbvps_with_no_intangibles(self):
        """USCB has no intangibles: the SEC reconstruction's tangible == book
        (12.635 both) and the release prints $12.64. The tangible < book gate
        must not reject a no-intangibles bank's figure."""
        self.assertEqual(extract_reported_tbvps_status(
            USCB, reconstructed=12.635, bvps=12.635), (12.64, "ok"))

    def test_equal_tangible_and_book_still_rejected_with_intangibles(self):
        """Only the reconstruction saying tangible == book relaxes the gate: a
        bank WITH intangibles (recon TBVPS < BVPS) still rejects a tangible
        figure that is not below book."""
        self.assertEqual(extract_reported_tbvps_status(
            USCB, reconstructed=12.10, bvps=12.64), (None, "not_disclosed"))


class TestPerDilutedShareNotMatched(unittest.TestCase):
    def test_ccbg_diluted_rows(self):
        """CCBG reports book/tangible book per DILUTED share only (Q2-2026,
        $33.27 / $28.07) — a different denominator; both stay n/a."""
        rows = []
        for i, (label, vals) in enumerate((
                ("Book Value Per Diluted Share", ("33.27", "32.71", "32.23")),
                ("Tangible Book Value Per Diluted Share", ("28.07", "27.51", "27.03")),
                ("Actual Basic Shares Outstanding", ("17,111", "17,098", "17,084")),
                ("Actual Diluted Shares Outstanding", ("17,136", "17,115", "17,155")))):
            top = 867 + 14 * i
            rows.append(_div(48.1, top, label))
            rows += [_div(389.5 + 87.1 * j, top, v) for j, v in enumerate(vals)]
        doc = _doc(_page(*rows))
        self.assertEqual(dict(_book_value_rows(doc))[
            "tangible book value per diluted share"], [28.07, 27.51, 27.03])
        self.assertEqual(extract_reported_tbvps_status(
            doc, reconstructed=28.11, bvps=33.32), (None, "not_disclosed"))
        self.assertEqual(extract_reported_bvps_status(
            doc, reconstructed=33.32), (None, "not_disclosed"))


if __name__ == "__main__":
    unittest.main()
