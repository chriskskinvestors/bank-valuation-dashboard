"""Unit tests for SOD branch-fetch pagination.

Pins the mega-bank truncation bug: fetch_branches used a single request
with limit 500 and no offset pagination, so any bank with more than 500
branches (WFC 4,214 / JPM 4,993 / BAC 3,640 in the 2025 survey) was
silently truncated in the branches store — deterministically the same
first page every run, which is why Wells Fargo appeared in some of its
markets and not others.

Run: python -m unittest tests.test_sod_pagination
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.sod_client as sod_client  # noqa: E402


class _FakeResp:
    def __init__(self, rows):
        self._rows = rows

    def json(self):
        return {"data": [{"data": r} for r in self._rows]}

    def raise_for_status(self):
        pass


def _branch(brnum: int) -> dict:
    return {"CERT": 3511, "YEAR": 2025, "BRNUM": brnum,
            "DEPSUMBR": 1000 + brnum}


class TestFetchBranchesPagination(unittest.TestCase):
    def test_fetches_all_pages_past_the_page_limit(self):
        # 8 branches, page size 3 → pages of 3, 3, 2 at offsets 0, 3, 6.
        all_rows = [_branch(i) for i in range(8)]
        calls = []

        def fake_get(url, params, **kw):
            calls.append(dict(params))
            off = params.get("offset", 0)
            return _FakeResp(all_rows[off:off + params["limit"]])

        with mock.patch.object(sod_client, "_get_with_retry", fake_get), \
             mock.patch.object(sod_client, "_PAGE_LIMIT", 3):
            df = sod_client.fetch_branches(3511, year=2025)

        self.assertEqual(len(df), 8)
        self.assertEqual(sorted(df["BRNUM"]), list(range(8)))
        self.assertEqual([c.get("offset", 0) for c in calls], [0, 3, 6])
        # deposits survive numeric conversion: 1000..1007 sum hand-computed
        self.assertEqual(int(df["DEPSUMBR"].sum()), 8 * 1000 + sum(range(8)))

    def test_single_short_page_needs_one_request(self):
        rows = [_branch(i) for i in range(2)]
        calls = []

        def fake_get(url, params, **kw):
            calls.append(dict(params))
            return _FakeResp(rows)

        with mock.patch.object(sod_client, "_get_with_retry", fake_get), \
             mock.patch.object(sod_client, "_PAGE_LIMIT", 3):
            df = sod_client.fetch_branches(21943, year=2025)

        self.assertEqual(len(df), 2)
        self.assertEqual(len(calls), 1)

    def test_failed_later_page_returns_empty_not_partial(self):
        # Partial data is truncation all over again — the contract is
        # all-or-nothing so the refresh job counts the bank as failed and
        # the store keeps its previous complete rows.
        all_rows = [_branch(i) for i in range(5)]

        def fake_get(url, params, **kw):
            off = params.get("offset", 0)
            if off > 0:
                raise ConnectionError("throttled")
            return _FakeResp(all_rows[:params["limit"]])

        with mock.patch.object(sod_client, "_get_with_retry", fake_get), \
             mock.patch.object(sod_client, "_PAGE_LIMIT", 3):
            df = sod_client.fetch_branches(3511, year=2025)

        self.assertTrue(df.empty)

    def test_no_rows_returns_empty(self):
        with mock.patch.object(sod_client, "_get_with_retry",
                               lambda url, params, **kw: _FakeResp([])):
            df = sod_client.fetch_branches(99999, year=2025)
        self.assertTrue(df.empty)


if __name__ == "__main__":
    unittest.main()
