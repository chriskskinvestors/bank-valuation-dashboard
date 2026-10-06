"""
Form 4 own-filing cap (data/form4_client.fetch_insider_trades).

The nightly sweep keeps a bank's 30 most recent Form 4s. Since PR #257 it
skips filings whose <issuer><issuerCik> is not the bank — but the 30-cap was
applied BEFORE that check, so a bank that files many Form 4s as a REPORTING
OWNER of other issuers (fund 10%-owner stakes) lost its own history: on
2026-10-05 BAC's 30 newest held 19 such filings and its insider view kept 11.
The cap now counts only filings that pass the issuer check, walking
newest-first until 30 own filings, the window cutoff, or _MAX_XML_FETCHES.

Fixtures:
  * BAC_FEED — BAC's (CIK 70858) real 12-month Form 4 list from its EDGAR
    submissions feed (accession, filingDate, newest first), fetched
    2026-10-06; each row's OWN/FGN class is the real issuerCik of that
    filing's XML (all 125 fetched and checked). Non-Form-4 rows (424B2 etc.)
    are dropped. Since 2026-10-05 one FGN filing was added on top, so the
    first 30 now hold 20 FGN (rows 2-31 are the 10-05 snapshot's 19).
  * OWN_XML — real 0000070858-26-000469 (issuer BAC; Brian Moynihan, Chair
    and CEO, M + D of 18,082 sh @ 59.52 on 2026-09-15).
  * FOREIGN_XML — real 0000070858-26-000460 (issuer Neuberger High Yield
    Strategies Fund, CIK 1487610; BAC + Bank of America NA as 10% owners).
  Both trimmed (addresses, holdings, derivative table, footnote text and
  signatures dropped; element structure and values verbatim). Each feed row
  is served the fixture of its real class — the walk only needs the class.
"""

import unittest
from datetime import datetime
from unittest.mock import patch

import data.form4_client as f4

BAC_CIK = 70858
OWN, FGN = True, False

OWN_XML = """<?xml version="1.0"?>
<ownershipDocument>
    <schemaVersion>X0609</schemaVersion>
    <documentType>4</documentType>
    <periodOfReport>2026-09-15</periodOfReport>
    <issuer>
        <issuerCik>0000070858</issuerCik>
        <issuerName>BANK OF AMERICA CORP /DE/</issuerName>
        <issuerTradingSymbol>BAC</issuerTradingSymbol>
        <issuerForeignTradingSymbol></issuerForeignTradingSymbol>
    </issuer>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>0001195071</rptOwnerCik>
            <rptOwnerName>MOYNIHAN BRIAN T</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isDirector>1</isDirector>
            <isOfficer>1</isOfficer>
            <officerTitle>Chair and CEO</officerTitle>
        </reportingOwnerRelationship>
    </reportingOwner>
    <aff10b5One>0</aff10b5One>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <securityTitle>
                <value>Common Stock</value>
            </securityTitle>
            <transactionDate>
                <value>2026-09-15</value>
            </transactionDate>
            <transactionCoding>
                <transactionFormType>4</transactionFormType>
                <transactionCode>M</transactionCode>
                <equitySwapInvolved>0</equitySwapInvolved>
            </transactionCoding>
            <transactionTimeliness></transactionTimeliness>
            <transactionAmounts>
                <transactionShares>
                    <value>18082</value>
                </transactionShares>
                <transactionPricePerShare>
                    <footnoteId id="F1"/>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>A</value>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction>
                    <value>2717694</value>
                </sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
            <ownershipNature>
                <directOrIndirectOwnership>
                    <value>D</value>
                </directOrIndirectOwnership>
            </ownershipNature>
        </nonDerivativeTransaction>
        <nonDerivativeTransaction>
            <securityTitle>
                <value>Common Stock</value>
            </securityTitle>
            <transactionDate>
                <value>2026-09-15</value>
            </transactionDate>
            <transactionCoding>
                <transactionFormType>4</transactionFormType>
                <transactionCode>D</transactionCode>
                <equitySwapInvolved>0</equitySwapInvolved>
            </transactionCoding>
            <transactionTimeliness></transactionTimeliness>
            <transactionAmounts>
                <transactionShares>
                    <value>18082</value>
                </transactionShares>
                <transactionPricePerShare>
                    <value>59.52</value>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>D</value>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction>
                    <value>2699612</value>
                </sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
            <ownershipNature>
                <directOrIndirectOwnership>
                    <value>D</value>
                </directOrIndirectOwnership>
            </ownershipNature>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>"""

FOREIGN_XML = """<?xml version="1.0"?>
<ownershipDocument>
    <schemaVersion>X0609</schemaVersion>
    <documentType>4</documentType>
    <periodOfReport>2026-08-28</periodOfReport>
    <notSubjectToSection16>0</notSubjectToSection16>
    <issuer>
        <issuerCik>0001487610</issuerCik>
        <issuerName>Neuberger High Yield Strategies Fund Inc.</issuerName>
        <issuerTradingSymbol>NHS</issuerTradingSymbol>
    </issuer>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>0000070858</rptOwnerCik>
            <rptOwnerName>BANK OF AMERICA CORP /DE/</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isDirector>0</isDirector>
            <isOfficer>0</isOfficer>
            <isTenPercentOwner>1</isTenPercentOwner>
            <isOther>0</isOther>
        </reportingOwnerRelationship>
    </reportingOwner>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>0001102113</rptOwnerCik>
            <rptOwnerName>BANK OF AMERICA NA</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isDirector>0</isDirector>
            <isOfficer>0</isOfficer>
            <isTenPercentOwner>1</isTenPercentOwner>
            <isOther>0</isOther>
        </reportingOwnerRelationship>
    </reportingOwner>
    <aff10b5One>0</aff10b5One>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <securityTitle>
                <value>Mandatory Redeemable Preferred Shares, Series D</value>
            </securityTitle>
            <transactionDate>
                <value>2026-08-28</value>
            </transactionDate>
            <transactionCoding>
                <transactionFormType>4</transactionFormType>
                <transactionCode>J</transactionCode>
                <equitySwapInvolved>0</equitySwapInvolved>
                <footnoteId id="F1"/>
                <footnoteId id="F2"/>
            </transactionCoding>
            <transactionTimeliness>
                <value></value>
            </transactionTimeliness>
            <transactionAmounts>
                <transactionShares>
                    <value>100</value>
                    <footnoteId id="F1"/>
                </transactionShares>
                <transactionPricePerShare>
                    <footnoteId id="F1"/>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>A</value>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction>
                    <value>500</value>
                </sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
            <ownershipNature>
                <directOrIndirectOwnership>
                    <value>I</value>
                </directOrIndirectOwnership>
                <natureOfOwnership>
                    <value>See Footnotes</value>
                    <footnoteId id="F2"/>
                    <footnoteId id="F3"/>
                </natureOfOwnership>
            </ownershipNature>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>"""

# (accession, filingDate, class) — BAC's 12-month Form 4s, newest first.
BAC_FEED = [
    ("0000070858-26-000473", "2026-10-05", FGN), ("0000070858-26-000469", "2026-09-17", OWN),
    ("0000070858-26-000464", "2026-09-04", OWN), ("0000070858-26-000460", "2026-09-01", FGN),
    ("0000070858-26-000458", "2026-08-26", FGN), ("0000070858-26-000457", "2026-08-26", FGN),
    ("0000070858-26-000451", "2026-08-20", FGN), ("0000070858-26-000449", "2026-08-18", OWN),
    ("0000070858-26-000439", "2026-08-12", FGN), ("0000070858-26-000411", "2026-08-06", FGN),
    ("0000070858-26-000409", "2026-08-04", FGN), ("0000070858-26-000368", "2026-07-28", FGN),
    ("0000070858-26-000359", "2026-07-22", FGN), ("0000070858-26-000357", "2026-07-20", FGN),
    ("0000070858-26-000356", "2026-07-17", OWN), ("0000070858-26-000349", "2026-07-10", FGN),
    ("0000070858-26-000343", "2026-06-17", OWN), ("0000070858-26-000336", "2026-06-15", FGN),
    ("0000070858-26-000327", "2026-06-02", FGN), ("0000070858-26-000322", "2026-05-22", FGN),
    ("0000070858-26-000321", "2026-05-22", FGN), ("0000070858-26-000320", "2026-05-21", FGN),
    ("0000070858-26-000319", "2026-05-20", FGN), ("0000070858-26-000318", "2026-05-19", OWN),
    ("0000070858-26-000316", "2026-05-18", FGN), ("0000070858-26-000287", "2026-05-12", FGN),
    ("0000070858-26-000286", "2026-05-07", OWN), ("0000070858-26-000284", "2026-05-06", OWN),
    ("0000070858-26-000283", "2026-05-06", OWN), ("0000070858-26-000282", "2026-05-06", OWN),
    ("0000070858-26-000281", "2026-05-06", OWN), ("0000070858-26-000280", "2026-05-06", OWN),
    ("0000070858-26-000279", "2026-05-06", OWN), ("0000070858-26-000278", "2026-05-06", OWN),
    ("0000070858-26-000277", "2026-05-06", OWN), ("0000070858-26-000276", "2026-05-06", OWN),
    ("0000070858-26-000275", "2026-05-06", OWN), ("0000070858-26-000274", "2026-05-06", OWN),
    ("0000070858-26-000253", "2026-05-04", FGN), ("0000070858-26-000251", "2026-05-04", FGN),
    ("0000070858-26-000250", "2026-05-04", OWN), ("0000070858-26-000226", "2026-04-24", OWN),
    ("0000070858-26-000224", "2026-04-17", OWN), ("0000070858-26-000216", "2026-04-06", FGN),
    ("0000070858-26-000215", "2026-04-06", FGN), ("0000070858-26-000214", "2026-03-27", FGN),
    ("0000070858-26-000210", "2026-03-23", FGN), ("0000070858-26-000203", "2026-03-17", OWN),
    ("0000070858-26-000201", "2026-03-17", FGN), ("0000070858-26-000200", "2026-03-13", OWN),
    ("0000070858-26-000196", "2026-03-06", OWN), ("0000070858-26-000195", "2026-03-06", OWN),
    ("0000070858-26-000194", "2026-03-06", OWN), ("0000070858-26-000193", "2026-03-06", OWN),
    ("0000070858-26-000186", "2026-03-05", OWN), ("0000070858-26-000184", "2026-03-03", OWN),
    ("0000070858-26-000183", "2026-03-03", OWN), ("0000070858-26-000182", "2026-03-03", OWN),
    ("0000070858-26-000181", "2026-03-03", OWN), ("0000070858-26-000180", "2026-03-03", OWN),
    ("0000070858-26-000179", "2026-03-03", OWN), ("0000070858-26-000178", "2026-03-03", OWN),
    ("0000070858-26-000177", "2026-03-03", OWN), ("0000070858-26-000176", "2026-03-03", OWN),
    ("0000070858-26-000175", "2026-03-03", OWN), ("0000070858-26-000174", "2026-03-03", OWN),
    ("0000070858-26-000162", "2026-02-27", FGN), ("0000070858-26-000161", "2026-02-25", FGN),
    ("0000070858-26-000159", "2026-02-25", FGN), ("0000070858-26-000143", "2026-02-19", FGN),
    ("0000070858-26-000142", "2026-02-18", OWN), ("0000070858-26-000141", "2026-02-18", OWN),
    ("0000070858-26-000140", "2026-02-18", OWN), ("0000070858-26-000139", "2026-02-18", OWN),
    ("0000070858-26-000138", "2026-02-18", OWN), ("0000070858-26-000137", "2026-02-18", OWN),
    ("0000070858-26-000136", "2026-02-18", OWN), ("0000070858-26-000135", "2026-02-18", OWN),
    ("0000070858-26-000134", "2026-02-18", OWN), ("0000070858-26-000133", "2026-02-18", OWN),
    ("0000070858-26-000132", "2026-02-18", OWN), ("0000070858-26-000131", "2026-02-18", OWN),
    ("0000070858-26-000130", "2026-02-18", OWN), ("0000070858-26-000114", "2026-02-13", OWN),
    ("0000070858-26-000113", "2026-02-13", OWN), ("0000070858-26-000112", "2026-02-13", OWN),
    ("0000070858-26-000111", "2026-02-13", OWN), ("0000070858-26-000110", "2026-02-13", OWN),
    ("0000070858-26-000109", "2026-02-13", OWN), ("0000070858-26-000108", "2026-02-13", OWN),
    ("0000070858-26-000107", "2026-02-13", OWN), ("0000070858-26-000106", "2026-02-13", OWN),
    ("0000070858-26-000105", "2026-02-13", OWN), ("0000070858-26-000104", "2026-02-13", OWN),
    ("0000070858-26-000103", "2026-02-13", OWN), ("0000070858-26-000102", "2026-02-13", OWN),
    ("0000070858-26-000038", "2026-02-06", OWN), ("0000070858-26-000030", "2026-02-04", FGN),
    ("0000070858-26-000023", "2026-01-28", FGN), ("0000070858-26-000022", "2026-01-20", OWN),
    ("0000070858-26-000005", "2026-01-05", FGN), ("0000070858-26-000004", "2026-01-05", FGN),
    ("0000070858-26-000003", "2026-01-05", FGN), ("0000070858-25-000488", "2025-12-30", FGN),
    ("0000070858-25-000487", "2025-12-22", FGN), ("0000070858-25-000485", "2025-12-17", OWN),
    ("0000070858-25-000481", "2025-12-15", FGN), ("0000070858-25-000480", "2025-12-12", OWN),
    ("0000070858-25-000478", "2025-12-11", OWN), ("0000070858-25-000476", "2025-12-10", FGN),
    ("0000070858-25-000475", "2025-12-05", OWN), ("0000070858-25-000472", "2025-12-02", OWN),
    ("0000070858-25-000470", "2025-11-20", FGN), ("0000070858-25-000469", "2025-11-18", FGN),
    ("0000070858-25-000467", "2025-11-18", OWN), ("0000070858-25-000466", "2025-11-18", OWN),
    ("0000070858-25-000465", "2025-11-18", OWN), ("0000070858-25-000464", "2025-11-18", OWN),
    ("0000070858-25-000457", "2025-11-14", OWN), ("0000070858-25-000412", "2025-11-12", FGN),
    ("0000070858-25-000409", "2025-11-07", FGN), ("0000070858-25-000399", "2025-10-24", OWN),
    ("0000070858-25-000397", "2025-10-17", OWN), ("0000070858-25-000391", "2025-10-15", FGN),
    ("0000070858-25-000384", "2025-10-14", FGN),
]

_OWN_ACCS = [a for a, _, c in BAC_FEED if c is OWN]


class _FrozenNow(datetime):
    """The feed's fetch day, so the real filing dates stay in the window."""
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 6, 12, 0)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class TestOwnFilingCap(unittest.TestCase):
    """fetch_insider_trades(force=True) — the refresh-insider job's path."""

    def setUp(self):
        clear = getattr(f4.fetch_insider_trades, "clear", None)
        if clear:
            clear()
        self.saved = {}
        self.fetched = []
        self.xml_for = {a: (OWN_XML if c is OWN else FOREIGN_XML)
                        for a, _, c in BAC_FEED}
        submissions = {"filings": {"recent": {
            "form": ["4"] * len(BAC_FEED),
            "accessionNumber": [a for a, _, _ in BAC_FEED],
            "filingDate": [d for _, d, _ in BAC_FEED]}}}

        def _fetch(acc, cik):
            self.fetched.append(acc)
            return self.xml_for[acc]

        def _save(prefix, name, obj):
            self.saved[name] = obj

        for target, name, fn in (
                (f4, "datetime", _FrozenNow),
                (f4.requests, "get", lambda url, *a, **k: _Resp(submissions)),
                (f4, "_fetch_form4_xml", _fetch),
                (f4, "load_json", lambda prefix, name: None),
                (f4, "save_json", _save)):
            p = patch.object(target, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def _kept(self):
        return {t["accession"]
                for t in self.saved[f"{BAC_CIK}.json"]["transactions"]}

    def test_foreign_filings_do_not_spend_the_cap(self):
        # Fixture shape: the 30 newest hold >=19 foreign-issuer filings, so
        # the old cap-then-filter kept only 10 of BAC's own.
        self.assertGreaterEqual(sum(c is FGN for *_, c in BAC_FEED[:30]), 19)
        self.assertEqual(sum(c is OWN for *_, c in BAC_FEED[:30]), 10)

        out = f4.fetch_insider_trades(BAC_CIK, force=True)

        self.assertEqual(self._kept(), set(_OWN_ACCS[:30]))
        self.assertEqual({t["accession"] for t in out}, set(_OWN_ACCS[:30]))
        self.assertFalse(any(t["insider"] == "BANK OF AMERICA CORP /DE/"
                             for t in out))
        # The walk stops at the 30th own filing (feed row 57), no further.
        self.assertEqual(_OWN_ACCS.index(self.fetched[-1]), 29)
        self.assertEqual(self.fetched, [a for a, _, _ in BAC_FEED[:57]])

    def test_fetch_bound_holds_when_nothing_matches(self):
        for label, xml in (("all foreign", FOREIGN_XML), ("all failed", None)):
            with self.subTest(label):
                self.fetched.clear()
                self.xml_for = dict.fromkeys(self.xml_for, xml)
                f4.fetch_insider_trades(BAC_CIK, force=True)
                self.assertEqual(len(self.fetched), f4._MAX_XML_FETCHES)
                self.assertEqual(self.fetched,
                                 [a for a, _, _ in BAC_FEED[:f4._MAX_XML_FETCHES]])
                self.assertEqual(self._kept(), set())

    def test_no_extra_fetches_when_every_filing_is_own(self):
        self.xml_for = dict.fromkeys(self.xml_for, OWN_XML)
        f4.fetch_insider_trades(BAC_CIK, force=True)
        self.assertEqual(self.fetched, [a for a, _, _ in BAC_FEED[:30]])

    def test_cutoff_respected(self):
        # months_back=3 from 2026-10-06 12:00 → cutoff 2026-07-08: the walk
        # ends at feed row 16 (2026-07-10) with only 4 own filings found.
        in_window = [a for a, d, _ in BAC_FEED if d >= "2026-07-08"]
        self.assertEqual(len(in_window), 16)

        f4.fetch_insider_trades(BAC_CIK, months_back=3, force=True)

        self.assertEqual(self.fetched, in_window)
        self.assertEqual(self._kept(),
                         {a for a, d, c in BAC_FEED
                          if c is OWN and d >= "2026-07-08"})
        self.assertEqual(len(self._kept()), 4)


class _HttpResp:
    def __init__(self, text="", payload=None):
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


class TestXmlFetchThrottle(unittest.TestCase):
    """_fetch_form4_xml goes through data/http.get_with_retry behind the
    ~9 req/s min-interval lock (SEC fair access is 10 req/s)."""

    ACC = "0000070858-26-000469"
    BASE = "https://www.sec.gov/Archives/edgar/data/70858/000007085826000469"

    def _run(self, responder):
        clock, sleeps, urls = [100.0], [], []

        class _Time:
            @staticmethod
            def monotonic():
                return clock[0]

            @staticmethod
            def sleep(s):
                sleeps.append(s)
                clock[0] += s

        def _get_with_retry(url, headers=None, timeout=15, **kw):
            urls.append(url)
            return responder(url)

        with patch.object(f4, "time", _Time), \
             patch.object(f4, "_sec_last", [0.0]), \
             patch("data.http.get_with_retry", _get_with_retry):
            xml = f4._fetch_form4_xml(self.ACC, BAC_CIK)
        return xml, urls, sleeps

    def test_requests_are_spaced_by_the_min_interval(self):
        def responder(url):
            if url.endswith("/index.json"):
                # The filing's real directory entry (primaryDocument form4.xml).
                return _HttpResp(payload={"directory": {"item": [
                    {"name": "form4.xml"}]}})
            return _HttpResp(text=OWN_XML)

        xml, urls, sleeps = self._run(responder)
        self.assertEqual(xml, OWN_XML)
        self.assertEqual(urls, [f"{self.BASE}/index.json",
                                f"{self.BASE}/form4.xml"])
        # Back-to-back calls on a frozen clock: the 2nd waits the full interval.
        self.assertEqual(sleeps, [f4._SEC_MIN_INTERVAL])

    def test_429_exhausted_is_a_missing_filing(self):
        xml, urls, _ = self._run(lambda url: None)
        self.assertIsNone(xml)
        self.assertEqual(urls, [f"{self.BASE}/index.json"])


if __name__ == "__main__":
    unittest.main()
