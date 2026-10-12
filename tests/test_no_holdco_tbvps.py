"""Derived TBVPS for a non-SEC bank with no holding company (owner decision
2026-10-12, PFBC).

Preferred Bank files with the FDIC, has no holding company, and its earnings
release states book value per share ($66.95, 2Q26) but no tangible figure, so
P/TBV was n/a. With no holdco the FDIC call report IS the company's balance
sheet: TBVPS = release BVPS x (EQTOT - INTAN) / EQTOT, served only when FDIC
shows no high holder and no preferred, FDIC's quarter is the release's, and
the release's own total equity ties to EQTOT within 1%.

PFBC 2Q26 (FDIC cert 33539): EQTOT 793,527K, INTAN 766K, EQPP 0, RSSDHCR null;
release total shareholders' equity $793,527K, BVPS $66.95.
Hand-computed: 66.95 x (793,527 - 766) / 793,527 = 66.95 - 0.064628 = 66.8854.
"""
import unittest
from datetime import date, timedelta

import pandas as pd

from tests import _streamlit_stub

_streamlit_stub.install()

import analysis.valuation as va  # noqa: E402
from data.release_shares import extract_total_equity  # noqa: E402

QEND = (date.today() - timedelta(days=30)).isoformat()
EXPECTED = 66.95 * (793_527 - 766) / 793_527


def _fdic(**over):
    rec = {"REPDTE": QEND.replace("-", ""), "EQTOT": 793_527.0,
           "INTAN": 766.0, "EQPP": 0.0, "RSSDHCR": None}
    rec.update(over)
    return rec


def _envelope(**over):
    env = {"qend": QEND, "metrics": {"bv_ps": 66.95, "tbv_ps": None},
           "equity_cands": [793_527_000.0]}
    env.update(over)
    return env


class _Base(unittest.TestCase):
    def setUp(self):
        import data.bank_mapping as bm
        import data.otc_release as orl
        self.bm, self.orl = bm, orl
        self._orig = (bm.get_cik, orl.otc_release_metrics)
        bm.get_cik = lambda t: None
        self.env = _envelope()
        orl.otc_release_metrics = lambda t, allow_fetch=True: self.env

    def tearDown(self):
        self.bm.get_cik, self.orl.otc_release_metrics = self._orig


class TestDerivedNoHoldco(_Base):

    def test_pfbc_value(self):
        self.assertAlmostEqual(EXPECTED, 66.8854, places=4)
        self.assertAlmostEqual(va._derived_tbvps_no_holdco("PFBC", _fdic()),
                               EXPECTED, places=9)

    def test_repdte_forms(self):
        for rep in (pd.Timestamp(QEND), QEND):
            self.assertAlmostEqual(
                va._derived_tbvps_no_holdco("PFBC", _fdic(REPDTE=rep)),
                EXPECTED, places=9)

    def test_nan_rssdhcr_is_no_holdco(self):
        # pd.to_numeric(None) in the coerced record path
        self.assertIsNotNone(va._derived_tbvps_no_holdco(
            "PFBC", _fdic(RSSDHCR=float("nan"))))

    def test_refusals(self):
        cases = {
            "has holdco": _fdic(RSSDHCR=5718627.0),
            "preferred": _fdic(EQPP=338_980.0),
            "preferred unknown": _fdic(EQPP=None),
            "intangibles unknown": _fdic(INTAN=float("nan")),
            "other quarter": _fdic(REPDTE="20250630"),
            "no equity": _fdic(EQTOT=0.0),
        }
        for label, rec in cases.items():
            self.assertIsNone(va._derived_tbvps_no_holdco("PFBC", rec), label)

    def test_release_side_refusals(self):
        cases = {
            "no bvps": _envelope(metrics={"bv_ps": None}),
            "equity off >1%": _envelope(equity_cands=[805_000_000.0]),
            "no equity row": _envelope(equity_cands=[]),
            "old envelope": {"qend": QEND, "metrics": {"bv_ps": 66.95}},
        }
        for label, env in cases.items():
            self.env = env
            self.assertIsNone(va._derived_tbvps_no_holdco("PFBC", _fdic()),
                              label)


class TestResolveTbvpsWiring(_Base):

    def test_served_with_its_own_source(self):
        self.assertEqual(
            va._resolve_tbvps("PFBC", None, None, fdic=_fdic())[1:],
            ("derived_no_holdco", False))

    def test_release_tbvps_still_wins(self):
        self.env = _envelope(metrics={"bv_ps": 66.95, "tbv_ps": 66.10})
        self.assertEqual(va._resolve_tbvps("PFBC", None, None, fdic=_fdic()),
                         (66.10, "company_release", False))

    def test_never_for_sec_banks_or_without_fdic(self):
        self.assertEqual(va._resolve_tbvps("PFBC", None, None),
                         (None, None, False))
        self.bm.get_cik = lambda t: 12345
        import data.sec_earnings_8k as s8
        orig = s8.reported_tbvps_status
        s8.reported_tbvps_status = lambda cik, **k: (None, "not_found")
        try:
            self.assertEqual(
                va._resolve_tbvps("XYZ", None, None, fdic=_fdic())[1], None)
        finally:
            s8.reported_tbvps_status = orig


class TestExtractTotalEquity(unittest.TestCase):

    def test_period_proven_row_at_stated_scale(self):
        html = ("<table><tr><td>(in thousands)</td><td>June 30, 2026</td>"
                "<td>March 31, 2026</td></tr>"
                "<tr><td>Total liabilities and shareholders' equity</td>"
                "<td>$ 7,730,621</td><td>$ 7,654,637</td></tr>"
                "<tr><td>Total shareholders' equity</td><td>$ 793,527</td>"
                "<td>$ 770,199</td></tr></table>")
        self.assertEqual(extract_total_equity(html, "2026-06-30"),
                         [793_527_000.0])
        self.assertEqual(extract_total_equity(html, "2026-03-31"),
                         [770_199_000.0])
        self.assertEqual(extract_total_equity(html, "2025-12-31"), [])


if __name__ == "__main__":
    unittest.main()
