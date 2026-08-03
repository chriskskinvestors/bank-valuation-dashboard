"""
(2026-08-02) The Geographic branch map opened at a hardcoded zoom=3 for every
view, so selecting one county, one MSA or one community bank rendered the whole
continental US with a speck on it. Company ▸ Market Analysis uses st.map, which
frames its points automatically — hence "the Geographic map sucks compared to
the Market Analysis one".

Pins:
  1. the viewport CENTRES on the plotted branches, not on the country;
  2. a tight cluster zooms IN; a nationwide network stays wide;
  3. a single branch is bounded (no rooftop zoom, where a map says nothing);
  4. degenerate/empty input falls back to the continental view instead of
     raising or producing NaN;
  5. the per-bank legend is dropped once a market has too many banks to read.

Run: python -m unittest tests.test_geo_map_viewport
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

import pandas as pd  # noqa: E402

from ui.geo_view import _fit_viewport  # noqa: E402


def _s(vals):
    return pd.Series(vals, dtype="float64")


class TestViewportCentres(unittest.TestCase):
    def test_centre_is_the_midpoint_of_the_branches(self):
        c, _ = _fit_viewport(_s([40.0, 42.0]), _s([-90.0, -88.0]))
        self.assertAlmostEqual(c["lat"], 41.0, places=6)
        self.assertAlmostEqual(c["lon"], -89.0, places=6)

    def test_iowa_cluster_is_not_centred_on_kansas(self):
        """The old behaviour framed the country regardless of the data."""
        c, z = _fit_viewport(_s([41.6, 42.0, 41.9]), _s([-93.6, -93.2, -93.4]))
        self.assertGreater(c["lat"], 40.0)
        self.assertLess(c["lon"], -92.0)
        self.assertGreater(z, 3.0, "a one-metro bank must zoom in past US view")


class TestZoomScalesWithSpread(unittest.TestCase):
    def test_tight_cluster_zooms_in_further_than_a_wide_one(self):
        _, tight = _fit_viewport(_s([41.5, 41.7]), _s([-93.7, -93.5]))
        _, wide = _fit_viewport(_s([32.0, 47.0]), _s([-120.0, -71.0]))
        self.assertGreater(tight, wide)

    def test_nationwide_network_stays_wide(self):
        _, z = _fit_viewport(_s([25.8, 47.6]), _s([-122.3, -71.0]))
        self.assertLessEqual(z, 4.5)
        self.assertGreaterEqual(z, 3.0)

    def test_single_branch_is_bounded(self):
        _, z = _fit_viewport(_s([41.6]), _s([-93.6]))
        self.assertLessEqual(z, 11.0, "must not zoom to rooftop level")
        self.assertGreaterEqual(z, 3.0)

    def test_zoom_never_below_the_continental_floor(self):
        _, z = _fit_viewport(_s([-33.0, 60.0]), _s([-170.0, 150.0]))
        self.assertGreaterEqual(z, 3.0)


class TestDegenerateInput(unittest.TestCase):
    def test_empty_falls_back_to_continental_us(self):
        c, z = _fit_viewport(_s([]), _s([]))
        self.assertAlmostEqual(c["lat"], 39.5, places=3)
        self.assertAlmostEqual(c["lon"], -98.35, places=3)
        self.assertAlmostEqual(z, 3.0, places=3)

    def test_nan_input_falls_back_rather_than_producing_nan_zoom(self):
        c, z = _fit_viewport(_s([float("nan")]), _s([float("nan")]))
        self.assertAlmostEqual(c["lat"], 39.5, places=3)
        self.assertEqual(z, 3.0)

    def test_identical_points_do_not_divide_by_zero(self):
        c, z = _fit_viewport(_s([41.6, 41.6]), _s([-93.6, -93.6]))
        self.assertAlmostEqual(c["lat"], 41.6, places=6)
        self.assertTrue(3.0 <= z <= 11.0)


class TestLegendSuppression(unittest.TestCase):
    """Structural: the legend must drop out once it can't be read."""

    def test_render_map_gates_the_legend_on_series_count(self):
        src = (REPO / "ui/geo_view.py").read_text(encoding="utf-8")
        self.assertIn("showlegend=n_series <= 15", src)

    def test_viewport_is_actually_passed_to_the_figure(self):
        src = (REPO / "ui/geo_view.py").read_text(encoding="utf-8")
        self.assertIn("center=center", src)
        self.assertIn("zoom=zoom", src)
        self.assertNotIn("zoom=3,", src, "the hardcoded zoom must not return")


if __name__ == "__main__":
    unittest.main()
