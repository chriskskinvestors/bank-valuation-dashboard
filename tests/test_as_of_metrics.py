"""Unit tests for data.as_of_metrics pure helpers (quarter labels + picker list).

The metric build itself is network-bound and is ground-truth-verified live
(SVB cert 24735 as of 2022-12-31 → total_assets $209,026,000,000).
"""
import unittest
from unittest import mock

import pandas as pd

from tests import _streamlit_stub

_streamlit_stub.install()

import data.as_of_metrics as aom  # noqa: E402
import data.fdic_client as fc  # noqa: E402
from data.as_of_metrics import quarter_label, recent_quarter_ends  # noqa: E402
from tests.test_cache_read_ceilings import _IsolatedCache  # noqa: E402


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


class TestAsOfIsFdicOnly(_IsolatedCache):
    """2026-10-08: as_of_quarter_metrics passed the TICKER to the engine, which
    then pulled today's SEC capital return and 8-K book value into the as-of
    row (prod: BAC "as of Q4 2021" TBVPS 29.37 = its 2026 figure; actual
    ~21.68) and cost ~6s/bank — the universe build never finished inside the
    request timeout. The engine must get ticker=None; the row keeps the id."""

    def test_engine_gets_no_ticker_and_row_keeps_id(self):
        calls = []

        def engine(ticker, fdic, sec, price, hist):
            calls.append((ticker, sec, price))
            return {"total_assets": 1.0}
        with mock.patch.object(fc, "fetch_quarter_financials",
                               return_value={3510: {"CERT": 3510}}), \
             mock.patch("analysis.metrics.build_bank_metrics", side_effect=engine):
            rows = aom.as_of_quarter_metrics("2021-12-31", {3510: "BAC"}, window=2)
        self.assertEqual(calls, [(None, {}, {})])
        self.assertEqual(rows[0]["ticker"], "BAC")
        self.assertEqual(rows[0]["_fdic_cert"], 3510)


if __name__ == "__main__":
    unittest.main()
