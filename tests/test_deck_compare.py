"""
Deck comparison-table rows (Third Coast / Great Plains 425, 2026-10-07):
"Price / LTM EPS 12.3x 9.3x" pairs the peer median with the deal, so the
deck's P/E and P/TBV read as two distinct values and blanked. Fixture text is
verbatim from the filing (slide colour-code noise included).
"""
from __future__ import annotations

import unittest

BULLETS = ("Implied aggregate transaction value of $240 million \u2022 Price / tangible book "
           "value per share: 1.56x LIGHT GRAY Transaction Value (2) 210, 210, 210 \u2022 Price "
           "/ LTM EPS: 9.3x and Multiples (2) \u2022 Price / LTM EPS + Fully Phased-in Cost "
           "Savings: 6.1x BLACK (3) \u2022 Core deposit premium: 7.7% 0, 0, 0 ACCENT 1")
TABLE = ("TCBX / Great Plains Multiples 255, 255, 255 (1) High Performing Targets BODY TEXT "
         "0, 0, 0 LIGHT GRAY (2) Price / Tangible Book Value 1.62x 1.56x 210, 210, 210 BLACK "
         "0, 0, 0 (3) ACCENT 1 Price / LTM EPS 12.3x 9.3x 1, 51, 81 ACCENT 2 15, 158, 213 (3) "
         "ACCENT 3 Price / LTM EPS + Cost Savings 8.2x 6.1x 233, 113, 50 ACCENT 4 25, 107, 36 "
         "(4) Core Deposit Premium ACCENT 5 9.3% 7.7% 160, 43, 147")


class TestDeckComparisonRows(unittest.TestCase):

    def test_bullets_win_over_the_comparison_table(self):
        from data.ma_announcements import extract_deck_metrics
        self.assertEqual(extract_deck_metrics(BULLETS + " " + TABLE),
                         {"deck_p_tbv": 1.56, "deck_p_e_ltm": 9.3,
                          "deck_core_dep_premium": 0.077})

    def test_table_alone_is_ambiguous(self):
        # which column is the deal is not knowable from the row itself
        from data.ma_announcements import extract_deck_metrics
        m = extract_deck_metrics(TABLE)
        self.assertIsNone(m["deck_p_tbv"])
        self.assertIsNone(m["deck_p_e_ltm"])


if __name__ == "__main__":
    unittest.main()
