"""
CET1 field semantics (found 2026-09-22 chasing the JPM Capital Adequacy crash).

Two plausible-wrong numbers shipped from one wrong note ("RBCT1J = CET1 $",
checked only on TCBK/BANR, which have no AT1 so CET1 == Tier 1 there):

  1. The Capital Adequacy statement's "Common Equity Tier 1 (CET1) Capital"
     line was RBCT1J — total Tier 1 — so any bank with bank-level preferred
     (OZK, USB, JPM) showed Tier 1 under the CET1 label and a fabricated $0
     of Additional Tier 1. Live 2026-06-30 OZK (cert 110): RBCT1C 5,300,530
     ($K), RBCT1J = RBCT1 = 5,639,510, RWAJ 44,916,234, IDT1CER 11.8009%.
     5,300,530 / 44,916,234 = 11.8009% — RBCT1C is CET1 capital.
  2. FDIC reports RBCT1C and IDT1CER as a literal 0 (not null) for quarters
     before the CET1 line existed — OZK 2014-12-31: RBCT1C 0, IDT1CER 0,
     RBCT1J 824,120 — so every deep-range view would chart a 0% CET1 ratio
     and a $0 CET1 line for 2014 and earlier.

Pins (pure, no network):
  * null_unreported_cet1: both-zero-with-Tier-1-present → both None; a real
    2015Q1 record is untouched; a stored row that never had RBCT1C still gets
    its 0% ratio nulled; a record with NO Tier 1 is left alone (no inference).
  * fetch_financials applies the rule at the boundary (mocked HTTP): the 2014
    row is NaN, the 2015 row keeps its hand-checked values.
  * the history store applies it on read, so rows stored before the rule
    (prod: 90k rows) come back clean without a re-backfill.
  * the statement spec: CET1 $ and CET1 growth read RBCT1C; AT1 = RBCT1 −
    RBCT1C; RBCT1C is in the fetched field set (spec rows never render dead).
  * the backfill job's `refill` mode re-pulls a cert that `backfill` would
    skip as already deep (stored rows only carry the fields requested when
    written — RBCT1C is absent from every pre-2026-09-22 row).
"""
from __future__ import annotations

import math
import unittest
from unittest.mock import patch

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from data.fdic_client import null_unreported_capital  # noqa: E402

null_unreported_cet1 = null_unreported_capital   # the CET1 rule kept its pins

# OZK cert 110, live FDIC 2026-09-24: before the 1990 risk-based capital
# rules FDIC reports the TOTAL capital ratio as 0 with RWA / RBC / Tier 1
# ratio all null — the MAX-range table printed "0.00%" for FY1986–FY1989.
_OZK_1988Q4 = {"CERT": 110, "REPDTE": "19881231", "RBCT1": 2660, "RBCT1J": 2660,
               "RBC": None, "RBCT2": None, "RWAJ": None, "RBCRWAJ": 0,
               "RBC1RWAJ": None, "IDT1CER": 0, "RBCT1C": None, "RBCT1JR": 7.509}
_OZK_1990Q4 = {"CERT": 110, "REPDTE": "19901231", "RBCT1": 2942, "RBCT1J": 2942,
               "RBC": 3321, "RBCT2": 379, "RWAJ": 30334.3,
               "RBCRWAJ": 10.948002755956129, "RBC1RWAJ": 9.7, "IDT1CER": 0,
               "RBCT1C": None}

# OZK cert 110, live FDIC 2026-09-22.
_OZK_2014Q4 = {"CERT": 110, "REPDTE": "20141231", "RBCT1C": 0, "IDT1CER": 0,
               "RBCT1J": 824120, "RBCT1": 824120}
_OZK_2015Q1 = {"CERT": 110, "REPDTE": "20150331", "RBCT1C": 1108508,
               "IDT1CER": 12.624566329061132, "RBCT1J": 1108508, "RBCT1": 1108508}
_OZK_2026Q2 = {"CERT": 110, "REPDTE": "20260630", "RBCT1C": 5300530,
               "IDT1CER": 11.800922579573344, "RBCT1J": 5639510,
               "RBCT1": 5639510, "RWAJ": 44916234}


class TestNullUnreportedCet1(unittest.TestCase):
    def test_pre_basel_zeros_become_na(self):
        rec = null_unreported_cet1(dict(_OZK_2014Q4))
        self.assertIsNone(rec["RBCT1C"])
        self.assertIsNone(rec["IDT1CER"])
        self.assertEqual(rec["RBCT1J"], 824120)        # Tier 1 untouched

    def test_reported_quarter_untouched(self):
        for src in (_OZK_2015Q1, _OZK_2026Q2):
            rec = null_unreported_cet1(dict(src))
            self.assertEqual(rec, src)

    def test_stored_row_without_rbct1c_still_nulls_the_zero_ratio(self):
        rec = null_unreported_cet1({"IDT1CER": 0, "RBCT1J": 824120})
        self.assertIsNone(rec["IDT1CER"])
        self.assertNotIn("RBCT1C", rec)                # no key invented

    def test_no_tier1_means_no_inference(self):
        """Zero CET1 with zero/absent Tier 1 is not evidence of anything —
        leave the record as FDIC gave it."""
        rec = null_unreported_cet1({"RBCT1C": 0, "IDT1CER": 0, "RBCT1J": 0})
        self.assertEqual(rec["IDT1CER"], 0)
        rec = null_unreported_cet1({"RBCT1C": 0, "IDT1CER": 0})
        self.assertEqual(rec["RBCT1C"], 0)

    def test_cet1_dollars_present_but_ratio_zero_is_left_alone(self):
        """Only the both-zero signature is the pre-Basel pattern."""
        rec = null_unreported_cet1({"RBCT1C": 500, "IDT1CER": 0, "RBCT1J": 600})
        self.assertEqual(rec["IDT1CER"], 0)


class TestPre1990RiskBasedZeros(unittest.TestCase):
    def test_total_capital_ratio_zero_without_rwa_is_na(self):
        rec = null_unreported_capital(dict(_OZK_1988Q4))
        self.assertIsNone(rec["RBCRWAJ"])
        self.assertIsNone(rec["IDT1CER"])
        self.assertIsNone(rec["RBC1RWAJ"])          # was already null
        self.assertEqual(rec["RBCT1JR"], 7.509)      # leverage is real, untouched
        self.assertEqual(rec["RBCT1"], 2660)

    def test_1990_onward_ratios_kept_and_cet1_still_nulled(self):
        rec = null_unreported_capital(dict(_OZK_1990Q4))
        self.assertAlmostEqual(rec["RBCRWAJ"], 10.948002755956129)
        self.assertEqual(rec["RBC1RWAJ"], 9.7)
        self.assertIsNone(rec["IDT1CER"])           # CET1 line did not exist yet
        self.assertIsNone(rec["RBCT1C"])

    def test_missing_rwa_key_is_unknown_not_absent(self):
        """A record that never carried RWAJ (older cache shapes, fixtures) says
        nothing about the denominator — the ratio is left as reported."""
        rec = null_unreported_capital({"RBCRWAJ": 0, "RBC": 5})
        self.assertEqual(rec["RBCRWAJ"], 0)

    def test_cet1_dollar_line_nulled_even_when_rwa_is_absent(self):
        """Order matters: the RWA rule alone would null the ratio and leave a
        $0 CET1 line behind."""
        rec = null_unreported_capital({"RWAJ": None, "RBCT1C": 0, "IDT1CER": 0,
                                       "RBCRWAJ": 0, "RBCT1J": 100})
        self.assertIsNone(rec["RBCT1C"])
        self.assertIsNone(rec["IDT1CER"])
        self.assertIsNone(rec["RBCRWAJ"])

    def test_a_real_zero_ratio_with_rwa_present_is_kept(self):
        """Only the no-denominator signature is the pre-adoption pattern."""
        rec = null_unreported_capital({"RBCRWAJ": 0, "RWAJ": 1000, "RBC": 0})
        self.assertEqual(rec["RBCRWAJ"], 0)


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class TestFetchBoundary(unittest.TestCase):
    def test_fetch_financials_nulls_2014_keeps_2015(self):
        import data.fdic_client as fc
        payload = {"data": [{"data": dict(_OZK_2015Q1)}, {"data": dict(_OZK_2014Q4)}]}
        with patch.object(fc, "_get_with_retry", return_value=_Resp(payload)):
            df = fc.fetch_financials(110, limit=2)
        self.assertEqual(len(df), 2)
        by = {str(d)[:10]: r for d, r in zip(df["REPDTE"], df.to_dict("records"))}
        r14, r15 = by["2014-12-31"], by["2015-03-31"]
        self.assertTrue(math.isnan(r14["RBCT1C"]) and math.isnan(r14["IDT1CER"]))
        self.assertEqual(r14["RBCT1J"], 824120)
        self.assertEqual(r15["RBCT1C"], 1108508)
        self.assertAlmostEqual(r15["IDT1CER"], 12.624566329061132)

    def test_rbct1c_is_fetched(self):
        from data.fdic_client import _BASE_FINANCIALS_FIELDS
        self.assertIn("RBCT1C", _BASE_FINANCIALS_FIELDS)


class TestStoreReadBoundary(unittest.TestCase):
    """Isolated SQLite store, the same harness tests/test_deep_history uses."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        import data.db as db
        import data.fdic_history_store as store
        self._db, self._store = db, store
        self._saved = (db._engine, store._engine, db.USE_POSTGRES, store._USE_POSTGRES)
        db._engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                   poolclass=StaticPool, future=True)
        db.USE_POSTGRES = False
        store._USE_POSTGRES = False
        store._engine = None
        store.init_history_schema()

    def tearDown(self):
        db, store = self._db, self._store
        db._engine, store._engine, db.USE_POSTGRES, store._USE_POSTGRES = self._saved

    def test_stored_pre_basel_zero_reads_back_as_na(self):
        s = self._store
        # Stored as FDIC gave it (rows written before the rule existed).
        s.upsert_history(110, [dict(_OZK_2014Q4), dict(_OZK_2015Q1), dict(_OZK_1988Q4)])
        recs = {r["REPDTE"]: r for r in s.get_cert_history(110)}
        self.assertIsNone(recs["20141231"]["IDT1CER"])
        self.assertIsNone(recs["20141231"]["RBCT1C"])
        self.assertEqual(recs["20150331"]["RBCT1C"], 1108508)
        self.assertIsNone(recs["19881231"]["RBCRWAJ"])   # pre-1990 total-capital 0


class TestStatementSpec(unittest.TestCase):
    def _rows(self):
        from ui.financials_statements import _CAPITAL_ADEQUACY
        return {row[0]: row for _, rows in _CAPITAL_ADEQUACY for row in rows}

    def test_cet1_line_is_rbct1c_and_at1_is_tier1_minus_cet1(self):
        rows = self._rows()
        self.assertEqual(rows["Common Equity Tier 1 (CET1) Capital"][1:],
                         ("dollar", "RBCT1C"))
        self.assertEqual(rows["Additional Tier 1 Capital"][1:],
                         ("diff", "RBCT1", "RBCT1C"))
        self.assertEqual(rows["» Tier 1 Capital"][1:], ("dollar", "RBCT1"))
        self.assertEqual(rows["CET1 Capital Growth"][1:], ("growth", "RBCT1C"))
        # OZK 2026-06-30 on this spec: AT1 = 5,639,510 − 5,300,530 = 338,980
        # ($K) — no longer a fabricated $0.
        self.assertEqual(_OZK_2026Q2["RBCT1"] - _OZK_2026Q2["RBCT1C"], 338980)

    def test_every_spec_field_is_fetched(self):
        from ui.financials_statements import _CAPITAL_ADEQUACY
        from data.fdic_client import _BASE_FINANCIALS_FIELDS
        from config import get_fdic_fields
        have = _BASE_FINANCIALS_FIELDS | set(get_fdic_fields())
        for _, rows in _CAPITAL_ADEQUACY:
            for row in rows:
                for f in row[2:]:
                    for term in f.replace("-", "+").split("+"):
                        self.assertIn(term, have, f"{term} in row {row[0]!r} is never fetched")


class TestRefillMode(unittest.TestCase):
    def test_refill_repulls_a_cert_backfill_would_skip(self):
        import pandas as pd
        import jobs.backfill_fdic_history as job
        import data.fdic_client as fc
        calls = []

        def fake_fetch(cert, limit=20):
            calls.append((cert, limit))
            return pd.DataFrame([{"CERT": cert, "REPDTE": "19931231", "ASSET": 1}])

        with patch.object(job, "_all_certs", return_value=[5]), \
             patch.object(fc, "fetch_financials", side_effect=fake_fetch), \
             patch.object(job, "_PACE_S", 0), \
             patch("data.fdic_history_store.init_history_schema"), \
             patch("data.fdic_history_store.min_repdte", return_value="19931231"), \
             patch("data.fdic_history_store.upsert_history", return_value=1), \
             patch("data.fdic_history_store.store_counts", return_value=(1, 1)):
            self.assertEqual(job.main("backfill"), 0)
            self.assertEqual(calls, [], "backfill skips a cert already deep")
            self.assertEqual(job.main("refill"), 0)
        self.assertEqual(calls, [(5, job._BACKFILL_LIMIT)], "refill re-pulls it in full")


if __name__ == "__main__":
    unittest.main()
