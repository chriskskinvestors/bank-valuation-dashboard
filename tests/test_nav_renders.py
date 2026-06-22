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

    def _section_nav(self, at):
        nav = [r for r in at.radio
               if set(self.SECTIONS).issubset(set(map(str, r.options)))]
        return nav[0] if nav else None

    def test_section_radio_has_all_sections(self):
        try:
            from streamlit.testing.v1 import AppTest
        except Exception as e:  # very old streamlit — skip rather than fail
            self.skipTest(f"AppTest unavailable: {e}")
        app_path = str(Path(__file__).parent.parent / "app.py")
        at = AppTest.from_file(app_path, default_timeout=180)
        at.run()
        self.assertIsNotNone(
            self._section_nav(at),
            "Section nav radio not found in rendered app — the top nav "
            "would be missing/blank. Radios present: "
            f"{[list(r.options) for r in at.radio]}")

    def test_can_leave_company_when_bank_in_url(self):
        """Regression (2026-06-22): on a Company page reached via ?bank=, clicking
        another section (e.g. Geographic) just reloaded Company. Cause: the
        ?bank= deep-link redirect fired on EVERY rerun, re-forcing nav_section
        back to Company before the radio's fresh choice could take — and before
        the post-nav cleanup that strips the stale bank param. The redirect must
        fire only when the bank param newly ARRIVES, not while it lingers."""
        try:
            from streamlit.testing.v1 import AppTest
        except Exception as e:
            self.skipTest(f"AppTest unavailable: {e}")
        app_path = str(Path(__file__).parent.parent / "app.py")
        at = AppTest.from_file(app_path, default_timeout=180)
        # Land on a Company page exactly as a deep-link would: ?s=Company&bank=.
        at.query_params["s"] = "Company"
        at.query_params["bank"] = "AMAL"
        at.run()
        nav = self._section_nav(at)
        self.assertIsNotNone(nav, "nav radio missing on Company deep-link load")
        self.assertEqual(nav.value, "Company",
                         "deep-link ?bank= should open Company")
        # Now click away to Geographic — it must STICK, not bounce to Company.
        self._section_nav(at).set_value("Geographic").run()
        self.assertEqual(
            self._section_nav(at).value, "Geographic",
            "clicking Geographic from a ?bank= Company page bounced back to "
            "Company — the deep-link redirect re-trapped the nav")


if __name__ == "__main__":
    unittest.main(verbosity=2)
