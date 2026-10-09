# Screen & Compare overhaul — build plan

Owner-confirmed 2026-06-17. Tracks the rebuild of the top-level **Screen & Compare**
section (folds the retired top-level "Screening" + "Peers"). Funnel refactor: keep
the Screen / Compare two-mode split but tie them together through a shared, saved
**Bank Groups** model.

## Audit (current state, pre-overhaul)

Screen sub-view lives inline in `app.py` (the `Screen & Compare … sc_sub == "Screen"`
block; since B8 it is launcher → builder → Run → results, see the build order
below); Compare lives in `ui/peer_comparison.py`. Findings that drove this plan:

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
- **B1** ✅ groups foundation (`data/bank_groups.py`, `ui/bank_scope.py`). Deployed green.
- **B2** ✅ Screen rebuild (scope selector, any-metric filters + n/a counter, save-as-group,
  honest header, themed picker, legend, na_rep). Deployed green.
- **B3** ✅ peer-relative primitive + `analysis/screen_engine.py`. Deployed green.
- **B4** ✅ state/region geography groups (`data/bank_geography.py`). Deployed (with B5).
- **B5** ✅ generic change/growth + trend/persistence (`analysis/metric_history.py`;
  recompute via the real engine; validated on real data). Deployed (with B4).
- **B6** ✅ Compare rebuild (shared scope selector, cohort-vs-display, Screen→Compare
  handoff). Render-verified; push pending B4+B5 green.
- **B7** ✅ CLOSED 2026-08-20. Reconciled against code: the hardcoded
  `portfolio = []` was already retired by the Bank Groups seed
  (`data/bank_groups.ensure_portfolio_seed` from `portfolio.json`); the last
  orphan (`config.DEFAULT_PORTFOLIO`, zero references) removed this pass.
  Point-in-time spec doc EXISTS and is BUILT (`docs/POINT-IN-TIME-RECONSTRUCTION.md`
  — v1 pulled forward 2026-06-18, 20-quarter window). Saved-screen versioning
  BUILT (`data/saved_screens.py` versioned save/load + history,
  `tests/test_saved_screens.py`, wired in `app.py`).
- **B8** ✅ SHIPPED 2026-09-24 (PR #144, CI follow-up #151) — **"build, then run"**
  (owner directive: "refresh the entire selection process"; design approved on a
  real-data mock). Launcher (New screen · Saved screens rows: name · table ·
  filter count · version · saved date · "ran HH:MM" this session · Recent, this
  session; a row loads AND runs). One builder panel replaces the control-bar
  dropdowns and the Filters / Columns / Export / Saved dialogs: row 1 Table ·
  Scope (+ secondary picker) · As of; row 2 filters inline, one line each
  (Type / Metric / Op / Value, ✕, `+ Add filter`, cap 4); row 3 Columns
  (popover) · Sort · Order; primary **Run screen**. Gating: the builder edits
  the DRAFT (the same `filt_*_{tab}_{i}` / scope / sort / column session keys,
  so saved screens restore unchanged); Run snapshots draft → `_screen_applied`,
  evaluates ONCE through `analysis/screen_engine` (semantics untouched) and
  stores the result set in `_screen_result`; the results block reads only those
  two, and unrun edits are flagged next to Run. Results bar: Save screen
  (popover; saves the screen AS LAST RUN, re-save bumps the version), Groups
  (popover — the old bank-groups panel), Compare hand-off (≤30), heatmap,
  Export via `ui/export.table_export`. As-of stays in the builder: picking a
  quarter reconstructs that quarter's universe on pick (cached) so the scope
  picker lists the right cohorts; evaluation still waits for Run. Dropped with
  the owner's OK: the quick "Add bank" box (Manual scope has search) and
  version rollback from the launcher (rows open the current version). Scope
  types kept as the existing seven. Tests: `tests/test_screen_builder.py`
  (hermetic — round trip incl. the pre-kind saved format, per-type spec
  serialization, ✕ shifts every key suffix, structural pins: dialogs gone,
  Run button, results block never reads the draft) and
  `tests/test_screen_run_gating.py` (AppTest, own process in ci.yml — edits
  change nothing until Run; Save → launcher → open reruns identically).
  Prod-verified 2026-09-24: 599-bank universe; P/TBV < 1.2 left unrun kept
  599 banks with the flag showing; Run → 96 banks · 1 filter · 270 excluded
  (no data). Not done: persisted last-run time on saved screens (needs
  `data/saved_screens.py`), Recent across sessions.
- **B9** ✅ SHIPPED 2026-09-25 (PR #164) — **Rate & Funding Risk table** (owner:
  "we need to be able to do stuff like this", a third-party *Higher Rate Bank
  Screen*; scoring explicitly out of scope). New screenable bank-sub FDIC
  metrics, each built from summed LEVELS so multi-charter groups stay exact:
  non-core funding (1 − COREDEP/LIAB), time dep % dom dep (NTRTIME/DEPDOM), CDs
  maturing/repricing ≤3M and ≤12M % dom dep (CD3LES+CD3LESS[+CD3T12+CD3T12S] /
  DEPDOM — FDIC's >$250K and ≤$250K buckets), CD book rate (quarter CD interest
  ECD100Q+EOTHTIMQ ×4 over avg begin/end NTRTIME) and CD rate − 6M bill (FRED
  DGS6MO at the quarter end, injected; n/a without it). 84/84 values matched the
  reference screen on 12 banks. Fixed alongside: "Unreal G/L" read IGLSEC
  (REALIZED gains) → AFS mark SCAF−SCAA; "HTM Unreal" read SCSNHAA (structured
  notes; JPM $0 vs a −$18.2B mark) → SCHF−SCHA; both labeled pre-tax, keys kept.
  Not shipped: AOCI % TCE — FDIC EQCCOMPI is YEAR-TO-DATE OCI, not the balance
  (JPM 3.1→8.5B through 2025, −3.1B in Q1 2026). Bank-sub AOCI = FFIEC RC-R
  B530; holdco AOCI from SEC AccumulatedOtherComprehensiveIncomeLossNetOfTax
  measured at ~81% coverage (10/10 reference banks within 0.22pt) — **open owner
  decisions**: holdco coverage (ship ~81% vs parse filings for ~86%), year-end-
  only goodwill (21 banks), AOCI+HTM basis. Uninsured % is wrong for
  foreign-office banks (domestic numerator ÷ total deposits; C 47.8 vs ref 76.8)
  but ÷DEPDOM gives STT 102% — needs its own investigation, unchanged.
- **B10** ✅ SHIPPED 2026-10-01 (PRs #175 stage 1, #183 stage 2) — **any call
  report field is screenable** (owner: "have any call report field be an
  option"). Stage 1 (live, prod-verified): builder gains **+ Call report field**
  (search ~2,190 FDIC BankFind financials fields by code/name) and **ƒ Formula**
  (name + expression over field codes, + − * / ( ), shown as number/%/$/x);
  anything added is a column and appears in Filter and Sort (all filter types,
  incl. Change/Trend via per-quarter fetches). One FDIC request per quarter
  covers every filer (~2-3 s cold, cached a week); latest published quarter or
  the As-of quarter. Saved screens/Recent carry the definitions (`dyn`); on
  reopen a field must still be in the catalog and a formula must still parse.
  Units: $K levels → dollars at the boundary; ratios/counts as reported;
  unproven fields "(as reported)". FDIC's dictionary cannot tell $ from ratios
  (its "double" tag marks 25 $ items and misses ~20 ratios) — the vendored
  `data/fdic_field_catalog.json` is classified from real values by
  `tools/build_fdic_field_catalog.py` (top-50 banks, then all filers); 38 traps
  pinned. Multi-charter: levels/counts strict-sum, ratios only via
  cert_group's exact quotients, else n/a. Grid header = field code, full title
  as tooltip/export header. Verified: a hand-typed CDs-≤12M formula matched
  the reference screen on 10 banks incl. JPM/WFC/BAC/BNY groups; prod CD3LES
  filled for 593/598 banks (JPM $157.7B, BAC $31.1B = sum of charters). Stage 2
  (deployed, **no data until the next refresh-ffiec run** — Nov 1 or manual):
  refresh-ffiec also stores EVERY line item of the call report it already
  downloads (`data/call_report_full.py`, long format by raw MDRM; Fed MDRM
  dictionary for titles; blank → NULL; a late filer's prior-quarter fallback is
  refused, not stored under the wrong quarter). **Open:** add FFIEC line items
  to the picker once data lands (brings bank-sub AOCI B530); MDRM→schedule map
  needs the FFIEC taxonomy; the local FFIEC JWT expired ~2026-08-31 — prod
  token health unverified (owner: Secret Manager); pre-existing bug found:
  existing call_report_store schedules are stamped with the requested quarter
  even when fetch_call_report fell back to the prior one (late filers).

- **B11** ✅ SHIPPED 2026-10-02/03 (PRs #240, #242, #244) — **"make it perfect"
  pass** (owner). Closes the open items of B9/B10 that needed no owner decision
  and all 13 defects confirmed by an adversarial review of #164/#175/#183
  (nothing in #164). Prod-verified 2026-10-03.
  - **Uninsured %** (B9 open item, RESOLVED): both producers (analysis/
    valuation screen metric, analysis/deposit_dynamics timeline) divided
    DEPUNINS (domestic + insured territory branches) by DEP (incl. foreign
    offices). Now DEPUNINS ÷ (DEPINS + DEPUNINS), the FDIC insurance base —
    matches the reference screen on 12/12 banks (C 47.8→76.8, STT 75.9→92.3);
    validation band max 75 → 100. Shows after the metrics snapshot rebuilds.
  - **Wrong numbers from the review**: FDIC literal-0 ratios over a zero
    denominator (1,831 CBLR banks RBCRWAJ 0.00%) now go through the pipeline's
    own null_unreported_capital + null_undefined_quotients; the catalog builder
    adds integer evidence across every filer (FDIC reports $ as whole $K) so
    NTCOMREQ & co. are $ (OZK $42.4M, was "42,437.00"), constant ratios
    (ASSETR/LIABEQR/IDNTILR = 100) guarded, CBLRIND (flag) excluded; OFFSTATE
    is n/a for charter groups (WFC showed 42 states); converted $ fields'
    labels/export headers say "($)".
  - **Crash/state from the review**: FDIC outage → n/a + "partly unavailable"
    warning; formula size/constant limits, overflow/recursion/inf/nan → n/a;
    filter Metric + Sort store the metric KEY (positions silently re-pointed
    when the option list changed; legacy sort_idx mapped once on restore; ""
    is the Default/— option — None rendered an empty "Choose an option",
    #244); formula keys = name + hash of the definition (no cross-session
    label/format collisions; redefining a name migrates its references);
    Peer Comparison picker lists only the static registry; change/trend
    history fetched once per quarter for all banks; FFIEC dates passed ISO;
    As-of caption states the mapped-charter rule.
  - **FFIEC line items in the picker** (B10 open item, RESOLVED in code):
    "+ Call report field" lists every MDRM line item in the full-report store
    (usable in formulas with FDIC codes; $ items $K→$; non-monetary as filed;
    groups strict-sum $ items, else n/a). Empty until the next refresh-ffiec
    run.
  - **Late-filer quarter stamp** (B10 open item, RESOLVED, #240):
    refresh-ffiec stores each report under the frame's OWN quarter (late
    filers' prior-quarter reports no longer land in the new quarter); a frame
    without one determinable quarter is stored nowhere. Read-only
    `tools/diagnose_ffiec_period_mislabels.py --prod` lists rows already
    mislabeled — **not yet run against prod** (owner).
  - Sticky ticker column on wide result grids.
  - **Still open (owner)**: holdco AOCI decisions (coverage ~81% vs parse
    filings ~86%; year-end-only goodwill for 21 banks; AOCI+HTM basis); FFIEC
    JWT health in Secret Manager (local copy expired ~2026-08-31); run the
    mislabel diagnostic in prod.

- **B12** ✅ SHIPPED 2026-10-05..08 (PRs #249, #350, #353) — **AOCI % TCE
  columns** (owner: "use your recommendations") + **As-of / Trends repair**.
  Prod-verified 2026-10-08/09.
  - **AOCI % TCE, both bases** (B9 deferred item, RESOLVED #249): five
    Rate & Funding Risk columns — AOCI % TCE (HoldCo), AOCI+HTM % TCE
    (HoldCo, HTM pre-tax), TCE GW prior FY (flag), AOCI % TCE (Bank), AOCI+HTM
    % TCE (Bank, HTM pre-tax). HoldCo = SEC AccumulatedOtherComprehensiveIncome-
    LossNetOfTax read only AT the parent-equity date ÷ (equity − preferred −
    the goodwill+intangibles adjustment tangible book uses). Bank = RC-R Part I
    item 3 (B530) ÷ (EQTOT − INTAN), strict-summed across charters. HTM mark =
    SCHF − SCHA, pre-tax, on both. n/a: AOCI from another period, preferred
    unresolved, TCE ≤ 0, any charter missing the item. Owner decisions
    applied: ship holdco at the coverage SEC data allows; year-end-only
    goodwill used as last reported and FLAGGED (21 banks, e.g. ZION); both
    bases as columns. **Prod 2026-10-09: HoldCo filled for 321 banks (~92% of
    the ~347 SEC filers; non-SEC banks n/a); 8/8 reference banks within
    0.2pt** (JPM −2.6/−2.5, C −23.3/−23.4, BNY −17.0/−17.0, USB −15.6/−15.6,
    BAC −5.9/−5.8, WFC −5.9/−5.7, TFC −16.6/−16.5, FHB −19.7/−19.7). **Bank
    columns are n/a for every bank**: the store's newest RC-R quarter is
    2026-03-31 because the prod FFIEC token expired ~2026-08-30 (health check
    2026-10-05: −36 days) — the code correctly refuses Q1 AOCI against Q2
    equity. Units checked on stored rows (Citibank AOCI −$23.5B).
  - **As-of screens, Trends and earnings comparisons were empty 10-05..08**
    (#350): FDIC caps `limit` at 500 once a request names >250 fields
    (measured: 250 → 200, 251+ → 400). #272 took the base field set to 253;
    every quarter fetch 400'd and `_fetch_fin_page` swallowed it as "no bank
    filed" — "No FDIC filings reconstructed for Q4 2021" (cached 24h), the
    nightly all-banks Trends grid failed, earnings prior/YoY went blank. Now
    pages at 500; a failed page raises `FdicQuarterFetchError` (screen/Trends
    say FDIC could not be fetched; nothing cached); empty unpublished quarters
    not cached. Q4 2021 whole system 4,904 rows = FDIC meta.total.
  - **As-of rows carried TODAY's SEC/8-K values and never finished** (#353,
    found verifying #350): the engine got the ticker, so "as of Q4 2021"
    showed BAC TBVPS 29.37 (its 2026 8-K; actual ≈ 21.68) and JPM buybacks
    $31.7B (current TTM), at ~6.4 s/bank — past the request timeout for the
    universe. Engine now gets ticker=None (FDIC-only, as designed; SEC/market
    n/a); cache key v3. Prod: Q4 2021 → 836 banks incl. 243 since-exited
    (SVB, Signature, First Republic, Silvergate) in ~2½ min first load; CRE %
    JPM 3.9 (= 42,314,000 / 1,085,106,000), WFC 10.6, BAC 6.2, C 1.9.
  - **Still open (owner)**: renew the FFIEC token in Secret Manager, then
    refresh-ffiec for 06/30/2026 fills bank AOCI (and every other FFIEC-fed
    view stuck at Q1); the Nov 1 scheduled run also fails until renewed. Run
    the mislabel diagnostic in prod. All-banks Trends grid repopulates on the
    next nightly refresh-trends (not yet verified).

## Do-not-touch (other lanes)

Market & Macro + `docs/HOME-MACRO-PLAN.md`; `tests/smoke_live.py` + the deploy smoke job;
any in-flight `ui/home.py` → `ui/components.py` refactor. `ui/peer_rank.py` is a Company
sub-tab (single-bank-vs-peers) — out of scope, left as-is.
