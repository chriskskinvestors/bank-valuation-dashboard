"""UX-review P1 regressions — Screen results + Compare (2026-09-30).

UX-P1-08  Screen said "Ran 15:07 — results below." at 11:07 ET: time.strftime
          printed the Cloud Run container's UTC clock, unlabeled.
UX-P1-07  The 27-column Screen results table clipped its last columns with no
          visible horizontal scrollbar; Ticker/Bank scrolled away.
UX-P1-10  Compare › Metrics Table: the Export button overlapped the last visible
          row (stMarkdownContainer's margin-bottom:-1rem under a div-ending block).
UX-P1-12  Compare cells: absent → "—", a REPORTED zero → "$0.0M"; and the custom
          scatter drew an absent bubble-size as the smallest bubble (`or 0`).

Run: python -m unittest tests.test_ux_p1_screen_compare -v
"""
import contextlib
import os
import re
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

from tests import _streamlit_stub

_streamlit_stub.install()

import ui.generic_table as gt  # noqa: E402
import ui.peer_comparison as pc  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


# ── UX-P1-08 ────────────────────────────────────────────────────────────

class TestScreenRanAtEt(unittest.TestCase):

    def test_utc_afternoon_is_eastern_morning_labeled(self):
        # The review's exact case: 15:07 UTC on 2026-09-24 is 11:07 EDT.
        self.assertEqual(
            gt.ran_at_et(datetime(2026, 9, 24, 15, 7, tzinfo=timezone.utc)), "11:07 ET")

    def test_standard_time_offset(self):
        # January is EST (UTC-5), not a fixed -4.
        self.assertEqual(
            gt.ran_at_et(datetime(2026, 1, 15, 15, 7, tzinfo=timezone.utc)), "10:07 ET")

    def test_default_is_now_and_labeled(self):
        self.assertRegex(gt.ran_at_et(), r"^\d{2}:\d{2} ET$")

    def test_app_no_longer_stamps_screen_runs_with_the_container_clock(self):
        with open(os.path.join(_ROOT, "app.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn('time.strftime("%H:%M")', src)
        self.assertIn("_ran_at = ran_at_et()", src)


# ── UX-P1-07 ────────────────────────────────────────────────────────────

class TestScreenTableScroll(unittest.TestCase):

    COLS = ["price", "roaa"]

    def _render(self):
        calls = []
        with mock.patch.object(gt, "st", _fake_st(calls)):
            gt.render_generic_table(
                [{"ticker": "ABCB", "price": 81.62, "roaa": 1.23},
                 {"ticker": "AMBK", "price": 23.43, "roaa": None}],
                self.COLS, table_key="p1_07")
        html = next((c for c in calls if "scrn-wrap" in c), None)
        self.assertIsNotNone(html, "renderer emitted no table HTML")
        return html

    def test_box_scrolls_with_a_visible_scrollbar(self):
        html = self._render()
        self.assertIn(".scrn-wrap{max-height:660px;overflow:auto;", html)
        self.assertIn(".scrn-wrap::-webkit-scrollbar{width:12px;height:12px;}", html)
        self.assertIn(".scrn-wrap::-webkit-scrollbar-thumb{background:var(--text-muted);}",
                      html)
        # The next element must not be pulled up over the scrollbar strip.
        self.assertIn('div[data-testid="stMarkdownContainer"]:has(> .scrn-wrap:last-child)'
                      "{margin-bottom:0 !important;}", html)

    def test_ticker_and_bank_are_frozen_left(self):
        html = self._render()
        self.assertIn(".scrn-wrap th:nth-child(-n+2),.scrn-wrap td:nth-child(-n+2)"
                      "{position:sticky;", html)
        self.assertRegex(html, r"\.scrn-wrap th:first-child,\.scrn-wrap td:first-child"
                               r"\{left:0;[^}]*width:64px;")
        self.assertIn(".scrn-wrap th:nth-child(2),.scrn-wrap td:nth-child(2){left:64px;", html)
        # Frozen body cells are opaque so scrolled cells don't show through.
        self.assertIn(".scrn-wrap td:nth-child(-n+2){background:var(--bg-base);}", html)

    def test_sticky_selectors_hit_ticker_then_bank(self):
        # The nth-child selectors are only right while Ticker/Bank are the
        # first two cells of every header and body row.
        html = self._render()
        self.assertRegex(html, r"<thead><tr><th>Ticker</th><th class=\"nm\">Bank</th>")
        rows = re.findall(r"<tr>(<td.*?)</tr>", html)
        self.assertEqual(len(rows), 2)
        for tr in rows:
            cells = re.findall(r"<td[^>]*>.*?</td>", tr)
            self.assertIn('class="lnk tk"', cells[0])
            self.assertTrue(cells[1].startswith('<td class="nm">'))
        self.assertIn(">—</td>", rows[1])   # absent roaa stays an em dash


# ── UX-P1-10 / UX-P1-12 (Compare › Metrics Table) ───────────────────────

class TestCompareMetricsTable(unittest.TestCase):

    COHORT = [
        # AMBK-like: FDIC reports P3LNLS = 0 (a genuine zero).
        {"ticker": "ZERO", "past_due_30_89": 0.0, "npl_ratio": 0.0},
        # Absent past-due (never reported / not joined).
        {"ticker": "NONE", "past_due_30_89": None, "npl_ratio": 0.4},
        {"ticker": "SOME", "past_due_30_89": 3_401_000.0, "npl_ratio": 0.3},
    ]

    def _render(self):
        calls = []
        with mock.patch.object(pc, "st", _fake_st(calls)), \
                mock.patch.object(pc, "_render_headline_charts", lambda *a, **k: None):
            pc._render_metrics_table(self.COHORT, self.COHORT, ["Credit"])
        html = next((c for c in calls if "cmp-wrap" in c), None)
        self.assertIsNotNone(html, "renderer emitted no table HTML")
        return html

    def _row(self, html, label):
        for tr in re.findall(r"<tr>(.*?)</tr>", html):
            if f'<td class="nm">{label}</td>' in tr:
                return re.findall(r"<td[^>]*>(.*?)</td>", tr)
        self.fail(f"no row {label!r}")

    def test_absent_is_dash_and_reported_zero_is_zero(self):
        cells = self._row(self._render(), "PD 30-89 ($M)")
        # [label, ZERO, NONE, SOME, Peer Median]
        self.assertEqual(cells[1:4], ["$0.0M", "—", "$3.4M"])
        # Median over the two REPORTED values only (0 and 3.401M) — the absent
        # bank is not counted as a zero.
        self.assertEqual(cells[4], "$1.7M")

    def test_export_button_is_not_pulled_over_the_table(self):
        self.assertIn('div[data-testid="stMarkdownContainer"]:has(> .cmp-wrap:last-child)'
                      "{margin-bottom:0 !important;}", self._render())


class TestCustomScatterAbsentSize(unittest.TestCase):

    def test_absent_size_is_not_the_smallest_bubble(self):
        peers = [
            {"ticker": "BIG", "roaa": 1.0, "nim": 3.0, "total_assets": 100e9},
            {"ticker": "SMALL", "roaa": 1.2, "nim": 3.2, "total_assets": 0.0},
            {"ticker": "GONE", "roaa": 1.1, "nim": 3.1, "total_assets": None},
        ]
        with mock.patch.object(pc, "get_name", lambda t: t):
            fig = pc._build_scatter(peers, {"name": "t", "x": "roaa", "y": "nim",
                                            "x_label": "x", "y_label": "y",
                                            "size": "total_assets"})
        mk = fig.data[0].marker
        by_tk = dict(zip(fig.data[0].text, zip(mk.size, mk.symbol,
                                                (c[3] for c in fig.data[0].customdata))))
        self.assertEqual(by_tk["BIG"], (40, "circle", "$100.0B"))
        self.assertEqual(by_tk["SMALL"][:2], (12, "circle"))     # a real zero
        self.assertEqual(by_tk["GONE"], (18, "circle-open", "—"))  # absent

    def test_uniform_when_no_size_metric(self):
        peers = [{"ticker": t, "roaa": 1.0 + i, "nim": 3.0 + i}
                 for i, t in enumerate(["A", "B"])]
        with mock.patch.object(pc, "get_name", lambda t: t):
            fig = pc._build_scatter(peers, {"name": "t", "x": "roaa", "y": "nim",
                                            "x_label": "x", "y_label": "y"})
        self.assertEqual(list(fig.data[0].marker.size), [18, 18])
        self.assertEqual(list(fig.data[0].marker.symbol), ["circle", "circle"])


if __name__ == "__main__":
    unittest.main()
