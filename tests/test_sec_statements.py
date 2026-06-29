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

    def test_income_statement_word_order_matched_not_comprehensive_or_cashflow(self):
        # PNC titles its primary income R-file "Consolidated Income Statement" (the
        # "income statement" word order, NOT "statement of income") and the matcher
        # must select it — while STILL rejecting the comprehensive-income and
        # cash-flow siblings that also contain the word "income"/"statement". First
        # matching Report wins, so the income statement (R3) must precede the
        # comprehensive companion in the summary and still be the one chosen.
        summary = (b'<?xml version="1.0"?><FilingSummary><MyReports>'
                   b'<Report><ShortName>Cover Page</ShortName><HtmlFileName>R1.htm</HtmlFileName></Report>'
                   b'<Report><ShortName>Consolidated Balance Sheet</ShortName><HtmlFileName>R2.htm</HtmlFileName></Report>'
                   b'<Report><ShortName>Consolidated Income Statement</ShortName><HtmlFileName>R3.htm</HtmlFileName></Report>'
                   b'<Report><ShortName>Consolidated Statement of Comprehensive Income</ShortName><HtmlFileName>R4.htm</HtmlFileName></Report>'
                   b'<Report><ShortName>Consolidated Statement of Cash Flows</ShortName><HtmlFileName>R5.htm</HtmlFileName></Report>'
                   b'</MyReports></FilingSummary>')
        import data.sec_statements as s
        with mock.patch.object(s, "_get", return_value=summary):
            out = _statement_rfiles("base/")
        self.assertEqual(out.get("income"), "R3.htm")     # "Income Statement" matched
        self.assertEqual(out.get("balance"), "R2.htm")
        self.assertEqual(out.get("cashflow"), "R5.htm")
        self.assertNotEqual(out.get("income"), "R4.htm")  # NOT comprehensive income

    def test_income_pattern_rejects_comprehensive_and_cashflow_directly(self):
        # The (want, reject) pair itself: "income statement" matches; the
        # comprehensive-income and cash-flow titles are rejected even though they
        # carry "income"/"statement".
        import data.sec_statements as s
        want, reject = s._STMT_PATTERNS["income"]
        for title in ("Consolidated Income Statement", "Income Statement",
                      "Consolidated Statements of Income",
                      "Consolidated Statements of Operations",
                      "Consolidated Statement of Earnings"):
            self.assertTrue(want.search(title) and not reject.search(title), title)
        for title in ("Consolidated Statement of Comprehensive Income",
                      "Consolidated Statements of Comprehensive Income",
                      "Consolidated Statement of Cash Flows",
                      "Consolidated Statements of Cash Flows"):
            self.assertTrue(bool(reject.search(title)), title)   # rejected


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


# ── Multi-quarter (discrete single quarters; audit invariant A21) ────────────
# A Q3 10-Q income R-file modeled on the real ZION layout: a "3 Months Ended"
# band over the first two columns and a "9 Months Ended" YTD band over the next
# two — same period-end dates, told apart by DURATION, not column position.
_Q3_INCOME = (
    b'<table class="report">'
    b'<tr><th class="tl">CONSOLIDATED STATEMENTS OF INCOME $ in Millions</th>'
    b'<th class="th" colspan="2">3 Months Ended</th>'
    b'<th class="th" colspan="2">9 Months Ended</th></tr>'
    b'<tr><th class="th">Sep. 30, 2025</th><th class="th">Sep. 30, 2024</th>'
    b'<th class="th">Sep. 30, 2025</th><th class="th">Sep. 30, 2024</th></tr>'
    b'<tr><td class="pl">Net interest income</td>'
    b'<td class="nump">600</td><td class="nump">560</td>'
    b'<td class="nump">1,750</td><td class="nump">1,650</td></tr>'
    b'<tr><td class="pl">Net income</td>'
    b'<td class="nump">222</td><td class="nump">214</td>'
    b'<td class="nump">636</td><td class="nump">568</td></tr>'
    b'</table>')

# A 10-K income R-file: "12 Months Ended" over three fiscal years.
_FY_INCOME = (
    b'<table class="report">'
    b'<tr><th class="tl">CONSOLIDATED STATEMENTS OF INCOME $ in Millions</th>'
    b'<th class="th" colspan="3">12 Months Ended</th></tr>'
    b'<tr><th class="th">Dec. 31, 2025</th><th class="th">Dec. 31, 2024</th>'
    b'<th class="th">Dec. 31, 2023</th></tr>'
    b'<tr><td class="pl">Net interest income</td>'
    b'<td class="nump">2,400</td><td class="nump">2,200</td><td class="nump">2,000</td></tr>'
    b'<tr><td class="pl">Net income</td>'
    b'<td class="nump">899</td><td class="nump">784</td><td class="nump">680</td></tr>'
    b'</table>')

# A balance R-file with a KEY/ZION-style empty spacer <td class="th"> in every
# data row — point-in-time, two date columns, no duration band.
_BS_QUARTER = (
    b'<table class="report">'
    b'<tr><th class="tl" colspan="2">CONSOLIDATED BALANCE SHEETS $ in Millions</th>'
    b'<th class="th">Sep. 30, 2025</th><th class="th">Dec. 31, 2024</th></tr>'
    b'<tr><td class="pl">Total assets</td><td class="th"></td>'
    b'<td class="nump">88,500</td><td class="nump">88,800</td></tr>'
    b'<tr><td class="pl">Total deposits</td><td class="th"></td>'
    b'<td class="nump">75,100</td><td class="nump">74,900</td></tr>'
    b'</table>')


def _parsed(html, meta_acc):
    """parse_rfile + _column_meta on synthetic bytes, packaged like the stitcher
    inputs (with _colmeta and _meta)."""
    from data.sec_statements import parse_rfile, _column_meta
    stmt = parse_rfile(html)
    ncol = max((len(r["values"]) for r in stmt["rows"] if not r["header"]), default=0)
    stmt["_colmeta"] = _column_meta(html, ncol)
    stmt["_meta"] = {"accession": meta_acc}
    return stmt


class TestQuarterColumnIdentification(unittest.TestCase):
    """The discrete-quarter column is identified by DURATION metadata, never by
    position — the 3-month and 9-month columns share the same period-end date."""

    def test_column_meta_tags_each_column_with_duration(self):
        from data.sec_statements import parse_rfile, _column_meta
        p = parse_rfile(_Q3_INCOME)
        ncol = max(len(r["values"]) for r in p["rows"] if not r["header"])
        meta = _column_meta(_Q3_INCOME, ncol)
        self.assertEqual(meta, [(3, "Sep. 30, 2025"), (3, "Sep. 30, 2024"),
                                (9, "Sep. 30, 2025"), (9, "Sep. 30, 2024")])

    def test_discrete_index_picks_three_month_not_ytd(self):
        from data.sec_statements import _column_meta, _discrete_quarter_index, parse_rfile
        p = parse_rfile(_Q3_INCOME)
        ncol = max(len(r["values"]) for r in p["rows"] if not r["header"])
        meta = _column_meta(_Q3_INCOME, ncol)
        idx = _discrete_quarter_index(meta, (2025, 9))
        self.assertEqual(idx, 0)                       # the 3-month column, not col 2 (9-month)
        # The value at that column is the discrete quarter (222), NOT the YTD (636).
        ni = next(r for r in p["rows"] if r["label"] == "Net income")
        self.assertEqual(ni["values"][idx], 222e6)
        self.assertNotEqual(ni["values"][idx], 636e6)  # A21: never the YTD

    def test_balance_meta_handles_spacer_and_no_duration_band(self):
        from data.sec_statements import parse_rfile, _column_meta
        p = parse_rfile(_BS_QUARTER)
        ncol = max(len(r["values"]) for r in p["rows"] if not r["header"])
        meta = _column_meta(_BS_QUARTER, ncol)
        # Spacer <td class="th"> dropped; two point-in-time date columns, no
        # duration band -> default 12 (unused for the point-in-time stitch).
        self.assertEqual(ncol, 2)
        self.assertEqual([d for _, d in meta], ["Sep. 30, 2025", "Dec. 31, 2024"])

    def test_unidentifiable_layout_returns_none(self):
        # A header whose expanded width can't be mapped to the value columns
        # yields no column metadata (caller then emits no discrete quarter).
        from data.sec_statements import _column_meta
        bad = (b'<table class="report">'
               b'<tr><th class="tl">X $ in Millions</th>'
               b'<th class="th" colspan="2">3 Months Ended</th></tr>'
               b'<tr><th class="th">Sep. 30, 2025</th></tr>'   # only 1 date for 2 value cols
               b'<tr><td class="pl">Net income</td>'
               b'<td class="nump">10</td><td class="nump">20</td></tr>'
               b'</table>')
        from data.sec_statements import parse_rfile
        p = parse_rfile(bad)
        ncol = max(len(r["values"]) for r in p["rows"] if not r["header"])
        self.assertIsNone(_column_meta(bad, ncol))


class TestFlowQuarterStitch(unittest.TestCase):
    """Discrete-quarter income stitch: Q1–Q3 are the as-reported 3-month column;
    Q4 = FY (10-K) − 9M (Q3 10-Q); no quarter is ever a YTD value (A21)."""

    def _build(self):
        from data.sec_statements import _stitch_flow_quarters
        pq = [_parsed(_Q3_INCOME, "Q3ACC")]
        pk = [_parsed(_FY_INCOME, "KACC")]
        # Window: Q4'25 (Dec) and Q3'25 (Sep) suffice to exercise both paths.
        q_ends = [(2025, 12), (2025, 9)]
        return _stitch_flow_quarters(pq, pk, q_ends)

    def test_q3_is_three_month_value(self):
        st = self._build()
        self.assertEqual(st["periods"], ["Q4'25", "Q3'25"])
        ni = next(r for r in st["rows"] if r["label"] == "Net income")
        # periods order: [Q4'25, Q3'25]; Q3'25 = the 3-month column value 222.
        self.assertEqual(ni["values"][1], 222e6)
        nii = next(r for r in st["rows"] if r["label"] == "Net interest income")
        self.assertEqual(nii["values"][1], 600e6)      # 3-month, not 1,750 YTD

    def test_q4_equals_fy_minus_nine_month(self):
        # Hand-computed: FY net income 899 − 9M 636 = 263 ($ in millions).
        st = self._build()
        ni = next(r for r in st["rows"] if r["label"] == "Net income")
        self.assertEqual(ni["values"][0], (899 - 636) * 1e6)   # Q4'25 = 263M
        nii = next(r for r in st["rows"] if r["label"] == "Net interest income")
        self.assertEqual(nii["values"][0], (2400 - 1750) * 1e6)  # 650M

    def test_a21_no_quarter_equals_a_ytd_value(self):
        # Guard the audit invariant directly: no emitted quarter cell may equal a
        # YTD figure that appears in the source filings (636 9M, 1,750 9M, 568 9M).
        st = self._build()
        ytd = {636e6, 1750e6, 568e6, 1650e6}
        for r in st["rows"]:
            if r["header"]:
                continue
            for v in r["values"]:
                self.assertNotIn(v, ytd)

    def test_q4_blank_when_nine_month_missing(self):
        # FY (10-K) present but the fiscal year's 9-month 10-Q absent: Q4 cannot
        # be derived (FY − 9M needs both), so that quarter is blank, never a
        # guess — even though a discrete Q3 exists to anchor the window.
        from data.sec_statements import _stitch_flow_quarters
        # parsed_q carries ONLY the Q3 discrete quarter (drop the 9-month band by
        # using a 3-month-only filing); parsed_k carries the FY.
        q3_only = (
            b'<table class="report">'
            b'<tr><th class="tl">CONSOLIDATED STATEMENTS OF INCOME $ in Millions</th>'
            b'<th class="th" colspan="2">3 Months Ended</th></tr>'
            b'<tr><th class="th">Sep. 30, 2025</th><th class="th">Sep. 30, 2024</th></tr>'
            b'<tr><td class="pl">Net income</td>'
            b'<td class="nump">222</td><td class="nump">214</td></tr>'
            b'</table>')
        pq = [_parsed(q3_only, "Q3ACC")]
        pk = [_parsed(_FY_INCOME, "KACC")]
        st = _stitch_flow_quarters(pq, pk, [(2025, 12), (2025, 9)])
        ni = next(r for r in st["rows"] if r["label"] == "Net income")
        self.assertEqual(st["periods"], ["Q4'25", "Q3'25"])
        self.assertIsNone(ni["values"][0])             # Q4 blank: no 9-month source
        self.assertEqual(ni["values"][1], 222e6)       # Q3 still the 3-month value


class TestBalanceQuarterStitch(unittest.TestCase):
    """Balance sheet is point-in-time: each quarter-end column is a snapshot
    taken straight from the filing, never a difference of two columns."""

    def test_point_in_time_snapshots(self):
        from data.sec_statements import _stitch_balance_quarters
        pq = [_parsed(_BS_QUARTER, "Q3ACC")]
        st = _stitch_balance_quarters(pq, [], [(2025, 9), (2024, 12)])
        self.assertEqual(st["periods"], ["Q3'25", "Q4'24"])
        ta = next(r for r in st["rows"] if r["label"] == "Total assets")
        # Snapshot values, NOT a difference (88,500 and 88,800), in raw dollars.
        self.assertEqual(ta["values"], [88500e6, 88800e6])


if __name__ == "__main__":
    unittest.main()
