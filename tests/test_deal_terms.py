"""
Tests for the structured deal terms behind the Transactions › Recent Deals
tab (owner directive 2026-10-05): data/ma_announcements term extractors +
build_terms, data/deal_comps.merger_arb and _ttm_eps_at (P/E at announce),
and ui/transactions._recent_rows. All network mocked.

Every fixture sentence is VERBATIM from the live filing it names (hand-read
2026-10-05) and every expected number is hand-computed:

  FHB/TriCo 2026-07-13 (all-stock)      — PR + merger-agreement 8-K Item 1.01
  Catalyst/Lakeside 2026-04-08 (cash)   — 8-K Item 1.01/7.01 + EX-99 PR/deck
  Old Second/Bancorp Fin. 2025-02-25 (mixed, fixed) — 8-K Item 1.01 + EX-99
  Seacoast/Villages 2025-05-29 (election) — 8-K Item 1.01 + EX-99
  QNB/Victory 2025-09-23                — 8-K Item 1.01 (fee; close range)

Negatives pin the strict style: distinct candidates -> None, "cash in lieu of
fractional shares" is not cash consideration, a core-deposit premium is not a
price premium, an unpinned close phrase yields no date.
"""
import unittest
from datetime import date
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

from data.ma_announcements import (  # noqa: E402
    build_terms,
    classify_consideration,
    close_phrase_to_date,
    extract_cash_per_share,
    extract_exchange_ratio,
    extract_expected_close,
    extract_implied_price,
    extract_mix_pcts,
    extract_premium_pct,
    extract_termination_fee,
    extract_terms,
    implied_offer,
)

# ── Verbatim fixtures ─────────────────────────────────────────────────────

FHB_PR = (
    "First Hawaiian, Inc. (NASDAQ: FHB) and TriCo Bancshares (NASDAQ: TCBK) "
    "today announced a definitive agreement. Pursuant to the terms of the "
    "agreement, TriCo's shareholders will receive 2.095 First Hawaiian shares "
    "for each TriCo share, representing $63.12 per share as of First "
    "Hawaiian's closing stock price on July 10, 2026. Upon closing of the "
    "transaction, First Hawaiian and TriCo shareholders are expected to own "
    "approximately 65% and 35%, respectively, of the combined company. The "
    "boards of directors of both companies have unanimously approved the "
    "definitive agreement and the parties expect to close the transaction by "
    "the end of 2026, subject to the receipt of required regulatory "
    "approvals, approval by First Hawaiian and TriCo shareholders and the "
    "satisfaction of customary closing conditions. Premium deposit franchise "
    "preserves funding advantage. Consideration mix – 100% common stock – "
    "Fixed exchange ratio – 2.095 shares of FHB common stock to be exchanged "
    "for each share of TCBK common stock.")
FHB_8K_101 = (
    "Holders of TriCo Common Stock will receive cash in lieu of fractional "
    "shares. The Merger Agreement provides certain termination rights for "
    "both FHI and TriCo and further provides that a termination fee of "
    "$80,000,000 will be payable by either FHI or TriCo in the event of "
    "termination of the Merger Agreement under certain circumstances.")

CLST_PR = (
    "Catalyst Bancorp, Inc. (NASDAQ: CLST) Announces Agreement to Acquire "
    "Lakeside Bancshares, Inc. Under the terms of the Merger Agreement, and "
    "subject to the completion of the Mergers, shareholders of Lakeside "
    "Bancshares (other than holders of Dissenting Shares, as such term is "
    "defined in the Merger Agreement) will receive $19.58 in cash for each "
    "outstanding share of Lakeside Bancshares common stock, or $41.1 million "
    "in aggregate, subject to adjustment under certain circumstances. Once "
    "the transaction closes, Lakeside's shareholders (other than dissenting "
    "shares) will receive $19.58 per share in cash, or $41.1 million in "
    "aggregate, subject to adjustment under certain circumstances. The "
    "transaction is expected to close in the third quarter of 2026, subject "
    "to customary closing conditions, including regulatory approvals and "
    "Lakeside shareholder approval. Subject to the receipt of all required "
    "approvals and the satisfaction of all other conditions, the Merger is "
    "expected to be completed in the third quarter of 2026. Aggregate deal "
    "value of $41.1M – Per share deal value of $19.58 – Price / TBV: 113.9%")

OSBC_8K = (
    "Old Second Bancorp, Inc. (NASDAQ: OSBC) announced a definitive "
    "agreement. Each Bancorp Financial stockholder will receive 2.5814 shares "
    "(the \"Exchange Ratio\") of Old Second common stock (the \"Stock "
    "Consideration\") and $15.93 in cash (the \"Cash Consideration\") for "
    "each share of Bancorp Financial common stock owned by the stockholder. "
    "Under the terms of the merger agreement, which has been unanimously "
    "approved by the Boards of Directors of both companies, Bancorp Financial "
    "stockholders will receive 2.5814 shares of Old Second common stock and "
    "$15.93 in cash for each share of Bancorp Financial's common stock, for "
    "total consideration consisting of approximately 75% stock and 25% cash. "
    "Based on the closing price of Old Second common stock of $18.08 per "
    "share on February 24, 2025, the implied purchase price is $62.60 per "
    "Bancorp Financial common share, with an aggregate transaction value of "
    "approximately $197 million. The merger is expected to close in the "
    "third quarter of 2025, subject to satisfaction of customary closing "
    "conditions, including receipt of required regulatory approvals and "
    "approval by the stockholders of Bancorp Financial. The Merger Agreement "
    "provides certain termination rights for both Old Second and Bancorp "
    "Financial and further provides that a termination fee of $8,500,000 "
    "will be payable by Bancorp Financial to Old Second upon termination of "
    "the Merger Agreement under certain circumstances.")

SBCF_8K = (
    "Seacoast Banking Corporation of Florida (NASDAQ: SBCF) announced a "
    "definitive agreement. Upon completion of the Merger, each share of VBI "
    "common stock will be converted at closing into the right to receive (i) "
    "$1,000.00 in cash, (ii) 38.5000 shares of Seacoast common stock (the "
    "\"Exchange Ratio\") or (iii) a 25%-75% combination of cash and Seacoast "
    "common stock, at the shareholder's election. The final election will be "
    "subject to a proration mechanism such that 25% of VBI shares of common "
    "stock will receive the cash consideration and 75% of VBI shares of "
    "common stock will receive the stock consideration. The Merger Agreement "
    "provides certain termination rights for both Seacoast and VBI, and "
    "further provides that a termination fee of $31.4 million will be "
    "payable by VBI upon termination of the Merger Agreement under certain "
    "circumstances.")

QNBC_8K = (
    "The Merger is expected to close in the fourth quarter of 2025 or first "
    "quarter of 2026, pending satisfaction of various closing conditions. "
    "Upon termination of the Merger Agreement under specified circumstances, "
    "Victory may be required to pay a termination fee to QNB of $1,575,000.")


class TestCashPerShare(unittest.TestCase):

    def test_catalyst_both_phrasings_one_value(self):
        self.assertEqual(extract_cash_per_share(CLST_PR), 19.58)

    def test_old_second_cash_leg(self):
        self.assertEqual(extract_cash_per_share(OSBC_8K), 15.93)

    def test_seacoast_election_cash_option(self):
        self.assertEqual(extract_cash_per_share(SBCF_8K), 1000.0)

    def test_cash_in_lieu_of_fractional_is_not_cash(self):
        self.assertIsNone(extract_cash_per_share(FHB_8K_101))
        self.assertIsNone(extract_cash_per_share(FHB_PR))

    def test_aggregate_in_cash_is_not_per_share(self):
        self.assertIsNone(extract_cash_per_share(
            "will pay $41.1 million in cash for all outstanding shares"))

    def test_distinct_amounts_ambiguous(self):
        self.assertIsNone(extract_cash_per_share(
            "receive $19.58 in cash for each share ... receive $20.00 in cash "
            "for each share"))


class TestExchangeRatioForms(unittest.TestCase):

    def test_mixed_form_old_second(self):
        r = extract_exchange_ratio(OSBC_8K)
        self.assertIsNotNone(r)
        self.assertEqual(r[0], 2.5814)
        self.assertIn("Old Second", r[1])
        self.assertIn("Bancorp Financial", r[2])

    def test_election_form_seacoast(self):
        r = extract_exchange_ratio(SBCF_8K)
        self.assertIsNotNone(r)
        self.assertEqual(r[0], 38.5)
        self.assertEqual((r[1], r[2]), ("Seacoast", "VBI"))

    def test_fhb_bare_form_still_single(self):
        self.assertEqual(extract_exchange_ratio(FHB_PR)[0], 2.095)


class TestImpliedPricePremiumFee(unittest.TestCase):

    def test_fhb_stated_per_share(self):
        self.assertEqual(extract_implied_price(FHB_PR), 63.12)

    def test_old_second_implied_purchase_price(self):
        self.assertEqual(extract_implied_price(OSBC_8K), 62.6)

    def test_catalyst_deck_per_share_value(self):
        self.assertEqual(extract_implied_price(CLST_PR), 19.58)

    def test_premium_only_in_price_context(self):
        self.assertEqual(extract_premium_pct(
            "The offer represents a premium of approximately 28% to the "
            "closing price of the target's common stock on March 3, 2026."),
            28.0)
        self.assertEqual(extract_premium_pct(
            "a 31.5% premium to the 20-day volume-weighted average price"),
            31.5)
        # Core-deposit / book premiums are not price premiums; FHB states none.
        self.assertIsNone(extract_premium_pct(
            "implies a core deposit premium of 8.5% and a price to tangible "
            "book value of 1.98x"))
        self.assertIsNone(extract_premium_pct(FHB_PR))
        self.assertIsNone(extract_premium_pct(
            "a premium of 28% to the closing price ... a premium of 35% to "
            "the 30-day VWAP"))                   # two distinct -> n/a

    def test_termination_fees(self):
        self.assertEqual(extract_termination_fee(FHB_8K_101), 80_000_000)
        self.assertEqual(extract_termination_fee(OSBC_8K), 8_500_000)
        self.assertEqual(extract_termination_fee(SBCF_8K), 31_400_000)
        self.assertEqual(extract_termination_fee(QNBC_8K), 1_575_000)
        self.assertIsNone(extract_termination_fee(FHB_PR))
        self.assertIsNone(extract_termination_fee(
            "a termination fee of $5.0 million payable by A ... a termination "
            "fee of $12.0 million payable by B"))   # two fees -> n/a
        self.assertIsNone(extract_termination_fee(
            "a termination fee equal to 4% of the aggregate deal value"))


class TestExpectedClose(unittest.TestCase):

    def test_phrase_to_date_table(self):
        table = [
            ("in the third quarter of 2026", "2026-09-30"),
            ("during the first quarter of 2027", "2027-03-31"),
            ("in Q4 2026", "2026-12-31"),
            ("in the second half of 2026", "2026-12-31"),
            ("in the first half of 2027", "2027-06-30"),
            ("by the end of 2026", "2026-12-31"),
            ("by year-end 2026", "2026-12-31"),
            ("in March 2027", "2027-03-31"),
            ("on or before March 31, 2027", "2027-03-31"),
            ("in the fourth quarter of 2025 or first quarter of 2026",
             "2026-03-31"),                          # range -> later bound
            ("in early 2027", None),
            ("in mid-2027", None),
            ("later this year", None),
        ]
        for phrase, want in table:
            with self.subTest(phrase=phrase):
                self.assertEqual(close_phrase_to_date(phrase), want)

    def test_fhb_catalyst_old_second_qnb(self):
        self.assertEqual(extract_expected_close(FHB_PR),
                         ("by the end of 2026", "2026-12-31"))
        # Two statements (PR + 8-K) resolving to the same quarter end.
        self.assertEqual(extract_expected_close(CLST_PR),
                         ("in the third quarter of 2026", "2026-09-30"))
        self.assertEqual(extract_expected_close(OSBC_8K),
                         ("in the third quarter of 2025", "2025-09-30"))
        self.assertEqual(extract_expected_close(QNBC_8K),
                         ("in the fourth quarter of 2025 or first quarter of "
                          "2026", "2026-03-31"))
        self.assertEqual(extract_expected_close(FHB_8K_101), (None, None))

    def test_dated_statement_wins_over_vague_one(self):
        # FHB's 425 legend says "by the end of the year"; the PR pins 2026.
        self.assertEqual(extract_expected_close(
            "The transaction is expected to close by the end of the year. "
            + FHB_PR), ("by the end of 2026", "2026-12-31"))

    def test_conflicting_statements_keep_phrase_drop_date(self):
        phrase, d = extract_expected_close(
            "expected to close in the third quarter of 2026. ... now expected "
            "to close in the first quarter of 2027.")
        self.assertEqual(phrase, "in the third quarter of 2026")
        self.assertIsNone(d)


class TestConsiderationMix(unittest.TestCase):

    def test_classification_by_deal(self):
        self.assertEqual(extract_terms(FHB_PR)["consideration"], "stock")
        self.assertEqual(extract_terms(CLST_PR)["consideration"], "cash")
        self.assertEqual(extract_terms(OSBC_8K)["consideration"], "mixed")
        self.assertEqual(extract_terms(SBCF_8K)["consideration"], "election")
        self.assertIsNone(classify_consideration("no terms here", None, None))

    def test_mix_percentages(self):
        self.assertEqual(extract_mix_pcts(OSBC_8K), (75.0, 25.0))
        self.assertEqual(extract_mix_pcts(SBCF_8K), (75.0, 25.0))
        self.assertIsNone(extract_mix_pcts(FHB_PR))
        self.assertIsNone(extract_mix_pcts("60% stock and 50% cash"))  # ≠ 100


class TestBuildTerms(unittest.TestCase):

    def test_fhb_stated_price_and_tickers(self):
        with patch("data.ma_announcements._close_before",
                   return_value=(30.13, "2026-07-10", True)) as cb:
            t, ok = build_terms(FHB_PR, "2026-07-13")
        self.assertTrue(ok)
        cb.assert_called_once_with("FHB", "2026-07-13")
        self.assertEqual((t["acq_ticker"], t["tgt_ticker"]), ("FHB", "TCBK"))
        self.assertEqual(t["exchange_ratio"], 2.095)
        self.assertEqual(t["consideration"], "stock")
        self.assertEqual(t["implied_price"], 63.12)
        self.assertEqual(t["implied_price_basis"], "stated")
        self.assertEqual(t["acq_close_at_announce"], 30.13)
        self.assertEqual(t["expected_close_date"], "2026-12-31")
        self.assertIsNone(t["premium_pct"])
        self.assertIsNone(t["termination_fee_usd"])

    def test_computed_price_when_not_stated(self):
        text = FHB_PR.replace(", representing $63.12 per share as of First "
                              "Hawaiian's closing stock price on July 10, 2026",
                              "")
        with patch("data.ma_announcements._close_before",
                   return_value=(30.13, "2026-07-10", True)):
            t, ok = build_terms(text, "2026-07-13")
        self.assertTrue(ok)
        # 2.095 × 30.13 = 63.12235 (hand) -> 63.1224
        self.assertEqual(t["implied_price"], 63.1224)
        self.assertEqual(t["implied_price_basis"], "computed")
        self.assertIn("2.095 × FHB close 2026-07-10 $30.13", t["implied_price_note"])

    def test_price_lookup_failure_not_ok(self):
        with patch("data.ma_announcements._close_before",
                   return_value=(None, None, False)):
            t, ok = build_terms(FHB_PR, "2026-07-13")
        self.assertFalse(ok)
        self.assertEqual(t["implied_price"], 63.12)   # stated survives

    def test_mixed_hand_math_and_fee(self):
        with patch("data.ma_announcements._close_before",
                   return_value=(18.08, "2025-02-24", True)):
            t, ok = build_terms(OSBC_8K, "2025-02-25", tgt_tick="BFIN")
        self.assertTrue(ok)
        self.assertEqual(t["acq_ticker"], "OSBC")
        self.assertEqual(t["implied_price"], 62.6)      # stated by the PR
        self.assertEqual(t["termination_fee_usd"], 8_500_000)
        # Computed leg agrees with the PR: 2.5814 × 18.08 + 15.93 = 62.601712
        v, _n = implied_offer(t, 18.08, basis_label="OSBC $18.08")
        self.assertEqual(v, 62.6017)

    def test_election_blended_at_stated_proration(self):
        with patch("data.ma_announcements._close_before",
                   return_value=(26.00, "2025-05-28", True)):
            t, ok = build_terms(SBCF_8K, "2025-05-29", tgt_tick=None)
        self.assertTrue(ok)
        self.assertEqual(t["consideration"], "election")
        # 0.75 × 38.5 × 26.00 + 0.25 × 1,000 = 750.75 + 250 = 1,000.75 (hand)
        self.assertEqual(t["implied_price"], 1000.75)
        self.assertIn("blended", t["implied_price_note"])

    def test_cash_deal_needs_no_price(self):
        with patch("data.ma_announcements._close_before") as cb:
            t, ok = build_terms(CLST_PR, "2026-04-08")
        self.assertTrue(ok)
        cb.assert_not_called()
        self.assertEqual(t["implied_price"], 19.58)
        self.assertEqual(t["implied_price_basis"], "stated")
        self.assertEqual(t["consideration"], "cash")

    def test_election_without_proration_is_na(self):
        terms = {"consideration": "election", "exchange_ratio": 38.5,
                 "cash_per_share": 1000.0, "stock_pct": None, "cash_pct": None}
        self.assertEqual(implied_offer(terms, 26.0, basis_label="x"), (None, None))


class TestMergerArb(unittest.TestCase):

    def test_stock_deal_hand_math(self):
        from data.deal_comps import merger_arb
        terms = {"consideration": "stock", "exchange_ratio": 2.095,
                 "cash_per_share": None, "expected_close_date": "2026-12-31",
                 "acq_ticker": "FHB"}
        a = merger_arb(terms, 28.50, 56.00, date(2026, 10, 5))
        # offer 2.095 × 28.50 = 59.7075; gross 59.7075/56 − 1 = 0.0662054
        # days 2026-10-05 -> 2026-12-31 = 87; annualized = gross × 365/87
        self.assertEqual(a["implied_offer"], 59.7075)
        self.assertAlmostEqual(a["gross_spread"], 0.0662054, places=6)
        self.assertEqual(a["days_to_close"], 87)
        self.assertAlmostEqual(a["annualized_spread"], 0.0662054 * 365 / 87,
                               places=6)
        self.assertAlmostEqual(a["annualized_spread"], 0.27776, places=4)

    def test_mixed_and_cash(self):
        from data.deal_comps import merger_arb
        mixed = {"consideration": "mixed", "exchange_ratio": 2.5814,
                 "cash_per_share": 15.93, "expected_close_date": "2025-09-30"}
        a = merger_arb(mixed, 18.08, 60.00, date(2025, 3, 1))
        self.assertEqual(a["implied_offer"], 62.6017)
        self.assertAlmostEqual(a["gross_spread"], 62.6017 / 60 - 1, places=9)
        self.assertEqual(a["days_to_close"], 213)
        cash = {"consideration": "cash", "exchange_ratio": None,
                "cash_per_share": 19.58, "expected_close_date": "2026-09-30"}
        a = merger_arb(cash, None, 18.90, date(2026, 5, 1))
        self.assertEqual(a["implied_offer"], 19.58)
        self.assertAlmostEqual(a["gross_spread"], 19.58 / 18.90 - 1, places=9)

    def test_missing_inputs_are_none(self):
        from data.deal_comps import merger_arb
        terms = {"consideration": "stock", "exchange_ratio": 2.095,
                 "cash_per_share": None, "expected_close_date": None}
        a = merger_arb(terms, None, 56.0, date(2026, 10, 5))      # no acq px
        self.assertIsNone(a["implied_offer"])
        self.assertIsNone(a["gross_spread"])
        a = merger_arb(terms, 28.5, None, date(2026, 10, 5))      # no tgt px
        self.assertEqual(a["implied_offer"], 59.7075)
        self.assertIsNone(a["gross_spread"])
        self.assertIsNone(a["annualized_spread"])                 # no close
        past = dict(terms, expected_close_date="2026-09-30")
        a = merger_arb(past, 28.5, 56.0, date(2026, 10, 5))       # past-due
        self.assertEqual(a["days_to_close"], -5)
        self.assertIsNone(a["annualized_spread"])
        self.assertEqual(merger_arb(None, 1, 1, date(2026, 1, 1))["implied_offer"],
                         None)


def _eps_facts(extra=()):
    """companyfacts-shaped diluted EPS: Q3-25 direct, FY-25 + 9M-25 (Q4 by
    YTD difference), Q1-26 and Q2-26 direct, plus a Q3-26 fact that must be
    clipped for an announce date of 2026-07-13."""
    rows = [
        {"start": "2025-07-01", "end": "2025-09-30", "val": 1.10, "form": "10-Q",
         "filed": "2025-11-01"},
        {"start": "2025-01-01", "end": "2025-09-30", "val": 3.27, "form": "10-Q",
         "filed": "2025-11-01"},
        {"start": "2025-01-01", "end": "2025-12-31", "val": 4.40, "form": "10-K",
         "filed": "2026-02-20"},
        {"start": "2026-01-01", "end": "2026-03-31", "val": 1.12, "form": "10-Q",
         "filed": "2026-05-01"},
        {"start": "2026-04-01", "end": "2026-06-30", "val": 1.12, "form": "10-Q",
         "filed": "2026-08-01"},
        {"start": "2026-07-01", "end": "2026-09-30", "val": 1.20, "form": "10-Q",
         "filed": "2026-11-01"},
        *extra,
    ]
    return {"facts": {"us-gaap": {"EarningsPerShareDiluted": {
        "units": {"USD/shares": rows}}}}}


class TestPEAtAnnounce(unittest.TestCase):

    def test_ttm_eps_clipped_at_announce(self):
        from data.deal_comps import _ttm_eps_at
        with patch("data.sec_client.fetch_company_facts",
                   return_value=_eps_facts()):
            eps, end = _ttm_eps_at(356171, "2026-07-13")
        # Q3-25 1.10 + Q4-25 (4.40 − 3.27 = 1.13) + Q1-26 1.12 + Q2-26 1.12
        # = 4.47 (hand); the Q3-26 fact (end > announce) is clipped.
        self.assertEqual(end, "2026-06-30")
        self.assertAlmostEqual(eps, 4.47, places=9)

    def test_stale_window_is_na(self):
        from data.deal_comps import _ttm_eps_at
        with patch("data.sec_client.fetch_company_facts",
                   return_value=_eps_facts()):
            self.assertEqual(_ttm_eps_at(356171, "2027-06-01"), (None, None))

    def test_compute_multiples_pe_and_loss(self):
        from data.deal_comps import compute_multiples
        deal = {"deal_kind": "whole_company", "announce_date": "2026-07-13",
                "value_usd": None, "target_cik": 356171,
                "terms": {"implied_price": 63.12}}
        with patch("data.sec_client.fetch_company_facts",
                   return_value=_eps_facts()):
            out, ok = compute_multiples(deal)
        self.assertTrue(ok)
        self.assertAlmostEqual(out["ttm_eps"], 4.47, places=9)
        self.assertAlmostEqual(out["p_e"], 63.12 / 4.47, places=9)   # 14.12x
        self.assertIsNone(out["p_tbv"])                 # no value -> no P/TBV
        # Loss-making target: EPS reported, P/E n/a.
        loss = _eps_facts()
        for r in loss["facts"]["us-gaap"]["EarningsPerShareDiluted"]["units"]["USD/shares"]:
            r["val"] = -abs(r["val"])
        with patch("data.sec_client.fetch_company_facts", return_value=loss):
            out, _ = compute_multiples(deal)
        self.assertLess(out["ttm_eps"], 0)
        self.assertIsNone(out["p_e"])


class TestRecentRowsFilter(unittest.TestCase):

    def test_pending_plus_24_months(self):
        from ui.transactions import _recent_rows
        deals = [
            {"status": "completed", "announce_date": "2024-09-01",
             "completion_date": "2025-01-15"},               # 25 months: out
            {"status": "completed", "announce_date": "2024-11-01",
             "completion_date": "2025-03-01"},               # in window
            {"status": "pending", "announce_date": "2026-07-13"},
            {"status": "terminated", "announce_date": "2025-02-01",
             "termination_date": "2025-09-01"},
            {"status": "pending", "announce_date": "2024-01-01"},  # stale but pending
        ]
        rows = _recent_rows(deals, date(2026, 10, 5))
        self.assertEqual([r["announce_date"] for r in rows],
                         ["2026-07-13", "2025-02-01", "2024-11-01", "2024-01-01"])


if __name__ == "__main__":
    unittest.main()
