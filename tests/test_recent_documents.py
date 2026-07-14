"""
Recent Documents (Company → News & Filings) — pins the pure logic:

  • parse_pdf_param: the ?pdf= handler must reject anything that isn't a
    strictly-shaped accession/filename/archives-path — it rebuilds sec.gov
    URLs server-side, so a malformed param must parse to None, never to a
    fetchable URL (SSRF/scheme-injection guard).
  • classify_filings: each filing lands in exactly one panel; earnings 8-Ks
    go to Annuals/Interims (CapIQ convention), DEFM14A to Merger Documents.
  • Menu/row HTML: data-derived text is escaped for the unsafe_allow_html
    sink (same _safe contract test_filings_escaping pins) and hrefs are
    attribute-escaped.
"""
import sys
import types
import unittest

# Stub streamlit + its components package before importing ui modules
# (same stub as test_filings_escaping — keep in sync if st grows new
# module-load APIs; see the 2026-07-02 stub-rot fix).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
sys.modules.setdefault("streamlit", _st)
_comp = types.ModuleType("streamlit.components")
_v1 = types.ModuleType("streamlit.components.v1")
_comp.v1 = _v1
sys.modules.setdefault("streamlit.components", _comp)
sys.modules.setdefault("streamlit.components.v1", _v1)

import ui.recent_documents as RD  # noqa: E402


def _f(form, date="2026-01-01", items="", is_earnings=False, acc="0001193125-26-077437",
       url="https://www.sec.gov/Archives/edgar/data/1068851/000119312526077437/doc.htm",
       index_url="https://www.sec.gov/Archives/edgar/data/1068851/000119312526077437/index.html"):
    return {"form": form, "date": date, "items": items, "is_earnings": is_earnings,
            "accession": acc, "url": url, "index_url": index_url}


class TestParsePdfParam(unittest.TestCase):
    def test_valid_doc(self):
        out = RD.parse_pdf_param("doc|0001193125-26-077437|pb-20251231.htm")
        self.assertEqual(out, {"kind": "doc", "accession": "0001193125-26-077437",
                               "doc": "pb-20251231.htm"})

    def test_valid_er_sec_tx(self):
        self.assertEqual(RD.parse_pdf_param("er|0001193125-26-188851")["kind"], "er")
        sec = RD.parse_pdf_param("sec|Archives/edgar/data/1068851/000119312526077437/ex99.htm")
        self.assertEqual(sec["kind"], "sec")
        tx = RD.parse_pdf_param("tx|2026|1")
        self.assertEqual((tx["year"], tx["quarter"]), (2026, 1))

    def test_valid_call_report_quarter_ends_only(self):
        self.assertEqual(RD.parse_pdf_param("call|03312026"),
                         {"kind": "call", "period": "03312026"})
        for raw in ("call|03302026",   # not a quarter-end day
                    "call|13312026",   # month 13
                    "call|3312026",    # 7 digits
                    "call|03312026|x", # arity
                    "call|03/31/2026"):  # separators not allowed
            self.assertIsNone(RD.parse_pdf_param(raw), raw)

    def test_rejects_malformed(self):
        bad = [
            "", "doc", "doc|badacc|file.htm",                     # shape/accession
            "doc|0001193125-26-077437|../../etc/passwd",          # traversal
            "doc|0001193125-26-077437|a/b.htm",                   # path separator
            "sec|Archives/edgar/data/../secret",                  # traversal
            "sec|https://evil.example/x",                         # scheme smuggle
            "url|https://www.sec.gov/x.htm",                      # no raw-URL kind
            "tx|26|1", "tx|2026|x",                               # bad year/quarter
            "doc|0001193125-26-077437|f.htm|extra",               # arity
        ]
        for raw in bad:
            self.assertIsNone(RD.parse_pdf_param(raw), raw)


class TestClassifyFilings(unittest.TestCase):
    def test_panels(self):
        filings = [
            _f("10-K"), _f("10-Q"), _f("11-K"), _f("ARS"),
            _f("8-K", items="2.02,9.01", is_earnings=True),
            _f("8-K", items="5.02"),
            _f("DEF 14A"), _f("PRE 14A"), _f("DEFM14A"),
            _f("S-4"), _f("425"),
            _f("SC 13G"), _f("S-3"), _f("15-12B"),
            _f("4"),  # insider form — belongs to no panel
        ]
        p = RD.classify_filings(filings)
        forms = {k: [x["form"] for x in v] for k, v in p.items()}
        self.assertEqual(forms["annuals"], ["10-K", "10-Q", "11-K", "ARS", "8-K"])
        self.assertEqual(forms["current"], ["8-K"])
        self.assertEqual(forms["proxies"], ["DEF 14A", "PRE 14A"])
        self.assertEqual(forms["mergers"], ["DEFM14A", "S-4", "425"])
        self.assertEqual(forms["regulatory"], ["SC 13G", "S-3", "15-12B"])
        # exactly-one-panel: nothing double-counted, "4" nowhere
        total = sum(len(v) for v in p.values())
        self.assertEqual(total, len(filings) - 1)

    def test_earnings_8k_is_er_row(self):
        f = _f("8-K", items="2.02", is_earnings=True)
        self.assertEqual(RD.doc_label(f), "Earnings Release (ER)")
        self.assertEqual(RD.doc_label(_f("10-K")), "10-K (10-K)")
        self.assertEqual(RD.doc_label(_f("ARS")), "Annual Report (ARS)")


class TestMenuHtmlEscaping(unittest.TestCase):
    def test_label_and_sub_escaped(self):
        out = RD._menu_html('<script>alert(1)</script>',
                            [("View HTML", "https://www.sec.gov/x.htm")],
                            sub='$1.35 billion & "more"')
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("&#36;1.35", out)      # KaTeX-safe dollar
        self.assertIn("&amp;", out)

    def test_href_attribute_escaped(self):
        out = RD._menu_html("Doc", [("View HTML",
                                     'https://www.sec.gov/x.htm"onmouseover="x')])
        self.assertNotIn('.htm"onmouseover', out)
        self.assertIn("&quot;onmouseover", out)

    def test_no_links_renders_inert_label(self):
        out = RD._menu_html("Doc", [])
        self.assertNotIn("<details", out)

    def test_filing_menu_pdf_param_requires_valid_accession(self):
        good = RD._filing_menu(_f("10-K"), "PB")
        self.assertIn("pdf=doc%7C0001193125-26-077437%7Cdoc.htm", good)
        bad = RD._filing_menu(_f("10-K", acc="ACC-INVALID"), "PB")
        self.assertNotIn("pdf=", bad)

    def test_earnings_menu_uses_er_kind(self):
        out = RD._filing_menu(_f("8-K", items="2.02", is_earnings=True), "PB")
        self.assertIn("pdf=er%7C0001193125-26-077437", out)


if __name__ == "__main__":
    unittest.main()
