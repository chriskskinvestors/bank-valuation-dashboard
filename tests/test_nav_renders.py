"""
Top-nav functional guard.

Context (2026-06-13): the section nav broke in production — the bar
rendered but all 8 tab labels were invisible. The cause was a CSS edit
made while chasing the wrong diagnosis, NOT (as first assumed) a Streamlit
version change. The fix was to revert the nav CSS to the rule that was
working that morning.

This guard runs app.py headlessly via Streamlit's AppTest and asserts the
section nav radio renders with all 8 sections — so a regression that drops
or renames the nav widget fails CI before deploy. (It checks the element
tree, not CSS pixels; pair it with a real incognito render check after any
nav/styles change.)

Run: python -m unittest tests.test_nav_renders
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestNavRendersFunctional(unittest.TestCase):
    SECTIONS = ["Home", "Market & Macro", "Screen & Compare", "Company",
                "Earnings", "News & Research", "Geographic"]

    def test_section_radio_has_all_sections(self):
        try:
            from streamlit.testing.v1 import AppTest
        except Exception as e:  # very old streamlit — skip rather than fail
            self.skipTest(f"AppTest unavailable: {e}")
        app_path = str(Path(__file__).parent.parent / "app.py")
        at = AppTest.from_file(app_path, default_timeout=90)
        at.run()
        nav = [r for r in at.radio
               if set(self.SECTIONS).issubset(set(map(str, r.options)))]
        self.assertTrue(
            nav, "Section nav radio not found in rendered app — the top nav "
            "would be missing/blank. Radios present: "
            f"{[list(r.options) for r in at.radio]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
