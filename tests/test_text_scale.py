"""Text-size preference (Extras ▸ Text size) — the root-font-size mechanism.

The whole stylesheet is rem-based, so ONE html{font-size} rule scales all app
text. Ladder shifted up 2026-09-02 (owner: "make all three bigger"): A− is the
old default 15px, and the DEFAULT is now 112% — every size key injects a rule,
computed off Streamlit's REAL 15px root (measured on prod), not the browser's
16px.
"""
import re
import unittest

from ui.styles import TEXT_SCALE_DEFAULT, TEXT_SCALES, text_scale_css


class TestTextScaleCss(unittest.TestCase):
    def test_pixel_values_per_key(self):
        # 15px × 1.0 / 1.12 / 1.25 — hand-computed off the measured root.
        self.assertIn("font-size: 15.00px", text_scale_css("sm"))
        self.assertIn("font-size: 16.80px", text_scale_css("md"))
        self.assertIn("font-size: 18.75px", text_scale_css("lg"))

    def test_default_is_md_and_injects_112pct(self):
        # The default now CHANGES rendering for everyone (owner decision):
        # no inject-nothing path — default is 16.80px.
        self.assertEqual("md", TEXT_SCALE_DEFAULT)
        self.assertIn("font-size: 16.80px", text_scale_css(TEXT_SCALE_DEFAULT))

    def test_rule_targets_the_root_element(self):
        for key in TEXT_SCALES:
            with self.subTest(key=key):
                m = re.search(
                    r"^<style>html \{ font-size: [\d.]+px; \}</style>$",
                    text_scale_css(key))
                self.assertIsNotNone(m, "must be a single html{font-size} rule")

    def test_mangled_query_param_falls_back_to_default(self):
        for junk in ("xl", "", "12", "LG", None):
            with self.subTest(junk=junk):
                self.assertEqual(text_scale_css(TEXT_SCALE_DEFAULT),
                                 text_scale_css(junk))

    def test_scale_keys_are_stable(self):
        # ?fs= values live in users' bookmarks — renaming a key breaks them.
        self.assertEqual({"sm", "md", "lg"}, set(TEXT_SCALES))


if __name__ == "__main__":
    unittest.main()
