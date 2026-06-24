"""County-level query functions in data.branches_store (Geographic 'By County').

Verifies the new get_branches_by_county / get_banks_by_county / list_counties
against an isolated in-memory SQLite branches table — same SQL the live (Postgres)
store runs, exercised without a network or the real DB.
"""
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import data.db as db
import data.branches_store as bs


# Two counties: Los Angeles (06037) with two banks, Cook IL (17031) with one.
_FIXTURES = [
    # ticker, bank,  state, county,        stcntybr, msa_code, msa_name,        deposits
    ("WFC", "Wells", "CA", "Los Angeles", "06037", "31080", "Los Angeles MSA", 500),
    ("WFC", "Wells", "CA", "Los Angeles", "06037", "31080", "Los Angeles MSA", 300),
    ("JPM", "JPM",   "CA", "Los Angeles", "06037", "31080", "Los Angeles MSA", 900),
    ("JPM", "JPM",   "IL", "Cook",        "17031", "16980", "Chicago MSA",     700),
]


class TestCountyQueries(unittest.TestCase):
    def setUp(self):
        self._eng = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self._orig_get_engine = db.get_engine
        db.get_engine = lambda: self._eng
        bs._engine = None
        bs.init_branches_schema()
        with self._eng.begin() as c:
            for i, (tk, nm, stt, cty, fips, mc, mn, dep) in enumerate(_FIXTURES):
                c.execute(text(
                    "INSERT INTO branches (cert, brnum, year, ticker, bank_name, "
                    "branch_name, address, city, state, zip, county, stcntybr, "
                    "msa_code, msa_name, deposits) VALUES (:cert,:brnum,2024,:tk,:nm,"
                    "'b','a','c',:stt,'z',:cty,:fips,:mc,:mn,:dep)"),
                    {"cert": i, "brnum": i, "tk": tk, "nm": nm, "stt": stt,
                     "cty": cty, "fips": fips, "mc": mc, "mn": mn, "dep": dep})

    def tearDown(self):
        db.get_engine = self._orig_get_engine
        bs._engine = None

    def test_list_counties_sorted_by_state_then_county(self):
        df = bs.list_counties()
        self.assertEqual(list(df["stcntybr"]), ["06037", "17031"])  # CA before IL
        self.assertEqual(list(df["county"]), ["Los Angeles", "Cook"])

    def test_get_branches_by_county_scopes_to_fips(self):
        la = bs.get_branches_by_county("06037")
        self.assertEqual(len(la), 3)                       # 2 WFC + 1 JPM branches
        self.assertTrue((la["stcntybr"] == "06037").all())
        cook = bs.get_branches_by_county("17031")
        self.assertEqual(len(cook), 1)

    def test_get_branches_by_county_ticker_filter(self):
        only_jpm = bs.get_branches_by_county("06037", tickers=["jpm"])  # case-insensitive
        self.assertEqual(len(only_jpm), 1)
        self.assertEqual(only_jpm["ticker"].iloc[0], "JPM")

    def test_get_banks_by_county_aggregates(self):
        df = bs.get_banks_by_county("06037")
        by_ticker = dict(zip(df["ticker"], df["total_deposits"]))
        self.assertEqual(by_ticker["WFC"], 800)            # 500 + 300
        self.assertEqual(by_ticker["JPM"], 900)
        wfc = df[df["ticker"] == "WFC"].iloc[0]
        self.assertEqual(int(wfc["n_branches"]), 2)
        self.assertEqual(wfc["county"], "Los Angeles")     # MAX(county) carried through
        # ordered by deposits desc → JPM (900) first
        self.assertEqual(df["ticker"].iloc[0], "JPM")


if __name__ == "__main__":
    unittest.main()
