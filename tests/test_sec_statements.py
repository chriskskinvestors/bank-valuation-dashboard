"""SEC R-file As-Reported statement parser (data/sec_statements).

Pins the rendered-statement parse deterministically on a synthetic R-file shaped
like a real one: the title-row units, the date columns, section-header rows (no
values) vs data rows, accounting-sign parentheses, and the FilingSummary
statement-type matching (incl. the 'Statements of Earnings' variant, and
rejecting comprehensive/parenthetical companions).
"""
import unittest
from unittest import mock

from data.sec_statements import parse_rfile, _units_scale, _statement_rfiles


_INCOME = b"""<table class="report">
<tr><th class="tl">Consolidated Statements of Income - USD ($) $ in Thousands</th><th class="th">12 Months Ended</th></tr>
<tr><th class="th">Dec. 31, 2025</th><th class="th">Dec. 31, 2024</th></tr>
<tr><td class="pl">Interest income</td><td class="text"> </td><td class="text"> </td></tr>
<tr><td class="pl">Interest and fees on loans</td><td class="nump">$ 1,000</td><td class="nump">900</td></tr>
<tr><td class="pl">Provision for credit losses</td><td class="num">(50)</td><td class="nump">10</td></tr>
<tr><td class="pl">Net income</td><td class="nump">2,500</td><td class="nump">2,100</td></tr>
</table>"""

_FILING_SUMMARY = b"""<?xml version="1.0"?>
<FilingSummary><MyReports>
<Report><ShortName>Consolidated Statements of Earnings</ShortName><HtmlFileName>R4.htm</HtmlFileName></Report>
<Report><ShortName>Consolidated Statements of Comprehensive Income</ShortName><HtmlFileName>R6.htm</HtmlFileName></Report>
<Report><ShortName>Consolidated Balance Sheets</ShortName><HtmlFileName>R2.htm</HtmlFileName></Report>
<Report><ShortName>Consolidated Balance Sheets (Parenthetical)</ShortName><HtmlFileName>R3.htm</HtmlFileName></Report>
<Report><ShortName>Cover Page</ShortName><HtmlFileName>R1.htm</HtmlFileName></Report>
</MyReports></FilingSummary>"""


class TestParseRfile(unittest.TestCase):
    def setUp(self):
        self.p = parse_rfile(_INCOME)

    def test_units_and_periods(self):
        self.assertEqual(self.p["units_scale"], 1e3)
        self.assertEqual(self.p["periods"], ["Dec. 31, 2025", "Dec. 31, 2024"])
        self.assertEqual(self.p["basis"], "12 Months Ended")

    def test_section_header_row(self):
        h = self.p["rows"][0]
        self.assertEqual(h["label"], "Interest income")
        self.assertTrue(h["header"])
        self.assertEqual(h["values"], [])

    def test_values_scaled_and_dollar_stripped(self):
        r = self.p["rows"][1]
        self.assertFalse(r["header"])
        self.assertEqual(r["values"], [1_000_000.0, 900_000.0])

    def test_negative_parentheses(self):
        r = self.p["rows"][2]
        self.assertEqual(r["values"], [-50_000.0, 10_000.0])

    def test_units_scale_helper(self):
        self.assertEqual(_units_scale("x $ in Millions"), 1e6)
        self.assertEqual(_units_scale("x $ in Billions"), 1e9)
        self.assertEqual(_units_scale("USD ($)"), 1.0)

    def test_xbrl_definition_footnotes_truncated(self):
        # SEC R-files append an element-definition footnote block after the
        # statement (no period values) — it must be dropped, not rendered.
        rf = (b'<table class="report">'
              b'<tr><th class="tl">Statements of Income - USD ($) $ in Thousands</th>'
              b'<th class="th">12 Months Ended</th></tr>'
              b'<tr><th class="th">Dec. 31, 2025</th></tr>'
              b'<tr><td class="pl">Net income</td><td class="nump">2,500</td></tr>'
              b'<tr><td class="pl">X</td><td class="text">x</td></tr>'
              b'<tr><td class="pl">- Definition foo</td><td class="text">y</td></tr>'
              b'<tr><td class="pl">Name:</td><td class="text">us-gaap:NetIncomeLoss</td></tr>'
              b'</table>')
        self.assertEqual([r["label"] for r in parse_rfile(rf)["rows"]], ["Net income"])


class TestStatementMatching(unittest.TestCase):
    def test_matches_earnings_rejects_comprehensive_and_parenthetical(self):
        import data.sec_statements as s
        with mock.patch.object(s, "_get", return_value=_FILING_SUMMARY):
            out = _statement_rfiles("base/")
        self.assertEqual(out.get("income"), "R4.htm")    # 'Earnings' variant matched
        self.assertEqual(out.get("balance"), "R2.htm")   # parenthetical companion rejected
        self.assertNotIn("cashflow", out)


class TestStitchIncome(unittest.TestCase):
    """Multi-year stitch (data.sec_statements._stitch_income): union of labels
    with each filing's order preserved, each year sourced from the NEWEST filing
    that reported it, and blank where a line wasn't reported that year."""

    def _filing(self, periods, rows):
        return {"periods": periods, "units_scale": 1e3,
                "rows": [{"label": l, "header": h, "values": v} for l, h, v in rows]}

    def test_union_order_and_blanks(self):
        from data.sec_statements import _stitch_income
        newer = self._filing(["Dec. 31, 2025", "Dec. 31, 2024"], [
            ("Income", True, []),
            ("Interest", False, [100.0, 90.0]),
            ("Equipment finance", False, [30.0, 20.0]),
            ("Net income", False, [130.0, 110.0]),
        ])
        older = self._filing(["Dec. 31, 2024", "Dec. 31, 2023"], [
            ("Income", True, []),
            ("Interest", False, [90.0, 80.0]),
            ("SBA gain", False, [5.0, 4.0]),
            ("Net income", False, [110.0, 100.0]),
        ])
        out = _stitch_income([newer, older], n_years=3)
        self.assertEqual(out["periods"],
                         ["Dec. 31, 2025", "Dec. 31, 2024", "Dec. 31, 2023"])
        order = [(r["label"], r["header"]) for r in out["rows"]]
        self.assertEqual(order, [("Income", True), ("Interest", False),
                                 ("SBA gain", False), ("Equipment finance", False),
                                 ("Net income", False)])
        byl = {r["label"]: r["values"] for r in out["rows"] if not r["header"]}
        self.assertEqual(byl["Interest"], [100.0, 90.0, 80.0])
        self.assertEqual(byl["SBA gain"], [None, None, 4.0])
        self.assertEqual(byl["Equipment finance"], [30.0, 20.0, None])
        self.assertEqual(byl["Net income"], [130.0, 110.0, 100.0])


if __name__ == "__main__":
    unittest.main()
