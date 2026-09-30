"""(2026-09-30) Table-less earnings releases — the page text layer (AMAL).

AMAL's Q2-2026 EX-99.1 (8-K 0001823608-26-000177) has NO <table>: it is filed
as 18 page JPGs (Workiva print-to-image), each over a hidden 1pt white-font
text layer holding the page's text as one flat string. _table_rows found zero
rows, so the release's clearly printed "Book value per common share $ 27.93"
and "Tangible book value per share (non-GAAP) $ 27.47" (page 10, verified
against the rendered page image) read as not-disclosed. Same shape in the
2026-09-30 universe sweep: ACNB, BAC, CBC, EGBN, GABC, FGBI, NEWT.

Pins (hermetic, the real AMAL markup/text):
  1. The text layer yields AMAL's per-share rows; TBVPS/BVPS extract and gate
     exactly as a table release would.
  2. Column alignment (audit P3): a flat-text row whose value count differs
     from its page's modal count — a vanished blank cell, a stray footnote
     digit — is [None] → n/a, and it still wins first-match.
  3. A narrative block (no tabular evidence) can never supply a value.
  4. The text layer never feeds the headline figures (BAC: "Net income $9.1"
     billion would be mis-scaled to $9.1M by the release-wide scale).
  5. A document WITH tables is parsed exactly as before.

Run: python -m unittest tests.test_text_layer_release
"""
import unittest

from data.sec_earnings_8k import (
    _book_value_rows, _match_tbvps_label, _table_rows, extract_earnings_figures,
    extract_reported_bvps_status, extract_reported_tbvps_status,
)

# AMAL page 1 (narrative highlights) and page 10 (Select Financial Data),
# text layer verbatim from a202606earningsreleasefi.htm.
_AMAL_P1 = (
    "Capital and Returns &#8226; Tangible book value per share1 increased "
    "$0.88, or 3.3%, to $27.47. &#8226; Tier 1 leverage ratio was 9.20% and "
    "Common Equity Tier 1 ratio was 14.20%.")
_AMAL_P10 = (
    "Select Financial Data As of and for the As of and for the Three Months "
    "Ended Six Months Ended June 30, March 31, June 30, June 30, (Shares in "
    "thousands) 2026 2026 2025 2026 2025 Selected Financial Ratios and Other "
    "Data: Earnings per share    Basic $ 1.16 $ 0.85 $ 0.85 $ 2.01 $ 1.67     "
    "Diluted  1.15  0.84  0.84  1.99  1.65  Core net income (non-GAAP)    "
    "Basic $ 1.11 $ 0.81 $ 0.88 $ 1.92 $ 1.77     Diluted  1.10  0.80  0.88  "
    "1.90  1.75  Book value per common share $ 27.93 $ 27.05 $ 24.79 $ 27.93 "
    "$ 24.79  Tangible book value per share (non-GAAP) $ 27.47 $ 26.59 $ "
    "24.33 $ 27.47 $ 24.33  Common shares outstanding, par value $.01 per "
    "share(1)  29,900  29,857  30,412  29,900  30,412  Weighted average common "
    "shares outstanding, basic  29,878  29,815  30,558  29,847  30,619  "
    "Weighted average common shares outstanding, diluted  30,189  30,150  "
    "30,758  30,184  30,872")


def _page(n, text):
    return (f'<!-- a202606earningsreleasefi{n:03d}.jpg -->\n'
            f'<DIV style="padding-top:2em;">\n'
            f'<IMG src="a202606earningsreleasefi{n:03d}.jpg" title="slide{n}" '
            f'width="1055" height="1365">\n'
            f'<DIV><FONT size="1" style="font-size:1pt;color:white">{text}'
            f'</FONT></DIV>\n</DIV>\n<P><HR noshade><P>\n'
            f'<DIV style="page-break-before:always;">&nbsp;</DIV>\n')


def _doc(*pages):
    body = "".join(_page(i + 1, p) for i, p in enumerate(pages))
    return (f'<HTML><HEAD><TITLE>a202606earningsreleasefi</TITLE></HEAD>'
            f'<BODY bgcolor="white"><DIV align="center">'
            f'<DIV style="margin-left:1em;width:1055;">{body}</DIV></DIV>'
            f'</BODY></HTML>').encode("utf-8")


AMAL = _doc(_AMAL_P1, _AMAL_P10)


class TestAmalTextLayer(unittest.TestCase):
    def test_table_rows_see_nothing(self):
        """The failure being fixed: no <table>, no rows."""
        self.assertEqual(_table_rows(AMAL), [])

    def test_text_layer_rows_carry_the_per_share_lines(self):
        rows = dict(_book_value_rows(AMAL))
        self.assertEqual(rows["book value per common share"],
                         [27.93, 27.05, 24.79, 27.93, 24.79])
        self.assertEqual(rows["tangible book value per share (non-gaap)"],
                         [27.47, 26.59, 24.33, 27.47, 24.33])

    def test_reported_tbvps_and_bvps_extract(self):
        # No caller anchor: the in-release BVPS 27.93 anchors TBVPS 27.47.
        self.assertEqual(extract_reported_tbvps_status(AMAL), (27.47, "ok"))
        self.assertEqual(extract_reported_bvps_status(AMAL), (27.93, "ok"))
        # With the reconstruction (29,900,147 shares) — same answer.
        self.assertEqual(extract_reported_tbvps_status(
            AMAL, reconstructed=27.47, bvps=27.93), (27.47, "ok"))

    def test_narrative_mention_is_not_a_row(self):
        """Page 1's '…per share1 increased $0.88…' must not match."""
        labels = [cl for cl, _ in _book_value_rows(_doc(_AMAL_P1))]
        self.assertFalse(any(_match_tbvps_label(cl) for cl in labels))


class TestTextLayerAlignment(unittest.TestCase):
    def test_vanished_blank_latest_cell_is_na(self):
        """A blank June-30 TBVPS cell disappears from flat text; the prior
        quarter's 26.59 would slide into nums[0]. 4 values ≠ the page's 5 →
        [None] → n/a (audit P3), never the prior period."""
        page = _AMAL_P10.replace(
            "(non-GAAP) $ 27.47 $ 26.59", "(non-GAAP) $ 26.59")
        doc = _doc(page)
        rows = dict(_book_value_rows(doc))
        self.assertEqual(rows["tangible book value per share (non-gaap)"],
                         [None])
        self.assertEqual(extract_reported_tbvps_status(doc, reconstructed=27.0),
                         (None, "not_disclosed"))

    def test_stray_footnote_digit_is_na(self):
        page = _AMAL_P10.replace("(non-GAAP) $ 27.47", "(non-GAAP) 2 $ 27.47")
        self.assertEqual(extract_reported_tbvps_status(
            _doc(page), reconstructed=27.47), (None, "not_disclosed"))

    def test_parenthesized_footnote_stays_label_side(self):
        """FBP-style "Tangible book value per share (1) $ 12.45 …": the
        "(1)" is a footnote ref, not a negative one."""
        page = _AMAL_P10.replace("(non-GAAP) $ 27.47", "(1) $ 27.47")
        rows = dict(_book_value_rows(_doc(page)))
        self.assertEqual(rows["tangible book value per share (1)"][0], 27.47)
        self.assertEqual(extract_reported_tbvps_status(
            _doc(page), reconstructed=27.47), (27.47, "ok"))

    def test_block_without_tabular_evidence_supplies_nothing(self):
        """Fewer than 3 rows sharing one value count → no alignment proof."""
        doc = _doc("Book value per common share $ 27.93 $ 27.05 "
                   "Tangible book value per share $ 27.47 $ 26.59")
        self.assertEqual(extract_reported_tbvps_status(doc, reconstructed=27.47),
                         (None, "not_disclosed"))


class TestScopeOfTheFallback(unittest.TestCase):
    def test_headline_figures_never_read_the_text_layer(self):
        """BAC's page 9 prints "$ in billions"; a text-layer "Net income $9.1"
        scaled by a release-wide $M scale would render $9.1M. Headline
        figures stay table-only."""
        page = ("Summary Income Statement ($ in billions) Q2 Q1 Q2 Total assets "
                "3,499.0 3,496.2 3,440.8 Total deposits 2,025.1 2,037.7 2,011.6 "
                "Net income $ 9.1 $ 7.4 $ 7.1")
        figs = extract_earnings_figures(
            _doc(page), {"total_assets": 3_499_000_000_000.0})
        self.assertTrue(all(v is None for v in figs.values()), figs)

    def test_document_with_tables_parses_as_before(self):
        doc = (b"<html><body><table><tr><td>Tangible book value per share</td>"
               b"<td>$</td><td>12.40</td><td>$</td><td>12.10</td></tr></table>"
               b"<div>Tangible book value per share $ 99.00 $ 98.00 $ 97.00"
               b"</div></body></html>")
        self.assertEqual(_book_value_rows(doc), _table_rows(doc))
        self.assertEqual(extract_reported_tbvps_status(doc, reconstructed=12.4),
                         (12.40, "ok"))


if __name__ == "__main__":
    unittest.main()
