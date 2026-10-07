"""Company Reported Highlights/Performance never mix a reverse-merger's
legacy-entity columns into the bank's ratios (REVIEW-2026-09-24 P1-6
follow-up; data/sec_statements period_entity, PR #321): legacy columns are
dropped, and an average whose prior column is legacy is n/a.

Run: python -m unittest tests.test_cr_highlights_legacy
"""
import unittest
from unittest import mock

from tests import _streamlit_stub

_streamlit_stub.install()

LEGACY = "Berkshire Hills Bancorp (legacy)"


def _stmt(rows, periods, entity=None):
    s = {"periods": list(periods),
         "rows": [{"label": l, "header": False, "values": list(v)} for l, v in rows],
         "units_scale": 1, "title": "x"}
    if entity:
        s["period_entity"] = list(entity)
    return {"statement": s, "meta": {"cik": 1, "accession": "a", "doc": "d.htm",
                                     "date": "2026-02-01"}}


class TestLegacyColumnsExcluded(unittest.TestCase):
    def test_legacy_dropped_and_cross_entity_average_na(self):
        periods = ("Dec. 31, 2025", "Dec. 31, 2024", "Dec. 31, 2023")
        ent = [None, None, LEGACY]
        inc = _stmt([("Net interest income", (900.0, 800.0, 300.0)),
                     ("Net income", (200.0, 150.0, 60.0))], periods, ent)
        bal = _stmt([("Total assets", (24000.0, 22000.0, 12000.0)),
                     ("Total deposits", (19000.0, 18000.0, 9000.0)),
                     ("Total stockholders' equity", (2400.0, 2200.0, 1100.0))],
                    periods, ent)
        import ui.financials_statements as fs
        import data.sec_statements as ss
        with mock.patch.object(fs, "get_bank_info", return_value={"cik": 1, "name": "T"}), \
             mock.patch.object(ss, "as_reported_statement_multiyear",
                               side_effect=lambda cik, st, n: inc if st == "income" else bal), \
             mock.patch("data.sec_filing_scraper.holdco_capital_for", return_value=None), \
             mock.patch("data.sec_filing_scraper.company_asset_quality_nim", return_value=None), \
             mock.patch("data.bank_mapping.get_fdic_cert", return_value=None):
            years, dicts, _src = fs._cr_highlights_by_year("BBT")
        self.assertEqual(years, ["FY2025", "FY2024"])            # FY2023 legacy dropped
        # FY2025 averages FY2025 and FY2024 (same entity): 200 / 23,000.
        self.assertAlmostEqual(dicts[0]["roaa"], 200.0 / 23000.0, places=9)
        # FY2024's prior is the legacy FY2023 → n/a, never 150 / 17,000.
        self.assertIsNone(dicts[1]["roaa"])
        self.assertEqual(dicts[1]["total_assets"], 22000.0)       # levels still shown


if __name__ == "__main__":
    unittest.main()
