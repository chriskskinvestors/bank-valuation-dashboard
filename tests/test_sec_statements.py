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

    def test_share_count_row_typed_by_xbrl_not_label_unscaled(self):
        # AFBI bug: the weighted-average-shares row is labelled bare "Basic" /
        # "Diluted" (no "in shares" hint), so the LABEL heuristic missed it and
        # the "$ in Thousands" multiplier inflated the 6.3M share count to 6.3B.
        # The fix reads each row's XBRL element data type (xbrli:sharesItemType /
        # perShareItemType / monetaryItemType) from the R-file's authRefData
        # blocks and scales ONLY monetary rows. Pin: a typed share row is NOT
        # ×1000-scaled, the typed dollar row in the SAME statement IS, and the
        # per-share row is unaffected.
        def ar(eid, dtype):
            return (b'<table class="authRefData" id="defref_' + eid + b'">'
                    b'<tr><td><strong> Data Type:</strong></td><td>' + dtype
                    + b'</td></tr></table>')
        rf = (b'<table class="report">'
              b'<tr><th class="tl">Statements of Income - USD ($) $ in Thousands</th>'
              b'<th class="th">12 Months Ended</th></tr>'
              b'<tr><th class="th">Dec. 31, 2025</th></tr>'
              b'<tr><td class="pl"><a onclick="Show.showAR( this, '
              b"'defref_us-gaap_NetIncomeLoss', window );\">Net income</a></td>"
              b'<td class="nump">2,500</td></tr>'
              b'<tr><td class="pl"><a onclick="Show.showAR( this, '
              b"'defref_us-gaap_EarningsPerShareBasic', window );\">Basic</a></td>"
              b'<td class="nump">$ 1.33</td></tr>'
              b'<tr><td class="pl"><a onclick="Show.showAR( this, '
              b"'defref_us-gaap_WeightedAverageNumberOfSharesOutstandingBasic', "
              b'window );">Basic</a></td>'
              b'<td class="nump">6,277,003</td></tr>'
              b'</table>'
              + ar(b'us-gaap_NetIncomeLoss', b'xbrli:monetaryItemType')
              + ar(b'us-gaap_EarningsPerShareBasic', b'dtr-types:perShareItemType')
              + ar(b'us-gaap_WeightedAverageNumberOfSharesOutstandingBasic',
                   b'xbrli:sharesItemType'))
        rows = [r for r in parse_rfile(rf)["rows"] if not r["header"]]
        ni = next(r for r in rows if r["label"] == "Net income")
        eps, shares = (r for r in rows if r["label"] == "Basic")
        self.assertEqual(ni["values"][0], 2_500_000.0)        # monetary -> x1000
        self.assertAlmostEqual(eps["values"][0], 1.33)        # per-share -> unscaled
        self.assertEqual(shares["values"][0], 6_277_003.0)    # shares -> NOT x1000

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

    def test_income_pattern_accepts_income_and_comprehensive_titles(self):
        # The (want, reject) pair is a CANDIDATE-gathering first cut: "income
        # statement" matches AND every "comprehensive income/loss" title matches
        # too — a filer may title its PRIMARY income statement that way (NFBK,
        # BSVN, CVBF, AMTB, TCBIO). The CONTENT guard (_is_income_body) at
        # selection time, not the title, rejects a standalone pure-OCI statement.
        # Only cash-flow and parenthetical siblings are title-rejected.
        import data.sec_statements as s
        want, reject = s._STMT_PATTERNS["income"]
        for title in ("Consolidated Income Statement", "Income Statement",
                      "Consolidated Statements of Income",
                      "Consolidated Statements of Operations",
                      "Consolidated Statement of Earnings",
                      "Consolidated Statements of (Loss) Income",      # GLBZ
                      "Consolidated Statement of Comprehensive Income",  # NFBK/BSVN
                      "Consolidated Statements of Comprehensive Income",
                      "Earnings and Comprehensive Income",             # CVBF
                      "Operations and Comprehensive (Loss) Income"):   # AMTB
            self.assertTrue(want.search(title) and not reject.search(title), title)
        for title in ("Consolidated Statement of Cash Flows",
                      "Consolidated Statements of Cash Flows",
                      "Consolidated Statements of Income (Parenthetical)"):
            self.assertTrue(bool(reject.search(title)), title)   # rejected


# A COMBINED "Statements of Income AND Comprehensive Income" R-file (ABCB shape):
# the FULL income statement (interest income/expense, provision, noninterest
# income/expense, net income) followed by the OCI continuation, then EPS/share
# rows. Two 3-month and two 9-month columns (a Q3 10-Q layout).
_COMBINED_INCOME = (
    b'<table class="report">'
    b'<tr><th class="tl">Consolidated Statements of Income and Comprehensive Income '
    b'(unaudited) - USD ($) $ in Thousands</th>'
    b'<th class="th" colspan="2">3 Months Ended</th>'
    b'<th class="th" colspan="2">9 Months Ended</th></tr>'
    b'<tr><th class="th">Sep. 30, 2025</th><th class="th">Sep. 30, 2024</th>'
    b'<th class="th">Sep. 30, 2025</th><th class="th">Sep. 30, 2024</th></tr>'
    b'<tr><td class="pl">Total interest income</td>'
    b'<td class="nump">355,046</td><td class="nump">355,146</td>'
    b'<td class="nump">1,036,462</td><td class="nump">1,031,921</td></tr>'
    b'<tr><td class="pl">Total interest expense</td>'
    b'<td class="nump">117,082</td><td class="nump">141,086</td>'
    b'<td class="nump">344,846</td><td class="nump">404,552</td></tr>'
    b'<tr><td class="pl">Provision for credit losses</td>'
    b'<td class="nump">22,630</td><td class="nump">6,107</td>'
    b'<td class="nump">47,294</td><td class="nump">45,985</td></tr>'
    b'<tr><td class="pl">Total noninterest expense</td>'
    b'<td class="nump">154,566</td><td class="nump">151,777</td>'
    b'<td class="nump">460,860</td><td class="nump">455,845</td></tr>'
    b'<tr><td class="pl">Net income</td>'
    b'<td class="nump">106,029</td><td class="nump">99,212</td>'
    b'<td class="nump">303,798</td><td class="nump">264,309</td></tr>'
    # OCI continuation — must be stripped.
    b'<tr><td class="pl">Other comprehensive income</td>'
    b'<td class="text"> </td><td class="text"> </td>'
    b'<td class="text"> </td><td class="text"> </td></tr>'
    b'<tr><td class="pl">Net unrealized holding gains on AFS securities</td>'
    b'<td class="nump">12,145</td><td class="nump">22,296</td>'
    b'<td class="nump">35,378</td><td class="nump">20,215</td></tr>'
    b'<tr><td class="pl">Total other comprehensive income</td>'
    b'<td class="nump">12,057</td><td class="nump">22,296</td>'
    b'<td class="nump">35,290</td><td class="nump">20,215</td></tr>'
    b'<tr><td class="pl">Comprehensive income</td>'
    b'<td class="nump">118,086</td><td class="nump">121,508</td>'
    b'<td class="nump">339,088</td><td class="nump">284,524</td></tr>'
    # EPS / share rows follow the OCI block — they are part of the income
    # statement and must SURVIVE the strip.
    b'<tr><td class="pl">Diluted earnings per common share (in dollars per share)</td>'
    b'<td class="nump">$ 1.54</td><td class="nump">1.44</td>'
    b'<td class="nump">4.41</td><td class="nump">3.83</td></tr>'
    b'</table>')

# A STANDALONE "Statements of Comprehensive Income" (OCI-only, ZION shape): it
# STARTS at net income and has NO revenue/expense lines above it — must be
# rejected as an income statement.
_OCI_ONLY = (
    b'<table class="report">'
    b'<tr><th class="tl">CONSOLIDATED STATEMENTS OF COMPREHENSIVE INCOME - '
    b'USD ($) $ in Millions</th><th class="th">3 Months Ended</th></tr>'
    b'<tr><th class="th">Mar. 31, 2026</th></tr>'
    b'<tr><td class="pl">Net income for the period</td><td class="nump">233</td></tr>'
    b'<tr><td class="pl">Other comprehensive income, net of tax</td>'
    b'<td class="nump">40</td></tr>'
    b'<tr><td class="pl">Net change in unrealized gains on investment securities</td>'
    b'<td class="nump">30</td></tr>'
    b'<tr><td class="pl">Comprehensive income</td><td class="nump">273</td></tr>'
    b'</table>')


class TestCombinedIncomeComprehensive(unittest.TestCase):
    """A COMBINED 'Income and Comprehensive Income' statement (ABCB) is the full
    income statement and must be ACCEPTED and parsed through net income, with the
    OCI continuation stripped; a STANDALONE OCI-only 'Comprehensive Income'
    statement (which starts at net income) must still be REJECTED — the
    discriminator is CONTENT (income lines above net income), not the title."""

    def test_combined_title_accepted_standalone_oci_rejected(self):
        # Title-level first cut: BOTH a combined 'income and comprehensive income'
        # AND a standalone 'comprehensive income' title now survive as CANDIDATES
        # (the title no longer gates on word order — a filer may title its primary
        # income statement either way). The standalone pure-OCI body is rejected by
        # the CONTENT guard at selection (see test_income_parse_rejects_standalone_
        # oci_only), not by the title. The parenthetical companion is still rejected.
        import data.sec_statements as s
        want, reject = s._STMT_PATTERNS["income"]
        combined = "Consolidated Statements of Income and Comprehensive Income (unaudited)"
        self.assertTrue(want.search(combined) and not reject.search(combined))
        for oci in ("Consolidated Statements of Comprehensive Income",
                    "CONSOLIDATED STATEMENTS OF COMPREHENSIVE INCOME"):
            self.assertTrue(want.search(oci) and not reject.search(oci), oci)
        # The combined statement's own parenthetical companion is still rejected.
        self.assertTrue(bool(reject.search(combined + " (Parenthetical)")))

    def test_matcher_picks_combined_when_it_is_the_only_income_rfile(self):
        # ABCB's 10-Q has ONLY the combined R-file (no separate income statement),
        # so the matcher must select it for 'income'.
        import data.sec_statements as s
        summary = (b'<?xml version="1.0"?><FilingSummary><MyReports>'
                   b'<Report><ShortName>Cover Page</ShortName><HtmlFileName>R1.htm</HtmlFileName></Report>'
                   b'<Report><ShortName>Consolidated Balance Sheets</ShortName><HtmlFileName>R2.htm</HtmlFileName></Report>'
                   b'<Report><ShortName>Consolidated Statements of Income and Comprehensive Income (unaudited)</ShortName><HtmlFileName>R4.htm</HtmlFileName></Report>'
                   b'<Report><ShortName>Consolidated Statements of Income and Comprehensive Income (unaudited) (Parenthetical)</ShortName><HtmlFileName>R5.htm</HtmlFileName></Report>'
                   b'</MyReports></FilingSummary>')
        with mock.patch.object(s, "_get", return_value=summary):
            out = _statement_rfiles("base/")
        self.assertEqual(out.get("income"), "R4.htm")

    def test_separate_income_statement_still_wins_over_combined_sibling(self):
        # When BOTH a plain 'Statements of Income' AND a combined sibling exist
        # (ABCB 10-K), first-match-wins must keep selecting the plain income
        # statement — the relaxation must not change that.
        import data.sec_statements as s
        summary = (b'<?xml version="1.0"?><FilingSummary><MyReports>'
                   b'<Report><ShortName>Consolidated Statements of Income</ShortName><HtmlFileName>R5.htm</HtmlFileName></Report>'
                   b'<Report><ShortName>Consolidated Statements of Comprehensive Income</ShortName><HtmlFileName>R6.htm</HtmlFileName></Report>'
                   b'</MyReports></FilingSummary>')
        with mock.patch.object(s, "_get", return_value=summary):
            out = _statement_rfiles("base/")
        self.assertEqual(out.get("income"), "R5.htm")    # plain income, not comprehensive

    def test_income_parse_strips_oci_and_keeps_net_income_and_eps(self):
        from data.sec_statements import _income_parse
        p = _income_parse(_COMBINED_INCOME)
        self.assertIsNotNone(p)
        labels = [r["label"] for r in p["rows"]]
        # Income lines + net income kept; OCI block removed; EPS preserved.
        self.assertIn("Net income", labels)
        self.assertIn("Total interest income", labels)
        self.assertIn("Diluted earnings per common share (in dollars per share)", labels)
        self.assertNotIn("Other comprehensive income", labels)
        self.assertNotIn("Total other comprehensive income", labels)
        self.assertNotIn("Comprehensive income", labels)
        # Net income value is the income bottom line (3-month col), in raw dollars.
        ni = next(r for r in p["rows"] if r["label"] == "Net income")
        self.assertEqual(ni["values"][0], 106_029_000.0)

    def test_income_parse_rejects_standalone_oci_only(self):
        # The OCI-only statement starts at net income with no income lines above —
        # the content guard returns None so it is never rendered as income.
        from data.sec_statements import _income_parse, _is_income_body
        from data.sec_statements import parse_rfile
        self.assertFalse(_is_income_body(parse_rfile(_OCI_ONLY)))
        self.assertIsNone(_income_parse(_OCI_ONLY))

    def test_combined_fixture_yields_discrete_quarter_income_series(self):
        # ABCB-style: the combined R-file feeds the discrete-quarter stitch and
        # yields a real single-quarter income series (3-month column, not YTD).
        from data.sec_statements import _income_parse, _column_meta
        from data.sec_statements import _stitch_flow_quarters
        stmt = _income_parse(_COMBINED_INCOME)
        ncol = max(len(r["values"]) for r in stmt["rows"] if not r["header"])
        stmt["_colmeta"] = _column_meta(_COMBINED_INCOME, ncol)
        stmt["_meta"] = {"accession": "Q3ACC"}
        out = _stitch_flow_quarters([stmt], [], [(2025, 9)])
        self.assertEqual(out["periods"], ["Q3'25"])
        ni = next(r for r in out["rows"] if r["label"] == "Net income")
        self.assertEqual(ni["values"][0], 106_029_000.0)   # discrete quarter
        self.assertNotEqual(ni["values"][0], 303_798_000.0)  # A21: never the 9M YTD


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


# ── PGC: per-share / share-count rows must NOT be Q4 YTD-differenced ──────────
# PGC labels BOTH its EPS rows and its weighted-average-share rows with the bare
# words "Basic"/"Diluted" (no "(in shares)" / "(per share)" disambiguator), under
# separate "EARNINGS PER SHARE" / "WEIGHTED AVERAGE NUMBER OF SHARES OUTSTANDING"
# headers. The two rows are told apart ONLY by their XBRL element data type
# (perShareItemType vs sharesItemType). Without that, both normalize to the same
# stitch key and the share count silently overwrites the EPS, then Q4 = FY − 9M
# differences a non-additive value into a NEGATIVE share count.
def _ar(eid, dtype):
    return (b'<table class="authRefData" id="defref_' + eid + b'">'
            b'<tr><td><strong> Data Type:</strong></td><td>' + dtype
            + b'</td></tr></table>')


def _pgc_row(eid, label, *vals):
    cells = b''.join(b'<td class="nump">' + v + b'</td>' for v in vals)
    return (b'<tr><td class="pl"><a onclick="Show.showAR( this, '
            b"'defref_" + eid + b"', window );\">" + label + b'</a></td>' + cells
            + b'</tr>')


# A Q3 10-Q income R-file (PGC shape): a 3-month band + a 9-month YTD band, with
# bare 'Basic'/'Diluted' EPS (perShare) AND bare 'Basic'/'Diluted' weighted-
# average shares (sharesItemType) — same labels, told apart by XBRL type only.
_PGC_Q3_INCOME = (
    b'<table class="report">'
    b'<tr><th class="tl">CONSOLIDATED STATEMENTS OF INCOME - USD ($) '
    b'$ in Thousands</th>'
    b'<th class="th" colspan="2">3 Months Ended</th>'
    b'<th class="th" colspan="2">9 Months Ended</th></tr>'
    b'<tr><th class="th">Sep. 30, 2025</th><th class="th">Sep. 30, 2024</th>'
    b'<th class="th">Sep. 30, 2025</th><th class="th">Sep. 30, 2024</th></tr>'
    + _pgc_row(b'us-gaap_NetIncomeLoss', b'NET INCOME',
               b'9,631', b'7,587', b'30,167', b'22,704')
    + _pgc_row(b'us-gaap_EarningsPerShareBasic', b'Basic',
               b'0.55', b'0.43', b'1.71', b'1.29')
    + _pgc_row(b'us-gaap_EarningsPerShareDiluted', b'Diluted',
               b'0.54', b'0.43', b'1.70', b'1.28')
    + _pgc_row(b'us-gaap_WeightedAverageNumberOfSharesOutstandingBasic', b'Basic',
               b'17,576,899', b'17,616,046', b'17,630,000', b'17,650,000')
    + _pgc_row(b'us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding', b'Diluted',
               b'17,686,979', b'17,700,042', b'17,740,000', b'17,760,000')
    + b'</table>'
    + _ar(b'us-gaap_NetIncomeLoss', b'xbrli:monetaryItemType')
    + _ar(b'us-gaap_EarningsPerShareBasic', b'dtr-types:perShareItemType')
    + _ar(b'us-gaap_EarningsPerShareDiluted', b'dtr-types:perShareItemType')
    + _ar(b'us-gaap_WeightedAverageNumberOfSharesOutstandingBasic',
          b'xbrli:sharesItemType')
    + _ar(b'us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding',
          b'xbrli:sharesItemType'))

# The matching 10-K income R-file (12-month FY column). PGC's 10-K income R-file
# carries EPS but NOT the weighted-average-share rows; net income is the only
# additive FY value the Q4 = FY − 9M derivation can use.
_PGC_FY_INCOME = (
    b'<table class="report">'
    b'<tr><th class="tl">CONSOLIDATED STATEMENTS OF INCOME - USD ($) '
    b'$ in Thousands</th><th class="th">12 Months Ended</th></tr>'
    b'<tr><th class="th">Dec. 31, 2025</th></tr>'
    + _pgc_row(b'us-gaap_NetIncomeLoss', b'NET INCOME', b'42,326')
    + _pgc_row(b'us-gaap_EarningsPerShareBasic', b'Basic', b'2.12')
    + _pgc_row(b'us-gaap_EarningsPerShareDiluted', b'Diluted', b'2.10')
    + b'</table>'
    + _ar(b'us-gaap_NetIncomeLoss', b'xbrli:monetaryItemType')
    + _ar(b'us-gaap_EarningsPerShareBasic', b'dtr-types:perShareItemType')
    + _ar(b'us-gaap_EarningsPerShareDiluted', b'dtr-types:perShareItemType'))


class TestPerShareShareRowsNotDifferenced(unittest.TestCase):
    """PGC cardinal-rule fix: in the discrete-quarter stitch a per-share (EPS) or
    share-count row carries its as-reported quarter value and is NEVER put through
    Q4 = FY − 9M differencing (which is valid only for additive monetary flows);
    its Q4 cell is n/a, never a difference and never the 12-month value. An EPS row
    a filer labels the same bare word as its weighted-average-share row stays a
    DISTINCT line — the share count never overwrites the EPS."""

    def _build(self):
        from data.sec_statements import _stitch_flow_quarters
        pq = [_parsed(_PGC_Q3_INCOME, "Q3ACC")]
        pk = [_parsed(_PGC_FY_INCOME, "KACC")]
        return _stitch_flow_quarters(pq, pk, [(2025, 12), (2025, 9)])

    def test_eps_and_shares_rows_are_distinct_not_swapped(self):
        # Two 'Basic' rows survive (EPS + weighted-average shares), each carrying
        # its OWN kind of value — the share count never clobbers the EPS.
        st = self._build()
        basics = [r for r in st["rows"] if r["label"] == "Basic"]
        self.assertEqual(len(basics), 2)
        eps = next(r for r in basics if r["values"][1] is not None
                   and abs(r["values"][1]) < 100)          # per-share magnitude
        shares = next(r for r in basics if r["values"][1] is not None
                      and r["values"][1] > 1000)           # share-count magnitude
        self.assertAlmostEqual(eps["values"][1], 0.55)     # Q3'25 EPS (3-month col)
        self.assertEqual(shares["values"][1], 17_576_899.0)  # Q3'25 shares (3-month)
        # The EPS row is a per-share number, NEVER a share count.
        self.assertLess(abs(eps["values"][1]), 100)

    def test_q3_eps_is_three_month_not_ytd(self):
        # Q3'25 EPS is the as-reported 3-month value (0.55), NOT the 9-month YTD
        # (1.71) — A21 holds for per-share rows too.
        st = self._build()
        eps = next(r for r in st["rows"]
                   if r["label"] == "Basic" and r["values"][1] is not None
                   and abs(r["values"][1]) < 100)
        self.assertAlmostEqual(eps["values"][1], 0.55)
        for r in st["rows"]:
            for v in r["values"]:
                self.assertNotEqual(v, 1.71)               # never the YTD EPS

    def test_q4_per_share_and_share_rows_are_na_never_differenced(self):
        # Q4 = FY − 9M is non-additive for EPS/shares: the Q4 cell is n/a, never a
        # difference (which would be NEGATIVE: 2.12 − 1.71, or 17,576k − 17,630k),
        # and never the 12-month FY value (2.12) mislabeled as a quarter.
        st = self._build()
        self.assertEqual(st["periods"], ["Q4'25", "Q3'25"])
        for r in st["rows"]:
            if r["label"] in ("Basic", "Diluted"):
                self.assertIsNone(r["values"][0])          # Q4 EPS / shares = n/a
                self.assertNotEqual(r["values"][0], 2.12)  # not the FY value
                # No negative cell anywhere on a per-share / share row.
                self.assertFalse(any(v is not None and v < 0 for v in r["values"]))

    def test_monetary_flow_row_is_differenced_for_q4(self):
        # The additive monetary flow (NET INCOME) IS differenced: Q4 = FY − 9M =
        # 42,326 − 30,167 = 12,159 (thousands → raw dollars).
        st = self._build()
        ni = next(r for r in st["rows"] if r["label"] == "NET INCOME")
        self.assertEqual(ni["values"][0], (42_326 - 30_167) * 1e3)   # Q4'25
        self.assertEqual(ni["values"][1], 9_631_000.0)               # Q3'25 (3-month)


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


# ── P0-A: "Net earnings" bottom line (FFIN) ──────────────────────────────────
# FFIN titles its income statement "Consolidated Statements of Earnings"; the
# bottom line is "Net earnings" (FY2025 $253,579K) and the EPS rows read
# "NET EARNINGS PER SHARE, BASIC". The content discriminator must recognize "net
# earnings" as the bottom line WITHOUT matching the per-share rows, or the whole
# income statement is rejected and FFIN income renders n/a.
_FFIN_EARNINGS = (
    b'<table class="report">'
    b'<tr><th class="tl">Consolidated Statements of Earnings - USD ($) $ in Thousands</th>'
    b'<th class="th">12 Months Ended</th></tr>'
    b'<tr><th class="th">Dec. 31, 2025</th><th class="th">Dec. 31, 2024</th></tr>'
    b'<tr><td class="pl">Total interest income</td>'
    b'<td class="nump">700,000</td><td class="nump">650,000</td></tr>'
    b'<tr><td class="pl">Provision for credit losses</td>'
    b'<td class="nump">20,000</td><td class="nump">18,000</td></tr>'
    b'<tr><td class="pl">Earnings before income taxes</td>'
    b'<td class="nump">309,603</td><td class="nump">271,846</td></tr>'
    b'<tr><td class="pl">Net earnings</td>'
    b'<td class="nump">253,579</td><td class="nump">223,511</td></tr>'
    b'<tr><td class="pl">NET EARNINGS PER SHARE, BASIC (in dollars per share)</td>'
    b'<td class="nump">$ 1.77</td><td class="nump">1.56</td></tr>'
    b'</table>')


class TestNetEarningsBottomLine(unittest.TestCase):
    """FFIN's 'Net earnings' bottom line must be recognized as a real income
    statement, with the bottom-line match landing on 'Net earnings' (the value)
    and NOT on 'NET EARNINGS PER SHARE' (the EPS row)."""

    def test_net_earnings_recognized_and_per_share_excluded(self):
        from data.sec_statements import (parse_rfile, _is_income_body,
                                          _income_parse, _NET_INCOME)
        p = parse_rfile(_FFIN_EARNINGS)
        self.assertTrue(_is_income_body(p))            # was False → income dropped
        matches = [r["label"] for r in p["rows"]
                   if not r["header"] and _NET_INCOME.match(r["label"])]
        self.assertEqual(matches, ["Net earnings"])    # not the PER SHARE row
        ip = _income_parse(_FFIN_EARNINGS)
        self.assertIsNotNone(ip)
        ni = next(r for r in ip["rows"] if r["label"] == "Net earnings")
        self.assertEqual(ni["values"][0], 253_579_000.0)   # ties FFIN FY2025

    def test_plain_net_income_still_recognized(self):
        # The relaxation must not regress the original 'net income' wording.
        from data.sec_statements import _is_income_body
        self.assertTrue(_is_income_body(parse_rfile(_INCOME)))


# ── P0-B: multi-table balance R-file — keep only the PRIMARY statement (JPM) ──
# JPM's balance R-file holds the primary Consolidated Balance Sheet (Total assets
# 4,424,900 $M) AND a 'VIEs consolidated by the Firm' supplemental table (its own
# 'Total assets' 43,295 subtotal), a footnote-[1] narrative paragraph, and a
# 'December 31, (in millions) | 2025' year-as-value garbage row. parse_rfile also
# pads every row to the widest column count the supplemental table produces.
_JPM_BALANCE = (
    b'<table class="report">'
    b'<tr><th class="tl">Consolidated Balance Sheets - USD ($) $ in Millions</th>'
    b'<th class="th">Dec. 31, 2025</th><th class="th">Dec. 31, 2024</th></tr>'
    b'<tr><td class="pl">Total assets</td>'
    b'<td class="nump">4,424,900</td><td class="nump">4,002,814</td></tr>'
    b'<tr><td class="pl">Total liabilities</td>'
    b'<td class="nump">4,062,462</td><td class="nump">3,658,056</td></tr>'
    b'<tr><td class="pl">Total stockholders\xe2\x80\x99 equity</td>'
    b'<td class="nump">362,438</td><td class="nump">344,758</td></tr>'
    b'<tr><td class="pl">Total liabilities and stockholders\xe2\x80\x99 equity</td>'
    b'<td class="nump">4,424,900</td><td class="nump">4,002,814</td></tr>'
    # --- supplemental VIE table + narrative + garbage row: ALL must be dropped ---
    b'<tr><td class="pl">Total assets</td>'
    b'<td class="nump">43,295</td><td class="nump">41,076</td></tr>'
    b'<tr><td class="pl">Total liabilities</td>'
    b'<td class="nump">28,642</td><td class="nump">27,777</td></tr>'
    b'<tr><td class="pl">[1] The following table presents information on assets</td>'
    b'<td class="text"> </td><td class="text"> </td></tr>'
    b'<tr><td class="pl">December 31, (in millions)</td>'
    b'<td class="nump">2,025</td><td class="nump">2,024</td></tr>'
    b'</table>')


class TestPrimaryBalanceIsolation(unittest.TestCase):
    """A multi-table balance R-file must keep ONLY the primary statement (through
    its 'Total liabilities and … equity' grand total); the VIE subtotal table,
    the footnote narrative, and the year-as-value garbage row are dropped."""

    def test_jpm_keeps_primary_drops_supplemental(self):
        from data.sec_statements import _balance_parse
        p = _balance_parse(_JPM_BALANCE)
        labels = [r["label"] for r in p["rows"]]
        # Primary 'Total assets' is the ONLY one, and it's JPM's real figure.
        ta = [r for r in p["rows"] if r["label"] == "Total assets"]
        self.assertEqual(len(ta), 1)
        self.assertEqual(ta[0]["values"], [4_424_900e6, 4_002_814e6])
        # A = L + E reconciles on the primary statement.
        end = next(r for r in p["rows"]
                   if r["label"].startswith("Total liabilities and"))
        self.assertEqual(end["values"][0], 4_424_900e6)
        # The supplemental narrative / garbage rows are gone.
        self.assertNotIn("December 31, (in millions)", labels)
        self.assertFalse(any(l.startswith("[1]") for l in labels))
        # Every kept row is trimmed to the two real period columns.
        for r in p["rows"]:
            if not r["header"]:
                self.assertEqual(len(r["values"]), 2)

    def test_single_table_balance_unchanged(self):
        # A balance sheet with no trailing supplemental table passes through
        # unchanged (truncation at the grand total is a no-op).
        from data.sec_statements import _balance_parse, parse_rfile
        single = (
            b'<table class="report">'
            b'<tr><th class="tl">Consolidated Balance Sheets $ in Millions</th>'
            b'<th class="th">Dec. 31, 2025</th><th class="th">Dec. 31, 2024</th></tr>'
            b'<tr><td class="pl">Total assets</td>'
            b'<td class="nump">88,990</td><td class="nump">88,775</td></tr>'
            b'<tr><td class="pl">Total liabilities and shareholders equity</td>'
            b'<td class="nump">88,990</td><td class="nump">88,775</td></tr>'
            b'</table>')
        self.assertEqual([r["label"] for r in _balance_parse(single)["rows"]],
                         [r["label"] for r in parse_rfile(single)["rows"]])


# ── P1: union-split — wording variants merge to one row, distinct lines stay ──
class TestConsolidateVariants(unittest.TestCase):
    """Cross-filing wording variants of the SAME line collapse to one row; two
    genuinely-distinct lines (both populated in a shared period) stay separate."""

    def _stmt(self, rows, periods=("25", "24", "23", "22", "21")):
        return {"periods": list(periods), "units_scale": 1e3,
                "rows": [{"label": l, "header": False, "values": v} for l, v in rows]}

    def test_net_income_loss_variant_merges_keeps_distinct_separate(self):
        from data.sec_statements import _consolidate_variants
        out = _consolidate_variants(self._stmt([
            ("Net income (loss)", [None, None, None, None, 5725.0]),
            ("Net income", [6997.0, 5953.0, 5647.0, 6113.0, None]),
            ("Net income attributable to common shareholders",
             [6619.0, 5529.0, 5153.0, 5735.0, 5436.0]),
        ]))
        byl = {r["label"]: r["values"] for r in out["rows"]}
        # 'Net income (loss)' and 'Net income' → ONE row (newest label kept).
        self.assertNotIn("Net income (loss)", byl)
        self.assertEqual(byl["Net income"], [6997.0, 5953.0, 5647.0, 6113.0, 5725.0])
        # The attributable line is DISTINCT and survives untouched.
        self.assertEqual(byl["Net income attributable to common shareholders"],
                         [6619.0, 5529.0, 5153.0, 5735.0, 5436.0])

    def test_over_merge_guard_same_period_both_populated(self):
        from data.sec_statements import _consolidate_variants
        # 'Total equity' (all years) vs 'Total Huntington shareholders equity'
        # (token-subset) MUST stay separate — they overlap in 2025-2023.
        out = _consolidate_variants(self._stmt([
            ("Total Huntington shareholders equity",
             [24342.0, 19740.0, 19353.0, None, None]),
            ("Total equity", [24379.0, 19782.0, 19398.0, 17769.0, 19318.0]),
        ]))
        labels = [r["label"] for r in out["rows"]]
        self.assertEqual(len(labels), 2)               # NOT merged
        self.assertIn("Total equity", labels)

    def test_registrant_name_equity_variant_merges(self):
        from data.sec_statements import _consolidate_variants
        out = _consolidate_variants(self._stmt([
            ("Total Huntington Bancshares Inc shareholders equity",
             [None, None, None, 17731.0, 19297.0]),
            ("Total Huntington shareholders equity",
             [24342.0, 19740.0, 19353.0, None, None]),
        ]))
        self.assertEqual(len(out["rows"]), 1)          # name variant collapses
        self.assertEqual(out["rows"][0]["values"],
                         [24342.0, 19740.0, 19353.0, 17731.0, 19297.0])

    def test_eps_basic_variant_merges_but_shares_stays_separate(self):
        from data.sec_statements import _consolidate_variants
        out = _consolidate_variants(self._stmt([
            ("Basic earnings per common share (in dollars per share)",
             [None, None, None, None, 12.71]),
            ("Basic (in dollars per share)", [16.6, 13.76, 12.8, 13.86, None]),
            ("Basic (shares)", [396.0, 399.0, 401.0, 412.0, 426.0]),
        ]))
        byl = {r["label"]: r["values"] for r in out["rows"]}
        # Single-token EPS variant merges (both per-share); the share-COUNT row,
        # populated every year, never merges into the per-share row.
        self.assertEqual(len(out["rows"]), 2)
        self.assertEqual(byl["Basic (in dollars per share)"],
                         [16.6, 13.76, 12.8, 13.86, 12.71])
        self.assertEqual(byl["Basic (shares)"], [396.0, 399.0, 401.0, 412.0, 426.0])

    def test_placeholder_label_dropped(self):
        from data.sec_statements import _consolidate_variants
        out = _consolidate_variants(self._stmt([
            ("Common stock, $5 par value", [6312.0, 6580.0, 6669.0, 6634.0, 6639.0]),
            ("Common Stock Shares Issued Not Disclosed",
             [None, None, None, None, None]),
        ]))
        labels = [r["label"] for r in out["rows"]]
        self.assertNotIn("Common Stock Shares Issued Not Disclosed", labels)
        self.assertIn("Common stock, $5 par value", labels)


# ── P2: XBRL noise rows ('[Extensible Enumeration]', bare '[1]' footnote) ─────
class TestXbrlNoiseRows(unittest.TestCase):
    def test_extensible_enumeration_and_footnote_marker_filtered(self):
        rf = (
            b'<table class="report">'
            b'<tr><th class="tl">Statements of Income - USD ($) $ in Thousands</th>'
            b'<th class="th">12 Months Ended</th></tr>'
            b'<tr><th class="th">Dec. 31, 2025</th></tr>'
            b'<tr><td class="pl">Net income</td><td class="nump">2,156,000</td></tr>'
            b'<tr><td class="pl">Defined Benefit Plan, Net Periodic Benefit Cost, '
            b'Statement of Income [Extensible Enumeration]</td>'
            b'<td class="text"> </td></tr>'
            b'<tr><td class="pl">[1]</td><td class="text"> </td></tr>'
            b'</table>')
        labels = [r["label"] for r in parse_rfile(rf)["rows"]]
        self.assertEqual(labels, ["Net income"])       # both noise rows dropped


# ── P0 statement-fidelity fixes (200-bank audit) ─────────────────────────────
# Each fixture is shaped like the real R-file that triggered a "whole statement
# missing or wrong" defect, so the content-aware selector / guards are pinned to
# the exact failure they fix.

# A comprehensive-titled PRIMARY income statement (NFBK/BSVN/CVBF shape): real
# revenue/expense lines ABOVE net income, then the OCI continuation. Body IS an
# income statement and must be ACCEPTED on content despite the 'comprehensive'
# title.
_COMPREHENSIVE_TITLED_INCOME = (
    b'<table class="report">'
    b'<tr><th class="tl">Consolidated Statements of Comprehensive Income - '
    b'USD ($) $ in Thousands</th><th class="th">12 Months Ended</th></tr>'
    b'<tr><th class="th">Dec. 31, 2025</th><th class="th">Dec. 31, 2024</th></tr>'
    b'<tr><td class="pl">Total interest income</td>'
    b'<td class="nump">100,000</td><td class="nump">90,000</td></tr>'
    b'<tr><td class="pl">Total interest expense</td>'
    b'<td class="nump">30,000</td><td class="nump">25,000</td></tr>'
    b'<tr><td class="pl">Provision for credit losses</td>'
    b'<td class="nump">5,000</td><td class="nump">4,000</td></tr>'
    b'<tr><td class="pl">Net income</td>'
    b'<td class="nump">20,000</td><td class="nump">18,000</td></tr>'
    b'<tr><td class="pl">Other comprehensive income</td>'
    b'<td class="text"> </td><td class="text"> </td></tr>'
    b'<tr><td class="pl">Comprehensive income</td>'
    b'<td class="nump">22,000</td><td class="nump">19,000</td></tr>'
    b'</table>')

# A net-LOSS income statement (PNBK shape): bottom line "Net loss" plus a leading
# parenthetical "Net (loss) income" form (GLBZ) — both must register as the
# bottom line so a loss year passes _is_income_body.
_NET_LOSS_INCOME = (
    b'<table class="report">'
    b'<tr><th class="tl">Consolidated Statements of (Loss) Income - '
    b'USD ($) $ in Thousands</th><th class="th">12 Months Ended</th></tr>'
    b'<tr><th class="th">Dec. 31, 2025</th></tr>'
    b'<tr><td class="pl">Total interest income</td><td class="nump">50,000</td></tr>'
    b'<tr><td class="pl">Total noninterest expense</td><td class="nump">60,000</td></tr>'
    b'<tr><td class="pl">Net (loss) income</td><td class="num">(12,710)</td></tr>'
    b'</table>')

# A real Statement of Condition (CBU/OBT term) with a Total assets row that ties
# to total liabilities + equity.
_STATEMENT_OF_CONDITION = (
    b'<table class="report">'
    b'<tr><th class="tl">CONSOLIDATED STATEMENTS OF CONDITION - '
    b'USD ($) $ in Thousands</th><th class="th">Dec. 31, 2025</th></tr>'
    b'<tr><th class="th">Dec. 31, 2025</th></tr>'
    b'<tr><td class="pl">Cash and due from banks</td><td class="nump">10,000</td></tr>'
    b'<tr><td class="pl">Total assets</td><td class="nump">100,000</td></tr>'
    b'<tr><td class="pl">Total deposits</td><td class="nump">80,000</td></tr>'
    b'<tr><td class="pl">Total liabilities</td><td class="nump">90,000</td></tr>'
    b'<tr><td class="pl">Total stockholders\x92 equity</td><td class="nump">10,000</td></tr>'
    b'<tr><td class="pl">Total liabilities and stockholders\x92 equity</td>'
    b'<td class="nump">100,000</td></tr>'
    b'</table>')

# A balance sheet (EWBC shape) whose assets total is a bare "TOTAL" — no
# "Total assets" text — recognized via the liabilities+equity structure.
_BARE_TOTAL_BALANCE = (
    b'<table class="report">'
    b'<tr><th class="tl">CONSOLIDATED BALANCE SHEETS - USD ($) $ in Thousands</th>'
    b'<th class="th">Dec. 31, 2025</th></tr>'
    b'<tr><th class="th">Dec. 31, 2025</th></tr>'
    b'<tr><td class="pl">Cash and due from banks</td><td class="nump">5,000</td></tr>'
    b'<tr><td class="pl">Other assets</td><td class="nump">95,000</td></tr>'
    b'<tr><td class="pl">TOTAL</td><td class="nump">100,000</td></tr>'
    b'<tr><td class="pl">Total liabilities</td><td class="nump">90,000</td></tr>'
    b'<tr><td class="pl">Total stockholders\x92 equity</td><td class="nump">10,000</td></tr>'
    b'<tr><td class="pl">TOTAL</td><td class="nump">100,000</td></tr>'
    b'</table>')

# A parent-company-only Schedule II income statement (BMRC shape): the "equity in
# undistributed net income of subsidiary" signature line means it is NOT the
# consolidated income statement and must be rejected.
_PARENT_ONLY_INCOME = (
    b'<table class="report">'
    b'<tr><th class="tl">Parent Company Financial Information - Statements of '
    b'Income - USD ($) $ in Thousands</th><th class="th">12 Months Ended</th></tr>'
    b'<tr><th class="th">Dec. 31, 2025</th></tr>'
    b'<tr><td class="pl">Dividends from bank subsidiary</td><td class="nump">5,000</td></tr>'
    b'<tr><td class="pl">Equity in undistributed net income of subsidiary</td>'
    b'<td class="nump">10,000</td></tr>'
    b'<tr><td class="pl">Net income</td><td class="nump">15,000</td></tr>'
    b'</table>')

# An off-balance-sheet note (OBT shape): no Total assets / liabilities / equity
# structure — rejected as a balance sheet by the positive guard, NOT by a bare
# "off-balance sheet" string (which is also a legit income line).
_OFF_BALANCE_NOTE = (
    b'<table class="report">'
    b'<tr><th class="tl">Financial Instruments with Off-Balance Sheet Risk - '
    b'USD ($) $ in Thousands</th><th class="th">Dec. 31, 2025</th></tr>'
    b'<tr><th class="th">Dec. 31, 2025</th></tr>'
    b'<tr><td class="pl">Commitments to extend credit</td><td class="nump">40,000</td></tr>'
    b'<tr><td class="pl">Standby letters of credit</td><td class="nump">2,000</td></tr>'
    b'</table>')

# A pension footnote (CBU shape) sharing a 'financial condition'-ish title but
# carrying no balance structure — must be rejected as a balance sheet.
_PENSION_NOTE = (
    b'<table class="report">'
    b'<tr><th class="tl">Defined Benefit Plan Financial Condition - '
    b'USD ($) $ in Thousands</th><th class="th">Dec. 31, 2025</th></tr>'
    b'<tr><th class="th">Dec. 31, 2025</th></tr>'
    b'<tr><td class="pl">Benefit obligation at beginning of year</td>'
    b'<td class="nump">30,000</td></tr>'
    b'<tr><td class="pl">Benefit obligation at end of year</td>'
    b'<td class="nump">32,000</td></tr>'
    b'</table>')


class TestP0IncomeSelectionByContent(unittest.TestCase):
    """Fix 1/2/6: income is chosen by CONTENT (_is_income_body), so a
    comprehensive-titled primary income statement is ACCEPTED, a net-loss bottom
    line is recognized, a standalone pure-OCI statement is rejected, and a
    parent-only Schedule II is rejected."""

    def test_comprehensive_titled_income_accepted_by_content(self):
        from data.sec_statements import _income_parse, _is_income_body, parse_rfile
        p = parse_rfile(_COMPREHENSIVE_TITLED_INCOME)
        self.assertTrue(_is_income_body(p))            # content, not title
        out = _income_parse(_COMPREHENSIVE_TITLED_INCOME)
        labels = [r["label"] for r in out["rows"]]
        self.assertIn("Net income", labels)
        self.assertIn("Total interest income", labels)
        self.assertNotIn("Comprehensive income", labels)   # OCI stripped

    def test_standalone_oci_only_still_rejected(self):
        from data.sec_statements import _is_income_body, parse_rfile
        self.assertFalse(_is_income_body(parse_rfile(_OCI_ONLY)))

    def test_net_loss_bottom_line_recognized(self):
        from data.sec_statements import _income_parse, _NET_INCOME
        self.assertTrue(_NET_INCOME.match("Net loss"))
        self.assertTrue(_NET_INCOME.match("Net (loss) income"))
        self.assertTrue(_NET_INCOME.match("NET (LOSS) INCOME"))
        out = _income_parse(_NET_LOSS_INCOME)
        self.assertIsNotNone(out)                       # loss year not dropped
        ni = next(r for r in out["rows"] if r["label"] == "Net (loss) income")
        self.assertEqual(ni["values"][0], -12_710_000.0)

    def test_net_income_on_sale_not_mistaken_for_bottom_line(self):
        # A realized-gain component line ("Net loss on sale of …") must NOT match
        # the bottom-line pattern, or _is_income_body's net-income anchor lands too
        # early and a real statement is wrongly rejected.
        from data.sec_statements import _NET_INCOME
        self.assertIsNone(_NET_INCOME.match("Net loss on sale of available-for-sale securities"))
        self.assertIsNone(_NET_INCOME.match("Net gain on sale of loans"))

    def test_parent_only_schedule_ii_rejected_as_income(self):
        from data.sec_statements import _is_income_body, parse_rfile
        self.assertFalse(_is_income_body(parse_rfile(_PARENT_ONLY_INCOME)))


class TestP0BalanceContentGuard(unittest.TestCase):
    """Fix 3/4: a balance candidate is accepted only when its body is a real
    Statement of Condition (Total assets, or Total liabilities + equity, ties),
    rejecting pension / off-balance-sheet / parent-only footnotes."""

    def test_statements_of_condition_title_matches(self):
        import data.sec_statements as s
        want, reject = s._STMT_PATTERNS["balance"]
        for t in ("Consolidated Statements of Condition",
                  "Statement of Condition", "Consolidated Balance Sheets",
                  "Consolidated Statements of Financial Position"):
            self.assertTrue(want.search(t) and not reject.search(t), t)

    def test_real_statement_of_condition_accepted(self):
        from data.sec_statements import _is_balance_body, _balance_parse
        self.assertTrue(_is_balance_body(_balance_parse(_STATEMENT_OF_CONDITION)))

    def test_bare_total_balance_accepted_via_liab_equity(self):
        # EWBC labels its assets total a bare "TOTAL" — accepted via the
        # liabilities+equity structure, not a "Total assets" string.
        from data.sec_statements import _is_balance_body, _balance_parse
        self.assertTrue(_is_balance_body(_balance_parse(_BARE_TOTAL_BALANCE)))

    def test_pension_note_rejected_as_balance(self):
        from data.sec_statements import _is_balance_body, parse_rfile
        self.assertFalse(_is_balance_body(parse_rfile(_PENSION_NOTE)))

    def test_off_balance_note_rejected_as_balance(self):
        from data.sec_statements import _is_balance_body, parse_rfile
        self.assertFalse(_is_balance_body(parse_rfile(_OFF_BALANCE_NOTE)))

    def test_off_balance_string_not_a_reject_signal_for_income(self):
        # "Provision for off-balance sheet credit exposures" is a legitimate
        # income line (BANF) — it must NOT flag the statement as a note.
        from data.sec_statements import _is_parent_only_or_note
        rf = (b'<table class="report">'
              b'<tr><th class="tl">Consolidated Statements of Comprehensive Income - '
              b'USD ($) $ in Thousands</th><th class="th">12 Months Ended</th></tr>'
              b'<tr><th class="th">Dec. 31, 2025</th></tr>'
              b'<tr><td class="pl">Total interest income</td><td class="nump">100,000</td></tr>'
              b'<tr><td class="pl">Provision for off-balance sheet credit exposures</td>'
              b'<td class="nump">500</td></tr>'
              b'<tr><td class="pl">Net income</td><td class="nump">20,000</td></tr>'
              b'</table>')
        from data.sec_statements import parse_rfile, _is_income_body
        self.assertFalse(_is_parent_only_or_note(parse_rfile(rf)))
        self.assertTrue(_is_income_body(parse_rfile(rf)))


class TestP0SelectorPicksRightCandidate(unittest.TestCase):
    """The content-aware selector walks title candidates in document order and
    returns the first whose body passes the guard — rejecting an OCI-only or
    parent-only sibling that title-matches."""

    def _summary(self, pairs):
        rows = b"".join(
            b"<Report><ShortName>" + s + b"</ShortName>"
            b"<HtmlFileName>" + f + b"</HtmlFileName></Report>"
            for s, f in pairs)
        return (b'<?xml version="1.0"?><FilingSummary><MyReports>'
                + rows + b'</MyReports></FilingSummary>')

    def test_income_selector_skips_oci_only_picks_real_income(self):
        import data.sec_statements as s
        summary = self._summary([
            (b"Consolidated Statement of Comprehensive (Loss) Income", b"R5.htm"),  # OCI-only
            (b"Consolidated Statements of (Loss) Income", b"R4.htm"),               # real
        ])

        def fake_get(url):
            if url.endswith("FilingSummary.xml"):
                return summary
            if url.endswith("R5.htm"):
                return _OCI_ONLY
            if url.endswith("R4.htm"):
                return _COMPREHENSIVE_TITLED_INCOME
            raise AssertionError(url)

        with mock.patch.object(s, "_get", side_effect=fake_get):
            fn, parsed = s._select_primary_rfile("base/", "income")
        self.assertEqual(fn, "R4.htm")                 # real income, not OCI-only
        self.assertIsNotNone(parsed)

    def test_balance_selector_skips_off_balance_picks_condition(self):
        import data.sec_statements as s
        summary = self._summary([
            (b"Consolidated Statements of Condition", b"R2.htm"),
            (b"Financial Instruments with Off-Balance Sheet Risk", b"R24.htm"),
        ])

        def fake_get(url):
            if url.endswith("FilingSummary.xml"):
                return summary
            if url.endswith("R2.htm"):
                return _STATEMENT_OF_CONDITION
            if url.endswith("R24.htm"):
                return _OFF_BALANCE_NOTE
            raise AssertionError(url)

        with mock.patch.object(s, "_get", side_effect=fake_get):
            fn, parsed = s._select_primary_rfile("base/", "balance")
        self.assertEqual(fn, "R2.htm")
        self.assertIsNotNone(parsed)

    def test_selector_returns_none_when_no_valid_candidate(self):
        # Only a parent-only income schedule exists → honest n/a, never that table.
        import data.sec_statements as s
        summary = self._summary([
            (b"Parent Company Financial Information - Statements of Income (Details)",
             b"R88.htm"),
        ])

        def fake_get(url):
            if url.endswith("FilingSummary.xml"):
                return summary
            return _PARENT_ONLY_INCOME

        with mock.patch.object(s, "_get", side_effect=fake_get):
            fn, parsed = s._select_primary_rfile("base/", "income")
        self.assertIsNone(fn)
        self.assertIsNone(parsed)


class TestP0ColumnMetaHeaderTolerance(unittest.TestCase):
    """Fix 5: _column_meta must tolerate a trailing footnote-[N] <th> (FCBC) and a
    colspan=2 date header where each period yields one value column (BANC), instead
    of bailing (None) and dropping the whole quarterly statement."""

    # FCBC: date header carries a trailing "[1]" footnote <th>; ncol = 2.
    _FCBC_BALANCE = (
        b'<table class="report">'
        b'<tr><th class="tl">Condensed Consolidated Balance Sheets</th>'
        b'<th class="th">Mar. 31, 2026</th><th class="th">Dec. 31, 2025</th>'
        b'<th class="th">[1]</th></tr>'
        b'<tr><td class="pl">Total assets</td>'
        b'<td class="nump">3,644,947</td><td class="nump">3,259,643</td></tr>'
        b'<tr><td class="pl">Total liabilities</td>'
        b'<td class="nump">3,300,000</td><td class="nump">2,900,000</td></tr>'
        b'<tr><td class="pl">Total stockholders equity</td>'
        b'<td class="nump">344,947</td><td class="nump">359,643</td></tr>'
        b'</table>')

    # BANC: each date header <th> has colspan="2"; each period yields ONE value
    # column (ncol = 2).
    _BANC_BALANCE = (
        b'<table class="report">'
        b'<tr><th class="tl" colspan="2">Condensed Consolidated Balance Sheets</th>'
        b'<th class="th" colspan="2">Mar. 31, 2026</th>'
        b'<th class="th" colspan="2">Dec. 31, 2025</th></tr>'
        b'<tr><td class="pl">Total assets</td>'
        b'<td class="nump">34,724,241</td><td class="nump">34,500,000</td></tr>'
        b'<tr><td class="pl">Total liabilities</td>'
        b'<td class="nump">30,000,000</td><td class="nump">29,800,000</td></tr>'
        b'<tr><td class="pl">Total stockholders equity</td>'
        b'<td class="nump">4,724,241</td><td class="nump">4,700,000</td></tr>'
        b'</table>')

    def _ncol(self, raw):
        from data.sec_statements import parse_rfile
        p = parse_rfile(raw)
        return max((len(r["values"]) for r in p["rows"] if not r["header"]), default=0)

    def test_trailing_footnote_th_stripped(self):
        from data.sec_statements import _column_meta
        raw = self._FCBC_BALANCE
        meta = _column_meta(raw, self._ncol(raw))
        self.assertIsNotNone(meta)                     # was None (bailed) before fix
        self.assertEqual([p for _, p in meta], ["Mar. 31, 2026", "Dec. 31, 2025"])

    def test_colspan_date_header_reconciled(self):
        from data.sec_statements import _column_meta
        raw = self._BANC_BALANCE
        meta = _column_meta(raw, self._ncol(raw))
        self.assertIsNotNone(meta)                     # was None (bailed) before fix
        self.assertEqual([p for _, p in meta], ["Mar. 31, 2026", "Dec. 31, 2025"])

    def test_clean_quarterly_balance_not_regressed(self):
        # A plain two-period header (ZION/USB/PNC shape) still maps cleanly.
        from data.sec_statements import _column_meta
        raw = (b'<table class="report">'
               b'<tr><th class="tl">Consolidated Balance Sheets</th>'
               b'<th class="th">Mar. 31, 2026</th><th class="th">Dec. 31, 2025</th></tr>'
               b'<tr><td class="pl">Total assets</td>'
               b'<td class="nump">100,000</td><td class="nump">99,000</td></tr>'
               b'</table>')
        meta = _column_meta(raw, self._ncol(raw))
        self.assertEqual([p for _, p in meta], ["Mar. 31, 2026", "Dec. 31, 2025"])


# ── P1 cleanliness pass — pinning the already-shipped fixes (7, 8) + OVLY (9) ─
# These backfill tests pin behavior the audit shipped without tests:
#   7  strip the XBRL [Member] revenue-disaggregation tail from an income R-file
#   8  fold cross-filing near-synonym label rewordings into one row, with the
#      over-merge guard holding two genuinely-distinct lines apart
#   9  OVLY: the total-assets value fills every disclosed year even when the row
#      is reworded between the us-gaap ShortName 'Assets' and 'Assets, Total'

# An income R-file with the ASC-606 disaggregation-of-revenue table leaking in
# after the real statement: a run of {'<topic> [Member]' caption, a duplicate
# section header, a generic value row}. The real income lines precede it.
_MEMBER_TAIL_INCOME = (
    b'<table class="report">'
    b'<tr><th class="tl">Consolidated Statements of Income - '
    b'USD ($) $ in Thousands</th><th class="th">12 Months Ended</th></tr>'
    b'<tr><th class="th">Dec. 31, 2025</th><th class="th">Dec. 31, 2024</th></tr>'
    b'<tr><td class="pl">Total interest income</td>'
    b'<td class="nump">100,000</td><td class="nump">90,000</td></tr>'
    b'<tr><td class="pl">Noninterest income</td>'
    b'<td class="nump">8,000</td><td class="nump">7,000</td></tr>'
    b'<tr><td class="pl">Net income</td>'
    b'<td class="nump">20,000</td><td class="nump">18,000</td></tr>'
    # ── disaggregation tail (must be dropped from the first [Member] on) ──
    b'<tr><td class="pl">Mortgage Banking [Member]</td>'
    b'<td class="text"> </td><td class="text"> </td></tr>'
    b'<tr><td class="pl">Disaggregation of Revenue [Line Items]</td>'
    b'<td class="text"> </td><td class="text"> </td></tr>'
    b'<tr><td class="pl">Mortgage banking fee income</td>'
    b'<td class="nump">1,200</td><td class="nump">1,100</td></tr>'
    b'</table>')


class TestStripMemberTail(unittest.TestCase):
    """Fix 7: a '[Member]' dimension caption (and the disaggregation tail it
    opens) is dropped from the income statement; real line items survive."""

    def test_member_tail_dropped_real_lines_kept(self):
        from data.sec_statements import _income_parse
        out = _income_parse(_MEMBER_TAIL_INCOME)
        labels = [r["label"] for r in out["rows"]]
        # The [Member] caption and everything after it are gone.
        self.assertNotIn("Mortgage Banking [Member]", labels)
        self.assertFalse(any(l.endswith("[Member]") for l in labels))
        self.assertNotIn("Mortgage banking fee income", labels)   # tail value row
        self.assertNotIn("Disaggregation of Revenue [Line Items]", labels)
        # Real income lines above the tail are kept.
        self.assertIn("Total interest income", labels)
        self.assertIn("Net income", labels)

    def test_no_member_row_statement_unchanged(self):
        from data.sec_statements import _strip_member_dimensions, parse_rfile
        p = parse_rfile(_INCOME)
        before = [r["label"] for r in p["rows"]]
        after = [r["label"] for r in _strip_member_dimensions(p)["rows"]]
        self.assertEqual(before, after)                  # no [Member] → untouched


class TestNearSynonymMerge(unittest.TestCase):
    """Fix 8: near-synonym label rewordings of the SAME line fold to ONE row
    carrying the populated values, while the over-merge guard keeps two lines
    that both hold a value in a shared period apart."""

    def _stmt(self, rows, periods=("25", "24", "23", "22", "21")):
        return {"periods": list(periods), "units_scale": 1e3,
                "rows": [{"label": l, "header": False, "values": v} for l, v in rows]}

    def test_direction_losses_gains_variant_merges(self):
        # CZFS: 'Available for sale security (losses) gains, net' (2021) vs
        # 'Available for sale security losses, net' (2022+) — same realized line.
        from data.sec_statements import _consolidate_variants
        out = _consolidate_variants(self._stmt([
            ("Available for sale security losses, net",
             [5.0, 8.0, 6.0, None, None]),
            ("Available for sale security (losses) gains, net",
             [None, None, None, 7.0, 9.0]),
        ]))
        self.assertEqual(len(out["rows"]), 1)
        self.assertEqual(out["rows"][0]["values"], [5.0, 8.0, 6.0, 7.0, 9.0])

    def test_stockholders_shareholders_equity_variant_merges(self):
        # A filer rewords its equity total 'stockholders'' ↔ 'shareholders'' —
        # the same grand total, merged via the structural-total concept anchor.
        from data.sec_statements import _consolidate_variants
        out = _consolidate_variants(self._stmt([
            ("Total stockholders' equity", [None, None, 300.0, 290.0, 280.0]),
            ("Total shareholders' equity", [350.0, 320.0, None, None, None]),
        ]))
        self.assertEqual(len(out["rows"]), 1)
        self.assertEqual(out["rows"][0]["values"], [350.0, 320.0, 300.0, 290.0, 280.0])

    def test_shortname_total_suffix_variant_merges(self):
        # us-gaap ShortName 'Liabilities and Equity, Total' ↔ the filer's
        # 'Total liabilities and equity' — the balance-sheet grand total.
        from data.sec_statements import _consolidate_variants
        out = _consolidate_variants(self._stmt([
            ("Total liabilities and equity", [None, None, 900.0, 880.0, 860.0]),
            ("Liabilities and Equity, Total", [950.0, 920.0, None, None, None]),
        ]))
        self.assertEqual(len(out["rows"]), 1)
        self.assertEqual(out["rows"][0]["values"], [950.0, 920.0, 900.0, 880.0, 860.0])

    def test_over_merge_guard_same_period_distinct_lines_stay_separate(self):
        # Two DISTINCT realized lines that coexist in one filing (both populated
        # in 2025-2023) must NOT collapse, even though _DIRECTION_DROP makes
        # their token sets equal — the same-period guard blocks the merge.
        from data.sec_statements import _consolidate_variants
        out = _consolidate_variants(self._stmt([
            ("Realized gains, net", [10.0, 12.0, 11.0, None, None]),
            ("Realized losses, net", [4.0, 5.0, 3.0, None, None]),
        ]))
        labels = [r["label"] for r in out["rows"]]
        self.assertEqual(len(labels), 2)                 # NOT merged
        self.assertIn("Realized gains, net", labels)
        self.assertIn("Realized losses, net", labels)

    def test_over_merge_guard_distinct_per_share_common_lines(self):
        # USB shape: 'Basic'/'Diluted' earnings-per-common-share are DISTINCT
        # lines (no subset relation, and both populated every year) — separate.
        from data.sec_statements import _consolidate_variants
        out = _consolidate_variants(self._stmt([
            ("Basic earnings per common share (in dollars per share)",
             [4.10, 3.90, 3.80, 3.70, 3.60]),
            ("Diluted earnings per common share (in dollars per share)",
             [4.05, 3.85, 3.75, 3.65, 3.55]),
        ]))
        self.assertEqual(len(out["rows"]), 2)            # distinct lines kept

    def test_different_section_lines_never_merge(self):
        # A balance-sheet 'Total assets' grand total and an income-statement
        # 'Total interest and dividend income' share only the over-linkable token
        # 'total' — they must stay two rows (no subset relation either way).
        from data.sec_statements import _consolidate_variants
        out = _consolidate_variants(self._stmt([
            ("Total assets", [1000.0, 950.0, 900.0, 880.0, 860.0]),
            ("Total interest and dividend income", [50.0, 48.0, 46.0, 44.0, 42.0]),
        ]))
        self.assertEqual(len(out["rows"]), 2)


class TestOvlyTotalAssetsRewording(unittest.TestCase):
    """Fix 9: OVLY's total-assets row reworded across years between the us-gaap
    ShortName 'Assets' (FY23-25 10-Ks) and 'Assets, Total' (FY21-22). Both are
    the us-gaap:Assets grand total — they must fold to ONE row so the most load-
    bearing balance line fills every disclosed year, not blank for 4 of 5."""

    def _stmt(self, rows, periods=("25", "24", "23", "22", "21")):
        return {"periods": list(periods), "units_scale": 1e3,
                "rows": [{"label": l, "header": False, "values": v} for l, v in rows]}

    def test_bare_assets_recognized_as_total_assets_concept(self):
        from data.sec_statements import _structural_total_class, _TOTAL_ASSETS
        self.assertEqual(_structural_total_class("Assets"), "assets")
        self.assertEqual(_structural_total_class("Assets, Total"), "assets")
        self.assertEqual(_structural_total_class("Total assets"), "assets")
        # Anchored: ordinary asset lines and the section header are NOT the total.
        self.assertIsNone(_TOTAL_ASSETS.match("Other assets"))
        self.assertIsNone(_TOTAL_ASSETS.match("Interest receivable and other assets"))
        self.assertIsNone(_TOTAL_ASSETS.match("Total assets acquired"))

    def test_assets_and_assets_total_fold_to_one_filled_row(self):
        from data.sec_statements import _consolidate_variants
        # FY25-22 carry the bare 'Assets' (newer filings); FY21 carries the older
        # 'Assets, Total'. Real OVLY values (us-gaap:Assets, $ thousands).
        out = _consolidate_variants(self._stmt([
            ("Assets", [2023116.0, 1900604.0, 1842422.0, 1968346.0, None]),
            ("Assets, Total", [None, None, None, None, 1964478.0]),
        ]))
        self.assertEqual(len(out["rows"]), 1)            # ONE total-assets row
        self.assertEqual(out["rows"][0]["values"],
                         [2023116.0, 1900604.0, 1842422.0, 1968346.0, 1964478.0])

    def test_assets_does_not_merge_into_an_ordinary_asset_line(self):
        # The bare-'Assets' total must not absorb 'Other assets' (different
        # concept, no structural-total class) — they stay separate.
        from data.sec_statements import _consolidate_variants
        out = _consolidate_variants(self._stmt([
            ("Assets", [2023116.0, 1900604.0, None, None, None]),
            ("Other assets", [41538.0, 35906.0, 33000.0, 31000.0, 29000.0]),
        ]))
        labels = [r["label"] for r in out["rows"]]
        self.assertEqual(len(labels), 2)
        self.assertIn("Assets", labels)
        self.assertIn("Other assets", labels)


# ── EFSCP (Enterprise Financial Services Corp) — standard-label statements ───
# EFSCP renders its PRIMARY income/balance statements with us-gaap STANDARD
# labels, not custom wording: the income bottom line is "Net Income (Loss)
# Attributable to Parent, Total"; revenue/expense lines are "Interest Income,
# Securities, …" / "Interest Expense, Deposits"; the balance total-assets row is
# "Assets, Total" and the grand total "Liabilities and Equity, Total". The income
# R-file sits beside a STANDALONE "Statements of Comprehensive Income" sibling
# (pure OCI) and a PARENT-ONLY "Statements of Operations (Details)" sibling; the
# balance R-file beside a "Fair Value … Reported on the Balance Sheets (Details)"
# footnote and a parent-only "Condensed Balance Sheets (Details)". A 200-bank
# audit reported EFSCP returning None for BOTH statements (whole statement
# missing). These pin that the CONTENT guards recognize the standard-label bottom
# lines and that the selector picks the real consolidated statement over the OCI /
# parent-only / footnote siblings — verified against EFSCP's FY2025 10-K
# (NetIncomeLoss 201,374 thousand; Assets 17,300,884 thousand; A = L + E).

_EFSCP_INCOME = b"""<table class="report">
<tr><th class="tl">Consolidated Statements of Income - USD ($) $ in Thousands</th><th class="th" colspan="3">12 Months Ended</th></tr>
<tr><th class="th">Dec. 31, 2025</th><th class="th">Dec. 31, 2024</th><th class="th">Dec. 31, 2023</th></tr>
<tr><td class="pl">Interest and Fee Income, Loans and Leases</td><td class="nump">754,459</td><td class="nump">754,930</td><td class="nump">687,852</td></tr>
<tr><td class="pl">Interest Income, Securities, Operating, Taxable</td><td class="nump">81,644</td><td class="nump">51,352</td><td class="nump">39,510</td></tr>
<tr><td class="pl">Interest Expense, Deposits</td><td class="nump">241,098</td><td class="nump">264,608</td><td class="nump">183,723</td></tr>
<tr><td class="pl">Provision for loan losses not covered under FDIC loss share</td><td class="nump">26,337</td><td class="nump">21,508</td><td class="nump">36,605</td></tr>
<tr><td class="pl">Income Tax Expense (Benefit)</td><td class="nump">82,343</td><td class="nump">45,978</td><td class="nump">52,467</td></tr>
<tr><td class="pl">Net Income (Loss) Attributable to Parent, Total</td><td class="nump">201,374</td><td class="nump">185,266</td><td class="nump">194,059</td></tr>
<tr><td class="pl">Basic (usd per share)</td><td class="nump">5.34</td><td class="nump">4.86</td><td class="nump">5.09</td></tr>
</table>"""

# Standalone OCI sibling: starts AT net income, no revenue/expense lines above.
_EFSCP_OCI = b"""<table class="report">
<tr><th class="tl">Consolidated Statements of Comprehensive Income - USD ($) $ in Thousands</th><th class="th" colspan="3">12 Months Ended</th></tr>
<tr><th class="th">Dec. 31, 2025</th><th class="th">Dec. 31, 2024</th><th class="th">Dec. 31, 2023</th></tr>
<tr><td class="pl">Net Income (Loss) Attributable to Parent, Total</td><td class="nump">201,374</td><td class="nump">185,266</td><td class="nump">194,059</td></tr>
<tr><td class="pl">Other Comprehensive Income (Loss), Net of Tax</td><td class="nump">62,131</td><td class="nump">(13,000)</td><td class="nump">9,000</td></tr>
<tr><td class="pl">Comprehensive Income (Loss), Net of Tax, Attributable to Parent</td><td class="nump">263,505</td><td class="nump">172,266</td><td class="nump">203,059</td></tr>
</table>"""

# Parent-only "Statements of Operations" sibling (Schedule II): carries the
# "Equity in undistributed net income of subsidiaries" tell.
_EFSCP_PARENT_OPS = b"""<table class="report">
<tr><th class="tl">Parent Company Only Condensed Financial Statements - Condensed Statements of Operations (Details) - USD ($) $ in Thousands</th><th class="th" colspan="3">12 Months Ended</th></tr>
<tr><th class="th">Dec. 31, 2025</th><th class="th">Dec. 31, 2024</th><th class="th">Dec. 31, 2023</th></tr>
<tr><td class="pl">Dividends from subsidiaries</td><td class="nump">120,000</td><td class="nump">110,000</td><td class="nump">100,000</td></tr>
<tr><td class="pl">Equity in undistributed net income of subsidiaries</td><td class="nump">81,374</td><td class="nump">75,266</td><td class="nump">94,059</td></tr>
<tr><td class="pl">Net Income (Loss) Attributable to Parent, Total</td><td class="nump">201,374</td><td class="nump">185,266</td><td class="nump">194,059</td></tr>
</table>"""

_EFSCP_BALANCE = b"""<table class="report">
<tr><th class="tl">Consolidated Balance Sheets - USD ($) $ in Thousands</th><th class="th" colspan="2"> </th></tr>
<tr><th class="th">Dec. 31, 2025</th><th class="th">Dec. 31, 2024</th></tr>
<tr><td class="pl">Cash and Due from Banks</td><td class="nump">208,080</td><td class="nump">270,975</td></tr>
<tr><td class="pl">Financing Receivable, Excluding Accrued Interest, after Allowance for Credit Loss, Total</td><td class="nump">11,660,316</td><td class="nump">11,082,405</td></tr>
<tr><td class="pl">Goodwill</td><td class="nump">416,968</td><td class="nump">365,164</td></tr>
<tr><td class="pl">Assets, Total</td><td class="nump">17,300,884</td><td class="nump">15,596,431</td></tr>
<tr><td class="pl">Deposits, Total</td><td class="nump">14,609,342</td><td class="nump">13,146,492</td></tr>
<tr><td class="pl">Liabilities, Total</td><td class="nump">15,261,498</td><td class="nump">13,772,429</td></tr>
<tr><td class="pl">Equity, Attributable to Parent, Total</td><td class="nump">2,039,386</td><td class="nump">1,824,002</td></tr>
<tr><td class="pl">Liabilities and Equity, Total</td><td class="nump">17,300,884</td><td class="nump">15,596,431</td></tr>
</table>"""

# Fair-value footnote sibling that title-matches "balance sheets": a list of
# instruments with carrying amounts, no Total-assets / liabilities+equity total.
_EFSCP_FV_NOTE = b"""<table class="report">
<tr><th class="tl">Fair Value Measurements - Summary of Carrying Amount and Fair Values of Financial Instruments Reported on the Balance Sheets (Details) - USD ($) $ in Thousands</th><th class="th" colspan="2"> </th></tr>
<tr><th class="th">Dec. 31, 2025</th><th class="th">Dec. 31, 2024</th></tr>
<tr><td class="pl">Held-to-maturity securities, carrying amount</td><td class="nump">1,074,957</td><td class="nump">928,935</td></tr>
<tr><td class="pl">Loans, carrying amount</td><td class="nump">11,660,316</td><td class="nump">11,082,405</td></tr>
</table>"""


class TestEfscpStandardLabelStatements(unittest.TestCase):
    """EFSCP root cause: primary income/balance carry us-gaap STANDARD labels and
    sit beside OCI / parent-only / footnote siblings. The content guards must
    recognize the standard-label bottom lines, and the selector must pick the real
    consolidated statement — never None, never the wrong sibling."""

    def _summary(self, pairs):
        rows = b"".join(
            b"<Report><ShortName>" + s + b"</ShortName>"
            b"<HtmlFileName>" + f + b"</HtmlFileName></Report>"
            for s, f in pairs)
        return (b'<?xml version="1.0"?><FilingSummary><MyReports>'
                + rows + b'</MyReports></FilingSummary>')

    def test_standard_label_net_income_is_income_body(self):
        # "Net Income (Loss) Attributable to Parent, Total" + standard-label income
        # lines above it ("Interest Income, Securities…", "Interest Expense…") must
        # be recognized as a real income body.
        from data.sec_statements import _is_income_body, parse_rfile
        self.assertTrue(_is_income_body(parse_rfile(_EFSCP_INCOME)))

    def test_standalone_oci_sibling_rejected(self):
        # The "Statements of Comprehensive Income" sibling (no income lines above
        # net income) must be rejected — never rendered as the income statement.
        from data.sec_statements import _is_income_body, parse_rfile
        self.assertFalse(_is_income_body(parse_rfile(_EFSCP_OCI)))

    def test_parent_only_operations_sibling_rejected(self):
        # The parent-only "Statements of Operations" Schedule II must be rejected.
        from data.sec_statements import _is_income_body, parse_rfile
        self.assertFalse(_is_income_body(parse_rfile(_EFSCP_PARENT_OPS)))

    def test_income_selector_picks_real_income_over_oci_and_parent_only(self):
        import data.sec_statements as s
        summary = self._summary([
            (b"Consolidated Statements of Income", b"R5.htm"),                       # real
            (b"Consolidated Statements of Comprehensive Income", b"R7.htm"),         # OCI-only
            (b"Parent Company Only Condensed Financial Statements - "
             b"Condensed Statements of Operations (Details)", b"R103.htm"),          # parent-only
        ])

        def fake_get(url):
            if url.endswith("FilingSummary.xml"):
                return summary
            if url.endswith("R5.htm"):
                return _EFSCP_INCOME
            if url.endswith("R7.htm"):
                return _EFSCP_OCI
            if url.endswith("R103.htm"):
                return _EFSCP_PARENT_OPS
            raise AssertionError(url)

        with mock.patch.object(s, "_get", side_effect=fake_get):
            fn, parsed = s._select_primary_rfile("base/", "income")
        self.assertEqual(fn, "R5.htm")
        self.assertIsNotNone(parsed)
        ni = next(r for r in parsed["rows"]
                  if not r["header"]
                  and r["label"] == "Net Income (Loss) Attributable to Parent, Total")
        # Net income ties EFSCP's FY2025 10-K (us-gaap NetIncomeLoss, $thousands).
        self.assertEqual(ni["values"][0], 201_374_000.0)

    def test_standard_label_assets_total_is_balance_body(self):
        # "Assets, Total" recognized as the balance total; A ≈ L + E ties via
        # "Liabilities and Equity, Total".
        from data.sec_statements import _is_balance_body, _balance_parse
        self.assertTrue(_is_balance_body(_balance_parse(_EFSCP_BALANCE)))

    def test_fair_value_footnote_rejected_as_balance(self):
        from data.sec_statements import _is_balance_body, parse_rfile
        self.assertFalse(_is_balance_body(parse_rfile(_EFSCP_FV_NOTE)))

    def test_balance_selector_picks_real_balance_over_footnote(self):
        import data.sec_statements as s
        summary = self._summary([
            (b"Consolidated Balance Sheets", b"R3.htm"),                              # real
            (b"Fair Value Measurements - Summary of Carrying Amount and Fair Values "
             b"of Financial Instruments Reported on the Balance Sheets (Details)",
             b"R101.htm"),                                                            # footnote
        ])

        def fake_get(url):
            if url.endswith("FilingSummary.xml"):
                return summary
            if url.endswith("R3.htm"):
                return _EFSCP_BALANCE
            if url.endswith("R101.htm"):
                return _EFSCP_FV_NOTE
            raise AssertionError(url)

        with mock.patch.object(s, "_get", side_effect=fake_get):
            fn, parsed = s._select_primary_rfile("base/", "balance")
        self.assertEqual(fn, "R3.htm")
        self.assertIsNotNone(parsed)
        ta = next(r for r in parsed["rows"]
                  if not r["header"] and r["label"] == "Assets, Total")
        # Total assets ties EFSCP's FY2025 10-K (us-gaap Assets, $thousands).
        self.assertEqual(ta["values"][0], 17_300_884_000.0)


if __name__ == "__main__":
    unittest.main()
