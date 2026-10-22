"""Agent dataset writer: Parquet parts in GCS (the durable copy), loaded into
BigQuery (the query layer an agent reads).

Every table arrives as a DataFrame plus its column dictionary
({column: description}); the two must agree exactly, so a column can't ship
without a description or a description outlive its column.

Layout, under gs://$GCS_BUCKET/agent_dataset/<table>/:
  replace    latest.parquet          — the whole table, rewritten each run
  partition  <YYYY-MM-DD>.parquet    — one part per day (bank_metrics,
                                       export_runs); a re-run of the same day
                                       overwrites that day's part, and the
                                       BigQuery load replaces only that day's
                                       partition ($YYYYMMDD decorator)
  append     <run-stamp>.parquet     — new rows only (transcripts); the table
                                       grows run over run, keyed by keys.json

Without GCS_BUCKET (local dev) parts go to ./export_out/ and BigQuery is
skipped unless EXPORT_BQ=on. BigQuery dataset: $EXPORT_BQ_DATASET
(default ksk_dashboard), project $GCLOUD_PROJECT.
"""
from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

PREFIX = "agent_dataset"
DATASET = os.environ.get("EXPORT_BQ_DATASET", "ksk_dashboard")
PROJECT = os.environ.get("GCLOUD_PROJECT", "ace-beanbag-486220-a8")
LOCAL_ROOT = Path(__file__).parent.parent.parent / "export_out"


@dataclass
class TableResult:
    name: str
    rows: int
    part: str
    bq: str = "skipped"
    notes: dict = field(default_factory=dict)


def check_columns(name: str, df: pd.DataFrame, columns: dict[str, str]) -> None:
    """The frame's columns must be exactly the dictionary's, in order."""
    got, want = list(df.columns), list(columns)
    if got != want:
        missing = [c for c in want if c not in got]
        extra = [c for c in got if c not in want]
        raise ValueError(f"{name}: columns disagree with the dictionary — "
                         f"missing {missing}, undocumented {extra}"
                         + ("" if missing or extra else ", order differs"))
    blank = [c for c, d in columns.items() if not str(d or "").strip()]
    if blank:
        raise ValueError(f"{name}: columns without a description: {blank}")


def to_parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow")
    return buf.getvalue()


# ── Part storage (GCS, or ./export_out locally) ───────────────────────────

def _bucket():
    from data import cloud_storage
    return cloud_storage._get_bucket() if cloud_storage.is_gcs_enabled() else None


def put_part(table: str, part: str, data: bytes,
             content_type: str = "application/octet-stream") -> str:
    """Store one object; returns its URI (gs://… or a local path)."""
    key = f"{PREFIX}/{table}/{part}"
    bucket = _bucket()
    if bucket is not None:
        bucket.blob(key).upload_from_string(data, content_type=content_type)
        return f"gs://{bucket.name}/{key}"
    path = LOCAL_ROOT / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def get_part(table: str, part: str) -> bytes | None:
    key = f"{PREFIX}/{table}/{part}"
    bucket = _bucket()
    if bucket is not None:
        blob = bucket.get_blob(key)
        return blob.download_as_bytes() if blob is not None else None
    path = LOCAL_ROOT / key
    return path.read_bytes() if path.exists() else None


def list_parts(table: str) -> list[str]:
    """Parquet part URIs of `table`, sorted."""
    bucket = _bucket()
    if bucket is not None:
        return sorted(f"gs://{bucket.name}/{b.name}"
                      for b in bucket.list_blobs(prefix=f"{PREFIX}/{table}/")
                      if b.name.endswith(".parquet"))
    d = LOCAL_ROOT / PREFIX / table
    return sorted(str(p) for p in d.glob("*.parquet")) if d.exists() else []


def read_keys(table: str) -> set[tuple]:
    """The append table's key index (keys.json) — rows already exported."""
    raw = get_part(table, "keys.json")
    return {tuple(k) for k in json.loads(raw)} if raw else set()


def write_keys(table: str, keys: set[tuple]) -> None:
    put_part(table, "keys.json", json.dumps(sorted(list(k) for k in keys)).encode(),
             content_type="application/json")


# ── BigQuery ──────────────────────────────────────────────────────────────

def bq_enabled() -> bool:
    flag = os.environ.get("EXPORT_BQ", "").lower()
    if flag in ("on", "1", "true"):
        return True
    if flag in ("off", "0", "false"):
        return False
    return _bucket() is not None


def _bq_client():
    from google.cloud import bigquery
    return bigquery.Client(project=PROJECT)


def _describe(client, table_id: str, columns: dict[str, str], description: str) -> None:
    """Column + table descriptions (BigQuery shows them to whoever queries)."""
    from google.cloud import bigquery
    table = client.get_table(table_id)
    table.schema = [bigquery.SchemaField(f.name, f.field_type, mode=f.mode,
                                         description=columns.get(f.name, "")[:1024],
                                         fields=f.fields)
                    for f in table.schema]
    table.description = description[:16384]
    client.update_table(table, ["schema", "description"])


def bq_load(table: str, uri: str, columns: dict[str, str], description: str,
            mode: str, partition_field: str | None = None,
            partition_day: date | None = None) -> str:
    """Load one Parquet part into BigQuery. replace → WRITE_TRUNCATE the
    table; partition → WRITE_TRUNCATE that day's partition only; append →
    WRITE_APPEND. New columns are allowed on partition/append loads."""
    from google.api_core.exceptions import NotFound
    from google.cloud import bigquery
    client = _bq_client()
    table_id = f"{PROJECT}.{DATASET}.{table}"
    cfg = bigquery.LoadJobConfig(source_format=bigquery.SourceFormat.PARQUET)
    dest = table_id
    if mode == "replace":
        cfg.write_disposition = bigquery.WriteDisposition.WRITE_TRUNCATE
    else:
        cfg.schema_update_options = [bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION]
        cfg.write_disposition = bigquery.WriteDisposition.WRITE_APPEND
        if mode == "partition":
            cfg.time_partitioning = bigquery.TimePartitioning(field=partition_field)
            try:
                client.get_table(table_id)
                # Existing table: replace just this day's partition.
                dest = f"{table_id}${partition_day:%Y%m%d}"
                cfg.write_disposition = bigquery.WriteDisposition.WRITE_TRUNCATE
            except NotFound:
                cfg.schema_update_options = None    # first load creates it
    if not uri.startswith("gs://"):
        with open(uri, "rb") as fh:
            job = client.load_table_from_file(fh, dest, job_config=cfg)
    else:
        job = client.load_table_from_uri(uri, dest, job_config=cfg)
    job.result()
    _describe(client, table_id, columns, description)
    return "loaded"


def write_table(name: str, df: pd.DataFrame, columns: dict[str, str], *,
                mode: str, description: str, part_day: date | None = None,
                partition_field: str | None = None, run_stamp: str | None = None,
                ) -> TableResult:
    """Validate, store the Parquet part, load BigQuery. mode ∈ replace |
    partition (needs part_day + partition_field) | append (needs run_stamp).
    An append with no rows stores nothing and loads nothing."""
    check_columns(name, df, columns)
    if mode == "replace":
        part = "latest.parquet"
    elif mode == "partition":
        if part_day is None or not partition_field:
            raise ValueError(f"{name}: partition mode needs part_day and partition_field")
        part = f"{part_day.isoformat()}.parquet"
    elif mode == "append":
        if not run_stamp:
            raise ValueError(f"{name}: append mode needs run_stamp")
        if df.empty:
            return TableResult(name, 0, "", "nothing new")
        part = f"{run_stamp}.parquet"
    else:
        raise ValueError(f"{name}: unknown mode {mode!r}")
    uri = put_part(name, part, to_parquet_bytes(df))
    res = TableResult(name, len(df), uri)
    if bq_enabled():
        res.bq = bq_load(name, uri, columns, description, mode,
                         partition_field=partition_field, partition_day=part_day)
    return res
