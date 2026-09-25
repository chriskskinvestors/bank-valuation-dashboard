"""UX-review P0 (2026-09-25): Market Demographics copy + live-yields job gate.

UX-P0-11  Market Demographics told end users to "set CENSUS_API_KEY ... in the
          environment / Secret Manager". Until the owner's key lands the copy
          is neutral — no env var names, no Secret Manager, no signup URL.
Live yields  data/live_rates.LIVE_YIELD_SYMBOLS dropped the 2Y future (5 → 4
          tenors); the job's hard-coded `got >= 4` then required every tenor.
          It now tolerates exactly one miss, as it did at 4 of 5.
"""
import os
import unittest
from unittest import mock

import pandas as pd


class TestMarketDemographicsCopy(unittest.TestCase):

    def test_missing_census_key_shows_neutral_copy(self):
        import data.census_client as CC
        import ui.branch_analytics as BA
        roster = pd.DataFrame([
            {"year": 2025, "stcntybr": "36061", "deposits": 1_000.0,
             "county": "New York", "state": "NY"},
            {"year": 2025, "stcntybr": "17031", "deposits": 500.0,
             "county": "Cook", "state": "IL"},
        ])
        out = []
        st = mock.MagicMock()
        st.caption.side_effect = lambda s, **k: out.append(s)
        st.markdown.side_effect = lambda s, **k: out.append(s)
        env = {k: v for k, v in os.environ.items() if k != "CENSUS_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(CC, "_fetch_acs", lambda *a, **k: None), \
                mock.patch.object(BA, "st", st), \
                mock.patch.object(BA, "_cert", lambda t: 628), \
                mock.patch.object(BA, "_roster", lambda c: (roster, [])), \
                mock.patch.object(BA, "table_export", mock.MagicMock()):
            BA.render_market_demographics("JPM")
        text = "\n".join(out)
        self.assertIn("Market demographics (county population, income and "
                      "growth from the U.S. Census) are coming soon.", text)
        for leak in ("CENSUS_API_KEY", "Secret Manager", "key_signup",
                     "api.census.gov"):
            self.assertNotIn(leak, text)
        st.dataframe.assert_not_called()


class TestRefreshLiveYieldsExitCode(unittest.TestCase):

    SYMS = {"3M": "^IRX", "5Y": "^FVX", "10Y": "^TNX", "30Y": "^TYX"}

    def _run(self, n_ok):
        from data import live_rates
        from jobs import refresh_live_yields
        data = {t: ((4.0, 4.0, 4.0) if i < n_ok else None)
                for i, t in enumerate(self.SYMS)}
        with mock.patch.object(live_rates, "LIVE_YIELD_SYMBOLS", self.SYMS), \
                mock.patch.object(live_rates, "refresh", lambda: data), \
                mock.patch("builtins.print"):
            return refresh_live_yields.main()

    def test_all_four_tenors_succeeds(self):
        self.assertEqual(self._run(4), 0)

    def test_three_of_four_succeeds(self):
        self.assertEqual(self._run(3), 0)

    def test_two_of_four_fails(self):
        self.assertEqual(self._run(2), 1)

    def test_none_fails(self):
        self.assertEqual(self._run(0), 1)


if __name__ == "__main__":
    unittest.main()
