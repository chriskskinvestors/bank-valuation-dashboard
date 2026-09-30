"""
Financial Highlights per-share rows (Book value / share, Tangible BV / share —
also Performance Analysis per-share rows and the P/TBV history chart) read
parent COMMON equity per period, mirroring the audited snapshot path.

Three defects in ui/financial_highlights._per_share_for_ends (2026-09-30):
  1. Plain us-gaap:StockholdersEquity only. Filers that migrated the equity
     total to StockholdersEquityIncludingPortionAttributableToNoncontrolling-
     Interest (TMP/AMAL/LNKB/FNWB in 2025, FRST/MVBF/OCFC/RBB in 2026) rendered
     n/a for every period after the switch. Now: plain SE at the date, else
     the NCI-inclusive total less same-date MinorityInterest, else n/a when a
     noncontrolling interest may exist (sec_client._parent_equity_at).
  2. Preferred never subtracted: "Total common equity" was total equity, so
     every preferred issuer's BVPS/TBVPS was overstated (FBIZ $47.24 vs
     $45.81; KEY $18.55 vs $16.21; BAFN $28.22 vs ~$4.82). Now preferred is
     resolved as the filer stood at the balance-sheet date, and preferred
     present but unresolved → n/a (cardinal rule).
  3. TBVPS fell back to BVPS whenever no intangibles tag joined at the date —
     dropping the deduction for a bank that has goodwill. Now n/a when a
     recent balance sheet carried intangibles; BVPS only for banks with none.

Fixtures are real companyfacts entries; expected values are hand-verified
against each filing's balance sheet (R2.htm) and computed by hand:

  TMP  10-Q 0001005817-26-000049, Mar 31 2026: "Total Equity" 946,741 ($K)
       (tagged IncludingNCI; no NCI line); Goodwill 72,736; other intangibles
       1,833 (segment note, R55); cover shares 14,382,941 (R1, Apr 30 2026).
       BVPS  = 946,741,000 / 14,382,941               = 65.8238812215
       TBVPS = (946,741,000 − 72,736,000 − 1,833,000)
               / 14,382,941 = 872,172,000 / 14,382,941 = 60.6393365585
       Served before the fix: n/a (no plain-SE fact after 2024-12-31).
  RBB  10-Q 0001437749-26-015865, Mar 31 2026: "Total shareholders' equity"
       531,054,000 INCLUDING "Non-controlling interest" 72,000; Goodwill
       71,498,000; 17,074,159 shares outstanding; no preferred.
       parent = 531,054,000 − 72,000 = 530,982,000
       BVPS  = 530,982,000 / 17,074,159               = 31.0985741670
       TBVPS = 459,484,000 / 17,074,159               = 26.9110765573
  FBIZ 10-Q 0001193125-26-328468, Jun 30 2026: "Total stockholders' equity"
       395,307 ($K); preferred (12,500 sh Series A) 11,992; goodwill and
       other intangibles 11,933; 8,368,320 common shares outstanding.
       common = 395,307,000 − 11,992,000 = 383,315,000
       BVPS  = 383,315,000 / 8,368,320                = 45.8054902298
       TBVPS = 371,382,000 / 8,368,320                = 44.3795170357
       Served before the fix: 395,307,000 / 8,368,320 = 47.2385138236.
"""
from __future__ import annotations

import copy
import sys
import unittest
import warnings
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
warnings.filterwarnings("ignore")

# Order-independent streamlit stub (shared helper).
from tests import _streamlit_stub

_st = _streamlit_stub.install()
# Extras beyond the helper baseline (additive — never clobber a richer stub).
if not hasattr(_st, "query_params"):
    _st.query_params = {}
for _name in ("markdown", "caption", "write", "info", "warning", "error",
              "divider", "metric", "dataframe", "button", "html", "subheader"):
    if not hasattr(_st, _name):
        setattr(_st, _name, lambda *a, **k: None)

import data.sec_client as sc  # noqa: E402
import ui.financial_highlights as fh  # noqa: E402

SE = "StockholdersEquity"
NCI = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
MI = "MinorityInterest"
NI_NCI = "NetIncomeLossAttributableToNoncontrollingInterest"
GW = "Goodwill"
EXCL = "IntangibleAssetsNetExcludingGoodwill"
INCL = "IntangibleAssetsNetIncludingGoodwill"


def _e(end, val, accn, form, filed, start=None):
    d = {"end": end, "val": val, "accn": accn, "form": form, "filed": filed}
    if start:
        d["start"] = start
    return d


def _facts(usd: dict, shares: dict | None = None, dei: list | None = None) -> dict:
    ug = {c: {"units": {"USD": rows}} for c, rows in usd.items()}
    for c, rows in (shares or {}).items():
        ug[c] = {"units": {"shares": rows}}
    return {"cik": 1, "entityName": "T", "facts": {
        "us-gaap": ug,
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": dei or []}}}}}


def _drop(facts, concept, end=None):
    """Copy of facts without `concept` rows at `end` (all rows if end is None)."""
    f = copy.deepcopy(facts)
    for rows in f["facts"]["us-gaap"][concept]["units"].values():
        rows[:] = [r for r in rows if end is not None and r["end"] != end]
    return f


def _add(facts, concept, row):
    f = copy.deepcopy(facts)
    f["facts"]["us-gaap"].setdefault(concept, {"units": {"USD": []}})["units"]["USD"].append(row)
    return f


TMP_Q1 = "0001005817-26-000049"
TMP = _facts({
    SE: [_e("2024-12-31", 713444000, "0001005817-25-000006", "10-K", "2025-02-28")],
    NCI: [_e("2024-12-31", 713444000, "0001005817-25-000006", "10-K", "2025-02-28"),
          _e("2025-09-30", 788805000, "0001005817-25-000040", "10-Q", "2025-10-28"),
          _e("2025-12-31", 938377000, "0001005817-26-000027", "10-K", "2026-02-26"),
          _e("2026-03-31", 946741000, TMP_Q1, "10-Q", "2026-05-05")],
    MI: [_e("2024-12-31", 0, "0001005817-25-000006", "10-K", "2025-02-28")],
    NI_NCI: [_e("2025-12-31", 0, "0001005817-26-000027", "10-K", "2026-02-26", "2025-01-01")],
    GW: [_e("2025-09-30", 92602000, "0001005817-25-000040", "10-Q", "2025-10-28"),
         _e("2025-12-31", 72736000, "0001005817-26-000027", "10-K", "2026-02-26"),
         _e("2026-03-31", 72736000, TMP_Q1, "10-Q", "2026-05-05")],
    EXCL: [_e("2025-09-30", 2325000, "0001005817-25-000040", "10-Q", "2025-10-28"),
           _e("2025-12-31", 1687000, "0001005817-26-000027", "10-K", "2026-02-26"),
           _e("2026-03-31", 1833000, TMP_Q1, "10-Q", "2026-05-05")],
}, dei=[_e("2026-02-20", 14418025, "0001005817-26-000027", "10-K", "2026-02-26"),
        _e("2026-04-30", 14382941, TMP_Q1, "10-Q", "2026-05-05"),
        _e("2026-07-24", 14382905, "0001005817-26-000112", "10-Q", "2026-07-31")])

RBB_Q1 = "0001437749-26-015865"
RBB = _facts({
    NCI: [_e("2025-12-31", 523410000, "0001437749-26-007387", "10-K", "2026-03-09"),
          _e("2026-03-31", 531054000, RBB_Q1, "10-Q", "2026-05-08")],
    MI: [_e("2025-12-31", 72000, "0001437749-26-007387", "10-K", "2026-03-09"),
         _e("2026-03-31", 72000, RBB_Q1, "10-Q", "2026-05-08")],
    GW: [_e("2025-12-31", 71498000, "0001437749-26-007387", "10-K", "2026-03-09"),
         _e("2026-03-31", 71498000, RBB_Q1, "10-Q", "2026-05-08")],
    "PreferredStockValue": [_e("2026-03-31", 0, RBB_Q1, "10-Q", "2026-05-08")],
}, shares={
    "CommonStockSharesOutstanding": [_e("2026-03-31", 17074159, RBB_Q1, "10-Q", "2026-05-08")],
    "PreferredStockSharesOutstanding": [_e("2026-03-31", 0, RBB_Q1, "10-Q", "2026-05-08")],
}, dei=[_e("2026-05-04", 16935888, RBB_Q1, "10-Q", "2026-05-08")])

FBIZ_Q2 = "0001193125-26-328468"
FBIZ = _facts({
    SE: [_e("2025-12-31", 371585000, "0001193125-26-071523", "10-K", "2026-02-25"),
         _e("2026-06-30", 395307000, FBIZ_Q2, "10-Q", "2026-07-31")],
    INCL: [_e("2025-12-31", 11985000, "0001193125-26-071523", "10-K", "2026-02-25"),
           _e("2026-06-30", 11933000, FBIZ_Q2, "10-Q", "2026-07-31")],
    "PreferredStockValue": [
        _e("2025-12-31", 11992000, "0001193125-26-071523", "10-K", "2026-02-25"),
        _e("2026-06-30", 11992000, FBIZ_Q2, "10-Q", "2026-07-31")],
    "PreferredStockDividendsIncomeStatementImpact": [
        _e("2026-06-30", 219000, FBIZ_Q2, "10-Q", "2026-07-31", "2026-04-01")],
}, shares={
    "CommonStockSharesOutstanding": [_e("2026-06-30", 8368320, FBIZ_Q2, "10-Q", "2026-07-31")],
    "PreferredStockSharesOutstanding": [_e("2026-06-30", 12500, FBIZ_Q2, "10-Q", "2026-07-31")],
}, dei=[_e("2026-07-27", 8368320, FBIZ_Q2, "10-Q", "2026-07-31")])


def _row(facts, end: datetime) -> dict:
    with patch.object(fh.sec_client, "fetch_company_facts", return_value=facts):
        return fh._per_share_for_ends("0000000001", [end], quarterly=True)[end]


class TestIncludingNciTagMigrator(unittest.TestCase):
    """TMP: equity total tagged only IncludingNCI after 2024 (no NCI line)."""
    END = datetime(2026, 3, 31)

    def test_resolves_from_including_nci_tag(self):
        r = _row(TMP, self.END)
        self.assertEqual(r["_eq"], 946_741_000)
        self.assertEqual(r["_eq_concept"], NCI)
        self.assertEqual(r["_ce"], 946_741_000)                # no preferred
        self.assertEqual(r["_adj"], 72_736_000 + 1_833_000)
        self.assertAlmostEqual(r["bvps"], 65.8238812215, places=8)
        self.assertAlmostEqual(r["tbvps"], 60.6393365585, places=8)
        self.assertEqual(r["_eq_prov"]["accn"], TMP_Q1)       # source link

    def test_plain_se_preferred_where_both_tagged(self):
        r = _row(TMP, datetime(2024, 12, 31))
        self.assertEqual(r["_eq_concept"], SE)

    def test_nci_evidence_without_separation_is_na(self):
        # NCI income in the period and no MinorityInterest at the date → the
        # NCI-inclusive total is not parent equity: n/a, never the total.
        f = _add(TMP, NI_NCI, _e("2026-03-31", 50000, TMP_Q1, "10-Q", "2026-05-05",
                                 "2026-01-01"))
        r = _row(f, self.END)
        self.assertIsNone(r["_eq"])
        self.assertIsNone(r["bvps"])
        self.assertIsNone(r["tbvps"])

    def test_intangibles_untagged_at_date_is_na_not_bvps(self):
        # Goodwill on the 2025-12-31 balance sheet, nothing tagged at 3/31:
        # the old code served TBVPS == BVPS (deduction dropped).
        f = _drop(_drop(TMP, GW, "2026-03-31"), EXCL, "2026-03-31")
        r = _row(f, self.END)
        self.assertAlmostEqual(r["bvps"], 65.8238812215, places=8)
        self.assertIsNone(r["tbvps"])

    def test_other_intangibles_only_while_goodwill_recent_is_na(self):
        # Other intangibles tagged at 3/31 but goodwill only at 12/31: using
        # the 1,833 alone would drop 72,736 of goodwill from the deduction.
        r = _row(_drop(TMP, GW, "2026-03-31"), self.END)
        self.assertIsNone(r["tbvps"])

    def test_no_intangibles_ever_tbvps_equals_bvps(self):
        r = _row(_drop(_drop(TMP, GW), EXCL), self.END)
        self.assertEqual(r["_adj"], 0.0)
        self.assertAlmostEqual(r["tbvps"], r["bvps"], places=10)


class TestMinorityInterestRemoved(unittest.TestCase):
    """RBB: NCI-inclusive total carries a $72K noncontrolling interest."""
    END = datetime(2026, 3, 31)

    def test_same_date_minority_interest_subtracted(self):
        r = _row(RBB, self.END)
        self.assertEqual(r["_eq"], 530_982_000)
        self.assertEqual(r["_eq_concept"], f"{NCI} − MinorityInterest")
        self.assertEqual(r["shares"], 17_074_159)
        self.assertAlmostEqual(r["bvps"], 31.0985741670, places=8)
        self.assertAlmostEqual(r["tbvps"], 26.9110765573, places=8)

    def test_unseparated_nci_is_na(self):
        # No MinorityInterest at 3/31, but $72K on the 12/31 balance sheet:
        # the NCI may still be inside the total → n/a.
        r = _row(_drop(RBB, MI, "2026-03-31"), self.END)
        self.assertIsNone(r["bvps"])
        self.assertIsNone(r["tbvps"])


class TestPreferredSubtracted(unittest.TestCase):
    """FBIZ: $11,992K Series A preferred must leave per-COMMON-share book."""
    END = datetime(2026, 6, 30)

    def test_book_value_is_per_common_share(self):
        r = _row(FBIZ, self.END)
        self.assertEqual(r["_pfd"], 11_992_000)
        self.assertEqual(r["_ce"], 383_315_000)
        self.assertAlmostEqual(r["bvps"], 45.8054902298, places=8)
        self.assertAlmostEqual(r["tbvps"], 44.3795170357, places=8)
        self.assertNotAlmostEqual(r["bvps"], 47.2385138236, places=2)  # old: incl. preferred

    def test_preferred_present_value_unresolved_is_na(self):
        # 12,500 preferred shares outstanding but no carrying-value tag → n/a,
        # never a preferred-inflated "common" book value.
        r = _row(_drop(FBIZ, "PreferredStockValue"), self.END)
        self.assertTrue(r["_pfd_present"])
        self.assertIsNone(r["bvps"])
        self.assertIsNone(r["tbvps"])


class TestAsOfAnchors(unittest.TestCase):
    """The per-period readers resolve facts as they stood at the date."""

    def test_extract_latest_value_as_of(self):
        self.assertEqual(sc._extract_latest_value(TMP, GW), 72_736_000)
        self.assertEqual(sc._extract_latest_value(TMP, GW, as_of="2025-09-30"), 92_602_000)

    def test_preferred_as_of_before_first_fact_is_absent(self):
        self.assertEqual(sc._resolve_preferred_stock(FBIZ, as_of="2025-12-31"),
                         (11_992_000, True))
        self.assertEqual(sc._resolve_preferred_stock(FBIZ, as_of="2024-06-30"),
                         (0.0, False))

    def test_parent_equity_at_matches_snapshot_rule(self):
        tup, concept, _ = sc._parent_equity_at(RBB, "2026-03-31")
        self.assertEqual(tup[0], 530_982_000)
        self.assertEqual(concept, f"{NCI} − MinorityInterest")


if __name__ == "__main__":
    unittest.main()
