"""(2026-08-19) The universe conflict sweep's three hits — all the same
defect family: ABSENCE OF EVIDENCE treated as absence of preferred/
intangibles, each shipping an overstated TBVPS while the bank's own release
said less. Hand-verified against raw companyfacts + the 2Q26 releases:

- BAFN served $28.22 vs release $4.82 (5.9×): a $96.05M distressed-recap
  preferred exists with ZERO balance-sheet preferred tags in companyfacts —
  _resolve_preferred_stock returned (0.0, False) and never hit the
  cardinal-rule n/a. Fresh duration facts proved it existed
  (PreferredStockDividendsIncomeStatementImpact $386K,
  ProceedsFromIssuanceOfPreferredStockAndPreferenceStock $74.5M).
- MBIN served $51.93 vs release $39.93 (+$12.00/sh = exactly the missed
  $551.29M preferred / 45.94M shares): carrying-value tags dead since 2018,
  fresh PreferredStockDividendsIncomeStatementImpact $10.27M proves
  preferred is outstanding.
- PNFP served $80.65 vs release $63.02: the goodwill>combined*1.05 guard
  trusted a STALE pre-Synovus-merger combined tag ($1,879M @ 2025-12-31)
  over fresh post-merger goodwill ($3,479M @ 2026-06-30) + fresh
  finite-lived intangibles ($1,045M) with no same-period gate.

Fixes pinned:
- income/cash-flow preferred EVIDENCE (duration facts) makes
  has_preferred=True → (None, True) → per-share metrics n/a → the release
  figure serves via reported_8k. PreferredStockSharesAuthorized is NOT
  evidence (every charter authorizes unissued preferred).
- the combined-tag guard requires incl_end >= gw_end; the same-period
  dimensional-goodwill case it was built for still works.

Run: python -m unittest tests.test_preferred_evidence_and_intangible_gate
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from data.sec_client import (  # noqa: E402
    _resolve_intangible_adjustment,
    _resolve_preferred_stock,
)

FRESH = (date.today() - timedelta(days=50)).isoformat()
STALE_2018 = "2018-12-31"
STALE_PRIOR_Q = (date.today() - timedelta(days=50 + 182)).isoformat()
FILED = date.today().isoformat()


def _pt(end, val, form="10-Q"):
    return {"end": end, "val": val, "form": form, "filed": FILED}


def _facts(us_gaap):
    return {"facts": {"us-gaap": us_gaap}}


class TestPreferredEvidence(unittest.TestCase):
    def test_bafn_shape_untagged_preferred_is_present_unresolved(self):
        """No balance-sheet preferred tags AT ALL, but fresh dividend +
        issuance duration facts → (None, True) → caller renders n/a."""
        facts = _facts({
            "PreferredStockDividendsIncomeStatementImpact": {
                "units": {"USD": [_pt(FRESH, 386_000)]}},
            "ProceedsFromIssuanceOfPreferredStockAndPreferenceStock": {
                "units": {"USD": [_pt(FRESH, 74_508_000)]}},
        })
        self.assertEqual(_resolve_preferred_stock(facts), (None, True))

    def test_mbin_shape_dead_value_tags_fresh_dividends(self):
        """Carrying-value tags abandoned in 2018 (staleness guard rightly
        rejects them) + fresh preferred dividends → present, unresolved."""
        facts = _facts({
            "PreferredStockValue": {
                "units": {"USD": [_pt(STALE_2018, 41_581_000, "10-K")]}},
            "PreferredStockSharesOutstanding": {
                "units": {"shares": [_pt(STALE_2018, 41_625, "10-K")]}},
            "PreferredStockDividendsIncomeStatementImpact": {
                "units": {"USD": [_pt(FRESH, 10_266_000)]}},
        })
        self.assertEqual(_resolve_preferred_stock(facts), (None, True))

    def test_no_preferred_at_all_unchanged(self):
        self.assertEqual(_resolve_preferred_stock(_facts({})), (0.0, False))

    def test_authorized_alone_is_not_evidence(self):
        """Nearly every charter authorizes preferred that was never issued —
        authorization must not flip a clean bank to n/a."""
        facts = _facts({
            "PreferredStockSharesAuthorized": {
                "units": {"shares": [_pt(FRESH, 5_000_000)]}},
        })
        self.assertEqual(_resolve_preferred_stock(facts), (0.0, False))

    def test_resolved_carrying_value_still_wins(self):
        facts = _facts({
            "PreferredStockValue": {
                "units": {"USD": [_pt(FRESH, 781_000_000)]}},
            "PreferredStockDividendsIncomeStatementImpact": {
                "units": {"USD": [_pt(FRESH, 10_000_000)]}},
        })
        self.assertEqual(_resolve_preferred_stock(facts),
                         (781_000_000, True))


class TestIntangibleCombinedTagGate(unittest.TestCase):
    def _adjustment(self, us_gaap, goodwill, intangibles=None):
        result = {"goodwill": goodwill, "intangibles": intangibles}
        return _resolve_intangible_adjustment(_facts(us_gaap), result)

    def test_pnfp_shape_stale_combined_tag_cannot_beat_fresh_goodwill(self):
        """Post-merger: fresh goodwill 3,479M + fresh finite-lived 1,045M;
        the combined tag is the STALE pre-merger 1,879M — it must be
        ignored, adjustment = 4,524M (ties the release's TCE math)."""
        adj = self._adjustment({
            "Goodwill": {"units": {"USD": [
                _pt(STALE_PRIOR_Q, 1_849_000_000, "10-K"),
                _pt(FRESH, 3_479_000_000)]}},
            "IntangibleAssetsNetIncludingGoodwill": {"units": {"USD": [
                _pt(STALE_PRIOR_Q, 1_878_619_000, "10-K")]}},
            "FiniteLivedIntangibleAssetsNet": {"units": {"USD": [
                _pt(FRESH, 1_045_000_000)]}},
        }, goodwill=3_479_000_000, intangibles=None)
        self.assertEqual(adj, 3_479_000_000 + 1_045_000_000)

    def test_same_period_dimensional_goodwill_still_trusts_combined(self):
        """The guard's original case: SAME-period goodwill > combined means
        the goodwill tag is dimensional/stale — combined wins, unchanged."""
        adj = self._adjustment({
            "Goodwill": {"units": {"USD": [_pt(FRESH, 3_480_000_000)]}},
            "IntangibleAssetsNetIncludingGoodwill": {"units": {"USD": [
                _pt(FRESH, 1_880_000_000)]}},
        }, goodwill=3_480_000_000, intangibles=None)
        self.assertEqual(adj, 1_880_000_000)


if __name__ == "__main__":
    unittest.main()
