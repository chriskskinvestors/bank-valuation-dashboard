# Point-in-time universe reconstruction — deferred spec

Status: **deferred** (user decision 2026-06-17 — separate backend track; do not block
the screening UX on it). The interactive screener runs the **current** universe,
labeled "as of latest". This doc specs the work to support "screen as of quarter Q".

## Why it matters

Institutions disappear (acquired) or fail. A screen or backtest run "as of Q2 2023"
must reconstruct the universe that *existed then* — not project today's survivors back.
Using today's universe for historical screens silently drops failed/acquired banks,
which biases deposit-share trends, peer-group medians, and any backtested screen
(survivorship bias). Peer membership is itself point-in-time: a bank enters/exits its
peer group via M&A and failure at specific effective dates.

## What already exists (the foundation)

- **`data/fdic_structure.py`** — FDIC structure-change history (`/banks/history`):
  mergers, absorptions, failures, charter events. CHANGECODE taxonomy verified live;
  merger records carry `OUT_*` (absorbed), `ACQ_*` (acquirer) roles **with effective
  dates**. This is the entity-event graph's raw feed.
- **`data/loaders.load_fdic_hist`** — ~20 quarters of per-bank FDIC financials.
- **`analysis/metrics.build_bank_metrics`** — computes a faithful metric dict from any
  single historical quarter's FDIC record (proven by the change/trend primitives).

So the per-quarter *financials* and the *events* both exist. What's missing is the
**as-of membership resolution** that ties them together over time.

## What to build

1. **Entity graph** (`data/entity_graph.py`): ingest `fdic_structure` events into a
   directed graph keyed by FDIC cert / RSSD, with edges (absorbed→survivor) stamped
   with effective dates. Persist as a versioned snapshot (nightly job, like the
   universe build). Source of truth for "did this cert exist / who absorbed it, when".
2. **As-of universe resolver**: `universe_as_of(quarter) -> set[cert]` — the certs
   ACTIVE at that quarter, reconstructed by walking the graph backward from the current
   universe + re-adding entities terminated after `quarter`. Must handle banks that have
   since failed (no current ticker) — these need name/cert-only rows.
3. **As-of metrics**: extend `build_bank_metrics` callers to fetch the FDIC record for
   `quarter` for every as-of cert (incl. defunct ones — the financials endpoint still
   serves historical REPDTEs by cert). Cache per (cert, quarter).
4. **Point-in-time peer groups**: peer-relative / percentile filters resolve against the
   as-of cohort at `quarter`, not today's.
5. **UI**: an "as of" quarter picker on Screen; default = latest (today's behavior). When
   a past quarter is chosen, banner the reconstruction (n entities, n since-failed/acquired).

## Hard parts / risks

- **Ticker↔cert over time**: tickers are reused/retired; the graph must key on cert/RSSD,
  not ticker. `bank_mapping` is current-only.
- **Defunct entities have no SEC/price data** — historical screens must be FDIC-only for
  failed banks, clearly labeled (cardinal rule: never fabricate a HoldCo metric for a
  bank that has no holdco data that quarter).
- **Cost**: as-of metrics for a full historical universe (4,500+ insured institutions,
  not just the ~470 public ones) is a large fetch/compute — needs a batch job, not
  interactive build.
- **Coverage gaps**: FDIC `/banks/history` completeness for older events; reconcile
  against known failure lists.

## Sequencing

Build 1–2 (graph + resolver) first behind a job + tests pinning known M&A timelines
(e.g. reconstruct Q1-2023 and assert First Republic / SVB are present pre-failure). Only
then wire 3–5. Until shipped, every historical/screen surface stays labeled "as of latest".
