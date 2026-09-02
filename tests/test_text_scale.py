"""Text-size preference (Extras ▸ Text size) — the root-font-size mechanism.

The whole stylesheet is rem-based, so ONE html{font-size} rule scales all app
text. These tests pin the contract: exact px per size key, an empty string for
the default (zero-change path for users who never touch the control), and a
safe fallback for mangled ?fs= values.
"""
import re
import unittest

from ui.styles import TEXT_SCALE_DEFAULT, TEXT_SCALES, text_scale_css


class TestTextScaleCss(unittest.TestCase):
    def test_default_injects_nothing(self):
        self.assertEqual("", text_scale_css(TEXT_SCALE_DEFAULT))

    def test_small_and_large_pixel_values(self):
        # 15px × 0.9 and 15px × 1.12 — hand-computed off Streamlit's REAL root
        # font-size (measured on prod 2026-09-02), not the browser's 16px.
        self.assertIn("font-size: 13.50px", text_scale_css("sm"))
        self.assertIn("font-size: 16.80px", text_scale_css("lg"))

    def test_rule_targets_the_root_element(self):
        m = re.search(r"<style>html \{ font-size: [\d.]+px; \}</style>",
                      text_scale_css("lg"))
        self.assertIsNotNone(m, "must be a single html{font-size} rule")

    def test_mangled_query_param_falls_back_to_default(self):
        for junk in ("xl", "", "12", "LG", None):
            with self.subTest(junk=junk):
                self.assertEqual("", text_scale_css(junk))

    def test_scale_keys_are_stable(self):
        # ?fs= values live in users' bookmarks — renaming a key breaks them.
        self.assertEqual({"sm", "md", "lg"}, set(TEXT_SCALES))
        self.assertEqual("md", TEXT_SCALE_DEFAULT)


if __name__ == "__main__":
    unittest.main()
