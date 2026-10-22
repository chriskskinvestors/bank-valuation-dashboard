"""Agent dataset export — core tables (data/export/tables_core.py).

Offline: every store is routed at a private temp SQLite FILE per test
(tests/_apptest_store.isolate_store) and seeded with a tiny universe of
made-up tickers (ZQ**), so nothing resolves through the curated maps or the
network. The universe memo / generation counters are patched per test so no
other suite's universe leaks in (or out).

Pins:
  * COLUMNS_<TABLE> == frame columns, in order, ticker first;
  * absent stays absent: a metric missing from the snapshot (or None in it)
    is NaN/None in the frame — never 0 — and a real 0 stays 0;
  * every key the dashboard's metric build emits has a declared column
    (a new metric without a description fails here);
  * one row per bank (preferred siblings fold into the primary common);
  * the events since-window and topic-row exclusion;
  * the "raw dollars" unit claim for every FDIC level metric.
"""
from __future__ import annotations

import itertools
import json
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import pandas as pd

import tests  # noqa: F401  — stub + store isolation + network guard first

_GEN = itertools.count(-900_000)

UNIVERSE = {
    "ZQAA": {"name": "ZQAA BANCORP INC", "cik": 9990001, "fdic_cert": 999001,
             "exchange": "Nasdaq", "share_class": "common",
             "bank_name": "Zq Aa Bank", "webaddr": "www.zqaa.example"},
    "ZQBB": {"name": "Zq Bb Financial Corp", "cik": 9990002, "fdic_cert": 999002,
             "exchange": "NYSE", "share_class": "common"},
    "ZQBBP": {"name": "Zq Bb Financial Corp", "cik": 9990002,
              "fdic_cert": 999002, "exchange": "NYSE",
              "share_class": "preferred"},
    "ZQCC": {"name": "Zq Cc Bankshares", "cik": None, "fdic_cert": 999003,
             "exchange": "OTC", "share_class": "common"},
    "ZQDD": {"name": "Zq Dd Holding Co", "cik": 9990004, "fdic_cert": None,
             "exchange": "OTC", "share_class": "common"},
}


class _StoreCase(unittest.TestCase):
    """Private store + the ZQ universe served through the real read path."""

    group_map = {"999001": [999001, 999011], "999011": [999001, 999011]}

    def setUp(self):
        from tests._apptest_store import isolate_store
        from data import cache
        import data.bank_mapping as bm
        import data.bank_universe as bu
        import data.sec_filer_status as sfs

        isolate_store(self)
        cache.put("bank_universe_lastgood", {
            "cached_at": datetime.now().isoformat(), "universe": UNIVERSE})
        cache.put("sec_filer_status:v1", {
            "date": date.today().isoformat(),
            "statuses": {"ZQAA": "periodic", "ZQDD": "deregistered"}})
        if self.group_map is not None:
            cache.put("cert_groups:v2", self.group_map)
        patches = [
            mock.patch.object(bu, "_UNIVERSE_CACHE", None),
            mock.patch.object(bu, "_UNIVERSE_LOADED", None),
            mock.patch.object(bu, "_UNIVERSE_GEN", next(_GEN)),
            mock.patch.object(bu, "_UNIVERSE_LOADED_AT", 0.0),
            mock.patch.object(bu, "_UNIVERSE_CHECKED_AT", 0.0),
            mock.patch.dict(sfs._memo, {"at": 0.0, "map": None}),
            mock.patch.dict(bm._SUCCESSOR_MEMO, {"at": 0.0, "map": None}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


# ══════════════════════════════════════════════════════════════════════════
# banks
# ══════════════════════════════════════════════════════════════════════════

class TestBanks(_StoreCase):

    def _seed_sod(self):
        from sqlalchemy import text
        from data import branches_store
        with branches_store._get_engine().begin() as conn:
            for cert, brnum, city, st in ((999001, 0, "Springfield", "IL"),
                                          (999001, 1, "Peoria", "IL"),
                                          (999002, 3, "Dover", "DE")):
                conn.execute(text(
                    "INSERT INTO branches (cert, brnum, year, city, state) "
                    "VALUES (:c, :b, 2025, :city, :st)"),
                    {"c": cert, "b": brnum, "city": city, "st": st})

    def test_columns_and_one_row_per_bank(self):
        from data.export.tables_core import COLUMNS_BANKS, build_banks
        df = build_banks(["ZQAA", "ZQBB", "ZQBBP", "zqbb", "ZQCC", "ZQDD", "NOPE"])
        self.assertEqual(list(df.columns), list(COLUMNS_BANKS))
        self.assertEqual(df.columns[0], "ticker")
        self.assertEqual(list(df["ticker"]), ["ZQAA", "ZQBB", "ZQCC", "ZQDD"])
        self.assertTrue(df["ticker"].is_unique)

    def test_preferred_alone_canonicalizes_to_common(self):
        from data.export.tables_core import build_banks
        df = build_banks(["ZQBBP"])
        self.assertEqual(list(df["ticker"]), ["ZQBB"])

    def test_identity_routing(self):
        from data.export.tables_core import build_banks
        df = build_banks(["ZQAA", "ZQBB", "ZQCC", "ZQDD"]).set_index("ticker")
        # SEC periodic filer
        self.assertEqual(df.at["ZQAA", "sec_cik"], 9990001)
        self.assertTrue(df.at["ZQAA", "is_sec_filer"])
        self.assertEqual(df.at["ZQAA", "sec_filer_status"], "periodic")
        # status unknown -> the dashboard's status quo (routes as SEC filer)
        self.assertEqual(df.at["ZQBB", "sec_cik"], 9990002)
        self.assertTrue(pd.isna(df.at["ZQBB", "sec_filer_status"]))
        # no CIK at all
        self.assertTrue(pd.isna(df.at["ZQCC", "sec_cik"]))
        self.assertTrue(pd.isna(df.at["ZQCC", "registrant_cik"]))
        self.assertFalse(df.at["ZQCC", "is_sec_filer"])
        # deregistered: identity CIK kept, data CIK None
        self.assertEqual(df.at["ZQDD", "registrant_cik"], 9990004)
        self.assertTrue(pd.isna(df.at["ZQDD", "sec_cik"]))
        self.assertFalse(df.at["ZQDD", "is_sec_filer"])
        self.assertEqual(df.at["ZQDD", "sec_filer_status"], "deregistered")
        # plain attributes
        self.assertEqual(df.at["ZQAA", "bank_name"], "Zq Aa Bank")
        self.assertEqual(df.at["ZQAA", "website"], "www.zqaa.example")
        self.assertTrue(pd.isna(df.at["ZQBB", "bank_name"]))
        self.assertEqual(df.at["ZQCC", "exchange"], "OTC")

    def test_charter_groups(self):
        from data.export.tables_core import build_banks
        df = build_banks(["ZQAA", "ZQBB", "ZQDD"]).set_index("ticker")
        self.assertEqual(df.at["ZQAA", "fdic_certs"], "999001,999011")
        self.assertEqual(df.at["ZQAA", "n_fdic_charters"], 2)
        self.assertEqual(df.at["ZQBB", "fdic_certs"], "999002")
        self.assertEqual(df.at["ZQBB", "n_fdic_charters"], 1)
        self.assertTrue(pd.isna(df.at["ZQDD", "fdic_cert"]))
        self.assertTrue(pd.isna(df.at["ZQDD", "fdic_certs"]))
        self.assertTrue(pd.isna(df.at["ZQDD", "n_fdic_charters"]))

    def test_main_office_from_sod_brnum_zero_only(self):
        from data.export.tables_core import build_banks
        self._seed_sod()
        df = build_banks(["ZQAA", "ZQBB"]).set_index("ticker")
        self.assertEqual(df.at["ZQAA", "main_office_city"], "Springfield")
        self.assertEqual(df.at["ZQAA", "main_office_state"], "IL")
        self.assertEqual(df.at["ZQAA", "sod_year"], 2025)
        # ZQBB has branches but no main-office row: n/a, never a branch city
        self.assertTrue(pd.isna(df.at["ZQBB", "main_office_city"]))
        self.assertTrue(pd.isna(df.at["ZQBB", "sod_year"]))


class TestBanksNoGroupMap(_StoreCase):
    group_map = None

    def test_absent_group_map_is_none_not_single_charter(self):
        from data.export.tables_core import build_banks
        df = build_banks(["ZQAA", "ZQBB"])
        self.assertTrue(df["fdic_certs"].isna().all())
        self.assertTrue(df["n_fdic_charters"].isna().all())
        self.assertEqual(list(df["fdic_cert"]), [999001, 999002])


# ══════════════════════════════════════════════════════════════════════════
# bank_metrics
# ══════════════════════════════════════════════════════════════════════════

def _snapshot(rows, cached_at="2026-10-21T09:15:00"):
    return {"cached_at": cached_at, "n_tickers": len(rows), "metrics": rows}


class TestBankMetricsColumns(unittest.TestCase):
    """Static contract — no store needed."""

    def test_ticker_first_and_unique(self):
        from data.export.tables_core import COLUMNS_BANK_METRICS
        cols = list(COLUMNS_BANK_METRICS)
        self.assertEqual(cols[:3], ["ticker", "as_of_date", "snapshot_at"])
        self.assertEqual(len(cols), len(set(cols)))

    def test_every_column_described_with_source(self):
        from data.export.tables_core import COLUMNS_BANK_METRICS
        for k, d in COLUMNS_BANK_METRICS.items():
            self.assertNotIn("UNDOCUMENTED", d, k)
            self.assertGreater(len(d), 20, k)

    def test_every_registry_metric_is_a_column(self):
        from config import METRICS
        from data.export.tables_core import COLUMNS_BANK_METRICS
        missing = [m["key"] for m in METRICS if m["key"] not in COLUMNS_BANK_METRICS]
        self.assertEqual(missing, [])

    def test_dashboard_build_emits_no_undeclared_key(self):
        """The new-metric guard against the CODE: every key the dashboard's
        per-bank metric build writes into the snapshot needs a column (and so
        a description). Add one to _COMPUTED_DESC / _PASSTHROUGH_DESC."""
        from analysis.metrics import build_bank_metrics
        from data.export.tables_core import undocumented_snapshot_keys
        row = build_bank_metrics(None, {}, {}, {}, [])
        self.assertEqual(undocumented_snapshot_keys([row]), [])

    def test_fixture_new_key_is_reported(self):
        """The same guard against a snapshot fixture: a key the dict lacks."""
        from data.export.tables_core import undocumented_snapshot_keys
        rows = [{"ticker": "ZQAA", "tbvps": 10.0, "_notes": {}},
                {"ticker": "ZQBB", "brand_new_metric": 1.0}]
        self.assertEqual(undocumented_snapshot_keys(rows), ["brand_new_metric"])


class TestFdicUnits(unittest.TestCase):
    """The descriptions claim FDIC levels are raw dollars and ratios/counts
    pass through unscaled — prove it through the dashboard's own build for
    every FDIC registry metric (Q4 REPDTE: YTD annualization factor 1)."""

    def test_units_match_catalog_kind(self):
        from config import METRICS
        from analysis.metrics import build_bank_metrics
        from pathlib import Path
        import data
        catalog = json.loads((Path(data.__file__).parent / "fdic_field_catalog.json")
                             .read_text(encoding="utf-8"))
        checked = 0
        for m in METRICS:
            if m.get("source") != "fdic":
                continue
            f = m["fdic_field"]
            kind = catalog[f]["kind"]
            row = build_bank_metrics(None, {f: 2.0, "REPDTE": "2025-12-31"}, {}, {}, [])
            want = 2000.0 if kind == "level" else 2.0
            self.assertEqual(row[m["key"]], want, f"{m['key']} ({f}, {kind})")
            checked += 1
        self.assertGreater(checked, 80)


class TestBankMetrics(_StoreCase):

    def _put(self, rows, **kw):
        from data import cache
        cache.put("watchlist_metrics_snap", _snapshot(rows, **kw))

    def test_columns_equal_dict(self):
        from data.export.tables_core import COLUMNS_BANK_METRICS, build_bank_metrics
        self._put([{"ticker": "ZQAA", "tbvps": 10.0}])
        df = build_bank_metrics(["ZQAA"], as_of=date(2026, 10, 22))
        self.assertEqual(list(df.columns), list(COLUMNS_BANK_METRICS))
        self.assertEqual(len(df), 1)

    def test_absent_stays_absent_and_zero_stays_zero(self):
        from data.export.tables_core import build_bank_metrics
        self._put([{"ticker": "ZQAA", "tbvps": None, "ore": 0.0,
                    "total_assets": 5.0e9, "tbvps_conflict": None}])
        df = build_bank_metrics(["ZQAA"], as_of=date(2026, 10, 22))
        r = df.iloc[0]
        self.assertTrue(pd.isna(r["tbvps"]))           # None in snapshot
        self.assertTrue(pd.isna(r["roatce"]))          # absent from snapshot
        self.assertTrue(pd.isna(r["tbvps_conflict"]))
        self.assertTrue(pd.isna(r["metric_notes"]))
        self.assertEqual(r["ore"], 0.0)                # a real zero survives
        self.assertEqual(r["total_assets"], 5.0e9)
        # nothing fabricated: every column but the three keys and the two
        # seeded values is null
        non_null = {c for c in df.columns if not pd.isna(r[c])}
        self.assertEqual(non_null, {"ticker", "as_of_date", "snapshot_at",
                                    "ore", "total_assets"})

    def test_one_row_per_ticker_and_scope(self):
        from data.export.tables_core import build_bank_metrics
        self._put([{"ticker": "ZQAA", "price": 10.0},
                   {"ticker": "ZQAA", "price": 99.0},     # duplicate: first kept
                   {"ticker": "ZQBB", "price": 20.0},
                   {"ticker": "ZQZZ", "price": 30.0}])    # not requested
        df = build_bank_metrics(["ZQAA", "ZQBBP", "ZQBB", "ZQCC"])
        self.assertEqual(list(df["ticker"]), ["ZQAA", "ZQBB"])   # ZQCC absent: no row
        self.assertEqual(list(df["price"]), [10.0, 20.0])

    def test_stamps_and_value_shaping(self):
        from data.export.tables_core import build_bank_metrics
        self._put([{"ticker": "ZQAA", "assets_per_employee": 7.5,
                    "_notes": {"tbvps": "share-basis mismatch"},
                    "sec_facts_overlay": {"accession": "0001", "form": "10-Q"},
                    "sec_filed_period": "2026-06-30",
                    "efficiency_release_qend": None,
                    "brand_new_metric": 3.0}],
                  cached_at="2026-10-21T09:15:00")
        df = build_bank_metrics(["ZQAA"], as_of=date(2026, 10, 22))
        r = df.iloc[0]
        self.assertEqual(r["as_of_date"], date(2026, 10, 22))
        self.assertEqual(r["snapshot_at"], pd.Timestamp("2026-10-21T09:15:00"))
        self.assertEqual(r["assets_per_employee"], 7_500_000)    # $M -> $
        self.assertEqual(json.loads(r["metric_notes"]),
                         {"tbvps": "share-basis mismatch"})
        self.assertEqual(json.loads(r["sec_facts_overlay"])["form"], "10-Q")
        self.assertEqual(r["sec_filed_period"], date(2026, 6, 30))
        self.assertTrue(pd.isna(r["efficiency_release_qend"]))
        self.assertNotIn("brand_new_metric", df.columns)
        self.assertNotIn("_notes", df.columns)

    def test_empty_notes_is_none(self):
        from data.export.tables_core import build_bank_metrics
        self._put([{"ticker": "ZQAA", "_notes": {}}])
        df = build_bank_metrics(["ZQAA"])
        self.assertTrue(pd.isna(df.iloc[0]["metric_notes"]))
        self.assertEqual(df.iloc[0]["as_of_date"], date.today())

    def test_mixed_types_unified_and_parquet_writable(self):
        # 2026-09-22 snapshot: cet1_current '0' (str) on 36 banks beside
        # floats — the Parquet write failed ("could not convert '0'").
        from data.export.tables_core import build_bank_metrics
        from data.export.writer import to_parquet_bytes
        self._put([{"ticker": "ZQAA", "cet1_current": "0", "metric_x": 1},
                   {"ticker": "ZQBB", "cet1_current": 12.5},
                   {"ticker": "ZQCC", "cet1_current": None}])
        df = build_bank_metrics(["ZQAA", "ZQBB", "ZQCC"])
        self.assertEqual(df["cet1_current"].iloc[0], 0.0)      # same value
        self.assertEqual(df["cet1_current"].iloc[1], 12.5)
        self.assertTrue(pd.isna(df["cet1_current"].iloc[2]))   # absent stays absent
        to_parquet_bytes(df)                                    # writes

    def test_mixed_nonnumeric_column_becomes_text(self):
        from data.export.tables_core import _unify_types
        df = _unify_types(pd.DataFrame({"a": [1.0, "n/a", None]}, dtype=object))
        self.assertEqual(list(df["a"][:2]), ["1.0", "n/a"])
        self.assertTrue(pd.isna(df["a"].iloc[2]))          # absent stays null

    def test_absent_snapshot_is_empty_frame(self):
        from data.export.tables_core import COLUMNS_BANK_METRICS, build_bank_metrics
        df = build_bank_metrics(["ZQAA"])
        self.assertEqual(len(df), 0)
        self.assertEqual(list(df.columns), list(COLUMNS_BANK_METRICS))


# ══════════════════════════════════════════════════════════════════════════
# events
# ══════════════════════════════════════════════════════════════════════════

class TestEvents(_StoreCase):

    def _seed(self, rows):
        from sqlalchemy import text
        from data.events import store
        with store._get_engine().begin() as conn:
            for i, (tk, src, days, tz) in enumerate(rows):
                pub = datetime.now(timezone.utc) - timedelta(days=days)
                stamp = pub.strftime("%Y-%m-%d %H:%M:%S") + ("+00:00" if tz else "")
                conn.execute(text(
                    "INSERT INTO events (ticker, source, event_type, headline, "
                    "summary, url, external_id, published_at, raw_json) VALUES "
                    "(:t, :s, 'press_release', :h, NULL, 'https://x.example', "
                    ":e, :p, :raw)"),
                    {"t": tk, "s": src, "h": f"headline {i}", "e": f"ext{i}",
                     "p": stamp, "raw": json.dumps({"secret": i})})

    def test_window_scope_and_topic_exclusion(self):
        from data.events.store import TOPIC_SOURCE, topic_ticker
        from data.export.tables_core import COLUMNS_EVENTS, build_events
        self._seed([
            ("ZQAA", "businesswire", 10, True),      # in
            ("ZQAA", "ir_site", 399, False),         # in (naive stamp, edge)
            ("ZQAA", "fmp_news", 401, True),         # out: older than window
            ("ZQBBP", "sec_8k", 5, False),           # in, remapped to ZQBB
            (topic_ticker("macro"), TOPIC_SOURCE, 1, True),   # out: topic
            ("ZQZZ", "businesswire", 2, True),       # out: not requested
        ])
        df = build_events(["ZQAA", "ZQBB"], since_days=400)
        self.assertEqual(list(df.columns), list(COLUMNS_EVENTS))
        self.assertNotIn("raw_json", df.columns)
        self.assertEqual(sorted(df["headline"]),
                         ["headline 0", "headline 1", "headline 3"])
        self.assertEqual(df.set_index("headline").at["headline 3", "ticker"], "ZQBB")
        self.assertEqual(str(df["published_at"].dt.tz), "UTC")
        self.assertTrue(df["event_id"].is_unique)
        self.assertFalse(df["ticker"].str.startswith("TOPIC:").any())

    def test_narrow_window(self):
        from data.export.tables_core import build_events
        self._seed([("ZQAA", "businesswire", 10, True),
                    ("ZQAA", "businesswire", 2, True)])
        df = build_events(["ZQAA"], since_days=5)
        self.assertEqual(list(df["headline"]), ["headline 1"])

    def test_empty_store(self):
        from data.export.tables_core import COLUMNS_EVENTS, build_events
        df = build_events(["ZQAA"])
        self.assertEqual(len(df), 0)
        self.assertEqual(list(df.columns), list(COLUMNS_EVENTS))


if __name__ == "__main__":
    unittest.main()
