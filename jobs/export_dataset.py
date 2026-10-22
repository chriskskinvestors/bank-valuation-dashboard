"""Cloud Run Job: nightly agent dataset export (BigQuery `ksk_dashboard`).

Writes what the dashboard holds as documented tables an AI agent (or an
analyst) can query without scraping the site — every column described,
every value the dashboard's own (no second calculation):

  banks                   universe directory                    replace
  bank_metrics            the ~165 home/screen metrics per bank  daily partition
  events                  news / filings / earnings, 400 days    replace
  bank_quarterly          FDIC call reports per charter, 1992→   replace
  holdco_quarterly        SEC holdco quarterly series            replace
  statements_as_reported  Financials-tab statements, as filed    replace
  company_reported        figures from the banks' own releases   replace
  valuation_history       the P/TBV · P/E chart series           replace
  transcripts             earnings-call transcripts (FMP)        append
  metric_dictionary       every table's column descriptions      replace
  export_runs             this run's per-table outcome           daily partition

Everything but transcripts and valuation_history's prices is READ-ONLY on
the dashboard's stores and caches (the nightly jobs that build them run
first); transcripts fetch new calls from FMP under a per-run budget, and
valuation_history refreshes each bank's FMP price history (~600 calls,
paced) through the chart's own fetcher. FMP-derived columns (prices, market
cap, the multiples, transcripts) are licensed for internal use only.

Usage:
  python -m jobs.export_dataset                 # every table
  python -m jobs.export_dataset banks,events    # a subset
  python -m jobs.export_dataset --transcripts-budget 200

Exit 0 when every table exported; 1 when any table failed or a core table
came out empty (the failure is in export_runs and the log either way).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

TABLE_DESCRIPTIONS = {
    "banks": "One row per bank in the dashboard's universe (publicly traded "
             "US banks): identifiers (ticker, SEC CIK, FDIC certs) and profile.",
    "bank_metrics": "The dashboard's computed metrics per bank, one row per "
                    "bank per day (partitioned by as_of_date): valuation, "
                    "returns, capital, credit, deposits, loans, securities.",
    "events": "News, SEC filings and earnings events per bank, last 400 days.",
    "bank_quarterly": "FDIC call-report history per bank charter per quarter "
                      "since 1992 (bank subsidiary, not the holding company).",
    "holdco_quarterly": "Holding-company quarterly series from SEC XBRL: equity, "
                        "intangibles, tangible common equity, shares, earnings.",
    "statements_as_reported": "Income statements and balance sheets exactly as "
                              "the banks report them in their 10-Q/10-K filings "
                              "(the dashboard's Financials tab), long format.",
    "company_reported": "Figures from the banks' own earnings releases "
                        "(tangible book value per share, EPS, efficiency...).",
    "valuation_history": "The price-to-tangible-book and P/E history behind the "
                         "dashboard's valuation chart.",
    "transcripts": "Earnings-call transcripts (FMP), one row per call. Internal "
                   "use only (FMP license).",
    "metric_dictionary": "Every exported table's columns with their definitions, "
                         "units and sources.",
    "export_runs": "Outcome of each nightly export per table: rows, status, "
                   "misses (inputs that were not cached).",
}

COLUMNS_METRIC_DICTIONARY = {
    "table_name": "Exported table the column belongs to",
    "column_name": "Column name",
    "description": "Definition, units and source of the column",
    "table_description": "What the table holds",
}

COLUMNS_EXPORT_RUNS = {
    "run_date": "Date of the export run (UTC), the partition key",
    "run_at": "When the table finished exporting (UTC)",
    "table_name": "Exported table",
    "status": "ok | failed | empty | nothing new",
    "rows": "Rows written this run (for transcripts: new calls only)",
    "seconds": "Wall time spent building and writing the table",
    "bigquery": "BigQuery load outcome",
    "notes": "JSON: the builder's counters (misses, skips, budget) or the error",
}

# A core table that comes out empty means an upstream store is missing —
# fail the run rather than publish a blank table as current.
_MUST_HAVE_ROWS = {"banks", "bank_metrics", "bank_quarterly", "holdco_quarterly"}


def _registry():
    """table → (module, build function name, COLUMNS name, mode). Imported
    lazily so one table's import error fails that table, not the run."""
    return {
        "banks": ("data.export.tables_core", "build_banks", "COLUMNS_BANKS", "replace"),
        "bank_metrics": ("data.export.tables_core", "build_bank_metrics",
                         "COLUMNS_BANK_METRICS", "partition"),
        "events": ("data.export.tables_core", "build_events", "COLUMNS_EVENTS", "replace"),
        "bank_quarterly": ("data.export.tables_history", "build_bank_quarterly",
                           "COLUMNS_BANK_QUARTERLY", "replace"),
        "holdco_quarterly": ("data.export.tables_history", "build_holdco_quarterly",
                             "COLUMNS_HOLDCO_QUARTERLY", "replace"),
        "statements_as_reported": ("data.export.tables_statements",
                                   "build_statements_as_reported",
                                   "COLUMNS_STATEMENTS_AS_REPORTED", "replace"),
        "company_reported": ("data.export.tables_statements", "build_company_reported",
                             "COLUMNS_COMPANY_REPORTED", "replace"),
        "valuation_history": ("data.export.tables_valuation", "build_valuation_history",
                              "COLUMNS_VALUATION_HISTORY", "replace"),
        "transcripts": ("data.export.tables_transcripts", "build_transcripts",
                        "COLUMNS_TRANSCRIPTS", "append"),
    }


def _notes(df) -> dict:
    attrs = getattr(df, "attrs", None) or {}
    return {k: (v if isinstance(v, (int, float, str, bool, type(None))) else str(v))
            for k, v in attrs.items()}


def export_table(name: str, tickers: list[str], run_day: date, run_stamp: str,
                 transcripts_budget: int) -> dict:
    """Build + write one table; returns its export_runs row (never raises)."""
    import importlib
    from data.export import writer
    t0 = time.time()
    row = {"run_date": run_day, "table_name": name, "status": "ok", "rows": 0,
           "bigquery": "", "notes": "{}"}
    try:
        mod_name, fn_name, cols_name, mode = _registry()[name]
        mod = importlib.import_module(mod_name)
        build, columns = getattr(mod, fn_name), getattr(mod, cols_name)
        if name == "bank_metrics":
            df = build(tickers, as_of=run_day)
            res = writer.write_table(name, df, columns, mode=mode, part_day=run_day,
                                     partition_field="as_of_date",
                                     description=TABLE_DESCRIPTIONS[name])
        elif name == "transcripts":
            key = ("ticker", "fiscal_year", "quarter")
            have = writer.read_keys(name)
            df = build(tickers, have=have, max_fetch=transcripts_budget)
            res = writer.write_table(name, df, columns, mode=mode, run_stamp=run_stamp,
                                     description=TABLE_DESCRIPTIONS[name])
            if len(df):
                # Keys only after the part is stored and loaded: a failed load
                # re-fetches those calls next run instead of dropping them.
                new = {tuple(r) for r in df[list(key)].itertuples(index=False, name=None)}
                writer.write_keys(name, have | new)
        else:
            # valuation_history refreshes each bank's FMP price history first
            # (else it is only as current as the last chart view).
            df = (build(tickers, refresh_prices=True) if name == "valuation_history"
                  else build(tickers))
            res = writer.write_table(name, df, columns, mode=mode,
                                     description=TABLE_DESCRIPTIONS[name])
        row.update(rows=res.rows, bigquery=res.bq,
                   notes=json.dumps(_notes(df), default=str))
        if res.rows == 0:
            row["status"] = "nothing new" if mode == "append" else "empty"
    except Exception as e:
        traceback.print_exc()
        row.update(status="failed", notes=json.dumps({"error": f"{type(e).__name__}: {e}"}))
    row["seconds"] = round(time.time() - t0, 1)
    row["run_at"] = datetime.now(timezone.utc)
    print(f"[export] {name:<24} {row['status']:<12} rows={row['rows']:<8} "
          f"{row['seconds']:>7}s  bq={row['bigquery']}  {row['notes'][:300]}", flush=True)
    return row


def export_dictionary(tables: list[str]) -> dict:
    """metric_dictionary from every table's own column dict (+ this job's)."""
    import importlib
    import pandas as pd
    from data.export import writer
    t0 = time.time()
    rows = []
    for name, (mod_name, _, cols_name, _) in _registry().items():
        try:
            cols = getattr(importlib.import_module(mod_name), cols_name)
        except Exception as e:
            print(f"[export] dictionary: {name} columns unavailable ({type(e).__name__}: {e})")
            continue
        rows += [(name, c, d, TABLE_DESCRIPTIONS[name]) for c, d in cols.items()]
    for name, cols in (("metric_dictionary", COLUMNS_METRIC_DICTIONARY),
                       ("export_runs", COLUMNS_EXPORT_RUNS)):
        rows += [(name, c, d, TABLE_DESCRIPTIONS[name]) for c, d in cols.items()]
    df = pd.DataFrame(rows, columns=list(COLUMNS_METRIC_DICTIONARY))
    res = writer.write_table("metric_dictionary", df, COLUMNS_METRIC_DICTIONARY,
                             mode="replace",
                             description=TABLE_DESCRIPTIONS["metric_dictionary"])
    print(f"[export] metric_dictionary        ok           rows={res.rows:<8} "
          f"{time.time() - t0:>7.1f}s  bq={res.bq}", flush=True)
    return {"table_name": "metric_dictionary", "status": "ok", "rows": res.rows,
            "bigquery": res.bq, "notes": "{}", "seconds": round(time.time() - t0, 1)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tables", nargs="?", default="all",
                    help="comma-separated table names, or 'all'")
    ap.add_argument("--transcripts-budget", type=int, default=800,
                    help="max transcripts fetched from FMP this run")
    a = ap.parse_args(argv)
    names = list(_registry()) if a.tables == "all" else [t.strip() for t in a.tables.split(",")]
    unknown = [n for n in names if n not in _registry()]
    if unknown:
        print(f"[export] unknown table(s): {unknown}; known: {list(_registry())}")
        return 2

    import pandas as pd
    from data.bank_universe import get_universe_tickers
    from data.export import writer
    run_at = datetime.now(timezone.utc)
    run_day, run_stamp = run_at.date(), run_at.strftime("%Y-%m-%dT%H%M%SZ")
    tickers = get_universe_tickers()
    print(f"[export] {len(tickers)} banks · tables {names} · BigQuery "
          f"{'on → ' + writer.PROJECT + '.' + writer.DATASET if writer.bq_enabled() else 'off'}",
          flush=True)

    runs = [export_table(n, tickers, run_day, run_stamp, a.transcripts_budget)
            for n in names]
    try:
        d = export_dictionary(names)
        d.update(run_date=run_day, run_at=datetime.now(timezone.utc))
        runs.append(d)
    except Exception as e:
        traceback.print_exc()
        runs.append({"run_date": run_day, "run_at": datetime.now(timezone.utc),
                     "table_name": "metric_dictionary", "status": "failed", "rows": 0,
                     "seconds": 0.0, "bigquery": "",
                     "notes": json.dumps({"error": f"{type(e).__name__}: {e}"})})

    bad = [r["table_name"] for r in runs if r["status"] == "failed"
           or (r["status"] == "empty" and r["table_name"] in _MUST_HAVE_ROWS)]
    try:
        log = pd.DataFrame(runs)[list(COLUMNS_EXPORT_RUNS)]
        writer.write_table("export_runs", log, COLUMNS_EXPORT_RUNS, mode="partition",
                           part_day=run_day, partition_field="run_date",
                           description=TABLE_DESCRIPTIONS["export_runs"])
    except Exception:
        traceback.print_exc()
        bad.append("export_runs")
    if bad:
        print(f"[export] FAILED: {', '.join(bad)}")
        return 1
    print(f"[export] done: {len(runs)} tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
