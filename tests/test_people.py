"""Unit tests for the People Summary data layer (data/people).

The extraction guards are the correctness surface: a hallucinated person,
age, or year must never render. Run: python -m unittest tests.test_people
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.people import (  # noqa: E402
    _slice_people_sections,
    _parse_people_json,
    _guard_people,
    _latest_proxy,
    get_insider_roster,
)

_PROXY = ("... PROPOSAL 1 — ELECTION OF DIRECTORS. Jane Q. Doe, age 61, has "
          "served as a director since 2015 and chairs the Audit Committee. "
          "EXECUTIVE OFFICERS. John Roe, 54, is our Chief Financial Officer. ...")


class TestParse(unittest.TestCase):
    def test_plain_json(self):
        out = _parse_people_json('[{"name": "Jane Q. Doe", "age": 61}]')
        self.assertEqual(out[0]["name"], "Jane Q. Doe")

    def test_fenced_json_and_preamble(self):
        raw = 'Here you go:\n```json\n[{"name": "John Roe"}]\n```'
        self.assertEqual(_parse_people_json(raw)[0]["name"], "John Roe")

    def test_garbage_is_empty(self):
        self.assertEqual(_parse_people_json("no json here"), [])
        self.assertEqual(_parse_people_json('{"name": "not a list"}'), [])
        self.assertEqual(_parse_people_json(""), [])

    def test_rows_without_name_dropped(self):
        self.assertEqual(_parse_people_json('[{"age": 60}, {"name": "A B"}]'),
                         [{"name": "A B"}])


class TestGuards(unittest.TestCase):
    def test_hallucinated_person_dropped(self):
        people = [{"name": "Jane Q. Doe", "age": 61},
                  {"name": "Invented Person", "age": 50}]
        out = _guard_people(people, _PROXY)
        self.assertEqual([p["name"] for p in out], ["Jane Q. Doe"])

    def test_out_of_range_age_and_year_become_none(self):
        people = [{"name": "Jane Q. Doe", "age": 500,
                   "director_since": 1492}]
        out = _guard_people(people, _PROXY)
        self.assertIsNone(out[0]["age"])
        self.assertIsNone(out[0]["director_since"])

    def test_valid_fields_survive(self):
        people = [{"name": "Jane Q. Doe", "age": 61, "director_since": 2015,
                   "independent": True, "committees": ["Audit"],
                   "role": "director", "position": "Chair",
                   "bio": "Former bank examiner."}]
        out = _guard_people(people, _PROXY)
        p = out[0]
        self.assertEqual((p["age"], p["director_since"], p["independent"]),
                         (61, 2015, True))
        self.assertEqual(p["committees"], ["Audit"])
        self.assertEqual(p["role"], "director")

    def test_nonbool_independent_and_bad_role_none(self):
        people = [{"name": "John Roe", "independent": "yes", "role": "CEO"}]
        out = _guard_people(people, _PROXY)
        self.assertIsNone(out[0]["independent"])
        self.assertIsNone(out[0]["role"])

    def test_short_surname_dropped(self):
        # Two-char surnames match text too easily to be a real guard.
        self.assertEqual(_guard_people([{"name": "X Li"}], _PROXY), [])


class TestSlice(unittest.TestCase):
    def test_short_text_passes_through(self):
        self.assertEqual(_slice_people_sections("abc", max_chars=100), "abc")

    def test_anchored_slice(self):
        text = ("x" * 50_000) + "ELECTION OF DIRECTORS then the roster..." + ("y" * 100_000)
        out = _slice_people_sections(text, max_chars=10_000)
        self.assertIn("ELECTION OF DIRECTORS", out.upper())
        self.assertEqual(len(out), 10_000)

    def test_no_anchor_takes_head(self):
        text = "z" * 100_000
        self.assertEqual(len(_slice_people_sections(text, max_chars=5_000)), 5_000)


class TestLatestProxy(unittest.TestCase):
    def test_reads_recent_filings_key(self):
        # Regression: get_filing_info returns filings under "recent_filings",
        # not "filings" — the wrong key silently yielded no proxy for every
        # bank (caught live on WAL during the build).
        info = {"recent_filings": [
            {"form": "10-K", "accession": "a1"},
            {"form": "DEF 14A", "accession": "a2", "date": "2026-04-22"},
            {"form": "DEF 14A", "accession": "a3", "date": "2025-04-20"},
        ]}
        with patch("data.sec_client.get_filing_info", return_value=info):
            p = _latest_proxy(123)
        self.assertEqual(p["accession"], "a2")  # newest-first order preserved

    def test_no_proxy_is_none(self):
        with patch("data.sec_client.get_filing_info",
                   return_value={"recent_filings": [{"form": "10-K"}]}):
            self.assertIsNone(_latest_proxy(123))


class TestInsiderRoster(unittest.TestCase):
    def test_aggregates_latest_role_officers_first(self):
        txs = [
            {"insider": "DOE JANE", "role": "Director", "date": "2026-06-01"},
            {"insider": "ROE JOHN", "role": "EVP, CFO", "date": "2026-05-01"},
            {"insider": "DOE JANE", "role": "Director", "date": "2026-07-01"},
        ]
        with patch("data.form4_client.fetch_insider_trades", return_value=txs):
            roster = get_insider_roster(123)
        self.assertEqual([r["name"] for r in roster], ["ROE JOHN", "DOE JANE"])
        self.assertEqual(roster[1]["latest_date"], "2026-07-01")

    def test_empty_cik(self):
        self.assertEqual(get_insider_roster(None), [])


if __name__ == "__main__":
    unittest.main()
