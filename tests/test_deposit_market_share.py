"""Unit tests for the Deposit Market Share row builder (SNL plan §11).

Run: python -m unittest tests.test_deposit_market_share
"""
from __future__ import annotations

import sys
import types
import unittest
from decimal import Decimal
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

# House stub header (order-safe next to the stub-installing suites).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from ui.deposit_market_share import _share_rows  # noqa: E402


def _frame(rows):
    return pd.DataFrame(rows, columns=[
        "market_key", "market_label", "cert", "bank_name", "ticker",
        "n_branches", "deposits"])


class TestShareRows(unittest.TestCase):
    def test_hand_computed_two_bank_market(self):
        # Market: subject 600k, competitor 400k → total 1,000k.
        # Share 60%; HHI = 60² + 40² = 3600 + 1600 = 5200; rank 1 of 2.
        df = _frame([
            ("06001", "Alameda, CA", 111, "Subject Bank", "SUBJ", 3, 600_000),
            ("06001", "Alameda, CA", 222, "Rival Bank", "RVL", 5, 400_000),
        ])
        rows = _share_rows(df, subject_cert=111)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertAlmostEqual(r["share_pct"], 60.0)
        self.assertAlmostEqual(r["hhi"], 5200.0)
        self.assertEqual((r["rank"], r["n_banks"]), (1, 2))
        self.assertEqual(r["top_competitor"], "Rival Bank")
        self.assertAlmostEqual(r["top_competitor_share_pct"], 40.0)
        self.assertEqual(r["subj_branches"], 3)

    def test_decimal_deposits_prod_postgres_parity(self):
        # Prod parity: Postgres SUM(BIGINT) returns NUMERIC, which psycopg2
        # hands back as decimal.Decimal — the deposits column arrives as an
        # object column of Decimals, not ints (sqlite/local returns ints).
        # Same hand-computed market as above: 600k/400k → share 60%,
        # HHI 60² + 40² = 5200, rank 1 of 2.
        df = _frame([
            ("06001", "Alameda, CA", 111, "Subject Bank", "SUBJ", 3,
             Decimal("600000")),
            ("06001", "Alameda, CA", 222, "Rival Bank", "RVL", 5,
             Decimal("400000")),
        ])
        rows = _share_rows(df, subject_cert=111)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertAlmostEqual(r["share_pct"], 60.0)
        self.assertAlmostEqual(r["hhi"], 5200.0)
        self.assertAlmostEqual(r["top_competitor_share_pct"], 40.0)
        self.assertEqual((r["rank"], r["n_banks"]), (1, 2))
        self.assertIsInstance(r["subj_deposits_k"], float)
        self.assertIsInstance(r["market_total_k"], float)

    def test_rank_when_not_leader_and_monopoly_market(self):
        df = _frame([
            # market A: subject is #3
            ("A", "A County, TX", 1, "Big", None, 9, 700_000),
            ("A", "A County, TX", 2, "Mid", None, 4, 200_000),
            ("A", "A County, TX", 111, "Subject", "SUBJ", 1, 100_000),
            # market B: subject alone → rank 1 of 1, HHI 10,000, no competitor
            ("B", "B County, TX", 111, "Subject", "SUBJ", 2, 50_000),
        ])
        rows = _share_rows(df, subject_cert=111)
        by_key = {r["market_key"]: r for r in rows}
        a, b = by_key["A"], by_key["B"]
        self.assertEqual((a["rank"], a["n_banks"]), (3, 3))
        self.assertAlmostEqual(a["share_pct"], 10.0)
        self.assertAlmostEqual(a["hhi"], 70**2 + 20**2 + 10**2)  # 5400
        self.assertEqual(a["top_competitor"], "Big")
        self.assertEqual((b["rank"], b["n_banks"]), (1, 1))
        self.assertAlmostEqual(b["hhi"], 10_000.0)
        self.assertIsNone(b["top_competitor"])

    def test_sorted_by_subject_deposits_desc(self):
        df = _frame([
            ("A", "A", 111, "S", None, 1, 10_000),
            ("B", "B", 111, "S", None, 1, 90_000),
        ])
        rows = _share_rows(df, subject_cert=111)
        self.assertEqual([r["market_key"] for r in rows], ["B", "A"])

    def test_zero_total_and_missing_subject_skipped(self):
        df = _frame([
            ("Z", "Zero, OK", 111, "S", None, 1, 0),
            ("Y", "Yonder, OK", 222, "Other", None, 1, 500),
        ])
        self.assertEqual(_share_rows(df, subject_cert=111), [])

    def test_empty_frame(self):
        self.assertEqual(_share_rows(pd.DataFrame(), 111), [])
        self.assertEqual(_share_rows(None, 111), [])


if __name__ == "__main__":
    unittest.main()
