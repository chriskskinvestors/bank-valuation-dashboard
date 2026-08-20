"""(2026-08-19) Release-first increment 2: composite release-anchored TTM EPS
(owner decision — the site's EPS stays TTM-basis; the P/E ~4x trap forbids a
lone release quarter, so the release quarter is COMPOSED with the prior three
discrete Company-Reported quarters).

Pinned here:
- composite_ttm_eps assembly: release quarter eps_diluted (period-proofed
  extractor) + 3 discrete CR quarters -> the sum; provenance components.
- The fiscal-Q4 fill: the CR stitch structurally refuses per-share Q4 cells
  (differencing is display-forbidden), so that one component fills from the
  XBRL quarterly construction (same filings, same FY-9M convention the XBRL
  TTM anchor itself uses) — per-quarter source recorded.
- The A21 refusal: any missing component (a 3-quarter "TTM") -> None.
- Stale-release refusal: release qend != the expected most-recent completed
  quarter -> None (and the statement stitch is never even fetched).
- _resolve_eps: band agreement -> composite serves as "release_ttm";
  >=15% divergence -> the RELEASE-QUARTER plausibility gate decides (the two
  TTMs share three quarters, so in-window the disagreement reduces to the
  release quarter vs the dropped year-ago quarter): release quarter within
  0.25x-4x of the shared-quarter median -> genuine YoY swing, composite
  serves (the ONB Bremer-merger shape: composite 2.26 vs stale XBRL 1.95);
  outside the band, or components missing -> XBRL TTM serves +
  eps_conflict=True (the mis-extraction shape); composite absent
  -> XBRL TTM as "reconstructed"; nothing -> (None, None, False).
- The release-internal tie-out (second gate, 2026-08-20): when the release
  also printed NI applicable to common + weighted average diluted shares,
  their ratio must reproduce the extracted EPS (scale-agnostic — the count
  tried at x1/x1e3/x1e6, sec_earnings_8k._internal_tie_out discipline). A
  contradiction VETOES a median-gate pass (conservative conflict path); a
  tie is additional confirmation; absent inputs change nothing.
- PERF GUARD: outside the release->10-Q window (latest earnings 8-K covers a
  quarter <= sec_as_of) the composite is never attempted — zero added
  fetches in the 440-bank screen build (call-counted via mocks).
- Wiring: build_bank_metrics passes eps/eps_source/eps_conflict through;
  validation errors on eps_conflict.

Run: python -m unittest tests.test_release_first_eps
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

import analysis.release_eps as re_mod  # noqa: E402
import analysis.valuation as va  # noqa: E402
import data.release_metrics as drm  # noqa: E402
import data.sec_statements as dss  # noqa: E402
from data import validation  # noqa: E402
from data.ir_provider import _quarter_end_before  # noqa: E402


# The expected most-recent completed quarter as of today — the only qend a
# fresh release may carry (kept dynamic so the suite never date-rots).
EXPECTED = _quarter_end_before(date.today().isoformat())


def _priors(qend, n=3):
    """[(label, iso_end), ...] for the n quarters before qend."""
    return re_mod._prior_quarters(qend, n)


def _release(qend=None, eps=2.10, accession="0000000001-26-000001"):
    return {"qend": qend or EXPECTED, "accession": accession,
            "url": "https://example/8k", "filed_date": "2026-01-01",
            "metrics": {"eps_diluted": eps}}


def _stitch(labels, eps_by_label, extra_periods=()):
    """A minimal as_reported_statement_multiquarter result: one diluted-EPS
    row over the given period labels (newest-first)."""
    periods = list(extra_periods) + list(labels)
    values = [eps_by_label.get(p) for p in periods]
    return {"meta": {}, "filings": [],
            "statement": {"periods": periods,
                          "rows": [{"label": "Diluted earnings per share",
                                    "values": values, "header": False},
                                   {"label": "PER SHARE DATA",
                                    "values": [None] * len(periods),
                                    "header": True}]}}


class TestCompositeAssembly(unittest.TestCase):
    def setUp(self):
        re_mod._MEMO.clear()

    def test_four_components_sum(self):
        labels = [lab for lab, _ in _priors(EXPECTED)]
        stitch = _stitch(labels, {labels[0]: 0.52, labels[1]: 0.48,
                                  labels[2]: 0.50})
        with patch.object(drm, "release_metrics",
                          return_value=_release(eps=0.55)), \
             patch.object(dss, "as_reported_statement_multiquarter",
                          return_value=stitch), \
             patch.object(re_mod, "_xbrl_quarterly_eps") as xq:
            v, qend, comp = re_mod.composite_ttm_eps(999)
        xq.assert_not_called()  # all three CR quarters present → no fill
        self.assertAlmostEqual(v, 0.55 + 0.52 + 0.48 + 0.50, places=9)
        self.assertEqual(qend, EXPECTED)
        self.assertEqual(comp["release"]["eps"], 0.55)
        self.assertEqual(comp["quarters"],
                         {labels[0]: 0.52, labels[1]: 0.48, labels[2]: 0.50})
        self.assertEqual(set(comp["quarter_sources"].values()),
                         {"company_reported"})

    def test_fiscal_q4_gap_fills_from_xbrl_quarters(self):
        """The CR stitch never carries a per-share fiscal-Q4 cell — that one
        component fills from the XBRL quarterly construction (same filings,
        same FY−9M convention as the XBRL TTM anchor)."""
        priors = _priors(EXPECTED)
        labels = [lab for lab, _ in priors]
        gap_label, gap_iso = priors[1]
        stitch = _stitch(labels, {labels[0]: 0.52, labels[2]: 0.50})
        with patch.object(drm, "release_metrics",
                          return_value=_release(eps=0.55)), \
             patch.object(dss, "as_reported_statement_multiquarter",
                          return_value=stitch), \
             patch.object(re_mod, "_xbrl_quarterly_eps",
                          return_value={gap_iso: 0.47}):
            v, qend, comp = re_mod.composite_ttm_eps(999)
        self.assertAlmostEqual(v, 0.55 + 0.52 + 0.47 + 0.50, places=9)
        self.assertEqual(comp["quarter_sources"][gap_label], "xbrl")
        self.assertEqual(comp["quarter_sources"][labels[0]],
                         "company_reported")

    def test_three_components_refused(self):
        """A21 defect class: a 3-quarter sum must never be labeled TTM —
        a gap neither the CR stitch nor the XBRL quarters can fill refuses
        the whole composite, never a partial sum."""
        labels = [lab for lab, _ in _priors(EXPECTED)]
        stitch = _stitch(labels, {labels[0]: 0.52, labels[2]: 0.50})  # gap
        with patch.object(drm, "release_metrics",
                          return_value=_release(eps=0.55)), \
             patch.object(dss, "as_reported_statement_multiquarter",
                          return_value=stitch), \
             patch.object(re_mod, "_xbrl_quarterly_eps", return_value={}):
            self.assertEqual(re_mod.composite_ttm_eps(999),
                             (None, None, None))

    def test_stale_release_refused_without_fetching_statements(self):
        """A release covering an OLD quarter (bank not yet reported / stopped
        reporting) is refused — and cheaply: the stitch is never fetched."""
        prior = _quarter_end_before(EXPECTED)  # one quarter older
        with patch.object(drm, "release_metrics",
                          return_value=_release(qend=prior)), \
             patch.object(dss, "as_reported_statement_multiquarter") as mq:
            self.assertEqual(re_mod.composite_ttm_eps(999),
                             (None, None, None))
        mq.assert_not_called()

    def test_release_without_eps_refused(self):
        with patch.object(drm, "release_metrics",
                          return_value=_release(eps=None)), \
             patch.object(dss, "as_reported_statement_multiquarter") as mq:
            self.assertEqual(re_mod.composite_ttm_eps(999),
                             (None, None, None))
        mq.assert_not_called()

    def test_memo_serves_second_call_without_reassembly(self):
        labels = [lab for lab, _ in _priors(EXPECTED)]
        stitch = _stitch(labels, {labels[0]: 0.52, labels[1]: 0.48,
                                  labels[2]: 0.50})
        with patch.object(drm, "release_metrics",
                          return_value=_release(eps=0.55)), \
             patch.object(dss, "as_reported_statement_multiquarter",
                          return_value=stitch) as mq:
            first = re_mod.composite_ttm_eps(999)
            second = re_mod.composite_ttm_eps(999)
        self.assertEqual(first, second)
        self.assertEqual(mq.call_count, 1)


class _ResolveBase(unittest.TestCase):
    """_resolve_eps with the perf-guard window OPEN (8-K quarter newer than
    sec_as_of) unless a test overrides the guard inputs."""

    def setUp(self):
        import data.bank_mapping as bm
        import data.sec_earnings_8k as s8k
        re_mod._MEMO.clear()
        self.bm, self.s8k = bm, s8k
        self._orig = (bm.get_cik, s8k._latest_earnings_8k)
        bm.get_cik = lambda t: 999
        # An 8-K filed after EXPECTED's quarter closed -> covers EXPECTED.
        s8k._latest_earnings_8k = lambda cik: {
            "accession": "x", "date": date.today().isoformat()}

    def tearDown(self):
        (self.bm.get_cik, self.s8k._latest_earnings_8k) = self._orig


def _components(rel_eps, quarters, ni_common=None, dil_shares=None):
    """A composite provenance dict in the composite_ttm_eps shape: release
    quarter EPS + the three shared prior quarters (newest-first). ni_common
    / dil_shares default absent, as for a release whose tables never
    printed them — the tie-out then cannot speak (PR #54 behavior)."""
    labels = [lab for lab, _ in _priors(EXPECTED)]
    return {"release": {"qend": EXPECTED, "eps": rel_eps,
                        "ni_common": ni_common, "dil_shares": dil_shares,
                        "accession": "x", "url": "u"},
            "quarters": dict(zip(labels, quarters)),
            "quarter_sources": {}}


class TestResolveEps(_ResolveBase):
    # sec_as_of one quarter behind the release -> window open.
    AS_OF = _quarter_end_before(EXPECTED)

    def test_band_agreement_serves_release_ttm(self):
        with patch.object(re_mod, "composite_ttm_eps",
                          return_value=(2.05, EXPECTED, {})):
            self.assertEqual(va._resolve_eps("JPM", 2.00, self.AS_OF),
                             (2.05, "release_ttm", False))

    def test_genuine_swing_serves_composite_beyond_band(self):
        """The ONB shape (2Q26, Bremer-merger quarter, hand-verified against
        the release 2026-08-20): composite 2.26 (release 0.65 + shared
        0.59/0.56/0.46) vs stale XBRL TTM 1.95 — 15.9% apart, but the
        release quarter is plausible against the shared-quarter median
        (0.65 / 0.56 = 1.16x), so the genuine YoY swing serves as
        release_ttm without conflict."""
        comp = _components(0.65, [0.59, 0.56, 0.46])
        with patch.object(re_mod, "composite_ttm_eps",
                          return_value=(2.26, EXPECTED, comp)):
            self.assertEqual(va._resolve_eps("ONB", 1.95, self.AS_OF),
                             (2.26, "release_ttm", False))

    def test_misextracted_release_quarter_conflicts(self):
        """The mis-extraction shape: a release quarter 10x the shared-quarter
        median (a YTD/aggregate grabbed into the EPS slot) fails the
        plausibility gate — the XBRL TTM serves, flagged."""
        comp = _components(5.00, [0.52, 0.50, 0.48])
        with patch.object(re_mod, "composite_ttm_eps",
                          return_value=(6.50, EXPECTED, comp)):
            self.assertEqual(va._resolve_eps("JPM", 2.00, self.AS_OF),
                             (2.00, "reconstructed", True))

    def test_tie_out_confirms_genuine_swing(self):
        """ONB 2Q26 (real figures from onb_exhibit991er2q26.htm): NI
        applicable to common $249,381K ÷ 383,273K weighted average diluted
        shares = $0.6507 vs extracted 0.65 — the release's own arithmetic
        confirms the quarter (at the ×1e3 share scale), so the divergent
        composite serves with the median gate's pass doubly confirmed."""
        comp = _components(0.65, [0.59, 0.56, 0.46],
                           ni_common=249_381_000.0, dil_shares=383_273.0)
        with patch.object(re_mod, "composite_ttm_eps",
                          return_value=(2.26, EXPECTED, comp)):
            self.assertEqual(va._resolve_eps("ONB", 1.95, self.AS_OF),
                             (2.26, "release_ttm", False))

    def test_tie_out_contradiction_vetoes_median_pass(self):
        """The median gate passes (0.65 vs median 0.56) but the release's
        own NI/shares arithmetic gives $1.30 at every plausible scale — a
        contradiction INSIDE the release vetoes: one of the three figures
        was mis-extracted, so the XBRL TTM serves, flagged (the owner-
        recommended conservative conflict path)."""
        comp = _components(0.65, [0.59, 0.56, 0.46],
                           ni_common=498_762_000.0, dil_shares=383_273.0)
        with patch.object(re_mod, "composite_ttm_eps",
                          return_value=(2.26, EXPECTED, comp)):
            self.assertEqual(va._resolve_eps("ONB", 1.95, self.AS_OF),
                             (1.95, "reconstructed", True))

    def test_tie_out_inputs_absent_median_gate_alone_decides(self):
        """No NI/share rows in the release → the tie-out cannot speak and
        the median gate decides alone (the PR #54 behavior, unchanged)."""
        comp = _components(0.65, [0.59, 0.56, 0.46])  # ni/shares absent
        with patch.object(re_mod, "composite_ttm_eps",
                          return_value=(2.26, EXPECTED, comp)):
            self.assertEqual(va._resolve_eps("ONB", 1.95, self.AS_OF),
                             (2.26, "release_ttm", False))

    def test_divergence_without_components_stays_conservative(self):
        """>=15% apart and no provenance components to gate on: the gate
        cannot prove plausibility — the XBRL TTM serves, flagged."""
        with patch.object(re_mod, "composite_ttm_eps",
                          return_value=(3.00, EXPECTED, {})):
            self.assertEqual(va._resolve_eps("JPM", 2.00, self.AS_OF),
                             (2.00, "reconstructed", True))

    def test_composite_serves_when_xbrl_absent(self):
        with patch.object(re_mod, "composite_ttm_eps",
                          return_value=(2.05, EXPECTED, {})):
            self.assertEqual(va._resolve_eps("PNSB", None, None),
                             (2.05, "release_ttm", False))

    def test_xbrl_fallback_when_composite_cannot_assemble(self):
        with patch.object(re_mod, "composite_ttm_eps",
                          return_value=(None, None, None)):
            self.assertEqual(va._resolve_eps("JPM", 2.00, self.AS_OF),
                             (2.00, "reconstructed", False))

    def test_nothing_available_is_none(self):
        with patch.object(re_mod, "composite_ttm_eps",
                          return_value=(None, None, None)):
            self.assertEqual(va._resolve_eps("JPM", None, self.AS_OF),
                             (None, None, False))

    def test_negative_xbrl_band_uses_absolute_value(self):
        """Loss-making bank: -1.00 vs -1.05 is within 15%, composite serves."""
        with patch.object(re_mod, "composite_ttm_eps",
                          return_value=(-1.05, EXPECTED, {})):
            self.assertEqual(va._resolve_eps("X", -1.00, self.AS_OF),
                             (-1.05, "release_ttm", False))


class TestReleaseQuarterGate(unittest.TestCase):
    """_release_quarter_plausible edge cases — anything unprovable refuses
    (the conservative conflict path), never a guess."""

    def test_low_side_refused(self):
        """A release quarter far BELOW the shared median (a cents-scale
        mis-parse) fails too — the band is two-sided."""
        self.assertFalse(va._release_quarter_plausible(
            _components(0.05, [0.52, 0.50, 0.48])))

    def test_zero_median_refused(self):
        self.assertFalse(va._release_quarter_plausible(
            _components(0.50, [0.10, 0.00, -0.10])))

    def test_missing_quarter_refused(self):
        self.assertFalse(va._release_quarter_plausible(
            _components(0.50, [0.52, None, 0.48])))

    def test_none_components_refused(self):
        self.assertFalse(va._release_quarter_plausible(None))

    def test_sign_flip_recovery_passes_on_magnitude(self):
        """Loss-making bank posting a genuine recovery quarter: magnitudes
        gate, not signed ratios."""
        self.assertTrue(va._release_quarter_plausible(
            _components(0.55, [-0.50, -0.52, -0.48])))


class TestReleaseEpsTieOut(unittest.TestCase):
    """_release_eps_tie_out three-valued semantics: True = some scale
    reproduces the extracted EPS; False = both inputs present, no scale
    does (contradiction); None = an input absent (cannot speak)."""

    QS = [0.59, 0.56, 0.46]

    def test_thousands_scale_ties(self):
        """ONB: 249,381,000 / (383,273 × 1e3) = 0.6507 vs 0.65 → ties."""
        self.assertIs(va._release_eps_tie_out(_components(
            0.65, self.QS, ni_common=249_381_000.0, dil_shares=383_273.0)),
            True)

    def test_raw_scale_ties(self):
        """Shares printed raw: 5,000,000 / 7,692,308 = 0.65 → ties at ×1."""
        self.assertIs(va._release_eps_tie_out(_components(
            0.65, self.QS, ni_common=5_000_000.0, dil_shares=7_692_308.0)),
            True)

    def test_millions_scale_ties(self):
        """Shares printed in a millions table: 249,381,000 / (383.3 × 1e6)
        = 0.6507 → ties at ×1e6."""
        self.assertIs(va._release_eps_tie_out(_components(
            0.65, self.QS, ni_common=249_381_000.0, dil_shares=383.3)),
            True)

    def test_no_scale_reproduces_is_contradiction(self):
        """NI implies $1.30 — off 2x at the near scale and 1000x at the
        rest; no scale ties → False (never rescued by scale trying)."""
        self.assertIs(va._release_eps_tie_out(_components(
            0.65, self.QS, ni_common=498_762_000.0, dil_shares=383_273.0)),
            False)

    def test_cent_rounding_floor_covers_low_eps(self):
        """Printed EPS rounds to cents: at $0.10 a true ratio of 0.1045 is
        4.5% off — inside the half-cent absolute floor, so a genuine tie is
        never false-vetoed on a low-EPS bank."""
        self.assertIs(va._release_eps_tie_out(_components(
            0.10, self.QS, ni_common=40_053_000.0, dil_shares=383_273.0)),
            True)

    def test_missing_either_input_cannot_speak(self):
        self.assertIsNone(va._release_eps_tie_out(_components(
            0.65, self.QS, ni_common=249_381_000.0)))
        self.assertIsNone(va._release_eps_tie_out(_components(
            0.65, self.QS, dil_shares=383_273.0)))
        self.assertIsNone(va._release_eps_tie_out(None))


class TestPerfGuard(_ResolveBase):
    def test_no_composite_attempt_when_8k_predates_latest_10q(self):
        """Outside the window (XBRL already covers the release quarter) the
        composite must never be attempted — zero added fetches per bank in
        the screen build."""
        with patch.object(re_mod, "composite_ttm_eps") as comp:
            out = va._resolve_eps("JPM", 2.00, EXPECTED)  # as_of == 8-K qend
        comp.assert_not_called()
        self.assertEqual(out, (2.00, "reconstructed", False))

    def test_no_composite_attempt_without_an_earnings_8k(self):
        self.s8k._latest_earnings_8k = lambda cik: None
        with patch.object(re_mod, "composite_ttm_eps") as comp:
            out = va._resolve_eps("JPM", 2.00, None)
        comp.assert_not_called()
        self.assertEqual(out, (2.00, "reconstructed", False))

    def test_window_open_when_no_xbrl_facts_at_all(self):
        """Empty-companyfacts filer (PNSB class): no sec_as_of leaves the
        composite as the only possible TTM source."""
        with patch.object(re_mod, "composite_ttm_eps",
                          return_value=(1.10, EXPECTED, {})) as comp:
            out = va._resolve_eps("PNSB", None, None)
        comp.assert_called_once()
        self.assertEqual(out, (1.10, "release_ttm", False))


class TestWiring(unittest.TestCase):
    def test_metrics_row_carries_eps_source_and_conflict(self):
        import analysis.metrics as am
        resolved = {"eps": 8.44, "eps_source": "release_ttm",
                    "eps_conflict": False, "pe_ratio": 12.0}
        with patch.object(am, "compute_all_valuations", return_value=resolved):
            row = am.build_bank_metrics("X", {}, {}, {"price": None}, [])
        self.assertEqual(row.get("eps"), 8.44)
        self.assertEqual(row.get("eps_source"), "release_ttm")
        self.assertEqual(row.get("pe_ratio"), 12.0)
        self.assertFalse(row.get("eps_conflict"))

    def test_validation_errors_on_eps_conflict(self):
        fs = validation.check_coherence_flags(
            {"eps": 2.00, "eps_conflict": True,
             "tbvps_conflict": False, "bvps_conflict": False}, {})
        self.assertEqual([(f.field, f.severity) for f in fs],
                         [("eps", "error")])


if __name__ == "__main__":
    unittest.main()
