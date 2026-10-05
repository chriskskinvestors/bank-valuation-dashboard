"""REVIEW 2026-10-05 P1-11: for a multi-charter group the FDIC statement
columns are the charters SUMMED, but the FFIEC detail store (Schedule RI
tax-exempt income, RI-E itemizations, the RI 2.a / RC-K deposit-cost split)
is per charter and was read for the LEAD charter only — WTFC's 1-of-16
charter figures sat beside group totals. Those rows are now n/a with the
reason; a single-charter bank is unchanged.

Run: python -m unittest tests.test_group_lead_charter_detail
"""
import unittest
from unittest import mock

import tests.test_statement_units_p2 as U
from tests.test_statement_units_p2 import _render, _row_cells

def _render_group(charters):
    T = U.TestFteRowsNotIngestedReason
    with mock.patch("data.cert_group.get_cert_group_cached",
                    lambda c: list(charters)), \
            mock.patch.object(U.FS, "entity_note", lambda t: "group"):
        return _render(T.SPEC, T.HIST, "Quarterly", T.RI, T.FLOWS)[0]


class TestGroupDetailIsNa(unittest.TestCase):
    def test_group_never_shows_the_lead_charters_detail(self):
        html = _render_group([4242, 9999])
        # Q1 '26 IS ingested for the lead cert (single-charter: $1.3M) —
        # for a 2-charter group it is n/a, with the group reason.
        for label in ("FTE adjustment", "Net interest income (FTE)"):
            tag, text = _row_cells(html, label)[1]
            self.assertEqual(text, "—", label)
            self.assertIn("data-cid", tag, label)
        self.assertIn("not combined across this bank", html)
        self.assertIn("2 charters", html)
        self.assertNotIn("$1.3M", html)

    def test_single_charter_unchanged(self):
        html = _render_group([4242])
        self.assertEqual(_row_cells(html, "FTE adjustment")[1][1], "$1.3M")
        self.assertNotIn("not combined across", html)


if __name__ == "__main__":
    unittest.main()
