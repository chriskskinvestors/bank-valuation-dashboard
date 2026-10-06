"""
(TBV audit, 2026-07-30) The tangible-book convention must be identical on every
surface that computes it.

House convention (CLAUDE.md, analysis/valuation.compute_roatce):
  tangible equity = EQTOT − INTAN
where INTAN is TOTAL intangibles. INTANGW is GOODWILL ONLY; deducting it instead
leaves core-deposit and other intangibles inside "tangible" equity, overstating
TCE. AUDIT-2026-07-02 #24 fixed that everywhere — except the Valuation Model,
which this pins.

Why it mattered: ui/valuation_model seeds TBV/share from that figure, and the
panel's headline is

    warranted_price = warranted P/TBV × TBV/share

so an overstated TBV/share overstated the fair value proportionally, on every
bank carrying non-goodwill intangibles.

Also pinned: the deal-comps bank-sub denominator reads EQTOT, not EQ. Both
fields exist on the FDIC financials endpoint and they differ (verified live
2026-07-30, JPM cert 628 @2026-03-31: EQ 335,931,000 vs EQTOT 335,961,000 $K —
minority interests), so EQ made deal P/TBV the one surface using a different
equity base.

Run: python -m unittest tests.test_tbv_conventions
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()


def _passthrough_resolvers(test):
    """_derive_defaults seeds EPS / TBV via analysis.valuation's release-first
    resolvers (live 8-K lookups); offline, pass the SEC reconstruction through
    exactly as the resolvers do when no release figure exists."""
    from unittest import mock as _m
    for name, fn in (
            ("_resolve_eps", lambda t, r, a=None: (r, "reconstructed" if r is not None else None, False)),
            ("_resolve_tbvps", lambda t, r, b=None, sec_as_of=None, shares=None:
                (r, "reconstructed" if r is not None else None, False))):
        p = _m.patch(f"analysis.valuation.{name}", fn)
        p.start()
        test.addCleanup(p.stop)


class TestValuationModelTbvps(unittest.TestCase):
    """ui/valuation_model._derive_defaults seeds the platform's RESOLVED
    TBV/share (company-reported, else the SEC holdco reconstruction) and never
    rebuilds one from FDIC bank-sub equity ÷ holdco shares (REVIEW 2026-10-02
    P0-1: WTFC seeded $95.97 vs its reported $92.13 — bank-sub TCE, holdco
    shares, holdco preferred ignored)."""

    def setUp(self):
        _passthrough_resolvers(self)

    def _hist(self, eqtot, intan, intangw):
        return [{"REPDTE": "20260331", "EQTOT": eqtot, "INTAN": intan,
                 "INTANGW": intangw, "LNLSNET": 500_000, "NETINC": 10_000,
                 "ASSET": 2_000_000}]

    def test_no_fdic_fallback_when_sec_tbvps_unresolved(self):
        # WTFC shape: FDIC group equity present, SEC TBVPS None (preferred
        # outstanding but unresolvable). The old fallback gave
        # (1,000,000 − 300,000) × 1000 / 10M = $70.00. Now: n/a.
        from ui.valuation_model import _derive_defaults
        seed = _derive_defaults(
            "TEST", self._hist(eqtot=1_000_000, intan=300_000, intangw=200_000),
            {"shares_outstanding": 10_000_000, "eps": 5.0})
        self.assertIsNone(seed["tbvps"])
        self.assertIsNone(seed["tbvps_source"])

    def test_sec_holdco_figure_is_seeded(self):
        from ui.valuation_model import _derive_defaults
        seed = _derive_defaults(
            "TEST", self._hist(eqtot=1_000_000, intan=300_000, intangw=200_000),
            {"shares_outstanding": 10_000_000, "eps": 5.0,
             "tangible_book_value_per_share": 64.25})
        self.assertAlmostEqual(seed["tbvps"], 64.25, places=6)
        self.assertEqual(seed["tbvps_source"], "reconstructed")

    def test_company_reported_value_wins(self):
        # JPM 2Q26: reported 113.35 vs reconstruction 112.69 — the model must
        # seed what the Company page shows.
        from unittest import mock
        from ui.valuation_model import _derive_defaults
        with mock.patch("analysis.valuation._resolve_tbvps",
                        lambda t, r, b=None, sec_as_of=None, shares=None: (113.35, "reported_8k", False)):
            seed = _derive_defaults(
                "JPM", self._hist(1_000_000, 300_000, 200_000),
                {"shares_outstanding": 10_000_000, "eps": 5.0,
                 "tangible_book_value_per_share": 112.69})
        self.assertAlmostEqual(seed["tbvps"], 113.35, places=6)
        self.assertEqual(seed["tbvps_source"], "reported_8k")

    def test_non_positive_tbvps_is_na(self):
        from ui.valuation_model import _derive_defaults
        seed = _derive_defaults(
            "TEST", self._hist(1_000_000, 300_000, 200_000),
            {"shares_outstanding": 10_000_000, "eps": 5.0,
             "tangible_book_value_per_share": -3.10})
        self.assertIsNone(seed["tbvps"])


class TestConventionIsUniform(unittest.TestCase):
    """Structural: no surface may compute tangible equity off goodwill alone,
    and the FDIC equity base must be EQTOT everywhere."""

    def test_valuation_model_does_not_derive_tce_from_goodwill(self):
        """Precise rather than a broad INTANGW scan: the field is legitimately
        READ elsewhere (statement decomposition, and capital_dynamics'
        max(goodwill, intangibles), which lands on INTAN). What must never come
        back is deriving tangible equity from goodwill alone here."""
        src = (REPO / "ui/valuation_model.py").read_text(encoding="utf-8")
        # What must never return: tangible equity derived from goodwill alone.
        self.assertNotIn("equity - goodwill", src)
        # The model no longer derives tangible equity at all — it seeds the
        # platform's resolved TBV/share (REVIEW 2026-10-02 P0-1).
        self.assertNotIn("INTANGW", src)
        self.assertIn("_resolve_tbvps(", src)

    def test_deal_comps_requests_eqtot_not_eq(self):
        src = (REPO / "data/deal_comps.py").read_text(encoding="utf-8")
        self.assertIn("CERT,REPDTE,EQTOT,INTAN,COREDEP,ASSET", src)
        self.assertNotIn('rec.get("EQ")', src)

    def test_ptbv_refuses_non_positive_tangible_book(self):
        """A negative/zero tangible book has no meaningful multiple — n/a, not
        a negative P/TBV rendered as if it were a valuation."""
        from analysis.valuation import compute_ptbv_ratio
        self.assertIsNone(compute_ptbv_ratio(10.0, 0.0))
        self.assertIsNone(compute_ptbv_ratio(10.0, -5.0))
        self.assertIsNone(compute_ptbv_ratio(None, 5.0))
        self.assertAlmostEqual(compute_ptbv_ratio(10.0, 5.0), 2.0, places=6)


if __name__ == "__main__":
    unittest.main()
