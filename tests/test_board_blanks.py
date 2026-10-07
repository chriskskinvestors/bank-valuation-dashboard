"""
Board blanks circled by the owner 2026-10-07 ("LOT MISSING"). Every fixture
sentence is verbatim from the live filing of the row it fills; every
expected number is hand-computed.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

NRIM = ("each PBCO shareholder will have the right to receive 1.160 shares of Northrim "
        "common stock in exchange for each share of PBCO common stock. The aggregate "
        "consideration is valued at approximately $167.3 million, or $32.36 per share of "
        "PBCO common stock, based on the closing price of Northrim common stock as of July "
        "21, 2026 of $27.90 per share. The closing of the transaction is expected to occur "
        "in the fourth quarter of 2026 or early in the first quarter of 2027.")
FBNC = ("The aggregate merger consideration has a total current value of approximately "
        "$166 million, or $64.22 per share. Subject to the terms and conditions of the "
        "Merger Agreement, First Carolina's shareholders will receive 14.5340 shares of "
        "First Bancorp common stock and cash in the amount of $294.94 for each share of "
        "First Carolina common stock. The parties anticipate closing the Merger during the "
        "fourth quarter of 2026 or early in the first quarter of 2027. Upon termination of "
        "the Merger Agreement, under certain circumstances First Carolina may be required "
        "to pay First Bancorp a termination fee of $6.4 million.")
BSVN = ("At closing, each outstanding share of Century common stock will be converted into "
        "the right to receive a pro rata portion of aggregate consideration consisting of "
        "$70 million in cash and 1,232,657 shares of Company common stock. Based on 332,683 "
        "shares of Century common stock outstanding, this would equate to $210.41 in cash "
        "and 3.7052 shares of Company common stock per Century share, and implies an "
        "aggregate transaction value of approximately $137.3 million.")
HBT = ("Under the terms of the merger agreement, Tri-County shareholders will have the "
       "right to receive either (1) 2.4589 shares of HBT Financial’s common stock for "
       "each share of Tri-County stock, or (2) $71.01 in cash for each share of Tri-County "
       "stock. "
       "will be converted into the right to receive, at the option of each TYFG "
       "stockholder, one of the following: (i) 2.4589 validly issued, fully paid and "
       "nonassessable shares of HBT common stock, par value $0.01 per share, (ii) cash in "
       "the amount of $71.01, or (iii) a combination of cash and shares of HBT common "
       "stock, in each case subject to adjustment and to the election and proration "
       "procedures as provided in the Merger Agreement. In aggregate, based on TYFG's "
       "common stock and stock options outstanding as of the date hereof, TYFG "
       "stockholders are expected to receive cash consideration of approximately $59.9 "
       "million and stock consideration of approximately 3.8 million shares of HBT common "
       "stock. Based upon HBT Financial's closing stock price of $36.35 on August 7, 2026, "
       "the implied per share purchase price is $82.89 with an aggregate transaction value "
       "of approximately $204.6 million.")
TOWN = ("TowneBank (NASDAQ: TOWN) and blueharbor bank (OTCQX: BLHK) (“blueharbor”), "
        "today announced the signing of a definitive merger agreement pursuant to which "
        "TowneBank will acquire blueharbor for approximately $154 million, based on "
        "TowneBank's 10-day volume-weighted average price of $36.15 as of October 2, 2026.")
VLY = ("Valley will acquire the outstanding equity of Bluevine for $340,000,000 in aggregate "
       "consideration, subject to certain adjustments described in the Merger Agreement. "
       "Under the terms of the proposed transaction, Valley will acquire Bluevine for total "
       "consideration of approximately $340 million.")


class TestNewForms(unittest.TestCase):

    def test_northrim_pbco(self):
        from data.ma_announcements import extract_terms
        t = extract_terms(NRIM)
        self.assertEqual((t["exchange_ratio"], t["consideration"], t["implied_price_stated"]),
                         (1.16, "stock", 32.36))
        self.assertEqual(t["expected_close_date"], "2027-03-31")   # later bound of the range
        self.assertAlmostEqual(1.160 * 27.90, 32.36, places=2)

    def test_first_carolina_mixed_and_fee(self):
        from data.ma_announcements import extract_terms
        t = extract_terms(FBNC)
        self.assertEqual((t["exchange_ratio"], t["cash_per_share"], t["consideration"]),
                         (14.534, 294.94, "mixed"))
        self.assertEqual(t["termination_fee_usd"], 6_400_000)
        self.assertEqual(t["expected_close_date"], "2027-03-31")
        from data.ma_announcements import extract_stated_value
        self.assertEqual(extract_stated_value(FBNC), 166_000_000)   # "total current value"

    def test_century_equate_form(self):
        from data.ma_announcements import extract_terms, implied_offer
        t = extract_terms(BSVN)
        self.assertEqual((t["exchange_ratio"], t["cash_per_share"], t["consideration"]),
                         (3.7052, 210.41, "mixed"))
        # 3.7052 x $54.57 + $210.41 = $412.60
        v, _ = implied_offer(t, 54.57, basis_label="BSVN")
        self.assertAlmostEqual(v, 412.6, places=1)

    def test_tri_county_derived_proration_reproduces_the_stated_price(self):
        from data.ma_announcements import extract_terms, fill_implied_price
        t = extract_terms(HBT)
        # 59.9M / 71.01 = 843,543 cash shares; 3.8M / 2.4589 = 1,545,406 stock
        # shares -> 64.7% stock / 35.3% cash.
        self.assertEqual((t["stock_pct"], t["cash_pct"], t["mix_basis"]), (64.7, 35.3, "derived"))
        self.assertEqual(t["consideration"], "election")    # "at the option of each"
        self.assertEqual(t["implied_price_stated"], 82.89)
        ok = fill_implied_price(t, "2026-08-10", acq_tick="HBT", tgt_tick="TYFG",
                                close_lookup=lambda tk, d: (36.35, "2026-08-07", True)
                                if tk == "HBT" else (70.00, "2026-08-07", True))
        self.assertTrue(ok)
        # 0.647 x 2.4589 x 36.35 + 0.353 x 71.01 = 57.83 + 25.07 = 82.90 ~ 82.89
        self.assertEqual((t["stock_pct"], t["cash_pct"]), (64.7, 35.3))   # kept
        self.assertEqual(t["implied_price"], 82.89)
        self.assertEqual(t["premium_computed"], round((82.89 / 70.00 - 1) * 100, 1))

    def test_derived_proration_that_misses_the_price_is_dropped(self):
        from data.ma_announcements import extract_terms, fill_implied_price
        t = extract_terms(HBT.replace("$82.89", "$95.00"))
        fill_implied_price(t, "2026-08-10", acq_tick="HBT",
                           close_lookup=lambda tk, d: (36.35, "2026-08-07", True))
        self.assertEqual((t["stock_pct"], t["cash_pct"]), (None, None))

    def test_stated_price_far_from_ratio_times_price_is_rejected(self):
        # First Carolina's "$64.22 per share" vs 14.534 x $50 + $294.94 = $1,021.64
        from data.ma_announcements import extract_terms, fill_implied_price
        t = extract_terms(FBNC)
        self.assertEqual(t["implied_price_stated"], 64.22)
        fill_implied_price(t, "2026-07-14", acq_tick="FBNC",
                           close_lookup=lambda tk, d: (50.00, "2026-07-11", True))
        self.assertIsNone(t["implied_price_stated"])
        self.assertEqual(t["implied_price_stated_rejected"], 64.22)
        self.assertAlmostEqual(t["implied_price"], 14.534 * 50 + 294.94, places=2)
        self.assertEqual(t["implied_price_basis"], "computed")

    def test_value_forms(self):
        from data.ma_announcements import extract_stated_value
        self.assertEqual(extract_stated_value(TOWN), 154_000_000)
        self.assertEqual(extract_stated_value(VLY), 340_000_000)       # both forms agree

    def test_premium_band_rejects_a_stale_close(self):
        from data.ma_announcements import fill_implied_price
        t = {"exchange_ratio": 2.0, "consideration": "stock", "implied_price_stated": 46.72}
        fill_implied_price(t, "2026-09-08", acq_tick="JMSB", tgt_tick="EFSI",
                           close_lookup=lambda tk, d: (23.36, "2026-09-05", True)
                           if tk == "JMSB" else (10.00, "2026-09-05", True))
        self.assertNotIn("premium_computed", t)   # 46.72/10 - 1 = 367% > 200% band


class TestMergerWithFallback(unittest.TestCase):

    def test_agreement_party_is_the_counterparty_when_nothing_else_captures(self):
        from unittest.mock import MagicMock
        from data import ma_announcements as ma
        text = ("On July 14, 2026, First Bancorp entered into an Agreement and Plan of Merger "
                "(the Merger Agreement) with First Carolina Bancshares Corporation, a South "
                "Carolina corporation. " + FBNC)
        hits = [{"_id": "0001-26-1:fbnc.htm",
                 "_source": {"adsh": "0001-26-1", "file_date": "2026-07-14",
                             "ciks": ["0000811589"], "file_type": "8-K",
                             "items": ["1.01", "8.01"],
                             "display_names": ["FIRST BANCORP /NC/ (FBNC) (CIK 0000811589)"]}}]
        resp = MagicMock(); resp.json.return_value = {"hits": {"hits": hits}}
        resp.raise_for_status = MagicMock()
        with patch("data.ma_announcements.requests.get", return_value=resp),              patch("data.ma_announcements._accession_text", return_value=(text, True)),              patch("data.ma_announcements.compute_stock_value", return_value=(None, True)),              patch("data.ma_announcements._close_before", return_value=(None, None, True)),              patch("data.ma_announcements.time.sleep", lambda *_: None):
            rows, ok = ma.find_open_announcements(811589, "First Bank")
        self.assertTrue(ok)
        self.assertEqual([r["counterparty_name"] for r in rows],
                         ["First Carolina Bancshares Corporation"])
        self.assertEqual(rows[0]["terms"]["termination_fee_usd"], 6_400_000)


class TestWireDefinedTerm(unittest.TestCase):

    def test_great_plains_expands_to_the_holdco_name(self):
        from data.ma_announcements import expand_defined_term
        text = ('Third Coast Bancshares, Inc. ("Third Coast") and Great Plains Bancshares, '
                'Inc. ("Great Plains"), the parent company of Great Plains National Bank, '
                'today jointly announced the signing of a definitive merger agreement '
                'pursuant to which Third Coast will acquire Great Plains in an all-stock '
                'transaction valued at approximately $239.6 million.')
        self.assertEqual(expand_defined_term("Great Plains", text), "Great Plains Bancshares, Inc")


class TestBoardCells(unittest.TestCase):

    def test_stock_deal_cash_cell_reads_none(self):
        import ui.transactions as tx
        self.assertIn('_fmt_px(cash) if cash or mix != "stock" else "none"',
                      open(tx.__file__, encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
