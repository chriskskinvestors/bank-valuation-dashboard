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


WAFD = ("Implied Valuation vs. Regional Bank Index(1): 2027E ROATCE vs. Price / Tangible Book "
        "Value (2) y = 12.68x – 0.16 R2 = 62% Illustrative Combined Company Tangible book "
        "value per share at close: $29.24 2027E ROATCE:(2) 15%+ Regression implied price / TBV: "
        "1.79x Implied share price: $52.41 Implied upside to WaFd shareholders: ~44% Price / TBV "
        "at announcement 1.18x")
BYLINE = ("this equates to an aggregate transaction value of $87.9 million or $261.23 per "
          "share(1) Transaction Multiples Price / TBV: 1.07x(1) Core Deposit Premium: 1.6%(1) "
          "LTM Earnings: 13.1x(1) Pro Forma Impacts Minimal TBV dilution")
THFF = ("Ownership: 92% THFF | 8% First Illinois ∙ $111.3MM in aggregate⁽²⁾ "
        "∙ $45.00 implied transaction value per share ∙ 135% of tangible book value "
        "∙ 13.0x LTM earnings ∙ 7.4x 2028E earnings + fully phased-in cost savings "
        "∙ 5.3% premium on core deposits")


class TestCombinedCompanyMultiple(unittest.TestCase):

    def test_regression_implied_multiple_is_not_the_deal(self):
        from data.ma_announcements import extract_deck_metrics
        self.assertEqual(extract_deck_metrics(WAFD),
                         {"deck_p_tbv": None, "deck_p_e_ltm": None,
                          "deck_core_dep_premium": None})

    def test_deal_multiples_still_read(self):
        from data.ma_announcements import extract_deck_metrics
        self.assertEqual(extract_deck_metrics(BYLINE),
                         {"deck_p_tbv": 1.07, "deck_p_e_ltm": 13.1,
                          "deck_core_dep_premium": 0.016})
        self.assertEqual(extract_deck_metrics(THFF),
                         {"deck_p_tbv": 1.35, "deck_p_e_ltm": 13.0,
                          "deck_core_dep_premium": 0.053})


class TestAggregateOnlySplit(unittest.TestCase):

    def test_bluevine_split_names_the_issuer(self):
        # Valley/Bluevine EX-99.1 2026-09-28, verbatim
        from data.ma_announcements import extract_terms
        t = extract_terms(
            "Under the terms of the proposed transaction, Valley will acquire Bluevine for "
            "total consideration of approximately $340 million. The consideration is expected "
            "to consist of approximately 75% cash and 25% Valley common stock, subject to the "
            "terms of the definitive agreement and customary adjustments.")
        self.assertEqual((t["consideration"], t["stock_pct"], t["cash_pct"], t["mix_basis"]),
                         ("mixed", 25.0, 75.0, "stated"))
        self.assertIsNone(t["exchange_ratio"])
        self.assertIsNone(t["cash_per_share"])


if __name__ == "__main__":
    unittest.main()
