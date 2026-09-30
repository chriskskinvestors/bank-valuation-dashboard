"""
get_filing_info shares ONE submissions download per CIK across every
max_filings value (REVIEW-2026-09-24 P1-8).

get_filing_info was memoised on (cik, max_filings) and did its own HTTP, and
callers use five different max_filings values (1, 50, 80, 200, 1000) — up to
five downloads of the same 1–3 MB submissions JSON per CIK per 15 minutes
(measured: Filings & Reports 2.9 s cold right after the Corporate Profile had
fetched the same document). The download now lives in _submissions_json(cik),
keyed on the CIK alone.

The house streamlit stub makes st.cache_data a pass-through, so memo hits are
not observable here; these pins guard the invariants that make the memo
share: the fetch is keyed on cik only, every max_filings value goes through it,
and it carries a bounded st.cache_data layer.
"""
import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

import data.sec_client as sc  # noqa: E402

SUBS = {
    "cik": 4242, "name": "Test Bancorp", "sic": "6022",
    "addresses": {"business": {"stateOrCountry": "WA", "city": "Tacoma"}},
    "stateOfIncorporation": "WA",
    "filings": {"recent": {
        "form": ["8-K", "10-Q", "8-K", "10-K", "DEF 14A"],
        "filingDate": ["2026-07-20", "2026-08-05", "2026-04-18", "2026-02-27", "2026-03-10"],
        "reportDate": ["", "2026-06-30", "", "2025-12-31", ""],
        "accessionNumber": ["0000004242-26-000011", "0000004242-26-000012",
                            "0000004242-26-000008", "0000004242-26-000003",
                            "0000004242-26-000005"],
        "primaryDocDescription": ["", "", "", "", ""],
        "primaryDocument": ["e.htm", "q.htm", "e2.htm", "k.htm", "p.htm"],
        "items": ["2.02,9.01", "", "7.01", "", ""],
        "size": [1, 2, 3, 4, 5],
    }},
}


class TestOneDownloadAcrossMaxFilings(unittest.TestCase):
    def test_every_max_filings_value_uses_the_cik_keyed_fetch(self):
        calls = []

        def fake(cik):
            calls.append(cik)
            return SUBS

        with patch.object(sc, "_submissions_json", fake):
            one = sc.get_filing_info(4242, max_filings=1)
            fifty = sc.get_filing_info(4242)
            eighty = sc.get_filing_info(4242, max_filings=80)
        # The fetch never sees max_filings — so the memo key is the CIK alone.
        self.assertEqual([4242, 4242, 4242], calls)
        self.assertEqual(1, len(one["recent_filings"]))
        self.assertEqual(5, len(fifty["recent_filings"]))
        self.assertEqual(5, len(eighty["recent_filings"]))
        self.assertEqual("WA", one["hq_state"])
        self.assertTrue(fifty["recent_filings"][0]["is_earnings"])      # 8-K item 2.02
        self.assertFalse(fifty["recent_filings"][2]["is_earnings"])     # 8-K item 7.01

    def test_fetch_failure_is_empty_dict(self):
        with patch.object(sc, "_submissions_json", lambda cik: None):
            self.assertEqual({}, sc.get_filing_info(4242, max_filings=80))

    def test_fetch_uses_shared_retry_and_returns_json(self):
        class _Resp:
            def json(self):
                return SUBS

        with patch("data.http.get_with_retry", return_value=_Resp()) as g:
            got = sc._submissions_json.__wrapped__(4242) \
                if hasattr(sc._submissions_json, "__wrapped__") else sc._submissions_json(4242)
        self.assertEqual(SUBS, got)
        self.assertIn("CIK0000004242.json", g.call_args[0][0])


class TestMemoShape(unittest.TestCase):
    """Structural: the download is a bounded st.cache_data keyed on cik only,
    and get_filing_info no longer does its own HTTP."""

    SRC = (Path(__file__).resolve().parent.parent / "data" / "sec_client.py"
           ).read_text(encoding="utf-8")

    def _fn(self, name):
        tree = ast.parse(self.SRC)
        return next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == name)

    def test_download_keyed_on_cik_only_and_bounded(self):
        fn = self._fn("_submissions_json")
        self.assertEqual(["cik"], [a.arg for a in fn.args.args])
        deco = ast.unparse(fn.decorator_list[0])
        self.assertIn("st.cache_data", deco)
        self.assertIn("max_entries", deco)
        self.assertIn("ttl=900", deco)

    def test_get_filing_info_does_not_fetch_itself(self):
        body = ast.unparse(self._fn("get_filing_info"))
        self.assertNotIn("get_with_retry", body)
        self.assertIn("_submissions_json(cik)", body)


if __name__ == "__main__":
    unittest.main()
