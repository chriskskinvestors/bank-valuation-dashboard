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

    def test_director_flag_accepts_both_boolean_spellings(self):
        # Filers spell the relationship booleans "1" AND "true" — the real
        # RVSB filing used "true" and its director rendered as "Insider".
        txs = _parse_form4(_form4_xml("P", _AD_ACQUIRED))
        self.assertEqual(txs[0]["role"], "Director")  # helper uses "1"
        alt = _form4_xml("P", _AD_ACQUIRED).replace(
            "<isDirector>1</isDirector>", "<isDirector>true</isDirector>")
        txs = _parse_form4(alt)
        self.assertEqual(txs[0]["role"], "Director")


class TestAcceptanceToUtc(unittest.TestCase):
    def test_fake_z_digits_are_eastern(self):
        # Same EDGAR quirk the 8-K lane pins: digits are ET despite ".000Z".
        from data.form4_client import _acceptance_to_utc_iso
        self.assertEqual(_acceptance_to_utc_iso("2026-09-15T10:50:27.000Z"),
                         "2026-09-15T14:50:27+00:00")
        self.assertIsNone(_acceptance_to_utc_iso(None))
        self.assertIsNone(_acceptance_to_utc_iso("garbage"))


# ── Firehose delta (poll_form4_firehose) ─────────────────────────────────
# Hermetic: requests.get and the GCS cache I/O are patched. The Atom page is
# shaped exactly like EDGAR's getcurrent feed on 2026-09-15, including the
# `type=4` prefix-match pollution (424B2) and the duplicate (Reporting) entry.

_ATOM_PAGE = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Latest Filings</title>
<entry>
<title>424B2 - Wells Fargo Finance LLC (0001738143) (Filer)</title>
<updated>2026-09-15T11:32:02-04:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="424B2"/>
<id>urn:tag:sec.gov,2008:accession-number=0001839882-26-045520</id>
</entry>
<entry>
<title>4 - Zamanizadeh Kourosh Nasser (0001966890) (Reporting)</title>
<updated>2026-09-15T10:50:27-04:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="4"/>
<id>urn:tag:sec.gov,2008:accession-number=0002128593-26-000004</id>
</entry>
<entry>
<title>4 - RIVERVIEW BANCORP INC (0001041368) (Issuer)</title>
<updated>2026-09-15T10:50:27-04:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="4"/>
<id>urn:tag:sec.gov,2008:accession-number=0002128593-26-000004</id>
</entry>
<entry>
<title>4 - SOME OTHER CORP (0009999999) (Issuer)</title>
<updated>2026-09-15T10:49:00-04:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="4"/>
<id>urn:tag:sec.gov,2008:accession-number=0009999999-26-000001</id>
</entry>
</feed>"""

_EMPTY_ATOM = ('<?xml version="1.0"?>'
               '<feed xmlns="http://www.w3.org/2005/Atom"><title>x</title></feed>')

_RVSB_CIK = 1041368
_RVSB_ACC = "0002128593-26-000004"


class _FakeResp:
    def __init__(self, text="", payload=None):
        self.text = text
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_requests_get(calls):
    """URL-dispatching stand-in for requests.get; appends each URL to calls."""
    form4_xml = _form4_xml("P", _AD_ACQUIRED)

    def _get(url, **kwargs):
        calls.append(url)
        if "action=getcurrent" in url:
            return _FakeResp(text=_ATOM_PAGE if "start=0" in url else _EMPTY_ATOM)
        if url.endswith("/index.json"):
            return _FakeResp(payload={"directory": {"item": [
                {"name": "form4-09152026_020923.xml"}]}})
        if url.endswith(".xml"):
            return _FakeResp(text=form4_xml)
        raise AssertionError(f"unexpected URL fetched: {url}")

    return _get


class TestFirehoseDelta(unittest.TestCase):
    def _run(self, cached):
        """Run poll_form4_firehose with patched network + cache I/O.
        Returns (result, saved_objects, fetched_urls)."""
        from unittest.mock import patch
        import data.form4_client as f4

        saved: dict = {}
        calls: list = []

        def _load(prefix, name):
            return cached

        def _save(prefix, name, obj):
            saved[name] = obj

        with patch.object(f4.requests, "get", _fake_requests_get(calls)), \
             patch.object(f4, "load_json", _load), \
             patch.object(f4, "save_json", _save):
            result = f4.poll_form4_firehose({"RVSB": _RVSB_CIK}, pages=2)
        return result, saved, calls

    def test_new_filing_merges_into_cache(self):
        (n_filings, n_tx), saved, calls = self._run(cached=None)
        self.assertEqual(n_filings, 1)
        self.assertEqual(n_tx, 1)
        obj = saved[f"{_RVSB_CIK}.json"]
        # Created cache is stamped permanently stale so the nightly sweep
        # still does the full 12-month backfill for this bank.
        self.assertEqual(obj["cached_at"], "1970-01-01T00:00:00")
        tx = obj["transactions"][0]
        self.assertEqual(tx["accession"], _RVSB_ACC)
        self.assertEqual(tx["filing_date"], "2026-09-15")
        # Real acceptance instant, UTC (feed entry says 10:50:27-04:00) —
        # this is what lets the Home feed rank the row at filing time.
        self.assertEqual(tx["filed_at"], "2026-09-15T14:50:27+00:00")
        self.assertEqual(tx["direction"], "Buy")
        self.assertEqual(tx["code"], "P")
        # The non-universe issuer's XML must never be fetched.
        self.assertFalse(any("9999999" in u for u in calls))

    def test_already_cached_accession_is_skipped(self):
        cached = {"cik": _RVSB_CIK, "cached_at": "2026-09-15T06:00:00",
                  "transactions": [{"accession": _RVSB_ACC, "date": "2026-09-14"}]}
        (n_filings, n_tx), saved, calls = self._run(cached=cached)
        self.assertEqual((n_filings, n_tx), (0, 0))
        self.assertEqual(saved, {})  # nothing rewritten
        # Only the two feed pages were fetched — no per-filing requests.
        self.assertTrue(all("action=getcurrent" in u for u in calls))

    def test_merge_preserves_existing_cache_freshness_and_rows(self):
        cached = {"cik": _RVSB_CIK, "cached_at": "2026-09-15T06:00:00",
                  "transactions": [{"accession": "0000000000-26-000001",
                                    "date": "2026-08-01", "code": "S"}]}
        (n_filings, n_tx), saved, _ = self._run(cached=cached)
        self.assertEqual(n_filings, 1)
        obj = saved[f"{_RVSB_CIK}.json"]
        # Nightly-sweep freshness untouched; old rows kept; newest first.
        self.assertEqual(obj["cached_at"], "2026-09-15T06:00:00")
        self.assertEqual(len(obj["transactions"]), 2)
        self.assertEqual(obj["transactions"][0]["accession"], _RVSB_ACC)
        self.assertEqual(obj["transactions"][1]["date"], "2026-08-01")


if __name__ == "__main__":
    unittest.main()
