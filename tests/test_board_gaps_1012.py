"""
Board 2026-10-12: First Seacoast still pending after its 10-01 delisting
(the buyer, a mutual holding company, files nothing on EDGAR); Great Plains'
deal value blank because the release says "$239.6 million" and the deck
"$240 million" (verbatim, Third Coast 425 filed 2026-10-07).
"""
from __future__ import annotations

import unittest


class TestStatedValuePrecision(unittest.TestCase):

    def test_rounded_restatement_is_the_same_value(self):
        from data.ma_announcements import extract_stated_value
        text = ("in an all-stock transaction valued at approximately $239.6 million. "
                "Implied aggregate transaction value of $240 million")
        self.assertEqual(extract_stated_value(text), 239_600_000)

    def test_genuinely_different_values_stay_ambiguous(self):
        from data.ma_announcements import extract_stated_value
        text = ("transaction valued at approximately $239.6 million. "
                "transaction valued at approximately $250 million")
        self.assertIsNone(extract_stated_value(text))

    def test_raw_dollars_and_millions_agree(self):
        from data.ma_announcements import extract_stated_value
        text = ("Valley will acquire the outstanding equity of Bluevine for $340,000,000 in "
                "aggregate consideration. Valley will acquire Bluevine for total consideration "
                "of approximately $340 million.")
        self.assertEqual(extract_stated_value(text), 340_000_000)


class TestTargetDelisted(unittest.TestCase):

    def test_first_seacoast_form_25_and_15_close_the_sale(self):
        from data.ma_pending import _self_delisted
        filings = [{"form": "15-12G", "date": "2026-10-01"},
                   {"form": "25-NSE", "date": "2026-10-01"},
                   {"form": "8-K", "date": "2026-08-27", "items": "5.07"}]
        self.assertTrue(_self_delisted(filings, "2026-05-05"))

    def test_a_delisting_before_the_announcement_does_not_count(self):
        from data.ma_pending import _self_delisted
        self.assertFalse(_self_delisted([{"form": "25-NSE", "date": "2026-01-02"}],
                                        "2026-05-05"))
        self.assertFalse(_self_delisted([{"form": "8-K", "date": "2026-10-01"}], "2026-05-05"))



VOTE_801 = ("At a special meeting of the shareholders of Grand River held on September 18, "
            "2026, Grand River shareholders approved the Merger Agreement and the transactions "
            "contemplated thereby, including the Transaction. The companies expect to complete "
            "the proposed Transaction in the fourth quarter of 2026, subject to the receipt of "
            "all regulatory approvals.")
APPROVAL_801 = ("Isabella Bank Corporation and Grand River Commerce, Inc. announced today receipt "
                "of all required regulatory approvals necessary to complete their previously "
                "announced merger. In addition, Grand River’s shareholders voted to approve "
                "the merger on September 18, 2026. The merger is expected to close on November 2, "
                "2026, pending satisfaction of customary closing conditions.")


class TestCounterpartyVoteAndCloseUpdate(unittest.TestCase):

    def _ms(self, texts):
        from unittest.mock import patch
        from data import ma_pending
        filings = [{"form": "8-K", "date": "2026-09-21", "accession": "a1", "doc": "d1.htm",
                    "items": "8.01"},
                   {"form": "8-K", "date": "2026-10-06", "accession": "a2", "doc": "d2.htm",
                    "items": "8.01,9.01"}]
        with patch("data.ma_pending._accession_text",
                   side_effect=lambda c, a, d: (texts[a], True)),              patch("data.ma_pending.time.sleep", lambda *_: None):
            return ma_pending._milestones(842517, "Grand River Commerce, Inc", "2026-06-12",
                                          filings, side="acquirer")

    def test_grand_river_vote_and_november_close(self):
        ms, ok = self._ms({"a1": VOTE_801, "a2": APPROVAL_801})
        self.assertTrue(ok)
        self.assertEqual([(v["side"], v["date"]) for v in ms["votes"]], [("target", "2026-09-18")])
        self.assertEqual((ms["expected_close"]["date"], ms["expected_close"]["filed"]),
                         ("2026-11-02", "2026-10-06"))       # the later filing wins

    def test_forward_looking_vote_is_not_a_vote(self):
        ms, _ = self._ms({"a1": "Closing is subject to approval by Grand River shareholders.",
                          "a2": "Grand River shareholders will vote at a special meeting."})
        self.assertEqual(ms["votes"], [])

    def test_another_deals_close_sentence_is_ignored(self):
        other = ("Peoples completed its acquisition of Citizens National. The Capital Bancorp "
                 "merger is expected to close in the second quarter of 2027.")
        ms, _ = self._ms({"a1": "Grand River Commerce update. " + other, "a2": "nothing"})
        self.assertNotIn("expected_close", ms)


if __name__ == "__main__":
    unittest.main()
