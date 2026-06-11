"""
Regression tests for the 2026-06-11 audit's P0 correctness fixes
(docs/AUDIT-2026-06-11.md). Each test pins a bug that shipped once:

A1  — unit-guess double-converted sub-$1B banks into trillion-dollar tiers
A12 — "TCE" and "ROATCE" on the statement pages used different intangibles fields
A13 — missing loan totals produced absurd past-due ratios via an `or 1` denominator
A19 — quarters beyond the hand-maintained Fed-funds table silently dropped out
"""
import sys
import types
import unittest

import pandas as pd

# Stub streamlit before importing modules that decorate with st.cache_data.
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)


class TestA1PeerTierUnits(unittest.TestCase):
    """total_assets is raw dollars; no '< 1e9 → ×1000' guessing."""

    def test_800m_bank_is_community_not_money_center(self):
        from analysis.peer_groups import group_banks, asset_size_tier
        # A genuine $800M community bank (raw dollars). The old heuristic
        # multiplied this by 1000 → $800B → "Large Regional ($100B-$1T)".
        m = {"ticker": "TINY", "total_assets": 8e8}
        groups = group_banks([m])
        self.assertIn("Community (<$10B)", groups["by_size"])
        self.assertNotIn("Large Regional ($100B-$1T)", groups["by_size"])
        self.assertEqual(asset_size_tier(8e8), "Community (<$10B)")

    def test_tier_context_uses_raw_dollars(self):
        from analysis.peer_groups import metric_percentile_context
        mets = [{"ticker": f"B{i}", "total_assets": 5e8 + i * 1e7,
                 "roaa": 1.0 + i * 0.05} for i in range(8)]
        ctx = metric_percentile_context("B0", mets, metric_keys=["roaa"], mode="size")
        self.assertEqual(ctx["_meta"]["tier"], "Community (<$10B)")

    def test_metrics_boundary_converts_asset_to_dollars(self):
        # The contract A1 relies on: build_bank_metrics emits total_assets in
        # raw dollars (FDIC reports $thousands).
        from analysis.metrics import build_bank_metrics
        out = build_bank_metrics("X", {"ASSET": 800_000, "REPDTE": "2025-12-31"},
                                 {}, {}, [])
        self.assertEqual(out.get("total_assets"), 800_000 * 1000)


class TestA12TceConvention(unittest.TestCase):
    """One TCE convention on the statement pages: equity − INTAN (total
    intangibles), for BOTH the tce row and the roatce row."""

    def test_tce_kind_uses_total_intangibles(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "ui" /
               "financials_statements.py").read_text(encoding="utf-8")
        tce_block = src.split('if kind == "tce":')[1].split('if kind ==')[0]
        self.assertIn('"INTAN"', tce_block,
                      "tce kind must subtract INTAN (total intangibles)")
        self.assertNotIn('"INTANGW"', tce_block,
                         "tce kind must not use INTANGW (goodwill only)")
        roatce_block = src.split('if kind == "roatce":')[1].split('if kind ==')[0]
        self.assertIn('"INTAN"', roatce_block)


class TestA13PastDueDenominator(unittest.TestCase):
    """Missing total_loans must skip the ratio, not divide by 1."""

    def test_missing_loans_skips_past_due_pct(self):
        from analysis.credit_dynamics import build_credit_timeline
        recs = [{"REPDTE": "2025-12-31", "P3ASSET": 5_000,  # $5M past due ($000)
                 "LNLSNET": None}]
        df = build_credit_timeline(recs)
        if "past_due_30_89_pct" in df.columns:
            self.assertTrue(df["past_due_30_89_pct"].isna().all(),
                            "past-due % must be absent when loans are missing "
                            "(the old `or 1` produced 500000%)")

    def test_present_loans_computes_normally(self):
        from analysis.credit_dynamics import build_credit_timeline, _CREDIT_FIELDS
        loans_field = _CREDIT_FIELDS.get("total_loans", "LNLSNET")
        pd3089_field = _CREDIT_FIELDS.get("past_due_30_89", "P3ASSET")
        recs = [{"REPDTE": "2025-12-31", loans_field: 1_000_000,
                 pd3089_field: 10_000}]
        df = build_credit_timeline(recs)
        self.assertAlmostEqual(float(df["past_due_30_89_pct"].iloc[0]), 1.0)


class TestA19FedFunds(unittest.TestCase):
    """Quarters beyond the static table derive from FRED instead of vanishing."""

    def test_table_quarter_still_served(self):
        from analysis.deposit_dynamics import _get_fed_funds
        self.assertEqual(_get_fed_funds("2025-12-31"), 4.00)

    def test_missing_quarter_derives_from_fred(self):
        import analysis.deposit_dynamics as dd
        dd._FED_FUNDS_LIVE.clear()
        fred = types.ModuleType("data.fred_client")
        fred.fetch_series = lambda sid, years=3: pd.DataFrame({
            "date": pd.to_datetime(["2026-04-01", "2026-05-01", "2026-06-01"]),
            "value": [3.70, 3.60, 3.50],
        })
        old = sys.modules.get("data.fred_client")
        sys.modules["data.fred_client"] = fred
        try:
            v = dd._get_fed_funds("2026-06-30")  # not in the static table
            self.assertAlmostEqual(v, 3.60, places=2)
        finally:
            if old is not None:
                sys.modules["data.fred_client"] = old
            else:
                sys.modules.pop("data.fred_client", None)

    def test_fred_failure_returns_none_not_garbage(self):
        import analysis.deposit_dynamics as dd
        dd._FED_FUNDS_LIVE.clear()
        fred = types.ModuleType("data.fred_client")
        def _boom(sid, years=3):
            raise ConnectionError("offline")
        fred.fetch_series = _boom
        old = sys.modules.get("data.fred_client")
        sys.modules["data.fred_client"] = fred
        try:
            self.assertIsNone(dd._get_fed_funds("2027-03-31"))
        finally:
            if old is not None:
                sys.modules["data.fred_client"] = old
            else:
                sys.modules.pop("data.fred_client", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
