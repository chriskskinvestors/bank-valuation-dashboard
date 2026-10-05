"""UX-review P2 regressions — Screen, Compare, Trends (2026-09-24 review).

UX-P2-09  Screen builder: the `As of` control + help icon sat ~600 px right of
          Scope (Scope's secondary picker is empty for "All banks").
UX-P2-10  Screen results: EPS rendered "$-1.54" (sign after the "$").
UX-P2-11  Screen results headers "ROATCE Bl." / "ROATCE adj" / "1-time" with no
          expansion; the "1-time" column was all blank in the Valuation default.
UX-P2-12  Compare › Metrics Table used half its column.
UX-P2-13  Trends carried a "KSK Investors · QUARTERLY TRENDS" title bar that
          Screen and Compare do not have.

Run: python -m unittest tests.test_ux_p2_screen_compare -v
"""
import contextlib
import os
import re
import types
import unittest
from unittest import mock

from tests import _streamlit_stub

_streamlit_stub.install()

import ui.generic_table as gt  # noqa: E402
import ui.peer_comparison as pc  # noqa: E402
from config import METRICS_BY_KEY, TABS  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app_src() -> str:
    with open(os.path.join(_ROOT, "app.py"), encoding="utf-8") as f:
        return f.read()


def _fake_st(calls: list):
    """Just enough streamlit for the two table renderers (captures markdown)."""
    return types.SimpleNamespace(
        markdown=lambda body, **k: calls.append(str(body)),
        warning=lambda *a, **k: None,
        columns=lambda spec, **k: [contextlib.nullcontext()
                                   for _ in (spec if isinstance(spec, list) else range(spec))],
        dialog=lambda *a, **k: (lambda f: f),
        button=lambda *a, **k: False,
    )


def _screen_html(rows, cols):
    calls = []
    with mock.patch.object(gt, "st", _fake_st(calls)):
        gt.render_generic_table(rows, cols, table_key="p2")
    html = next((c for c in calls if "scrn-wrap" in c), None)
    assert html is not None, "renderer emitted no table HTML"
    return html


# ── UX-P2-09 ────────────────────────────────────────────────────────────

class TestScreenBuilderRow(unittest.TestCase):

    def test_as_of_sits_between_table_and_scope(self):
        src = _app_src()
        i_tbl = src.index('"Table", options=_ordered_keys')
        i_asof = src.index('"As of", _asof_opts')
        i_scope = src.index('_scope_type = st.selectbox("Scope"')
        self.assertLess(i_tbl, i_asof)
        self.assertLess(i_asof, i_scope)
        # ...and in the adjacent column slots, so nothing floats right of an
        # empty secondary picker.
        self.assertRegex(src[i_tbl - 200:i_tbl], r"with r1\[0\]:")
        self.assertRegex(src[i_asof - 200:i_asof], r"with r1\[1\]:")
        self.assertRegex(src[i_scope - 200:i_scope], r"with r1\[2\]:")


# ── UX-P2-10 ────────────────────────────────────────────────────────────

class TestScreenNegativeEps(unittest.TestCase):

    def test_sign_before_the_dollar(self):
        html = _screen_html([{"ticker": "ABCB", "eps": -1.54}], ["eps"])
        self.assertIn(">-$1.54</td>", html)
        self.assertNotIn("$-1.54", html)


# ── UX-P2-11 ────────────────────────────────────────────────────────────

class TestScreenCrypticHeaders(unittest.TestCase):

    COLS = ["roatce_blended", "roatce_normalized", "earnings_distorted"]

    def test_short_headers_carry_the_full_label_as_tooltip(self):
        html = _screen_html(
            [{"ticker": "ABCB", "roatce_blended": 14.2, "roatce_normalized": 13.1,
              "earnings_distorted": True}], self.COLS)
        self.assertIn('<th title="ROATCE Blended">ROATCE Bl.</th>', html)
        self.assertIn('<th title="ROATCE Adjusted">ROATCE adj</th>', html)
        self.assertIn('<th title="One-time Earnings Item">1-time</th>', html)

    def test_every_valuation_default_header_is_explained(self):
        # A header shorter than its label must still expand on hover — the
        # renderer's tooltip is the label, so header != label is the contract.
        for k in TABS[0]["columns"]:
            m = METRICS_BY_KEY[k]
            if m.get("header"):
                self.assertNotEqual(m["header"], m["label"], k)

    def test_column_picker_label_is_spelled_out(self):
        # The Columns multiselect and the export use the label, not the header.
        self.assertEqual(METRICS_BY_KEY["earnings_distorted"]["label"],
                         "One-time Earnings Item")

    def test_all_blank_one_time_flag_is_not_a_valuation_default(self):
        self.assertEqual(TABS[0]["key"], "valuation")
        self.assertNotIn("earnings_distorted", TABS[0]["columns"])
        # The flag stays selectable — never removed from the metric set.
        self.assertIn("earnings_distorted", METRICS_BY_KEY)


# ── UX-P2-12 ────────────────────────────────────────────────────────────

class TestCompareTableFullWidth(unittest.TestCase):

    def test_metrics_table_fills_its_column(self):
        cohort = [{"ticker": "AAA", "npl_ratio": 0.3}, {"ticker": "BBB", "npl_ratio": 0.4}]
        calls = []
        with mock.patch.object(pc, "st", _fake_st(calls)), \
                mock.patch.object(pc, "_render_headline_charts", lambda *a, **k: None):
            pc._render_metrics_table(cohort, cohort, ["Credit"])
        html = next((c for c in calls if "cmp-wrap" in c), None)
        self.assertIsNotNone(html, "renderer emitted no table HTML")
        self.assertIn(".cmp-wrap table{width:100%;}", html)


# ── UX-P2-13 ────────────────────────────────────────────────────────────

class TestTrendsHeaderMatchesScreenCompare(unittest.TestCase):

    @staticmethod
    def _block(src, head):
        start = src.index(head)
        nxt = re.search(r"\nelif section ==", src[start + len(head):])
        return src[start:start + len(head) + (nxt.start() if nxt else len(src))]

    def test_no_title_bar_on_any_screen_compare_view(self):
        src = _app_src()
        for head in ('elif section == "Screen & Compare" and sc_sub == "Screen" and screening_tab:',
                     'elif section == "Screen & Compare" and sc_sub == "Compare":',
                     'elif section == "Screen & Compare" and sc_sub == "Trends":'):
            self.assertNotIn("title_bar(", self._block(src, head), head)
        self.assertNotIn('"Quarterly Trends"', src)

    def test_trends_keeps_its_provenance_caption(self):
        blk = self._block(_app_src(),
                          'elif section == "Screen & Compare" and sc_sub == "Trends":')
        self.assertIn('st.caption("One metric across recent quarters', blk)

    def test_compare_page_has_no_title_bar(self):
        with open(os.path.join(_ROOT, "ui", "peer_comparison.py"), encoding="utf-8") as f:
            self.assertNotIn("title_bar(", f.read())


if __name__ == "__main__":
    unittest.main()
