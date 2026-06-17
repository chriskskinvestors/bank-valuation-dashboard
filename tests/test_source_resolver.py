"""Tests for the freshest-wins source resolver (data/source_resolver.py).

Hand-computed expectations for every selection rule — the resolver decides what
the user sees, so its tie-breaks and None-handling are pinned exactly.
"""
import unittest
from datetime import date

from data.source_resolver import SourceRecord, resolve, first_available


def _rec(value, as_of, source, **kw):
    return SourceRecord(value=value, as_of=as_of, source=source, **kw)


class TestResolve(unittest.TestCase):
    def test_latest_as_of_wins_regardless_of_order(self):
        older = lambda: _rec(11.5, date(2025, 9, 30), "FDIC Call Report")
        newer = lambda: _rec(11.99, date(2025, 12, 31), "SEC 10-K")
        # newer listed second AND first — date decides either way
        self.assertEqual(resolve([older, newer]).value, 11.99)
        self.assertEqual(resolve([newer, older]).value, 11.99)

    def test_dated_beats_undated(self):
        dated = lambda: _rec(13.89, date(2025, 12, 31), "SEC 10-K")
        undated = lambda: _rec(99.9, None, "FMP (live market data)")
        self.assertEqual(resolve([undated, dated]).source, "SEC 10-K")

    def test_tie_breaks_to_fresher_source_class_first_in_list(self):
        ir = lambda: _rec(1.01, date(2025, 12, 31), "IR earnings release")
        sec = lambda: _rec(1.02, date(2025, 12, 31), "SEC 10-K")
        # same as_of → the provider listed first (fresher class) wins
        self.assertEqual(resolve([ir, sec]).source, "IR earnings release")
        self.assertEqual(resolve([sec, ir]).source, "SEC 10-K")

    def test_none_record_skipped(self):
        absent = lambda: None
        present = lambda: _rec(7.0, date(2025, 6, 30), "FDIC Call Report")
        self.assertEqual(resolve([absent, present]).value, 7.0)

    def test_value_none_treated_as_absent(self):
        # A record whose value is None must never be surfaced (cardinal rule):
        empty = lambda: _rec(None, date(2026, 1, 1), "SEC 10-K")
        real = lambda: _rec(5.5, date(2025, 12, 31), "FDIC Call Report")
        got = resolve([empty, real])
        self.assertEqual(got.value, 5.5)
        self.assertEqual(got.source, "FDIC Call Report")

    def test_all_absent_returns_none(self):
        self.assertIsNone(resolve([lambda: None, lambda: _rec(None, None, "x")]))

    def test_empty_provider_list_returns_none(self):
        self.assertIsNone(resolve([]))

    def test_provider_exception_is_isolated(self):
        def boom():
            raise RuntimeError("source down")
        good = lambda: _rec(2.0, date(2025, 12, 31), "SEC 10-K")
        self.assertEqual(resolve([boom, good]).value, 2.0)

    def test_two_undated_records_break_to_first(self):
        a = lambda: _rec("a", None, "first")
        b = lambda: _rec("b", None, "second")
        self.assertEqual(resolve([a, b]).source, "first")


class TestSourceRecord(unittest.TestCase):
    def test_display_asof_defaults_to_iso_date(self):
        r = _rec(1.0, date(2025, 12, 31), "SEC 10-K")
        self.assertEqual(r.display_asof, "2025-12-31")

    def test_explicit_display_asof_preserved(self):
        r = SourceRecord(1.0, date(2025, 12, 31), "SEC 10-K",
                         display_asof="FY2025 (filed Jan 30, 2026)")
        self.assertEqual(r.display_asof, "FY2025 (filed Jan 30, 2026)")

    def test_undated_record_has_empty_display_by_default(self):
        r = _rec(1.0, None, "FMP")
        self.assertEqual(r.display_asof, "")


class TestFirstAvailable(unittest.TestCase):
    def test_returns_first_answering_in_order_not_freshest(self):
        # first_available ignores recency: the first non-None answer wins even if
        # a later provider is newer (used where ordering already encodes preference).
        stale_first = lambda: _rec("holdco", date(2025, 9, 30), "SEC 10-K")
        newer_second = lambda: _rec("banksub", date(2025, 12, 31), "FDIC")
        self.assertEqual(first_available([stale_first, newer_second]).value, "holdco")

    def test_skips_absent_then_returns_next(self):
        self.assertEqual(
            first_available([lambda: None, lambda: _rec(3, date(2025, 1, 1), "x")]).value, 3)

    def test_all_absent_returns_none(self):
        self.assertIsNone(first_available([lambda: None, lambda: None]))


if __name__ == "__main__":
    unittest.main()
