"""
Tests for data/sec_client.py companyfacts fetch — the (facts, ok) permanence
contract that gates M&A deal-comp caching. Pins:

  • HTTP 404 (SEC has no XBRL facts for a non-reporting CIK) -> ({}, True),
    a cacheable honest gap — otherwise a 404-target deal keeps a bank's
    ma_history perpetually uncached and re-fetched every nightly run
  • transient failures (503, timeout, 429-exhausted) -> ({}, False)
  • success -> (facts, True)
"""
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import requests

# Full house stub (see tests/test_audit_regressions.py) — sec_client does
# `import streamlit as st` and decorates fetch_company_facts with st.cache_data
# at module load.
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


def _http_error(status):
    resp = MagicMock()
    resp.status_code = status
    return requests.HTTPError(f"{status} error", response=resp)


class TestDownloadCompanyFactsOk(unittest.TestCase):

    @patch("data.http.requests.get")
    def test_404_is_cacheable_gap(self, mock_get):
        from data.sec_client import _download_company_facts_ok
        resp = MagicMock()
        resp.status_code = 404
        resp.raise_for_status.side_effect = _http_error(404)
        mock_get.return_value = resp
        facts, ok = _download_company_facts_ok(949589)
        self.assertEqual(facts, {})
        self.assertTrue(ok)             # permanent — non-reporting CIK

    @patch("data.http.requests.get")
    def test_503_is_transient(self, mock_get):
        from data.sec_client import _download_company_facts_ok
        resp = MagicMock()
        resp.status_code = 503
        resp.raise_for_status.side_effect = _http_error(503)
        mock_get.return_value = resp
        facts, ok = _download_company_facts_ok(320193)
        self.assertEqual(facts, {})
        self.assertFalse(ok)

    @patch("data.http.requests.get")
    def test_success(self, mock_get):
        from data.sec_client import _download_company_facts_ok
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"facts": {"dei": {}}}
        mock_get.return_value = resp
        facts, ok = _download_company_facts_ok(320193)
        self.assertEqual(facts, {"facts": {"dei": {}}})
        self.assertTrue(ok)


class TestFetchCompanyFactsOk(unittest.TestCase):

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.sec_client._download_company_facts_ok", return_value=({}, True))
    def test_permanent_404_not_cached_but_ok(self, _dl, _cg, mock_put):
        from data.sec_client import fetch_company_facts_ok
        facts, ok = fetch_company_facts_ok(949589)
        self.assertEqual(facts, {})
        self.assertTrue(ok)
        mock_put.assert_not_called()    # nothing worth caching, but not a retry

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.sec_client._download_company_facts_ok", return_value=({}, False))
    def test_transient_is_uncacheable(self, _dl, _cg, mock_put):
        from data.sec_client import fetch_company_facts_ok
        facts, ok = fetch_company_facts_ok(320193)
        self.assertEqual(facts, {})
        self.assertFalse(ok)

    @patch("data.cache.get")
    def test_cache_hit_is_ok_true(self, mock_cget):
        from data.sec_client import fetch_company_facts_ok
        mock_cget.return_value = {"facts": {"dei": {}}}
        facts, ok = fetch_company_facts_ok(320193)
        self.assertEqual(facts, {"facts": {"dei": {}}})
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
