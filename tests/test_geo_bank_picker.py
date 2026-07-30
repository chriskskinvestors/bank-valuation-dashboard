"""
Geographic → By Bank(s): the bank picker.

Two things are pinned here.

1. THE CRASH (2026-07-28). The tab died with

     streamlit.errors.StreamlitAPIException: The default value 'nan' is not part
     of the options.

   The old coverage query GROUPed BY ticker, so every branch with no ticker
   collapsed into ONE row whose deposits were the SUM across all of them —
   ranking it near the top of the deposits-ordered frame. `options` was built
   with .dropna() but `default` with a plain .head(5), so the NaN reached the
   default and Streamlit raised. The invariant: default ⊆ options, always.

2. PRIVATE BANKS (2026-07-30, owner request). The picker is now keyed on CERT,
   not ticker: refresh_sod already stores SOD for every active FDIC institution
   (~4,500, ticker=None for the ~4,200 private ones), and the other three tabs
   already show them — only this tab excluded them, because a private bank has
   no ticker to select or query by. Pinned: private banks are selectable, two
   banks sharing a name stay distinct, and labels round-trip to the right cert.

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

from ui.geo_view import (_bank_option_label, _bank_options,  # noqa: E402
                         _default_bank_picks)


def _coverage(tickers, names=None, certs=None):
    """Deposits-descending frame, mirroring get_branch_counts_by_bank's ordering."""
    n = len(tickers)
    return pd.DataFrame({
        "cert": certs if certs is not None else list(range(1, n + 1)),
        "ticker": tickers,
        "bank_name": names if names is not None else [f"Bank {i}" for i in range(n)],
        "n_branches": list(range(n, 0, -1)),
        "total_deposits": [1_000_000 - i * 1000 for i in range(n)],
    })


class TestDefaultIsAlwaysASubsetOfOptions(unittest.TestCase):
    """The invariant whose violation is a hard StreamlitAPIException."""

    def test_nan_identifier_never_reaches_the_default(self):
        # The production shape: the unmapped aggregate outranked every real bank.
        ids = [None, "JPM", "BAC", "WFC", "C", "USB", "PNC"]
        opts = sorted(i for i in ids if i)
        picks = _default_bank_picks(ids, opts)
        self.assertNotIn(None, picks)
        self.assertFalse(any(pd.isna(p) for p in picks))
        self.assertEqual(picks, ["JPM", "BAC", "WFC", "C", "USB"])

    def test_float_nan_also_excluded(self):
        ids = [float("nan"), "JPM", "BAC"]
        picks = _default_bank_picks(ids, ["JPM", "BAC"])
        self.assertEqual(picks, ["JPM", "BAC"])

    def test_subset_holds_across_shapes(self):
        shapes = [
            [None, "JPM", "BAC", "WFC", "C", "USB"],
            ["JPM"],
            [None],
            [float("nan"), None],
            ["JPM", "JPM", "BAC"],
            ["A", "B", "C", "D", "E", "F", "G", "H"],
        ]
        for ids in shapes:
            opts = sorted({i for i in ids if isinstance(i, str)})
            picks = _default_bank_picks(ids, opts)
            self.assertTrue(set(picks).issubset(set(opts)),
                            f"default escaped options for {ids!r}")
            self.assertLessEqual(len(picks), 5)

    def test_all_unidentified_yields_empty_default_not_a_crash(self):
        self.assertEqual(_default_bank_picks([None, float("nan")], []), [])

    def test_order_preserved_and_deduped(self):
        ids = ["AAA", "AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
        picks = _default_bank_picks(ids, sorted(set(ids)))
        self.assertEqual(picks, ["AAA", "BBB", "CCC", "DDD", "EEE"])
        self.assertEqual(len(picks), len(set(picks)))

    def test_n_is_honoured_and_short_lists_are_fine(self):
        self.assertEqual(_default_bank_picks(["A", "B", "C"], ["A", "B", "C"], n=2),
                         ["A", "B"])
        self.assertEqual(_default_bank_picks(["A", "B"], ["A", "B"]), ["A", "B"])


class TestPrivateBanksAreSelectable(unittest.TestCase):
    """The owner-requested change: a bank with no ticker must be pickable."""

    def test_private_bank_gets_an_option(self):
        cov = _coverage([None, "JPM"],
                        names=["Farmers State Bank", "JPMorgan Chase Bank"])
        labels, by_label = _bank_options(cov)
        self.assertIn("Farmers State Bank", labels)
        self.assertIn("JPM — JPMorgan Chase Bank", labels)
        self.assertEqual(by_label["Farmers State Bank"], 1)
        self.assertEqual(by_label["JPM — JPMorgan Chase Bank"], 2)

    def test_every_row_with_a_cert_becomes_an_option(self):
        cov = _coverage([None] * 4 + ["JPM"])
        labels, by_label = _bank_options(cov)
        self.assertEqual(len(labels), 5)
        self.assertEqual(len(by_label), 5)

    def test_labels_round_trip_to_the_right_cert(self):
        cov = _coverage([None, "BAC", None],
                        names=["Alpha Bank", "Bank of America", "Omega Bank"],
                        certs=[101, 202, 303])
        labels, by_label = _bank_options(cov)
        self.assertEqual([by_label[lb] for lb in labels], [101, 202, 303])

    def test_deposit_order_is_preserved_in_options(self):
        cov = _coverage([None, "JPM", None],
                        names=["Biggest Private", "JPMorgan", "Small Private"])
        labels, _ = _bank_options(cov)
        self.assertEqual(labels[0], "Biggest Private")   # frame is deposits-desc

    def test_same_named_banks_stay_distinct(self):
        """Distinct institutions genuinely share names — they must not collapse
        into one option, or the picker cannot tell them apart."""
        cov = _coverage([None, None],
                        names=["First National Bank", "First National Bank"],
                        certs=[11, 22])
        labels, by_label = _bank_options(cov)
        self.assertEqual(len(labels), 2)
        self.assertEqual(len(set(labels)), 2)
        self.assertEqual(sorted(by_label.values()), [11, 22])

    def test_rows_without_a_cert_are_skipped(self):
        cov = _coverage([None, "JPM"], certs=[None, 7])
        labels, by_label = _bank_options(cov)
        self.assertEqual(list(by_label.values()), [7])
        self.assertEqual(len(labels), 1)

    def test_default_picks_are_valid_options_under_the_new_model(self):
        """End-to-end: the label list feeds both options and default."""
        cov = _coverage([None, "JPM", None, "BAC", None, "WFC", None])
        labels, _ = _bank_options(cov)
        picks = _default_bank_picks(labels, labels)
        self.assertEqual(len(picks), 5)
        self.assertTrue(set(picks).issubset(set(labels)))


class TestBankOptionLabel(unittest.TestCase):
    def test_public_bank_leads_with_ticker(self):
        self.assertEqual(
            _bank_option_label({"ticker": "JPM", "bank_name": "JPMorgan Chase",
                                "cert": 628}),
            "JPM — JPMorgan Chase")

    def test_private_bank_is_just_the_name(self):
        for missing in (None, "", "   ", float("nan")):
            self.assertEqual(
                _bank_option_label({"ticker": missing, "bank_name": "Farmers State",
                                    "cert": 1}),
                "Farmers State")

    def test_nameless_row_falls_back_to_cert(self):
        self.assertEqual(
            _bank_option_label({"ticker": None, "bank_name": None, "cert": 99}),
            "Cert 99")


if __name__ == "__main__":
    unittest.main()
