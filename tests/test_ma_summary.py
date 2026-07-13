"""
Tests for data/ma_summary.py — the Transactions Summary aggregates
(docs/SNL-BUILD-PLAN.md §14). All HTTP mocked. Pins:

  • form-type bucketing: S-1/S-3 family -> shelf, 424B* -> offerings,
    everything else ignored
  • FULL history: the recent block plus every archived submissions page
  • buyback sweep: grouped per accession, earnings 8-Ks (item 2.02)
    excluded, newest-first, EFTS pagination followed
  • any fetch failure -> None and nothing cached; success cached under
    ma_summary:v1:{cik}
"""
import sys
import types
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

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

CIK = 946673


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    return r


def _efts_hit(adsh, file_date, doc="body.htm", items=("8.01",)):
    return {"_id": f"{adsh}:{doc}",
            "_source": {"adsh": adsh, "file_date": file_date,
                        "items": list(items), "file_type": "8-K",
                        "root_forms": ["8-K"]}}


def _wire(recent, pages=None, efts_pages=None, fail=()):
    """requests.get side_effect. recent/pages: submissions blocks;
    efts_pages: list of hits lists returned per EFTS call; fail: URL
    substrings that raise."""
    pages = pages or {}
    efts_pages = list(efts_pages or [[]])
    calls = {"efts": 0}

    def side_effect(url, params=None, headers=None, timeout=30):
        for frag in fail:
            if frag in url:
                raise Exception("down")
        if "efts.sec.gov" in url:
            page = (efts_pages[calls["efts"]]
                    if calls["efts"] < len(efts_pages) else [])
            calls["efts"] += 1
            return _resp({"hits": {"hits": page}})
        if url.endswith(f"CIK{CIK:010d}.json"):
            return _resp({"filings": {"recent": recent,
                                      "files": [{"name": n} for n in pages]}})
        for name, block in pages.items():
            if url.endswith(name):
                return _resp(block)
        raise AssertionError(f"unexpected URL {url}")

    return side_effect


class TestFilingCounts(unittest.TestCase):

    @patch("data.ma_summary.time.sleep", lambda *_: None)
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.ma_summary.requests.get")
    def test_buckets_and_paged_history(self, mock_get, _cg, mock_cput):
        from data import ma_summary
        recent = {"form": ["S-3ASR", "424B5", "8-K", "10-K", "S-1/A"],
                  "filingDate": ["2024-05-01", "2024-06-01", "2024-07-01",
                                 "2024-02-01", "2023-01-15"]}
        page = {"form": ["S-3", "424B3", "424B2"],
                "filingDate": ["2009-04-01", "2009-05-01", "2010-01-01"]}
        mock_get.side_effect = _wire(recent,
                                     pages={"CIK-sub-001.json": page},
                                     efts_pages=[[]])
        s = ma_summary.get_summary(CIK)
        self.assertEqual(s["filings_by_year"], {
            "2009": {"shelf": 1, "offerings": 1},
            "2010": {"shelf": 0, "offerings": 1},
            "2023": {"shelf": 1, "offerings": 0},
            "2024": {"shelf": 1, "offerings": 1},
        })
        key, payload = mock_cput.call_args[0]
        self.assertEqual(key, f"ma_summary:v1:{CIK}")
        self.assertEqual(payload["summary"], s)

    @patch("data.ma_summary.time.sleep", lambda *_: None)
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.ma_summary.requests.get")
    def test_submissions_failure_returns_none_uncached(self, mock_get, _cg, mock_cput):
        from data import ma_summary
        mock_get.side_effect = _wire({"form": [], "filingDate": []},
                                     fail=("submissions",))
        self.assertIsNone(ma_summary.get_summary(CIK))
        mock_cput.assert_not_called()


class TestBuybackSweep(unittest.TestCase):

    @patch("data.ma_summary.time.sleep", lambda *_: None)
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.ma_summary.requests.get")
    def test_earnings_excluded_grouped_newest_first(self, mock_get, _cg, _cp):
        from data import ma_summary
        hits = [
            _efts_hit("0001-24-1", "2024-07-25"),
            _efts_hit("0001-24-1", "2024-07-25", doc="ex99.htm"),  # same group
            _efts_hit("0001-24-2", "2024-01-25", items=("2.02", "9.01")),  # earnings
            _efts_hit("0001-25-1", "2025-07-24", items=("7.01",)),
        ]
        mock_get.side_effect = _wire({"form": [], "filingDate": []},
                                     efts_pages=[hits, []])
        s = ma_summary.get_summary(CIK)
        self.assertEqual([r["date"] for r in s["buybacks"]],
                         ["2025-07-24", "2024-07-25"])
        self.assertNotIn("0001-24-2", [r["adsh"] for r in s["buybacks"]])

    @patch("data.ma_summary.time.sleep", lambda *_: None)
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.ma_summary.requests.get")
    def test_efts_failure_returns_none_uncached(self, mock_get, _cg, mock_cput):
        from data import ma_summary
        mock_get.side_effect = _wire({"form": [], "filingDate": []},
                                     fail=("efts",))
        self.assertIsNone(ma_summary.get_summary(CIK))
        mock_cput.assert_not_called()


class TestCache(unittest.TestCase):

    @patch("data.ma_summary.requests.get")
    @patch("data.cache.get")
    def test_cache_hit_short_circuits(self, mock_cget, mock_get):
        from data import ma_summary
        summary = {"filings_by_year": {}, "buybacks": []}
        mock_cget.return_value = {"summary": summary,
                                  "cached_at": datetime.now().isoformat()}
        self.assertEqual(ma_summary.get_summary(CIK), summary)
        mock_get.assert_not_called()

    def test_falsy_cik(self):
        from data import ma_summary
        self.assertIsNone(ma_summary.get_summary(None))


if __name__ == "__main__":
    unittest.main()
