"""Tests for the IR earnings-release locator (data/ir_provider.py, increment 5a).

Pin the pure selection logic — finding the latest Item-2.02 8-K and choosing its
EX-99.1 exhibit — against fixtures. Network I/O (latest_earnings_release) is a
thin wrapper over these and isn't exercised here.
"""
import unittest

from data.ir_provider import (
    _latest_earnings_8k, _pick_ex99, _parse_index_html, _dash_accession,
)


def _subs(forms, items, dates, accs, primaries=None):
    return {"filings": {"recent": {
        "form": forms, "items": items, "filingDate": dates,
        "accessionNumber": accs,
        "primaryDocument": primaries or ["d.htm"] * len(forms),
    }}}


class TestLatestEarnings8K(unittest.TestCase):
    def test_picks_first_8k_with_item_202_newest_first(self):
        subs = _subs(
            forms=["8-K", "8-K", "10-Q"],
            items=["7.01", "2.02,9.01", "2.02"],   # newest first; 2nd is the earnings 8-K
            dates=["2026-02-01", "2026-01-22", "2025-12-31"],
            accs=["0000000000-26-000002", "0000000000-26-000001", "x"],
        )
        got = _latest_earnings_8k(subs)
        self.assertEqual(got["accession"], "000000000026000001")
        self.assertEqual(got["filed_date"], "2026-01-22")

    def test_skips_8k_without_202(self):
        subs = _subs(["8-K"], ["7.01,9.01"], ["2026-01-22"], ["a-1"])
        self.assertIsNone(_latest_earnings_8k(subs))

    def test_ignores_10q_even_if_listed(self):
        # A 10-Q is not an 8-K; the 2.02 item filter must not match it.
        subs = _subs(["10-Q"], ["2.02"], ["2026-01-22"], ["a-1"])
        self.assertIsNone(_latest_earnings_8k(subs))

    def test_semicolon_separated_items(self):
        subs = _subs(["8-K"], ["2.02; 9.01"], ["2026-01-22"], ["a-1"])
        self.assertIsNotNone(_latest_earnings_8k(subs))

    def test_8k_amendment_counts(self):
        subs = _subs(["8-K/A"], ["2.02"], ["2026-01-22"], ["a-1"])
        self.assertIsNotNone(_latest_earnings_8k(subs))

    def test_no_filings_returns_none(self):
        self.assertIsNone(_latest_earnings_8k({}))

    def test_strips_dashes_from_accession(self):
        subs = _subs(["8-K"], ["2.02"], ["2026-01-22"], ["0001193125-26-000123"])
        self.assertEqual(_latest_earnings_8k(subs)["accession"], "000119312526000123")


class TestPickEx99(unittest.TestCase):
    def test_prefers_ex99_1_over_ex99_2(self):
        items = [
            {"name": "d8k.htm", "type": "8-K"},
            {"name": "slides.htm", "type": "EX-99.2"},
            {"name": "press.htm", "type": "EX-99.1"},
        ]
        self.assertEqual(_pick_ex99(items), "press.htm")

    def test_lowest_ex99_when_no_dot1(self):
        items = [{"name": "a.htm", "type": "EX-99.3"},
                 {"name": "b.htm", "type": "EX-99.2"}]
        self.assertEqual(_pick_ex99(items), "b.htm")

    def test_filename_fallback_when_types_missing(self):
        items = [{"name": "d8k.htm", "type": ""},
                 {"name": "dex991.htm", "type": ""}]
        self.assertEqual(_pick_ex99(items), "dex991.htm")

    def test_none_when_no_ex99(self):
        items = [{"name": "d8k.htm", "type": "8-K"},
                 {"name": "g.jpg", "type": "GRAPHIC"}]
        self.assertIsNone(_pick_ex99(items))

    def test_handles_ex99_without_dash(self):
        items = [{"name": "p.htm", "type": "EX99.1"}]
        self.assertEqual(_pick_ex99(items), "p.htm")

    def test_empty_list(self):
        self.assertIsNone(_pick_ex99([]))


class TestParseIndexHtml(unittest.TestCase):
    # A trimmed real JPMorgan -index.htm document table: the press release
    # (EX-99.1) and the data supplement (EX-99.2). The picker must prefer .1.
    _JPM_INDEX = '''
    <table class="tableFile" summary="Document Format Files">
    <tr><td>4</td><td>Press Release</td>
      <td><a href="/Archives/edgar/data/19617/000162828026024990/a1q26erfexhibit991narrative.htm">a1q26erfexhibit991narrative.htm</a></td>
      <td>EX-99.1</td><td>123456</td></tr>
    <tr><td>5</td><td>Financial Supplement</td>
      <td><a href="/Archives/edgar/data/19617/000162828026024990/a1q26erfex992supplement.htm">a1q26erfex992supplement.htm</a></td>
      <td>EX-99.2</td><td>654321</td></tr>
    </table>'''

    def test_extracts_ex99_rows_with_types(self):
        got = _parse_index_html(self._JPM_INDEX)
        names = {d["name"]: d["type"] for d in got}
        self.assertEqual(names["a1q26erfexhibit991narrative.htm"], "EX-99.1")
        self.assertEqual(names["a1q26erfex992supplement.htm"], "EX-99.2")

    def test_picker_selects_the_press_release_not_the_supplement(self):
        # The end-to-end fix: parse the index table, then pick EX-99.1.
        self.assertEqual(
            _pick_ex99(_parse_index_html(self._JPM_INDEX)),
            "a1q26erfexhibit991narrative.htm")

    def test_no_ex99_rows_returns_empty(self):
        html = '<tr><td><a href="/x/jpm-10q.htm">jpm-10q.htm</a></td><td>10-Q</td></tr>'
        self.assertEqual(_parse_index_html(html), [])


class TestDashAccession(unittest.TestCase):
    def test_inserts_dashes(self):
        self.assertEqual(_dash_accession("000162828026024990"), "0001628280-26-024990")

    def test_short_input_unchanged(self):
        self.assertEqual(_dash_accession("123"), "123")


if __name__ == "__main__":
    unittest.main()
