"""
Tests for data/hmda_client.py — the CFPB HMDA client behind the HMDA
Mortgages / Mortgage Analytics sub-tabs (docs/SNL-BUILD-PLAN.md §11).
All HTTP is mocked; no live network. Pins:

  • origination totals + purpose/type buckets parsed from the aggregation
    response shape (fixtures are Banner Bank's real 2024 numbers)
  • per-state breakdown: one call per state, zero states dropped,
    volume-descending, any state failing → None (no partial lists)
  • find_lei: name exact/substring/ambiguous; RSSD chain (FDIC name →
    filers match → institution rssd round-trip) incl. mismatch → None
  • failures return None and never cache
  • a fresh cache entry short-circuits the network entirely
"""
import sys
import types
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

# Stub streamlit before importing modules that decorate with st.cache_data.
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)


def _resp(payload):
    """A requests.Response stand-in whose .json() returns `payload`."""
    r = MagicMock()
    r.json.return_value = payload
    return r


def _agg(rows):
    return _resp({"parameters": {}, "aggregations": rows, "servedFrom": "cache"})


# Banner Bank (WE0I402RW25AU38DTI13) 2024, captured live 2026-06-11.
# Hand-checks: purpose counts 1313+1142+169+167+349+0 = 3140 = total;
# purpose sums 8.22595e8+1.5224e8+4.3595e7+5.9165e7+4.5805e7 = 1.1234e9.
TOTAL_2024 = [{"count": 3140, "sum": 1.1234e9, "actions_taken": "1"}]
PURPOSES_2024 = [
    {"count": 1142, "sum": 1.5224e8, "actions_taken": "1", "loan_purposes": "2"},
    {"count": 1313, "sum": 8.22595e8, "actions_taken": "1", "loan_purposes": "1"},
    {"count": 169, "sum": 4.3595e7, "actions_taken": "1", "loan_purposes": "31"},
    {"count": 0, "sum": 0.0, "actions_taken": "1", "loan_purposes": "5"},
    {"count": 349, "sum": 4.5805e7, "actions_taken": "1", "loan_purposes": "4"},
    {"count": 167, "sum": 5.9165e7, "actions_taken": "1", "loan_purposes": "32"},
]
TYPES_2024 = [
    {"count": 2964, "sum": 1.05307e9, "actions_taken": "1", "loan_types": "1"},
    {"count": 130, "sum": 4.991e7, "actions_taken": "1", "loan_types": "2"},
    {"count": 43, "sum": 1.9585e7, "actions_taken": "1", "loan_types": "3"},
    {"count": 3, "sum": 835000.0, "actions_taken": "1", "loan_types": "4"},
]

LEI = "WE0I402RW25AU38DTI13"

FILERS_2024 = {"institutions": [
    {"lei": "549300BO5UTSDB76V724", "name": "The Havana National Bank", "period": "2024"},
    {"lei": LEI, "name": "Banner Bank", "period": "2024"},
    {"lei": "FAKE0FIRST0BANK00001", "name": "First Bank", "period": "2024"},
    {"lei": "FAKE0FIRST0BANK00002", "name": "First Bank of Elsewhere", "period": "2024"},
]}


class TestLenderOriginations(unittest.TestCase):

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_happy_path_parses_and_caches(self, mock_get, _cg, mock_cput):
        from data import hmda_client
        mock_get.side_effect = [_agg(TOTAL_2024), _agg(PURPOSES_2024),
                                _agg(TYPES_2024)]
        out = hmda_client.get_lender_originations(LEI, 2024)
        self.assertIsNotNone(out)
        self.assertEqual(out["total_count"], 3140)
        self.assertEqual(out["total_volume_usd"], 1.1234e9)
        self.assertEqual(out["by_purpose"]["purchase"],
                         {"count": 1313, "volume_usd": 8.22595e8})
        self.assertEqual(out["by_purpose"]["refi"],
                         {"count": 169, "volume_usd": 4.3595e7})
        self.assertEqual(out["by_purpose"]["cash_out_refi"],
                         {"count": 167, "volume_usd": 5.9165e7})
        self.assertEqual(out["by_purpose"]["not_applicable"],
                         {"count": 0, "volume_usd": 0.0})
        self.assertEqual(out["by_type"]["conventional"],
                         {"count": 2964, "volume_usd": 1.05307e9})
        self.assertEqual(out["by_type"]["va"],
                         {"count": 43, "volume_usd": 1.9585e7})
        # Counts re-add to the total exactly (HMDA counts are exact).
        self.assertEqual(sum(b["count"] for b in out["by_purpose"].values()),
                         out["total_count"])
        # Cached under the documented key, with a cached_at stamp.
        key, payload = mock_cput.call_args[0]
        self.assertEqual(key, f"hmda:orig:{LEI}:2024")
        self.assertIn("cached_at", payload)
        # Exactly three aggregation calls: total, by-purpose, by-type.
        self.assertEqual(mock_get.call_count, 3)
        params = mock_get.call_args_list[0].kwargs.get("params") \
            or mock_get.call_args_list[0][0][1]
        self.assertEqual(params["actions_taken"], "1")  # originations only
        self.assertEqual(params["leis"], LEI)

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_http_failure_returns_none(self, mock_get, _cg, mock_cput):
        from data import hmda_client
        mock_get.side_effect = Exception("connection reset")
        self.assertIsNone(hmda_client.get_lender_originations(LEI, 2024))
        mock_cput.assert_not_called()

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_partial_failure_returns_none(self, mock_get, _cg, mock_cput):
        # Total and purposes succeed, types call fails → None, nothing cached.
        from data import hmda_client
        mock_get.side_effect = [_agg(TOTAL_2024), _agg(PURPOSES_2024),
                                Exception("timeout")]
        self.assertIsNone(hmda_client.get_lender_originations(LEI, 2024))
        mock_cput.assert_not_called()

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_bad_shape_returns_none(self, mock_get, _cg, mock_cput):
        from data import hmda_client
        mock_get.return_value = _resp({"errorType": "provide-atleast-one-filter-criteria"})
        self.assertIsNone(hmda_client.get_lender_originations(LEI, 2024))
        mock_cput.assert_not_called()

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_unknown_enum_returns_none(self, mock_get, _cg, mock_cput):
        # An enum value we don't label must refuse, never mislabel.
        from data import hmda_client
        bad = PURPOSES_2024 + [{"count": 5, "sum": 1.0, "actions_taken": "1",
                                "loan_purposes": "99"}]
        mock_get.side_effect = [_agg(TOTAL_2024), _agg(bad), _agg(TYPES_2024)]
        self.assertIsNone(hmda_client.get_lender_originations(LEI, 2024))
        mock_cput.assert_not_called()

    @patch("data.http.get_with_retry")
    @patch("data.cache.get")
    def test_cache_hit_short_circuits_network(self, mock_cget, mock_get):
        from data import hmda_client
        mock_cget.return_value = {
            "lei": LEI, "year": 2024, "total_count": 3140,
            "total_volume_usd": 1.1234e9, "by_purpose": {}, "by_type": {},
            "cached_at": datetime.now().isoformat(),
        }
        out = hmda_client.get_lender_originations(LEI, 2024)
        self.assertEqual(out["total_count"], 3140)
        self.assertEqual(mock_get.call_count, 0)

    @patch("data.cache.put")
    @patch("data.cache.get")
    @patch("data.http.get_with_retry")
    def test_stale_cache_refetches(self, mock_get, mock_cget, _cp):
        from data import hmda_client
        stale = (datetime.now() - timedelta(days=31)).isoformat()
        mock_cget.return_value = {"total_count": 1, "cached_at": stale}
        mock_get.side_effect = [_agg(TOTAL_2024), _agg(PURPOSES_2024),
                                _agg(TYPES_2024)]
        out = hmda_client.get_lender_originations(LEI, 2024)
        self.assertEqual(out["total_count"], 3140)
        self.assertEqual(mock_get.call_count, 3)


class TestLenderByState(unittest.TestCase):

    @patch("data.hmda_client.STATES", ("WA", "OR", "MT"))
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_state_breakdown_drops_zeros_and_sorts(self, mock_get, _cg, mock_cput):
        from data import hmda_client
        # Live-captured Banner 2024: WA 1677/$470.355M, OR 733/$231.155M
        # (WA+OR=2410 from the live two-state probe), MT zero.
        mock_get.side_effect = [
            _agg([{"count": 1677, "sum": 4.70355e8, "actions_taken": "1"}]),
            _agg([{"count": 733, "sum": 2.31155e8, "actions_taken": "1"}]),
            _agg([{"count": 0, "sum": 0.0, "actions_taken": "1"}]),
        ]
        out = hmda_client.get_lender_by_state(LEI, 2024)
        self.assertEqual(out, [
            {"state": "WA", "count": 1677, "volume_usd": 4.70355e8},
            {"state": "OR", "count": 733, "volume_usd": 2.31155e8},
        ])
        self.assertEqual(mock_get.call_count, 3)  # one call per state
        params = mock_get.call_args_list[1].kwargs.get("params") \
            or mock_get.call_args_list[1][0][1]
        self.assertEqual(params["states"], "OR")
        key, payload = mock_cput.call_args[0]
        self.assertEqual(key, f"hmda:bystate:{LEI}:2024")
        self.assertIn("cached_at", payload)

    @patch("data.hmda_client.STATES", ("WA", "OR", "MT"))
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_any_state_failing_returns_none(self, mock_get, _cg, mock_cput):
        # A partial list would be wrong by omission — must be all or None.
        from data import hmda_client
        mock_get.side_effect = [
            _agg([{"count": 1677, "sum": 4.70355e8, "actions_taken": "1"}]),
            Exception("timeout"),
        ]
        self.assertIsNone(hmda_client.get_lender_by_state(LEI, 2024))
        mock_cput.assert_not_called()

    @patch("data.http.get_with_retry")
    @patch("data.cache.get")
    def test_cache_hit_short_circuits_network(self, mock_cget, mock_get):
        from data import hmda_client
        rows = [{"state": "WA", "count": 1677, "volume_usd": 4.70355e8}]
        mock_cget.return_value = {"rows": rows,
                                  "cached_at": datetime.now().isoformat()}
        self.assertEqual(hmda_client.get_lender_by_state(LEI, 2024), rows)
        self.assertEqual(mock_get.call_count, 0)


class TestFindLei(unittest.TestCase):

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_name_exact_match_case_insensitive(self, mock_get, _cg, _cp):
        from data import hmda_client
        mock_get.return_value = _resp(FILERS_2024)
        self.assertEqual(hmda_client.find_lei("banner bank", 2024), LEI)

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_name_unique_substring_match(self, mock_get, _cg, _cp):
        from data import hmda_client
        mock_get.return_value = _resp(FILERS_2024)
        self.assertEqual(hmda_client.find_lei("Havana", 2024),
                         "549300BO5UTSDB76V724")

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_ambiguous_name_returns_none(self, mock_get, _cg, _cp):
        # "First Bank" is an exact match for one filer AND a substring of
        # another — exact wins. A substring-only ambiguity returns None.
        from data import hmda_client
        mock_get.return_value = _resp(FILERS_2024)
        self.assertEqual(hmda_client.find_lei("First Bank", 2024),
                         "FAKE0FIRST0BANK00001")
        self.assertIsNone(hmda_client.find_lei("Bank", 2024))

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_rssd_chain_resolves_and_verifies(self, mock_get, _cg, _cp):
        from data import hmda_client

        def fake(url, params=None, **kw):
            if "fdic" in url:
                self.assertEqual(params["filters"], "FED_RSSD:352772")
                return _resp({"data": [{"data": {"NAME": "Banner Bank"}}]})
            if "/reporting/filers/" in url:
                return _resp(FILERS_2024)
            if "/public/institutions/" in url:
                self.assertIn(LEI, url)
                return _resp({"lei": LEI, "rssd": 352772})
            raise AssertionError(f"unexpected url {url}")

        mock_get.side_effect = fake
        self.assertEqual(hmda_client.find_lei(352772, 2024), LEI)
        self.assertEqual(hmda_client.find_lei("352772", 2024), LEI)

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_rssd_mismatch_returns_none(self, mock_get, _cg, _cp):
        # The candidate LEI's institution record reports a DIFFERENT rssd
        # → never return a plausible-wrong LEI.
        from data import hmda_client

        def fake(url, params=None, **kw):
            if "fdic" in url:
                return _resp({"data": [{"data": {"NAME": "Banner Bank"}}]})
            if "/reporting/filers/" in url:
                return _resp(FILERS_2024)
            return _resp({"lei": LEI, "rssd": 999999})

        mock_get.side_effect = fake
        self.assertIsNone(hmda_client.find_lei(352772, 2024))

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_unknown_rssd_returns_none(self, mock_get, _cg, _cp):
        from data import hmda_client
        mock_get.return_value = _resp({"data": []})  # FDIC: no such RSSD
        self.assertIsNone(hmda_client.find_lei(123456789, 2024))

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_filers_fetch_failure_returns_none(self, mock_get, _cg, mock_cput):
        from data import hmda_client
        mock_get.side_effect = Exception("connection reset")
        self.assertIsNone(hmda_client.find_lei("Banner Bank", 2024))
        mock_cput.assert_not_called()

    @patch("data.http.get_with_retry")
    @patch("data.cache.get")
    def test_filers_cache_hit_short_circuits_network(self, mock_cget, mock_get):
        from data import hmda_client
        mock_cget.return_value = {"institutions": FILERS_2024["institutions"],
                                  "cached_at": datetime.now().isoformat()}
        self.assertEqual(hmda_client.find_lei("Banner Bank", 2024), LEI)
        self.assertEqual(mock_get.call_count, 0)


class TestEnumTables(unittest.TestCase):

    def test_enum_coverage(self):
        # HMDA FIG enumerations — all purposes (1,2,31,32,4,5) and
        # types (1-4) present, so _bucket can never drop a row silently.
        from data.hmda_client import LOAN_PURPOSES, LOAN_TYPES, STATES
        self.assertEqual(set(LOAN_PURPOSES), {"1", "2", "31", "32", "4", "5"})
        self.assertEqual(set(LOAN_TYPES), {"1", "2", "3", "4"})
        self.assertGreaterEqual(len(STATES), 51)  # 50 states + DC at minimum
        self.assertIn("DC", STATES)


if __name__ == "__main__":
    unittest.main()
