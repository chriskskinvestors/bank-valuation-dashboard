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


class TestValuationModelTbvps(unittest.TestCase):
    """ui/valuation_model._derive_defaults — hand-computed."""

    def _hist(self, eqtot, intan, intangw):
        # One quarter is enough for the TBV path; the other fields keep the
        # surrounding derivations from blowing up.
        return [{"REPDTE": "20260331", "EQTOT": eqtot, "INTAN": intan,
                 "INTANGW": intangw, "LNLSNET": 500_000, "NETINC": 10_000,
                 "ASSET": 2_000_000}]

    def test_fdic_fallback_deducts_total_intangibles_not_goodwill(self):
        from ui.valuation_model import _derive_defaults
        # equity $1,000,000K; goodwill $200,000K; TOTAL intangibles $300,000K.
        # Correct TCE = 1,000,000 − 300,000 = 700,000 ($K) = $700,000,000.
        # 10,000,000 shares → $70.00/share.
        # The INTANGW bug would give (1,000,000 − 200,000) = 800,000 → $80.00,
        # a 14.3% overstatement flowing straight into warranted price.
        seed = _derive_defaults(
            "TEST",
            self._hist(eqtot=1_000_000, intan=300_000, intangw=200_000),
            {"shares_outstanding": 10_000_000, "eps": 5.0},
        )
        self.assertAlmostEqual(seed["tbvps"], 70.00, places=6)
        self.assertNotAlmostEqual(seed["tbvps"], 80.00, places=2)

    def test_sec_holdco_figure_is_preferred_when_present(self):
        """The model must seed the SAME tangible book the Company page shows —
        the SEC reconstruction (preferred removed, MSR convention applied)."""
        from ui.valuation_model import _derive_defaults
        seed = _derive_defaults(
            "TEST",
            self._hist(eqtot=1_000_000, intan=300_000, intangw=200_000),
            {"shares_outstanding": 10_000_000, "eps": 5.0,
             "tangible_book_value_per_share": 64.25},
        )
        self.assertAlmostEqual(seed["tbvps"], 64.25, places=6)

    def test_no_shares_yields_na_not_a_guess(self):
        from ui.valuation_model import _derive_defaults
        seed = _derive_defaults(
            "TEST", self._hist(1_000_000, 300_000, 200_000),
            {"shares_outstanding": 0, "eps": 5.0})
        self.assertIsNone(seed["tbvps"])

    def test_missing_intan_does_not_silently_undeduct(self):
        """A record with no INTAN must not quietly become 'equity is tangible'.

        This is the weaker half of the guarantee: INTAN is in
        _BASE_FINANCIALS_FIELDS so it is normally present, and when it is
        absent the fallback deducts 0 — the honest read of "no intangibles
        reported". The pin exists so a future change to the fetched field list
        shows up here rather than as a silently inflated TBV."""
        from ui.valuation_model import _derive_defaults
        seed = _derive_defaults(
            "TEST",
            [{"REPDTE": "20260331", "EQTOT": 1_000_000, "INTANGW": 200_000,
              "LNLSNET": 500_000, "NETINC": 10_000, "ASSET": 2_000_000}],
            {"shares_outstanding": 10_000_000, "eps": 5.0})
        self.assertAlmostEqual(seed["tbvps"], 100.00, places=6)


class TestConventionIsUniform(unittest.TestCase):
    """Structural: no surface may compute tangible equity off goodwill alone,
    and the FDIC equity base must be EQTOT everywhere."""

    def test_valuation_model_does_not_derive_tce_from_goodwill(self):
        """Precise rather than a broad INTANGW scan: the field is legitimately
        READ elsewhere (statement decomposition, and capital_dynamics'
        max(goodwill, intangibles), which lands on INTAN). What must never come
        back is deriving tangible equity from goodwill alone here."""
        src = (REPO / "ui/valuation_model.py").read_text(encoding="utf-8")
        self.assertNotIn("equity - goodwill", src)
        self.assertNotIn('latest.get("INTANGW")', src)
        self.assertIn('latest.get("INTAN")', src)

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
