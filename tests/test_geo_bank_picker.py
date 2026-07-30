"""
Geographic → By Bank(s) crashed the whole tab in production (2026-07-28):

  streamlit.errors.StreamlitAPIException: The default value 'nan' is not part of
  the options.

get_branch_counts_by_ticker GROUPs BY ticker over the branches table, so every
branch with no ticker mapping collapses into ONE row with a NULL ticker — and
because that row's deposits are the SUM across all of them, it sorts near the top
of the deposits-ordered frame. The multiselect built `options` with .dropna() but
`default` with a plain .head(5), so the NaN reached the default and Streamlit
raised instead of skipping it.

Pins:
  1. a NaN/None ticker never reaches the default (the crash);
  2. the default is always a SUBSET of options (the general invariant);
  3. deposit ordering is preserved and a full n picks is still returned when
     unmapped rows sit at the top (filter before the slice, not after);
  4. duplicates and short frames behave sanely.

Pure-function tests, no Streamlit runtime and no DB.

Run: python -m unittest tests.test_geo_bank_picker
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

import pandas as pd  # noqa: E402

from ui.geo_view import _default_bank_picks  # noqa: E402


def _coverage(tickers):
    """Deposits-descending coverage frame, mirroring the real query's ordering."""
    return pd.DataFrame({
        "ticker": tickers,
        "n_branches": list(range(len(tickers), 0, -1)),
        "total_deposits": [1_000_000 - i * 1000 for i in range(len(tickers))],
    })


def _options(cov):
    """Exactly how the render builds options."""
    return sorted(cov["ticker"].dropna().unique().tolist())


class TestNaNNeverReachesDefault(unittest.TestCase):
    def test_null_ticker_row_ranked_first_is_excluded(self):
        # The production shape: the unmapped-branch aggregate outranks every bank.
        cov = _coverage([None, "JPM", "BAC", "WFC", "C", "USB", "PNC"])
        opts = _options(cov)
        picks = _default_bank_picks(cov, opts)
        self.assertNotIn(None, picks)
        self.assertFalse(any(pd.isna(p) for p in picks))
        # …and we still get a full five, taken in deposit order.
        self.assertEqual(picks, ["JPM", "BAC", "WFC", "C", "USB"])

    def test_float_nan_also_excluded(self):
        cov = _coverage([float("nan"), "JPM", "BAC"])
        picks = _default_bank_picks(cov, _options(cov))
        self.assertEqual(picks, ["JPM", "BAC"])

    def test_multiple_unmapped_rows_interleaved(self):
        cov = _coverage(["JPM", None, "BAC", float("nan"), "WFC", "C", "USB"])
        picks = _default_bank_picks(cov, _options(cov))
        self.assertEqual(picks, ["JPM", "BAC", "WFC", "C", "USB"])


class TestDefaultIsAlwaysASubsetOfOptions(unittest.TestCase):
    """The invariant whose violation is a hard StreamlitAPIException."""

    def test_subset_holds_across_shapes(self):
        shapes = [
            [None, "JPM", "BAC", "WFC", "C", "USB"],
            ["JPM"],
            [None],
            [float("nan"), None],
            ["JPM", "JPM", "BAC"],
            ["A", "B", "C", "D", "E", "F", "G", "H"],
        ]
        for tickers in shapes:
            cov = _coverage(tickers)
            opts = _options(cov)
            picks = _default_bank_picks(cov, opts)
            self.assertTrue(set(picks).issubset(set(opts)),
                            f"default escaped options for {tickers!r}")
            self.assertLessEqual(len(picks), 5)

    def test_all_unmapped_yields_empty_default_not_a_crash(self):
        cov = _coverage([None, float("nan")])
        picks = _default_bank_picks(cov, _options(cov))
        self.assertEqual(picks, [])          # renders the "pick one" hint


class TestOrderingAndDedup(unittest.TestCase):
    def test_deposit_order_preserved(self):
        cov = _coverage(["AAA", "BBB", "CCC"])
        self.assertEqual(_default_bank_picks(cov, _options(cov)), ["AAA", "BBB", "CCC"])

    def test_duplicate_tickers_counted_once(self):
        cov = _coverage(["AAA", "AAA", "BBB", "CCC", "DDD", "EEE", "FFF"])
        picks = _default_bank_picks(cov, _options(cov))
        self.assertEqual(len(picks), len(set(picks)))
        self.assertEqual(picks, ["AAA", "BBB", "CCC", "DDD", "EEE"])

    def test_fewer_rows_than_n(self):
        cov = _coverage(["AAA", "BBB"])
        self.assertEqual(_default_bank_picks(cov, _options(cov)), ["AAA", "BBB"])

    def test_n_is_honoured(self):
        cov = _coverage(["A", "B", "C", "D", "E", "F"])
        self.assertEqual(_default_bank_picks(cov, _options(cov), n=2), ["A", "B"])


if __name__ == "__main__":
    unittest.main()
