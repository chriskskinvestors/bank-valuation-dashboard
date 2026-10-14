"""Release metrics: breakdown sub-rows + the sign of a parenthesized NCO ratio.

Fixtures are verbatim EDGAR EX-99.1 excerpts (cell text + colspan/rowspan
only) from 2Q26 releases, under tests/fixtures/release_nco_breakdown/.

NBBK prints its NCO rate NEGATIVE ("(0.07)% | (0.91)% | 0.00%") in two
tables, plus "… – PCD loans (1)" / "… – Non-PCD loans" breakdown rows under
the firmwide row. Pre-fix: the negative firmwide rows were refused, so the
PCD-only 0.00 was the quarter's table NCO rate; the basis guard then blanked
the correct prose 0.07 and the year-ago 0.00.

1. A breakdown sub-row ("<row above> – <qualifier>") never serves the
   firmwide metric; a basis qualifier (FTE, diluted, GAAP …) still does.
2. A parenthesized NCO ratio without a "recoveries" label is read only when
   the same table's roll-forward PROVES its sign (|net| = | |gross| −
   |recoveries| |); the served value is |ratio| × that sign. Unproven → n/a.
"""
import os
import re
import unittest

from data import release_metrics as rm

Q, PQ, YQ = "2026-06-30", "2026-03-31", "2025-06-30"


def _fx(name):
    p = os.path.join(os.path.dirname(__file__), "fixtures",
                     "release_nco_breakdown", name + ".html")
    with open(p, encoding="utf-8") as f:
        return f.read()


def _nco(html, q):
    return rm.extract_table_metrics(html, q)["nco_ratio"]


def _val(html):
    """The deterministic part of release_metrics() (see test_release_pit_basis)."""
    val = {"qend": Q,
           "metrics": rm.extract_release_metrics(html, expected_qend=Q),
           "prior_metrics": rm.extract_table_metrics(html, PQ),
           "yoy_metrics": rm.extract_table_metrics(html, YQ)}
    rm._comparative_basis_guard(val, rm.extract_table_metrics(html, Q))
    return val


class TestNbbk(unittest.TestCase):
    def test_served_columns(self):
        # Prose: "Net charge-offs (annualized) as a percent of average loans
        # declined to 0.07% from 0.91% in the prior quarter". Pre-fix the
        # guard served (None, None, None) with basis_split ["nco_ratio"].
        val = _val(_fx("NBBK"))
        self.assertEqual(tuple(val[b]["nco_ratio"] for b in
                               ("metrics", "prior_metrics", "yoy_metrics")),
                         (0.07, 0.91, 0.0))
        self.assertNotIn("basis_split", val)

    def test_breakdown_rows_are_not_candidates(self):
        # Prior quarter: PCD "(0.82)%" and Non-PCD "(0.08)%" beside the
        # firmwide "(0.91)%" — read as candidates they'd disagree (→ None);
        # pre-fix the current read was the PCD row's 0.00.
        html = _fx("NBBK")
        self.assertEqual((_nco(html, Q), _nco(html, PQ), _nco(html, YQ)),
                         (0.07, 0.91, 0.0))

    def test_highlights_table_alone_proves_no_sign(self):
        # "Annualized net charge-offs / Average total loans | (0.07)%" with
        # no roll-forward in its table: the sign is unproven → n/a.
        tabs = re.findall(r"(?s)<table>.*?</table>", _fx("NBBK"))
        self.assertEqual(len(tabs), 3)
        self.assertIsNone(_nco(tabs[1], Q))

    def test_duplicate_gross_row_proves_nothing(self):
        html = _fx("NBBK")
        row = re.search(r"<tr><td[^>]*>Total charge-offs</td>.*?</tr>", html).group(0)
        self.assertIsNone(_nco(html.replace(row, row + row), Q))


class TestRollForwardSign(unittest.TestCase):
    """HBT 2Q26: "Charge-offs | (314)", "Recoveries | 414", "Net charge-offs
    (recoveries) | $ (100)", then "Net charge-offs (recoveries) to average
    loans * | (0.01) %" — a genuine net recovery."""

    LABEL = "Net charge-offs (recoveries) to average loans"

    def test_recovery_label_unchanged(self):
        self.assertEqual(_nco(_fx("HBT"), Q), -0.01)

    def test_rollforward_proves_a_recovery_without_the_label(self):
        html = _fx("HBT").replace(self.LABEL, "Net charge-offs to average loans")
        self.assertNotIn("recoveries) to", html)
        self.assertEqual((_nco(html, Q), _nco(html, PQ), _nco(html, YQ)),
                         (-0.01, 0.08, 0.12))

    def test_rollforward_that_does_not_tie_out_proves_nothing(self):
        html = _fx("HBT").replace(self.LABEL, "Net charge-offs to average loans")
        cell = '<td colspan="2">414</td>'
        self.assertIn(cell, html)
        # one printed unit is rounding (|314 − 415| = 101 vs 100) …
        self.assertEqual(_nco(html.replace(cell, '<td colspan="2">415</td>'), Q), -0.01)
        # … two is not; equal gross and recoveries prove no direction
        for v in ("416", "314"):
            self.assertIsNone(_nco(html.replace(cell, f'<td colspan="2">{v}</td>'), Q), v)


class TestBreakdownLabels(unittest.TestCase):
    """Verbatim row labels (and their parent rows) from the 313-release
    corpus (2Q26 EX-99.1s)."""

    def test_breakdowns(self):
        for parent, row in [
            ("Annualized net charge-offs to average loans outstanding during the period",
             "Annualized net charge-offs to average loans outstanding during the period – PCD loans (1)"),
            ("Annualized net charge-offs to average loans outstanding during the period",
             "Annualized net charge-offs to average loans outstanding during the period – Non-PCD loans"),
            ("Net interest margin 3", "Net interest margin 3 – Banking Business Segment"),   # AX
            ("Efficiency ratio 4", "Efficiency ratio 4 – Banking Business Segment"),
        ]:
            self.assertTrue(rm._breakdown_row(row, {rm._label_key(parent)}), row)

    def test_basis_qualifiers_stay_readable(self):
        for parent, row in [
            ("Net Interest Margin", "Net Interest Margin - FTE"),                       # AROW
            ("Earnings per share", "Earnings per share - diluted"),                     # CTBI
            ("Net interest margin (1)", "Net interest margin - tax-equivalent (2)"),    # FBNC
            ("Book value per share (3)", "Book value per share - GAAP (a/e)"),              # SYBT
            ("Diluted earnings per common share",
             "Diluted earnings per common share - as adjusted (non-GAAP)"),            # PNC
            ("Net interest margin",
             "Net interest margin – fully taxable-equivalent (non-GAAP) (2)"),          # WTFC
            ("Efficiency ratio (2)", "Efficiency ratio - operating (1)(2)"),            # MCBS
        ]:
            self.assertFalse(rm._breakdown_row(row, {rm._label_key(parent)}), row)

    def test_no_parent_row_no_breakdown(self):
        for row in ["Net Charge-Offs / Average Loans - Quarter (1)",          # PNFP
                    "Net charge-offs to average total loans and leases held for "
                    "investment, net before ACL - loans and leases"]:          # BY
            self.assertFalse(rm._breakdown_row(row, set()), row)


if __name__ == "__main__":
    unittest.main()
