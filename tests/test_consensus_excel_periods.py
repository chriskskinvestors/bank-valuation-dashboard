"""Pin parse_consensus_excel_periods (data/consensus.py, 2026-07-09).

The upload gap: broker models are METRIC ROWS × PERIOD COLUMNS, but the Excel
path saved everything under one hand-typed period (and Strategy 2 silently
read only the first value column). These tests pin: period-column detection
across broker notations (2Q26E / Q2'26 / FY26 / dates), header-row FINDING
(title rows above), $/%/parens value cleanup, canonical period grouping, and
the no-period-columns fallback contract (empty periods, no error).

Run: python -m unittest tests.test_consensus_excel_periods
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.consensus import parse_consensus_excel_periods


def parse_csv(text: str) -> dict:
    return parse_consensus_excel_periods(text.encode(), "model.csv")


class TestPeriodGrid(unittest.TestCase):
    def test_quarters_and_fy_columns_all_extracted(self):
        out = parse_csv(
            "Metric,2Q26E,3Q26E,FY26E,FY27E\n"
            "EPS,1.15,1.22,4.80,5.40\n"
            "NIM,3.42,3.45,3.44,3.50\n")
        self.assertIsNone(out["error"])
        by = {p["period"]: p["metrics"] for p in out["periods"]}
        self.assertEqual(sorted(by), ["2026", "2026Q2", "2026Q3", "2027"])
        q2 = {m["name"]: m["value"] for m in by["2026Q2"]}
        self.assertEqual(q2, {"EPS": 1.15, "NIM": 3.42})
        self.assertEqual({m["name"]: m["value"] for m in by["2027"]},
                         {"EPS": 5.4, "NIM": 3.5})

    def test_title_rows_above_header_are_skipped(self):
        out = parse_csv(
            "Southern First Bancshares — Earnings Model,,,\n"
            "Brean Capital Research,,,\n"
            "Line Item,2026Q2,2026Q3,2026Q4\n"
            "EPS,1.15,1.22,1.31\n")
        by = {p["period"]: p["metrics"] for p in out["periods"]}
        self.assertEqual(sorted(by), ["2026Q2", "2026Q3", "2026Q4"])

    def test_dollar_percent_and_paren_values_clean(self):
        out = parse_csv(
            "Metric,2026Q2,2026Q3\n"
            "Net Income,\"$12,345\",\"$13,050\"\n"
            "Efficiency,55.3%,(54.1%)\n")
        by = {p["period"]: {m["name"]: m["value"] for m in p["metrics"]}
              for p in out["periods"]}
        self.assertEqual(by["2026Q2"]["Net Income"], 12345.0)
        self.assertEqual(by["2026Q3"]["Efficiency"], -54.1)

    def test_date_form_headers_map_to_quarters(self):
        out = parse_csv(
            "Metric,6/30/2026,9/30/2026\n"
            "EPS,1.15,1.22\n")
        self.assertEqual(sorted(p["period"] for p in out["periods"]),
                         ["2026Q2", "2026Q3"])

    def test_blank_cells_skipped_not_fabricated(self):
        out = parse_csv(
            "Metric,2026Q2,2026Q3\n"
            "EPS,1.15,\n"
            "NIM,,3.45\n")
        by = {p["period"]: {m["name"] for m in p["metrics"]}
              for p in out["periods"]}
        self.assertEqual(by["2026Q2"], {"EPS"})
        self.assertEqual(by["2026Q3"], {"NIM"})

    def test_no_period_columns_falls_back_empty(self):
        out = parse_csv("Metric,Estimate\nEPS,1.15\nNIM,3.42\n")
        self.assertEqual(out["periods"], [])
        self.assertIsNone(out["error"])

    def test_single_period_column_not_treated_as_grid(self):
        # One period column (<2) is ambiguous with a stray year cell — fall back.
        out = parse_csv("Metric,2026Q2\nEPS,1.15\n")
        self.assertEqual(out["periods"], [])

    def test_unparseable_bytes_error_not_crash(self):
        out = parse_consensus_excel_periods(b"\x00\x01\x02", "model.xlsx")
        self.assertEqual(out["periods"], [])
        self.assertIsNotNone(out["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestDateHeaderAnnualVsQuarterly(unittest.TestCase):
    """AUDIT-2026-07-27 P3: every date header was mapped to the quarter it falls
    in, so a research model whose ANNUAL columns are headed by fiscal-year-end
    dates ingested FY EPS as that year's Q4 quarterly estimate — re-opening the
    x4 class AUDIT-2026-07-02 #6/#7 closed, via the upload path. The header ROW
    disambiguates: quarters step ~3 months, annual columns repeat one month
    across consecutive years."""

    def test_fiscal_year_end_dated_columns_are_annual(self):
        out = parse_csv(
            "Metric,12/31/2025,12/31/2026,12/31/2027\n"
            "EPS,9.80,10.40,11.10\n")
        self.assertIsNone(out["error"])
        by = {p["period"]: p["metrics"] for p in out["periods"]}
        self.assertEqual(sorted(by), ["2025", "2026", "2027"])
        self.assertEqual({m["name"]: m["value"] for m in by["2026"]},
                         {"EPS": 10.4})

    def test_iso_year_end_dates_are_annual_too(self):
        out = parse_csv(
            "Metric,2026-12-31,2027-12-31\n"
            "EPS,10.40,11.10\n")
        self.assertEqual(sorted(p["period"] for p in out["periods"]),
                         ["2026", "2027"])

    def test_june_fiscal_year_end_is_annual(self):
        """Annual-ness comes from the SPACING, not from December."""
        out = parse_csv(
            "Metric,6/30/2026,6/30/2027\n"
            "EPS,3.10,3.40\n")
        self.assertEqual(sorted(p["period"] for p in out["periods"]),
                         ["2026", "2027"])

    def test_quarterly_dates_stay_quarterly(self):
        out = parse_csv(
            "Metric,3/31/2026,6/30/2026,9/30/2026,12/31/2026\n"
            "EPS,1.10,1.15,1.22,1.31\n")
        self.assertEqual(sorted(p["period"] for p in out["periods"]),
                         ["2026Q1", "2026Q2", "2026Q3", "2026Q4"])

    def test_repeated_year_stays_quarterly(self):
        """Same month AND a repeated year is not an annual series."""
        out = parse_csv(
            "Metric,12/31/2026,12/31/2026 ,2026Q3\n"
            "EPS,10.40,10.40,1.22\n")
        self.assertIn("2026Q4", [p["period"] for p in out["periods"]])
