"""REVIEW 2026-10-05 Interest Rate Risk P2s.

* The NIM-vs-slope scatter plotted YTD NIMY against the curve slope AT each
  quarter-end — a Q3 point carried Q1–Q2's margins. It plots the
  single-quarter NIMYQ.
* The Fed Funds card is labeled "effective federal funds rate" and cites
  FRED DFF (daily), but read FEDFUNDS (the monthly average).

Run: python -m unittest tests.test_irr_nim_and_fed_funds
"""
import types
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from tests import _streamlit_stub

_streamlit_stub.install()

import ui.rate_sensitivity as R  # noqa: E402


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestScatterSingleQuarterNim(unittest.TestCase):
    def test_points_are_nimyq_not_ytd(self):
        hist = [{"REPDTE": f"{y}{md}", "NIMY": 9.0, "NIMYQ": 3.0 + i / 100}
                for i, (y, md) in enumerate((y, md) for y in (2024, 2025)
                                            for md in ("0331", "0630", "0930", "1231"))]
        dates = pd.date_range("2023-01-01", "2026-01-31", freq="D")
        ser = pd.DataFrame({"date": dates, "value": 4.0})
        figs = []
        st = types.SimpleNamespace(plotly_chart=lambda f, **k: figs.append(f),
                                   markdown=lambda *a, **k: None,
                                   caption=lambda *a, **k: None)
        with mock.patch.object(R, "st", st), \
                mock.patch("data.fred_client.fetch_series", lambda s, years=6: ser.copy()), \
                mock.patch("data.fred_client.latest_value", lambda s: 4.0):
            R._render_historical_nim_scatter(hist)
        self.assertEqual(len(figs), 1)
        ys = sorted(float(v) for v in figs[0].data[0].y)
        self.assertEqual(ys, sorted(3.0 + i / 100 for i in range(8)))   # never 9.0


class TestFedFundsCardReadsDaily(unittest.TestCase):
    def test_card_reads_the_series_it_cites(self):
        src = (Path(__file__).resolve().parent.parent / "ui"
               / "rate_sensitivity.py").read_text(encoding="utf-8")
        self.assertIn('ff = latest_value("DFF")', src)
        self.assertNotIn('latest_value("FEDFUNDS")', src)


class TestGrowthAcquisitionNote(unittest.TestCase):
    """HBAN loans +40.9% YoY (the Cadence deal) fed the volume projection
    unflagged (REVIEW 2026-10-05 IRR P2)."""
    HIST = [{"REPDTE": "20260630"}, {"REPDTE": "20260331"}]

    def _note(self, deals):
        with mock.patch("ui.history_range.group_acquisitions", lambda t, c=None: deals):
            return R._acquisition_note("HBAN", 6560, self.HIST)

    def test_deal_inside_the_trailing_year_is_flagged(self):
        n = self._note([{"date": "2026-02-01", "target_name": "Cadence Bank"}])
        self.assertIn("Cadence Bank (Feb 2026)", n)
        self.assertIn("not organic", n)

    def test_deal_outside_the_window_or_none_is_silent(self):
        self.assertEqual(self._note([{"date": "2025-06-30", "target_name": "Old"}]), "")
        self.assertEqual(self._note([]), "")


if __name__ == "__main__":
    unittest.main()
