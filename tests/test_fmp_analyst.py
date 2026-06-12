"""
Parser tests for the FMP analyst-coverage / compensation / insider client
(data/fmp_client.py). No live network: `_get` is patched with fixtures
shaped per FMP's stable-endpoint documentation (WAL-representative values,
hand-entered), and every assertion is the exact hand-checked value from
the fixture — the parser must extract, never transform.

Run: PYTHONIOENCODING=utf-8 python -m unittest tests.test_fmp_analyst
"""
import unittest
from contextlib import ExitStack
from unittest import mock

from data import fmp_client


def call_patched(func, fixture, *args, **kwargs):
    """Call `func` with the key present, cache cold, and `_get` returning
    `fixture`. Returns (result, cache_put_calls) — puts is the list of
    (key, value) the function tried to cache (empty on failure paths,
    per the never-cache-failures house pattern)."""
    puts = []
    with ExitStack() as stack:
        stack.enter_context(
            mock.patch.object(fmp_client, "_has_key", return_value=True))
        stack.enter_context(
            mock.patch.object(fmp_client, "_cache_get", return_value=None))
        stack.enter_context(mock.patch.object(
            fmp_client, "_cache_put",
            side_effect=lambda k, v: puts.append((k, v))))
        stack.enter_context(
            mock.patch.object(fmp_client, "_get", return_value=fixture))
        return func(*args, **kwargs), puts


# ── Fixtures: stable-endpoint response shapes, WAL-representative ────────

PT_CONSENSUS = [{
    "symbol": "WAL",
    "targetHigh": 105.0,
    "targetLow": 72.0,
    "targetConsensus": 89.43,
    "targetMedian": 90.0,
}]

PT_SUMMARY = [{
    "symbol": "WAL",
    "lastMonthCount": 4,
    "lastMonthAvgPriceTarget": 91.25,
    "lastQuarterCount": 11,
    "lastQuarterAvgPriceTarget": 88.18,
    "lastYearCount": 28,
    "lastYearAvgPriceTarget": 84.5,
    "allTimeCount": 113,
    "allTimeAvgPriceTarget": 79.92,
    "publishers": '["Benzinga", "TheFly", "Pulse 2.0"]',  # JSON-in-string
}]

GRADES = [
    {  # stable field names
        "symbol": "WAL", "date": "2026-06-08",
        "gradingCompany": "Morgan Stanley", "action": "upgrade",
        "previousGrade": "Equal-Weight", "newGrade": "Overweight",
    },
    {  # legacy field names + datetime-style date
        "date": "2025-11-03 08:12:00", "company": "Citigroup",
        "action": "downgrade", "fromGrade": "Buy", "grade": "Neutral",
    },
]

RATINGS = [{
    "symbol": "WAL", "rating": "A-",
    "overallScore": 4, "discountedCashFlowScore": 3,
    "returnOnEquityScore": 4, "returnOnAssetsScore": 4,
    "debtToEquityScore": 5, "priceToEarningsScore": 4,
    "priceToBookScore": 5,
}]

_DEF14A = ("https://www.sec.gov/Archives/edgar/data/1212545/"
           "000121254526000045/0001212545-26-000045-index.htm")
EXEC_COMP = [
    {
        "symbol": "WAL", "cik": "0001212545", "year": 2025,
        "nameAndPosition": "Kenneth A. Vecchione, President and CEO",
        "salary": 1100000.0,
        "bonus": 0.0,  # real $0 — must survive as 0.0, never None
        "stockAward": 4750000.0,
        "incentivePlanCompensation": 2310000.0,
        "allOtherCompensation": 61542.0,
        "total": 8221542.0,
        "link": _DEF14A,
    },
    {  # no filing link — source_url must fall back to the endpoint
        "symbol": "WAL", "year": 2025,
        "name": "Dale Gibbons", "position": "Vice Chairman and CFO",
        "salary": 700000.0, "bonus": 0.0, "stockAward": 2100000.0,
        "incentivePlanCompensation": 1050000.0,
        "allOtherCompensation": 28411.0, "total": 3878411.0,
    },
]

_FORM4 = ("https://www.sec.gov/Archives/edgar/data/1212545/"
          "000150938726000003/0001509387-26-000003-index.htm")
INSIDER = [{
    "symbol": "WAL",
    "filingDate": "2026-06-09 18:31:24",
    "transactionDate": "2026-06-08",
    "reportingCik": "0001509387",
    "companyCik": "0001212545",
    "transactionType": "S-Sale",
    "securitiesOwned": 104567.0,
    "reportingName": "Vecchione Kenneth A",
    "typeOfOwner": "officer: President and CEO",
    "acquisitionOrDisposition": "D",
    "directOrIndirect": "D",
    "formType": "4",
    "securitiesTransacted": 12500.0,
    "price": 88.41,
    "securityName": "Common Stock",
    "url": _FORM4,
}]


class TestPriceTargetConsensus(unittest.TestCase):
    def test_exact_extraction(self):
        out, puts = call_patched(
            fmp_client.get_price_target_consensus, PT_CONSENSUS, "wal")
        self.assertEqual(out, {
            "consensus": 89.43,
            "high": 105.0,
            "low": 72.0,
            "median": 90.0,
            "source_url": ("https://financialmodelingprep.com/stable/"
                           "price-target-consensus?symbol=WAL"),
        })
        self.assertNotIn("apikey", out["source_url"])
        self.assertEqual(len(puts), 1)  # success IS cached

    def test_missing_field_is_none(self):
        row = [{"symbol": "WAL", "targetConsensus": 89.43}]
        out, _ = call_patched(
            fmp_client.get_price_target_consensus, row, "WAL")
        self.assertEqual(out["consensus"], 89.43)
        self.assertIsNone(out["high"])
        self.assertIsNone(out["median"])

    def test_denial_returns_none_and_never_caches(self):
        # plan-denied / network failure → _get returns None (house pattern)
        out, puts = call_patched(
            fmp_client.get_price_target_consensus, None, "WAL")
        self.assertIsNone(out)
        self.assertEqual(puts, [])

    def test_no_key_short_circuits(self):
        with mock.patch.object(fmp_client, "_has_key", return_value=False), \
             mock.patch.object(fmp_client, "_get") as g:
            self.assertIsNone(fmp_client.get_price_target_consensus("WAL"))
            g.assert_not_called()


class TestPriceTargetSummary(unittest.TestCase):
    def test_exact_extraction_and_publisher_decoding(self):
        out, _ = call_patched(
            fmp_client.get_price_target_summary, PT_SUMMARY, "WAL")
        self.assertEqual(out["last_month_count"], 4)
        self.assertEqual(out["last_month_avg"], 91.25)
        self.assertEqual(out["last_quarter_count"], 11)
        self.assertEqual(out["last_quarter_avg"], 88.18)
        self.assertEqual(out["last_year_count"], 28)
        self.assertEqual(out["last_year_avg"], 84.5)
        self.assertEqual(out["all_time_count"], 113)
        self.assertEqual(out["all_time_avg"], 79.92)
        self.assertEqual(out["publishers"],
                         ["Benzinga", "TheFly", "Pulse 2.0"])
        self.assertEqual(out["source_url"],
                         "https://financialmodelingprep.com/stable/"
                         "price-target-summary?symbol=WAL")

    def test_unparseable_publishers_become_empty_list(self):
        row = [{**PT_SUMMARY[0], "publishers": "not json ["}]
        out, _ = call_patched(
            fmp_client.get_price_target_summary, row, "WAL")
        self.assertEqual(out["publishers"], [])

    def test_empty_payload_returns_none(self):
        out, puts = call_patched(
            fmp_client.get_price_target_summary, [], "WAL")
        self.assertIsNone(out)
        self.assertEqual(puts, [])


class TestAnalystGrades(unittest.TestCase):
    def test_stable_and_legacy_field_names(self):
        out, _ = call_patched(fmp_client.get_analyst_grades, GRADES, "WAL")
        self.assertEqual(out[0], {
            "date": "2026-06-08",
            "firm": "Morgan Stanley",
            "action": "upgrade",
            "from_grade": "Equal-Weight",
            "to_grade": "Overweight",
            "source_url": ("https://financialmodelingprep.com/stable/"
                           "grades?symbol=WAL"),
        })
        # legacy names map; datetime string truncates to the date
        self.assertEqual(out[1]["date"], "2025-11-03")
        self.assertEqual(out[1]["firm"], "Citigroup")
        self.assertEqual(out[1]["from_grade"], "Buy")
        self.assertEqual(out[1]["to_grade"], "Neutral")

    def test_failure_returns_empty_list(self):
        out, puts = call_patched(fmp_client.get_analyst_grades, None, "WAL")
        self.assertEqual(out, [])
        self.assertEqual(puts, [])


class TestRatingsSnapshot(unittest.TestCase):
    def test_exact_extraction(self):
        out, _ = call_patched(fmp_client.get_ratings_snapshot, RATINGS, "WAL")
        self.assertEqual(out, {
            "rating": "A-",
            "overall_score": 4,
            "dcf_score": 3,
            "roe_score": 4,
            "roa_score": 4,
            "debt_to_equity_score": 5,
            "pe_score": 4,
            "pb_score": 5,
            "source_url": ("https://financialmodelingprep.com/stable/"
                           "ratings-snapshot?symbol=WAL"),
        })

    def test_failure_returns_none(self):
        out, puts = call_patched(fmp_client.get_ratings_snapshot, None, "WAL")
        self.assertIsNone(out)
        self.assertEqual(puts, [])


class TestExecutiveCompensation(unittest.TestCase):
    def test_exact_extraction_with_filing_link(self):
        out, _ = call_patched(
            fmp_client.get_executive_compensation, EXEC_COMP, "WAL")
        ceo = out[0]
        self.assertEqual(ceo["name"],
                         "Kenneth A. Vecchione, President and CEO")
        self.assertIsNone(ceo["title"])  # no separate position field
        self.assertEqual(ceo["year"], 2025)
        self.assertEqual(ceo["salary"], 1100000.0)
        self.assertEqual(ceo["bonus"], 0.0)  # $0 preserved, not None
        self.assertEqual(ceo["stock_awards"], 4750000.0)
        self.assertEqual(ceo["incentive"], 2310000.0)
        self.assertEqual(ceo["other"], 61542.0)
        self.assertEqual(ceo["total"], 8221542.0)
        # provenance: source_url IS the DEF 14A link
        self.assertEqual(ceo["filing_url"], _DEF14A)
        self.assertEqual(ceo["source_url"], _DEF14A)

    def test_missing_link_falls_back_to_endpoint(self):
        out, _ = call_patched(
            fmp_client.get_executive_compensation, EXEC_COMP, "WAL")
        cfo = out[1]
        self.assertEqual(cfo["name"], "Dale Gibbons")
        self.assertEqual(cfo["title"], "Vice Chairman and CFO")
        self.assertEqual(cfo["total"], 3878411.0)
        self.assertIsNone(cfo["filing_url"])
        self.assertEqual(cfo["source_url"],
                         "https://financialmodelingprep.com/stable/"
                         "executive-compensation?symbol=WAL")

    def test_failure_returns_empty_list(self):
        out, puts = call_patched(
            fmp_client.get_executive_compensation, None, "WAL")
        self.assertEqual(out, [])
        self.assertEqual(puts, [])


class TestInsiderTrading(unittest.TestCase):
    def test_exact_extraction(self):
        out, puts = call_patched(
            fmp_client.get_insider_trading, INSIDER, "wal")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0], {
            "filing_date": "2026-06-09",      # truncated from datetime
            "transaction_date": "2026-06-08",
            "insider": "Vecchione Kenneth A",
            "relationship": "officer: President and CEO",
            "transaction_type": "S-Sale",
            "acquisition_or_disposition": "D",
            "shares": 12500.0,
            "price": 88.41,
            "shares_owned": 104567.0,
            "security": "Common Stock",
            "form_type": "4",
            "source_url": _FORM4,             # the actual Form 4 filing
        })
        self.assertEqual(len(puts), 1)

    def test_missing_url_falls_back_to_endpoint(self):
        row = [{k: v for k, v in INSIDER[0].items() if k != "url"}]
        out, _ = call_patched(fmp_client.get_insider_trading, row, "WAL")
        self.assertEqual(out[0]["source_url"],
                         "https://financialmodelingprep.com/stable/"
                         "insider-trading/search?symbol=WAL")

    def test_failure_returns_empty_list_and_never_caches(self):
        out, puts = call_patched(fmp_client.get_insider_trading, None, "WAL")
        self.assertEqual(out, [])
        self.assertEqual(puts, [])

    def test_no_key_short_circuits(self):
        with mock.patch.object(fmp_client, "_has_key", return_value=False), \
             mock.patch.object(fmp_client, "_get") as g:
            self.assertEqual(fmp_client.get_insider_trading("WAL"), [])
            g.assert_not_called()


if __name__ == "__main__":
    unittest.main()
