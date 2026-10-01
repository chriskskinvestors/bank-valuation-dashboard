"""Deterministic BV/share from earnings releases (release_metrics v19/v20).

BV/share used to come only from the AI fill (data/release_ai), so a wire-only
bank whose release wasn't AI-filled showed a reconstructed BV — for GLBZ
(2026-10-01) the year-old 2025-09-30 10-Q ($7.10) beside TBV $7.16 from the
Q2-2026 release, which states "Book value per share $ 7.27" in its
SELECTED FINANCIAL DATA table (verified against the live release; the
full page extracts 7.27 through both entry points).

The prose label must never match the tail of "tangible (common) book value
per share" — a TBV sentence is not a BV candidate. v20: when the release
carries preferred EQUITY (or no table rows prove it doesn't), only an explicit
"per common share" BV serves — a bare label can be total equity incl.
preferred ÷ common shares (NPB); the AI fill obeys the same rule.

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
_GLBZ_TABLE = """<html><body><table>
<tr><td>(dollars in thousands, except per share amounts)</td></tr>
<tr><td></td><td>June 30, 2026</td><td>March 31, 2026</td>
<td>December 31, 2025</td><td>September 30, 2025</td><td>June 30, 2025</td></tr>
<tr><td>Tangible book value per share (3)</td><td>$ 7.16</td><td>$ 7.07</td>
<td>$ 7.23</td><td>$ 6.99</td><td>$ 6.53</td></tr>
<tr><td>Book value per share</td><td>$ 7.27</td><td>$ 7.18</td>
<td>$ 7.34</td><td>$ 7.10</td><td>$ 6.53</td></tr>
</table></body></html>"""


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
        # Prose-only (no tables): preferred can't be ruled out, so only the
        # explicit per-common label serves — this one is.
        text = ("Book value per common share was $21.80. Tangible book value "
                "per common share was $14.32.")
        out = extract_release_metrics(text)
        self.assertEqual((out["bv_ps"], out["tbv_ps"]), (21.80, 14.32))

    def test_growth_then_level_form(self):
        self.assertEqual(_bv("Book value per share increased 2.1% to $49.87."), 49.87)

    def test_band(self):
        self.assertIsNone(_bv("Book value per share was $0.55."))


# NPB 2Q26 shape (EX-99.1 0001336706-26-000059): a bare "Book value per share
# (GAAP)" row is total equity INCLUDING preferred ÷ common shares — the release
# shows "Less: preferred stock" in its TCE reconciliation. Per-common BV is not
# stated, so bv_ps must be n/a (sec_earnings_8k refuses the same row).
def _npb_table(bv_label="Book value per share (GAAP)"):
    return f"""<html><body><table>
<tr><td>(in thousands, except per share data)</td></tr>
<tr><td></td><td>June 30, 2026</td><td>March 31, 2026</td><td>June 30, 2025</td></tr>
<tr><td>Total shareholders' equity</td><td>$ 611,754</td><td>$ 589,993</td><td>$ 529,071</td></tr>
<tr><td>Less: preferred stock</td><td>24,979</td><td>24,979</td><td>24,979</td></tr>
<tr><td>Tangible book value per share</td><td>$ 16.94</td><td>$ 16.35</td><td>$ 14.67</td></tr>
<tr><td>{bv_label}</td><td>$ 17.69</td><td>$ 17.10</td><td>$ 17.58</td></tr>
</table></body></html>"""


class TestPreferredEquityGuard(unittest.TestCase):

    def test_npb_bare_label_with_preferred_is_na(self):
        out = extract_release_metrics(_npb_table(), expected_qend="2026-06-30")
        self.assertIsNone(out["bv_ps"])
        self.assertEqual(out["tbv_ps"], 16.94)
        self.assertNotIn("bv_ps_common", out)

    def test_explicit_per_common_label_still_serves(self):
        out = extract_release_metrics(_npb_table("Book value per common share"),
                                      expected_qend="2026-06-30")
        self.assertEqual(out["bv_ps"], 17.69)

    def test_tcbi_bare_label_ties_to_common_equity(self):
        # TCBI 2Q26: "Book value per share" $77.01 × 43,470,167 end-of-period
        # shares = $3,347.6M = $3,647.7M stockholders' equity − $300M preferred.
        html = """<html><body><table>
<tr><td>(dollars in thousands, except per share data)</td></tr>
<tr><td></td><td>June 30, 2026</td><td>March 31, 2026</td></tr>
<tr><td>Preferred stock</td><td>300,000</td><td>300,000</td></tr>
<tr><td>Total stockholders' equity</td><td>$ 3,647,707</td><td>$ 3,600,000</td></tr>
<tr><td>End of period shares outstanding</td><td>43,470,167</td><td>43,800,000</td></tr>
<tr><td>Book value per share</td><td>$ 77.01</td><td>$ 75.34</td></tr>
</table></body></html>"""
        out = extract_release_metrics(html, expected_qend="2026-06-30")
        self.assertEqual(out["bv_ps"], 77.01)

    def test_tfin_ties_to_stated_common_equity(self):
        # TFIN 2Q26: $38.43 × 23,894,537 = $918.3M "total common stockholders'
        # equity" (total $963,263K incl. $45M preferred).
        html = """<html><body><table>
<tr><td>(dollars in thousands, except per share amounts)</td></tr>
<tr><td></td><td>June 30, 2026</td><td>March 31, 2026</td></tr>
<tr><td>Preferred stock</td><td>45,000</td><td>45,000</td></tr>
<tr><td>Total stockholders' equity</td><td>$ 963,263</td><td>$ 950,000</td></tr>
<tr><td>Total common stockholders' equity</td><td>$ 918,263</td><td>$ 905,000</td></tr>
<tr><td>Shares outstanding end of period</td><td>23,894,537</td><td>23,900,000</td></tr>
<tr><td>Book value per share</td><td>$ 38.43</td><td>$ 37.87</td></tr>
</table></body></html>"""
        out = extract_release_metrics(html, expected_qend="2026-06-30")
        self.assertEqual(out["bv_ps"], 38.43)

    def test_ties_to_total_equity_is_refused(self):
        # The NPB shape with a share count present: $17.69 × 34,581,842 =
        # $611.8M = total equity incl. the $24,979K preferred → n/a.
        html = _npb_table().replace(
            "<tr><td>Tangible book value per share</td>",
            "<tr><td>Common shares outstanding</td><td>34,581,842</td>"
            "<td>34,488,000</td><td>34,000,000</td></tr>"
            "<tr><td>Tangible book value per share</td>")
        out = extract_release_metrics(html, expected_qend="2026-06-30")
        self.assertIsNone(out["bv_ps"])

    def test_banc_verb_to_level_form(self):
        # BANC 2Q26 prose: the level is pinned to "to $", never the comparison.
        text = ("At June 30, 2026, book value per common share decreased to "
                "$18.38 compared to $19.80 at March 31, 2026.")
        self.assertEqual(extract_release_metrics(text)["bv_ps"], 18.38)

    def test_ai_fill_never_supplies_bv_when_preferred_shows(self):
        from unittest.mock import patch
        import data.release_metrics as rm
        val = {"metrics": {}, "accession": "x", "qend": "2026-06-30"}
        with patch("data.release_ai.has_api_key", return_value=True), \
                patch("data.release_ai.release_ai_metrics",
                      return_value={"cur": {"bv_ps": 17.69, "nim": 3.4}}), \
                patch("data.ir_provider.earnings_supplement", return_value=None):
            rm._ai_fill(val, 1336706, {"html": _npb_table()})
        self.assertNotIn("bv_ps", val["metrics"])
        self.assertEqual(val["metrics"]["nim"], 3.4)

    def test_ai_fill_still_fills_bv_without_preferred(self):
        from unittest.mock import patch
        import data.release_metrics as rm
        val = {"metrics": {}, "accession": "x", "qend": "2026-06-30"}
        with patch("data.release_ai.has_api_key", return_value=True), \
                patch("data.release_ai.release_ai_metrics",
                      return_value={"cur": {"bv_ps": 7.27}}), \
                patch("data.ir_provider.earnings_supplement", return_value=None):
            rm._ai_fill(val, 890066, {"html": _GLBZ_TABLE})
        self.assertEqual(val["metrics"]["bv_ps"], 7.27)


if __name__ == "__main__":
    unittest.main(verbosity=2)
