"""
(AUDIT-2026-07-02 P3) _extract_time_series must keep the MOST-RECENTLY-FILED
value per period end. A later 10-K/10-Q restates the same period, and that
restatement must win over the as-first-reported figure.

The bug: the dedup sorted only by `end`, so among the multiple filings for one
end the "winner" was whatever pandas' stable sort left first — the array order,
which is usually the ORIGINAL filing. Restated values were silently dropped
(TBVPS/BVPS history then rode stale numbers). Fix: sort by (end, filed) and keep
the last.

Every expected value hand-picked; no network (_extract_time_series is pure).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.sec_client as sec_client  # noqa: E402


def _e(end, val, *, form="10-K", filed="2024-02-15"):
    return {"end": end, "val": val, "form": form, "filed": filed}


def _facts(entries):
    return {"facts": {"us-gaap": {"StockholdersEquity": {"units": {"USD": entries}}}}}


class TestRestatementDedup(unittest.TestCase):
    def test_latest_filed_wins_regardless_of_array_order(self):
        # Same period end 2023-12-31 reported twice: original (filed 2024-02) and
        # a restated comparative in the FY2024 10-K (filed 2025-02). Original is
        # placed FIRST in the array — the exact ordering the old code kept.
        facts = _facts([
            _e("2023-12-31", 100, filed="2024-02-15"),   # original, first in array
            _e("2023-12-31", 110, filed="2025-02-15"),   # restatement
            _e("2022-12-31", 90, filed="2023-02-15"),
        ])
        df = sec_client._extract_time_series(facts, "StockholdersEquity")
        # one row per end, newest end first (contract preserved)
        self.assertEqual(len(df), 2)
        self.assertEqual(list(df["end"].dt.strftime("%Y-%m-%d")), ["2023-12-31", "2022-12-31"])
        # restated value wins for the duplicated end
        val_2023 = df.loc[df["end"] == "2023-12-31", "val"].iloc[0]
        self.assertEqual(val_2023, 110)

    def test_missing_filed_never_beats_a_dated_filing(self):
        facts = _facts([
            _e("2023-12-31", 200, filed=None),
            _e("2023-12-31", 205, filed="2024-05-01"),
        ])
        df = sec_client._extract_time_series(facts, "StockholdersEquity")
        self.assertEqual(len(df), 1)
        self.assertEqual(df["val"].iloc[0], 205)


if __name__ == "__main__":
    unittest.main()
