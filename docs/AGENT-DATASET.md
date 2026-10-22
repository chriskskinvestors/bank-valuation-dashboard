# Agent dataset (BigQuery `ace-beanbag-486220-a8.ksk_dashboard`)

What the dashboard holds, as documented tables an AI agent or analyst can
query without scraping the site. Built nightly by `jobs/export_dataset.py`
(Cloud Run job `export-dataset`, 12:15 UTC, after the overnight data jobs).
Owner decision 2026-10-22.

## Ground rules for readers

- **Every column is described.** Start with `metric_dictionary`
  (table, column, definition/units/source) — BigQuery also shows the same
  descriptions on each table's schema.
- **Values are the dashboard's own**, not a second calculation: the export
  reads the same snapshot, caches and seams the pages render from.
- **Absent is NULL, never 0.** A NULL means the dashboard shows n/a.
- **Dollars are raw dollars** (FDIC's $thousands are converted); ratios say
  percent vs fraction in their description.
- **Bank vs holding company:** `bank_quarterly` and FDIC-sourced metrics
  describe the bank subsidiary (call report); `holdco_quarterly`,
  statements and SEC-sourced metrics describe the holding company. They
  differ structurally.
- **Licensing:** prices, market cap, the multiples and `transcripts` derive
  from FMP — internal KSK use only. SEC/FDIC data has no such limit.
- **Freshness:** check `export_runs` (per table, per night: rows, status,
  misses) before trusting a table's currency.

## Tables

| Table | Grain | Refresh |
|---|---|---|
| `banks` | bank | replaced nightly |
| `bank_metrics` | bank × day (partition `as_of_date`) | one partition per night — history accumulates |
| `events` | news / filing / earnings event, last 400 days | replaced nightly |
| `bank_quarterly` | FDIC charter × quarter, 1992 → | replaced nightly |
| `holdco_quarterly` | holdco × quarter-end (SEC XBRL) | replaced nightly |
| `statements_as_reported` | statement line × period (income, balance; 12 quarters, 5 years) | replaced nightly |
| `company_reported` | bank × quarter × metric from earnings releases | replaced nightly |
| `valuation_history` | bank × date (month-ends, filing steps, latest) — the P/TBV·P/E chart | replaced nightly |
| `transcripts` | earnings call | appended (new calls only) |
| `metric_dictionary` | table × column | replaced nightly |
| `export_runs` | table × night (partition `run_date`) | one partition per night |

Not exported (yet): securities / fair-value / loan- and deposit-mix /
holdco-capital / credit-quality schedules (assembled live from the filing
index — exporting them would mean a second implementation), Valuation
Model what-ifs, user-entered data, filing full text.

## Example queries

```sql
-- Latest metrics for one bank
SELECT ticker, price, ptbv_ratio, tbvps, tbvps_source, pe_ratio, roatce_holdco
FROM ksk_dashboard.bank_metrics
WHERE ticker = 'HFWA' AND as_of_date = (SELECT MAX(as_of_date) FROM ksk_dashboard.bank_metrics);

-- What management said about deposit costs last quarter
SELECT ticker, fiscal_year, quarter, call_date
FROM ksk_dashboard.transcripts
WHERE REGEXP_CONTAINS(LOWER(content), r'deposit costs?') AND call_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 120 DAY);
```

## Operating

- Run by hand: `gh workflow run run-job.yml -f job=export-dataset`
  (subset: `-f args="-m,jobs.export_dataset,banks,events"`; first
  transcript backfill: `-f args="-m,jobs.export_dataset,transcripts,--transcripts-budget,4000"`).
- Durable copy: `gs://ksk-bank-dashboard-data/agent_dataset/<table>/` (Parquet);
  BigQuery is loaded from it. `transcripts/keys.json` indexes exported calls.
- A failed table or an empty core table fails the run (and is in `export_runs`).
- Adding a dashboard metric: `tests/test_export_core.py` fails until the
  metric gets a description in `data/export/tables_core.py`.
