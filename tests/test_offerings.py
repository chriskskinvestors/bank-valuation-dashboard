"""
Tests for data/offerings.py — Detailed Offerings classification/extraction
(docs/SNL-BUILD-PLAN.md §14). All HTTP mocked. Pins (cover phrasings taken
verbatim from live-verified Banner documents, 2026-07-13):

  • 424B cover classification precedence: merger prospectus > preliminary >
    resale (no company proceeds) > DCM > ECM > Unclassified
  • DCM gross from "aggregate principal amount"; ECM price+gross from the
    SEC cover price table with the total>>per-share sanity check, and the
    shares × lone-price fallback
  • 8-K Item 3.02 = private placement; 3.02 + 2.01 SKIPPED (acquisition
    consideration, not a raise); PP gross ambiguity -> n/a
  • missing primary document (old archived filings) -> honest row, still
    cached; an HTTP failure -> None, nothing cached
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

DCM_COVER = ("Prospectus Supplement (To Prospectus dated June 12, 2020) "
             "$100,000,000 5.000% Fixed-to-Floating Rate Subordinated Notes "
             "due 2030 We are offering $100,000,000 aggregate principal "
             "amount of our 5.00% Fixed-to-Floating Rate Subordinated Notes "
             "due 2030")
ECM_COVER = ("75,000,000 Shares Common Stock We are offering 75,000,000 "
             "shares of our common stock. Per Share Total Public offering "
             "price $ 2.00 $ 150,000,000 Underwriting discounts $ 0.10 "
             "$ 7,500,000")
MERGER_COVER = ("PROSPECTUS relating to the merger of Pacific Financial "
                "with Banner pursuant to the Agreement and Plan of Merger "
                "dated April 2026")
PRELIM_COVER = ("Subject to Completion, dated June 25, 2020 $ % "
                "Fixed-to-Floating Rate Subordinated Notes due 2030")
RESALE_COVER = ("124,000 Shares of Fixed Rate Cumulative Perpetual Preferred "
                "Stock, Series A ... sold by the United States Department of "
                "the Treasury. We will not receive any proceeds from the "
                "sale of any Preferred Shares sold by Treasury.")


class TestClassify424B(unittest.TestCase):

    def test_dcm_with_stated_principal(self):
        from data.offerings import classify_424b
        c = classify_424b(DCM_COVER)
        self.assertEqual(c["kind"], "DCM")
        self.assertEqual(c["gross_usd"], 100_000_000)
        self.assertIn("Subordinated Notes due 2030", c["security"])

    def test_ecm_price_table(self):
        from data.offerings import classify_424b
        c = classify_424b(ECM_COVER)
        self.assertEqual(c["kind"], "ECM")
        self.assertEqual(c["gross_usd"], 150_000_000)   # $2.00 x 75M (table)
        self.assertEqual(c["price_per_share"], 2.0)
        self.assertEqual(c["security"], "Common Stock")

    def test_ecm_fallback_shares_times_price(self):
        from data.offerings import classify_424b
        c = classify_424b("Common Stock We are offering 3,500,000 shares "
                          "at a public offering price of $45.00 per share")
        self.assertEqual(c["kind"], "ECM")
        self.assertEqual(c["gross_usd"], 157_500_000)

    def test_precedence_merger_preliminary_resale(self):
        from data.offerings import classify_424b
        self.assertEqual(classify_424b(MERGER_COVER)["kind"],
                         "Merger prospectus")
        self.assertEqual(classify_424b(PRELIM_COVER)["kind"], "Preliminary")
        r = classify_424b(RESALE_COVER)
        self.assertEqual(r["kind"], "Resale (selling holder)")
        self.assertIsNone(r["gross_usd"])

    def test_unmatched_cover_unclassified(self):
        from data.offerings import classify_424b
        c = classify_424b("Rule 424(b)(3) filing with no cover signals")
        self.assertEqual(c["kind"], "Unclassified")
        self.assertIsNone(c["gross_usd"])

    def test_price_table_sanity_rejects_garbage(self):
        # total NOT >> per-share (two small numbers) -> no gross claimed.
        from data.offerings import classify_424b
        c = classify_424b("Common Stock Public offering price $ 2.00 $ 3.00")
        self.assertEqual(c["kind"], "ECM")
        self.assertIsNone(c["gross_usd"])


class TestPPGross(unittest.TestCase):

    def test_single_and_million_forms(self):
        from data.offerings import extract_pp_gross
        self.assertEqual(extract_pp_gross(
            "the Company sold $25,000,000 aggregate principal amount of "
            "subordinated notes in a private placement"), 25_000_000)
        self.assertEqual(extract_pp_gross(
            "gross proceeds of $25 million"), 25_000_000)

    def test_ambiguous_is_none(self):
        from data.offerings import extract_pp_gross
        self.assertIsNone(extract_pp_gross(
            "sold $25,000,000 of notes and issued $10,000,000 of warrants"))


def _filings(rows):
    return [{"form": f, "date": d, "accession": a, "doc": doc, "items": it}
            for f, d, a, doc, it in rows]


class TestGetOfferings(unittest.TestCase):

    def _wire(self, docs, fail=()):
        def side_effect(url, params=None, headers=None, timeout=30):
            for frag in fail:
                if frag in url:
                    raise Exception("down")
            fn = url.rsplit("/", 1)[-1]
            r = MagicMock()
            r.text = docs[fn]
            r.raise_for_status = MagicMock()
            return r
        return side_effect

    @patch("data.offerings.time.sleep", lambda *_: None)
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.offerings.requests.get")
    @patch("data.offerings.iter_submission_filings")
    def test_full_flow(self, mock_iter, mock_get, _cg, mock_cput):
        from data import offerings
        mock_iter.return_value = (_filings([
            ("424B2", "2020-06-26", "0001-20-1", "dcm.htm", ""),
            ("S-3ASR", "2020-06-12", "0001-20-0", "s3.htm", ""),
            ("8-K", "2014-11-12", "0001-14-1", "pp.htm", "3.02,9.01"),
            ("8-K", "2015-10-02", "0001-15-1", "acq.htm", "2.01,3.02"),
            ("424B3", "1998-10-09", "0001-98-1", "", ""),   # no primary doc
        ]), True)
        mock_get.side_effect = self._wire({
            "dcm.htm": f"<html>{'We are offering $100,000,000 aggregate principal amount of 5.00% Subordinated Notes due 2030'}</html>",
            "pp.htm": "<html>gross proceeds of $25 million in a private placement</html>",
        })
        rows = offerings.get_offerings(CIK)
        kinds = {r["date"]: r["kind"] for r in rows}
        self.assertEqual(kinds, {
            "2020-06-26": "DCM", "2020-06-12": "Shelf registration",
            "2014-11-12": "Private placement", "1998-10-09": "Unclassified"})
        self.assertNotIn("2015-10-02", kinds)   # 2.01+3.02 skipped
        pp = next(r for r in rows if r["kind"] == "Private placement")
        self.assertEqual(pp["gross_usd"], 25_000_000)
        # newest-first; missing-doc row cached fine
        self.assertEqual([r["date"] for r in rows],
                         sorted([r["date"] for r in rows], reverse=True))
        key, payload = mock_cput.call_args[0]
        self.assertEqual(key, f"offerings:v1:{CIK}")
        self.assertEqual(payload["rows"], rows)

    @patch("data.offerings.time.sleep", lambda *_: None)
    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.offerings.requests.get")
    @patch("data.offerings.iter_submission_filings")
    def test_doc_fetch_failure_uncacheable(self, mock_iter, mock_get, _cg, mock_cput):
        from data import offerings
        mock_iter.return_value = (_filings([
            ("424B2", "2020-06-26", "0001-20-1", "dcm.htm", "")]), True)
        mock_get.side_effect = self._wire({}, fail=("dcm.htm",))
        self.assertIsNone(offerings.get_offerings(CIK))
        mock_cput.assert_not_called()

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.offerings.iter_submission_filings",
           return_value=([], False))
    def test_submissions_failure_uncacheable(self, _mi, _cg, mock_cput):
        from data import offerings
        self.assertIsNone(offerings.get_offerings(CIK))
        mock_cput.assert_not_called()

    @patch("data.offerings.requests.get")
    @patch("data.cache.get")
    def test_cache_hit(self, mock_cget, mock_get):
        from data import offerings
        rows = [{"date": "2020-06-26", "form": "424B2", "kind": "DCM",
                 "security": "x", "gross_usd": 1, "price_per_share": None,
                 "url": None, "accession": "a"}]
        mock_cget.return_value = {"rows": rows,
                                  "cached_at": datetime.now().isoformat()}
        self.assertEqual(offerings.get_offerings(CIK), rows)
        mock_get.assert_not_called()

    def test_falsy_cik(self):
        from data.offerings import get_offerings
        self.assertIsNone(get_offerings(None))


if __name__ == "__main__":
    unittest.main()
