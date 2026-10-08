"""
Board blanks 2026-10-08: Peoples/Citizens National (generic holdco name ->
no FDIC cert -> every valuation cell n/a; deck "Deal Value / TBV: 118%"
unread). Fixture text verbatim from the filings.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

CZNL_PR = ("pursuant to which Peoples will acquire Citizens, a bank holding company headquartered "
           "in Paintsville, Kentucky, and the parent company of Citizens Bank of Kentucky, Inc. "
           "(\u201cCitizens Bank\u201d), in a cash and stock transaction.")
CZNL_DECK = ("Kentucky Peers Kentucky Peers 20 \u2022 Deal Value / TBV: 118% \u2022 Deal Value / "
             "LTM Earnings: 12.7x \u2022 TBV Dilution at Close: 0.9% | TBV Earnback Period: <1 year")
# FDIC institutions rows for NAMEHCR:"Citizens National" (2026-10-08)
FDIC_ROWS = [
    {"NAMEHCR": "CITIZENS NATIONAL CORP", "CERT": 2718, "NAME": "Citizens Bank of Kentucky, Inc."},
    {"NAMEHCR": "CITIZENS NATIONAL BANC CORP", "CERT": 4993,
     "NAME": "The Citizens National Bank of Meridian"},
    {"NAMEHCR": "CITIZENS NATIONAL CORP", "CERT": 5495, "NAME": "Citizens State Bank"},
]


def _resp(rows):
    r = MagicMock()
    r.json.return_value = {"data": [{"data": d} for d in rows]}
    return r


class TestSubsidiaryNames(unittest.TestCase):

    def test_parent_company_of(self):
        from data.ma_announcements import extract_subsidiary_names, extract_terms
        self.assertEqual(extract_subsidiary_names(CZNL_PR), ["Citizens Bank of Kentucky"])
        self.assertEqual(extract_terms(CZNL_PR)["subsidiary_names"],
                         ["Citizens Bank of Kentucky"])


class TestFdicCertForSubsidiary(unittest.TestCase):

    def test_generic_holdco_resolved_by_its_named_bank(self):
        # Two holdcos are "CITIZENS NATIONAL CORP"; the release's bank names one.
        from data.ma_pending import fdic_cert_for_subsidiary
        with patch("data.http.get_with_retry", return_value=_resp(FDIC_ROWS)):
            self.assertEqual(fdic_cert_for_subsidiary("Citizens National Corporation",
                                                      ["Citizens Bank of Kentucky"]),
                             (2718, True))

    def test_bank_under_another_holdco_is_not_a_link(self):
        # The acquirer's own bank named in the same release never links.
        from data.ma_pending import fdic_cert_for_subsidiary
        with patch("data.http.get_with_retry", return_value=_resp(FDIC_ROWS)):
            self.assertEqual(fdic_cert_for_subsidiary("Citizens National Corporation",
                                                      ["Peoples Bank"]), (None, True))

    def test_fdic_unreachable(self):
        from data.ma_pending import fdic_cert_for_subsidiary
        with patch("data.http.get_with_retry", return_value=None):
            self.assertEqual(fdic_cert_for_subsidiary("Citizens National Corporation",
                                                      ["Citizens Bank of Kentucky"]),
                             (None, False))


class TestDealValueOverTbv(unittest.TestCase):

    def test_deck_deal_value_forms(self):
        from data.ma_announcements import extract_deck_metrics
        self.assertEqual(extract_deck_metrics(CZNL_DECK),
                         {"deck_p_tbv": 1.18, "deck_p_e_ltm": 12.7,
                          "deck_core_dep_premium": None})


class TestPendingRowUsesTheSubsidiary(unittest.TestCase):

    def test_find_pending_deals_falls_back_to_the_subsidiary(self):
        from data import ma_pending
        cash = [{"announce_date": "2026-04-21", "direction": "acquisition",
                 "counterparty_name": "Citizens National Corporation",
                 "counterparty_ticker": None, "counterparty_cik": None,
                 "value_usd": 76_600_000, "value_basis": "stated", "value_note": None,
                 "target_cik": None, "announce_url": "u",
                 "terms": {"consideration": "mixed", "exchange_ratio": 2.1,
                           "cash_per_share": 8.0, "implied_price": 78.39,
                           "implied_price_basis": "stated", "acq_close_at_announce": 33.52,
                           "subsidiary_names": ["Citizens Bank of Kentucky"]}}]
        with patch("data.ma_pending._find_pending_425", return_value=([], True)), \
             patch("data.ma_pending.find_open_announcements", return_value=(cash, True)), \
             patch("data.ma_pending.find_pending_wire", return_value=([], True)), \
             patch("data.ma_pending.fdic_cert_for_name", return_value=(None, None, True)), \
             patch("data.ma_pending.fdic_cert_for_subsidiary", return_value=(2718, True)) as sub, \
             patch("data.ma_pending._wire_resolved", return_value=False), \
             patch("data.ma_pending._resolved_after", return_value=(False, True)), \
             patch("data.ma_pending.iter_submission_filings", return_value=([], True)), \
             patch("data.ma_pending._milestones",
                   return_value=({"votes": [], "regulatory_approval": None}, True)):
            rows, ok = ma_pending.find_pending_deals(318300, "Peoples Bank", ticker="PEBO")
        self.assertTrue(ok)
        sub.assert_called_once_with("Citizens National Corporation", ["Citizens Bank of Kentucky"])
        self.assertEqual(rows[0]["counterparty_cert"], 2718)



ISBA_PR = ("The aggregate deal value is estimated to be approximately $5.72 and the Exchange "
           "Ratio is estimated to be 0.1415. The companies expect to complete the proposed "
           "transaction in the fourth quarter of 2026, subject to the satisfaction of customary "
           "closing conditions, including the receipt of all required regulatory approvals and "
           "approval by Grand River’s shareholders.")
ISBA_EMAIL = ("approval by Grand River Commerce, Inc.’s shareholders and regulatory agencies, "
              "as well as the satisfaction of customary closing conditions. We currently expect "
              "the pending transaction to close in the fourth quarter of 2026. In the coming weeks")
ISBA_101 = ("The Merger Agreement provides certain termination rights for both Isabella and Grand "
            "River and further provides that a termination fee of $2.18 million will be payable "
            "by Grand River upon termination of the Merger Agreement under certain circumstances.")


class TestIsabellaGrandRiver(unittest.TestCase):

    def test_close_phrasings(self):
        from data.ma_announcements import extract_expected_close
        for text in (ISBA_PR, ISBA_EMAIL):
            self.assertEqual(extract_expected_close(text),
                             ("in the fourth quarter of 2026", "2026-12-31"))

    def test_follow_up_fills_only_empty_groups(self):
        from data.ma_announcements import _fill_missing_terms
        dst = {"termination_fee_usd": None, "expected_close_phrase": "in 2026",
               "expected_close_date": None, "exchange_ratio": None,
               "deck_p_tbv": 1.23, "deck_p_e_ltm": None, "deck_core_dep_premium": None}
        src = {"termination_fee_usd": 2_180_000, "expected_close_phrase": "Q4",
               "expected_close_date": "2026-12-31", "exchange_ratio": 9.9,
               "deck_p_tbv": 2.0, "deck_p_e_ltm": None, "deck_core_dep_premium": None}
        _fill_missing_terms(dst, src)
        self.assertEqual(dst["termination_fee_usd"], 2_180_000)
        # the close group was not empty: phrase and date never mix filings
        self.assertEqual((dst["expected_close_phrase"], dst["expected_close_date"]),
                         ("in 2026", None))
        self.assertIsNone(dst["exchange_ratio"])          # pricing never merges
        self.assertEqual(dst["deck_p_tbv"], 1.23)

    def test_unattributed_follow_up_8k_supplies_the_fee(self):
        from data import ma_announcements as ma
        pr = ("Isabella Bank Corporation (OTCQX: ISBA) and Grand River Commerce, Inc. (OTCQX: "
              "GNRV) today announced the signing of a definitive merger agreement pursuant to "
              "which Isabella will acquire Grand River. " + ISBA_PR)
        hits = [
            {"_id": "0001-26-138:isba_pr_ex991.htm",
             "_source": {"adsh": "0001-26-138", "file_date": "2026-06-12", "ciks": ["0000842517"],
                         "file_type": "EX-99.1", "items": ["7.01", "9.01"],
                         "display_names": ["ISABELLA BANK CORP (ISBA) (CIK 0000842517)"]}},
            {"_id": "0001-26-158:isba_voting_ex991.htm",
             "_source": {"adsh": "0001-26-158", "file_date": "2026-06-15", "ciks": ["0000842517"],
                         "file_type": "EX-99.1", "items": ["1.01", "9.01"],
                         "display_names": ["ISABELLA BANK CORP (ISBA) (CIK 0000842517)"]}}]
        texts = {"0001-26-138": pr,
                 "0001-26-158": ("Isabella Bank Corporation entered into an Agreement and Plan "
                                 "of Merger. " + ISBA_101)}
        resp = MagicMock(); resp.json.return_value = {"hits": {"hits": hits}}
        resp.raise_for_status = MagicMock()
        with patch("data.ma_announcements.requests.get", return_value=resp),              patch("data.ma_announcements._accession_text",
                   side_effect=lambda c, a, d: (texts[a], True)),              patch("data.ma_announcements.compute_stock_value", return_value=(None, True)),              patch("data.ma_announcements._close_before", return_value=(None, None, True)),              patch("data.ma_announcements.time.sleep", lambda *_: None):
            rows, ok = ma.find_open_announcements(842517, "Isabella Bank")
        self.assertTrue(ok)
        self.assertEqual([r["counterparty_name"] for r in rows], ["Grand River Commerce, Inc"])
        t = rows[0]["terms"]
        self.assertEqual((t["termination_fee_usd"], t["expected_close_date"]),
                         (2_180_000, "2026-12-31"))


if __name__ == "__main__":
    unittest.main()
