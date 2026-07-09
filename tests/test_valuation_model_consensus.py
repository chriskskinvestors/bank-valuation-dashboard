"""
Pins the 2026-07-02 audit's consensus-period-basis fixes (#6 / #7) in
ui/valuation_model.py:

  #6 — the "Pre-fill EPS / payout" consensus action blanket-×4'd the selected
       period's EPS/DPS, assuming quarterly. Broker-model uploads carry BOTH
       quarterly ("2026Q2") and annual ("2026") periods (data/consensus
       normalize_period), so selecting an annual period seeded Base EPS at 4×
       the annual figure — propagating into DCF fair value, warranted price,
       the blended verdict and IRR.
  #7 — the Model-vs-Consensus EPS row always compared model-annual ÷ 4 against
       the selected period, so an annual period produced a confident wrong
       Δ≈−75% "Below consensus" verdict.

Both sites now route through _consensus_annualizer(period): quarterly → 4.0,
annual → 1.0, unrecognized label → None (skip the pre-fill / EPS row entirely —
n/a over a guessed basis).
"""
import sys
import types
import unittest
from pathlib import Path

# Stub streamlit before importing ui modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
# ui.valuation_model decorates its panel with @st.fragment.
_st.fragment = _st.cache_data
# Another suite's leaner stub may already be registered (setdefault keeps the
# first one) — backfill the attributes this import chain needs onto it, so the
# suite passes standalone AND alongside the other stub-based suites.
_reg = sys.modules.setdefault("streamlit", _st)
for _attr in ("cache_data", "cache_resource", "fragment"):
    if not hasattr(_reg, _attr):
        setattr(_reg, _attr, _st.cache_data)

from ui.valuation_model import _consensus_annualizer, _derive_defaults  # noqa: E402

_SRC = (Path(__file__).parent.parent / "ui" /
        "valuation_model.py").read_text(encoding="utf-8")


class TestConsensusAnnualizer(unittest.TestCase):
    """The period-basis decision: ×4 quarterly, ×1 annual, None unknown."""

    def test_quarterly_period_annualizes_x4(self):
        for period in ("2026Q1", "2026Q2", "2025Q4"):
            self.assertEqual(_consensus_annualizer(period), 4.0, period)

    def test_annual_period_used_as_is(self):
        for period in ("2026", "2025"):
            self.assertEqual(_consensus_annualizer(period), 1.0, period)

    def test_unknown_period_returns_none_never_a_guess(self):
        # Legacy free-text labels that normalize_period passes through
        # unchanged — the basis is unknowable, so no multiplier.
        for period in ("1H26", "2026-H1", "2026Q5", "NTM", "", None):
            self.assertIsNone(_consensus_annualizer(period), repr(period))

    def test_whitespace_and_case_tolerated(self):
        self.assertEqual(_consensus_annualizer(" 2026q2 "), 4.0)
        self.assertEqual(_consensus_annualizer(" 2026 "), 1.0)


class TestPrefillMathPinned(unittest.TestCase):
    """The exact pre-fill arithmetic (#6), applied as the render code does:
    base_eps = eps × annualizer; payout = dps × annualizer / base_eps."""

    def _prefill(self, period, eps, dps):
        annualize = _consensus_annualizer(period)
        if annualize is None:
            return None
        base_eps = eps * annualize
        return base_eps, min(0.95, dps * annualize / base_eps)

    def test_quarterly_consensus_annualized(self):
        # $0.80/qtr EPS, $0.25/qtr DPS → $3.20 annual, 31.25% payout.
        base_eps, payout = self._prefill("2026Q2", 0.80, 0.25)
        self.assertAlmostEqual(base_eps, 3.20)
        self.assertAlmostEqual(payout, 0.3125)

    def test_annual_consensus_used_as_is(self):
        # $3.20 annual EPS must seed 3.20, NOT 12.80 (the #6 bug).
        base_eps, payout = self._prefill("2026", 3.20, 1.00)
        self.assertAlmostEqual(base_eps, 3.20)
        self.assertAlmostEqual(payout, 0.3125)

    def test_unknown_period_prefills_nothing(self):
        self.assertIsNone(self._prefill("1H26", 3.20, 1.00))


class TestRenderSitesUseAnnualizer(unittest.TestCase):
    """Both render sites must route through _consensus_annualizer — pins that
    neither regresses to a blanket ×4 (source inspection, house pattern)."""

    def test_no_blanket_x4_prefill_remains(self):
        self.assertNotIn('float(m["value"]) * 4', _SRC,
                         "pre-fill must scale by _consensus_annualizer, not ×4")
        self.assertNotIn("model_eps_annual_y1 / 4", _SRC,
                         "model EPS must scale by the period's annualizer")

    def test_both_sites_call_the_annualizer(self):
        # 1 def + the pre-fill site + the Model-vs-Consensus site.
        self.assertGreaterEqual(_SRC.count("_consensus_annualizer("), 3)

    def test_unknown_basis_skips_model_eps_row(self):
        # The Model-vs-Consensus EPS value must be gated on the annualizer so
        # an unknown-basis period yields no row instead of a wrong verdict.
        self.assertIn("if (model_eps_annual_y1 and annualize) else None", _SRC)


class TestNoPlaceholderSeed(unittest.TestCase):
    """AUDIT-2026-07-02 #30 — the model must not seed a $2 EPS / $20 TBV
    placeholder when SEC fundamentals are missing (a confident DCF / warranted
    price off made-up inputs). Missing → None (empty input); the render path
    then refuses to compute a headline until real values exist."""

    # A minimal FDIC history: goodwill-adjusted TCE = EQTOT − INTANGW =
    # 1,000,000 − 200,000 = 800,000 ($000) = $800M.
    _HIST = [{"REPDTE": "20260331", "EQTOT": 1_000_000, "INTANGW": 200_000,
              "NETINC": 30_000, "LNLSNET": 5_000_000}]

    def test_missing_sec_yields_none_not_placeholder(self):
        d = _derive_defaults("XYZ", self._HIST, {})   # no SEC data
        self.assertIsNone(d["base_eps"],
                          "missing EPS must be None, never a $2 placeholder")
        self.assertIsNone(d["tbvps"],
                          "no share count must be None, never a $20 placeholder")

    def test_present_sec_yields_real_values(self):
        sec = {"eps": 3.50, "shares_outstanding": 40_000_000}
        d = _derive_defaults("XYZ", self._HIST, sec)
        self.assertAlmostEqual(d["base_eps"], 3.50)
        # $800M TCE / 40M shares = $20.00 / share (hand-computed).
        self.assertAlmostEqual(d["tbvps"], 20.00)


class TestHeadlineGuardOnMissingInputs(unittest.TestCase):
    """The render path must stop with an honest message (never a fabricated
    verdict) when EPS or TBV/share is missing — source-inspection, house
    pattern (the panel is a Streamlit fragment, not unit-renderable)."""

    def test_no_fabricated_placeholder_defaults_remain(self):
        self.assertNotIn("or 2.0)", _SRC,
                         "Base EPS input must not fall back to a $2 placeholder")
        self.assertNotIn("or 20.0)", _SRC,
                         "TBV/share input must not fall back to a $20 placeholder")

    def test_guard_returns_before_computing_headline(self):
        self.assertIn("if base_eps is None or tbvps is None:", _SRC,
                      "missing headline inputs must be guarded before the DCF run")


if __name__ == "__main__":
    unittest.main()
