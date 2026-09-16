"""data.branches_store.get_bank_footprint — post-survey merger union.

Pins the Beacon Financial case (2026-09-16): the latest SOD survey predates
the whole-bank merger, so the survivor cert's roster (28 legacy Brookline
branches) missed the 85 legacy Berkshire branches sitting under the absorbed,
now-inactive cert. The footprint must union same-survey rosters of charters
absorbed AFTER the survey's June-30 as-of date — and ONLY those.

Hermetic: isolated sqlite branches table + patched fdic_structure events.
"""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import data.db as db
import data.branches_store as bs

_SURVIVOR, _ABSORBED, _OLD_DEAL, _STRANGER = 17798, 23621, 900, 901


def _event(date, ocert, name, direction="acquired"):
    return {"date": date, "event_type": 810, "description": "merger",
            "other_institution": {"name": name, "cert": ocert},
            "direction": direction}


class TestBankFootprint(unittest.TestCase):
    def setUp(self):
        self._eng = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self._orig_get_engine = db.get_engine
        db.get_engine = lambda: self._eng
        bs._engine = None
        bs.init_branches_schema()
        rows = [
            # survivor: 2 branches in the 2025 survey (and an older vintage
            # that must NOT leak in)
            (_SURVIVOR, 1, 2025, "BBT", "Brookline Bank", 500),
            (_SURVIVOR, 2, 2025, "BBT", "Brookline Bank", 300),
            (_SURVIVOR, 1, 2024, "BBT", "Brookline Bank", 480),
            # absorbed post-survey: 3 branches under the dead cert, same survey
            (_ABSORBED, 1, 2025, "", "Berkshire Bank", 900),
            (_ABSORBED, 2, 2025, "", "Berkshire Bank", 200),
            (_ABSORBED, 3, 2025, "", "Berkshire Bank", 100),
            # a pre-survey acquisition — its branches already report under the
            # survivor in the 2025 survey, so unioning it would double-count
            (_OLD_DEAL, 1, 2025, "", "Old Deal Bank", 50),
            # unrelated bank
            (_STRANGER, 1, 2025, "", "Stranger Bank", 999),
        ]
        with self._eng.begin() as c:
            for cert, brnum, year, tk, nm, dep in rows:
                c.execute(text(
                    "INSERT INTO branches (cert, brnum, year, ticker, "
                    "bank_name, branch_name, address, city, state, zip, "
                    "county, stcntybr, msa_code, msa_name, deposits) VALUES "
                    "(:cert,:brnum,:year,:tk,:nm,'b','a','c','MA','z','cty',"
                    "'25001','1','m',:dep)"),
                    {"cert": cert, "brnum": brnum, "year": year,
                     "tk": tk, "nm": nm, "dep": dep})

    def tearDown(self):
        db.get_engine = self._orig_get_engine
        bs._engine = None

    def test_post_survey_absorption_is_unioned(self):
        events = [
            _event("2026-02-01", _ABSORBED, "Berkshire Bank"),   # after 2025-06-30
            _event("2024-01-15", _OLD_DEAL, "Old Deal Bank"),    # before → skip
            _event("2026-03-01", _SURVIVOR, "Beacon Bank and Trust",
                   direction="other"),                            # not an acquisition
        ]
        with patch("data.fdic_structure.get_structure_events",
                   return_value=events):
            df, absorbed = bs.get_bank_footprint(_SURVIVOR)
        self.assertEqual(len(df), 5)                    # 2 own + 3 absorbed
        self.assertEqual((df["year"] == 2025).all(), True)
        self.assertEqual(len(absorbed), 1)
        self.assertEqual(absorbed[0]["name"], "Berkshire Bank")
        self.assertEqual(absorbed[0]["n_branches"], 3)
        # deposits desc across the union; absorbed rows carry provenance
        self.assertEqual(int(df.iloc[0]["deposits"]), 900)
        self.assertEqual(df.iloc[0]["legacy_bank"], "Berkshire Bank")
        self.assertIsNone(df[df["legacy_bank"].isna()].iloc[0]["legacy_bank"])
        # the pre-survey deal and the stranger never leak in
        self.assertNotIn(50, df["deposits"].tolist())
        self.assertNotIn(999, df["deposits"].tolist())

    def test_no_events_returns_plain_roster(self):
        with patch("data.fdic_structure.get_structure_events",
                   return_value=[]):
            df, absorbed = bs.get_bank_footprint(_SURVIVOR)
        self.assertEqual(len(df), 2)
        self.assertEqual(absorbed, [])
        self.assertIn("legacy_bank", df.columns)

    def test_structure_fetch_failure_degrades_to_plain_roster(self):
        with patch("data.fdic_structure.get_structure_events",
                   side_effect=RuntimeError("FDIC down")):
            df, absorbed = bs.get_bank_footprint(_SURVIVOR)
        self.assertEqual(len(df), 2)                    # never an error
        self.assertEqual(absorbed, [])

    def test_unknown_cert_stays_empty(self):
        with patch("data.fdic_structure.get_structure_events",
                   return_value=[]):
            df, absorbed = bs.get_bank_footprint(555555)
        self.assertTrue(df.empty)
        self.assertEqual(absorbed, [])


if __name__ == "__main__":
    unittest.main()
