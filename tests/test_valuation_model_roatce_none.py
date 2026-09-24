"""
The Valuation Model never seeds a placeholder ROATCE (REVIEW-2026-09-24 P0-1).

_derive_defaults used to fall back to a hard-coded 12.0 when neither the SEC
holdco figure nor the FDIC sub-bank blend resolved; the seed card then showed
"12.00%" under the label "trailing-4Q ROATCE from FDIC" and the placeholder
drove the Warranted P/TBV headline. Missing → None, exactly like base_eps and
tbvps (AUDIT-2026-07-02 #30); the render path refuses a headline until a real
(derived or typed) value exists.
"""
import unittest

from tests import _streamlit_stub

_streamlit_stub.install()

from ui.valuation_model import _derive_defaults  # noqa: E402


class TestRoatceSeedNeverPlaceholder(unittest.TestCase):
    # Four consecutive quarters with NO net income or equity: nothing to
    # compute a return on. Loans present so the other seeds still derive.
    _HIST_NO_RETURN = [
        {"REPDTE": "20260630", "LNLSNET": 5_000_000},
        {"REPDTE": "20260331", "LNLSNET": 4_900_000},
        {"REPDTE": "20251231", "LNLSNET": 4_800_000},
        {"REPDTE": "20250930", "LNLSNET": 4_700_000},
        {"REPDTE": "20250630", "LNLSNET": 4_600_000},
    ]

    def test_unresolvable_roatce_is_none(self):
        d = _derive_defaults("XYZ", self._HIST_NO_RETURN, {})
        self.assertIn("roatce_pct", d)
        self.assertIsNone(d["roatce_pct"], "missing ROATCE must be None, never 12.0")

    def test_resolvable_roatce_is_the_fdic_figure(self):
        # Latest quarter: Q2 YTD NI 200 ($000), Q1 YTD 100 → quarterly 100;
        # compute_roatce annualizes the latest YTD: 200 × (4/2) = 400 over
        # TCE 10,000 − 0 → 4.0 %. With only two quarters the 4Q figure is None,
        # so the blend is the current-quarter figure alone (hand-computed).
        hist = [
            {"REPDTE": "20260630", "NETINC": 200.0, "EQTOT": 10_000.0, "INTAN": 0.0,
             "LNLSNET": 5_000_000},
            {"REPDTE": "20260331", "NETINC": 100.0, "EQTOT": 10_000.0, "INTAN": 0.0,
             "LNLSNET": 4_900_000},
        ]
        d = _derive_defaults("XYZ", hist, {})
        self.assertIsNotNone(d["roatce_pct"])
        self.assertAlmostEqual(d["roatce_pct"], 4.0, places=6)


if __name__ == "__main__":
    unittest.main()
