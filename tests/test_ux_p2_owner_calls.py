"""UX review P2 owner calls (decided 2026-10-05).

  P2-21  Deposit Trends said "Uninsured deposits at 49% of total — elevated
         run risk" for JPM. Owner: neutral wording, thresholds unchanged.
  P2-24  The SEC holding-company capital block rendered on BOTH Company
         Reported › Regulatory Capital and Templated › Capital Adequacy.
         Owner: Company Reported only; Templated points to it.
  Total Assets on Corporate Profile printed "$4091.39B" — owner: keep
         billions, add the thousands separator ("$4,091.39B").
(P2-16 stock statistics are pinned in tests/test_stock_chart.py.)
"""
import unittest

import pandas as pd

from tests import _streamlit_stub

_streamlit_stub.install()

from analysis.deposit_dynamics import detect_alerts  # noqa: E402


class TestUninsuredAlertWording(unittest.TestCase):
    def _alert(self, pct):
        alerts = detect_alerts(pd.DataFrame([{"uninsured_pct": pct}]))
        return next((a for a in alerts if a["code"] == "uninsured_high"), None)

    def test_neutral_fact_not_a_verdict(self):
        a = self._alert(49.2)
        self.assertEqual(a["message"],
                         "Uninsured deposits at 49% of total — above the 40% watch level")
        self.assertNotIn("run risk", a["message"])
        self.assertEqual(a["severity"], "medium")

    def test_thresholds_unchanged(self):
        self.assertIsNone(self._alert(40.0))
        self.assertEqual(self._alert(55.1)["severity"], "high")


class TestHoldcoCapitalOnOneBasis(unittest.TestCase):
    def test_templated_links_to_company_reported(self):
        from ui.capital_dynamics import holdco_capital_pointer_html
        h = holdco_capital_pointer_html("JPM")
        self.assertIn('href="?s=Company&bank=JPM&tab=Regulatory%20Capital'
                      '&basis=Company%20Reported"', h)
        self.assertIn('target="_self"', h)

    def test_company_reported_still_renders_it(self):
        import inspect
        from ui import company_nav
        self.assertIn("_render_holdco_capital(t)",
                      inspect.getsource(company_nav._cr_reg_capital))

    def test_deep_link_basis_is_a_real_basis(self):
        # app.py honours ?basis= only when it names one of the section's bases.
        from ui.company_nav import COMPANY_NAV
        fin = next(v for v in COMPANY_NAV.values()
                   if isinstance(v, dict) and "Company Reported" in v)
        self.assertIn("Regulatory Capital", fin["Company Reported"])


class TestProfileTotalsSeparator(unittest.TestCase):
    def test_mega_bank_total_has_thousands_separator(self):
        from ui.bank_detail import _usd_b, _usd_b_thou
        self.assertEqual(_usd_b(4_091_390_000_000), "$4,091.39B")
        self.assertEqual(_usd_b_thou(4_091_390_000), "$4,091.39B")   # FDIC $K
        self.assertEqual(_usd_b(871_800_000), "$871.8M")
        self.assertEqual(_usd_b(-1_200_000_000), "-$1.20B")
        self.assertIsNone(_usd_b(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
