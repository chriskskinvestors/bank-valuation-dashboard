"""
Tests for data/fdic_structure.py — the FDIC structure-change history client
behind the Transactions section's Detailed M&A History (docs/SNL-BUILD-PLAN.md
§14). All HTTP is mocked; no live network. Pins:

  • event parsing of the history endpoint's record shape (verified live
    against Banner Bank, cert 28489)
  • direction classification: 810 survivor-side -> acquired; 2xx
    absorbed-side -> was_acquired; charter changes / phantom
    self-reorganizations -> other
  • newest-first ordering regardless of API order
  • empty response and HTTP failure -> [] (never raise), nothing cached
  • a fresh cache entry short-circuits the network entirely
"""
import sys
import types
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

# Stub streamlit before importing data modules that may touch st decorators.
# Full house stub (see tests/test_audit_regressions.py): a minimal stub that
# wins the sys.modules setdefault race would break later suites needing
# st.fragment / streamlit.components.v1 at module load (the stub-rot trap,
# memory 2026-07-02).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
_st_components = types.ModuleType("streamlit.components")
_st_components_v1 = types.ModuleType("streamlit.components.v1")
_st_components_v1.html = lambda *a, **k: None
_st_components.v1 = _st_components_v1
_st.components = _st_components
sys.modules.setdefault("streamlit", _st)
sys.modules.setdefault("streamlit.components", _st_components)
sys.modules.setdefault("streamlit.components.v1", _st_components_v1)

BANNER = 28489


def _resp(rows, total=None):
    """A requests.Response stand-in in the FDIC envelope shape."""
    r = MagicMock()
    r.json.return_value = {
        "meta": {"total": total if total is not None else len(rows)},
        "data": [{"data": d, "score": 0} for d in rows],
    }
    return r


def _merger_row(effdate, out_cert, out_name, cert=BANNER):
    """An 810 row as recorded on the survivor's cert (live shape)."""
    return {
        "CERT": cert, "INSTNAME": "Banner Bank",
        "EFFDATE": f"{effdate}T00:00:00",
        "CHANGECODE": 810,
        "CHANGECODE_DESC": "Participated in Absorbtion/Consolidation/Merger",
        "OUT_CERT": out_cert, "OUT_INSTNAME": out_name,
        "ACQ_CERT": cert, "ACQ_INSTNAME": "Banner Bank",
        "SUR_CERT": cert, "SUR_INSTNAME": "Banner Bank",
    }


# The absorbed bank's own termination row (live shape: RiverBank cert 23288
# absorbed by People's United Bank, CHANGECODE 221).
ABSORBED_ROW = {
    "CERT": 23288, "INSTNAME": None,
    "EFFDATE": "2010-11-30T00:00:00",
    "CHANGECODE": 221, "CHANGECODE_DESC": "Absorbtion - Without Assistance",
    "OUT_CERT": 23288, "OUT_INSTNAME": "RiverBank",
    "ACQ_CERT": 27334, "ACQ_INSTNAME": "People's United Bank",
    "SUR_CERT": 27334, "SUR_INSTNAME": "People's United Bank",
}

CHARTER_ROW = {
    "CERT": BANNER, "INSTNAME": "Banner Bank",
    "EFFDATE": "1991-10-01T00:00:00",
    "CHANGECODE": 420, "CHANGECODE_DESC": "Change in Chartering Agency",
    "OUT_CERT": None, "OUT_INSTNAME": None,
    "ACQ_CERT": None, "ACQ_INSTNAME": None,
    "SUR_CERT": None, "SUR_INSTNAME": None,
}


class TestGetStructureEvents(unittest.TestCase):

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_happy_path_parses_and_caches(self, mock_get, _cg, mock_cput):
        from data import fdic_structure
        mock_get.return_value = _resp([
            _merger_row("2018-11-01", 17874, "Skagit Bank"),
            CHARTER_ROW,
        ])
        events = fdic_structure.get_structure_events(BANNER)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0], {
            "date": "2018-11-01",
            "event_type": 810,
            "description": "Participated in Absorbtion/Consolidation/Merger",
            "other_institution": {"name": "Skagit Bank", "cert": 17874},
            "direction": "acquired",
        })
        # Charter change: no counterparty, direction "other".
        self.assertEqual(events[1]["direction"], "other")
        self.assertIsNone(events[1]["other_institution"])
        self.assertEqual(events[1]["event_type"], 420)
        # Cached under the documented key, with a cached_at stamp.
        key, payload = mock_cput.call_args[0]
        self.assertEqual(key, f"fdic_structure:{BANNER}")
        self.assertIn("cached_at", payload)
        self.assertEqual(payload["events"], events)
        # Request excludes branch noise server-side and filters on the cert.
        params = mock_get.call_args[0][1]
        self.assertIn(f"CERT:{BANNER}", params["filters"])
        self.assertIn("CHANGECODE:[100 TO 499]", params["filters"])
        self.assertIn("CHANGECODE:[800 TO 899]", params["filters"])

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_was_acquired_direction(self, mock_get, _cg, _cp):
        # Queried from the absorbed bank's cert: its own termination event.
        from data import fdic_structure
        mock_get.return_value = _resp([ABSORBED_ROW])
        events = fdic_structure.get_structure_events(23288)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["direction"], "was_acquired")
        self.assertEqual(events[0]["other_institution"],
                         {"name": "People's United Bank", "cert": 27334})

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_phantom_self_reorg_is_other(self, mock_get, _cg, _cp):
        # OUT == SUR == cert (interim corporate reorg) is not an acquisition.
        from data import fdic_structure
        row = _merger_row("2000-10-30", BANNER, "Banner Bank")
        row["CHANGECODE"] = 820
        row["CHANGECODE_DESC"] = "Phantom (Interim) Corporate Reorganization"
        mock_get.return_value = _resp([row])
        events = fdic_structure.get_structure_events(BANNER)
        self.assertEqual(events[0]["direction"], "other")
        self.assertIsNone(events[0]["other_institution"])

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_newest_first_ordering(self, mock_get, _cg, _cp):
        from data import fdic_structure
        mock_get.return_value = _resp([  # deliberately out of order
            _merger_row("2015-03-06", 19006, "Siuslaw Bank"),
            _merger_row("2019-11-01", 58275, "AltaPacific Bank"),
            _merger_row("2015-10-02", 22441, "AmericanWest Bank"),
        ])
        events = fdic_structure.get_structure_events(BANNER)
        self.assertEqual([e["date"] for e in events],
                         ["2019-11-01", "2015-10-02", "2015-03-06"])

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_empty_response_returns_empty(self, mock_get, _cg, _cp):
        from data import fdic_structure
        mock_get.return_value = _resp([])
        self.assertEqual(fdic_structure.get_structure_events(BANNER), [])

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_http_failure_returns_empty_uncached(self, mock_get, _cg, mock_cput):
        from data import fdic_structure
        mock_get.side_effect = Exception("connection reset")
        self.assertEqual(fdic_structure.get_structure_events(BANNER), [])
        mock_cput.assert_not_called()

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_retries_exhausted_returns_empty_uncached(self, mock_get, _cg, mock_cput):
        from data import fdic_structure
        mock_get.return_value = None  # get_with_retry: every attempt 429'd
        self.assertEqual(fdic_structure.get_structure_events(BANNER), [])
        mock_cput.assert_not_called()

    def test_falsy_cert_returns_empty(self):
        from data import fdic_structure
        self.assertEqual(fdic_structure.get_structure_events(None), [])
        self.assertEqual(fdic_structure.get_structure_events(0), [])

    @patch("data.http.get_with_retry")
    @patch("data.cache.get")
    def test_cache_hit_short_circuits_network(self, mock_cget, mock_get):
        from data import fdic_structure
        cached_events = [{"date": "2018-11-01", "event_type": 810,
                          "description": "x", "other_institution": None,
                          "direction": "other"}]
        mock_cget.return_value = {"events": cached_events,
                                  "cached_at": datetime.now().isoformat()}
        self.assertEqual(fdic_structure.get_structure_events(BANNER),
                         cached_events)
        self.assertEqual(mock_get.call_count, 0)

    @patch("data.cache.put")
    @patch("data.cache.get")
    @patch("data.http.get_with_retry")
    def test_stale_cache_refetches(self, mock_get, mock_cget, _cp):
        from data import fdic_structure
        stale = (datetime.now() - timedelta(days=8)).isoformat()
        mock_cget.return_value = {"events": [], "cached_at": stale}
        mock_get.return_value = _resp([_merger_row("2018-11-01", 17874, "Skagit Bank")])
        events = fdic_structure.get_structure_events(BANNER)
        self.assertEqual(len(events), 1)
        self.assertEqual(mock_get.call_count, 1)


class TestGetAcquisitionHistory(unittest.TestCase):

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_acquired_subset_shaped_for_table(self, mock_get, _cg, _cp):
        from data import fdic_structure
        mock_get.return_value = _resp([
            _merger_row("2018-11-01", 17874, "Skagit Bank"),
            CHARTER_ROW,  # direction "other" — must not appear
            _merger_row("2015-03-06", 19006, "Siuslaw Bank"),
        ])
        acq = fdic_structure.get_acquisition_history(BANNER)
        self.assertEqual(acq, [
            {"date": "2018-11-01", "target_name": "Skagit Bank",
             "target_cert": 17874,
             "event_desc": "Participated in Absorbtion/Consolidation/Merger"},
            {"date": "2015-03-06", "target_name": "Siuslaw Bank",
             "target_cert": 19006,
             "event_desc": "Participated in Absorbtion/Consolidation/Merger"},
        ])

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_failure_propagates_as_empty(self, mock_get, _cg, _cp):
        from data import fdic_structure
        mock_get.side_effect = Exception("timeout")
        self.assertEqual(fdic_structure.get_acquisition_history(BANNER), [])


class TestParsingEdges(unittest.TestCase):

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_undated_rows_skipped_and_zero_certs_none(self, mock_get, _cg, _cp):
        from data import fdic_structure
        undated = _merger_row("", 17874, "Skagit Bank")
        undated["EFFDATE"] = None
        zero_cert = dict(CHARTER_ROW, OUT_CERT=0, ACQ_CERT="", SUR_CERT=None)
        mock_get.return_value = _resp([undated, zero_cert])
        events = fdic_structure.get_structure_events(BANNER)
        self.assertEqual(len(events), 1)  # undated row dropped
        self.assertEqual(events[0]["direction"], "other")
        self.assertIsNone(events[0]["other_institution"])


if __name__ == "__main__":
    unittest.main()
