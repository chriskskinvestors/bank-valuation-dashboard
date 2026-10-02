"""
Late-filer period integrity for jobs/refresh_ffiec._refresh_one.

data/ffiec_client.fetch_call_report falls back one quarter when a bank has
not filed the requested one yet. Before this fix the job handed that
prior-quarter frame to every per-schedule persister stamped with the
REQUESTED period, so last quarter's RI / RC-N / RC-R / RI-E / deposit-cost /
securities-ladder numbers were stored (and displayed) as this quarter's.

The job now reads the period from the frame's own 'quarter' column (the
ffiec-data-connect 3.0.0 schema — see tests/test_call_report_full.py) and
stores every schedule under it; a frame with no single determinable quarter
is stored nowhere and counted as an error.

Isolated in-memory SQLite (tests/test_call_report_full._IsolatedDb); no
network, no cache.db.

Run:  python -m unittest tests.test_ffiec_period_integrity
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from tests.test_call_report_full import _IsolatedDb, _row  # noqa: E402

CERT, RSSD = 628, 852218
REQUESTED = "06/30/2026"

# One bank-quarter carrying at least one line of every persisted schedule
# ($000, as the package returns USD facts).
_CODES = {
    "RIAD4107": ("int", 5_000),     # RI total interest income
    "RIAD4340": ("int", 1_200),     # RI net income
    "RCON1406": ("int", 70),        # RC-N 30-89 past due, total
    "RCON1403": ("int", 30),        # RC-N nonaccrual, total
    "RCOAP859": ("int", 9_000),     # RC-R CET1
    "RCOA8274": ("int", 9_000),     # RC-R tier 1
    "RIADC017": ("int", 250),       # RI-E data processing
    "RIAD4508": ("int", 40),        # deposit cost: transaction-acct interest
    "RCON3485": ("int", 8_000),     # deposit cost: avg IB transaction (RC-K)
    "RCONA549": ("int", 100),       # RC-B Memo 2 ladder ≤ 3 months
    "RCONA550": ("int", 300),       # RC-B Memo 2 ladder 3 months – 1 year
    "RCONA564": ("int", 600),       # RC-C Memo 2.a loans ≤ 3 months
    "RCONA570": ("int", 400),       # RC-C Memo 2.b loans ≤ 3 months
    "RCONA565": ("int", 1_000),     # RC-C Memo 2.a loans 3 months – 1 year
}

_DETAIL_TABLES = ("ri_income_detail", "rcn_detail", "rcr_capital",
                  "rie_detail", "deposit_cost_detail")
_ALL_TABLES = _DETAIL_TABLES + ("call_report_securities", "call_report_full")


def _frame(quarter) -> pd.DataFrame:
    return pd.DataFrame([_row(c, dt, v, quarter, str(RSSD))
                         for c, (dt, v) in _CODES.items()])


class _JobCase(_IsolatedDb):

    def _run(self, df, period=REQUESTED):
        from jobs import refresh_ffiec
        with patch("data.ffiec_client.fetch_call_report", return_value=df):
            return refresh_ffiec._refresh_one(CERT, RSSD, period)

    def _dates(self, table) -> list[str]:
        from sqlalchemy import text
        import data.call_report_store as store
        self.full._get_engine()  # both stores' schemas exist even if unwritten
        with store._get_engine().connect() as conn:
            return [str(r[0])[:10] for r in conn.execute(text(
                f"SELECT report_date FROM {table} WHERE cert = :c "
                f"GROUP BY report_date ORDER BY report_date"), {"c": CERT})]


class TestLateFilerStoredUnderOwnQuarter(_JobCase):
    """Requested 6/30/2026, bank has only filed 3/31/2026 (the fallback)."""

    def setUp(self):
        super().setUp()
        self.result = self._run(_frame("3/31/2026"))

    def test_every_schedule_ok_and_ladder_written(self):
        _cert, n_buckets, err, statuses = self.result
        self.assertEqual(err, "")
        self.assertEqual(n_buckets, 2)
        self.assertEqual(statuses, {s: "ok" for s in statuses})

    def test_every_table_dated_the_frames_quarter_not_the_request(self):
        for table in _ALL_TABLES:
            with self.subTest(table=table):
                self.assertEqual(self._dates(table), ["2026-03-31"])

    def test_detail_json_period_copy_matches(self):
        from sqlalchemy import text
        import data.call_report_store as store
        with store._get_engine().connect() as conn:
            for table in _DETAIL_TABLES:
                with self.subTest(table=table):
                    raw = conn.execute(text(
                        f"SELECT detail_json FROM {table} WHERE cert = :c"),
                        {"c": CERT}).scalar()
                    self.assertEqual(json.loads(raw)["reporting_period"],
                                     "03/31/2026")

    def test_readers_see_the_values_under_the_true_quarter(self):
        import data.call_report_store as s
        ri = s.get_stored_ri_detail(CERT)
        self.assertEqual([(d["reporting_period"], d["total_int_income"])
                          for d in ri], [("03/31/2026", 5_000.0)])
        rcn = s.get_stored_rcn_detail(CERT)
        self.assertEqual([(d["reporting_period"], d["total_pd30_89"])
                          for d in rcn], [("03/31/2026", 70.0)])
        rcr = s.get_stored_rcr_detail(CERT)
        self.assertEqual([(d["reporting_period"], d["cet1"])
                          for d in rcr], [("03/31/2026", 9_000.0)])
        rie = s.get_stored_rie_detail(CERT)
        self.assertEqual([(d["reporting_period"], d["data_processing"])
                          for d in rie], [("03/31/2026", 250.0)])
        dep = s.get_stored_deposit_cost_detail(CERT)
        self.assertEqual([(d["reporting_period"], d["int_transaction"],
                           d["avg_transaction"]) for d in dep],
                         [("03/31/2026", 40.0, 8_000.0)])

    def test_ladder_values_hand_computed(self):
        import data.call_report_store as s
        lad = s.get_latest_ladder(CERT)
        self.assertEqual(lad["reporting_period"], "03/31/2026")
        # 100 + 300 = 400 ($000) → $400,000; 100/400 = 0.25, 300/400 = 0.75.
        self.assertEqual(lad["total_usd"], 400_000)
        self.assertEqual(lad["buckets"], {"le_3mo": 0.25, "3mo_1y": 0.75})
        # Floating share = ≤3mo loans / all loans = (600+400)/(600+400+1000).
        self.assertEqual(lad["floating_loan_share"], 0.5)


class TestOnTimeFilerUnchanged(_JobCase):

    def test_stored_under_the_requested_quarter(self):
        _cert, _n, err, statuses = self._run(_frame("6/30/2026"))
        self.assertEqual(err, "")
        self.assertEqual(statuses, {s: "ok" for s in statuses})
        for table in _ALL_TABLES:
            with self.subTest(table=table):
                self.assertEqual(self._dates(table), ["2026-06-30"])

    def test_unpadded_override_period_matches_padded_frame(self):
        # FFIEC_PERIOD may be typed '6/30/2026'; the frame normalizes to
        # 06/30/2026 — same quarter, stored there.
        self._run(_frame("6/30/2026"), period="6/30/2026")
        self.assertEqual(self._dates("ri_income_detail"), ["2026-06-30"])

    def test_late_then_on_time_keeps_both_quarters_distinct(self):
        self._run(_frame("3/31/2026"))
        self._run(_frame("6/30/2026"))
        for table in _ALL_TABLES:
            with self.subTest(table=table):
                self.assertEqual(self._dates(table),
                                 ["2026-03-31", "2026-06-30"])


class TestUndeterminablePeriodStoresNothing(_JobCase):

    def _assert_nothing_stored(self, df):
        _cert, n, err, statuses = self._run(df)
        self.assertEqual((n, err), (0, "undeterminable_frame_period"))
        self.assertEqual(statuses, {
            s: "fail:undeterminable_frame_period" for s in statuses})
        for table in _ALL_TABLES:
            with self.subTest(table=table):
                self.assertEqual(self._dates(table), [])

    def test_no_quarter_column(self):
        self._assert_nothing_stored(_frame("3/31/2026").drop(columns="quarter"))

    def test_mixed_quarters(self):
        df = _frame("3/31/2026")
        df.loc[0, "quarter"] = "6/30/2026"
        self._assert_nothing_stored(df)

    def test_unparseable_quarter(self):
        self._assert_nothing_stored(_frame("not-a-date"))

    def test_all_null_quarter(self):
        self._assert_nothing_stored(_frame(None))

    def test_counted_as_an_error_by_main_loop(self):
        # err is not one of main()'s no-data sentinels, so the bank lands
        # in `errors` (and the exit-code error rate).
        _c, _n, err, _s = self._run(_frame(None))
        self.assertNotIn(err, ("", "empty_call_report", "no_securities_data"))


class TestMislabelDiagnostic(_JobCase):
    """tools/diagnose_ffiec_period_mislabels flags the rows the pre-fix job
    wrote: a later quarter whose payload equals the prior quarter's."""

    def _seed_old_bug(self, cert, quarter_frame, stamped):
        """What the pre-fix job did: parse `quarter_frame`'s report but
        stamp every schedule with `stamped`."""
        from data import ffiec_client as fc
        import data.call_report_store as s
        df = _frame(quarter_frame)
        s.upsert_ri_income_detail(cert, RSSD, fc.get_ri_income_detail(
            RSSD, stamped, call_report_df=df))
        s.upsert_rcn_detail(cert, RSSD, fc.get_rcn_detail(
            RSSD, stamped, call_report_df=df))
        s.upsert_securities_ladder(cert, RSSD, fc.get_securities_maturity_ladder(
            RSSD, stamped, call_report_df=df), floating_loan_share=0.5)

    def test_flags_only_the_mislabeled_quarter(self):
        from tools.diagnose_ffiec_period_mislabels import find_suspects
        import data.call_report_store as s
        # Bank 628: genuine Q1, then the late-filer Q1 frame stamped Q2.
        self._seed_old_bug(628, "3/31/2026", "03/31/2026")
        self._seed_old_bug(628, "3/31/2026", "06/30/2026")
        # Bank 700: genuine Q1 and a genuinely different Q2 (via the job).
        df_q2 = _frame("6/30/2026")
        df_q2.loc[df_q2["mdrm"] == "RIAD4107", "int_data"] = 9_800
        df_q2.loc[df_q2["mdrm"] == "RCON1406", "int_data"] = 55
        df_q2.loc[df_q2["mdrm"] == "RCONA549", "int_data"] = 150
        df_q2.loc[df_q2["mdrm"] == "RCOAP859", "int_data"] = 9_100
        df_q2.loc[df_q2["mdrm"] == "RIADC017", "int_data"] = 510
        df_q2.loc[df_q2["mdrm"] == "RIAD4508", "int_data"] = 85
        from jobs import refresh_ffiec
        with patch("data.ffiec_client.fetch_call_report",
                   return_value=_frame("3/31/2026")):
            refresh_ffiec._refresh_one(700, RSSD, "03/31/2026")
        with patch("data.ffiec_client.fetch_call_report", return_value=df_q2):
            refresh_ffiec._refresh_one(700, RSSD, "06/30/2026")

        with s._get_engine().connect() as conn:
            got = find_suspects(conn)
        self.assertEqual(got, [{
            "cert": 628, "report_date": "2026-06-30",
            "prior_date": "2026-03-31", "strength": "strong",
            "tables": ["call_report_securities", "rcn_detail",
                       "ri_income_detail"],
        }])

    def test_weak_when_only_balances_match(self):
        from tools.diagnose_ffiec_period_mislabels import find_suspects
        import data.call_report_store as s
        from data import ffiec_client as fc
        df = _frame("3/31/2026")
        for stamped in ("12/31/2025", "03/31/2026"):  # year boundary step
            s.upsert_rcn_detail(628, RSSD, fc.get_rcn_detail(
                RSSD, stamped, call_report_df=df))
        with s._get_engine().connect() as conn:
            got = find_suspects(conn)
        self.assertEqual([(e["report_date"], e["prior_date"], e["strength"])
                          for e in got],
                         [("2026-03-31", "2025-12-31", "weak")])

    def test_empty_db_no_suspects(self):
        from tools.diagnose_ffiec_period_mislabels import find_suspects
        import data.db as db
        with db.get_engine().connect() as conn:
            self.assertEqual(find_suspects(conn), [])


class TestFramePeriod(unittest.TestCase):

    def test_formats(self):
        from jobs.refresh_ffiec import _frame_period
        self.assertEqual(_frame_period(_frame("3/31/2026")), "03/31/2026")
        self.assertEqual(_frame_period(_frame("12/31/2025")), "12/31/2025")
        self.assertEqual(
            _frame_period(_frame(pd.Timestamp("2026-09-30"))), "09/30/2026")
        self.assertIsNone(_frame_period(pd.DataFrame({"mdrm": ["RIAD4107"]})))


if __name__ == "__main__":
    unittest.main()
