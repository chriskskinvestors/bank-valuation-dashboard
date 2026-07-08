"""
(AUDIT-2026-07-02 P2 #35) Financial Highlights TBVPS must subtract the
intangible adjustment taken from the SAME balance-sheet date as equity.

The bug: equity falls back to a nearby period-end (`eq_date`) on an exact
date-miss, but goodwill/intangibles were looked up only at the requested
column end (`key`). When those disagree, the intangible lookups return None,
`adj` is None, and TBVPS silently collapses onto BVPS — goodwill never
subtracted, so a bank with goodwill shows an overstated tangible book value.

Fix: look up goodwill / other-intangibles / incl-goodwill at `eq_date`.

Pins (mocked fetch_company_facts, no network):
  1. equity date-miss + goodwill present at eq_date -> TBVPS subtracts goodwill
     (TBVPS < BVPS), it does NOT fall back to BVPS.
  2. exact-date match (normal path) is unchanged: goodwill at the column end is
     still used.
"""
from __future__ import annotations

import sys
import types
import unittest
import warnings
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
warnings.filterwarnings("ignore")


def _install_streamlit_stub():
    st = types.ModuleType("streamlit")

    def _noop(*a, **k):
        return None

    def _cache(*a, **k):
        if a and callable(a[0]):
            return a[0]
        return lambda f: f

    st.cache_data = _cache
    st.cache_resource = _cache
    st.fragment = _cache          # decorator-safe passthrough
    st.session_state = {}
    st.query_params = {}
    for name in ("markdown", "caption", "write", "info", "warning", "error",
                 "divider", "metric", "dataframe", "button", "html", "subheader"):
        setattr(st, name, _noop)
    comp_pkg = types.ModuleType("streamlit.components")
    comp_v1 = types.ModuleType("streamlit.components.v1")
    comp_v1.html = _noop
    comp_pkg.v1 = comp_v1
    st.components = comp_pkg
    sys.modules["streamlit"] = st
    sys.modules["streamlit.components"] = comp_pkg
    sys.modules["streamlit.components.v1"] = comp_v1


# Reuse a richer streamlit stub if a sibling test module already installed one
# (running combined with test_audit_regressions et al.); only install ours when
# none exists, so we never clobber their st.fragment/segmented_control support.
if "streamlit" not in sys.modules:
    _install_streamlit_stub()

import ui.financial_highlights as fh  # noqa: E402


def _facts(equity_rows, goodwill_rows, share_rows, incl_rows=None):
    """Build a minimal companyfacts dict. Rows are (end, val) tuples."""
    def _usd(rows):
        return {"units": {"USD": [
            {"form": "10-Q", "end": e, "val": v} for e, v in rows]}}
    us_gaap = {
        "StockholdersEquity": _usd(equity_rows),
        "Goodwill": _usd(goodwill_rows),
    }
    if incl_rows:
        us_gaap["IntangibleAssetsNetIncludingGoodwill"] = _usd(incl_rows)
    return {"facts": {
        "us-gaap": us_gaap,
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"end": e, "val": v} for e, v in share_rows]}}},
    }}


class TestTbvpsDateAlignment(unittest.TestCase):
    END = datetime(2025, 9, 30)

    def _run(self, facts):
        with patch.object(fh.sec_client, "fetch_company_facts", return_value=facts):
            out = fh._per_share_for_ends("0000000001", [self.END], quarterly=True)
        return out[self.END]

    def test_date_miss_still_subtracts_goodwill(self):
        # Equity + goodwill reported at 2025-09-28 (2 days off the column end);
        # shares at the exact end. eq_date resolves to 2025-09-28, so the
        # intangible lookup must join there — not at the missing 2025-09-30.
        facts = _facts(
            equity_rows=[("2025-09-28", 1_000_000)],
            goodwill_rows=[("2025-09-28", 200_000)],
            share_rows=[("2025-09-30", 100_000)],
        )
        row = self._run(facts)
        self.assertEqual(row["bvps"], 10.0)        # 1_000_000 / 100_000
        self.assertEqual(row["tbvps"], 8.0)        # (1_000_000 - 200_000)/100_000
        self.assertNotEqual(row["tbvps"], row["bvps"])   # did NOT collapse to BVPS
        self.assertEqual(row["_gw"], 200_000)
        self.assertEqual(row["_eq_date"], "2025-09-28")

    def test_exact_match_unchanged(self):
        # Normal path: everything at the exact column end. Goodwill still applied.
        facts = _facts(
            equity_rows=[("2025-09-30", 1_000_000)],
            goodwill_rows=[("2025-09-30", 200_000)],
            share_rows=[("2025-09-30", 100_000)],
        )
        row = self._run(facts)
        self.assertEqual(row["bvps"], 10.0)
        self.assertEqual(row["tbvps"], 8.0)
        self.assertEqual(row["_eq_date"], "2025-09-30")


if __name__ == "__main__":
    unittest.main()
