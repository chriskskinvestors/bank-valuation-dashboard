"""
Regression test for AUDIT-2026-07-02 P2 #38: AI filing summaries + item chips
were injected into an unsafe_allow_html table with only _no_latex ($ handling),
so '<', '>', '"', '&' in an AI-generated summary could inject markup; hrefs were
neither validated nor escaped. filings._safe() must HTML-escape (then neutralize
$), and the View/Index links must go through is_safe_news_url + html-escaping.
"""
import sys
import types
import unittest

# Stub streamlit + its components package before importing ui.filings.
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

import ui.filings as F  # noqa: E402
from data.events.wire_base import is_safe_news_url  # noqa: E402


class TestFilingsEscaping(unittest.TestCase):
    def test_safe_escapes_html(self):
        out = F._safe("<script>alert(1)</script>")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_safe_escapes_quotes_and_amp(self):
        out = F._safe('a & "b" <c>')
        self.assertNotIn("<c>", out)
        self.assertIn("&amp;", out)
        self.assertIn("&quot;", out)  # can't break out of an attribute

    def test_safe_neutralizes_dollar_without_double_escaping(self):
        # $ -> &#36; (KaTeX-safe), and the entity's '&' is NOT re-escaped.
        out = F._safe("$1.35 billion")
        self.assertEqual(out, "&#36;1.35 billion")
        self.assertNotIn("&amp;#36;", out)

    def test_safe_handles_empty_and_none(self):
        self.assertEqual(F._safe(""), "")
        self.assertEqual(F._safe(None), "")

    def test_url_gate_rejects_dangerous_schemes(self):
        # The href gate the table uses — javascript:/data: have no host → rejected.
        self.assertFalse(is_safe_news_url("javascript:alert(1)"))
        self.assertFalse(is_safe_news_url("data:text/html,<script>1</script>"))
        # Real filing URLs pass.
        self.assertTrue(is_safe_news_url("https://www.sec.gov/Archives/edgar/x.htm"))


if __name__ == "__main__":
    unittest.main()
