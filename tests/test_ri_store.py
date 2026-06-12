"""
Unit tests for the Schedule RI income-detail store
(data/call_report_store.py: upsert_ri_income_detail / get_stored_ri_detail).

The store is pointed at an isolated in-memory SQLite engine — no test ever
touches the real cache.db or Postgres.

Run:  python -m unittest tests.test_ri_store
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _detail(period: str, rssd: int = 123456, **overrides) -> dict:
    """Minimal get_ri_income_detail-shaped dict for one bank-quarter."""
    d = {
        "reporting_period": period,
        "rssd_id": rssd,
        "boli_income": 100.0,
        "provision_loans": 250.0,
        "provision_total": 300.0,
        "provision_unfunded": 50.0,
        "net_income": 5000.0,
        "tax_exempt_loan_income": None,  # absent from filing stays None
        "boli_income_usd": 100_000.0,
        "provision_loans_usd": 250_000.0,
        "provision_total_usd": 300_000.0,
        "provision_unfunded_usd": 50_000.0,
        "net_income_usd": 5_000_000.0,
        "tax_exempt_loan_income_usd": None,
    }
    d.update(overrides)
    return d


class TestRiIncomeDetailStore(unittest.TestCase):

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        import data.db as db
        import data.call_report_store as store

        self._db = db
        self._store = store
        self._saved_db_engine = db._engine
        self._saved_store_engine = store._engine

        # One shared in-memory SQLite DB for the whole test, isolated from
        # the real cache.db.
        db._engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        store._engine = None  # force re-init (schema create) on first use

    def tearDown(self):
        self._db._engine.dispose()
        self._db._engine = self._saved_db_engine
        self._store._engine = self._saved_store_engine

    # ── round-trip ──────────────────────────────────────────────────────

    def test_round_trip_newest_first(self):
        """Write 3 quarters out of order; read back newest-first, intact."""
        s = self._store
        self.assertEqual(s.upsert_ri_income_detail(
            5000, 123456, _detail("06/30/2025", net_income=2000.0)), 1)
        self.assertEqual(s.upsert_ri_income_detail(
            5000, 123456, _detail("12/31/2025", net_income=4000.0)), 1)
        self.assertEqual(s.upsert_ri_income_detail(
            5000, 123456, _detail("09/30/2025", net_income=3000.0)), 1)

        rows = s.get_stored_ri_detail(5000)
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["reporting_period"] for r in rows],
                         ["12/31/2025", "09/30/2025", "06/30/2025"])
        self.assertEqual([r["net_income"] for r in rows],
                         [4000.0, 3000.0, 2000.0])
        # Full shape survives the round-trip, including None for codes
        # absent from the filing and the *_usd scaling.
        newest = rows[0]
        self.assertEqual(newest["rssd_id"], 123456)
        self.assertEqual(newest["boli_income"], 100.0)
        self.assertEqual(newest["boli_income_usd"], 100_000.0)
        self.assertEqual(newest["provision_unfunded"], 50.0)
        self.assertIsNone(newest["tax_exempt_loan_income"])
        self.assertIsNone(newest["tax_exempt_loan_income_usd"])

    def test_quarters_limit(self):
        s = self._store
        for q, period in enumerate(
                ["03/31/2025", "06/30/2025", "09/30/2025", "12/31/2025"]):
            s.upsert_ri_income_detail(5000, 123456,
                                      _detail(period, net_income=float(q)))
        rows = s.get_stored_ri_detail(5000, quarters=2)
        self.assertEqual([r["reporting_period"] for r in rows],
                         ["12/31/2025", "09/30/2025"])

    # ── idempotency ─────────────────────────────────────────────────────

    def test_rewrite_same_quarter_is_idempotent(self):
        """Re-running the job for the same quarter updates in place —
        one row per (cert, report_date), latest values win."""
        s = self._store
        s.upsert_ri_income_detail(5000, 123456,
                                  _detail("12/31/2025", net_income=4000.0))
        s.upsert_ri_income_detail(5000, 123456,
                                  _detail("12/31/2025", net_income=4100.0))
        rows = s.get_stored_ri_detail(5000)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["net_income"], 4100.0)

    def test_banks_are_isolated_by_cert(self):
        s = self._store
        s.upsert_ri_income_detail(5000, 123456, _detail("12/31/2025"))
        s.upsert_ri_income_detail(6000, 654321,
                                  _detail("12/31/2025", rssd=654321))
        self.assertEqual(len(s.get_stored_ri_detail(5000)), 1)
        self.assertEqual(s.get_stored_ri_detail(6000)[0]["rssd_id"], 654321)

    # ── empty / invalid input ───────────────────────────────────────────

    def test_reader_returns_empty_list_when_nothing_stored(self):
        self.assertEqual(self._store.get_stored_ri_detail(99999), [])

    def test_upsert_rejects_empty_or_periodless_detail(self):
        s = self._store
        self.assertEqual(s.upsert_ri_income_detail(5000, 123456, {}), 0)
        self.assertEqual(s.upsert_ri_income_detail(5000, 123456, None), 0)
        self.assertEqual(s.upsert_ri_income_detail(
            5000, 123456, {"net_income": 1.0}), 0)
        self.assertEqual(s.get_stored_ri_detail(5000), [])

    def test_iso_period_normalized_to_mmddyyyy_on_read(self):
        """The client may hand an ISO reporting_period; the reader always
        returns MM/DD/YYYY (the shape get_ri_income_detail uses)."""
        s = self._store
        s.upsert_ri_income_detail(5000, 123456, _detail("2025-12-31"))
        rows = s.get_stored_ri_detail(5000)
        self.assertEqual(rows[0]["reporting_period"], "12/31/2025")


if __name__ == "__main__":
    unittest.main()
