# Point-in-time universe reconstruction

Status: **BUILT (v1 shipped 2026-06-18)** — the user pulled it forward. The Screen
view has an "As of" quarter picker that reconstructs the universe as it filed at a
past quarter-end and screens over FDIC point-in-time fundamentals.

## What shipped (v1)

- **`data/entity_graph.py`** (increment 1): as-of membership from FDIC institution
  charter dates (`ESTYMD ≤ Q ≤ ENDEFYMD`); a tracked public-failures registry; lineage
  helper. Ground-truth tested (SVB/Signature/First Republic in Q1-2023, gone by year-end).
- **`data/as_of_metrics.py` + `fdic_client.fetch_quarter_financials`** (increment 2):
  one quarter's financials for the whole banking system in ~5 paginated calls (≈26s,
  cached), built through the real engine. Single-quarter mode → market/SEC and all 35
  history-dependent metrics (4Q averages, trends, fair-value) are n/a, never guessed.
- **Screen "As of" picker** (last 20 quarters): swaps the screen to the reconstruction
  with an amber banner stating quarter, bank count, since-exited count, and the
  FDIC-point-in-time/n/a provenance. Verified: As-of Q4 2022 → SVB at $209.026B etc.

## Known v1 limitations (labeled, not wrong)

- Candidate set = today's public banks + tracked failures. **Lineage** (a target
  absorbed by a current bank after Q, shown separately at Q) is NOT yet expanded in the
  UI — the surviving acquirer shows its own correct Q filing, but the absorbed target is
  absent. `entity_graph.public_universe_as_of(..., with_lineage=True)` is the hook.
- **Multi-quarter as-of metrics** (4Q / trends) are n/a in single-quarter mode. A
  windowed fetch (N quarter-batches up to Q) would enable them at extra latency.
- Defunct banks have no Company page, so their ticker cell does not deep-link.

## Original spec (retained)

The interactive screener also still runs the **current** universe by default
("Latest (live)"). The remaining backend depth below is the future-fidelity track.

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
