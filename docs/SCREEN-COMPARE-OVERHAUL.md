# Screen & Compare overhaul — build plan

Owner-confirmed 2026-06-17. Tracks the rebuild of the top-level **Screen & Compare**
section (folds the retired top-level "Screening" + "Peers"). Funnel refactor: keep
the Screen / Compare two-mode split but tie them together through a shared, saved
**Bank Groups** model.

## Audit (current state, pre-overhaul)

Screen sub-view lives inline in `app.py` (the `Screen & Compare … sc_sub == "Screen"`
block); Compare lives in `ui/peer_comparison.py`. Findings that drove this plan:

1. **"Watchlist" and "All Banks" are the same set.** `watchlist = sorted(get_universe_tickers())`
   and the "All Banks" branch also calls `get_universe_tickers()`. Different load paths
   (snapshot vs synchronous rebuild) → same banks can show different freshness.
2. **"Portfolio" is permanently empty** (`portfolio = []` hardcoded) while `portfolio.json`
   holds a real ~30-ticker list the app ignores.
3. **Filters silently drop no-data banks** exactly like failed-threshold banks — conflates
   n/a with "fails the screen" (cardinal-rule adjacent).
4. **Header prints a price source** (`IBKR Live / FMP`) on FDIC/SEC fundamental tables.
5. 16 flat tables in a bare dropdown; filters limited to the active table's columns;
   no Screen↔Compare handoff; no clickable ticker → Company deep-link; no Screen legend.

## Confirmed decisions

- **Ambition:** funnel refactor (keep Screen + Compare, connect them).
- **Bank Groups (the core):** a named, saved list of tickers is a first-class scope object,
  shared by BOTH Screen and Compare. Firm-wide (GCS-backed, like saved screens — no per-user
  identity). Three create-paths: save-from-screen-results, manual builder, edit existing.
  Scope selector = **All banks** + **dynamic cohorts** (asset-size tier, business-mix, from
  `analysis/peer_groups.py`) + **saved groups**. Seed a "Portfolio" group from `portfolio.json`;
  retire the hardcoded `portfolio = []`.
- **Filters:** any metric (not just the active table's columns), AND-combined, with an
  explicit "N excluded: no data" counter — no-data is never silently scored as a failed screen.
- **Tables:** keep all 16 curated column-sets, but group them by theme in the picker with a
  one-line description each. Keep the custom column picker + CSV/Excel export.
- **Polish:** Screen color legend, clickable ticker → `?bank=` deep-link, honest header
  (data freshness, not a price-source label).

## Batches (each leaves the app working; push to main → watch deploy green)

- **B1 — foundation (additive, unwired):** `data/bank_groups.py` (CRUD over `cloud_storage`)
  + `tests/test_bank_groups.py`; `ui/bank_scope.py` with a pure `resolve_scope()` + the
  selector widget. Safe to ship before wiring.
- **B2 — Screen rebuild** (`app.py`): scope selector replaces the Banks dropdown; any-metric
  filters + n/a counter; "save survivors as group"; themed table picker + descriptions;
  honest header; legend; clickable tickers.
- **B3 — Compare rebuild** (`ui/peer_comparison.py`): scope selector adds saved groups to the
  existing Asset/Business/Manual; Screen→Compare "compare this group" handoff.
- **B4 — cleanup:** retire `portfolio = []`, remove orphaned code, final render-verify.

## Screening engine — SNL-grade primitives (user guidance 2026-06-17)

Confirmed: build all four filter primitives; defer point-in-time reconstruction to a
separate backend track; saved-screen **versioning only** this pass (alerts later).

Filter primitives (composable, AND-combined) — `analysis/screen_engine.py`:
1. **Absolute threshold** — SHIPPED (B2). `metric op value`.
2. **Peer-relative** — percentile/rank within the ACTIVE scope/group (e.g. efficiency
   in worst quartile). Uses `peer_groups.compute_peer_percentile`. No new data.
3. **Change / growth** — QoQ / YoY on any metric. Common cases already filterable via
   precomputed deltas (`dep_qoq_growth`, `npl_trend_bps`, `cet1_qoq_pp`, …); the generic
   version recomputes prior-quarter values through the REAL engine
   (`build_bank_metrics` per quarter, FDIC-sourced) — never a reimplementation.
4. **Trend / persistence** — N consecutive quarters of a direction; needs per-metric
   quarterly history in the evaluator (same engine path).

Peer groups gain a **geography (state)** dimension. HQ state = FDIC `STALP`, available
only on the institutions endpoint → add a cached ticker→state resolver
(`data/bank_geography.py`); MSA/county deferred (extra fetch).

**Deferred — point-in-time universe reconstruction** (separate track): the M&A/failure
event data EXISTS (`data/fdic_structure.py`, effective dates + OUT/ACQ roles); as-of-
quarter membership + historical financials for defunct entities is a substantial build.
Screen the CURRENT universe meanwhile, labeled "as of latest". Spec doc TBD.

Build order (each its own verified, shippable batch):
- **B3** — peer-relative primitive (engine + UI). No new data.
- **B4** — state geography groups (ticker→state resolver + scope dimension).
- **B5** — generic change/growth + trend/persistence (history engine; correctness-gated).
- **B6** — Compare rebuild (shared scope selector + Screen→Compare handoff) +
  saved-screen versioning.
- **B7** — retire `portfolio = []`, point-in-time spec doc, cleanup.

## Do-not-touch (other lanes)

Market & Macro + `docs/HOME-MACRO-PLAN.md`; `tests/smoke_live.py` + the deploy smoke job;
any in-flight `ui/home.py` → `ui/components.py` refactor. `ui/peer_rank.py` is a Company
sub-tab (single-bank-vs-peers) — out of scope, left as-is.
