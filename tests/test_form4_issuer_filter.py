"""
Form 4 issuer filter (data/form4_client._issuer_matches + both ingest seams).

A bank CIK's EDGAR submissions feed lists every Form 4 the bank is a party
to, including ones it filed as a REPORTING OWNER of ANOTHER issuer's stock.
The nightly sweep (fetch_insider_trades) walked them all without checking
<issuer><issuerCik>, so another company's securities were booked as the
bank's own insider activity — People Summary showed "JPMORGAN CHASE & CO —
Insider" (the 10%-owner flag maps to the generic "Insider" role).

Fixtures are the REAL EDGAR documents, trimmed (addresses, footnote text,
signatures and holdings dropped; element structure and values verbatim):
  * 0001193125-26-258526 — issuer BLACKROCK MUNIHOLDINGS FUND, INC.
    (issuerCik 0001034665); reporting owners JPMORGAN CHASE & CO (0000019617)
    and DNT Asset Trust. Listed in JPM's submissions feed (CIK 19617).
  * 0001225208-26-007727 — issuer JPMORGAN CHASE & CO (0000019617); Robin
    Leopold, Head of Human Resources, open-market sale of 2,500 sh @ 352.8106.
Fetched 2026-10-05.
"""

import unittest
from datetime import datetime
from unittest.mock import patch

import data.form4_client as f4

JPM_CIK = 19617
OWNER_ACC = "0001193125-26-258526"   # JPM as reporting owner on MHD
INSIDER_ACC = "0001225208-26-007727"  # a JPM officer's own sale

BANK_AS_OWNER_XML = """<?xml version="1.0"?>
<ownershipDocument>
    <schemaVersion>X0609</schemaVersion>
    <documentType>4</documentType>
    <periodOfReport>2026-06-02</periodOfReport>
    <issuer>
        <issuerCik>0001034665</issuerCik>
        <issuerName>BLACKROCK MUNIHOLDINGS FUND, INC.</issuerName>
        <issuerTradingSymbol>MHD</issuerTradingSymbol>
        <issuerForeignTradingSymbol></issuerForeignTradingSymbol>
    </issuer>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>0000019617</rptOwnerCik>
            <rptOwnerName>JPMORGAN CHASE &amp; CO</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isDirector>false</isDirector>
            <isOfficer>false</isOfficer>
            <isTenPercentOwner>true</isTenPercentOwner>
            <isOther>false</isOther>
        </reportingOwnerRelationship>
    </reportingOwner>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>0002005954</rptOwnerCik>
            <rptOwnerName>DNT Asset Trust</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isDirector>false</isDirector>
            <isOfficer>false</isOfficer>
            <isTenPercentOwner>true</isTenPercentOwner>
            <isOther>false</isOther>
        </reportingOwnerRelationship>
    </reportingOwner>
    <aff10b5One>false</aff10b5One>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <securityTitle>
                <value>Series W-7 Variable Rate Muni Term Preferred Shares</value>
            </securityTitle>
            <transactionDate>
                <value>2026-06-02</value>
            </transactionDate>
            <transactionCoding>
                <transactionFormType>4</transactionFormType>
                <transactionCode>J</transactionCode>
                <equitySwapInvolved>false</equitySwapInvolved>
                <footnoteId id="F1"/>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares>
                    <value>7178</value>
                </transactionShares>
                <transactionPricePerShare>
                    <footnoteId id="F1"/>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>D</value>
                    <footnoteId id="F1"/>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction>
                    <value>0</value>
                    <footnoteId id="F1"/>
                </sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
            <ownershipNature>
                <directOrIndirectOwnership>
                    <value>I</value>
                    <footnoteId id="F2"/>
                </directOrIndirectOwnership>
                <natureOfOwnership>
                    <value>By Subsidiary</value>
                    <footnoteId id="F2"/>
                    <footnoteId id="F3"/>
                </natureOfOwnership>
            </ownershipNature>
        </nonDerivativeTransaction>
        <nonDerivativeTransaction>
            <securityTitle>
                <value>Series W-7 Variable Rate Muni Term Preferred Shares</value>
            </securityTitle>
            <transactionDate>
                <value>2026-06-02</value>
            </transactionDate>
            <transactionCoding>
                <transactionFormType>4</transactionFormType>
                <transactionCode>J</transactionCode>
                <equitySwapInvolved>false</equitySwapInvolved>
                <footnoteId id="F1"/>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares>
                    <value>7178</value>
                </transactionShares>
                <transactionPricePerShare>
                    <footnoteId id="F1"/>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>A</value>
                    <footnoteId id="F1"/>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction>
                    <value>7178</value>
                    <footnoteId id="F1"/>
                </sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
            <ownershipNature>
                <directOrIndirectOwnership>
                    <value>I</value>
                </directOrIndirectOwnership>
                <natureOfOwnership>
                    <value>By Trust</value>
                    <footnoteId id="F1"/>
                    <footnoteId id="F2"/>
                </natureOfOwnership>
            </ownershipNature>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>"""

INSIDER_SALE_XML = """<?xml version="1.0"?>
<ownershipDocument>
    <schemaVersion>X0609</schemaVersion>
    <documentType>4</documentType>
    <periodOfReport>2026-09-10</periodOfReport>
    <issuer>
        <issuerCik>0000019617</issuerCik>
        <issuerName>JPMORGAN CHASE &amp; CO</issuerName>
        <issuerTradingSymbol>JPM</issuerTradingSymbol>
        <issuerForeignTradingSymbol></issuerForeignTradingSymbol>
    </issuer>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>0001726825</rptOwnerCik>
            <rptOwnerName>Leopold Robin</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isOfficer>1</isOfficer>
            <officerTitle>Head of Human Resources</officerTitle>
        </reportingOwnerRelationship>
    </reportingOwner>
    <aff10b5One>1</aff10b5One>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <securityTitle>
                <value>Common Stock</value>
            </securityTitle>
            <transactionDate>
                <value>2026-09-10</value>
            </transactionDate>
            <transactionCoding>
                <transactionFormType>4</transactionFormType>
                <transactionCode>S</transactionCode>
                <equitySwapInvolved>0</equitySwapInvolved>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares>
                    <value>2500.0000</value>
                </transactionShares>
                <transactionPricePerShare>
                    <value>352.8106</value>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>D</value>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction>
                    <value>71047.0000</value>
                </sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
            <ownershipNature>
                <directOrIndirectOwnership>
                    <value>D</value>
                </directOrIndirectOwnership>
            </ownershipNature>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
    <derivativeTable></derivativeTable>
    <footnotes></footnotes>
</ownershipDocument>"""

_XML_BY_ACC = {OWNER_ACC: BANK_AS_OWNER_XML, INSIDER_ACC: INSIDER_SALE_XML}


class TestIssuerMatches(unittest.TestCase):
    def test_bank_as_reporting_owner_is_not_the_issuer(self):
        self.assertFalse(f4._issuer_matches(BANK_AS_OWNER_XML, JPM_CIK))
        # ...it IS the issuer document for the fund it was filed on.
        self.assertTrue(f4._issuer_matches(BANK_AS_OWNER_XML, 1034665))

    def test_own_insider_filing_matches(self):
        self.assertTrue(f4._issuer_matches(INSIDER_SALE_XML, JPM_CIK))

    def test_zero_padded_cik_compares_as_integer(self):
        # XML says "0000019617"; callers pass int 19617 or a padded string.
        self.assertTrue(f4._issuer_matches(INSIDER_SALE_XML, 19617))
        self.assertTrue(f4._issuer_matches(INSIDER_SALE_XML, "0000019617"))
        self.assertFalse(f4._issuer_matches(INSIDER_SALE_XML, 196170))

    def test_unparseable_issuer_cik_is_skipped(self):
        bad = INSIDER_SALE_XML.replace("<issuerCik>0000019617</issuerCik>",
                                       "<issuerCik>n/a</issuerCik>")
        self.assertFalse(f4._issuer_matches(bad, JPM_CIK))
        self.assertFalse(f4._issuer_matches("<not xml", JPM_CIK))


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class TestNightlySweepExcludesForeignIssuer(unittest.TestCase):
    """fetch_insider_trades (the refresh-insider job's force=True path)."""

    def setUp(self):
        clear = getattr(f4.fetch_insider_trades, "clear", None)
        if clear:
            clear()
        self.saved = {}
        today = datetime.now().strftime("%Y-%m-%d")
        # JPM's submissions feed lists both accessions as Form 4 (as it does).
        submissions = {"filings": {"recent": {
            "form": ["4", "4"],
            "accessionNumber": [INSIDER_ACC, OWNER_ACC],
            "filingDate": [today, today],
            "reportDate": [today, today],
            "acceptanceDateTime": [f"{today}T16:00:00.000Z"] * 2}}}

        def _save(prefix, name, obj):
            self.saved[name] = obj

        for target, name, fn in (
                (f4.requests, "get", lambda url, *a, **k: _Resp(submissions)),
                (f4, "_fetch_form4_xml", lambda acc, cik: _XML_BY_ACC[acc]),
                (f4, "load_json", lambda prefix, name: None),
                (f4, "save_json", _save)):
            p = patch.object(target, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def test_only_the_banks_own_insider_rows_survive(self):
        out = f4.fetch_insider_trades(JPM_CIK, force=True)
        self.assertEqual([t["insider"] for t in out], ["Leopold Robin"])
        self.assertEqual(out[0]["accession"], INSIDER_ACC)
        self.assertEqual(out[0]["role"], "Head of Human Resources")
        self.assertEqual((out[0]["code"], out[0]["shares"], out[0]["price"]),
                         ("S", 2500.0, 352.8106))
        # The persisted per-CIK cache (read by Home feed / People) is clean too.
        cached = self.saved[f"{JPM_CIK}.json"]["transactions"]
        self.assertEqual({t["accession"] for t in cached}, {INSIDER_ACC})
        self.assertFalse(any("JPMORGAN" in (t["insider"] or "") for t in cached))


class TestFirehoseExcludesForeignIssuer(unittest.TestCase):
    """poll_form4_firehose (poll-events delta) applies the same rule."""

    def _run(self, accession):
        saved = {}
        feed = [{"cik": JPM_CIK, "accession": accession,
                 "filed": "2026-09-10", "filed_at": None}]
        with patch.object(f4, "_recent_form4_filings", lambda pages: feed), \
             patch.object(f4, "_fetch_form4_xml",
                          lambda acc, cik: _XML_BY_ACC[acc]), \
             patch.object(f4, "load_json", lambda prefix, name: None), \
             patch.object(f4, "save_json",
                          lambda prefix, name, obj: saved.__setitem__(name, obj)):
            result = f4.poll_form4_firehose({"JPM": JPM_CIK})
        return result, saved

    def test_foreign_issuer_filing_not_merged(self):
        result, saved = self._run(OWNER_ACC)
        self.assertEqual(result, (0, 0))
        self.assertEqual(saved, {})

    def test_own_insider_filing_merged(self):
        result, saved = self._run(INSIDER_ACC)
        self.assertEqual(result, (1, 1))
        tx = saved[f"{JPM_CIK}.json"]["transactions"][0]
        self.assertEqual((tx["insider"], tx["accession"]),
                         ("Leopold Robin", INSIDER_ACC))


if __name__ == "__main__":
    unittest.main()
