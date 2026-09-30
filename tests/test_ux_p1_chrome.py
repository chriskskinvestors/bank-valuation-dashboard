"""UX-review P1 global chrome fixes (2026-09-30). String-level pins — the
pixels are verified on prod by the main thread.

UX-P1-14  un-keyed horizontal st.radio groups rendered as square checkboxes
          (the global zero-radius rule squares the radio circle). ONE global
          rule set renders them as Home-style pill buttons, excluding every
          radio group that already has dedicated keyed styling.
UX-P1-17  the empty-state "◦"-in-a-box icon read as a broken image — removed.
UX-P1-32  the top nav wrapped "Geographic" to a second row at ~1380px; a
          <1500px step keeps one row down to 1280px.

Run: python -m unittest tests.test_ux_p1_chrome -v
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests import _streamlit_stub  # noqa: F401

REPO = Path(__file__).resolve().parent.parent

# A keyed radio style scope: `<scope> [div][role="radiogroup"]`.
_KEYED_RADIO_SCOPE = re.compile(
    r'(\.st-key-[A-Za-z0-9_-]+|\[class\*="st-key-[A-Za-z0-9_-]+"\]'
    r'|section\[data-testid="stSidebar"\])\s*(?:div)?\[role="radiogroup"\]')


def _media_block(css: str, query: str) -> str:
    """Body of the `@media (<query>) { ... }` block (brace-matched)."""
    start = css.index(f"@media ({query})")
    i = css.index("{", start) + 1
    depth = 1
    j = i
    while depth:
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
        j += 1
    return css[i:j - 1]


class TestRadioPills(unittest.TestCase):
    def setUp(self):
        from ui import styles
        self.styles = styles
        self.css = styles.CUSTOM_CSS

    def test_single_style_block_contains_pill_rules(self):
        # Injected inside the ONE <style> block (after the radius override),
        # not as a stray second block.
        self.assertEqual(1, self.css.count("<style>"))
        self.assertEqual(1, self.css.count("</style>"))
        g = self.styles._RADIO_PILL_GROUP
        self.assertIn(g + " > label {", self.css)
        self.assertLess(self.css.index(g), self.css.index("</style>"))
        self.assertGreater(self.css.index(g),
                           self.css.index("*, *::before, *::after"))

    def test_scope_is_horizontal_stradio_only(self):
        g = self.styles._RADIO_PILL_GROUP
        self.assertTrue(g.startswith(
            'div[data-testid="stRadio"] [role="radiogroup"]'
            '[aria-orientation="horizontal"]'), g)
        # Vertical radios keep the native look: nothing targets them.
        self.assertNotIn('aria-orientation="vertical"', self.css)
        # Checkboxes are never touched by this rule set.
        self.assertNotIn("stCheckbox", g)

    def test_pill_look(self):
        g = self.styles._RADIO_PILL_GROUP
        css = self.css
        # native marker hidden (structure-proof hider, never positional)
        self.assertIn(
            g + ' > label div:not([data-testid="stMarkdownContainer"])'
            ':not(:has([data-testid="stMarkdownContainer"])) {', css)
        # bordered pill
        self.assertRegex(css, re.escape(g + " > label {")
                         + r"[^}]*border: 1px solid rgba\(49, 51, 63, 0\.2\)")
        # selected = Home segmented_controlActive: primary 10% fill + border
        self.assertRegex(css, re.escape(g + " > label:has(input:checked) {")
                         + r"[^}]*background: rgba\(37, 99, 235, 0\.10\)"
                         r"[^}]*border-color: #2563eb")
        self.assertIn(g + " > label:has(input:checked) "
                      '[data-testid="stMarkdownContainer"] p {', css)
        # keyboard focus survives hiding the native marker
        self.assertIn(g + " > label:has(input:focus-visible) {", css)

    def test_every_keyed_radio_style_is_excluded(self):
        """Any radio group with its own keyed CSS anywhere in the app must be
        in the exclusion list, or the global pill rule would stack borders
        onto its tabs/underlines. Scans the real sources so a NEW keyed style
        added without updating the list fails here."""
        excluded = set(self.styles._RADIO_PILL_EXCLUDE)
        found = {}
        for path in REPO.rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if rel.startswith((".claude/", "tests/")):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for m in _KEYED_RADIO_SCOPE.finditer(text):
                found.setdefault(m.group(1), rel)
        # The scan must actually see the known keyed styles (guards the regex).
        for known in (".st-key-topnav", ".st-key-company_section_nav",
                      ".st-key-sc_subnav", ".st-key-macro_section_nav",
                      '[class*="st-key-lazytabs_"]',
                      'section[data-testid="stSidebar"]'):
            self.assertIn(known, found)
        missing = {s: f for s, f in found.items() if s not in excluded}
        self.assertEqual({}, missing,
                         "keyed radio styles not excluded from the global "
                         "pill rule (add them to _RADIO_PILL_EXCLUDE)")

    def test_exclusions_are_in_the_selector(self):
        g = self.styles._RADIO_PILL_GROUP
        self.assertIn(":not(:is(", g)
        inner = g[g.index(":not(:is(") + len(":not(:is("):]
        self.assertTrue(inner.endswith(") *)"), g)
        for scope in self.styles._RADIO_PILL_EXCLUDE:
            self.assertIn(scope, inner)


class TestEmptyStateNoIcon(unittest.TestCase):
    def test_markup_has_no_icon(self):
        from ui import states
        captured = []
        st = states.st
        orig = getattr(st, "markdown", None)
        st.markdown = lambda body, **k: captured.append(body)
        try:
            states.empty_state("Nothing here", hint="why")
        finally:
            if orig is None:
                del st.markdown
            else:
                st.markdown = orig
        body = captured[0]
        self.assertNotIn('class="ico"', body)
        self.assertNotIn("&#9702;", body)
        self.assertTrue(body.startswith(
            '<div class="ksk-empty"><div class="l1">Nothing here</div>'), body)
        self.assertIn('<div class="l2">why</div>', body)

    def test_css_has_no_icon_rule(self):
        from ui.styles import CUSTOM_CSS
        self.assertNotIn(".ksk-empty .ico", CUSTOM_CSS)
        self.assertIn(".ksk-empty .l1", CUSTOM_CSS)


class TestTopNavOneRow(unittest.TestCase):
    def setUp(self):
        from ui.styles import CUSTOM_CSS
        self.css = CUSTOM_CSS
        self.narrow = _media_block(CUSTOM_CSS, "max-width: 1500px")

    def test_existing_1700_step_kept(self):
        self.assertIn("@media (max-width: 1700px)", self.css)
        self.assertLess(self.css.index("@media (max-width: 1700px)"),
                        self.css.index("@media (max-width: 1500px)"))

    def test_tab_text_shrinks(self):
        # Streamlit sizes the label's stMarkdownContainer itself, so the size
        # must land on the container/p, not only on the label.
        self.assertRegex(
            self.narrow,
            r'\.st-key-topnav \[role="radiogroup"\] label '
            r'\[data-testid="stMarkdownContainer"\] p \{\s*'
            r"font-size: var\(--fs-sm\) !important;")
        self.assertIn('.st-key-topnav [role="radiogroup"] label '
                      "{ padding: 4px 7px !important; }", self.narrow)

    def test_band_columns_resplit_by_content(self):
        n = self.narrow
        self.assertRegex(n, r'div\[data-testid="stColumn"\]:has\(\.ksk-brand\) \{\s*'
                            r"flex: 0 0 9rem !important; width: 9rem !important;")
        nav = re.search(r'stColumn"\]:has\(\.st-key-topnav\) \{\s*flex: 1 1 '
                        r"calc\((\d+(?:\.\d+)?)% - 1rem\) !important", n)
        search = re.search(r'stColumn"\]:has\(\.st-key-nav_bank_search\) \{\s*'
                           r"flex: 1 1 calc\((\d+(?:\.\d+)?)% - 1rem\) !important", n)
        self.assertIsNotNone(nav)
        self.assertIsNotNone(search)
        nav_pct, search_pct = float(nav.group(1)), float(search.group(1))
        # Search cedes share to the nav; together they never exceed the
        # share top_nav's ratios give them ([0.78, 4.35, 1.42, 0.95]).
        orig_nav = 4.35 / 7.5 * 100
        orig_search = 1.42 / 7.5 * 100
        self.assertGreater(nav_pct, orig_nav)
        self.assertLess(search_pct, orig_search)
        self.assertLessEqual(nav_pct + search_pct, orig_nav + orig_search)

    def test_top_nav_ratios_unchanged(self):
        src = (REPO / "ui" / "chrome.py").read_text(encoding="utf-8")
        self.assertIn("st.columns([0.78, 4.35, 1.42, 0.95]", src)


if __name__ == "__main__":
    unittest.main()
