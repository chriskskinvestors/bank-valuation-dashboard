"""
Tests for data/census_client.py — the ACS demographics client behind the
Market Demographics sub-tab (docs/SNL-BUILD-PLAN.md §11). All HTTP is
mocked; no live network. Pins:

  • happy-path parsing of the Census array-of-arrays response shape
  • unemployment rate derived from B23025 components (hand-computed)
  • None (never raise) on HTTP failure, sentinel values, bad shapes
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


# One Washington-state row in the Census API's array-of-arrays shape.
# Hand-computed: unemployment = 205_000 / 4_100_000 * 100 = 5.0
WA_2023 = [
    ["NAME", "B01003_001E", "B19013_001E", "B25077_001E",
     "B23025_005E", "B23025_003E", "state"],
    ["Washington", "7812880", "94605", "473400", "205000", "4100000", "53"],
]
WA_2018 = [
    ["NAME", "B01003_001E", "B19013_001E", "B25077_001E",
     "B23025_005E", "B23025_003E", "state"],
    ["Washington", "7294336", "70979", "339000", "190000", "3800000", "53"],
]


class TestStateDemographics(unittest.TestCase):

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_happy_path_parses_and_caches(self, mock_get, mock_cget, mock_cput):
        from data import census_client
        mock_get.return_value = _resp(WA_2023)

        out = census_client.get_state_demographics("53")
        self.assertIsNotNone(out)
        self.assertEqual(out["population"], 7812880)
        self.assertEqual(out["median_hh_income"], 94605)
        self.assertEqual(out["median_home_value"], 473400)
        self.assertEqual(out["vintage"], "ACS5 2023")
        self.assertEqual(out["name"], "Washington")
        self.assertEqual(out["geo"], "state:53")
        # Cached under the documented key, with a cached_at stamp.
        key, payload = mock_cput.call_args[0]
        self.assertEqual(key, "census:acs5_2023:state:53")
        self.assertIn("cached_at", payload)

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_unemployment_rate_from_components(self, mock_get, _cg, _cp):
        from data import census_client
        mock_get.return_value = _resp(WA_2023)
        out = census_client.get_state_demographics("53")
        # 205,000 unemployed / 4,100,000 labor force = 5.00%
        self.assertEqual(out["unemployment_rate_pct"], 5.0)

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_sentinel_values_become_none(self, mock_get, _cg, _cp):
        from data import census_client
        # Census suppression sentinel for median home value; zero labor force.
        mock_get.return_value = _resp([
            WA_2023[0],
            ["Somewhere", "1200", "-666666666", "-666666666", "0", "0", "53"],
        ])
        out = census_client.get_state_demographics("53")
        self.assertEqual(out["population"], 1200)
        self.assertIsNone(out["median_hh_income"])
        self.assertIsNone(out["median_home_value"])
        self.assertIsNone(out["unemployment_rate_pct"])  # no divide-by-zero

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_http_failure_returns_none(self, mock_get, _cg, mock_cput):
        from data import census_client
        mock_get.side_effect = Exception("connection reset")
        self.assertIsNone(census_client.get_state_demographics("53"))
        mock_cput.assert_not_called()

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_missing_key_html_returns_none(self, mock_get, _cg, mock_cput):
        # The API answers HTTP 200 + HTML "Missing Key" when no key is set;
        # the client must return None (logged), never raise or parse it.
        from data import census_client
        r = MagicMock()
        r.json.side_effect = ValueError("Expecting value: line 2 column 1")
        r.text = "<html><head><title>Missing Key</title></head>...</html>"
        mock_get.return_value = r
        self.assertIsNone(census_client.get_state_demographics("53"))
        mock_cput.assert_not_called()

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_bad_shape_returns_none(self, mock_get, _cg, mock_cput):
        from data import census_client
        mock_get.return_value = _resp({"error": "no such variable"})
        self.assertIsNone(census_client.get_state_demographics("53"))
        mock_cput.assert_not_called()

    @patch("data.http.get_with_retry")
    @patch("data.cache.get")
    def test_cache_hit_short_circuits_network(self, mock_cget, mock_get):
        from data import census_client
        mock_cget.return_value = {
            "population": 7812880, "median_hh_income": 94605,
            "median_home_value": 473400, "unemployed": 205000,
            "labor_force": 4100000, "name": "Washington",
            "cached_at": datetime.now().isoformat(),
        }
        out = census_client.get_state_demographics("53")
        self.assertEqual(out["population"], 7812880)
        self.assertEqual(mock_get.call_count, 0)

    @patch("data.cache.put")
    @patch("data.cache.get")
    @patch("data.http.get_with_retry")
    def test_stale_cache_refetches(self, mock_get, mock_cget, _cp):
        from data import census_client
        stale = (datetime.now() - timedelta(days=31)).isoformat()
        mock_cget.return_value = {"population": 1, "cached_at": stale}
        mock_get.return_value = _resp(WA_2023)
        out = census_client.get_state_demographics("53")
        self.assertEqual(out["population"], 7812880)
        self.assertEqual(mock_get.call_count, 1)


class TestCountyDemographics(unittest.TestCase):

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_county_geo_params_and_key(self, mock_get, _cg, mock_cput):
        from data import census_client
        mock_get.return_value = _resp([
            WA_2023[0][:-1] + ["state", "county"],
            ["King County, Washington", "2252305", "120824", "850300",
             "50000", "1250000", "53", "033"],
        ])
        out = census_client.get_county_demographics("53", "33")  # zfill → 033
        self.assertEqual(out["population"], 2252305)
        self.assertEqual(out["unemployment_rate_pct"], 4.0)  # 50k/1.25M
        self.assertEqual(out["geo"], "county:53033")
        params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[0][1]
        self.assertEqual(params["for"], "county:033")
        self.assertEqual(params["in"], "state:53")
        self.assertEqual(mock_cput.call_args[0][0], "census:acs5_2023:county:53033")


class TestPopulationChange(unittest.TestCase):

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_change_pct_across_vintages(self, mock_get, _cg, _cp):
        from data import census_client
        mock_get.side_effect = [_resp(WA_2023), _resp(WA_2018)]
        out = census_client.get_population_change("53")
        self.assertEqual(out["pop_latest"], 7812880)
        self.assertEqual(out["pop_prior"], 7294336)
        # (7812880 - 7294336) / 7294336 * 100 = 7.1088... → 7.11
        self.assertEqual(out["change_pct"], 7.11)
        self.assertEqual(out["years"], "2018-2023")
        self.assertEqual(mock_get.call_count, 2)

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_either_vintage_failing_returns_none(self, mock_get, _cg, _cp):
        from data import census_client
        mock_get.side_effect = [_resp(WA_2023), Exception("timeout")]
        self.assertIsNone(census_client.get_population_change("53"))


class TestFipsHelper(unittest.TestCase):

    def test_state_fips_coverage(self):
        from data.census_client import STATE_FIPS
        self.assertEqual(STATE_FIPS["WA"], "53")
        self.assertEqual(STATE_FIPS["DC"], "11")
        self.assertEqual(STATE_FIPS["PR"], "72")
        self.assertEqual(STATE_FIPS["GU"], "66")
        self.assertEqual(STATE_FIPS["VI"], "78")
        self.assertGreaterEqual(len(STATE_FIPS), 55)
        # Every value is a 2-digit zero-padded string.
        for code, fips in STATE_FIPS.items():
            self.assertRegex(fips, r"^\d{2}$", f"{code} fips malformed")


if __name__ == "__main__":
    unittest.main()
