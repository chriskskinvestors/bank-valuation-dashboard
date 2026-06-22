"""Regression: filing summaries with dollar amounts must not break the
filings-table render.

Streamlit runs KaTeX over st.markdown even with unsafe_allow_html=True, so an
unescaped "$…$" pair in a summary ("$1.35 billion … $300 million") was parsed
as a math span — which mangled the surrounding HTML and dumped the whole
<table> as raw text on the Company → News & Filings page. _no_latex neutralizes
'$' to an HTML entity that KaTeX can't see.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ui.filings import _no_latex  # noqa: E402


class TestNoLatex(unittest.TestCase):
    def test_strips_dollar_pairs(self):
        s = "On May 26, 2026, PNC completed a $1.35 billion offering plus $300 million"
        out = _no_latex(s)
        self.assertNotIn("$", out)
        self.assertIn("&#36;1.35 billion", out)
        self.assertIn("&#36;300 million", out)

    def test_single_dollar_also_neutralized(self):
        self.assertEqual(_no_latex("raised the dividend to $1.60"),
                         "raised the dividend to &#36;1.60")

    def test_no_dollar_unchanged(self):
        s = "PNC named a new Chief Risk Officer effective July 1, 2026."
        self.assertEqual(_no_latex(s), s)

    def test_empty_and_none_safe(self):
        self.assertEqual(_no_latex(""), "")
        self.assertEqual(_no_latex(None), None)


if __name__ == "__main__":
    unittest.main()
