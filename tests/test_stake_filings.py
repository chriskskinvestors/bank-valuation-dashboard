"""
Tests for data/stake_filings.py — 13D/G stake filings for the Private
Equity Transactions tab (§14). All HTTP mocked. Pins:

  • authoritative list from issuer submissions (SC 13D/13G families only),
    holder names joined from EFTS display_names by accession (the
    non-issuer entry), brand-token query
  • pre-EFTS filings keep an honest None holder with a filing-index link
  • fetch failures (submissions or EFTS) -> None, nothing cached
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


def _filings(rows):
    return [{"form": f, "date": d, "accession": a, "doc": doc, "items": ""}
            for f, d, a, doc in rows]


def _efts_hit(adsh, holder_dn):
    return {"_id": f"{adsh}:doc.htm",
            "_source": {"adsh": adsh, "file_date": "x",
                        "display_names": [
                            "BANNER CORP  (BANR)  (CIK 0000946673)",
                            holder_dn]}}


class TestGetStakeFilings(unittest.TestCase):

    @patch("data.stake_filings.time.sleep", lambda *_: None)
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.stake_filings.requests.get")
    @patch("data.stake_filings.iter_submission_filings")
    def test_join_and_orphan(self, mock_iter, mock_get, _cg, mock_cput):
        from data import stake_filings
        mock_iter.return_value = (_filings([
            ("SC 13D", "2007-06-26", "0001-07-1", "d.htm"),
            ("SC 13G/A", "2024-02-13", "0001-24-1", "g.htm"),
            ("SC 13G", "1998-02-10", "0001-98-1", ""),     # pre-EFTS
            ("8-K", "2020-01-01", "0001-20-1", "x.htm"),   # not a stake form
        ]), True)
        pages = [[_efts_hit("0001-07-1", "Klaue David A  (CIK 0001401560)"),
                  _efts_hit("0001-24-1",
                            "VANGUARD GROUP INC  (CIK 0000102909)")], []]
        calls = {"n": 0}

        def side_effect(url, params=None, headers=None, timeout=30):
            r = MagicMock()
            r.json.return_value = {"hits": {"hits": pages[min(calls["n"], 1)]}}
            r.raise_for_status = MagicMock()
            calls["n"] += 1
            return r

        mock_get.side_effect = side_effect
        rows = stake_filings.get_stake_filings(CIK, "Banner Corp")
        self.assertEqual(len(rows), 3)                    # 8-K excluded
        self.assertEqual(rows[0]["holder_name"], "VANGUARD GROUP INC")
        self.assertEqual(rows[1]["holder_name"], "Klaue David A")
        self.assertEqual(rows[1]["holder_cik"], 1401560)
        # Pre-EFTS: honest None holder, index-listing link (no primary doc).
        self.assertIsNone(rows[2]["holder_name"])
        self.assertTrue(rows[2]["url"].endswith("/000019810001/".replace(
            "000019810001", "0001981")))
        # Brand-token query, not the full corporate phrase.
        self.assertEqual(mock_get.call_args_list[0][1]["params"]["q"],
                         '"banner"')
        key, payload = mock_cput.call_args[0]
        self.assertEqual(key, f"stake_filings:v1:{CIK}")
        self.assertEqual(payload["rows"], rows)

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.stake_filings.iter_submission_filings",
           return_value=([], False))
    def test_submissions_failure_uncacheable(self, _mi, _cg, mock_cput):
        from data import stake_filings
        self.assertIsNone(stake_filings.get_stake_filings(CIK, "Banner"))
        mock_cput.assert_not_called()

    @patch("data.stake_filings.time.sleep", lambda *_: None)
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.stake_filings.requests.get",
           side_effect=Exception("efts down"))
    @patch("data.stake_filings.iter_submission_filings")
    def test_efts_failure_uncacheable(self, mock_iter, _mg, _cg, mock_cput):
        from data import stake_filings
        mock_iter.return_value = (_filings([
            ("SC 13D", "2007-06-26", "0001-07-1", "d.htm")]), True)
        self.assertIsNone(stake_filings.get_stake_filings(CIK, "Banner"))
        mock_cput.assert_not_called()

    @patch("data.cache.get")
    def test_cache_hit(self, mock_cget):
        from data import stake_filings
        rows = [{"date": "2007-06-26", "form": "SC 13D", "holder_name": "K",
                 "holder_cik": 1, "url": None, "accession": "a"}]
        mock_cget.return_value = {"rows": rows,
                                  "cached_at": datetime.now().isoformat()}
        self.assertEqual(stake_filings.get_stake_filings(CIK, "B"), rows)

    def test_falsy_cik(self):
        from data.stake_filings import get_stake_filings
        self.assertIsNone(get_stake_filings(None))


if __name__ == "__main__":
    unittest.main()
