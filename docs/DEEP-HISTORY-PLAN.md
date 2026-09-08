# Deep Financial History — build plan (2026-09-08)

Status: **PLANNED — awaiting owner sign-off on the three decisions at the
bottom.** Cost brief delivered 2026-09-08: source data is free (primary
sources), storage fits the existing 10GB Cloud SQL provision (~+0.4-0.8GB),
one-time backfill compute ~$5, recurring ~$0. The cost is engineering and
validation care, not money.

## Goal

Extend per-bank fundamental history from today's ~20 quarters (5y) to the
full available depth, universe-wide, served warm:

| Source | Depth available | What it powers |
|---|---|---|
| FDIC call reports (bank-sub) | **1992 →** (~135 quarters) | every call-report ratio: capital, credit, deposits, NIM, efficiency |
| SEC companyfacts XBRL (HoldCo) | **~2009 →** (already fetched in full) | HoldCo statements, EPS/TBVPS series |
| FFIEC (rate ladders) | 2001 → (bulk CDR) | out of scope here — quarterly job unchanged |
| FMP prices | plan-dependent — VERIFY before promising >5y price overlays | long-run valuation multiples |

Views are labeled by entity: bank-sub series to 1992, HoldCo to 2009 —
never blended, gaps render n/a (cardinal rule). Pre-XBRL HoldCo parsing is
REJECTED (fragile per-bank HTML, the same reason the MD&A parser was
deferred indefinitely).

## Architecture (jobs build, renders read — unchanged doctrine)

Today the history lives as `fdic_hist:{ticker}` JSON cache entries capped
at 20 quarters. Deep history gets a REAL table:

```
fdic_history(
  cert     INTEGER,
  repdte   DATE,
  fields   JSONB,          -- the ~212-field record fetch_financials returns
  PRIMARY KEY (cert, repdte)
)
```

- **Producer seam is unchanged and mandatory:** `data/cert_group.
  fetch_group_history` remains THE seam (multi-charter memory) — the deep
  store is populated per-cert and consolidated per-REPDTE through the same
  group logic at read time, so WTFC-class holdcos stay correct at depth.
- **Backfill job** `jobs/backfill_fdic_history.py` (one-time, resumable):
  walks all ~620 active certs, pages the FDIC financials endpoint back to
  1992 (YYYYMMDD REPDTE format — the API-trap memory), upserts
  (cert, repdte) rows, checkpoints per cert so a rerun continues, paced
  under FDIC courtesy limits. Est. runtime 1-3h, ~$2-5 once.
- **Nightly append**: refresh-universe already touches every bank; add an
  incremental step that upserts only quarters newer than the stored max
  per cert (a no-op most nights, one new quarter per bank per quarter).
- **Reader**: `data/loaders.load_fdic_hist(limit=...)` reads the table
  (group-consolidated) instead of the capped cache entry; the `limit`
  parameter becomes real depth. The hot 20-quarter path keeps its warm
  cache in front so current pages get zero slower. Callers unchanged.

## Validation at depth (where the engineering days go)

1. **Field availability drift**: many of the 212 fields simply don't exist
   in 1990s records — absent = n/a in every derived ratio, never 0
   (fabricated-aggregate defect-class memory). Add a per-field first-
   available audit so tabs can annotate "series begins YYYY".
2. **Structure breaks**: mergers/charter changes make raw levels
   discontinuous. Group consolidation handles the certs we know; a
   level-jump flag (>50% q/q on assets) marks rows for the banner rather
   than silently charting a cliff.
3. **Golden dataset extension**: hand-verify (tools/golden_handcheck.py
   discipline — never pipeline output) 2-3 banks' deep values against raw
   FDIC records: one simple bank ~1995, one multi-charter holdco ~2005,
   one merger-heavy bank across its break. Pin in tests/golden.
4. **Growth gate**: the nightly validation gate must not choke on deep
   rows — validation stays scoped to recent quarters; deep rows validated
   once at backfill.

## UI phases (each ships separately, mock-first where pixel-level)

- **Phase 1 — dynamics tabs** (capital / credit / deposit / rate,
  financial highlights trends): range picker gains 10Y / 20Y / MAX. The
  chart builders already take a `quarters` param; this is plumbing + the
  series-begins annotation. *~2 days incl. validation.*
- **Phase 2 — statement pages**: Templated statements get deep paging
  (columns beyond 20 quarters, paged or range-picked; bordered style per
  the 2026-09-04 owner call). *~1-2 days.*
- **Phase 3 — long-run context** (needs FMP price-depth verification
  first): valuation percentile vs own 20-30y history (P/TBV vs long-run
  median). Fundamentals-only fallback if price depth is short. *~2 days.*

Backfill + store + reader (pre-phase foundation): *~2 days incl. golden
handchecks.* Total ≈ 7-9 focused days.

## Rollout order

1. Foundation PR (table + backfill job + reader change + tests) → deploy
   → create job once → run backfill in prod → spot-check golden values.
2. Phase 1 PR → verify on prod (charts at MAX for the golden banks).
3. Phases 2-3 as separate PRs.

## Owner decisions needed before the foundation PR

1. **Depth**: full 1992 (recommended — the cost difference vs 2000 is
   nothing) or a round 2000 start?
2. **Field set**: the 212 fields we already use (recommended) or all
   ~1,100 FDIC fields (+~2GB, still fits; only worth it if new deep
   metrics are planned soon)?
3. **Phase order**: 1 → 2 → 3 as above, or statements first?
