"""Pin the release-metrics extractors (data/release_metrics.py, 2026-07-06).

Cardinal-rule tests on realistic earnings-release prose: happy paths per
metric, prior-period comparisons never captured (first-% discipline + before-
label qualifiers), non-GAAP variants excluded, disagreement → None, bands,
denominator pinning for credit ratios, and the ROE/ROTCE label separation.

Run: python -m unittest tests.test_release_metrics
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.release_metrics import extract_release_metrics


def x(html: str) -> dict:
    return extract_release_metrics(html)


class TestPercentMetrics(unittest.TestCase):
    def test_nim_with_trailing_comparison_not_captured(self):
        m = x("<p>Net interest margin of 3.42%, compared with 3.38% in the "
              "prior quarter and 3.19% a year ago.</p>")
        self.assertEqual(m["nim"], 3.42)

    def test_prior_period_restatement_before_label_excluded(self):
        m = x("<p>Net interest margin expanded to 3.42%, up from a net "
              "interest margin of 3.19% in the year-ago quarter.</p>")
        # "up from a …" disqualifies the second candidate; only 3.42 survives.
        self.assertEqual(m["nim"], 3.42)

    def test_two_clean_disagreeing_candidates_yield_none(self):
        m = x("<p>Net interest margin was 3.42%. Net interest margin was "
              "3.38%.</p>")
        self.assertIsNone(m["nim"])

    def test_adjusted_variant_excluded_gaap_survives(self):
        m = x("<p>The efficiency ratio was 55.3%; the adjusted efficiency "
              "ratio was 52.1%.</p>")
        self.assertEqual(m["efficiency"], 55.3)

    def test_roa_happy(self):
        self.assertEqual(x("<p>Return on average assets of 1.15%.</p>")["roa"],
                         1.15)

    def test_roe_and_rotce_do_not_cross_match(self):
        m = x("<p>Return on average common equity was 12.4% and return on "
              "average tangible common equity was 15.2%.</p>")
        self.assertEqual(m["roe"], 12.4)
        self.assertEqual(m["rotce"], 15.2)

    def test_rotce_alone_never_fills_roe(self):
        m = x("<p>Return on average tangible common equity of 15.2%.</p>")
        self.assertEqual(m["rotce"], 15.2)
        self.assertIsNone(m["roe"])

    def test_nco_dollar_then_ratio_form(self):
        m = x("<p>Net charge-offs were $12.3 million, or 0.25% of average "
              "loans, annualized.</p>")
        self.assertEqual(m["nco_ratio"], 0.25)

    def test_band_rejects_implausible(self):
        self.assertIsNone(x("<p>Net interest margin of 34.2%.</p>")["nim"])

    def test_absent_metric_is_none(self):
        self.assertIsNone(x("<p>Strong quarter across the board.</p>")["nim"])


class TestTableRowRejection(unittest.TestCase):
    """Flattened multi-period table rows must NEVER be captured — which period
    the first cell holds is column order, i.e. luck (2026-07-06 spot-check)."""

    def test_pct_table_row_rejected(self):
        m = x("<p>Net interest margin 3.71 % 3.69 % 3.66 %</p>")
        self.assertIsNone(m["nim"])

    def test_bare_label_value_without_verb_rejected(self):
        m = x("<p>Net interest margin (NIM) 2.95 2.84 2.78</p>")
        self.assertIsNone(m["nim"])

    def test_dollar_table_row_rejected(self):
        m = x("<p>Tangible book value per share (non-GAAP) (1) $14.87 $14.60 "
              "$14.13 $13.44</p>")
        self.assertIsNone(m["tbv_ps"])
        m2 = x("<p>Dividends per share $0.21 $0.21 $0.20 $0.20</p>")
        self.assertIsNone(m2["div_ps"])

    def test_verb_never_matches_inside_a_word(self):
        # "of" inside "charge-offs" must not act as the verb before 208.
        m = x("<p>Total net loan charge-offs 208 162 205</p>")
        self.assertIsNone(m["nco_ratio"])

    def test_adjusted_between_label_and_value_rejected(self):
        m = x("<p>The efficiency ratio, as adjusted, was 52.1%.</p>")
        self.assertIsNone(m["efficiency"])

    def test_prose_with_comparison_still_extracts(self):
        # The guards must not break normal prose (connective text separates values).
        m = x("<p>Net interest margin was 3.42%, compared with 3.38% for the "
              "prior quarter.</p>")
        self.assertEqual(m["nim"], 3.42)


class TestPinnedCreditRatios(unittest.TestCase):
    def test_npa_value_then_denominator(self):
        m = x("<p>Nonperforming assets were $45.2 million, or 0.42% of total "
              "assets.</p>")
        self.assertEqual(m["npa_assets"], 0.42)

    def test_npa_wrong_denominator_never_qualifies(self):
        m = x("<p>Nonperforming assets were 0.42% of total loans.</p>")
        self.assertIsNone(m["npa_assets"])

    def test_acl_label_form(self):
        m = x("<p>The allowance for credit losses to total loans was "
              "1.21%.</p>")
        self.assertEqual(m["acl_loans"], 1.21)

    def test_acl_value_then_denominator_form(self):
        m = x("<p>The allowance for credit losses was $210 million, or 1.21% "
              "of total loans held for investment.</p>")
        self.assertEqual(m["acl_loans"], 1.21)


class TestPerShare(unittest.TestCase):
    def test_tbv_plain(self):
        m = x("<p>Tangible book value per share of $23.45.</p>")
        self.assertEqual(m["tbv_ps"], 23.45)

    def test_tbv_growth_then_value_captures_value_not_prior(self):
        m = x("<p>Tangible book value per common share increased 8.2% to "
              "$23.45 from $21.67.</p>")
        self.assertEqual(m["tbv_ps"], 23.45)

    def test_tbv_prior_value_alone_not_captured(self):
        m = x("<p>Tangible book value per share was up 8.2% from $21.67.</p>")
        self.assertIsNone(m["tbv_ps"])

    def test_dividend_declared(self):
        m = x("<p>The Board declared a quarterly cash dividend of $0.23 per "
              "common share.</p>")
        self.assertEqual(m["div_ps"], 0.23)

    def test_dividend_raised_to_form(self):
        m = x("<p>The Board increased the quarterly dividend to $0.24 per "
              "share.</p>")
        self.assertEqual(m["div_ps"], 0.24)

    def test_special_dividend_excluded(self):
        m = x("<p>The Board declared a special dividend of $1.00 per "
              "share.</p>")
        self.assertIsNone(m["div_ps"])

    def test_regular_and_special_disagree_safely(self):
        m = x("<p>A quarterly cash dividend of $0.23 per share and a special "
              "dividend of $1.00 per share.</p>")
        self.assertEqual(m["div_ps"], 0.23)   # special is excluded, not merged


class TestRealisticComposite(unittest.TestCase):
    RELEASE = """
    <h1>Bancorp Reports Second Quarter 2026 Results</h1>
    <p>Net income of $52.3 million, or $1.31 per diluted share. Return on
    average assets of 1.24%, return on average common equity of 11.8% and
    return on average tangible common equity of 14.6%, compared with 1.19%,
    11.2% and 13.9%, respectively, for the first quarter of 2026.</p>
    <p>Net interest margin of 3.55%, up from 3.47% in the linked quarter.
    The efficiency ratio improved to 54.2%.</p>
    <p>Nonperforming assets were $31.0 million, or 0.28% of total assets.
    Net charge-offs were $4.1 million, or 0.11% of average loans. The
    allowance for credit losses to total loans was 1.18%.</p>
    <p>Tangible book value per share increased 9.1% to $27.83. The Board
    declared a quarterly cash dividend of $0.27 per share.</p>
    """

    def test_full_extraction(self):
        m = x(self.RELEASE)
        self.assertEqual(m["roa"], 1.24)
        self.assertEqual(m["roe"], 11.8)
        self.assertEqual(m["rotce"], 14.6)
        self.assertEqual(m["nim"], 3.55)
        self.assertEqual(m["efficiency"], 54.2)
        self.assertEqual(m["npa_assets"], 0.28)
        self.assertEqual(m["nco_ratio"], 0.11)
        self.assertEqual(m["acl_loans"], 1.18)
        self.assertEqual(m["tbv_ps"], 27.83)
        self.assertEqual(m["div_ps"], 0.27)


if __name__ == "__main__":
    unittest.main(verbosity=2)
