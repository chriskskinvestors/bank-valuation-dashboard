"""Agent dataset export — writer + job runner (offline, local part store)."""
import io
import json
import sys
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import pandas as pd

from data.export import writer
import jobs.export_dataset as job


class _LocalStore(unittest.TestCase):
    """Parts go to a temp dir; GCS and BigQuery off."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (mock.patch.object(writer, "LOCAL_ROOT", Path(self.tmp.name)),
                  mock.patch.object(writer, "_bucket", return_value=None),
                  mock.patch.dict("os.environ", {"EXPORT_BQ": "off"})):
            p.start()
            self.addCleanup(p.stop)


class TestCheckColumns(unittest.TestCase):
    COLS = {"ticker": "Ticker", "x": "Some value, $"}

    def test_exact_match_passes(self):
        writer.check_columns("t", pd.DataFrame(columns=["ticker", "x"]), self.COLS)

    def test_undocumented_column_fails(self):
        with self.assertRaisesRegex(ValueError, r"undocumented \['y'\]"):
            writer.check_columns("t", pd.DataFrame(columns=["ticker", "x", "y"]), self.COLS)

    def test_missing_column_fails(self):
        with self.assertRaisesRegex(ValueError, r"missing \['x'\]"):
            writer.check_columns("t", pd.DataFrame(columns=["ticker"]), self.COLS)

    def test_order_differs_fails(self):
        with self.assertRaisesRegex(ValueError, "order differs"):
            writer.check_columns("t", pd.DataFrame(columns=["x", "ticker"]), self.COLS)

    def test_blank_description_fails(self):
        with self.assertRaisesRegex(ValueError, "without a description"):
            writer.check_columns("t", pd.DataFrame(columns=["ticker"]), {"ticker": " "})


class TestWriteTable(_LocalStore):
    COLS = {"ticker": "Ticker", "as_of_date": "Export date", "ptbv": "P/TBV, x"}

    def test_parquet_keeps_absent_as_null_and_dates_as_dates(self):
        df = pd.DataFrame({"ticker": ["A", "B"],
                           "as_of_date": [date(2026, 10, 22)] * 2,
                           "ptbv": [1.43, None]})
        back = pd.read_parquet(io.BytesIO(writer.to_parquet_bytes(df)))
        self.assertTrue(pd.isna(back.loc[1, "ptbv"]))      # absent, never 0
        self.assertEqual(back.loc[0, "as_of_date"], date(2026, 10, 22))

    def test_replace_overwrites_latest(self):
        for v in (1.0, 2.0):
            res = writer.write_table("t", pd.DataFrame({"ticker": ["A"],
                                                        "as_of_date": [date(2026, 10, 22)],
                                                        "ptbv": [v]}),
                                     self.COLS, mode="replace", description="d")
        self.assertEqual(writer.list_parts("t"), [res.part])
        self.assertEqual(pd.read_parquet(res.part)["ptbv"].tolist(), [2.0])
        self.assertEqual(res.bq, "skipped")

    def test_partition_one_part_per_day_rerun_overwrites(self):
        def day(d, v):
            return writer.write_table(
                "m", pd.DataFrame({"ticker": ["A"], "as_of_date": [d], "ptbv": [v]}),
                self.COLS, mode="partition", part_day=d, partition_field="as_of_date",
                description="d")
        day(date(2026, 10, 21), 1.0)
        day(date(2026, 10, 22), 2.0)
        day(date(2026, 10, 22), 3.0)                       # same-day re-run
        parts = writer.list_parts("m")
        self.assertEqual([Path(p).name for p in parts],
                         ["2026-10-21.parquet", "2026-10-22.parquet"])
        self.assertEqual(pd.read_parquet(parts[1])["ptbv"].tolist(), [3.0])

    def test_append_empty_writes_nothing(self):
        res = writer.write_table("tx", pd.DataFrame(columns=list(self.COLS)), self.COLS,
                                 mode="append", run_stamp="2026-10-22T0100Z",
                                 description="d")
        self.assertEqual((res.rows, res.bq), (0, "nothing new"))
        self.assertEqual(writer.list_parts("tx"), [])

    def test_keys_roundtrip(self):
        self.assertEqual(writer.read_keys("tx"), set())
        writer.write_keys("tx", {("HFWA", 2026, 2), ("JPM", 2026, 3)})
        self.assertEqual(writer.read_keys("tx"), {("HFWA", 2026, 2), ("JPM", 2026, 3)})

    def test_bad_columns_store_nothing(self):
        with self.assertRaises(ValueError):
            writer.write_table("t", pd.DataFrame({"ticker": ["A"]}), self.COLS,
                               mode="replace", description="d")
        self.assertEqual(writer.list_parts("t"), [])


def _fake_tables_module():
    """A stand-in table module for the job runner."""
    m = types.ModuleType("fake_export_tables")
    m.COLUMNS_OK = {"ticker": "Ticker", "v": "Value"}
    m.build_ok = lambda tickers: pd.DataFrame({"ticker": tickers, "v": [1.0] * len(tickers)})
    m.build_empty = lambda tickers: pd.DataFrame(columns=["ticker", "v"])

    def boom(tickers):
        raise RuntimeError("store missing")
    m.build_boom = boom
    m.COLUMNS_TRANSCRIPTS = {"ticker": "Ticker", "fiscal_year": "FY", "quarter": "Q",
                             "content": "Text"}
    m.seen_have = []

    def build_tx(tickers, have, max_fetch):
        m.seen_have.append(set(have))
        rows = [(t, 2026, 3, "text") for t in tickers if (t, 2026, 3) not in have]
        return pd.DataFrame(rows, columns=list(m.COLUMNS_TRANSCRIPTS))
    m.build_transcripts = build_tx
    m.COLUMNS_METRICS = {"ticker": "Ticker", "as_of_date": "Date"}
    m.build_metrics = lambda tickers, as_of: pd.DataFrame(
        {"ticker": tickers, "as_of_date": [as_of] * len(tickers)})
    return m


class TestJob(_LocalStore):
    def setUp(self):
        super().setUp()
        self.mod = _fake_tables_module()
        sys.modules["fake_export_tables"] = self.mod
        self.addCleanup(sys.modules.pop, "fake_export_tables", None)
        reg = {
            "banks": ("fake_export_tables", "build_ok", "COLUMNS_OK", "replace"),
            "bank_metrics": ("fake_export_tables", "build_metrics", "COLUMNS_METRICS",
                             "partition"),
            "events": ("fake_export_tables", "build_boom", "COLUMNS_OK", "replace"),
            "holdco_quarterly": ("fake_export_tables", "build_empty", "COLUMNS_OK", "replace"),
            "company_reported": ("fake_export_tables", "build_empty", "COLUMNS_OK", "replace"),
            "transcripts": ("fake_export_tables", "build_transcripts",
                            "COLUMNS_TRANSCRIPTS", "append"),
        }
        for p in (mock.patch.object(job, "_registry", return_value=reg),
                  mock.patch.dict(job.TABLE_DESCRIPTIONS,
                                  {k: k for k in reg})):
            p.start()
            self.addCleanup(p.stop)

    def _run(self, *args):
        with mock.patch("data.bank_universe.get_universe_tickers",
                        return_value=["HFWA", "JPM"]):
            return job.main(list(args))

    def _runs(self):
        part = writer.list_parts("export_runs")[-1]
        return pd.read_parquet(part).set_index("table_name")

    def test_failure_is_isolated_logged_and_fails_the_run(self):
        self.assertEqual(self._run("banks,events"), 1)
        runs = self._runs()
        self.assertEqual(runs.loc["banks", "status"], "ok")
        self.assertEqual(runs.loc["banks", "rows"], 2)
        self.assertEqual(runs.loc["events", "status"], "failed")
        self.assertIn("store missing", json.loads(runs.loc["events", "notes"])["error"])

    def test_empty_core_table_fails_run_but_empty_optional_does_not(self):
        self.assertEqual(self._run("holdco_quarterly"), 1)      # core
        self.assertEqual(self._run("company_reported"), 0)      # optional
        self.assertEqual(self._runs().loc["company_reported", "status"], "empty")

    def test_bank_metrics_gets_run_date(self):
        self.assertEqual(self._run("bank_metrics"), 0)
        part = [p for p in writer.list_parts("bank_metrics")][0]
        self.assertEqual(set(pd.read_parquet(part)["as_of_date"]),
                         {date.fromisoformat(Path(part).stem)})

    def test_transcripts_append_only_new_and_index_keys(self):
        self.assertEqual(self._run("transcripts"), 0)
        self.assertEqual(writer.read_keys("transcripts"),
                         {("HFWA", 2026, 3), ("JPM", 2026, 3)})
        self.assertEqual(self._run("transcripts"), 0)             # nothing new
        self.assertEqual(self.mod.seen_have[-1], {("HFWA", 2026, 3), ("JPM", 2026, 3)})
        self.assertEqual(len(writer.list_parts("transcripts")), 1)
        self.assertEqual(self._runs().loc["transcripts", "status"], "nothing new")

    def test_dictionary_covers_every_table_column(self):
        self.assertEqual(self._run("banks"), 0)
        d = pd.read_parquet(writer.list_parts("metric_dictionary")[0])
        got = set(zip(d["table_name"], d["column_name"]))
        self.assertIn(("banks", "v"), got)
        self.assertIn(("transcripts", "content"), got)
        self.assertIn(("export_runs", "notes"), got)
        self.assertFalse((d["description"].str.strip() == "").any())

    def test_unknown_table_rejected(self):
        self.assertEqual(self._run("nope"), 2)


class TestBigQueryLoadConfig(unittest.TestCase):
    """The load job each mode issues (client mocked — no network)."""

    def _load(self, mode, exists=True, uri="gs://b/agent_dataset/t/x.parquet"):
        from google.api_core.exceptions import NotFound
        client = mock.MagicMock()
        if not exists:
            client.get_table.side_effect = NotFound("absent")
        with mock.patch.object(writer, "_bq_client", return_value=client), \
             mock.patch.object(writer, "_describe"):
            writer.bq_load("t", uri, {"ticker": "T"}, "d", mode,
                           partition_field="as_of_date", partition_day=date(2026, 10, 22))
        (src, dest), kw = client.load_table_from_uri.call_args
        return dest, kw["job_config"]

    def test_replace_truncates_table(self):
        dest, cfg = self._load("replace")
        self.assertEqual(dest, f"{writer.PROJECT}.{writer.DATASET}.t")
        self.assertEqual(cfg.write_disposition, "WRITE_TRUNCATE")

    def test_partition_replaces_only_that_day(self):
        dest, cfg = self._load("partition")
        self.assertTrue(dest.endswith(".t$20261022"))
        self.assertEqual(cfg.write_disposition, "WRITE_TRUNCATE")
        self.assertEqual(cfg.schema_update_options, ["ALLOW_FIELD_ADDITION"])
        self.assertEqual(cfg.time_partitioning.field, "as_of_date")

    def test_partition_first_load_creates_partitioned_table(self):
        dest, cfg = self._load("partition", exists=False)
        self.assertFalse("$" in dest)
        self.assertEqual(cfg.write_disposition, "WRITE_APPEND")
        self.assertEqual(cfg.time_partitioning.field, "as_of_date")

    def test_append_appends_allowing_new_columns(self):
        dest, cfg = self._load("append")
        self.assertEqual(cfg.write_disposition, "WRITE_APPEND")
        self.assertEqual(cfg.schema_update_options, ["ALLOW_FIELD_ADDITION"])


class TestRealRegistry(unittest.TestCase):
    def test_every_table_has_a_description(self):
        self.assertEqual(set(job._registry()) | {"metric_dictionary", "export_runs"},
                         set(job.TABLE_DESCRIPTIONS))


if __name__ == "__main__":
    unittest.main()
