"""(2026-10-12) SEC filer status — data/sec_filer_status, the DATA cik seam
(data/bank_mapping.get_data_cik) and the nightly job's routing.

The coverage audit's "share count unresolved" class held four banks whose
CIK is not a source of current XBRL: FOTB and OSBK (last 10-Q May 2012),
UBOH (Form 15-12G 2023-03-23 after its FY2022 10-K), DWNX (Regulation A,
never a 10-K/10-Q). Routed as SEC filers their XBRL came back empty or a
decade stale; the growth gate failed them nightly ("sec_filings: Data is
5302 days old") and the wire-release path never ran. A CIK whose latest
10-K/10-Q is older than PERIODIC_MAX_DAYS (455) — or which has none — now
routes like no CIK for data, from a map the nightly refresh persists.

Run: python -m unittest tests.test_sec_filer_status
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from sqlalchemy import create_engine, text  # noqa: E402

import data.cache as cache  # noqa: E402
import data.sec_filer_status as sfs  # noqa: E402
from data import bank_mapping  # noqa: E402

TODAY = date.today()


def _per(days_ago, form="10-Q"):
    d = (TODAY - timedelta(days=days_ago)).isoformat()
    return {"form": form, "date": d, "report_date": d}


class _IsolatedCache(unittest.TestCase):
    def setUp(self):
        eng = create_engine("sqlite://")
        with eng.begin() as conn:
            conn.execute(text(
                "CREATE TABLE cache (key VARCHAR(255) PRIMARY KEY, "
                "value TEXT NOT NULL, timestamp DOUBLE PRECISION NOT NULL)"))
        p = patch.object(cache, "_engine", eng)
        p.start()
        self.addCleanup(p.stop)
        sfs._memo["map"], sfs._memo["at"] = None, 0.0
        self.addCleanup(lambda: sfs._memo.update(map=None, at=0.0))


class TestLiveStatus(unittest.TestCase):
    def _status(self, per):
        with patch("data.sec_earnings_8k.latest_periodic_filing", return_value=per):
            return sfs.sec_filer_status(1)

    def test_recent_periodic_filing(self):
        self.assertEqual(self._status(_per(60)), "periodic")
        self.assertEqual(self._status(_per(sfs.PERIODIC_MAX_DAYS)), "periodic")

    def test_uboh_fy2022_10k_is_deregistered(self):
        # Filed 2023-03-13; well past 455 days on 2026-10-12.
        self.assertEqual(self._status({"form": "10-K", "date": "2023-03-13",
                                       "report_date": "2022-12-31"}),
                         "deregistered")
        self.assertEqual(self._status(_per(sfs.PERIODIC_MAX_DAYS + 1)),
                         "deregistered")

    def test_fotb_osbk_2012(self):
        self.assertEqual(self._status({"form": "10-Q", "date": "2012-05-14",
                                       "report_date": "2012-03-31"}),
                         "deregistered")

    def test_dwnx_no_periodic_filing_ever(self):
        self.assertEqual(self._status(None), "non-periodic")

    def test_lookup_failure_never_demotes(self):
        with patch("data.sec_earnings_8k.latest_periodic_filing",
                   side_effect=RuntimeError("429")):
            self.assertEqual(sfs.sec_filer_status(1), "periodic")
        self.assertEqual(self._status({"form": "10-Q", "date": "bad"}), "periodic")


class TestPersistedMapAndDataCik(_IsolatedCache):
    def test_no_map_reads_as_periodic(self):
        self.assertIsNone(sfs.persisted_status("FOTB"))
        self.assertTrue(sfs.is_data_filer("FOTB"))
        with patch.object(bank_mapping, "get_cik", return_value=1099668):
            self.assertEqual(bank_mapping.get_data_cik("FOTB"), 1099668)

    def test_written_map_routes_non_periodic_as_no_cik(self):
        sfs.write_statuses({"FOTB": "deregistered", "DWNX": "non-periodic",
                            "STT": "periodic"})
        self.assertEqual(sfs.persisted_status("fotb"), "deregistered")
        self.assertFalse(sfs.is_data_filer("FOTB"))
        self.assertFalse(sfs.is_data_filer("DWNX"))
        self.assertTrue(sfs.is_data_filer("STT"))
        self.assertTrue(sfs.is_data_filer("UNKNOWN"))
        with patch.object(bank_mapping, "get_cik", return_value=1099668):
            self.assertIsNone(bank_mapping.get_data_cik("FOTB"))
            self.assertEqual(bank_mapping.get_data_cik("STT"), 1099668)
        # Identity is untouched.
        with patch.object(bank_mapping, "get_cik", return_value=1099668):
            self.assertEqual(bank_mapping.get_cik("FOTB"), 1099668)

    def test_map_survives_the_24h_ceiling_and_is_memoised(self):
        sfs.write_statuses({"UBOH": "deregistered"})
        with cache._engine.begin() as conn:
            conn.execute(text("UPDATE cache SET timestamp = timestamp - 400000"))
        sfs._memo["map"], sfs._memo["at"] = None, 0.0
        self.assertEqual(sfs.persisted_status("UBOH"), "deregistered")
        with patch.object(cache, "get", side_effect=AssertionError("memoised")):
            self.assertEqual(sfs.persisted_status("UBOH"), "deregistered")

    def test_no_cik_is_none_without_consulting_the_map(self):
        with patch.object(bank_mapping, "get_cik", return_value=None), \
                patch.object(sfs, "is_data_filer",
                             side_effect=AssertionError("not consulted")):
            self.assertIsNone(bank_mapping.get_data_cik("PBAM"))


class TestNightlyJobRouting(_IsolatedCache):
    """refresh_one demotes a non-periodic CIK before any XBRL fetch and
    records the status; the run's end persists the map."""

    def _run(self, status):
        import jobs.refresh_universe as ru
        calls = []
        with patch("data.bank_mapping.get_cik", return_value=1099668), \
                patch("data.bank_mapping.get_fdic_cert", return_value=None), \
                patch("data.sec_filer_status.sec_filer_status", return_value=status), \
                patch("data.sec_client.invalidate_company_facts",
                      side_effect=lambda c: calls.append(("invalidate", c))), \
                patch("data.sec_client.get_latest_fundamentals",
                      side_effect=lambda c: calls.append(("fetch", c)) or {}):
            row = ru.refresh_one("FOTB")
        return row, calls

    def test_deregistered_cik_skips_xbrl(self):
        row, calls = self._run("deregistered")
        self.assertEqual(row["sec_status"], "deregistered")
        self.assertIn("sec_status:deregistered", row["warnings"])
        self.assertEqual(calls, [])
        # No cert either → the bank has no data source at all.
        self.assertIn("no_mapping", row["errors"])

    def test_periodic_cik_fetches_as_before(self):
        row, calls = self._run("periodic")
        self.assertEqual(row["sec_status"], "periodic")
        self.assertEqual([c for c, _ in calls], ["invalidate", "fetch"])


class TestRenderPathsUseTheDataCik(unittest.TestCase):
    """A Company-page render of a deregistered bank re-created its stale
    sec: row through app.load_sec_data and read 2012 fundamentals for the
    card's share count (FOTB, 2026-10-14); the warm then saw the bank "in
    sec" and skipped its wire release. Both paths route on the data cik."""

    def test_app_loader_and_card_fundamentals(self):
        app_src = (REPO / "app.py").read_text(encoding="utf-8")
        loader = app_src[app_src.index("def load_sec_data"):]
        loader = loader[:loader.index("return results")]
        self.assertIn("get_data_cik(ticker)", loader)
        self.assertNotIn("= get_cik(ticker)", loader)
        bd = (REPO / "ui/bank_detail.py").read_text(encoding="utf-8")
        i = bd.index("fund = sec_client.get_latest_fundamentals(cik)")
        self.assertIn("get_data_cik(ticker)", bd[i - 400:i])


if __name__ == "__main__":
    unittest.main()
