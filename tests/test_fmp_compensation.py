"""data.fmp_compensation — parsing + de-dup of the FMP exec-comp endpoint.

The HTTP layer and key check are monkeypatched (no network). Verifies the
de-dup keeps the most-recently-FILED record per (executive, year), numeric
mapping, and newest-year-first ordering.
"""
import unittest

import data.fmp_compensation as comp


class _Patch(unittest.TestCase):
    def setUp(self):
        self._g, self._h, self._cg, self._cp = (
            comp._get, comp._has_key, comp._cache_get, comp._cache_put)
        comp._has_key = lambda: True
        comp._cache_get = lambda k, ttl: None
        comp._cache_put = lambda k, v: None

    def tearDown(self):
        comp._get, comp._has_key, comp._cache_get, comp._cache_put = (
            self._g, self._h, self._cg, self._cp)


def _row(name, year, total, filed, salary=1000000):
    return {"nameAndPosition": name, "year": year, "total": total,
            "filingDate": filed, "salary": salary, "bonus": 0, "stockAward": 0,
            "optionAward": 0, "incentivePlanCompensation": total - salary,
            "allOtherCompensation": 0,
            "link": f"https://sec.gov/{filed}"}


class TestExecComp(_Patch):
    def test_dedup_keeps_latest_filing(self):
        # Same (exec, FY2023) reported in two proxies; the 2025 filing supersedes.
        comp._get = lambda p, params: [
            _row("James Dimon CEO", 2023, 36000000, "2024-04-08"),
            _row("James Dimon CEO", 2023, 36000000, "2025-04-07"),
        ]
        out = comp.get_executive_compensation("JPM")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["filing_date"], "2025-04-07")
        self.assertEqual(out[0]["year"], 2023)
        self.assertEqual(out[0]["total"], 36000000)
        self.assertNotIn("_filed", out[0])  # scratch key stripped

    def test_sorted_newest_year_then_total(self):
        comp._get = lambda p, params: [
            _row("Exec A", 2023, 10_000_000, "2024-04-01"),
            _row("Exec B", 2024, 40_000_000, "2025-04-01"),
            _row("Exec C", 2024, 20_000_000, "2025-04-01"),
        ]
        out = comp.get_executive_compensation("JPM")
        self.assertEqual([(r["year"], r["total"]) for r in out],
                         [(2024, 40_000_000), (2024, 20_000_000), (2023, 10_000_000)])

    def test_numeric_components_mapped(self):
        comp._get = lambda p, params: [_row("Exec A", 2024, 5_000_000, "2025-04-01",
                                            salary=1_500_000)]
        r = comp.get_executive_compensation("JPM")[0]
        self.assertEqual(r["salary"], 1_500_000)
        self.assertEqual(r["incentive"], 3_500_000)
        self.assertEqual(r["link"], "https://sec.gov/2025-04-01")

    def test_empty_on_non_list(self):
        comp._get = lambda p, params: {"Error Message": "no"}
        self.assertEqual(comp.get_executive_compensation("ABC"), [])

    def test_drops_rows_missing_name_or_year(self):
        comp._get = lambda p, params: [
            {"nameAndPosition": "", "year": 2024, "total": 1},
            {"nameAndPosition": "X", "year": None, "total": 1},
            _row("Good Exec", 2024, 9_000_000, "2025-04-01"),
        ]
        out = comp.get_executive_compensation("ABC")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name_position"], "Good Exec")


if __name__ == "__main__":
    unittest.main()
