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
             "completion_date": "2025-01-15"},               # announced 25 months
                                                             # ago, CLOSED inside: in
            {"status": "completed", "announce_date": "2024-06-01",
             "completion_date": "2024-09-30"},               # both outside: out
            {"status": "completed", "announce_date": "2024-11-01",
             "completion_date": "2025-03-01"},               # in window
            {"status": "pending", "announce_date": "2026-07-13"},
            {"status": "terminated", "announce_date": "2025-02-01",
             "termination_date": "2025-09-01"},
            {"status": "pending", "announce_date": "2024-01-01"},  # stale but pending
        ]
        rows = _recent_rows(deals, date(2026, 10, 5))
        self.assertEqual([r["announce_date"] for r in rows],
                         ["2026-07-13", "2025-02-01", "2024-11-01", "2024-09-01",
                          "2024-01-01"])


# ── Second pass (owner: "there is missing data??", 2026-10-05) ────────────
# Verbatim sentences from the rows whose terms came back empty or WRONG on
# the first local render.

PB_STELLAR = (
    "Prosperity Bancshares, Inc. (NYSE: PB) and Stellar Bancorp, Inc. (NYSE: "
    "STEL) announced a definitive agreement. Under the terms and subject to "
    "the conditions of the definitive agreement, Prosperity will issue 0.3803 "
    "shares of Prosperity common stock and $11.36 in cash for each "
    "outstanding share of Stellar common stock. Based on Prosperity's closing "
    "price of $72.90 on January 27, 2026, the total consideration was valued "
    "at approximately $2.002 billion.")
PB_STELLAR_DECK = (
    " Transaction Structure – Approximately 70% stock / 30% cash "
    "consideration – $11.36 in cash and 0.3803 PB common shares for each "
    "STEL common share; fixed exchange ratio – Implied value of $39.08 per "
    "common share – Implied aggregate transaction value of $2,002 million – "
    "Price / tangible book value per share: 1.81x – Core deposit premium: "
    "10.9%")
PB_TEXAS_PARTNERS = (
    "Under the terms and subject to the conditions of the definitive "
    "agreement, Prosperity will issue 4,062,520 shares of Prosperity common "
    "stock for all outstanding shares of Southwest common stock and "
    "restricted stock awards, subject to certain potential adjustments. "
    "Based on Prosperity's closing price of $65.97 on September 29, 2025, "
    "the total consideration was valued at approximately $268.9 million.")
SBCF_HEARTLAND = (
    "Seacoast Banking Corporation of Florida (NASDAQ: SBCF) announced a "
    "definitive agreement. Under the terms of the definitive agreement, each "
    "share of Heartland common stock will be converted at closing into the "
    "right to receive (i) $147.10 in cash, (ii) 4.9164 shares of Seacoast "
    "common stock (subject to certain potential adjustments) or (iii) a "
    "50-50 combination of cash and common stock, or a total value of "
    "$141.96 per share of Heartland common stock. Shareholders will have the "
    "ability to elect to receive stock, cash, or a mix of 50% cash and 50% "
    "stock, with the final consideration mix being maintained at 50% cash "
    "and 50% stock. Based on Seacoast's closing price of $27.83 as of "
    "February 26, 2025, the aggregate value of merger consideration to be "
    "paid by Seacoast would be approximately $110 million. Closing of the "
    "transaction is expected in the third quarter of 2025, following "
    "regulatory approval.")


class TestSecondPassFixtures(unittest.TestCase):

    def test_prosperity_stellar_mixed_not_cash(self):
        # First render showed this deal as all-cash at $11.36 — the
        # plausible-wrong class. The "will issue N shares ... and $C in cash
        # for each outstanding share" form now parses.
        from data.ma_announcements import extract_stated_value
        r = extract_exchange_ratio(PB_STELLAR)
        self.assertEqual((r[0], r[1], r[2]), (0.3803, "Prosperity", "Stellar"))
        t = extract_terms(PB_STELLAR + PB_STELLAR_DECK)
        self.assertEqual(t["consideration"], "mixed")
        self.assertEqual(t["cash_per_share"], 11.36)
        self.assertEqual((t["stock_pct"], t["cash_pct"]), (70.0, 30.0))
        self.assertEqual(t["implied_price_stated"], 39.08)
        self.assertIsNone(t["premium_pct"])      # core deposit premium ≠ price premium
        self.assertEqual(extract_stated_value(PB_STELLAR + PB_STELLAR_DECK),
                         2_002_000_000)           # $2.002B == $2,002M
        # Computed leg reproduces the deck: 0.3803 × 72.90 + 11.36 = 39.08387
        with patch("data.ma_announcements._close_before",
                   return_value=(72.90, "2026-01-27", True)):
            tt, ok = build_terms(PB_STELLAR, "2026-01-28")
        self.assertTrue(ok)
        self.assertEqual((tt["acq_ticker"], tt["tgt_ticker"]), ("PB", "STEL"))
        self.assertEqual(tt["implied_price"], 39.0839)
        self.assertEqual(tt["implied_price_basis"], "computed")

    def test_cash_with_unparsed_stock_leg_is_ambiguous(self):
        # Deck-only wording: cash parses, the ratio form does not — never
        # all-cash, never a per-share price.
        t = extract_terms(PB_STELLAR_DECK)
        self.assertEqual(t["cash_per_share"], 11.36)
        self.assertIsNone(t["exchange_ratio"])
        self.assertIsNone(t["consideration"])
        with patch("data.ma_announcements._close_before") as cb:
            tt, _ = build_terms(PB_STELLAR_DECK.replace(
                "Implied value of $39.08 per common share – ", ""), "2026-01-28")
        cb.assert_not_called()
        self.assertIsNone(tt["implied_price"])

    def test_fixed_share_count_is_stock_without_ratio(self):
        from data.ma_announcements import extract_stated_value
        t = extract_terms(PB_TEXAS_PARTNERS)
        self.assertEqual(t["consideration"], "stock")
        self.assertIsNone(t["exchange_ratio"])
        self.assertIsNone(t["implied_price_stated"])
        self.assertEqual(extract_stated_value(PB_TEXAS_PARTNERS), 268_900_000)

    def test_heartland_election_stated_value_and_close(self):
        from data.ma_announcements import extract_stated_value
        t = extract_terms(SBCF_HEARTLAND)
        self.assertEqual(t["consideration"], "election")
        self.assertEqual(t["exchange_ratio"], 4.9164)
        self.assertEqual(t["cash_per_share"], 147.1)
        self.assertEqual((t["stock_pct"], t["cash_pct"]), (50.0, 50.0))
        self.assertEqual(t["implied_price_stated"], 141.96)
        self.assertEqual(extract_stated_value(SBCF_HEARTLAND), 110_000_000)
        self.assertEqual((t["expected_close_phrase"], t["expected_close_date"]),
                         ("in the third quarter of 2025", "2025-09-30"))
        # Blended at the stated 50/50: 0.5 × 4.9164 × 27.83 + 0.5 × 147.10
        # = 68.411706 + 73.55 = 141.961706 — reproduces the PR's $141.96.
        v, _n = implied_offer(t, 27.83, basis_label="SBCF $27.83")
        self.assertEqual(v, 141.9617)


class TestResolveAnnouncementAccession(unittest.TestCase):
    """resolve_announcement gates on the single matched document but reads
    terms off the WHOLE accession, and re-gates deck-only candidates on it."""

    def _run(self, docs, indexes):
        from tests.test_ma_announcements import _hit, _wire
        from data.ma_announcements import resolve_announcement
        # EFTS matched the investor DECK (EX-99.1), as it did live for
        # Seacoast/Villages; the 8-K body is the listing's non-exhibit doc.
        hits = [_hit("0001-25-1", "2025-05-29", "tm25_ex99-1.htm", cik="0000730708")]
        with patch("data.ma_announcements.requests.get",
                   side_effect=_wire(hits, docs, indexes=indexes)), \
             patch("data.ma_announcements.time.sleep", lambda *_: None), \
             patch("data.cache.get", return_value=None), \
             patch("data.cache.put"), \
             patch("data.ma_announcements._close_before",
                   return_value=(27.83, "2025-05-28", True)):
            return resolve_announcement("Citizens First Bank", "Seacoast Bank",
                                        "2025-10-01")

    def test_deck_only_candidate_regated_on_accession(self):
        deck = ("<p>Acquisition of Villages Bancorporation, Inc. Seacoast and "
                "Citizens First Bank franchise overview.</p>")
        body = ("<p>Seacoast Banking Corporation of Florida (NASDAQ: SBCF) "
                "entered into an Agreement and Plan of Merger with Villages "
                "Bancorporation, parent of Citizens First Bank. " + SBCF_8K
                + "</p>")
        # Listing mirrors EDGAR: site-nav links first, then the filing's
        # own documents sharing the primary document's stem.
        r, ok = self._run({"tm25_ex99-1.htm": deck, "tm25_8k.htm": body,
                           "index.htm": "<p>EDGAR nav</p>", "R1.htm": "<p>x</p>"},
                          {"0001251": ["index.htm", "search.htm", "R1.htm",
                                       "tm25_8k.htm", "tm25_ex2-1.htm",
                                       "tm25_ex99-1.htm"]})
        self.assertTrue(ok)
        self.assertIsNotNone(r, "deck-only candidate must re-gate on the 8-K body")
        self.assertEqual(r["announce_date"], "2025-05-29")
        self.assertEqual(r["terms"]["consideration"], "election")
        self.assertEqual(r["terms"]["exchange_ratio"], 38.5)
        self.assertEqual(r["terms"]["termination_fee_usd"], 31_400_000)

    def test_single_doc_gate_still_rejects_completion(self):
        done = ("<p>Seacoast has completed its acquisition of Citizens First "
                "Bank pursuant to the previously announced definitive "
                "agreement.</p>")
        r, ok = self._run({"tm25_ex99-1.htm": done}, {})
        self.assertTrue(ok)
        self.assertIsNone(r)


class TestParentheticalRatioForm(unittest.TestCase):

    QNBC_101 = (
        "the shares of common stock, $1.00 par value per share, of the Company "
        "(\"Company Common Stock\") issued and outstanding immediately prior "
        "to the Effective Time will, without any further action on the part of "
        "the holder thereof, be automatically converted, in accordance with "
        "the procedures set forth in the Merger Agreement, into a right to "
        "receive 0.5500 (the \"Exchange Ratio\") shares of common stock, "
        "$0.625 par value, of QNB (\"QNB Common Stock\" and such "
        "consideration the \"Merger Consideration\").")

    def test_qnb_victory_ratio_parses_with_unnamed_target(self):
        r = extract_exchange_ratio(self.QNBC_101)
        self.assertEqual(r, (0.55, "QNB", ""))
        t = extract_terms(self.QNBC_101)
        self.assertEqual(t["consideration"], "stock")
        # Unnamed target side resolves to nothing — never a guessed ticker.
        with patch("data.ma_announcements._close_before",
                   return_value=(35.60, "2025-09-22", True)):
            tt, ok = build_terms(self.QNBC_101 + " QNB Corp. (OTC: QNBC)",
                                 "2025-09-23", acq_tick="QNBC")
        self.assertTrue(ok)
        self.assertIsNone(tt["tgt_ticker"])
        self.assertEqual(tt["implied_price"], 19.58)        # 0.55 × 35.60

    def test_otc_acquirer_resolves_through_universe(self):
        # QNB Corp. carries no exchange-ticker parenthetical; the universe
        # match by brand token supplies QNBC so the offer can be priced.
        with patch("data.ma_pending._universe_match",
                   side_effect=lambda name: (("QNBC", 7714, 750558)
                                             if "QNB" in name else (None, None, None))),              patch("data.ma_announcements._close_before",
                   return_value=(35.60, "2025-09-22", True)) as cb:
            tt, ok = build_terms(self.QNBC_101, "2025-09-23")
        self.assertTrue(ok)
        cb.assert_called_once_with("QNBC", "2025-09-23")
        self.assertEqual(tt["acq_ticker"], "QNBC")
        self.assertIsNone(tt["tgt_ticker"])
        self.assertEqual(tt["implied_price"], 19.58)


# ── Hotfix pass (first universe run on prod, 2026-10-05) ──────────────────
# Verbatim sentences behind the plausible-wrong cells the board showed.

FSBW_AGG = ("Under the terms of the agreement, the aggregate consideration will "
            "consist of 430,176 shares of FS Bancorp common stock and "
            "$16,832,742 in cash. Pacific West shareholders will have the right "
            "to elect shares of FS Bancorp common stock or cash, subject to "
            "proration.")
EQBK_AGG = ("(i) 1,934,452 shares of the Company's Class A common stock, par "
            "value $0.01 per share (\"Common Stock\") and (ii) $32,500,000 in "
            "cash. The cash consideration is subject to reduction in the event "
            "that Frontier does not deliver a minimum of $99 million of equity.")
MCBS_AGG = ("First IC shareholders will receive 3,384,588 shares of MetroCity "
            "common stock and $111,965,213 in cash, subject to adjustment, for "
            "total consideration consisting of approximately 46% stock and 54% "
            "cash.")
CIVB_PER_SHARE = (
    "each share of Farmers common stock (other than Dissenting Shares, as "
    "defined in the Merger Agreement) will be converted into the right to "
    "receive $69,850 in cash and approximately 2,869 Civista common shares, "
    "resulting in aggregate merger consideration payable by Civista of "
    "approximately $34.925 million in cash and 1,434,491 Civista common "
    "shares.")
BFC_FEE_TYPO = ("Termination Fee. Centre will pay BFC a termination fee equal "
                "to $5,300,000 million in the event (i) the Merger Agreement is "
                "terminated by BFC because Centre's board changed its "
                "recommendation.")
FITB_FEE = ("The Merger Agreement provides certain termination rights for both "
            "Comerica and Fifth Third and further provides that a termination "
            "fee of $500,000,000 will be payable by either Comerica or Fifth "
            "Third, as applicable, in the event of a termination.")


class TestHotfixGuards(unittest.TestCase):

    def test_aggregate_cash_is_not_per_share(self):
        # Three real aggregates that rendered as per-share cash on the board.
        for txt in (FSBW_AGG, EQBK_AGG, MCBS_AGG):
            with self.subTest(txt=txt[:30]):
                self.assertIsNone(extract_cash_per_share(txt))
                t = extract_terms(txt)
                self.assertIsNone(t["cash_per_share"])
                self.assertIsNone(t["implied_price_stated"])
        # The per-share forms the pass-one fixtures use still parse.
        self.assertEqual(extract_cash_per_share(CLST_PR), 19.58)
        self.assertEqual(extract_cash_per_share(OSBC_8K), 15.93)
        self.assertEqual(extract_cash_per_share(PB_STELLAR), 11.36)
        self.assertEqual(extract_cash_per_share(SBCF_8K), 1000.0)

    def test_civista_500_share_target_is_mixed_not_cash(self):
        # $69,850 cash + 2,869 Civista shares PER Farmers share is genuine
        # (a 500-share bank) — but never "Cash" with a $69,850 implied price.
        self.assertEqual(extract_cash_per_share(CIVB_PER_SHARE), 69850.0)
        r = extract_exchange_ratio(CIVB_PER_SHARE)
        self.assertEqual((r[0], r[1], r[2]), (2869.0, "Civista", ""))
        t = extract_terms(CIVB_PER_SHARE)
        self.assertEqual(t["consideration"], "mixed")
        # 2,869 × $20.00 + $69,850 = $127,230 per Farmers share (hand)
        v, _n = implied_offer(t, 20.00, basis_label="CIVB $20.00")
        self.assertEqual(v, 127230.0)

    def test_cash_with_unparsed_share_count_is_ambiguous(self):
        # Same sentence shape, but a share-count form the ratio regexes do
        # not know: cash parses, classification must stay None.
        txt = CIVB_PER_SHARE.replace("approximately 2,869 Civista common shares",
                                     "2,869 shares of Civista's common equity")
        t = extract_terms(txt)
        self.assertEqual(t["cash_per_share"], 69850.0)
        self.assertIsNone(t["exchange_ratio"])
        self.assertIsNone(t["consideration"])

    def test_fee_typo_and_tight_gap(self):
        self.assertIsNone(extract_termination_fee(BFC_FEE_TYPO))   # "$5,300,000 million"
        self.assertEqual(extract_termination_fee(FITB_FEE), 500_000_000)
        # A loose gap once reached across to an unrelated dollar figure.
        self.assertIsNone(extract_termination_fee(
            "The termination fee, and Bank First, which has total assets of "
            "$5.3 billion, agreed to customary covenants."))
        # Payee clauses still allowed.
        self.assertEqual(extract_termination_fee(QNBC_8K), 1_575_000)
        self.assertEqual(extract_termination_fee(
            "a termination fee payable by VBI to Seacoast of $31.4 million"),
            31_400_000)

    def test_spaced_ordinal_quarter(self):
        self.assertEqual(close_phrase_to_date("in the 4 th quarter of 2025"),
                         "2025-12-31")
        self.assertEqual(close_phrase_to_date("in the 1 st quarter of 2027"),
                         "2027-03-31")

    def test_self_deal_rows_never_render(self):
        from ui.transactions import _recent_rows
        rows = _recent_rows([
            {"status": "pending", "announce_date": "2026-09-08",
             "buyer_ticker": "EFSI", "target_ticker": "EFSI"},
            {"status": "pending", "announce_date": "2026-09-08",
             "buyer_ticker": "JMSB", "target_ticker": "EFSI"},
        ], date(2026, 10, 5))
        self.assertEqual([r["buyer_ticker"] for r in rows], ["JMSB"])


class TestPendingLegendGuards(unittest.TestCase):
    """ma_pending: unreadable legends yield no row; the ratio sentence
    arbitrates direction over the legend."""

    def _run(self, legend, universe, cik, subject):
        from tests.test_ma_pending import _Harness, _filings
        h = _Harness()
        return h._run(_filings([
            ("425", "2026-08-10", "0001-26-1", "d4_425.htm", ""),
        ]), texts=(legend, True), universe=universe, cik=cik, subject=subject,
            today="2026-08-12")

    def test_legend_with_digits_yields_no_row(self):
        from tests.test_ma_pending import UNIVERSE
        legend = ("Filed by: First Hawaiian, Inc. Pursuant to Rule 425 Subject "
                  "Company: Cincinnati, Ohio - July 21, 2026. First Financial "
                  "Bancorp Commission File No.: 000-10661 definitive agreement")
        rows, ok = self._run(legend, UNIVERSE, 36377, "First Hawaiian Bank")
        self.assertTrue(ok)
        self.assertEqual(rows, [])

    def test_ratio_side_overrides_legend_direction(self):
        # Tri-County's own 425s legend HBT (the registrant) as the Subject
        # Company; the ratio sentence says Tri-County holders receive HBT
        # shares -> Tri-County is the TARGET -> a sale row, not "TYFG
        # acquires HBT".
        universe = {"HBT": {"name": "HBT Financial", "fdic_cert": 111, "cik": 1000},
                    "TYFG": {"name": "Tri-County Financial Group", "fdic_cert": 222,
                             "cik": 2000}}
        legend = ("Filed by: Tri-County Financial Group Pursuant to Rule 425 "
                  "Subject Company: HBT Financial, Inc. Commission File No.: "
                  "001-38870 This filing relates to the proposed transaction "
                  "between HBT Financial, Inc. and Tri-County Financial Group "
                  "pursuant to the Agreement and Plan of Merger. Tri-County "
                  "shareholders will receive 1.25 HBT Financial shares for each "
                  "Tri-County share.")
        rows, ok = self._run(legend, universe, 2000, "First State Bank")
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["direction"], "sale")
        self.assertEqual(rows[0]["counterparty_ticker"], "HBT")
        self.assertEqual(rows[0]["target_cik"], 2000)


class TestPendingLegendGuardsPass2(unittest.TestCase):

    def test_acquire_object_naming_self_flips_to_sale(self):
        # Tri-County's 425 legends HBT as Subject Company; the PR headline
        # says HBT is acquiring Tri-County and the deal is cash (no ratio).
        from tests.test_ma_pending import _Harness, _filings
        universe = {"HBT": {"name": "HBT Financial", "fdic_cert": 111, "cik": 1000},
                    "TYFG": {"name": "Tri-County Financial Group", "fdic_cert": 222,
                             "cik": 2000}}
        legend = ("Filed by: Tri-County Financial Group Pursuant to Rule 425 "
                  "Subject Company: HBT Financial, Inc. Commission File No.: "
                  "001-38870 HBT Financial, Inc. Announces Agreement to Acquire "
                  "Tri-County Financial Group, Inc. in a definitive agreement. "
                  "Tri-County shareholders will receive $71.01 in cash for each "
                  "share of Tri-County common stock.")
        rows, ok = _Harness()._run(_filings([
            ("425", "2026-08-10", "0001-26-1", "d4_425.htm", ""),
        ]), texts=(legend, True), universe=universe, cik=2000,
            subject="First State Bank", today="2026-08-12")
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["direction"], "sale")
        self.assertEqual(rows[0]["counterparty_name"], "HBT Financial, Inc")

    def test_cash_leg_dateline_counterparty_dropped(self):
        from unittest.mock import MagicMock
        from data import ma_announcements as ma
        pr = ("Cincinnati, Ohio - July 21, 2026. First Financial Bancorp "
              "(NASDAQ: FFBC) today announced a definitive agreement to acquire "
              "Cincinnati, Ohio - July 21, 2026. First Financial Bancorp, the "
              "parent of its bank, in an all-stock transaction.")
        hits = [{"_id": "0001-26-9:ffbc.htm",
                 "_source": {"adsh": "0001-26-9", "file_date": "2026-07-21",
                             "ciks": ["0000708955"], "file_type": "8-K",
                             "items": ["1.01", "8.01"],
                             "display_names": ["FIRST FINANCIAL BANCORP /OH/ "
                                               "(FFBC) (CIK 0000708955)"]}}]
        resp = MagicMock(); resp.json.return_value = {"hits": {"hits": hits}}
        resp.raise_for_status = MagicMock()
        with patch("data.ma_announcements.requests.get", return_value=resp), \
             patch("data.ma_announcements._accession_text",
                   return_value=(pr, True)), \
             patch("data.ma_announcements.time.sleep", lambda *_: None):
            rows, ok = ma.find_open_announcements(708955, "First Financial Bank")
        self.assertTrue(ok)
        self.assertEqual(rows, [])


class TestSnapshotMissingDeals(unittest.TestCase):
    """build_comps_snapshot: sibling charters collapse to the largest target,
    and an empty history is retried once (a failed FDIC fetch returns [])."""

    def _bank_rows(self, hist_side_effect):
        from data.deal_comps import build_comps_snapshot
        with patch("data.ma_history.get_ma_history", side_effect=hist_side_effect), \
             patch("data.deal_comps.compute_multiples", return_value=({}, True)), \
             patch("data.cache.put"), \
             patch("data.deal_comps._EMPTY_HISTORY_RETRY_WAITS", (0,)):
            return build_comps_snapshot([{"ticker": "FITB", "cert": 6672, "cik": 35527}])

    def test_sibling_charters_keep_largest_target(self):
        url = "https://www.sec.gov/Archives/edgar/data/35527/000119312525230873/d91245dex991.htm"
        trust = {"deal_kind": "whole_company", "direction": "acquisition",
                 "status": "completed", "completion_date": "2026-02-01",
                 "counterparty": {"name": "Comerica Bank & Trust, National Association",
                                  "cert": 2},
                 "announce_date": "2025-10-06", "announce_url": url,
                 "target_assets": 126_300_000, "value_usd": 10_900_000_000}
        bank = dict(trust, counterparty={"name": "Comerica Bank", "cert": 1},
                    target_assets=80_000_000_000)
        snap = self._bank_rows([[trust, bank]])
        self.assertEqual(snap["deals_total"], 1)
        self.assertEqual(snap["deals"][0]["target_name"], "Comerica Bank")
        # Order-independent: the bank first, the trust second.
        snap = self._bank_rows([[bank, trust]])
        self.assertEqual([r["target_name"] for r in snap["deals"]], ["Comerica Bank"])

    def test_empty_history_retried_once(self):
        deal = {"deal_kind": "whole_company", "direction": "acquisition",
                "status": "completed", "completion_date": "2025-06-21",
                "counterparty": {"name": "CrossFirst Bank", "cert": 3},
                "announce_date": None, "announce_url": None,
                "target_assets": 7_600_000_000, "value_usd": None}
        snap = self._bank_rows([[], [deal]])        # first call: failed fetch
        self.assertEqual(snap["deals_total"], 1)
        self.assertEqual(snap["deals"][0]["target_name"], "CrossFirst Bank")


class TestResolverPartyGate(unittest.TestCase):
    """resolve_announcement: third-party filers naming the target as a
    lender never consume the budget or anchor the deal (live candidate
    lists 2026-10-06: Core Scientific for "Bremer Bank", Credit Acceptance
    for "Comerica Bank"); the target must be named in deal context."""

    BREMER_PR = ("<p>Old National Bancorp (NASDAQ: ONB) and Bremer Financial "
                 "Corporation today announced that they have entered into a "
                 "definitive merger agreement under which Old National will "
                 "acquire Bremer Financial, the parent of Bremer Bank, in a "
                 "transaction valued at approximately $1.4 billion.</p>")
    LENDER_8K = ("<p>Core Scientific entered into a credit agreement with "
                 "Bremer Bank, National Association, as lender, and Old "
                 "Republic as agent, a definitive agreement providing for a "
                 "term loan.</p>")

    def _run(self, hits, docs):
        from tests.test_ma_announcements import _wire
        from data.ma_announcements import resolve_announcement
        fetched = []
        wire = _wire(hits, docs, indexes={})
        def spy(url, params=None, headers=None, timeout=30):
            if "efts" not in url and not url.endswith("/"):
                fetched.append(url.rsplit("/", 1)[-1])
            return wire(url, params=params, headers=headers, timeout=timeout)
        with patch("data.ma_announcements.requests.get", side_effect=spy), \
             patch("data.ma_announcements.time.sleep", lambda *_: None), \
             patch("data.cache.get", return_value=None), \
             patch("data.cache.put"), \
             patch("data.ma_announcements._close_before",
                   return_value=(None, None, True)):
            r, ok = resolve_announcement("Bremer Bank, National Association",
                                         "Old National Bank", "2025-05-01")
        return r, ok, fetched

    def _hit(self, adsh, date, doc, cik, names):
        from tests.test_ma_announcements import _hit
        return _hit(adsh, date, doc, cik=cik, items=["1.01", "8.01"],
                    display_names=names)

    def test_third_party_lender_filings_skipped_without_fetch(self):
        hits = [self._hit(f"0001-23-{i}", f"2023-11-{10+i:02d}", f"corz{i}.htm",
                          "0001839341", ["Core Scientific, Inc./tx  (CORZ)  (CIK 0001839341)"])
                for i in range(1, 20)]
        hits.append(self._hit("0001-24-9", "2024-11-25", "onb.htm", "0000707179",
                              ["OLD NATIONAL BANCORP /IN/  (ONB)  (CIK 0000707179)"]))
        docs = {f"corz{i}.htm": self.LENDER_8K for i in range(1, 20)}
        docs["onb.htm"] = self.BREMER_PR
        r, ok, fetched = self._run(hits, docs)
        self.assertTrue(ok)
        self.assertIsNotNone(r)
        self.assertEqual(r["announce_date"], "2024-11-25")
        self.assertEqual(r["value_usd"], 1_400_000_000)
        # 19 lender filings never fetched (the accepted doc is read twice:
        # gate, then the whole-accession terms read).
        self.assertEqual(set(fetched), {"onb.htm"})

    def test_party_filing_naming_target_as_peer_only_is_skipped(self):
        deck = ("<p>Old National Bancorp investor presentation. Peer group: "
                "Bremer Bank, Associated Bank, Commerce Bank. Our definitive "
                "agreement with CapStar remains on track.</p>")
        hits = [self._hit("0001-24-1", "2024-01-17", "deck.htm", "0000707179",
                          ["OLD NATIONAL BANCORP /IN/  (ONB)  (CIK 0000707179)"]),
                self._hit("0001-24-9", "2024-11-25", "onb.htm", "0000707179",
                          ["OLD NATIONAL BANCORP /IN/  (ONB)  (CIK 0000707179)"])]
        r, ok, fetched = self._run(hits, {"deck.htm": deck, "onb.htm": self.BREMER_PR})
        self.assertTrue(ok)
        self.assertEqual(r["announce_date"], "2024-11-25")

    def test_all_generic_names_keep_gate_open(self):
        from data.ma_announcements import _filed_by_a_party
        self.assertTrue(_filed_by_a_party({"filers": "some corp (cik 1)"}, None, None))
        self.assertFalse(_filed_by_a_party({"filers": "core scientific (cik 1)"},
                                           "old", "bremer"))
        self.assertTrue(_filed_by_a_party({"filers": "old national bancorp /in/"},
                                          "old", "bremer"))


class TestParValueRatioForm(unittest.TestCase):

    BUSEY_101 = (
        "At the effective time of the Merger (the \"Effective Time\"), each "
        "share of common stock, par value $0.01 per share, of CrossFirst "
        "(\"CrossFirst Common Stock\") outstanding immediately prior to the "
        "Effective Time, other than certain shares held by CrossFirst or Busey, "
        "will be converted into the right to receive 0.6675 of a share (the "
        "\"Exchange Ratio\") of common stock, par value $0.001 per share, of "
        "Busey (\"Busey Common Stock\"). Holders of CrossFirst Common Stock "
        "will receive cash in lieu of fractional shares.")

    def test_crossfirst_busey(self):
        r = extract_exchange_ratio(self.BUSEY_101)
        self.assertEqual(r, (0.6675, "Busey", "CrossFirst"))
        t = extract_terms(self.BUSEY_101)
        self.assertEqual(t["consideration"], "stock")
        self.assertIsNone(t["cash_per_share"])     # cash in lieu is not cash


class TestCompletedTenseSplit(unittest.TestCase):

    def test_deck_boilerplate_is_not_this_deals_completion(self):
        from data.ma_announcements import _completed_for
        deck = ("Comprehensive Due Diligence Old National Diligence Summary "
                "Old National management team has successfully completed and "
                "integrated 9 bank M&A transactions over the last 10 years "
                "Experienced integration playbook Conservative credit marks "
                "Cultural alignment Strong pro forma capital Bremer Bank "
                "franchise overview Minnesota deposit share")
        # "Bremer" sits well beyond the 120-char window of the marker.
        self.assertFalse(_completed_for(deck, "bremer"))
        # Real completion wording is caught by the strong forms, named or not.
        self.assertTrue(_completed_for(
            "Old National today announced that it has completed its "
            "acquisition of Bremer Financial Corporation.", "bremer"))
        self.assertTrue(_completed_for(
            "The Company today announced the completion of the merger.", "bremer"))
        # The generic form counts when the target is named right there.
        self.assertTrue(_completed_for(
            "Old National has successfully completed the Bremer transaction.",
            "bremer"))
        # No usable token: the generic form counts everywhere (old behavior).
        self.assertTrue(_completed_for("The bank has completed the deal.", None))


class TestFiledBySelfIdentity(unittest.TestCase):

    def test_ambiguous_universe_name_resolved_by_filed_by_line(self):
        # "Tri-County" matches two universe names (ambiguous -> no CIK) and
        # the FDIC charter name "First State Bank" is all-generic: only the
        # legend's "Filed by: Tri-County Financial Group" identifies self.
        from tests.test_ma_pending import _Harness, _filings
        universe = {"HBT": {"name": "HBT Financial", "fdic_cert": 111, "cik": 1000},
                    "TYFG": {"name": "Tri-County Financial Group", "fdic_cert": 222,
                             "cik": 2000},
                    "TRIX": {"name": "Tri City Bankshares", "fdic_cert": 333,
                             "cik": 3000}}
        legend = ("Filed by: Tri-County Financial Group Pursuant to Rule 425 "
                  "Subject Company: HBT Financial, Inc. Commission File No.: "
                  "001-38870 HBT Financial, Inc. Announces Agreement to Acquire "
                  "Tri-County Financial Group, Inc. in a definitive agreement. "
                  "Tri-County shareholders will receive $71.01 in cash for each "
                  "share of Tri-County common stock.")
        rows, ok = _Harness()._run(_filings([
            ("425", "2026-08-10", "0001-26-1", "d4_425.htm", ""),
        ]), texts=(legend, True), universe=universe, cik=2000,
            subject="First State Bank", today="2026-08-12")
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["direction"], "sale")
        self.assertEqual(rows[0]["counterparty_name"], "HBT Financial, Inc")


# ── Wire source (owner 2026-10-06: "no SEC filings is not an excuse") ─────
# Verbatim sentences from the wire releases (GlobeNewswire / PR Newswire).

TOWN_WIRE = (
    "TowneBank Enhances North Carolina Presence Through Agreement To Acquire "
    "blueharbor bank. TowneBank (NASDAQ: TOWN) today announced that it has "
    "entered into a definitive agreement to acquire blueharbor bank. Under the "
    "terms of the agreement, shareholders of blueharbor will receive $12.70 in "
    "cash and 1.0534 shares of TowneBank common stock for each share of "
    "blueharbor outstanding common stock, for an implied value of $50.78 per "
    "share based on TowneBank's 10-day volume-weighted average price. The "
    "transaction is expected to close in the first quarter of 2027 and is "
    "subject to customary closing conditions, including regulatory approval, "
    "as well as the approval of blueharbor's shareholders.")
RMBI_WIRE = (
    "Richmond Mutual Bancorporation, Inc. (NASDAQ: RMBI) and The Farmers "
    "Bancorp today announced that they have entered into a definitive "
    "agreement under which Farmers Bancorp will merge with and into Richmond "
    "Mutual in an all-stock transaction valued at approximately $82 million, "
    "or $44.71 per share of Farmers Bancorp common stock, based on a closing "
    "price for Richmond Mutual's common stock of $13.15 as of November 11, "
    "2025. For Farmers Bancorp shareholders, based on the exchange ratio of "
    "3.40x and the current dividend levels of each company, the merger will "
    "result in dividend per share accretion of approximately 27.5%.")
ESQ_WIRE = (
    "Esquire Financial Holdings, Inc. (NASDAQ: ESQ) announced a definitive "
    "agreement. Under the terms of the merger agreement, shareholders of "
    "Signature will receive a fixed exchange ratio of 2.63 shares of Esquire "
    "common stock for each share of Signature common stock. The per share "
    "value equates to $260.48 for Signature shareholders based on the closing "
    "price of Esquire common stock on March 11, 2026, or approximately $348.4 "
    "million in aggregate transaction value.")
CBAN_8K = (
    "On June 24, 2026, Colony Bankcorp, Inc. (NASDAQ: CBAN), a Georgia "
    "corporation (the \"Company\"), entered into an Agreement and Plan of "
    "Merger (the \"Merger Agreement\") with First Reliance Bancshares, Inc. "
    "(\"FSRL\"), pursuant to which FSRL will merge with and into the Company. "
    "The Company's previously announced agreement to acquire TC Bancshares, "
    "Inc. remains pending. Under the Merger Agreement, Colony will acquire "
    "First Reliance in an all-stock transaction. The Company expects to close "
    "the merger with TC Bancshares, Inc. in the third quarter of 2026.")


class TestWireRatioForms(unittest.TestCase):

    def test_townebank_cash_and_shares(self):
        r = extract_exchange_ratio(TOWN_WIRE)
        self.assertEqual((r[0], r[1], r[2]), (1.0534, "TowneBank", "blueharbor"))
        self.assertEqual(extract_cash_per_share(TOWN_WIRE), 12.7)
        t = extract_terms(TOWN_WIRE)
        self.assertEqual(t["consideration"], "mixed")
        self.assertEqual(t["implied_price_stated"], 50.78)
        self.assertEqual(t["expected_close_date"], "2027-03-31")
        # 1.0534 × $36.15 + $12.70 = 38.08041 + 12.70 = 50.78041 (hand) — the
        # release's own $50.78 on its stated 10-day VWAP of $36.15.
        v, _n = implied_offer(dict(t, consideration="mixed"), 36.15, basis_label="x")
        self.assertEqual(v, 50.7804)

    def test_richmond_mutual_bare_exchange_ratio(self):
        from data.ma_announcements import extract_stated_value
        self.assertEqual(extract_exchange_ratio(RMBI_WIRE), (3.4, "", ""))
        self.assertEqual(extract_stated_value(RMBI_WIRE), 82_000_000)
        self.assertEqual(extract_terms(RMBI_WIRE)["consideration"], "stock")

    def test_esquire_fixed_exchange_ratio(self):
        from data.ma_announcements import extract_stated_value
        r = extract_exchange_ratio(ESQ_WIRE)
        self.assertEqual((r[0], r[1], r[2]), (2.63, "Esquire", "Signature"))
        self.assertEqual(extract_terms(ESQ_WIRE)["implied_price_stated"], 260.48)
        self.assertEqual(extract_stated_value(ESQ_WIRE), 348_400_000)


class TestCashLegMergerWith(unittest.TestCase):

    def test_two_targets_in_one_8k_resolve_to_the_agreement_party(self):
        from unittest.mock import MagicMock
        from data import ma_announcements as ma
        hits = [{"_id": "0001-26-7:cban.htm",
                 "_source": {"adsh": "0001-26-7", "file_date": "2026-06-24",
                             "ciks": ["0000711669"], "file_type": "8-K",
                             "items": ["1.01", "8.01"],
                             "display_names": ["COLONY BANKCORP INC  (CBAN)  "
                                               "(CIK 0000711669)"]}}]
        resp = MagicMock(); resp.json.return_value = {"hits": {"hits": hits}}
        resp.raise_for_status = MagicMock()
        with patch("data.ma_announcements.requests.get", return_value=resp), \
             patch("data.ma_announcements._accession_text",
                   return_value=(CBAN_8K, True)), \
             patch("data.ma_announcements._close_before",
                   return_value=(None, None, True)), \
             patch("data.ma_announcements.time.sleep", lambda *_: None):
            rows, ok = ma.find_open_announcements(711669, "Colony Bank")
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["counterparty_name"], "First Reliance")
        self.assertEqual(rows[0]["direction"], "acquisition")


class TestWireResolver(unittest.TestCase):

    def _prs(self):
        return [
            {"title": "Richmond Mutual Bancorporation, Inc. Announces Completion "
                      "of Merger with The Farmers Bancorp, Frankfort, Indiana",
             "url": "https://www.prnewswire.com/done", "published_at": "2026-07-01 09:00:00",
             "text": "completed the merger"},
            {"title": "RICHMOND MUTUAL AND THE FARMERS BANCORP ANNOUNCE "
                      "TRANSFORMATIONAL STRATEGIC MERGER",
             "url": "https://www.prnewswire.com/ann", "published_at": "2025-11-12 09:00:00",
             "text": "Richmond Mutual Bancorporation, Inc. and The Farmers Bancorp"},
            {"title": "Richmond Mutual Bancorporation, Inc. Announces Third Quarter Results",
             "url": "https://www.prnewswire.com/q3", "published_at": "2025-10-24 09:00:00",
             "text": "earnings"},
        ]

    def test_completed_deal_resolved_from_wire(self):
        from data.ma_announcements import resolve_announcement_wire
        with patch("data.ma_announcements._wire_releases", return_value=self._prs()), \
             patch("data.ma_announcements._wire_story_text",
                   side_effect=lambda u: {"https://www.prnewswire.com/ann": RMBI_WIRE}.get(u, "")), \
             patch("data.events.fmp_news._is_subject", return_value=True), \
             patch("data.ma_announcements._close_before",
                   return_value=(13.15, "2025-11-11", True)), \
             patch("data.ma_announcements.time.sleep", lambda *_: None):
            r, ok = resolve_announcement_wire(
                "RMBI", "The Farmers Bank, Frankfort, Indiana",
                "First Bank Richmond", "2026-07-01")
        self.assertTrue(ok)
        self.assertIsNotNone(r)
        self.assertEqual(r["announce_date"], "2025-11-12")
        self.assertEqual(r["value_usd"], 82_000_000)
        self.assertEqual(r["source"], "wire")
        self.assertEqual(r["terms"]["exchange_ratio"], 3.4)
        self.assertEqual(r["terms"]["acq_ticker"], "RMBI")
        # 3.40 × $13.15 = $44.71 — the release's own per-share figure.
        self.assertEqual(r["terms"]["implied_price"], 44.71)

    def test_feed_unavailable_is_not_cacheable(self):
        from data.ma_announcements import resolve_announcement_wire
        with patch("data.ma_announcements._wire_releases", return_value=None):
            self.assertEqual(resolve_announcement_wire("RMBI", "X Bank", "Y", "2026-07-01"),
                             (None, False))


class TestWirePendingLeg(unittest.TestCase):

    def test_townebank_blueharbor_pending_from_wire(self):
        from data import ma_pending
        prs = [{"title": "TowneBank Enhances North Carolina Presence Through "
                         "Agreement To Acquire blueharbor bank",
                "url": "https://www.globenewswire.com/town", "published_at": "2026-10-06 08:30:00",
                "text": "TowneBank (NASDAQ: TOWN) today announced"},
               {"title": "TowneBank Reports Third Quarter 2026 Earnings",
                "url": "https://www.globenewswire.com/q3", "published_at": "2026-10-02 08:30:00",
                "text": "TowneBank"}]
        import datetime as _dt

        class _FakeDate(_dt.date):
            @classmethod
            def today(cls):
                return _dt.date(2026, 10, 6)
        with patch("data.ma_pending._wire_releases", return_value=prs), \
             patch("data.ma_pending._wire_story_text",
                   side_effect=lambda u: TOWN_WIRE if u.endswith("/town") else ""), \
             patch("data.events.fmp_news._is_subject", return_value=True), \
             patch("data.ma_pending._universe_match", return_value=(None, None, None)), \
             patch("data.ma_announcements._close_before",
                   return_value=(36.15, "2026-10-05", True)), \
             patch("data.ma_pending.time.sleep", lambda *_: None), \
             patch("data.ma_pending.date", _FakeDate):
            rows, ok = ma_pending.find_pending_wire("TOWN", "TowneBank")
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["counterparty_name"], "blueharbor bank")
        self.assertEqual(r["announce_date"], "2026-10-06")
        self.assertEqual(r["source"], "wire")
        self.assertEqual(r["terms"]["consideration"], "mixed")
        self.assertEqual(r["terms"]["implied_price"], 50.78)
        self.assertEqual(r["terms"]["expected_close_date"], "2027-03-31")

    def test_later_completion_release_closes_the_deal(self):
        from data import ma_pending
        prs = [{"title": "TowneBank Completes Acquisition of blueharbor bank",
                "url": "https://www.globenewswire.com/done", "published_at": "2027-03-01 08:30:00",
                "text": "blueharbor"},
               {"title": "TowneBank Enhances North Carolina Presence Through "
                         "Agreement To Acquire blueharbor bank",
                "url": "https://www.globenewswire.com/town", "published_at": "2026-10-06 08:30:00",
                "text": "TowneBank"}]
        import datetime as _dt

        class _FakeDate(_dt.date):
            @classmethod
            def today(cls):
                return _dt.date(2027, 3, 5)
        with patch("data.ma_pending._wire_releases", return_value=prs), \
             patch("data.ma_pending._wire_story_text", return_value=TOWN_WIRE), \
             patch("data.events.fmp_news._is_subject", return_value=True), \
             patch("data.ma_pending._universe_match", return_value=(None, None, None)), \
             patch("data.ma_announcements._close_before",
                   return_value=(36.15, "2027-03-04", True)), \
             patch("data.ma_pending.time.sleep", lambda *_: None), \
             patch("data.ma_pending.date", _FakeDate):
            rows, ok = ma_pending.find_pending_wire("TOWN", "TowneBank")
        self.assertTrue(ok)
        self.assertEqual(rows, [])

    def test_wire_rows_pass_without_edgar(self):
        from data import ma_pending
        row = {"announce_date": "2026-10-06", "direction": "acquisition",
               "counterparty_name": "blueharbor bank", "counterparty_ticker": None,
               "counterparty_cert": None, "counterparty_cik": None,
               "value_usd": None, "value_basis": None, "value_note": None,
               "target_cik": None, "announce_url": "u", "terms": {}, "source": "wire"}
        with patch("data.ma_pending._find_pending_425", return_value=([], True)), \
             patch("data.ma_pending.find_open_announcements", return_value=([], True)), \
             patch("data.ma_pending.find_pending_wire", return_value=([row], True)):
            rows, ok = ma_pending.find_pending_deals(None, "TowneBank", ticker="TOWN")
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["milestones"], {"votes": [], "regulatory_approval": None})

    def test_cash_row_takes_universe_name_and_ticker(self):
        from data import ma_pending
        cash = [{"announce_date": "2026-09-30", "direction": "acquisition",
                 "counterparty_name": "Capital", "counterparty_cik": None,
                 "value_usd": 728_100_000, "value_basis": "stated", "value_note": None,
                 "target_cik": None, "announce_url": "u",
                 "terms": {"exchange_ratio": 1.11, "tgt_ticker": None}}]
        with patch("data.ma_pending._find_pending_425", return_value=([], True)), \
             patch("data.ma_pending.find_open_announcements", return_value=(cash, True)), \
             patch("data.ma_pending._universe_match", return_value=("CBNK", 35278, 1419536)), \
             patch("data.bank_mapping.get_name", return_value="Capital Bancorp"), \
             patch("data.ma_pending._resolved_after", return_value=(False, True)), \
             patch("data.ma_pending.iter_submission_filings", return_value=([], True)), \
             patch("data.ma_pending._milestones",
                   return_value=({"votes": [], "regulatory_approval": None}, True)):
            rows, ok = ma_pending.find_pending_deals(318300, "Peoples Bank", ticker=None)
        self.assertTrue(ok)
        self.assertEqual(rows[0]["counterparty_name"], "Capital Bancorp")
        self.assertEqual(rows[0]["counterparty_ticker"], "CBNK")
        self.assertEqual(rows[0]["terms"]["tgt_ticker"], "CBNK")


class TestInternalConsolidation(unittest.TestCase):

    def test_same_holdco_without_announcement_is_not_a_deal(self):
        from data.deal_comps import build_comps_snapshot
        internal = {"deal_kind": "whole_company", "direction": "acquisition",
                    "status": "completed", "completion_date": "2025-05-01",
                    "counterparty": {"name": "Lena State Bank", "cert": 1},
                    "announce_date": None, "announce_url": None,
                    "target_assets": 97_400_000, "value_usd": None, "internal": True}
        real = dict(internal, counterparty={"name": "Bank of Idaho", "cert": 2},
                    internal=False, target_assets=1_330_000_000)
        with patch("data.ma_history.get_ma_history", return_value=[internal, real]), \
             patch("data.deal_comps.compute_multiples", return_value=({}, True)), \
             patch("data.cache.put"), \
             patch("data.deal_comps._EMPTY_HISTORY_RETRY_WAITS", ()):
            snap = build_comps_snapshot([{"ticker": "GBCI", "cert": 9, "cik": 1}])
        self.assertEqual([r["target_name"] for r in snap["deals"]], ["Bank of Idaho"])

    def test_same_holdco_helper(self):
        from data.ma_history import _same_holdco
        with patch("data.fdic_client.get_holdco_rssd_for_cert",
                   side_effect=lambda c: {1: 777, 2: 777, 3: 888, 4: None}[c]):
            self.assertTrue(_same_holdco(1, 2))
            self.assertFalse(_same_holdco(1, 3))
            self.assertFalse(_same_holdco(1, 4))
            self.assertFalse(_same_holdco(None, 2))

    def test_wire_url_dedupe_key(self):
        from data.deal_comps import _dedupe_key
        k = _dedupe_key({"announce_url": "https://www.globenewswire.com/news-release/2026/10/06/x.html"}, 1)
        self.assertEqual(k, ("acc", "https://www.globenewswire.com/news-release/2026/10/06/x.html"))


if __name__ == "__main__":
    unittest.main()
