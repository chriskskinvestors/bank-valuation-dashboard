"""Deterministic BV/share from earnings releases (release_metrics v19).

BV/share used to come only from the AI fill (data/release_ai), so a wire-only
bank whose release wasn't AI-filled showed a reconstructed BV — for GLBZ
(2026-10-01) the year-old 2025-09-30 10-Q ($7.10) beside TBV $7.16 from the
Q2-2026 release, which states "Book value per share $ 7.27" in its
SELECTED FINANCIAL DATA table (verified against the live release; the
full page extracts 7.27 through both entry points).

The prose label must never match the tail of "tangible (common) book value
per share" — a TBV sentence is not a BV candidate.

Run: python -m unittest tests.test_release_bvps
"""
import unittest

from tests import _streamlit_stub

_streamlit_stub.install()

from data.release_metrics import (  # noqa: E402
    _BV_BAND, _BV_PATS, _dollar_metric, extract_release_metrics,
    extract_table_metrics)


def _bv(text: str):
    return _dollar_metric(text, _BV_PATS, _BV_BAND)


# GLBZ Q2-2026 structure: quarter-end header, TBV row directly above the BV row
# (values are the release's: Q2'26, Q1'26, Q4'25, Q3'25, Q2'25).
_GLBZ_TABLE = """<table>
<tr><td>(dollars in thousands, except per share amounts)</td></tr>
<tr><td></td><td>June 30, 2026</td><td>March 31, 2026</td>
<td>December 31, 2025</td><td>September 30, 2025</td><td>June 30, 2025</td></tr>
<tr><td>Tangible book value per share (3)</td><td>$ 7.16</td><td>$ 7.07</td>
<td>$ 7.23</td><td>$ 6.99</td><td>$ 6.53</td></tr>
<tr><td>Book value per share</td><td>$ 7.27</td><td>$ 7.18</td>
<td>$ 7.34</td><td>$ 7.10</td><td>$ 6.53</td></tr>
</table>"""


class TestTableBv(unittest.TestCase):

    def test_glbz_row_reads_the_release_quarter_column(self):
        t = extract_table_metrics(_GLBZ_TABLE, "2026-06-30")
        self.assertEqual(t["bv_ps"], 7.27)
        self.assertEqual(t["tbv_ps"], 7.16)       # TBV row never feeds BV

    def test_full_extraction_fills_bv_from_the_table(self):
        out = extract_release_metrics(_GLBZ_TABLE, expected_qend="2026-06-30")
        self.assertEqual((out["bv_ps"], out["tbv_ps"]), (7.27, 7.16))

    def test_other_quarter_column_by_header(self):
        self.assertEqual(extract_table_metrics(_GLBZ_TABLE, "2025-09-30")["bv_ps"], 7.10)


class TestProseBv(unittest.TestCase):

    def test_book_value_sentence(self):
        self.assertEqual(_bv("Book value per share was $30.30 at June 30, 2026."), 30.30)

    def test_tbv_sentence_is_not_a_bv_candidate(self):
        self.assertIsNone(_bv("Tangible book value per share was $23.98."))
        self.assertIsNone(_bv("Tangible common book value per share of $14.32."))

    def test_both_sentences_each_resolve_their_own(self):
        text = ("Book value per common share was $21.80. Tangible book value "
                "per common share was $14.32.")
        out = extract_release_metrics(text)
        self.assertEqual((out["bv_ps"], out["tbv_ps"]), (21.80, 14.32))

    def test_growth_then_level_form(self):
        self.assertEqual(_bv("Book value per share increased 2.1% to $49.87."), 49.87)

    def test_band(self):
        self.assertIsNone(_bv("Book value per share was $0.55."))


if __name__ == "__main__":
    unittest.main(verbosity=2)
