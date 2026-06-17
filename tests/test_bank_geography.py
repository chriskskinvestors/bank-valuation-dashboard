"""Unit tests for data.bank_geography — region mapping + ticker→state resolution.

The FDIC institutions fetch and cert lookup are monkeypatched so the tests never
hit the network.
"""
import unittest

import data.bank_geography as geo


class TestRegionMapping(unittest.TestCase):
    def test_known_regions(self):
        self.assertEqual(geo.region_for_state("GA"), "South")
        self.assertEqual(geo.region_for_state("ny"), "Northeast")   # case-insensitive
        self.assertEqual(geo.region_for_state("CA"), "West")
        self.assertEqual(geo.region_for_state("OH"), "Midwest")

    def test_unknown_and_territory(self):
        self.assertEqual(geo.region_for_state(""), "Unknown")
        self.assertEqual(geo.region_for_state(None), "Unknown")
        self.assertEqual(geo.region_for_state("PR"), "Other")   # territory, not a region


class TestGetStatesFor(unittest.TestCase):
    def setUp(self):
        self._orig_map = geo._cert_state_map
        self._orig_cert = geo.get_fdic_cert
        geo._cert_state_map = lambda: {"123": "GA", "456": "TX"}
        certs = {"A": 123, "B": 456, "C": None, "D": 999}  # D maps to unknown cert
        geo.get_fdic_cert = lambda t: certs.get(t)

    def tearDown(self):
        geo._cert_state_map = self._orig_map
        geo.get_fdic_cert = self._orig_cert

    def test_resolution(self):
        out = geo.get_states_for(["A", "B", "C", "D"])
        self.assertEqual(out, {"A": "GA", "B": "TX", "C": "", "D": ""})


if __name__ == "__main__":
    unittest.main()
