"""
Board pass 2026-10-07 21:22Z defects (verbatim fixtures, hand-computed
values): Century priced as an election, First Carolina's unchecked stated
price, First Seacoast shown as the acquirer, WaFd's close phrase, and
listed targets sold to unlisted buyers.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

FSEA = ("On May 4, 2026, First Seacoast Bancorp, Inc. (the \u201cCompany\u201d), the holding "
        "company of First Seacoast Bank, and Cambridge Financial Group, Inc. (\u201cCambridge "
        "Financial\u201d), the mutual holding company of Cambridge Savings Bank, entered into "
        "an Agreement and Plan of Merger (the \u201cMerger Agreement\u201d) pursuant to which "
        "the Company will merge with Cambridge Financial, with Cambridge Financial as the "
        "surviving corporation (the \u201cMerger\u201d). Under the terms of the Merger Agreement, "
        "each share of Company common stock outstanding immediately before the effective "
        "time of the Merger will be converted into the right to receive $17.25 in cash, "
        "without interest. The transaction is expected to close in the third quarter of 2026.")


class TestCenturyIsMixed(unittest.TestCase):

    def test_both_legs_per_share_is_mixed_despite_election_wording(self):
        from data.ma_announcements import extract_terms, implied_offer
        text = ("Century shareholders may elect the timing of their tax reporting. "
                "Based on 332,683 shares of Century common stock outstanding, this would "
                "equate to $210.41 in cash and 3.7052 shares of Company common stock per "
                "Century share.")
        t = extract_terms(text)
        self.assertEqual(t["consideration"], "mixed")
        v, _ = implied_offer(t, 54.57, basis_label="BSVN")
        self.assertAlmostEqual(v, 3.7052 * 54.57 + 210.41, places=2)   # 412.60


class TestCloseCut(unittest.TestCase):

    def test_and_be_ends_the_phrase(self):
        from data.ma_announcements import extract_expected_close
        phrase, d = extract_expected_close(
            "The merger is expected to close in early 2027 and be tax-free to shareholders.")
        self.assertEqual(phrase, "in early 2027")
        self.assertIsNone(d)                          # "early 2027" pins no period


class TestFirstSeacoastIsTheTarget(unittest.TestCase):

    def test_per_share_side_is_self_so_the_row_is_a_sale(self):
        from data import ma_announcements as ma
        hits = [{"_id": "0001-26-5:d74001d8k.htm",
                 "_source": {"adsh": "0001-26-5", "file_date": "2026-05-05",
                             "ciks": ["0001772921"], "file_type": "8-K",
                             "items": ["1.01", "8.01"],
                             "display_names": ["First Seacoast Bancorp, Inc. (FSEA) (CIK 0001772921)"]}}]
        resp = MagicMock(); resp.json.return_value = {"hits": {"hits": hits}}
        resp.raise_for_status = MagicMock()
        with patch("data.ma_announcements.requests.get", return_value=resp), \
             patch("data.ma_announcements._accession_text", return_value=(FSEA, True)), \
             patch("data.ma_announcements.compute_stock_value", return_value=(None, True)), \
             patch("data.ma_announcements._close_before", return_value=(None, None, True)), \
             patch("data.ma_announcements.time.sleep", lambda *_: None):
            rows, ok = ma.find_open_announcements(1772921, "First Seacoast Bank")
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["direction"], "sale")
        self.assertEqual(rows[0]["counterparty_name"], "Cambridge Financial Group, Inc")
        self.assertEqual((rows[0]["terms"]["consideration"], rows[0]["terms"]["cash_per_share"]),
                         ("cash", 17.25))

    def test_surviving_corporation_tail_is_not_the_name(self):
        from data.ma_announcements import _clean_company_name
        self.assertEqual(_clean_company_name("Cambridge Financial as the surviving corporation"),
                         "Cambridge Financial")


class TestUnlistedBuyerRow(unittest.TestCase):

    def test_sale_to_an_unlisted_buyer_is_shown_from_the_target_side(self):
        from data.deal_comps import _as_target_view, _sale_to_unlisted, _snapshot_row
        d = {"status": "pending", "direction": "sale", "deal_kind": "whole_company",
             "announce_date": "2026-05-05",
             "counterparty": {"name": "Cambridge Financial Group, Inc", "cert": 90001,
                              "ticker": None},
             "terms": {"consideration": "cash", "cash_per_share": 17.25,
                       "acq_ticker": None, "tgt_ticker": None}}
        b = {"ticker": "FSEA", "name": "First Seacoast Bancorp", "cert": 4561, "cik": 1772921}
        self.assertTrue(_sale_to_unlisted(d))
        buyer, bcert, d2 = _as_target_view(b, 4561, d)
        row = _snapshot_row(buyer, bcert, d2, {})
        self.assertEqual((row["buyer_ticker"], row["buyer_name"], row["target_name"],
                          row["target_ticker"], row["target_cert"]),
                         (None, "Cambridge Financial Group, Inc", "First Seacoast Bancorp",
                          "FSEA", 4561))
        # a sale to a LISTED buyer stays off (the buyer's own row carries it)
        d_listed = dict(d, counterparty={"name": "John Marshall Bancorp", "cert": 1,
                                         "ticker": "JMSB"})
        self.assertFalse(_sale_to_unlisted(d_listed))

    def test_target_view_yields_to_the_listed_acquirers_row(self):
        # Finward's sale row named "First Financial Bancorp" without a
        # ticker; FFBC's own row already carries the deal (board 10-07 23:36).
        from data.deal_comps import _drop_shadowed_target_views
        ffbc = {"buyer_ticker": "FFBC", "status": "pending", "target_ticker": "FNWD",
                "target_cert": 28520}
        dup = {"buyer_ticker": None, "status": "pending", "target_ticker": "FNWD",
               "target_cert": 28520}
        fsea = {"buyer_ticker": None, "status": "pending", "target_ticker": "FSEA",
                "target_cert": 4561}
        done = {"buyer_ticker": None, "status": "completed", "target_ticker": "FNWD"}
        self.assertEqual(_drop_shadowed_target_views([ffbc, dup, fsea, done]),
                         [ffbc, fsea, done])

    def test_cash_deal_spread_needs_no_acquirer_price(self):
        # $17.25 cash vs FSEA 16.90 -> 17.25 / 16.90 - 1 = 2.071%
        from data.deal_spreads import build_spread_histories
        d = {"status": "pending", "buyer_ticker": None, "buyer_name": "Cambridge Financial",
             "target_ticker": "FSEA", "target_name": "First Seacoast Bancorp",
             "announce_date": "2026-05-05",
             "terms": {"consideration": "cash", "cash_per_share": 17.25,
                       "expected_close_date": "2026-09-30"}}
        built = build_spread_histories(
            [d], history=lambda t: [{"date": "2026-05-06", "close": 16.90}] if t == "FSEA" else None)
        s = next(iter(built["deals"].values()))["series"]
        self.assertAlmostEqual(s[0]["gross"], 17.25 / 16.90 - 1, places=5)
        self.assertIsNone(s[0]["acq"])


class TestStatedPriceRecheck(unittest.TestCase):

    def test_prefilled_stated_price_is_checked_against_ratio_times_close(self):
        from data import ma_pending
        cash = [{"announce_date": "2026-07-14", "direction": "acquisition",
                 "counterparty_name": "First Carolina Bancshares Corporation",
                 "counterparty_ticker": None, "counterparty_cik": None,
                 "value_usd": 166_000_000, "value_basis": "stated", "value_note": None,
                 "target_cik": None, "announce_url": "u",
                 "terms": {"consideration": "mixed", "exchange_ratio": 14.534,
                           "cash_per_share": 294.94, "implied_price_stated": 64.22,
                           "implied_price": 64.22, "implied_price_basis": "stated",
                           "acq_close_at_announce": None}}]
        with patch("data.ma_pending._find_pending_425", return_value=([], True)), \
             patch("data.ma_pending.find_open_announcements", return_value=(cash, True)), \
             patch("data.ma_pending.find_pending_wire", return_value=([], True)), \
             patch("data.ma_pending.fdic_cert_for_name", return_value=(16723, "Carolina Bank & Trust Co.", True)), \
             patch("data.ma_pending._close_before", return_value=(50.00, "2026-07-11", True)), \
             patch("data.ma_pending._wire_resolved", return_value=False), \
             patch("data.ma_pending._resolved_after", return_value=(False, True)), \
             patch("data.ma_pending.iter_submission_filings", return_value=([], True)), \
             patch("data.ma_pending._milestones",
                   return_value=({"votes": [], "regulatory_approval": None}, True)):
            rows, ok = ma_pending.find_pending_deals(811589, "First Bank", ticker="FBNC")
        t = rows[0]["terms"]
        # 14.534 x $50.00 + $294.94 = $1,021.64; the stated $64.22 is rejected
        self.assertIsNone(t["implied_price_stated"])
        self.assertAlmostEqual(t["implied_price"], 1021.64, places=2)


class TestCaption(unittest.TestCase):

    def test_premium_caption_dollar_is_escaped(self):
        import ui.transactions as tx
        src = open(tx.__file__, encoding="utf-8").read()
        self.assertIn("premium* = implied \\$/sh", src)
        self.assertNotIn("premium* = implied $/sh", src)


if __name__ == "__main__":
    unittest.main()
