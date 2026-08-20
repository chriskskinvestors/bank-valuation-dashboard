"""
Offline regression tests for data/hmda_client.py (CFPB HMDA public-API client).

All HTTP is stubbed at the shared-retry seam (data.http.get_with_retry) and
all caching at data.cache.get/put — no network, no cache.db. Fixtures are
trimmed from real API responses captured live 2026-08-20 (WaFd Bank
LEI D38AC76TAMYI50NBPX33, Banner Bank LEI WE0I402RW25AU38DTI13; reporter
panel 2023_public_panel_csv.zip; view/aggregations; view/csv). Pins:

  • RSSD → LEI resolution from the reporter-panel zip; newest panel year wins
  • non-filer RSSD → None (n/a, never an error)
  • by-year aggregation math; count-0 years omitted, never rendered as $0
  • unit convention: aggregation `sum` / loan_amount are RAW dollars (no ×1000)
  • state and county breakdown grouping math (hand-computed)
  • a failed fetch is returned as None/omitted and is NEVER cached
"""
from __future__ import annotations

import io
import json
import re
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import hmda_client  # noqa: E402

WAFD_LEI = "D38AC76TAMYI50NBPX33"
BANR_LEI = "WE0I402RW25AU38DTI13"

# Real reporter-panel header + rows (2023_public_panel_csv.zip, trimmed).
PANEL_HEADER = ("activity_year,lei,tax_id,agency_code,id_2017,"
                "respondent_rssd,respondent_name,respondent_state,"
                "respondent_city,assets,other_lender_code,parent_rssd,"
                "parent_name,topholder_rssd,topholder_name")
PANEL_2023_ROWS = [
    f"2023,{WAFD_LEI},91-0135860,9,656377,656377,WASHINGTON FEDERAL BANK,"
    "WA,Seattle,21645013,0,3065617,\"WAFD, INC.\",3065617,\"WAFD, INC.\"",
    f"2023,{BANR_LEI},91-1645638,3,352772,352772,Banner Bank,WA,"
    "Walla Walla,15821645,0,2126977,BANNER CORPORATION,2126977,"
    "BANNER CORPORATION",
    # Credit-union-style row with no RSSD — must be skipped, never mapped.
    "2023,549300HVRAK6I8QGFR41,66-0000000,5,0,0,COOPERATIVA DE AC SAN JOSE,"
    "PR,San Juan,-1,0,-1,,-1,",
]
# Synthetic 2018 row (real shape): same RSSD under a superseded LEI, to pin
# that the newest published panel year wins the merge.
STALE_2018_LEI = "STALE18LEI00000000XX"
PANEL_2018_ROWS = [
    f"2018,{STALE_2018_LEI},91-0135860,9,656377,656377,WASHINGTON FEDERAL,"
    "WA,Seattle,15000000,0,3065617,W.F. INC,3065617,W.F. INC",
]

# Real view/aggregations responses (WaFd 2023 / 2022; zero-year shape).
AGG_FIXTURES = {
    2023: {"parameters": {"lei": WAFD_LEI, "actions_taken": "1"},
           "aggregations": [{"count": 1950, "sum": 1.10112E9,
                             "actions_taken": "1"}],
           "servedFrom": "cache"},
    2022: {"parameters": {"lei": WAFD_LEI, "actions_taken": "1"},
           "aggregations": [{"count": 3903, "sum": 3.223225E9,
                             "actions_taken": "1"}],
           "servedFrom": "cache"},
    # A year the lender did not file: the API answers count 0, not an error.
    2021: {"parameters": {"lei": WAFD_LEI, "actions_taken": "1"},
           "aggregations": [{"count": 0, "sum": 0.0, "actions_taken": "1"}],
           "servedFrom": "db"},
}

# Real view/csv header (trimmed to the leading columns) + hand-picked rows.
CSV_HEADER = ("activity_year,lei,derived_msa-md,state_code,county_code,"
              "census_tract,action_taken,loan_amount")
CSV_ROWS = [
    f"2023,{WAFD_LEI},42660,WA,53073,53073010402,1,75000.0",
    f"2023,{WAFD_LEI},42660,WA,53033,53033025801,1,225000.0",
    f"2023,{WAFD_LEI},42660,WA,53033,53033025802,1,300000.0",
    f"2023,{WAFD_LEI},38900,OR,41051,41051001000,1,415000.0",
    f"2023,{WAFD_LEI},38900,OR,41067,41067030000,1,185000.0",
]


def _panel_zip(rows: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("panel.csv", PANEL_HEADER + "\n" + "\n".join(rows) + "\n")
    return buf.getvalue()


def _http_404() -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = 404
    return requests.HTTPError("404 Client Error", response=resp)


class _FakeResp:
    def __init__(self, content=b"", text="", payload=None):
        self.content = content
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class HmdaClientTestCase(unittest.TestCase):
    """Shared stubs: dict-backed cache + a get_with_retry dispatcher."""

    def setUp(self):
        self.store: dict[str, dict] = {}
        self.http_calls: list[tuple[str, dict | None]] = []
        # Panels published per year; every other year in range 404s
        # (exactly how files.ffiec.cfpb.gov answers unpublished years).
        self.panels: dict[int, bytes] = {2023: _panel_zip(PANEL_2023_ROWS)}
        self.agg_by_year: dict[int, dict] = dict(AGG_FIXTURES)
        self.csv_text = CSV_HEADER + "\n" + "\n".join(CSV_ROWS) + "\n"
        self.fail_years: set[int] = set()      # agg years answering None (429)
        self.panel_conn_error_years: set[int] = set()

        def fake_cache_get(key, max_age_s=None):
            return self.store.get(key)

        def fake_cache_put(key, value):
            self.store[key] = json.loads(json.dumps(value, default=str))

        def fake_get_with_retry(url, params=None, headers=None,
                                timeout=15, max_attempts=3):
            self.http_calls.append((url, params))
            m = re.search(r"snapshot/(\d{4})/", url)
            if m:                               # reporter-panel zip
                year = int(m.group(1))
                if year in self.panel_conn_error_years:
                    raise requests.ConnectionError("boom")
                if year not in self.panels:
                    raise _http_404()
                return _FakeResp(content=self.panels[year])
            if url == hmda_client.HMDA_AGG_URL:
                year = int(params["years"])
                if year in self.fail_years:
                    return None                 # retries exhausted on 429s
                return _FakeResp(payload=self.agg_by_year[year])
            if url == hmda_client.HMDA_CSV_URL:
                return _FakeResp(text=self.csv_text)
            raise AssertionError(f"unexpected url {url}")

        for target, side in [("data.cache.get", fake_cache_get),
                             ("data.cache.put", fake_cache_put),
                             ("data.http.get_with_retry", fake_get_with_retry)]:
            p = mock.patch(target, side_effect=side)
            p.start()
            self.addCleanup(p.stop)


class TestLeiResolution(HmdaClientTestCase):

    def test_resolves_lei_from_panel(self):
        self.assertEqual(hmda_client.resolve_lei(656377), WAFD_LEI)
        self.assertEqual(hmda_client.resolve_lei(352772), BANR_LEI)

    def test_non_filer_returns_none(self):
        # Esquire Financial's RSSD — a real bank that has never filed HMDA.
        self.assertIsNone(hmda_client.resolve_lei(3447820))

    def test_zero_rssd_rows_never_mapped(self):
        # The credit-union row carries respondent_rssd 0 — resolving 0 must
        # not accidentally hit it.
        self.assertIsNone(hmda_client.resolve_lei(0))
        self.assertEqual(len(self.http_calls), 0)  # short-circuits pre-HTTP

    def test_newest_panel_year_wins(self):
        self.panels[2018] = _panel_zip(PANEL_2018_ROWS)
        self.assertEqual(hmda_client.resolve_lei(656377), WAFD_LEI)
        self.assertNotEqual(hmda_client.resolve_lei(656377), STALE_2018_LEI)

    def test_complete_map_is_cached_and_served(self):
        hmda_client.resolve_lei(656377)
        self.assertIn("hmda:panel_map:v1", self.store)
        n_calls = len(self.http_calls)
        self.assertEqual(hmda_client.resolve_lei(352772), BANR_LEI)
        self.assertEqual(len(self.http_calls), n_calls)  # no re-fetch

    def test_partial_panel_failure_not_cached(self):
        # 2019's zip dies with a connection error: positive hits from 2023
        # still resolve, but the incomplete map must NOT be frozen into the
        # cache (a miss could be a false "never filed").
        self.panel_conn_error_years.add(2019)
        self.assertEqual(hmda_client.resolve_lei(656377), WAFD_LEI)
        self.assertNotIn("hmda:panel_map:v1", self.store)


class TestOriginationsByYear(HmdaClientTestCase):

    def test_by_year_values_and_zero_year_omitted(self):
        out = hmda_client.originations_by_year(
            WAFD_LEI, [2021, 2022, 2023])
        # 2021 answered count 0 (did-not-file) — omitted, never a $0 row.
        self.assertEqual(sorted(out), [2022, 2023])
        self.assertEqual(out[2023], {"count": 1950,
                                     "volume_usd": 1101120000.0})
        self.assertEqual(out[2022], {"count": 3903,
                                     "volume_usd": 3223225000.0})

    def test_volume_is_raw_dollars_not_thousands(self):
        # API sum 1.10112E9 must pass through unscaled: WaFd 2023 is $1.1B
        # across 1,950 loans (avg ≈ $565K/loan) — a ×1000 in either
        # direction would be absurd on its face.
        out = hmda_client.originations_by_year(WAFD_LEI, [2023])
        avg = out[2023]["volume_usd"] / out[2023]["count"]
        self.assertAlmostEqual(out[2023]["volume_usd"], 1.10112e9)
        self.assertTrue(100_000 < avg < 2_000_000, avg)

    def test_pre_lei_years_skipped_without_http(self):
        out = hmda_client.originations_by_year(WAFD_LEI, [2016, 2017])
        self.assertEqual(out, {})
        self.assertEqual(self.http_calls, [])

    def test_results_cached_and_served_offline(self):
        hmda_client.originations_by_year(WAFD_LEI, [2022, 2023])
        n_calls = len(self.http_calls)
        out = hmda_client.originations_by_year(WAFD_LEI, [2022, 2023])
        self.assertEqual(len(self.http_calls), n_calls)  # served from cache
        self.assertEqual(out[2023]["count"], 1950)

    def test_failed_fetch_omitted_and_never_cached(self):
        self.fail_years.add(2022)              # 429s exhausted → None
        out = hmda_client.originations_by_year(WAFD_LEI, [2022, 2023])
        self.assertEqual(sorted(out), [2023])
        self.assertNotIn(f"hmda:orig:v1:{WAFD_LEI}:2022", self.store)
        self.assertIn(f"hmda:orig:v1:{WAFD_LEI}:2023", self.store)
        # Next call retries the failed year instead of serving a tombstone.
        self.fail_years.clear()
        out = hmda_client.originations_by_year(WAFD_LEI, [2022, 2023])
        self.assertEqual(out[2022]["count"], 3903)

    def test_exception_during_fetch_returns_no_year(self):
        with mock.patch("data.http.get_with_retry",
                        side_effect=requests.Timeout("slow")):
            out = hmda_client.originations_by_year(WAFD_LEI, [2023])
        self.assertEqual(out, {})
        self.assertNotIn(f"hmda:orig:v1:{WAFD_LEI}:2023", self.store)


class TestLatestBreakdown(HmdaClientTestCase):

    def test_state_grouping_math(self):
        rows = hmda_client.latest_breakdown(WAFD_LEI, 2023, by="state")
        # Hand-computed from the 5 fixture rows:
        #   WA: 3 loans, 75,000 + 225,000 + 300,000 = 600,000
        #   OR: 2 loans, 415,000 + 185,000        = 600,000
        self.assertEqual(rows, [
            {"state": "WA", "count": 3, "volume_usd": 600000.0},
            {"state": "OR", "count": 2, "volume_usd": 600000.0},
        ])

    def test_county_grouping_math(self):
        rows = hmda_client.latest_breakdown(WAFD_LEI, 2023, by="county")
        by_fips = {r["county"]: r for r in rows}
        self.assertEqual(by_fips["53033"],
                         {"county": "53033", "state": "WA", "count": 2,
                          "volume_usd": 525000.0})
        self.assertEqual(by_fips["53073"]["volume_usd"], 75000.0)
        self.assertEqual(len(rows), 4)
        # Sorted by count desc — the 2-loan county leads.
        self.assertEqual(rows[0]["county"], "53033")

    def test_invalid_by_raises(self):
        with self.assertRaises(ValueError):
            hmda_client.latest_breakdown(WAFD_LEI, 2023, by="msa")

    def test_empty_csv_is_genuine_no_data(self):
        self.csv_text = CSV_HEADER + "\n"      # header only: nothing filed
        rows = hmda_client.latest_breakdown(WAFD_LEI, 2023, by="state")
        self.assertEqual(rows, [])
        # Genuine no-data IS cacheable (it is real data, not a failure).
        self.assertIn(f"hmda:breakdown:v1:{WAFD_LEI}:2023:state", self.store)

    def test_failed_fetch_returns_none_and_not_cached(self):
        with mock.patch("data.http.get_with_retry",
                        side_effect=requests.ConnectionError("down")):
            rows = hmda_client.latest_breakdown(WAFD_LEI, 2023, by="state")
        self.assertIsNone(rows)
        self.assertNotIn(f"hmda:breakdown:v1:{WAFD_LEI}:2023:state",
                         self.store)

    def test_unparseable_amount_refuses_partial_totals(self):
        # One corrupt loan_amount must fail the whole breakdown (n/a),
        # never a silently-partial volume.
        self.csv_text = (CSV_HEADER + "\n" + CSV_ROWS[0] + "\n"
                         + CSV_ROWS[1].replace("225000.0", "Exempt") + "\n")
        rows = hmda_client.latest_breakdown(WAFD_LEI, 2023, by="state")
        self.assertIsNone(rows)
        self.assertNotIn(f"hmda:breakdown:v1:{WAFD_LEI}:2023:state",
                         self.store)

    def test_breakdown_cached_and_served_offline(self):
        hmda_client.latest_breakdown(WAFD_LEI, 2023, by="state")
        n_calls = len(self.http_calls)
        rows = hmda_client.latest_breakdown(WAFD_LEI, 2023, by="state")
        self.assertEqual(len(self.http_calls), n_calls)
        self.assertEqual(rows[0]["state"], "WA")


class TestPanelZipParsing(unittest.TestCase):
    """_parse_panel_zip against the real panel column layout — no stubs."""

    def test_parse_real_shape(self):
        got = hmda_client._parse_panel_zip(_panel_zip(PANEL_2023_ROWS))
        self.assertEqual(got, {"656377": WAFD_LEI, "352772": BANR_LEI})

    def test_missing_columns_raise(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("panel.csv", "foo,bar\n1,2\n")
        with self.assertRaises(ValueError):
            hmda_client._parse_panel_zip(buf.getvalue())


if __name__ == "__main__":
    unittest.main()
