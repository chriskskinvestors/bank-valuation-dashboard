"""Peer Comparison exports on ui/export.py (owner directive 2026-09-22).

Pins Phase-1 defect D1 at its origin: the old `_compare_export_bytes` wrote
2,100,000,000 under a "Mkt Cap ($B)" header. Now the header says "($)" and
the cell holds whole dollars with a $#,##0 format; pct columns carry the
literal-% format on percent-unit values; absent metrics are "n/a"; the
sheet keeps its AutoFilter and frozen Ticker/Bank columns.
"""
import io
import unittest

from openpyxl import load_workbook

import ui.peer_comparison as pc


def _rows(ws, n=None):
    return [[c.value for c in r] for r in ws.iter_rows(min_row=1, max_row=n or ws.max_row)]


class TestCompareExport(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._saved = pc.get_name
        pc.get_name = lambda t: f"{t} Bancorp"
        cohort = [
            {"ticker": "BANR", "pe_ratio": 10.2, "market_cap": 2_100_000_000.0,
             "nim": 3.41, "efficiency_ratio": 61.2, "npl_ratio": 0.31,
             "total_assets": 16_200_000_000.0},
            {"ticker": "EWBC", "pe_ratio": 9.1, "market_cap": 13_400_000_000.0,
             "nim": 3.25, "efficiency_ratio": None, "npl_ratio": 0.55,
             "total_assets": 74_500_000_000.0},
        ]
        cats = [c for c in pc.CATEGORY_METRICS
                if any(k in cohort[0] for k in pc.CATEGORY_METRICS[c])]
        cls.cats = cats
        data = pc._compare_export_bytes(cohort, cats, provenance={"Scope": "All banks"})
        cls.wb = load_workbook(io.BytesIO(data))
        cls.ws = cls.wb["Peer comparison"]

    @classmethod
    def tearDownClass(cls):
        pc.get_name = cls._saved

    def _col(self, header):
        hdrs = [c.value for c in self.ws[1]]
        self.assertIn(header, hdrs)
        return hdrs.index(header) + 1

    def test_headers_are_honest_about_units(self):
        hdrs = [c.value for c in self.ws[1]]
        self.assertEqual(hdrs[:3], ["Ticker", "Bank", "Overall score"])
        self.assertIn("Mkt Cap ($)", hdrs)
        self.assertIn("Total Assets ($)", hdrs)
        self.assertFalse(any("($B)" in h or "($M)" in h for h in hdrs), hdrs)

    def test_dollar_cells_are_raw_dollars_with_dollar_format(self):
        c = self._col("Mkt Cap ($)")
        self.assertEqual(self.ws.cell(2, c).value, 2_100_000_000)
        self.assertEqual(self.ws.cell(2, c).number_format, "$#,##0")
        c = self._col("Total Assets ($)")
        self.assertEqual(self.ws.cell(3, c).value, 74_500_000_000)

    def test_pct_and_multiple_formats(self):
        c = self._col("P/E")
        self.assertEqual(self.ws.cell(2, c).value, 10.2)
        self.assertEqual(self.ws.cell(2, c).number_format, '0.00"x"')
        c = self._col("NIM")
        self.assertEqual(self.ws.cell(2, c).value, 3.41)
        self.assertIn('"%"', self.ws.cell(2, c).number_format)

    def test_missing_metric_is_na(self):
        c = self._col("Efficiency")
        self.assertEqual(self.ws.cell(2, c).value, 61.2)
        self.assertEqual(self.ws.cell(3, c).value, "n/a")

    def test_rows_and_score(self):
        self.assertEqual(self.ws.cell(2, 1).value, "BANR")
        self.assertEqual(self.ws.cell(2, 2).value, "BANR Bancorp")
        self.assertIsInstance(self.ws.cell(2, 3).value, int)
        self.assertEqual(self.ws.cell(2, 3).number_format, "#,##0")

    def test_sortable_sheet_structure(self):
        self.assertEqual(self.ws.freeze_panes, "C2")
        ncols = len([c for c in self.ws[1] if c.value is not None])
        self.assertEqual(self.ws.auto_filter.ref,
                         f"A1:{chr(ord('A') + ncols - 1)}3")

    def test_source_sheet(self):
        d = dict(_rows(self.wb["Source"]))
        self.assertEqual(d["Banks in cohort"], 2)
        self.assertEqual(d["Scope"], "All banks")
        self.assertEqual(d["Categories"], ", ".join(self.cats))
        self.assertIn("whole US dollars", d["Units"])
        self.assertIn("percent units", d["Units"])


if __name__ == "__main__":
    unittest.main()
