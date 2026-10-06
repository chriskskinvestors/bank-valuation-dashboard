"""
RC-B Memorandum 2 securities ladder — MBS coverage (review 2026-10-05 P1-14).

The ladder used to read only Memo 2.a (A549–A554), which excludes 1-4 family
residential mortgage pass-throughs (Memo 2.b, A555–A560) and other MBS/CMOs
(Memo 2.c, A561/A562) — a minority slice of an MBS-heavy book (ONB ~72% MBS,
WTFC ~92%). Item titles verified against the Federal Reserve MDRM data
dictionary 2026-10-06.

Pinned here with HAND-COMPUTED values:
  * 2.b adds into the same six buckets exactly;
  * 2.c is placed by the owner's stated assumption (A561 → 1–3y; A562 split
    evenly 3–5y / 5–15y), and the placed dollars are exposed;
  * a sub-item absent from the filing stays None (never read as $0) and the
    label says the ladder excludes it;
  * an OLD stored ladder (2.a only, no composition) is labeled Memo 2.a-only;
  * the composition survives the call_report_securities round-trip;
  * maturity_ladder_to_yearly_pace on the new ladder.

Run:  python -m unittest tests.test_ffiec_ladder_mbs
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))


def _frame(values: dict[str, float]) -> pd.DataFrame:
    """ffiec-data-connect long-form frame ($thousands)."""
    return pd.DataFrame([
        {"mdrm": code, "rssd": 1, "quarter": "06/30/2026",
         "data_type": "int", "int_data": v, "float_data": None}
        for code, v in values.items()
    ])


# $thousands. Each Memo 2 sub-item totals 1,000 → book total 3,000.
M2A = {"RCONA549": 100, "RCONA550": 200, "RCONA551": 300,
       "RCONA552": 100, "RCONA553": 200, "RCONA554": 100}
M2B = {"RCONA555": 50, "RCONA556": 0, "RCONA557": 100,
       "RCONA558": 200, "RCONA559": 400, "RCONA560": 250}
M2C = {"RCONA561": 600, "RCONA562": 400}

# Bucket $K, by hand:
#   le_3mo  = 100 + 50                       = 150
#   3mo_1y  = 200 + 0                        = 200
#   1y_3y   = 300 + 100 + 600 (A561, all)    = 1000
#   3y_5y   = 100 + 200 + 200 (A562 × 0.5)   = 500
#   5y_15y  = 200 + 400 + 200 (A562 × 0.5)   = 800
#   gt_15y  = 100 + 250                      = 350      Σ = 3000
EXPECTED_K = {"le_3mo": 150, "3mo_1y": 200, "1y_3y": 1000,
              "3y_5y": 500, "5y_15y": 800, "gt_15y": 350}


def _ladder(values):
    from data.ffiec_client import get_securities_maturity_ladder
    return get_securities_maturity_ladder(
        1, "06/30/2026", call_report_df=_frame(values))


class TestFullMemo2Ladder(unittest.TestCase):

    def setUp(self):
        self.lad = _ladder({**M2A, **M2B, **M2C})

    def test_total_includes_mbs(self):
        # Pre-fix: 1,000,000 (2.a only).
        self.assertEqual(self.lad["total_usd"], 3_000_000)

    def test_bucket_amounts_and_fractions(self):
        for k, v in EXPECTED_K.items():
            self.assertAlmostEqual(self.lad["amounts_usd"][k], v * 1000, places=6, msg=k)
            self.assertAlmostEqual(self.lad["buckets"][k], v / 3000, places=12, msg=k)
        self.assertAlmostEqual(sum(self.lad["buckets"].values()), 1.0, places=12)

    def test_midpoint_maturity(self):
        # Midpoints .125/.625/2/4/10/22.5:
        # 150×.125 + 200×.625 + 1000×2 + 500×4 + 800×10 + 350×22.5
        # = 18.75 + 125 + 2000 + 2000 + 8000 + 7875 = 20018.75; /3000 = 6.6729
        self.assertEqual(self.lad["weighted_avg_duration_years"], 6.67)

    def test_composition_and_assumption_exposed(self):
        self.assertEqual(self.lad["component_usd"],
                         {"m2a": 1_000_000, "m2b": 1_000_000, "m2c": 1_000_000})
        self.assertEqual(self.lad["assumed_usd"], 1_000_000)
        self.assertIn("≤3y → 1–3y", self.lad["assumption"])
        self.assertIn(">3y → 3–15y", self.lad["assumption"])

    def test_coverage_note(self):
        from data.ffiec_client import securities_ladder_coverage_note
        note = securities_ladder_coverage_note(self.lad)
        self.assertIn("2.a + 2.b + 2.c", note)
        self.assertIn("includes $1.00M of other MBS placed by assumption", note)
        self.assertIn("avg life ≤3y → 1–3y; >3y → 3–15y", note)
        self.assertNotIn("excludes", note)

    def test_yearly_pace_on_new_ladder(self):
        from data.ffiec_client import maturity_ladder_to_yearly_pace
        pace = maturity_ladder_to_yearly_pace(self.lad)
        # Y1 = (150+200)/3000                 = 0.11667
        # Y2 = Y1 + ½·1000/3000 = 850/3000    = 0.28333
        # Y3 = Y2 + ½·1000/3000 = 1350/3000   = 0.45
        # Y4 = Y3 + ½·500/3000  = 1600/3000   = 0.53333
        # Y5 = Y4 + ½·500/3000  = 1850/3000   = 0.61667
        self.assertEqual(pace, {1: 0.1167, 2: 0.2833, 3: 0.45,
                                4: 0.5333, 5: 0.6167})

    def test_rcfd_beats_rcon_per_code(self):
        # Consolidated filer: RCFD (larger) wins over RCON for the same item.
        lad = _ladder({**M2A, **M2B, **M2C, "RCFDA559": 700})
        self.assertAlmostEqual(lad["amounts_usd"]["5y_15y"], (800 - 400 + 700) * 1000)
        self.assertEqual(lad["component_usd"]["m2b"], 1_300_000)


class TestMissingSubItems(unittest.TestCase):

    def test_2a_only_filing_excludes_mbs_honestly(self):
        from data.ffiec_client import securities_ladder_coverage_note
        lad = _ladder(M2A)
        self.assertEqual(lad["total_usd"], 1_000_000)
        self.assertEqual(lad["component_usd"],
                         {"m2a": 1_000_000, "m2b": None, "m2c": None})
        self.assertIsNone(lad["assumed_usd"])          # absent, not $0
        note = securities_ladder_coverage_note(lad)
        self.assertIn("excludes", note)
        self.assertIn("Memo 2.b", note)
        self.assertIn("Memo 2.c", note)
        self.assertIn("not in this filing", note)
        self.assertNotIn("all accruing", note)
        self.assertNotIn("placed by assumption", note)

    def test_zero_other_mbs_reported_is_full_coverage_no_assumption(self):
        from data.ffiec_client import securities_ladder_coverage_note
        lad = _ladder({**M2A, **M2B, "RCONA561": 0, "RCONA562": 0})
        self.assertEqual(lad["assumed_usd"], 0)
        note = securities_ladder_coverage_note(lad)
        self.assertIn("2.a + 2.b + 2.c", note)
        self.assertNotIn("placed by assumption", note)

    def test_no_memo2_codes_is_none(self):
        self.assertIsNone(_ladder({"RCONA564": 10}))


class TestOldStoredLadderLabel(unittest.TestCase):

    def test_legacy_shape_labeled_2a_only(self):
        from data.ffiec_client import securities_ladder_coverage_note
        old = {"reporting_period": "03/31/2026",
               "buckets": {"le_3mo": 0.5, "3mo_1y": 0.5},
               "amounts_usd": {"le_3mo": 5e8, "3mo_1y": 5e8},
               "total_usd": 1_000_000_000,
               "weighted_avg_duration_years": 0.38, "source": "ffiec"}
        note = securities_ladder_coverage_note(old)
        self.assertTrue(note.startswith("RC-B Memo 2.a only"), note)
        self.assertIn("excludes 1-4 family residential mortgage pass-throughs", note)
        self.assertIn("other MBS", note)

    def test_no_ladder_no_note(self):
        from data.ffiec_client import securities_ladder_coverage_note
        self.assertEqual(securities_ladder_coverage_note(None), "")


class TestStoreRoundTrip(unittest.TestCase):

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        import data.db as db
        import data.call_report_store as store
        self._db, self._store = db, store
        self._saved = (db._engine, store._engine)
        db._engine = create_engine("sqlite://",
                                   connect_args={"check_same_thread": False},
                                   poolclass=StaticPool, future=True)
        store._engine = None

    def tearDown(self):
        self._db._engine.dispose()
        self._db._engine, self._store._engine = self._saved

    def test_composition_survives_and_amounts_stay_clean(self):
        lad = _ladder({**M2A, **M2B, **M2C})
        self.assertEqual(self._store.upsert_securities_ladder(5000, 1, lad), 1)
        got = self._store.get_latest_ladder(5000)
        self.assertEqual(got["component_usd"], lad["component_usd"])
        self.assertEqual(got["assumed_usd"], 1_000_000)
        self.assertEqual(got["assumption"], lad["assumption"])
        # Reserved key lifted out — amounts_usd is buckets only.
        self.assertEqual(set(got["amounts_usd"]), set(EXPECTED_K))
        self.assertEqual(got["total_usd"], 3_000_000)
        self.assertEqual(got["weighted_avg_duration_years"], 6.67)

    def test_legacy_row_reads_back_without_composition(self):
        legacy = {"reporting_period": "03/31/2026",
                  "buckets": {"le_3mo": 1.0}, "amounts_usd": {"le_3mo": 1e9},
                  "total_usd": 1_000_000_000, "weighted_avg_duration_years": 0.13}
        self._store.upsert_securities_ladder(5001, 2, legacy)
        got = self._store.get_latest_ladder(5001)
        self.assertNotIn("component_usd", got)
        from data.ffiec_client import securities_ladder_coverage_note
        self.assertTrue(securities_ladder_coverage_note(got).startswith(
            "RC-B Memo 2.a only"))


if __name__ == "__main__":
    unittest.main()
