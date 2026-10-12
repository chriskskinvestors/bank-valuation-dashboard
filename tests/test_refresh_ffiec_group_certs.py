"""refresh-ffiec ingests every charter of a multi-charter holdco (2026-10-12).

Bank-side metrics strict-sum RC-R over the whole charter group
(analysis/metrics._bank_aoci_by_ticker: any charter missing → n/a). The job
ingested only each ticker's LEAD cert, so after the token renewal "AOCI % TCE
(Bank)" filled for single-charter banks (C −15.0, USB −13.2, TFC −16.5) but
stayed n/a for JPM (628 + Dearborn 21761), WFC, BAC and every other group.
With the real FDIC structure the ingest list grows 602 → 647 certs.

Run: python -m unittest tests.test_refresh_ffiec_group_certs
"""
import unittest
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

import jobs.refresh_ffiec as job  # noqa: E402

_GROUPS = {628: [628, 21761], 3511: [3511, 27389, 5146]}


class TestUniverseCerts(unittest.TestCase):

    def _run(self, bank_map, resolved, groups):
        with patch("data.bank_mapping.BANK_MAP", bank_map), \
                patch("data.bank_mapping._RESOLVED_FROM_JSON", resolved), \
                patch("data.cert_group.get_cert_group_cached",
                      side_effect=lambda c: groups.get(int(c), [int(c)])):
            return job._universe_certs()

    def test_group_siblings_are_ingested(self):
        u = self._run({"JPM": {"fdic_cert": 628}, "C": {"fdic_cert": 7213}},
                      {"WFC": {"fdic_cert": 3511}}, _GROUPS)
        self.assertEqual(u, {628, 21761, 7213, 3511, 27389, 5146})

    def test_no_group_map_keeps_lead_certs_only(self):
        u = self._run({"JPM": {"fdic_cert": 628}}, {"C": {"fdic_cert": 7213}}, {})
        self.assertEqual(u, {628, 7213})

    def test_cert_less_entries_are_skipped(self):
        u = self._run({"X": {"fdic_cert": None}}, {"Y": {}}, _GROUPS)
        self.assertEqual(u, set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
