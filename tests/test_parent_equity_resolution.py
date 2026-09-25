"""Parent equity must be read AT the latest balance-sheet date across BOTH
equity tags (2026-09-25 AOCI coverage study).

Filers that moved the equity total from us-gaap:StockholdersEquity to
StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest kept
resolving to their LAST plain-SE balance (3-year tolerance), a stale figure
behind a fresh-looking sec_as_of. Fixtures are real companyfacts entries;
expected values are hand-verified against each filing's balance sheet (R2.htm):

  OCFC 10-Q 0001004702-26-000120, Jun 30 2026: "Total stockholders' equity"
       2,411,080 ($K), no NCI line (NCI $890K at 9/30/25, $0 at 12/31/25).
       Served before the fix: 1,662,550,000 (plain SE @ 2025-12-31).
  TMP  10-Q 0001005817-26-000112, Jun 30 2026: "Total Equity" 959,932 ($K),
       no NCI line. Served before: 713,444,000 (plain SE @ 2024-12-31).
  RBB  10-Q 0001437749-26-026563, Jun 30 2026: "Total shareholders' equity"
       535,177 ($K) INCLUDING "Non-controlling interest" 72 ($K) →
       parent = 535,177 − 72 = 535,105 ($K). Served before: 523,400,000.
  FRST 10-Q 0001104659-26-092672, Jun 30 2026: "Total Primis stockholders'
       equity" 433,829 ($K); NCI (Panacea) deconsolidated in 2025.
       Served before: 422,896,000 (plain SE @ 2025-12-31).
"""
import copy
import unittest
from unittest.mock import patch

import data.sec_client as sc

SE = "StockholdersEquity"
NCI = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
MI = "MinorityInterest"
NI_NCI = "NetIncomeLossAttributableToNoncontrollingInterest"


def _e(end, val, accn, form, filed, start=None):
    d = {"end": end, "val": val, "accn": accn, "form": form, "filed": filed}
    if start:
        d["start"] = start
    return d


def _facts(concepts: dict) -> dict:
    return {"cik": 1, "entityName": "T", "facts": {
        "us-gaap": {c: {"units": {"USD": rows}} for c, rows in concepts.items()},
        "dei": {}}}


OCFC = _facts({
    SE: [_e("2025-09-30", 1652537000, "0001004702-25-000129", "10-Q", "2025-11-04"),
         _e("2025-12-31", 1662550000, "0001004702-26-000015", "10-K", "2026-02-27")],
    NCI: [_e("2025-09-30", 1653427000, "0001004702-25-000129", "10-Q", "2025-11-04"),
          _e("2025-12-31", 1662550000, "0001004702-26-000120", "10-Q", "2026-08-07"),
          _e("2026-03-31", 1669368000, "0001004702-26-000120", "10-Q", "2026-08-07"),
          _e("2026-06-30", 2411080000, "0001004702-26-000120", "10-Q", "2026-08-07")],
    MI: [_e("2025-09-30", 890000, "0001004702-25-000129", "10-Q", "2025-11-04"),
         _e("2025-12-31", 0, "0001004702-26-000015", "10-K", "2026-02-27")],
    NI_NCI: [_e("2025-12-31", 49000, "0001004702-26-000015", "10-K", "2026-02-27", "2025-01-01"),
             _e("2026-06-30", 0, "0001004702-26-000120", "10-Q", "2026-08-07", "2026-01-01"),
             _e("2026-06-30", 0, "0001004702-26-000120", "10-Q", "2026-08-07", "2026-04-01")],
    "Assets": [_e("2025-12-31", 14564317000, "0001004702-26-000120", "10-Q", "2026-08-07"),
               _e("2026-03-31", 14556336000, "0001004702-26-000052", "10-Q", "2026-05-01"),
               _e("2026-06-30", 23270010000, "0001004702-26-000120", "10-Q", "2026-08-07")],
})

TMP = _facts({
    SE: [_e("2024-09-30", 719855000, "0001005817-24-000027", "10-Q", "2024-11-06"),
         _e("2024-12-31", 713444000, "0001005817-25-000006", "10-K", "2025-02-28")],
    NCI: [_e("2025-09-30", 788805000, "0001005817-25-000040", "10-Q", "2025-10-28"),
          _e("2025-12-31", 938377000, "0001005817-26-000112", "10-Q", "2026-07-31"),
          _e("2026-03-31", 946741000, "0001005817-26-000112", "10-Q", "2026-07-31"),
          _e("2026-06-30", 959932000, "0001005817-26-000112", "10-Q", "2026-07-31")],
    MI: [_e("2024-09-30", 1493000, "0001005817-24-000027", "10-Q", "2024-11-06"),
         _e("2024-12-31", 0, "0001005817-25-000006", "10-K", "2025-02-28")],
    NI_NCI: [_e("2025-12-31", 0, "0001005817-26-000027", "10-K", "2026-02-26", "2025-01-01")],
    "Assets": [_e("2026-03-31", 8695761000, "0001005817-26-000049", "10-Q", "2026-05-05"),
               _e("2026-06-30", 8801522000, "0001005817-26-000112", "10-Q", "2026-07-31")],
})

RBB = _facts({
    SE: [_e("2025-12-31", 523400000, "0001437749-26-007387", "10-K", "2026-03-09")],
    NCI: [_e("2025-12-31", 523410000, "0001437749-26-026563", "10-Q", "2026-08-07"),
          _e("2026-03-31", 531054000, "0001437749-26-026563", "10-Q", "2026-08-07"),
          _e("2026-06-30", 535177000, "0001437749-26-026563", "10-Q", "2026-08-07")],
    MI: [_e("2025-12-31", 72000, "0001437749-26-026563", "10-Q", "2026-08-07"),
         _e("2026-03-31", 72000, "0001437749-26-015865", "10-Q", "2026-05-08"),
         _e("2026-06-30", 72000, "0001437749-26-026563", "10-Q", "2026-08-07")],
    "Assets": [_e("2026-06-30", 4275002000, "0001437749-26-026563", "10-Q", "2026-08-07")],
})

FRST = _facts({
    SE: [_e("2025-09-30", 382153000, "0001104659-25-109181", "10-Q", "2025-11-10"),
         _e("2025-12-31", 422896000, "0001104659-26-028599", "10-K", "2026-03-16")],
    NCI: [_e("2025-12-31", 422896000, "0001104659-26-092672", "10-Q", "2026-08-07"),
          _e("2026-03-31", 427198000, "0001104659-26-092672", "10-Q", "2026-08-07"),
          _e("2026-06-30", 433829000, "0001104659-26-092672", "10-Q", "2026-08-07")],
    MI: [_e("2025-09-30", 0, "0001104659-25-109181", "10-Q", "2025-11-10")],
    NI_NCI: [_e("2025-12-31", -3602000, "0001104659-26-028599", "10-K", "2026-03-16", "2025-01-01")],
    "Assets": [_e("2026-06-30", 4353614000, "0001104659-26-092672", "10-Q", "2026-08-07")],
})


# LNKB 10-K 0001193125-26-104211, Dec 31 2025: "TOTAL SHAREHOLDERS' EQUITY"
# 306,432 ($K), no NCI line; its $483K NCI was last tagged at 2023-12-31.
LNKB = _facts({
    SE: [_e("2024-12-31", 280221000, "0000950170-25-047247", "10-K", "2025-03-31")],
    NCI: [_e("2025-09-30", 305457000, "0001193125-25-272457", "10-Q", "2025-11-07"),
          _e("2025-12-31", 306432000, "0001193125-26-104211", "10-K", "2026-03-12")],
    MI: [_e("2023-12-31", 483000, "0000950170-25-047247", "10-K", "2025-03-31")],
})

# FUSB 10-Q 0001193125-26-337583, Jun 30 2026: "Total shareholders' equity"
# 104,285 ($K), no NCI line; NCI (−$11K) last tagged at 2018-12-31.
FUSB = _facts({
    NCI: [_e("2026-03-31", 104634000, "0001193125-26-337583", "10-Q", "2026-08-06"),
          _e("2026-06-30", 104285000, "0001193125-26-337583", "10-Q", "2026-08-06")],
    MI: [_e("2018-12-31", -11000, "0001564590-20-011613", "10-K", "2020-03-18")],
})


def _drop(facts, concept, end):
    f = copy.deepcopy(facts)
    rows = f["facts"]["us-gaap"][concept]["units"]["USD"]
    rows[:] = [r for r in rows if r["end"] != end]
    return f


class TestResolveParentEquity(unittest.TestCase):

    def _val(self, facts):
        tup, concept, _ = sc._resolve_parent_equity(facts)
        return (tup[0] if tup else None), (tup[1] if tup else None), concept

    def test_ocfc_nci_tag_at_balance_sheet_date(self):
        self.assertEqual(self._val(OCFC), (2_411_080_000, "2026-06-30", NCI))

    def test_tmp_stale_plain_se_not_served(self):
        self.assertEqual(self._val(TMP), (959_932_000, "2026-06-30", NCI))

    def test_rbb_same_date_nci_removed(self):
        val, end, concept = self._val(RBB)
        self.assertEqual(val, 535_177_000 - 72_000)
        self.assertEqual(val, 535_105_000)
        self.assertEqual(end, "2026-06-30")
        self.assertEqual(concept, f"{NCI} − MinorityInterest")

    def test_frst_deconsolidated_nci_history_is_not_evidence(self):
        # FY2025 NCI loss (−$3.6M) ends 2025-12-31, not at the 2026-06-30
        # balance-sheet date; the newest MinorityInterest balance is $0.
        self.assertEqual(self._val(FRST), (433_829_000, "2026-06-30", NCI))

    def test_plain_se_wins_when_both_at_date(self):
        # OCFC at 2025-09-30: SE 1,652,537 (parent) vs incl-NCI 1,653,427.
        f = copy.deepcopy(OCFC)
        for rows in (r["units"]["USD"] for r in f["facts"]["us-gaap"].values()):
            rows[:] = [r for r in rows if r["end"] <= "2025-09-30"]
        self.assertEqual(self._val(f), (1_652_537_000, "2025-09-30", SE))

    def test_nci_present_but_unseparated_is_na(self):
        # RBB without the 2026-06-30 MinorityInterest fact: the newest NCI
        # balance ($72K at 2026-03-31) says NCI exists → n/a, not a guess.
        self.assertIsNone(self._val(_drop(RBB, MI, "2026-06-30"))[0])

    def test_nci_balance_older_than_a_year_is_not_evidence(self):
        self.assertEqual(self._val(LNKB), (306_432_000, "2025-12-31", NCI))
        self.assertEqual(self._val(FUSB), (104_285_000, "2026-06-30", NCI))

    def test_nci_income_at_date_is_evidence(self):
        f = copy.deepcopy(TMP)
        f["facts"]["us-gaap"][NI_NCI]["units"]["USD"].append(
            _e("2026-06-30", 31000, "x", "10-Q", "2026-07-31", "2026-04-01"))
        self.assertIsNone(self._val(f)[0])

    def test_no_equity_at_balance_sheet_date_is_na(self):
        # Assets filed at 2026-06-30 but no equity total there: never fall
        # back to the prior quarter's 946,741,000.
        self.assertIsNone(self._val(_drop(TMP, NCI, "2026-06-30"))[0])


class TestFundamentalsUseResolver(unittest.TestCase):

    def _run(self, facts):
        with patch.object(sc, "fetch_company_facts", return_value=facts):
            return (sc.get_latest_fundamentals.__wrapped__(1)
                    if hasattr(sc.get_latest_fundamentals, "__wrapped__")
                    else sc.get_latest_fundamentals(1)), \
                sc.get_fundamentals_with_provenance(1)

    def test_display_and_trace_serve_current_equity(self):
        for facts, want in ((OCFC, 2_411_080_000), (TMP, 959_932_000),
                            (RBB, 535_105_000), (FRST, 433_829_000)):
            disp, prov = self._run(facts)
            self.assertEqual(disp["book_value_total"], want)
            self.assertEqual(prov["book_value_total"]["value"], want)
            self.assertEqual(prov["book_value_total"]["source"].as_of, "2026-06-30")

    def test_trace_shows_na_reason(self):
        _, prov = self._run(_drop(RBB, MI, "2026-06-30"))
        self.assertIsNone(prov["book_value_total"]["value"])
        self.assertIn("noncontrolling", prov["book_value_total"]["source"].notes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
