"""
Regression tests for AUDIT-2026-07-02 P2 #46: the all-banks Trends grid write
had no coverage threshold — a partial FDIC response (429s return empty pages)
silently dropped banks from `rows` and the shrunken grid was persisted under
the STABLE key, clobbering the last complete grid until the next nightly run.

quarterly_series must NOT persist a build whose coverage is below
_MIN_GRID_COVERAGE (90%; measured baseline 99.7%), and must flag it via
payload["persisted"] so jobs/refresh_trends fails loudly.
"""
import sys
import types
import unittest
from unittest import mock

# Stub streamlit before importing data modules that decorate with st.cache_data.
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from data.as_of_metrics import quarterly_series, TREND_KEYS  # noqa: E402


def _run_build(present_certs, all_certs):
    """Run quarterly_series with mocked FDIC/cache/engine; return (payload, put)."""
    cert_to_id = {c: f"T{c}" for c in all_certs}
    fake_quarter = {c: {"CERT": c} for c in present_certs}
    fake_metrics = {k: 1.0 for k in TREND_KEYS}
    with mock.patch("data.cache.get", return_value=None), \
         mock.patch("data.cache.put") as put, \
         mock.patch("data.fdic_client.fetch_quarter_financials",
                    return_value=fake_quarter), \
         mock.patch("analysis.metrics.build_bank_metrics",
                    return_value=fake_metrics):
        payload = quarterly_series(cert_to_id, 2, build_if_missing=True,
                                   scope_id="GATETEST")
    return payload, put


class TestTrendsCoverageGate(unittest.TestCase):
    def test_partial_build_is_not_persisted(self):
        # 5 of 10 banks return data → 50% coverage → below the 90% gate.
        payload, put = _run_build(present_certs=range(5), all_certs=range(10))
        self.assertEqual(len(payload["rows"]), 5)      # partial payload returned
        self.assertFalse(payload["persisted"])          # ...but flagged
        self.assertAlmostEqual(payload["coverage"], 0.5)
        put.assert_not_called()                          # stable key untouched

    def test_full_build_is_persisted(self):
        payload, put = _run_build(present_certs=range(10), all_certs=range(10))
        self.assertEqual(len(payload["rows"]), 10)
        self.assertTrue(payload["persisted"])
        self.assertAlmostEqual(payload["coverage"], 1.0)
        put.assert_called_once()


if __name__ == "__main__":
    unittest.main()
