"""Per-period intangible deduction = the snapshot's TCE rule, at one date.

Owner, 2026-10-01 (HFWA Corporate Profile): the Valuation card said P/TBV
1.44x (TBV/Share $19.15) while the P/TBV-history chart beside it said 1.36x
— $20.31. HFWA tags its $47.3M of core-deposit intangibles ONLY under
`FiniteLivedIntangibleAssetsNet`; the snapshot resolver falls back to that
tag, the history path (ui/financial_highlights._per_share_for_ends) read only
`IntangibleAssetsNetExcludingGoodwill` and deducted goodwill alone. Two
answers for one number on one page.

Pins (hermetic, synthetic companyfacts — hand-computed values):
  1. HFWA shape: goodwill + finite-lived only → both paths deduct both.
  2. Same-date MSR inside a rollup is netted out; a separately-tagged MSR
     larger than the rollup is not (FITB shape).
  3. Combined tag backs out other-intangibles a filer tags only there (FBIZ
     shape); goodwill exceeding the combined tag yields the combined tag.
  4. Untagged date → None (caller decides n/a vs none), never goodwill-only.
  5. Equivalence: at the snapshot's own date the per-period deduction equals
     _resolve_intangible_adjustment for every shape above.
"""
import unittest
from datetime import datetime
from unittest.mock import patch

from tests import _streamlit_stub

_st = _streamlit_stub.install()
if not hasattr(_st, "query_params"):
    _st.query_params = {}
for _name in ("markdown", "caption", "write", "info", "warning", "error",
              "divider", "metric", "dataframe", "button", "html", "subheader"):
    if not hasattr(_st, _name):
        setattr(_st, _name, lambda *a, **k: None)

import data.sec_client as sc  # noqa: E402
import ui.financial_highlights as fh  # noqa: E402

D = "2026-06-30"
Q = {"accn": "0001046025-26-000040", "form": "10-Q", "filed": "2026-08-07"}


def _facts(usd: dict, shares: float = 40_906_122) -> dict:
    ug = {c: {"units": {"USD": [{"end": D, "val": v, **Q}]}} for c, v in usd.items()}
    ug["CommonStockSharesOutstanding"] = {"units": {"shares": [{"end": D, "val": shares, **Q}]}}
    ug["PreferredStockValue"] = {"units": {"USD": [{"end": D, "val": 0, **Q}]}}
    return {"cik": 1046025, "entityName": "HFWA", "facts": {"us-gaap": ug, "dei": {}}}


HFWA = _facts({"StockholdersEquity": 1_109_692_000, "Goodwill": 279_029_000,
               "FiniteLivedIntangibleAssetsNet": 47_269_000})


def _snapshot_adj(facts):
    """The snapshot resolver's deduction for the same blob."""
    result = {"goodwill": sc._extract_latest_value(facts, "Goodwill"),
              "intangibles": sc._extract_latest_value(facts, "IntangibleAssetsNetExcludingGoodwill")}
    return sc._resolve_intangible_adjustment(facts, result)


def _history_tbvps(facts):
    with patch.object(fh.sec_client, "fetch_company_facts", return_value=facts):
        return fh._per_share_for_ends("0001046025", [datetime(2026, 6, 30)], quarterly=True)[datetime(2026, 6, 30)]


class TestHfwaShape(unittest.TestCase):
    def test_finite_lived_only_filer_deducts_both(self):
        adj, basis = sc._intangible_adjustment_at(HFWA, D)
        self.assertEqual(adj, 279_029_000 + 47_269_000)
        self.assertEqual(basis, "goodwill + other intangibles")
        r = _history_tbvps(HFWA)
        self.assertAlmostEqual(r["tbvps"], (1_109_692_000 - 326_298_000) / 40_906_122, places=9)
        self.assertAlmostEqual(r["tbvps"], 19.151020964539246, places=9)   # the card's $19.15
        self.assertEqual(r["_other"], 47_269_000)

    def test_matches_snapshot_resolver(self):
        self.assertEqual(sc._intangible_adjustment_at(HFWA, D)[0], _snapshot_adj(HFWA))


class TestMsrAndRollups(unittest.TestCase):
    def test_same_date_msr_inside_rollup_is_netted(self):
        f = _facts({"StockholdersEquity": 1e9, "Goodwill": 500e6,
                    "IntangibleAssetsNetExcludingGoodwill": 120e6,
                    "ServicingAssetAtFairValueAmount": 20e6})
        self.assertEqual(sc._intangible_adjustment_at(f, D)[0], 500e6 + 100e6)
        self.assertEqual(sc._intangible_adjustment_at(f, D)[0], _snapshot_adj(f))

    def test_separately_tagged_msr_larger_than_rollup_not_netted(self):
        f = _facts({"StockholdersEquity": 1e9, "Goodwill": 500e6,
                    "IntangibleAssetsNetExcludingGoodwill": 120e6,
                    "ServicingAssetAtFairValueAmount": 300e6})          # FITB shape
        self.assertEqual(sc._intangible_adjustment_at(f, D)[0], 620e6)

    def test_finite_lived_never_msr_netted(self):
        f = _facts({"StockholdersEquity": 1e9, "Goodwill": 500e6,
                    "FiniteLivedIntangibleAssetsNet": 120e6,
                    "ServicingAssetAtFairValueAmount": 20e6})
        self.assertEqual(sc._intangible_adjustment_at(f, D)[0], 620e6)

    def test_combined_tag_backs_out_other_intangibles(self):
        f = _facts({"StockholdersEquity": 1e9, "Goodwill": 500e6,
                    "IntangibleAssetsNetIncludingGoodwill": 560e6})     # FBIZ shape
        self.assertEqual(sc._intangible_adjustment_at(f, D)[0], 560e6)
        self.assertEqual(sc._intangible_adjustment_at(f, D)[0], _snapshot_adj(f))

    def test_goodwill_exceeding_combined_uses_combined(self):
        f = _facts({"StockholdersEquity": 1e9, "Goodwill": 700e6,
                    "IntangibleAssetsNetIncludingGoodwill": 560e6})
        adj, basis = sc._intangible_adjustment_at(f, D)
        self.assertEqual(adj, 560e6)
        self.assertIn("exceeds the rollup", basis)
        self.assertEqual(adj, _snapshot_adj(f))

    def test_combined_only_and_other_only(self):
        self.assertEqual(sc._intangible_adjustment_at(
            _facts({"StockholdersEquity": 1e9, "IntangibleAssetsNetIncludingGoodwill": 90e6}), D)[0], 90e6)
        self.assertEqual(sc._intangible_adjustment_at(
            _facts({"StockholdersEquity": 1e9, "FiniteLivedIntangibleAssetsNet": 9e6}), D)[0], 9e6)


class TestCarriedOtherIntangibles(unittest.TestCase):
    """JPM tags other intangibles only at year-end: at a Q2 date the snapshot
    resolver carries the latest within a year; so must the history path —
    goodwill-only understated JPM's deduction by $1.3B (TBVPS $113.18 vs the
    card's $112.69, 2026-10-01)."""

    def _jpm(self):
        f = _facts({"StockholdersEquity": 350e9, "Goodwill": 52_711e6})
        f["facts"]["us-gaap"]["IntangibleAssetsNetExcludingGoodwill"] = {"units": {"USD": [
            {"end": "2025-12-31", "val": 1_300e6, "accn": "k", "form": "10-K", "filed": "2026-02-20"}]}}
        return f

    def test_carries_latest_within_a_year(self):
        adj, basis = sc._intangible_adjustment_at(self._jpm(), D)
        self.assertEqual(adj, 52_711e6 + 1_300e6)
        self.assertEqual(basis, "goodwill + other intangibles (other intangibles as of 2025-12-31)")
        self.assertEqual(adj, _snapshot_adj(self._jpm()))

    def test_does_not_carry_beyond_a_year(self):
        f = self._jpm()
        f["facts"]["us-gaap"]["IntangibleAssetsNetExcludingGoodwill"]["units"]["USD"][0]["end"] = "2025-03-31"
        adj, basis = sc._intangible_adjustment_at(f, D)
        self.assertEqual((adj, basis), (52_711e6, "goodwill"))


class TestFresherFiniteLivedWins(unittest.TestCase):
    """PNFP after Synovus (hand-checked 2026-08-19: release TBVPS $63.02):
    the rollup tag exists only in the 10-K ($29.7M at 2025-12-31) while the
    quarter's $1,045M of core-deposit intangibles is tagged only as
    FiniteLivedIntangibleAssetsNet (2026-06-30). The snapshot resolver took
    the stale rollup because it was not None → TBVPS $69.86 (2026-10-01)."""

    def _pnfp(self):
        f = _facts({"StockholdersEquity": 7_612e6, "Goodwill": 3_479e6,
                    "FiniteLivedIntangibleAssetsNet": 1_045e6}, shares=120_600_000)
        f["facts"]["us-gaap"]["IntangibleAssetsNetExcludingGoodwill"] = {"units": {"USD": [
            {"end": "2025-12-31", "val": 29.7e6, "accn": "k", "form": "10-K", "filed": "2026-03-02"}]}}
        f["facts"]["us-gaap"]["IntangibleAssetsNetIncludingGoodwill"] = {"units": {"USD": [
            {"end": "2025-12-31", "val": 1_878.6e6, "accn": "k", "form": "10-K", "filed": "2026-03-02"}]}}
        return f

    def test_snapshot_uses_the_fresher_finite_lived_tag(self):
        f = self._pnfp()
        self.assertEqual(_snapshot_adj(f), 3_479e6 + 1_045e6)
        self.assertEqual(sc._intangible_adjustment_at(f, D)[0], 3_479e6 + 1_045e6)
        # (7,612 − 4,524) / 120.6 = $25.60 on this synthetic equity — the point
        # is the deduction, which both paths now agree on.
        r = _history_tbvps(f)
        self.assertAlmostEqual(r["tbvps"], (7_612e6 - 4_524e6) / 120_600_000, places=9)

    def test_same_date_tie_keeps_the_rollup(self):
        f = _facts({"StockholdersEquity": 1e9, "Goodwill": 500e6,
                    "IntangibleAssetsNetExcludingGoodwill": 120e6,
                    "FiniteLivedIntangibleAssetsNet": 90e6,
                    "ServicingAssetAtFairValueAmount": 20e6})
        self.assertEqual(_snapshot_adj(f), 500e6 + 100e6)          # rollup, MSR netted
        self.assertEqual(sc._intangible_adjustment_at(f, D)[0], 600e6)


class TestUntagged(unittest.TestCase):
    def test_nothing_at_date_is_none_not_zero(self):
        adj, basis = sc._intangible_adjustment_at(_facts({"StockholdersEquity": 1e9}), D)
        self.assertIsNone(adj)
        self.assertEqual(basis, "no intangibles tagged at this date")

    def test_history_path_no_intangibles_ever_is_bvps(self):
        r = _history_tbvps(_facts({"StockholdersEquity": 1e9}))
        self.assertEqual(r["_adj"], 0.0)
        self.assertAlmostEqual(r["tbvps"], r["bvps"], places=10)


if __name__ == "__main__":
    unittest.main()
