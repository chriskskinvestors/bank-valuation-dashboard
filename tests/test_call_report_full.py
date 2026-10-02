"""
Unit tests for the full Call Report store (data/call_report_full.py) and its
wiring into jobs/refresh_ffiec._refresh_one.

Fixture frames copy the exact schema ffiec-data-connect 3.0.0 returns from
collect_data(output_type="pandas") (xbrl_processor._process_xml):
mdrm / rssd / quarter ('M/D/YYYY') / data_type / int_data / float_data /
bool_data / str_data. USD facts arrive as data_type 'int' in $thousands; a
blank (nil) fact arrives as data_type 'str' with str_data "None".

Everything runs against an isolated in-memory SQLite engine (the pattern of
tests/test_ri_store.py) — no test touches cache.db, Postgres, or the network.

Run:  python -m unittest tests.test_call_report_full
"""
from __future__ import annotations

import io
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

NA = pd.NA


def _row(code, dtype, value, quarter="6/30/2026", rssd="852218"):
    return {
        "mdrm": code, "rssd": rssd, "quarter": quarter, "data_type": dtype,
        "int_data": value if dtype == "int" else NA,
        "float_data": value if dtype == "float" else NA,
        "bool_data": value if dtype == "bool" else NA,
        "str_data": (str(value) if dtype == "str" else None),
    }


def _frame(quarter="6/30/2026", **over) -> pd.DataFrame:
    """One bank-quarter as the package returns it. Values in $000."""
    vals = {
        "RCFD2170": ("int", 3_500_000),     # total assets, consolidated
        "RCON2170": ("int", 3_000_000),     # total assets, domestic offices
        "RCONB530": ("int", -1_234),        # AOCI (negative)
        "RCFA7206": ("float", 15.25),       # Tier 1 ratio (PURE)
        "RCON6648": ("str", None),          # blank / nil fact
        "TEXT4461": ("str", "Merchant Fee Income"),
        "RCONFT00": ("bool", True),         # yes/no answer
    }
    vals.update(over)
    rows = [_row(c, dt, v, quarter) for c, (dt, v) in vals.items()]
    # A duplicated code: the first occurrence wins (as in _lookup_concept).
    rows.append(_row("RCON2170", "int", 9, quarter))
    return pd.DataFrame(rows)


def _mdrm_zip() -> bytes:
    """Small MDRM zip in the real layout: 'PUBLIC' banner, header row,
    multi-row codes, a quoted multi-line Description, non-Call forms."""
    csv_text = (
        "PUBLIC\r\n"
        "Mnemonic,Item Code,Start Date,End Date,Item Name,Confidentiality,"
        "ItemType,Reporting Form,Description,SeriesGlossary,\r\n"
        # Old then current Call Report rows for RCON2170: current wins.
        '"RCON","2170",3/31/1984 12:00:00 AM,12/31/2000 12:00:00 AM,'
        '"OLD TOTAL ASSETS",N,F,FFIEC 032,"old",,\r\n'
        '"RCON","2170",3/31/2001 12:00:00 AM,12/31/9999 12:00:00 AM,'
        '"TOTAL ASSETS",N,F,FFIEC 041,"The sum of all asset items.&#x0D;\r\n'
        '&#x0D;\r\nsecond line, with a comma",,\r\n'
        # RCFD2170: a non-Call form row with a different name must lose.
        '"RCFD","2170",3/31/1984 12:00:00 AM,12/31/9999 12:00:00 AM,'
        '"TOTAL ASSETS",N,F,FFIEC 031,"x",,\r\n'
        '"RCFD","2170",1/1/2030 12:00:00 AM,12/31/9999 12:00:00 AM,'
        '"NOT A CALL REPORT TITLE",N,F,FR 2886b,"x",,\r\n'
        '"RCON","B530",3/31/2017 12:00:00 AM,12/31/9999 12:00:00 AM,'
        '"ACCUMULATED OTHER COMPREHENSIVE INCOME",N,F,FFIEC 051,"x",,\r\n'
        # FR Y-9C only — not a Call Report code.
        '"BHCK","2170",3/31/1986 12:00:00 AM,12/31/9999 12:00:00 AM,'
        '"TOTAL ASSETS",N,F,FR Y-9C,"x",,\r\n'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("MDRM_CSV.csv", csv_text.encode("utf-8"))
        z.writestr("README File for MDRM.txt", b"readme")
    return buf.getvalue()


class _IsolatedDb(unittest.TestCase):
    """Point data/db (and every store that caches an engine) at one shared
    in-memory SQLite DB for the test."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        import data.cache as cache
        import data.call_report_full as full
        import data.call_report_store as store
        import data.db as db

        self._mods = (db, full, store, cache)
        self._saved = [m._engine for m in self._mods]
        db._engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool, future=True)
        full._engine = store._engine = cache._engine = None
        self.full = full

    def tearDown(self):
        self._mods[0]._engine.dispose()
        for m, e in zip(self._mods, self._saved):
            m._engine = e

    def _count(self, cert=None) -> int:
        from sqlalchemy import text
        sql = "SELECT COUNT(*) FROM call_report_full"
        params = {}
        if cert is not None:
            sql += " WHERE cert = :c"
            params = {"c": cert}
        with self.full._get_engine().connect() as conn:
            return conn.execute(text(sql), params).scalar()


class TestFullReportStore(_IsolatedDb):

    def test_round_trip_values(self):
        f = self.full
        # 7 distinct codes (the duplicate RCON2170 row is dropped).
        self.assertEqual(f.upsert_full_report(628, 852218, "06/30/2026",
                                              _frame()), 7)
        codes = ["RCFD2170", "RCON2170", "RCONB530", "RCFA7206",
                 "RCON6648", "TEXT4461", "RCONFT00", "RIAD4340"]
        got = f.values(codes, "2026-06-30", [628])
        self.assertEqual(got, {628: {
            "RCFD2170": 3_500_000.0,
            "RCON2170": 3_000_000.0,   # first occurrence, not the dup's 9
            "RCONB530": -1_234.0,
            "RCFA7206": 15.25,
            "RCON6648": None,          # blank in the filing → None, never 0
            "TEXT4461": None,          # text item → non-numeric
            "RCONFT00": None,          # bool item → non-numeric
            "RIAD4340": None,          # not in the filing at all
        }})

    def test_rcfd_and_rcon_stay_distinct(self):
        f = self.full
        f.upsert_full_report(628, 852218, "06/30/2026", _frame())
        v = f.values(["RCFD2170", "RCON2170"], "06/30/2026", [628])[628]
        self.assertNotEqual(v["RCFD2170"], v["RCON2170"])
        self.assertEqual((v["RCFD2170"], v["RCON2170"]),
                         (3_500_000.0, 3_000_000.0))

    def test_blank_is_stored_null_not_zero(self):
        from sqlalchemy import text
        f = self.full
        f.upsert_full_report(628, 852218, "06/30/2026", _frame())
        with f._get_engine().connect() as conn:
            row = conn.execute(text(
                "SELECT value, data_type FROM call_report_full "
                "WHERE cert = 628 AND mdrm = 'RCON6648'")).fetchone()
        self.assertIsNone(row[0])
        self.assertEqual(row[1], "str")

    def test_absent_certs_codes_and_dates_are_none(self):
        f = self.full
        f.upsert_full_report(628, 852218, "06/30/2026", _frame())
        got = f.values(["rcfd2170", "RIAD4340"], "2026-06-30", [628, 999])
        # Lower-case request normalized to the stored upper-case code.
        self.assertEqual(got[628], {"RCFD2170": 3_500_000.0,
                                    "RIAD4340": None})
        self.assertEqual(got[999], {"RCFD2170": None, "RIAD4340": None})
        self.assertEqual(f.values(["RCFD2170"], "2026-03-31", [628]),
                         {628: {"RCFD2170": None}})
        self.assertEqual(f.values([], "2026-06-30", [628]), {628: {}})
        self.assertEqual(f.values(["RCFD2170"], "2026-06-30", []), {})

    def test_reupsert_replaces_the_bank_quarter(self):
        f = self.full
        f.upsert_full_report(628, 852218, "06/30/2026", _frame())
        f.upsert_full_report(3510, 480228, "06/30/2026", _frame())
        # Amended filing: RCON2170 revised, RCONB530 dropped.
        amended = _frame(RCON2170=("int", 3_100_000))
        amended = amended[amended["mdrm"] != "RCONB530"]
        self.assertEqual(f.upsert_full_report(628, 852218, "06/30/2026",
                                              amended), 6)
        v = f.values(["RCON2170", "RCONB530"], "2026-06-30", [628, 3510])
        self.assertEqual(v[628], {"RCON2170": 3_100_000.0, "RCONB530": None})
        # The other bank is untouched.
        self.assertEqual(v[3510], {"RCON2170": 3_000_000.0,
                                   "RCONB530": -1_234.0})
        self.assertEqual(self._count(628), 6)
        # Identical re-run is idempotent.
        f.upsert_full_report(628, 852218, "06/30/2026", amended)
        self.assertEqual(self._count(628), 6)
        self.assertEqual(self._count(), 13)

    def test_quarter_mismatch_raises_and_stores_nothing(self):
        """fetch_call_report falls back one quarter for late filers — that
        frame must never be stored under the requested date."""
        f = self.full
        with self.assertRaisesRegex(ValueError, "period mismatch"):
            f.upsert_full_report(628, 852218, "06/30/2026",
                                 _frame(quarter="3/31/2026"))
        self.assertEqual(self._count(), 0)

    def test_empty_frame_writes_nothing_and_keeps_existing(self):
        f = self.full
        f.upsert_full_report(628, 852218, "06/30/2026", _frame())
        self.assertEqual(f.upsert_full_report(628, 852218, "06/30/2026",
                                              pd.DataFrame()), 0)
        self.assertEqual(f.upsert_full_report(628, 852218, "06/30/2026",
                                              None), 0)
        self.assertEqual(self._count(628), 7)

    def test_available_report_dates_newest_first(self):
        f = self.full
        self.assertEqual(f.available_report_dates(), [])
        f.upsert_full_report(628, 852218, "12/31/2025",
                             _frame(quarter="12/31/2025"))
        f.upsert_full_report(628, 852218, "2026-06-30", _frame())
        f.upsert_full_report(3510, 480228, "03/31/2026",
                             _frame(quarter="3/31/2026"))
        self.assertEqual(f.available_report_dates(),
                         ["2026-06-30", "2026-03-31", "2025-12-31"])


class TestMdrmAndCatalog(_IsolatedDb):

    def test_parse_mdrm_zip(self):
        titles = self.full._parse_mdrm_zip(_mdrm_zip())
        self.assertEqual(titles, {
            "RCON2170": "TOTAL ASSETS",   # current row beats the 2000 one
            "RCFD2170": "TOTAL ASSETS",   # FR 2886b row ignored
            "RCONB530": "ACCUMULATED OTHER COMPREHENSIVE INCOME",
        })                                 # BHCK2170 (FR Y-9C) excluded

    def test_catalog_numeric_codes_with_titles(self):
        f = self.full
        f.upsert_full_report(628, 852218, "06/30/2026", _frame())
        with patch.object(f, "_download_mdrm", return_value=_mdrm_zip()):
            cat = f.catalog()
        self.assertEqual(cat, [
            {"code": "RCFA7206", "title": None, "schedule": None,
             "unit": "non_monetary"},      # not in the fixture dictionary
            {"code": "RCFD2170", "title": "TOTAL ASSETS", "schedule": None,
             "unit": "usd_thousands"},
            {"code": "RCON2170", "title": "TOTAL ASSETS", "schedule": None,
             "unit": "usd_thousands"},
            {"code": "RCONB530",
             "title": "ACCUMULATED OTHER COMPREHENSIVE INCOME",
             "schedule": None, "unit": "usd_thousands"},
        ])  # blank-only / text / bool codes are not screenable → excluded

    def test_titles_cached_after_first_download(self):
        f = self.full
        with patch.object(f, "_download_mdrm",
                          return_value=_mdrm_zip()) as dl:
            f.mdrm_titles()
            f.mdrm_titles()
        self.assertEqual(dl.call_count, 1)

    def test_download_unreachable_titles_none(self):
        f = self.full
        f.upsert_full_report(628, 852218, "06/30/2026", _frame())
        with patch.object(f, "_download_mdrm",
                          side_effect=ConnectionError("down")):
            cat = f.catalog()
        self.assertEqual([c["code"] for c in cat],
                         ["RCFA7206", "RCFD2170", "RCON2170", "RCONB530"])
        self.assertTrue(all(c["title"] is None for c in cat))

    def test_stale_titles_beat_nothing_when_download_fails(self):
        import data.cache as cache
        f = self.full
        import time
        cache.put(f._MDRM_CACHE_KEY, {"RCON2170": "TOTAL ASSETS"})
        later = time.time() + f._MDRM_TTL_S + 3600  # past the 30-day TTL
        with patch("data.cache.time.time", return_value=later), \
             patch.object(f, "_download_mdrm",
                          side_effect=ConnectionError("down")) as dl:
            self.assertEqual(f.mdrm_titles(), {"RCON2170": "TOTAL ASSETS"})
        self.assertEqual(dl.call_count, 1)  # stale → a refresh was tried


class TestRefreshJobWiring(_IsolatedDb):

    def _run(self, df, period="06/30/2026"):
        from jobs import refresh_ffiec
        with patch("data.ffiec_client.fetch_call_report", return_value=df):
            return refresh_ffiec._refresh_one(628, 852218, period)

    def test_full_report_persisted_from_the_same_frame(self):
        from jobs import refresh_ffiec
        self.assertIn("full", refresh_ffiec._SCHEDULES)
        self.assertIn("full", refresh_ffiec._SCHEDULE_LABELS)
        _cert, _n, _err, statuses = self._run(_frame())
        self.assertEqual(statuses["full"], "ok")
        self.assertEqual(
            self.full.values(["RCONB530"], "2026-06-30", [628]),
            {628: {"RCONB530": -1_234.0}})

    def test_fallback_quarter_frame_stored_under_its_own_quarter(self):
        """A late filer's previous-quarter fallback frame is stored under
        3/31/2026 (what it is) — never under the requested 6/30/2026."""
        _cert, _n, _err, statuses = self._run(_frame(quarter="3/31/2026"))
        self.assertEqual(statuses["full"], "ok")
        self.assertEqual(self.full.available_report_dates(), ["2026-03-31"])
        self.assertEqual(
            self.full.values(["RCONB530"], "2026-03-31", [628]),
            {628: {"RCONB530": -1_234.0}})

    def test_empty_fetch_reports_no_data(self):
        _cert, _n, err, statuses = self._run(pd.DataFrame())
        self.assertEqual(err, "empty_call_report")
        self.assertEqual(statuses["full"], "no_data")


if __name__ == "__main__":
    unittest.main()
