"""
Form 4 fetch window vs the Insiders tab's 1Y window.

fetch_insider_history kept filings filed in the last 30 * months_back days —
360 for the default 12 — while ui/insider_activity aggregates a 1Y window of
365 days (_window_aggregates / _window_truncated / Total Txns (12M)). A trade
in the window's first 5 days whose Form 4 was filed before the fetch cutoff
was silently missing, even with complete_since=None. The cutoff is now
365 * months_back // 12 days (91/182/365 = the UI's 3M/6M/1Y), and a trade
is never filed before it happens, so the fetch covers the whole window.

Fixtures:
  * BAC_FEED's oldest four rows (tests/test_form4_own_filing_cap; real BAC
    filings): 0000070858-25-000399 filed 2025-10-24 OWN, -000397 filed
    2025-10-17 OWN, -000391 2025-10-15 FGN, -000384 2025-10-14 FGN. Only
    the tail is served so the 30-filing cap cannot bind — the window
    boundary alone decides what is read.
  * BRONSTEIN_XML — real 0000070858-25-000399 (issuer BAC; Sheri B.
    Bronstein, Chief People Officer, G of 6,000 sh on 2025-10-22, filed
    2025-10-24). Trimmed like the sibling fixtures (address, footnote text,
    signature dropped; element structure and values verbatim).

Dates are hand-computed: the 12 months before any date in 2026-03..2027-02
contain no Feb 29, so "minus 365 days" is the same calendar date in 2025.
"""

import unittest
from datetime import date, datetime
from unittest.mock import patch

import data.form4_client as f4
from tests.test_form4_own_filing_cap import (
    BAC_CIK, BAC_FEED, FOREIGN_XML, OWN, OWN_XML, _Resp)

# ui.insider_activity is imported inside the tests, never at module level: a
# top-level import binds it to whichever streamlit stub is installed at
# collection time, and a later wholesale stub swap leaves it on the old one
# (test_export_sites_ownership's render tests then lose st.column_config).

BRONSTEIN_ACC = "0000070858-25-000399"

BRONSTEIN_XML = """<?xml version="1.0"?>
<ownershipDocument>
    <schemaVersion>X0508</schemaVersion>
    <documentType>4</documentType>
    <periodOfReport>2025-10-22</periodOfReport>
    <issuer>
        <issuerCik>0000070858</issuerCik>
        <issuerName>BANK OF AMERICA CORP /DE/</issuerName>
        <issuerTradingSymbol>BAC</issuerTradingSymbol>
    </issuer>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>0001766714</rptOwnerCik>
            <rptOwnerName>Bronstein Sheri B.</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isOfficer>1</isOfficer>
            <officerTitle>Chief People Officer</officerTitle>
        </reportingOwnerRelationship>
    </reportingOwner>
    <aff10b5One>0</aff10b5One>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <securityTitle>
                <value>Common Stock</value>
            </securityTitle>
            <transactionDate>
                <value>2025-10-22</value>
            </transactionDate>
            <transactionCoding>
                <transactionFormType>4</transactionFormType>
                <transactionCode>G</transactionCode>
                <equitySwapInvolved>0</equitySwapInvolved>
                <footnoteId id="F1"/>
            </transactionCoding>
            <transactionTimeliness></transactionTimeliness>
            <transactionAmounts>
                <transactionShares>
                    <value>6000</value>
                </transactionShares>
                <transactionPricePerShare>
                    <value>0</value>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>D</value>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction>
                    <value>324622</value>
                </sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
            <ownershipNature>
                <directOrIndirectOwnership>
                    <value>D</value>
                </directOrIndirectOwnership>
            </ownershipNature>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>
"""

TAIL = BAC_FEED[-4:]


def _frozen(y, m, d):
    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(y, m, d, 12, 0)
    return _Now


class TestOneYearWindowCoverage(unittest.TestCase):

    def setUp(self):
        clear = getattr(f4.fetch_insider_history, "clear", None)
        if clear:
            clear()
        self.assertEqual([(a, d) for a, d, _ in TAIL], [
            (BRONSTEIN_ACC, "2025-10-24"),
            ("0000070858-25-000397", "2025-10-17"),
            ("0000070858-25-000391", "2025-10-15"),
            ("0000070858-25-000384", "2025-10-14")])
        self.saved, self.fetched = {}, []
        submissions = {"filings": {"recent": {
            "form": ["4"] * len(TAIL),
            "accessionNumber": [a for a, _, _ in TAIL],
            "filingDate": [d for _, d, _ in TAIL]}}}
        xml_for = {a: (OWN_XML if c is OWN else FOREIGN_XML)
                   for a, _, c in TAIL}
        xml_for[BRONSTEIN_ACC] = BRONSTEIN_XML

        def _fetch(acc, cik, primary_doc=None):
            self.fetched.append(acc)
            return xml_for[acc]

        for target, name, fn in (
                (f4.requests, "get", lambda url, *a, **k: _Resp(submissions)),
                (f4, "_fetch_form4_xml", _fetch),
                (f4, "load_json", lambda prefix, name: None),
                (f4, "save_json",
                 lambda prefix, name, obj: self.saved.update({name: obj}))):
            p = patch.object(target, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def _walk(self, y, m, d, **kw):
        with patch.object(f4, "datetime", _frozen(y, m, d)):
            return f4.fetch_insider_history(BAC_CIK, force=True, **kw)

    def test_trade_on_the_1y_windows_first_day_is_fetched(self):
        # Today 2026-10-22: the 1Y window starts 2025-10-22, Bronstein's trade
        # date. Old cutoff 2026-10-22 - 360d = 2025-10-27 dropped her
        # 2025-10-24 filing; the new cutoff is 2025-10-22. The 10-17 filing
        # is before it (and its trades are dated 2025-10-15, outside the
        # window), so exactly one filing is read.
        from ui.insider_activity import _window_count, _window_truncated
        hist = self._walk(2026, 10, 22)
        today = date(2026, 10, 22)

        self.assertEqual(self.fetched, [BRONSTEIN_ACC])
        self.assertEqual(self.saved[f"{BAC_CIK}.json"]["cutoff"], "2025-10-22")
        self.assertIsNone(hist["complete_since"])
        self.assertEqual([(t["date"], t["code"], t["insider"])
                          for t in hist["transactions"]],
                         [("2025-10-22", "G", "Bronstein Sheri B.")])
        self.assertFalse(_window_truncated(365, hist["complete_since"], today))
        self.assertEqual(_window_count(hist["transactions"], 365, today), 1)

    def test_filed_in_window_trade_dated_before_it_is_not_counted(self):
        # Today 2026-10-23: cutoff 2025-10-23 still reads the 10-24 filing,
        # but its trade (2025-10-22) is a day before the 1Y window: Total
        # Txns (12M) counts trades dated in the window, not fetched rows.
        from ui.insider_activity import _window_count
        hist = self._walk(2026, 10, 23)
        self.assertEqual(self.fetched, [BRONSTEIN_ACC])
        self.assertEqual(len(hist["transactions"]), 1)
        self.assertEqual(
            _window_count(hist["transactions"], 365, date(2026, 10, 23)), 0)

    def test_filing_before_the_cutoff_is_not_read(self):
        # Today 2026-10-25: cutoff 2025-10-25 > 2025-10-24.
        hist = self._walk(2026, 10, 25)
        self.assertEqual(self.fetched, [])
        self.assertEqual(hist["transactions"], [])

    def test_cutoff_matches_the_ui_windows(self):
        # From 2026-10-06: 3M = 91d → 2026-07-07, 6M = 182d → 2026-04-07,
        # 1Y = 365d → 2025-10-06 (render_insider_activity's 91/182/365).
        for months, cutoff in ((3, "2026-07-07"), (6, "2026-04-07"),
                               (12, "2025-10-06")):
            with self.subTest(months_back=months):
                self._walk(2026, 10, 6, months_back=months)
                self.assertEqual(self.saved[f"{BAC_CIK}.json"]["cutoff"],
                                 cutoff)


class TestCachedCutoff(unittest.TestCase):
    """A cached object is served only if its walk reached back to the
    requested cutoff — objects from the 360-day walk carry no `cutoff`."""

    OBJ = {"cached_at": "2026-10-22T04:30:00",
           "transactions": [{"date": "2026-09-15", "insider": "MOYNIHAN BRIAN T",
                             "code": "D", "form_type": "non-derivative",
                             "accession": "0000070858-26-000469"}],
           "complete_since": None}

    def setUp(self):
        clear = getattr(f4.fetch_insider_history, "clear", None)
        if clear:
            clear()

    def _read(self, obj):
        clear = getattr(f4.fetch_insider_history, "clear", None)
        if clear:
            clear()
        refetched = []

        def _get(url, *a, **k):
            refetched.append(url)
            return _Resp({"filings": {"recent": {}}})

        with patch.object(f4, "datetime", _frozen(2026, 10, 22)), \
             patch.object(f4, "load_json", lambda prefix, name: obj), \
             patch.object(f4.requests, "get", _get):
            hist = f4.fetch_insider_history(BAC_CIK)
        return hist, bool(refetched)

    def test_cutoff_reaching_the_window_is_served(self):
        # Written 2026-10-22 04:30 by the 365-day walk: cutoff 2025-10-22.
        for cutoff in ("2025-10-22", "2025-10-20"):
            with self.subTest(cutoff=cutoff):
                hist, refetched = self._read({**self.OBJ, "cutoff": cutoff})
                self.assertFalse(refetched)
                self.assertEqual(len(hist["transactions"]), 1)

    def test_short_or_missing_cutoff_is_refetched(self):
        # No field = the 360-day walk (its cutoff would be 2025-10-27);
        # 2025-10-23 = a walk that stops a day inside the 1Y window.
        for obj in (self.OBJ, {**self.OBJ, "cutoff": "2025-10-23"}):
            with self.subTest(cutoff=obj.get("cutoff")):
                _, refetched = self._read(obj)
                self.assertTrue(refetched)


if __name__ == "__main__":
    unittest.main()
