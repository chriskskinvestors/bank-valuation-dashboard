"""Unit tests for data.as_of_metrics pure helpers (quarter labels + picker list).

The metric build itself is network-bound and is ground-truth-verified live
(SVB cert 24735 as of 2022-12-31 → total_assets $209,026,000,000).
"""
import unittest
import pandas as pd

from data.as_of_metrics import quarter_label, recent_quarter_ends


class TestAsOfHelpers(unittest.TestCase):
    def test_quarter_label(self):
        self.assertEqual(quarter_label("2022-12-31"), "Q4 2022")
        self.assertEqual(quarter_label("2023-03-31"), "Q1 2023")
        self.assertEqual(quarter_label("2023-06-30"), "Q2 2023")
        self.assertEqual(quarter_label("2024-09-30"), "Q3 2024")

    def test_recent_quarter_ends_descending_quarter_ends(self):
        qs = recent_quarter_ends(8)
        self.assertEqual(len(qs), 8)
        # strictly descending
        self.assertTrue(all(qs[i] > qs[i + 1] for i in range(len(qs) - 1)))
        # each is a genuine quarter-end (Mar/Jun/Sep/Dec, last day of month)
        for q in qs:
            self.assertIn(q.month, (3, 6, 9, 12))
            self.assertEqual(q, q + pd.offsets.QuarterEnd(startingMonth=12) * 0)
            self.assertEqual((q + pd.Timedelta(days=1)).day, 1)  # last day of month
        # newest is a completed quarter (strictly before today)
        self.assertLess(qs[0], pd.Timestamp.today().normalize())


if __name__ == "__main__":
    unittest.main()
