"""
Form 4 XML parser regression tests (data/form4_client._parse_form4).

Pins the direction bug caught on the RVSB director purchase of 2026-09-15:
acquiredDisposedCode lives under transactionAmounts with a <value> child, but
the parser read it from transactionCoding — matching nothing — so EVERY
non-derivative transaction (open-market purchases included) was stamped
direction "Sell". The XML below is shaped exactly like the real filing
(accession 0002128593-26-000004, values abridged).
"""

import unittest

from data.form4_client import _parse_form4


def _form4_xml(code: str, acq_disp_block: str) -> str:
    return f"""<?xml version="1.0"?>
<ownershipDocument>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerName>Zamanizadeh Kourosh Nasser</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isDirector>1</isDirector>
            <isOfficer>false</isOfficer>
        </reportingOwnerRelationship>
    </reportingOwner>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <transactionDate><value>2026-09-14</value></transactionDate>
            <transactionCoding>
                <transactionFormType>4</transactionFormType>
                <transactionCode>{code}</transactionCode>
                <equitySwapInvolved>0</equitySwapInvolved>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares><value>1800</value></transactionShares>
                <transactionPricePerShare><value>5.3599</value></transactionPricePerShare>
                {acq_disp_block}
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction><value>13676.81</value></sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>"""


_AD_ACQUIRED = ("<transactionAcquiredDisposedCode><value>A</value>"
                "</transactionAcquiredDisposedCode>")
_AD_DISPOSED = ("<transactionAcquiredDisposedCode><value>D</value>"
                "</transactionAcquiredDisposedCode>")


class TestAcquiredDisposedDirection(unittest.TestCase):
    def test_open_market_purchase_is_buy(self):
        """The exact RVSB failure: code P + A/D 'A' must be a Buy."""
        txs = _parse_form4(_form4_xml("P", _AD_ACQUIRED))
        self.assertEqual(len(txs), 1)
        tx = txs[0]
        self.assertEqual(tx["code"], "P")
        self.assertEqual(tx["direction"], "Buy")
        self.assertEqual(tx["shares"], 1800.0)
        self.assertEqual(tx["price"], 5.3599)
        self.assertAlmostEqual(tx["value_usd"], 9647.82)

    def test_open_market_sale_is_sell(self):
        txs = _parse_form4(_form4_xml("S", _AD_DISPOSED))
        self.assertEqual(txs[0]["direction"], "Sell")

    def test_grant_with_acquired_flag_is_buy_direction(self):
        # Grants acquire shares; the P/S feed filter excludes them anyway.
        txs = _parse_form4(_form4_xml("A", _AD_ACQUIRED))
        self.assertEqual(txs[0]["direction"], "Buy")

    def test_missing_ad_flag_falls_back_to_code_semantics(self):
        txs = _parse_form4(_form4_xml("P", ""))
        self.assertEqual(txs[0]["direction"], "Buy")
        txs = _parse_form4(_form4_xml("S", ""))
        self.assertEqual(txs[0]["direction"], "Sell")

    def test_missing_ad_flag_and_nonmarket_code_is_none(self):
        # Neither the A/D flag nor P/S semantics — never guess a direction.
        txs = _parse_form4(_form4_xml("J", ""))
        self.assertIsNone(txs[0]["direction"])


if __name__ == "__main__":
    unittest.main()
