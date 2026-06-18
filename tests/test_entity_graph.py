"""Unit tests for data.entity_graph — as-of membership + reconstruction.

Lifespans and lineage are monkeypatched so the logic is tested without network;
a separate live ground-truth check (tools, not CI) confirms the real 2023 failures.
"""
import unittest
from datetime import date

import data.entity_graph as eg


class TestDateParse(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(eg._parse_fdic_date("03/10/2023"), date(2023, 3, 10))
        self.assertEqual(eg._parse_fdic_date("2023-03-10"), date(2023, 3, 10))
        self.assertEqual(eg._parse_fdic_date("12/31/9999"), eg._FAR_FUTURE)
        self.assertIsNone(eg._parse_fdic_date(""))
        self.assertIsNone(eg._parse_fdic_date(None))


class TestMembership(unittest.TestCase):
    def setUp(self):
        # cert → (established, ended|None)
        self._spans = {
            100: (date(2000, 1, 1), None),            # current public bank
            200: (date(2024, 6, 1), None),            # chartered AFTER 2023
            300: (date(1990, 1, 1), date(2024, 3, 1)),  # absorbed by 100 in 2024
            24735: (date(1983, 10, 17), date(2023, 3, 10)),  # SVB failed 2023-03
        }
        self._orig_span = eg.cert_lifespan
        self._orig_pred = eg.predecessors
        eg.cert_lifespan = lambda c: self._spans.get(int(c), (None, None))
        eg.predecessors = lambda c: [300] if int(c) == 100 else []

    def tearDown(self):
        eg.cert_lifespan = self._orig_span
        eg.predecessors = self._orig_pred

    def test_was_open(self):
        self.assertTrue(eg.was_open(24735, "2023-01-01"))    # alive before failure
        self.assertFalse(eg.was_open(24735, "2023-06-01"))   # after failure
        self.assertFalse(eg.was_open(24735, "1980-01-01"))   # before charter
        self.assertTrue(eg.was_open(100, "2023-01-01"))      # active, long-open
        self.assertFalse(eg.was_open(200, "2023-01-01"))     # chartered later

    def test_reconstruct_q1_2023(self):
        uni = eg.public_universe_as_of([100, 200], "2023-01-01")
        # 100 (base, open), 24735 (failure, open), 300 (lineage, absorbed 2024 so
        # open at Q). 200 chartered 2024 → excluded.
        self.assertIn(100, uni)
        self.assertIn(24735, uni)
        self.assertIn(300, uni)
        self.assertNotIn(200, uni)
        self.assertEqual(uni[100]["source"], "base")
        self.assertEqual(uni[24735]["source"], "failure")
        self.assertEqual(uni[300]["source"], "lineage")

    def test_failure_excluded_after_it_fails(self):
        uni = eg.public_universe_as_of([100], "2023-12-31")
        self.assertNotIn(24735, uni)   # SVB gone by year-end 2023
        self.assertIn(100, uni)

    def test_lineage_predecessor_excluded_if_already_gone(self):
        # If 300 had been absorbed BEFORE Q, it isn't in the as-of universe.
        self._spans[300] = (date(1990, 1, 1), date(2021, 1, 1))
        uni = eg.public_universe_as_of([100], "2023-01-01")
        self.assertNotIn(300, uni)


if __name__ == "__main__":
    unittest.main()
