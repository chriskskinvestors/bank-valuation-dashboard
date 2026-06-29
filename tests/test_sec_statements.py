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

    def test_per_share_rows_not_unit_scaled(self):
        # EPS ($/share) and share counts are in their own units, not the
        # statement's "$ in Thousands" — they must not be scaled.
        rf = (b'<table class="report">'
              b'<tr><th class="tl">Statements of Income - USD ($) $ in Thousands</th>'
              b'<th class="th">12 Months Ended</th></tr>'
              b'<tr><th class="th">Dec. 31, 2025</th></tr>'
              b'<tr><td class="pl">Net income</td><td class="nump">2,500</td></tr>'
              b'<tr><td class="pl">Basic earnings per common share (in dollars per share)</td>'
              b'<td class="nump">$ 6.02</td></tr>'
              b'<tr><td class="pl">Basic (in shares)</td><td class="nump">68,448,812</td></tr>'
              b'</table>')
        vals = {r["label"]: r["values"][0]
                for r in parse_rfile(rf)["rows"] if not r["header"]}
        self.assertEqual(vals["Net income"], 2_500_000.0)          # x1000 (thousands)
        self.assertAlmostEqual(
            vals["Basic earnings per common share (in dollars per share)"], 6.02)
        self.assertEqual(vals["Basic (in shares)"], 68_448_812.0)  # not scaled

    def test_spacer_td_th_does_not_swallow_data_rows(self):
        # KEY (and peers) insert an empty spacer <td class="th"> into EVERY data
        # row. Classifying a row as a header by the CLASS string 'th' then routed
        # every data row into the header — periods/rows came back empty. Rows must
        # be told apart by TAG (all-<th> = header), and the spacer dropped so each
        # value stays aligned to its period.
        rf = (b'<table class="report">'
              b'<tr><th class="tl">Consolidated Statements of Income - USD ($) '
              b'shares in Thousands, $ in Millions</th><th class="th">12 Months Ended</th></tr>'
              b'<tr><th class="th">Dec. 31, 2025</th><th class="th">Dec. 31, 2024</th>'
              b'<th class="th">Dec. 31, 2023</th></tr>'
              b'<tr><td class="pl">Loans</td><td class="th"> </td>'
              b'<td class="nump">$ 5,749</td><td class="nump">6,026</td>'
              b'<td class="nump">6,219</td></tr>'
              b'</table>')
        p = parse_rfile(rf)
        self.assertEqual(p["periods"],
                         ["Dec. 31, 2025", "Dec. 31, 2024", "Dec. 31, 2023"])
        loans = [r for r in p["rows"] if r["label"] == "Loans"][0]
        self.assertFalse(loans["header"])
        # $ in Millions governs the dollar scale (NOT 'shares in Thousands'); the
        # spacer cell is dropped so values map 1:1 to the three periods.
        self.assertEqual(loans["values"], [5_749e6, 6_026e6, 6_219e6])

    def test_units_scale_dollar_phrase_beats_shares_clause(self):
        # 'shares in Thousands, $ in Millions' must scale dollars by 1e6 — the
        # '$ in …' phrase wins over a leading 'shares in Thousands' clause.
        self.assertEqual(
            _units_scale("Income - USD ($) shares in Thousands, $ in Millions"), 1e6)
        self.assertEqual(_units_scale("Balance - USD ($) $ in Millions"), 1e6)
        self.assertEqual(_units_scale("Income - USD ($) $ in Thousands"), 1e3)

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
        from data.sec_statements import _stitch_statement as _stitch_income
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

    def test_varying_numeric_labels_merge_to_one_row(self):
        # A line whose label embeds changing numbers (allowance amounts) must
        # stay ONE row across filings, not fragment — display the newest label.
        from data.sec_statements import _stitch_statement
        newer = self._filing(["Dec. 31, 2025", "Dec. 31, 2024"], [
            ("AFS securities, net of allowance of $75 and $69", False, [2207.0, 1671.0]),
        ])
        older = self._filing(["Dec. 31, 2024", "Dec. 31, 2023"], [
            ("AFS securities, net of allowance of $69 and $69", False, [1671.0, 1402.0]),
        ])
        out = _stitch_statement([newer, older], n_years=3)
        afs = [r for r in out["rows"] if "AFS securities" in r["label"]]
        self.assertEqual(len(afs), 1)                                  # not fragmented
        self.assertEqual(afs[0]["label"], "AFS securities, net of allowance of $75 and $69")
        self.assertEqual(afs[0]["values"], [2207.0, 1671.0, 1402.0])   # FY2025, FY2024, FY2023

    def test_blank_cell_in_owner_column_backfills_from_older(self):
        # KEY's latest balance sheet lists Dec-31-2023 as a third date but leaves
        # most of that column blank (Total assets = None) — the line EXISTS in the
        # owner, just the cell is empty. That hole must backfill from the older
        # filing that DOES report it (188.3), not stay None.
        from data.sec_statements import _stitch_statement
        newer = self._filing(["Dec. 31, 2025", "Dec. 31, 2024", "Dec. 31, 2023"], [
            ("Total assets", False, [184.4, 187.2, None]),   # 2023 cell blank
        ])
        older = self._filing(["Dec. 31, 2024", "Dec. 31, 2023"], [
            ("Total assets", False, [187.2, 188.3]),
        ])
        out = _stitch_statement([newer, older], n_years=3)
        ta = [r for r in out["rows"] if r["label"] == "Total assets"][0]
        self.assertEqual(ta["values"], [184.4, 187.2, 188.3])

    def test_line_absent_from_owner_stays_blank(self):
        # The flip side: a line the OWNER filing doesn't carry for a period stays
        # BLANK (the company's own absence), never backfilled from an older filing.
        from data.sec_statements import _stitch_statement
        newer = self._filing(["Dec. 31, 2025", "Dec. 31, 2024"], [
            ("Interest", False, [100.0, 90.0]),
        ])
        older = self._filing(["Dec. 31, 2024", "Dec. 31, 2023"], [
            ("Interest", False, [90.0, 80.0]),
            ("SBA gain", False, [5.0, 4.0]),     # owner of 2024 has no SBA-gain line
        ])
        out = _stitch_statement([newer, older], n_years=3)
        sba = [r for r in out["rows"] if r["label"] == "SBA gain"][0]
        self.assertEqual(sba["values"], [None, None, 4.0])   # 2024 NOT backfilled to 5.0


_DEP_SUMMARY = b"""<?xml version="1.0"?>
<FilingSummary><MyReports>
<Report><ShortName>Deposits - Composition of Deposits (Details)</ShortName><HtmlFileName>R95.htm</HtmlFileName></Report>
<Report><ShortName>Deposits - Maturities of Time Deposits Outstanding (Details)</ShortName><HtmlFileName>R96.htm</HtmlFileName></Report>
<Report><ShortName>Deposits - Narrative (Details)</ShortName><HtmlFileName>R97.htm</HtmlFileName></Report>
<Report><ShortName>Deposits (Tables)</ShortName><HtmlFileName>R45.htm</HtmlFileName></Report>
<Report><ShortName>Deposits</ShortName><HtmlFileName>R19.htm</HtmlFileName></Report>
</MyReports></FilingSummary>"""

# A filer (e.g. PNFP) whose only deposit "(Details)" is generically named but is
# actually a time-deposit MATURITY ladder — the ShortName alone can't tell.
_DEP_GENERIC_SUMMARY = b"""<?xml version="1.0"?>
<FilingSummary><MyReports>
<Report><ShortName>Deposits (Details)</ShortName><HtmlFileName>R69.htm</HtmlFileName></Report>
<Report><ShortName>Deposits (Tables)</ShortName><HtmlFileName>R40.htm</HtmlFileName></Report>
</MyReports></FilingSummary>"""


class TestNoteRfileFinder(unittest.TestCase):
    """The as-reported NOTE finder (data.sec_statements._note_rfile / _NOTE_SPECS)
    must pick the by-type composition table and reject sibling tables that share
    the topic word (maturities, narrative) — and the content guard must catch a
    generically-named note whose body is a maturity ladder."""

    def _find(self, summary):
        import data.sec_statements as s
        with mock.patch.object(s, "_get", return_value=summary):
            return s._note_rfile("base/", s._NOTE_SPECS["deposit_composition"])

    def test_prefers_composition_rejects_maturity_and_narrative(self):
        self.assertEqual(self._find(_DEP_SUMMARY), "R95.htm")

    def test_generic_details_is_still_picked_by_name(self):
        # ShortName can't reject it (no 'maturit'/'narrative' in the name) — the
        # name-level finder returns it; the content guard is what rejects it.
        self.assertEqual(self._find(_DEP_GENERIC_SUMMARY), "R69.htm")

    def test_none_when_no_deposit_note(self):
        empty = (b'<?xml version="1.0"?><FilingSummary><MyReports>'
                 b'<Report><ShortName>Securities (Details)</ShortName>'
                 b'<HtmlFileName>R55.htm</HtmlFileName></Report>'
                 b'</MyReports></FilingSummary>')
        self.assertIsNone(self._find(empty))

    def test_maturity_table_detected_by_content(self):
        from data.sec_statements import _is_maturity_table
        mat = {"rows": [
            {"label": "2025", "header": False, "values": [1.0]},
            {"label": "2026", "header": False, "values": [2.0]},
            {"label": "2027", "header": False, "values": [3.0]},
            {"label": "Thereafter", "header": False, "values": [4.0]},
            {"label": "Time deposits, Total", "header": False, "values": [10.0]},
        ]}
        self.assertTrue(_is_maturity_table(mat))   # 4 of 5 rows are years/thereafter

    def test_composition_table_not_flagged_as_maturity(self):
        from data.sec_statements import _is_maturity_table
        comp = {"rows": [
            {"label": "Noninterest-bearing deposits", "header": False, "values": [1.0]},
            {"label": "Interest checking", "header": False, "values": [2.0]},
            {"label": "Savings accounts", "header": False, "values": [3.0]},
            {"label": "Time deposits", "header": False, "values": [4.0]},
            {"label": "Total deposits", "header": False, "values": [10.0]},
        ]}
        self.assertFalse(_is_maturity_table(comp))


_LOAN_SUMMARY = b"""<?xml version="1.0"?>
<FilingSummary><MyReports>
<Report><ShortName>Loans and Allowance for Credit Losses - Composition of Loan Portfolio (Details)</ShortName><HtmlFileName>R68.htm</HtmlFileName></Report>
<Report><ShortName>Loans and Allowance for Credit Losses - Activity in Allowance for Credit Losses (Details)</ShortName><HtmlFileName>R70.htm</HtmlFileName></Report>
<Report><ShortName>Loans and Allowance for Credit Losses - Loans by Portfolio Class, Including Delinquency Status (Details)</ShortName><HtmlFileName>R72.htm</HtmlFileName></Report>
<Report><ShortName>Loans and Allowance for Credit Losses - Loans by Portfolio Class and Internal Credit Quality Rating (Details)</ShortName><HtmlFileName>R73.htm</HtmlFileName></Report>
<Report><ShortName>Loans and Allowance for Credit Losses (Tables)</ShortName><HtmlFileName>R39.htm</HtmlFileName></Report>
</MyReports></FilingSummary>"""

# A filer (PNFP) whose loan "(Details)" are all credit-quality/allowance grab-bags
# with no by-type composition table — must resolve to None (n/a), not a wrong table.
_LOAN_GRABBAG = b"""<?xml version="1.0"?>
<FilingSummary><MyReports>
<Report><ShortName>Loans and Allowance for Loan Losses (Details)</ShortName><HtmlFileName>R59.htm</HtmlFileName></Report>
<Report><ShortName>Loans and Allowance for Loan Losses, Allowance (Details)</ShortName><HtmlFileName>R62.htm</HtmlFileName></Report>
<Report><ShortName>Loans and Allowance for Loan Losses Loan Classification by Risk Rating Category (Details)</ShortName><HtmlFileName>R60.htm</HtmlFileName></Report>
</MyReports></FilingSummary>"""


class TestLoanComposition(unittest.TestCase):
    """Loan composition: the finder must match prefer/reject on the table-specific
    SUFFIX (so the parent 'Loans and Allowance for Credit Losses' name doesn't
    reject the composition), and the dimensional collapse must turn XBRL
    member-header + generic 'Loans' value rows into one labeled row per class."""

    def _find(self, summary):
        import data.sec_statements as s
        with mock.patch.object(s, "_get", return_value=summary):
            return s._note_rfile("base/", s._NOTE_SPECS["loan_composition"])

    def test_specific_suffix_extraction(self):
        from data.sec_statements import _specific
        self.assertEqual(
            _specific("Loans and Allowance for Credit Losses - Composition of Loan Portfolio (Details)"),
            "Composition of Loan Portfolio")
        self.assertEqual(_specific("Deposits (Details)"), "Deposits")

    def test_picks_composition_despite_allowance_in_parent_name(self):
        # Every sibling's full name contains 'Allowance'; matching on the suffix
        # is what lets the composition table survive while the others are rejected.
        self.assertEqual(self._find(_LOAN_SUMMARY), "R68.htm")

    def test_grabbag_filer_resolves_to_none(self):
        self.assertIsNone(self._find(_LOAN_GRABBAG))

    def test_fhlb_advances_not_matched_as_loans(self):
        # 'Federal Home Loan Bank Advances' contains 'loan' (Home Loan Bank) and
        # matched `want`, then collapsed an FHLB advance into a fake 'Total loans'
        # for PNFP — it must be rejected, not treated as a loan composition.
        summary = (b'<?xml version="1.0"?><FilingSummary><MyReports>'
                   b'<Report><ShortName>Federal Home Loan Bank Advances (Details)</ShortName>'
                   b'<HtmlFileName>R70.htm</HtmlFileName></Report>'
                   b'</MyReports></FilingSummary>')
        self.assertIsNone(self._find(summary))

    def test_collapse_dimensional_members_to_labeled_rows(self):
        from data.sec_statements import _collapse_dimensional
        # USB-style: 'Axis | Member' headers, amounts in generic 'Loans' rows,
        # XBRL [Abstract] noise rows interleaved.
        dim = {"title": "Composition of Loan Portfolio", "units_scale": 1e6,
               "periods": ["Dec. 31, 2025", "Dec. 31, 2024"], "rows": [
            {"label": "Accounts, Notes, Loans and Financing Receivable [Abstract]", "header": True, "values": []},
            {"label": "Loans", "header": False, "values": [391335.0, 379832.0]},
            {"label": "Commercial | Total commercial", "header": True, "values": []},
            {"label": "Accounts, Notes, Loans and Financing Receivable [Abstract]", "header": True, "values": []},
            {"label": "Loans", "header": False, "values": [153958.0, 139484.0]},
            {"label": "Commercial | Lease financing", "header": True, "values": []},
            {"label": "Loans", "header": False, "values": [4436.0, 4230.0]},
        ]}
        out = _collapse_dimensional(dim)
        byl = {r["label"]: r["values"] for r in out["rows"]}
        self.assertEqual([r["label"] for r in out["rows"]],
                         ["Total loans", "Total commercial", "Lease financing"])
        self.assertEqual(byl["Total loans"], [391335.0, 379832.0])      # no-dimension default
        self.assertEqual(byl["Total commercial"], [153958.0, 139484.0])  # axis prefix stripped
        self.assertEqual(byl["Lease financing"], [4436.0, 4230.0])

    def test_collapse_handles_members_without_axis_prefix(self):
        from data.sec_statements import _collapse_dimensional
        # CFR-style: member headers carry no 'Axis | ' prefix.
        dim = {"units_scale": 1e3, "periods": ["Dec. 31, 2025"], "rows": [
            {"label": "Financing Receivable, Credit Quality Indicator [Line Items]", "header": True, "values": []},
            {"label": "Loans", "header": False, "values": [13791.7]},
            {"label": "Commercial and industrial loans", "header": True, "values": []},
            {"label": "Loans", "header": False, "values": [4478.3]},
        ]}
        out = _collapse_dimensional(dim)
        self.assertEqual([r["label"] for r in out["rows"]],
                         ["Total loans", "Commercial and industrial loans"])


if __name__ == "__main__":
    unittest.main()
