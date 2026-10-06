"""Caches whose own freshness stamp outlives data.cache's default 24h max
age must read with max_age_s=None — otherwise every entry expires after a
day and the fetch re-runs (UX-P1-05 class: data/nic_client re-parsed the NIC
bulk files daily on the Corporate Structure render path).

A 3-day-old entry inside each module's own window must be served without
touching the network.

Run: python -m unittest tests.test_cache_stamp_reads
"""
import unittest
from datetime import datetime, timedelta
from unittest import mock

from tests import _streamlit_stub  # noqa: F401


def _serve(entry):
    seen = {}

    def fake_get(key, max_age_s="default"):
        seen["max_age_s"] = max_age_s
        return dict(entry, cached_at=(datetime.now() - timedelta(days=3)).isoformat())
    return fake_get, seen


def _no_network(*a, **k):
    raise AssertionError("refetched a fresh entry")


class TestStampGovernsTheRead(unittest.TestCase):
    def test_census_acs_30d(self):
        from data import census_client as cc
        get, seen = _serve({"name": "X County"})
        with mock.patch("data.cache.get", get), \
                mock.patch("data.http.get_with_retry", _no_network):
            out = cc._fetch_acs(2023, "county:001", {"for": "county:001"})
        self.assertIsNone(seen["max_age_s"])
        self.assertEqual(out["name"], "X County")

    def test_entity_lifespan_30d(self):
        from data import entity_graph as eg
        get, seen = _serve({"est": "1985-01-01", "end": None})
        with mock.patch("data.cache.get", get), \
                mock.patch("data.http.get_with_retry", _no_network):
            est, end = eg.cert_lifespan(3832)
        self.assertIsNone(seen["max_age_s"])
        self.assertEqual(est.year, 1985)

    def test_fdic_structure_7d(self):
        from data import fdic_structure as fs
        get, seen = _serve({"events": [{"date": "2024-02-01"}]})
        with mock.patch("data.cache.get", get), \
                mock.patch.object(fs, "fetch_history_rows", _no_network):
            out = fs.get_structure_events(3832)
        self.assertIsNone(seen["max_age_s"])
        self.assertEqual(out, [{"date": "2024-02-01"}])


if __name__ == "__main__":
    unittest.main()
