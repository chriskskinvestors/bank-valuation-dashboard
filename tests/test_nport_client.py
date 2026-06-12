"""
Tests for data/nport_client.py — NPORT-P (mutual-fund holdings) reverse lookup.

No network. Fixture XML is hand-built from a REAL filing fetched live on
2026-06-12: Matthew 25 Fund NPORT-P accession 0001162044-26-000494
(CIK 1003839), whose EWBC entry is pinned verbatim below:

    <name>East West Bancorp, Inc.</name>
    <cusip>27579R104</cusip>
    <identifiers><isin value="US27579R1041"/></identifiers>
    <balance>87500.00</balance> <units>NS</units>
    <valUSD>9341500.00</valUSD> <pctVal>3.09</pctVal> <assetCat>EC</assetCat>

87500 sh x $106.76 = $9,341,500 — valUSD is RAW DOLLARS (no 13F-style
thousands ambiguity). The First Bancorp ambiguity fixtures are likewise
real entries observed live in an Invesco ETF Trust II filing:
"First Bancorp" (NC, CUSIP 318910106) vs "First BanCorp." (PR, CUSIP
318672706) — both normalize to FIRST BANCORP, neither carries a ticker.
"""

import unittest
from unittest.mock import patch, Mock
from xml.etree import ElementTree as ET

import requests

from data import nport_client as npc

NS = "http://www.sec.gov/edgar/nport"

# Verbatim shape of the live EWBC entry (Matthew 25 Fund, rep 2026-03-31).
EWBC_ENTRY = """
  <invstOrSec>
    <name>East West Bancorp, Inc.</name>
    <lei>N/A</lei>
    <title>East West Bancorp, Inc.</title>
    <cusip>27579R104</cusip>
    <identifiers><isin value="US27579R1041" /></identifiers>
    <balance>87500.00</balance>
    <units>NS</units>
    <curCd>USD</curCd>
    <valUSD>9341500.00</valUSD>
    <pctVal>3.09</pctVal>
    <payoffProfile>Long</payoffProfile>
    <assetCat>EC</assetCat>
    <issuerCat>CORP</issuerCat>
    <invCountry>US</invCountry>
  </invstOrSec>
"""

# Same issuer as a debt position: units PA, not NS — must be skipped.
EWBC_BOND_ENTRY = """
  <invstOrSec>
    <name>East West Bancorp, Inc.</name>
    <title>East West Bancorp 3.875% Notes</title>
    <cusip>27579R104</cusip>
    <balance>1000000.00</balance>
    <units>PA</units>
    <valUSD>985000.00</valUSD>
    <pctVal>0.33</pctVal>
    <assetCat>DBT</assetCat>
  </invstOrSec>
"""

# Share-denominated (NS) but preferred equity (assetCat EP) — must be skipped.
EWBC_PREF_ENTRY = """
  <invstOrSec>
    <name>East West Bancorp, Inc.</name>
    <title>East West Bancorp Pfd</title>
    <cusip>27579R104</cusip>
    <balance>5000.00</balance>
    <units>NS</units>
    <valUSD>125000.00</valUSD>
    <pctVal>0.04</pctVal>
    <assetCat>EP</assetCat>
  </invstOrSec>
"""

# Entry identified only by a ticker attribute (CUSIP placeholder).
EWBC_TICKER_ONLY_ENTRY = """
  <invstOrSec>
    <name>EAST WEST BANCORP INC COM</name>
    <cusip>N/A</cusip>
    <identifiers><ticker value="EWBC" /></identifiers>
    <balance>1000.00</balance>
    <units>NS</units>
    <valUSD>106760.00</valUSD>
    <pctVal>0.10</pctVal>
    <assetCat>EC</assetCat>
  </invstOrSec>
"""

# Real ambiguous pair observed live (Invesco Exchange-Traded Fund Trust II):
# both names normalize to FIRST BANCORP; CUSIPs differ; no ticker attribute.
FBNC_ENTRY = """
  <invstOrSec>
    <name>First Bancorp</name>
    <cusip>318910106</cusip>
    <balance>12000.00</balance>
    <units>NS</units>
    <valUSD>480000.00</valUSD>
    <pctVal>0.20</pctVal>
    <assetCat>EC</assetCat>
  </invstOrSec>
"""

FBP_ENTRY = """
  <invstOrSec>
    <name>First BanCorp.</name>
    <cusip>318672706</cusip>
    <balance>34000.00</balance>
    <units>NS</units>
    <valUSD>700000.00</valUSD>
    <pctVal>0.28</pctVal>
    <assetCat>EC</assetCat>
  </invstOrSec>
"""


def _filing(entries: str, series_name: str = "Matthew 25 Fund",
            series_id: str = "S000005937",
            rep_date: str = "2026-03-31") -> ET.Element:
    """NPORT-P primary_doc.xml skeleton in the real edgar/nport namespace."""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="{NS}">
  <formData>
    <genInfo>
      <seriesName>{series_name}</seriesName>
      <seriesId>{series_id}</seriesId>
      <repPdEnd>2026-09-30</repPdEnd>
      <repPdDate>{rep_date}</repPdDate>
    </genInfo>
    <invstOrSecs>
{entries}
    </invstOrSecs>
  </formData>
</edgarSubmission>"""
    return ET.fromstring(xml)


class TestParseFilingEntries(unittest.TestCase):
    """_parse_filing_entries against fixtures pinned from the real filing."""

    def test_cusip_match_pins_real_values(self):
        pos = npc._parse_filing_entries(
            _filing(EWBC_ENTRY), "EWBC", "27579R104", "East West Bancorp Inc")
        self.assertIsNotNone(pos)
        self.assertEqual(pos["fund_name"], "Matthew 25 Fund")
        self.assertEqual(pos["series_id"], "S000005937")
        self.assertEqual(pos["report_date"], "2026-03-31")
        self.assertEqual(pos["shares"], 87500.0)
        self.assertEqual(pos["value_usd"], 9341500.0)      # raw dollars
        self.assertEqual(pos["pct_of_fund"], 3.09)
        self.assertEqual(pos["matched_by"], "cusip")
        # valUSD is dollars, not thousands: implied price ~$106.76/sh.
        self.assertAlmostEqual(pos["value_usd"] / pos["shares"], 106.76,
                               places=2)

    def test_ticker_attribute_match(self):
        pos = npc._parse_filing_entries(
            _filing(EWBC_TICKER_ONLY_ENTRY), "EWBC", None, "")
        self.assertIsNotNone(pos)
        self.assertEqual(pos["matched_by"], "ticker")
        self.assertEqual(pos["shares"], 1000.0)
        self.assertEqual(pos["value_usd"], 106760.0)

    def test_ticker_only_entry_matches_despite_cusip_placeholder(self):
        # Caller supplies a CUSIP; entry has placeholder cusip N/A — must
        # fall through to the ticker identifier, not be rejected outright.
        pos = npc._parse_filing_entries(
            _filing(EWBC_TICKER_ONLY_ENTRY), "EWBC", "27579R104", "")
        self.assertIsNotNone(pos)
        self.assertEqual(pos["matched_by"], "ticker")

    def test_normalized_name_exact_match(self):
        pos = npc._parse_filing_entries(
            _filing(EWBC_ENTRY), "EWBC", None, "East West Bancorp Inc")
        self.assertIsNotNone(pos)
        # "East West Bancorp, Inc." == "East West Bancorp Inc" after
        # punctuation strip + trailing-suffix drop. No identifiers needed.
        self.assertEqual(pos["matched_by"], "name")
        self.assertEqual(pos["shares"], 87500.0)

    def test_name_does_not_substring_match_different_issuer(self):
        # "East West Bancorp" must not match e.g. "Bancorp Inc" or
        # "East West Bancorp Capital Trust" style names — full-string only.
        trust = EWBC_ENTRY.replace(
            "<name>East West Bancorp, Inc.</name>",
            "<name>East West Bancorp Capital Trust I</name>").replace(
            "<cusip>27579R104</cusip>", "<cusip>27579RAB1</cusip>")
        pos = npc._parse_filing_entries(
            _filing(trust), "EWBC", None, "East West Bancorp Inc")
        self.assertIsNone(pos)

    def test_non_ns_units_skipped(self):
        # Bond of the SAME issuer with the same CUSIP query: units PA, so it
        # must not be counted as share ownership.
        pos = npc._parse_filing_entries(
            _filing(EWBC_BOND_ENTRY), "EWBC", "27579R104",
            "East West Bancorp Inc")
        self.assertIsNone(pos)
        # And mixed with the common-stock lot, totals stay the equity lot's.
        pos = npc._parse_filing_entries(
            _filing(EWBC_ENTRY + EWBC_BOND_ENTRY), "EWBC", "27579R104",
            "East West Bancorp Inc")
        self.assertEqual(pos["shares"], 87500.0)
        self.assertEqual(pos["value_usd"], 9341500.0)

    def test_non_ec_asset_category_skipped(self):
        pos = npc._parse_filing_entries(
            _filing(EWBC_ENTRY + EWBC_PREF_ENTRY), "EWBC", "27579R104",
            "East West Bancorp Inc")
        self.assertEqual(pos["shares"], 87500.0)
        self.assertEqual(pos["value_usd"], 9341500.0)

    def test_multiple_matching_lots_summed(self):
        second_lot = EWBC_ENTRY.replace(
            "<balance>87500.00</balance>", "<balance>12500.00</balance>"
        ).replace("<valUSD>9341500.00</valUSD>", "<valUSD>1334500.00</valUSD>"
        ).replace("<pctVal>3.09</pctVal>", "<pctVal>0.44</pctVal>")
        pos = npc._parse_filing_entries(
            _filing(EWBC_ENTRY + second_lot), "EWBC", "27579R104",
            "East West Bancorp Inc")
        self.assertEqual(pos["shares"], 100000.0)
        self.assertEqual(pos["value_usd"], 10676000.0)
        self.assertAlmostEqual(pos["pct_of_fund"], 3.53, places=2)

    def test_no_match_returns_none(self):
        pos = npc._parse_filing_entries(
            _filing(FBNC_ENTRY), "EWBC", "27579R104", "East West Bancorp Inc")
        self.assertIsNone(pos)


class TestAmbiguousNameNoCrossMatch(unittest.TestCase):
    """First Bancorp NC vs First BanCorp PR — the docstring's ambiguity case."""

    def test_both_names_normalize_identically(self):
        self.assertEqual(npc._normalize_name("First Bancorp"),
                         "FIRST BANCORP")
        self.assertEqual(npc._normalize_name("First BanCorp."),
                         "FIRST BANCORP")

    def test_cusip_prevents_cross_match(self):
        # Caller = FBNC (NC, 318910106); filing holds the PR issuer only.
        pos = npc._parse_filing_entries(
            _filing(FBP_ENTRY), "FBNC", "318910106", "First Bancorp")
        self.assertIsNone(pos)
        # And in a filing holding BOTH, only the NC position is counted.
        pos = npc._parse_filing_entries(
            _filing(FBNC_ENTRY + FBP_ENTRY), "FBNC", "318910106",
            "First Bancorp")
        self.assertEqual(pos["matched_by"], "cusip")
        self.assertEqual(pos["shares"], 12000.0)
        self.assertEqual(pos["value_usd"], 480000.0)

    def test_ticker_identifier_mismatch_disqualifies(self):
        # PR entry carrying an explicit ticker FBP must NOT fall through to
        # the (ambiguous) name match when the caller asks for FBNC.
        pr_with_ticker = FBP_ENTRY.replace(
            "<cusip>318672706</cusip>",
            "<cusip>N/A</cusip>"
            '<identifiers><ticker value="FBP" /></identifiers>')
        pos = npc._parse_filing_entries(
            _filing(pr_with_ticker), "FBNC", None, "First Bancorp")
        self.assertIsNone(pos)

    def test_entry_matches_basis_directly(self):
        nc = {"name": "First Bancorp", "cusip": "318910106"}
        pr = {"name": "First BanCorp.", "cusip": "318672706"}
        self.assertEqual(
            npc._entry_matches(nc, "FBNC", "318910106", "First Bancorp"),
            "cusip")
        self.assertIsNone(
            npc._entry_matches(pr, "FBNC", "318910106", "First Bancorp"))
        self.assertIsNone(
            npc._entry_matches({**pr, "cusip": "", "ticker": "FBP"},
                               "FBNC", None, "First Bancorp"))


class TestNormalizeName(unittest.TestCase):

    def test_suffix_and_punctuation_stripping(self):
        self.assertEqual(npc._normalize_name("East West Bancorp, Inc."),
                         "EAST WEST BANCORP")
        self.assertEqual(npc._normalize_name("East West Bancorp Inc"),
                         "EAST WEST BANCORP")

    def test_no_substring_semantics(self):
        self.assertNotEqual(npc._normalize_name("First Bancorp of Indiana"),
                            npc._normalize_name("First Bancorp"))


class TestSearchTransient500(unittest.TestCase):
    """EDGAR FTS transient 500s: retry, then [] so query fallback proceeds."""

    @staticmethod
    def _http_error(status):
        resp = Mock(status_code=status)
        return requests.HTTPError(f"{status} Server Error", response=resp)

    def test_persistent_500_returns_empty_after_retries(self):
        with patch.object(npc, "get_with_retry",
                          side_effect=self._http_error(500)) as gw, \
             patch.object(npc.time, "sleep"):
            self.assertEqual(npc._search_nport_filings("27579R104", 10), [])
        self.assertEqual(gw.call_count, 3)

    def test_transient_500_then_success(self):
        ok = Mock()
        ok.json.return_value = {"hits": {"total": {"value": 1}, "hits": [{
            "_id": "0001162044-26-000494:primary_doc.xml",
            "_source": {"ciks": ["0001003839"],
                        "display_names": ["MATTHEW 25 FUND  (CIK 0001003839)"],
                        "file_date": "2026-06-01"},
        }]}}
        with patch.object(npc, "get_with_retry",
                          side_effect=[self._http_error(500), ok]), \
             patch.object(npc.time, "sleep"):
            hits = npc._search_nport_filings("27579R104", 10)
        self.assertEqual(hits, [{
            "cik": "0001003839",
            "accession": "0001162044-26-000494",
            "primary_doc": "primary_doc.xml",
            "registrant": "MATTHEW 25 FUND",
            "date_filed": "2026-06-01",
        }])

    def test_non_5xx_http_error_propagates(self):
        with patch.object(npc, "get_with_retry",
                          side_effect=self._http_error(404)):
            with self.assertRaises(requests.HTTPError):
                npc._search_nport_filings("27579R104", 10)


if __name__ == "__main__":
    unittest.main()
