# Code health & performance review — 2026-09-24 (lane 3 of 3)

Owner directive 2026-09-24: "I want more review of the platform." This lane covers
**code health and performance**; the numbers-vs-ground-truth and UX lanes ran
concurrently. Nothing in application code was changed for this review. Every
finding carries file:line, the evidence, a suggested fix, an effort estimate
(S < 1h mechanical · M half-day with render/verify · L multi-day), and the test
that would pin it. Fix nothing until the owner picks an item.

Method: local app on port 8503 from the `infallible-hermann-53aebc` worktree at
`e9278c7`, with the main checkout's warm `cache.db` (1.4 GB) copied in and every
row's timestamp re-stamped to "now" so the local store behaves like production's
warm Postgres (a 49h-old snapshot otherwise forced a 364-bank live rebuild).
Timings come from three sources: the app's own `[timing]` marks
(`utils/timing.py`, ≥50 ms), a browser-side harness that clicks a tab and waits
for Streamlit's script state to settle (wall time the user feels), and `py-spy`
sampling of the server process. Static sweeps used `rg`, `vulture 2.x`,
`pyflakes`, an AST structure scan, an 8-line window copy-paste detector, a
read-only pass over `cache.db`, and the full unittest discovery.

Local limits that keep some prod costs out of these tables: no `FMP_API_KEY` /
`FRED_API_KEY` (price, ETF, rates and analyst panels are blank and fast), the
deep FDIC history store (`fdic_history`) is empty locally, NIC bulk mirrors are
absent (the FFIEC download 403s locally), and GCS is off (per-source JSON caches
read local files instead of a blob per call). Where a prod-only cost is inferred
rather than measured it is labelled **inference**.

---

## 1. Measurements

### 1.1 Top-level sections (one session, cold process → same tab clicked again)

"Cold" = first visit in the process: `st.cache_data` empty, persistent store
warm and fresh. "Warm" = second click on the same tab. Wall = browser-measured
time from click to settled; mark = the app's own `[timing]` phase.

| Section | Cold wall | Cold mark | Warm wall | Warm mark | Notes |
|---|---|---|---|---|---|
| Home | 0.34 s (page load) | `render:Home` 264 ms, `home.above_fold` 258 ms, `home.af.calendar` 174 ms | 0.30 s | 76 ms | keyless locally → markets/rates panes render dashes; the snapshot path (`watchlist_metrics_snap`) hit, so `app.load_all_data_fast` < 50 ms |
| **Market & Macro** | **23.1 s** | `macro.render` **20,778 ms** | 0.61 s | 117 ms | 80 sequential live FRED CSV fetches on the render thread (macro_cache files were >1h old). See P1-1. |
| Screen & Compare (Screen) | 1.97 s | — | 0.26 s | — | builder only; no run |
| **Earnings** | **3.83 s** | `render:Earnings` 1,847 ms | 0.21 s | — | results board build failed locally (`FMP earnings calendar unavailable`); prod builds it inline every 15 min (K3-4) |
| News & Research | 1.76 s | — | 0.18 s | — | |
| Transactions | 1.75 s | — | 0.19 s | — | |
| Geographic | 2.17 s | `render:Geographic` 154 ms | 0.23 s | 59 ms | ~2 s of the cold cost is above the section (app.py top-level + imports) |

### 1.2 Company leaves (COLB unless stated; deep link or tab click; cold → warm)

| Section › leaf | Cold wall | Cold mark (`render:Company/…`) | Warm wall | Warm mark | Cost class (from code/profile) |
|---|---|---|---|---|---|
| **Overview › Corporate Profile** (deep-link page load) | **38.2 s** | **36,024 ms** — `cp.prefetch` 33,601 ms, `cp.snapshot` 549 ms | 1.0 s | 910 ms (`cp.prefetch` 832 ms) | prefetch thread pool; the recurring 0.8 s is an unsatisfiable `load_fdic_hist(min_quarters=44)` live refetch (P1-3). The 33 s cold is not reproduced by calling the three prefetch tasks directly (1.7 s + 0.01 s + 0 s) — see 1.4. |
| Overview › Stock Chart (TCBK, clean pass) | 0.23 s | — | 0.26 s | — | keyless locally |
| Overview › Corporate Structure (TCBK) | 2.92 s | 2,780 ms | **2.57 s** | — | NIC bulk-file download attempted on **every** rerun (403 locally, ~2.5 s recurring; prod reads the GCS mirror — confirm the recurring cost there) |
| Overview › Corporate Governance (TCBK) | 1.35 s | 1,187 ms | 1.12 s | — | |
| **Overview › People Summary (TCBK)** | **79.7 s** (COLB: 67.2 s mark) | — | 0.77 s | — | DEF 14A download + Claude extraction on the render thread; a failed extraction is not cached (P1-11) |
| Overview › Analyst Coverage (TCBK) | 0.20 s | — | — | — | keyless |
| Overview › Compensation (TCBK) | 0.18 s | — | — | — | keyless |
| Overview › Corporate Profile (TCBK, revisit) | — | — | 1.22 s | — | |
| Financials › Templated › Financial Highlights | 2.94 s | 1,521 ms | 0.61 s | 241 ms | |
| Templated › Income Statement | 1.62 s | 147 ms | — | — | |
| Templated › Balance Sheet | 0.87 s | 234 ms | — | — | |
| Templated › Performance Analysis | 0.46 s | 141 ms | — | — | |
| **Templated › Capital Adequacy** | **18.6 s** (FHB: 19.6 s page load) | **16,870 ms** (FHB 17,854 ms) | **2.56 s** (FHB) | 2,371 ms | profile in 1.4: SEC filing downloads + iXBRL parses on the render thread (P1-2) |
| **Templated › Asset Quality Detail** | **8.7 s** (FHB 8.5 s) | **6,746 ms** (FHB 7,985 ms) | 0.94 s (FHB) | 664 ms | same family of live extraction (credit-quality scrape) |
| Templated › Asset Quality by Loan Type | 0.51 s | 274 ms | — | — | |
| Templated › Deposit/Loan Composition | 2.04 s | 159 ms | — | — | |
| Templated › Deposit Trends | 2.16 s | 167 ms | — | — | |
| Templated › Portfolio Analysis | 0.43 s | 98 ms | — | — | |
| Templated › Capital Structure Details | 0.41 s | 96 ms | — | — | |
| **Templated › Interest Rate Risk** | **3.47 s** | 1,481 ms | 0.46 s (FHB) | — | |
| **Financials › Company Reported › Financial Highlights** (basis switch) | **23.6 s** | **21,663 ms** | **3.97 s** | **3,747 ms** | CR pages re-download and re-parse the same filing once per page (P1-2); the warm 3.7 s recurs on every rerun |
| Company Reported › Income Statement | 0.82 s | 551 ms | — | — | |
| Company Reported › Balance Sheet | 0.77 s | 490 ms | — | — | |
| **Company Reported › Performance Analysis** | **7.40 s** | **7,040 ms** | — | — | |
| **Company Reported › Regulatory Capital** | **4.55 s** | **4,243 ms** | — | — | |
| **Company Reported › Credit Quality / Allowance** | **3.75 s** | **3,511 ms** | — | — | |
| **Company Reported › Loan Composition** | **4.87 s** | **3,132 ms** | — | — | |
| Company Reported › Deposit Composition | 0.63 s | 430 ms | — | — | |
| **Company Reported › Securities Portfolio** | **4.75 s** | **4,498 ms** | — | — | |
| **Company Reported › Fair Value** | **7.43 s** | **7,079 ms** | — | — | |
| **Company Reported › Segment Reporting** | **6.05 s** | **5,880 ms** | — | — | |
| **Company Reported › Interest Rate Risk** | **5.94 s** | **5,772 ms** | — | — | |
| Valuation › Valuation Model (FHB) | 2.07 s | 151 ms | 0.33 s | — | |
| Valuation › Peer Rank (FHB) | 1.75 s | < 50 ms | — | — | cohort from the `watchlist_metrics_last` blob |
| Valuation › Price & Trends (FHB) | 2.19 s | 323 ms | — | — | keyless |
| **Estimates / Earnings › Earnings (FHB)** | **6.78 s** | **4,838 ms** | — | — | results board built inline (FMP unavailable locally → failed fast; prod pays the build) |
| **News & Filings › Recent Documents (FHB)** | **6.67 s** | **5,592 ms** | 0.20 s | — | |
| **News & Filings › Filings & Reports (FHB)** | **3.02 s** | 2,868 ms | — | — | live `submissions` JSON per (cik, max_filings) — K1-5 |
| News & Filings › Key Exhibits / Press Releases / Events Calendar (FHB) | 0.14–0.17 s | — | — | — | |
| News & Filings › Transcripts & Presentations (FHB) | 0.98 s | — | — | — | |
| Market Analysis › Market Share & Branches (FHB) | see 1.3 | 1,225 ms | — | — | |
| **Market Analysis › HMDA Mortgages (FHB)** | see 1.3 | **7,792 ms** | — | — | |
| Market Analysis › other leaves, Ownership › all leaves (FHB) | see 1.3 | | | | |

Wall − mark ≈ 1–2 s on every cold Company leaf: that is app.py's own top-level
work (universe tickers memo, nav, `load_single_bank_metrics_cached`, the
`[metrics] slowest: COLB 1.8s` line) plus Streamlit's rerun overhead.

### 1.3 Market Analysis and Ownership leaves (FHB)

| Section › leaf | Cold wall | Cold mark | Warm wall | Notes |
|---|---|---|---|---|
| Market Analysis › Market Share & Branches (section click) | 1.39 s | 1,225 ms | 0.25 s | SOD store reads |
| Market Analysis › Deposit Market Share | 0.95 s | — | — | |
| Market Analysis › Branch List | 0.90 s | — | — | |
| Market Analysis › Branch Map | 0.13 s | — | — | |
| Market Analysis › Branch Competitors | 0.15 s | — | — | |
| Market Analysis › Branch Proximity | 0.95 s | — | — | |
| Market Analysis › Merger Planning (HHI) | 0.14 s | — | — | |
| Market Analysis › Market Demographics | 0.13 s | — | — | census key absent locally |
| **Market Analysis › HMDA Mortgages** | **8.85 s** | **7,792 ms** | — | `hmda:*:v1` keys absent locally → live HMDA panel + originations fetch; prod keys are 7 d-fresh but read at the 24 h ceiling (§P1-5 class) |
| **Ownership › Institutional (13F)** (section click) | **87.1 s** | **85,996 ms** | 0.19 s | live EDGAR full-text search + per-filer info-table fetches on the render thread (P1-10) |
| Ownership › Detailed | not measured | — | — | the tab click did not trigger a rerun in the harness (the 13F render had just written the per-quarter files) |
| Ownership › Holder History | 0.17 s | < 50 ms | — | |
| Ownership › Crossholdings | 0.13 s | < 50 ms | — | |
| **Ownership › Insider Activity** | **30.2 s** | (see note) | — | `form4_cache/{cik}.json` older than its 24 h TTL → EDGAR Form 4 crawl on the render thread (`data/form4_client.py:296-300`); the warming job runs 04:30 weekdays, so every bank's first Ownership view after the 24 h mark pays it (same class as P1-10) |

Cold Corporate Profile on a third bank (TCBK, deep link, fresh session): 10.3 s
page load — the 38 s COLB number in §1.2 was the process's first Company page
(imports, first engine connection, first `load_single_bank_metrics_cached`); see
§1.4 for the sampled breakdown.

### 1.4 Where the slow phases go (py-spy, 45 s at 50 Hz, cold Capital Adequacy for FHB; 651 samples)

| Inclusive share | Frame | What it is |
|---|---|---|
| 80.6 % | `render_company_subtab` → `_capital_adequacy` | the leaf |
| 63.3 % | `ui/capital_dynamics.py:427 _render_holdco_capital` | holdco regulatory-capital table from the 10-K/10-Q iXBRL |
| 41.2 % | `data/http.py:39 get_with_retry` (leaf: `ssl_wrap_socket` 38.7 %) | **live SEC filing downloads** on the render thread |
| 34.4 % | `data/sec_filing_scraper.py:103 parse_inline_xbrl` (leaf: `lxml document_fromstring` 17.7 %) | iXBRL parse of each downloaded filing |
| 31.6 % | `sec_filing_scraper.py:600 _holdco_capital_extract_cached` | per-accession extraction, `cache.get` at the 24 h default |
| 23.0 % | `sec_filing_scraper.py:546 _fye_month_for` → `instance_facts` | **a second download + parse of the same filing** just to read the fiscal-year-end month (`fyemonth:v1` also read at 24 h) |
| 6.5 % | `data/loaders.py:57 load_fdic_hist` → `fetch_group_history` | live FDIC refetch (P1-3) |
| 6.3 % | `data/ir_provider.py:601 fresh_capital` → `latest_earnings_release` | IR/EDGAR release lookup |

`holdco_capital_for` (`sec_filing_scraper.py:628-670`) walks the latest 10-Q,
then the latest 10-K, then up to `_MAX_YEARS = 5` 10-Ks; for each it calls
`instance_facts(meta)` (download + parse, no in-process memo — `_get` at `:56`
goes straight to `get_with_retry`) once for capital and once more inside
`_fye_month_for`. Cold, that is up to 12 downloads and parses of ~7 MB filings
on one render. The other Company Reported pages (`fair_value` `:880/:901`,
`securities` `:1115/:1136`, `credit_quality` `:1454`, `performance` `:2191`,
`highlights` `:2260`, `segments` `:2469`, `rate_risk` `:2596`) each call
`instance_facts(meta)` for the same accession under their own cache key — a
cold walk through the Company Reported basis downloads and parses the same 10-K
about eight times. This is the measured 4–7 s per CR leaf and 21.7 s for the CR
Financial Highlights above.

Direct timings of the Corporate Profile prefetch tasks (`ui/bank_detail.py:815-818`),
run outside Streamlit against the same store:

| Task | Time | Result |
|---|---|---|
| `load_fdic_hist("COLB", min_quarters=44, limit=44)` | 1.74 s, then 0.75 s on a repeat call | 20 quarters both times — the cache holds 20, the caller demands 44, so it refetches live every call and rewrites the same 20 |
| `deep_group_history("COLB", limit=44)` | 1.26 s | 0 rows (store empty locally) |
| `sec_client.fetch_company_facts(887343)` | 0.01 s | store hit |
| `fmp_client.get_history("COLB", "1Y")` | 0.00 s | keyless → empty |

The in-app `cp.prefetch` 33.6 s cold is therefore NOT explained by the tasks'
own cost; the warm 0.8 s is. The cold number needs a py-spy pass on a fresh bank
(not done — the browser was busy with the leaf sweep) before it is treated as
more than "cold Corporate Profile is slow"; it is listed under P1-3 with that caveat.

Second profile — cold Corporate Profile for TCBK (50 s at 50 Hz; 256 non-idle samples):

| Inclusive share | Frame | What it is |
|---|---|---|
| 33.6 % | `data/http.py:39 get_with_retry` | every non-trivial sample is a network wait |
| 22.7 % | `ui/bank_detail.py:815` prefetch lambda → `load_fdic_hist` (`loaders.py:37`) → `deep_group_history` (`fdic_history_store.py:182`) → `get_cert_group` (`cert_group.py:119`) → `_resolve_group` (`:195`, `:177`) → FDIC live | the deep-store read resolves the cert group **live** (FDIC institutions API) when the job-written bulk map `cert_groups:v2` is absent and the per-cert key is missing (`cert_group.py:100-125`; both read with an explicit 30 d age, so this is a fresh-environment / missing-key path, not the 24 h ceiling), then `fetch_group_history` (`:300`) → `fetch_financials` (`fdic_client.py:398`) fetches the 20 quarters live (P1-3) |
| 8.2 % | `_render_snapshot` (`bank_detail.py:403`) → `sec_client.get_filing_info` (`:1540`) | live `data.sec.gov/submissions` JSON |
| 3.9 % | `load_single_bank_metrics_cached` → `_resolve_eps` → `_latest_earnings_8k` → `sec_earnings_8k._submissions_record` (`:81`) → `_get` | **the same submissions JSON downloaded again** by a second code path (P1-8) |
| 5.5 % | `build_bank_metrics` / `compute_all_valuations` | compute; the only non-network cost |

Peer-median loops that read all 364 `fdic_hist:{t}` rows one by one
(`ui/capital_dynamics.py:106-117`, `ui/credit_dynamics.py:30-44`) measured
0.08 s each locally — fine on SQLite, an **inference** of ~364 round trips on
Cloud SQL (the same medians are available in one 13 ms read of
`watchlist_metrics_last`, which already carries `cet1_ratio` and
`reserve_coverage_pct`).

Per-rerun write of the 364-bank metrics blob (`app.py:807`,
`cache.put("watchlist_metrics_last", all_metrics)` on every Home/Screen/Compare
rerun): 1,767,328 bytes, 29–48 ms per `put` on SQLite. **Inference**: a 1.7 MB
upsert per widget interaction on Cloud SQL; it is written even when nothing changed.

### 1.5 Test suite

Full discovery, CI's exact form (`PYTHONIOENCODING=utf-8; python -m unittest discover -s tests -t .`),
on this workstation with the warm store present:

```
Ran 2982 tests in 64.002s
OK (skipped=7)
```

Per-module, each `tests.test_x` in its own subprocess (216 modules, 2,982 tests,
serial wall 211 s of which 82.6 s is inside the tests; the rest is interpreter
start + imports per process):

| Wall | Inside tests | Tests | Module | Why |
|---|---|---|---|---|
| 30.4 s | 1.0 s | 5 | `tests.test_sec8k_budget` | a fake adapter does a real `time.sleep(30)` on a worker thread (`:42`) to "hang past the budget"; the test returns in 1 s but the process cannot exit until the thread wakes — 29 s of dead wall per run |
| 30.1 s | 29.7 s | 4 | `tests.test_poll_events_budget` | real sleeps against `_TASK_BUDGET_S`-derived caps (`:135-148`) |
| 2.9 s | 2.6 s | 14 | `tests.test_deep_history` | in-memory store build |
| 2.6 s | 2.3 s | 24 | `tests.test_price_freshness_badge` | |
| 2.5 s | 1.5 s | 114 | `tests.test_audit_regressions` | |
| 2.4 s | 2.1 s | 37 | `tests.test_render_smoke` | render fixtures |
| 2.0 s | 0.9 s | 9 | `tests.test_export_sites_risk_branch` | |
| 1.9 s | 0.8 s | 11 | `tests.test_merger_proximity_ui` | |
| 1.8 s | 0.7 s | 12 | `tests.test_export_sites_misc` | |
| 1.8 s | 1.0 s | 26 | `tests.test_fmp_news` | |

The two budget suites are 60 s of the 211 s serial wall; every other module is
under 3 s. Fix (S): patch `time.sleep` in the fake adapter / make the worker
thread a daemon; drive the poll-events budget with a fake clock.

Order-dependent tests (green under discovery, red alone): `tests.test_audit_regressions.TestBalanceSheetComputedLines`
`test_computed_lines_match_hand_values` and `test_negative_residual_renders_na`
error standalone with `AttributeError: module 'streamlit' has no attribute 'container'`
(`ui/financials_statements.py:2264 _cr_export` under `render_statement:1481`) —
the module's own stub lacks `st.container`; under discovery an earlier module's
additive stub supplies it. The memory note "Streamlit stub replace hazard"
describes this class. Fix (S): add `container` to the stub the module installs;
pin by running the class alone in CI (`python -m unittest tests.test_audit_regressions.TestBalanceSheetComputedLines`).

Skips (7): five AppTest suites skip under discovery by design
(`test_nav_renders` ×2, `test_transactions_ui`, `test_screen_run_gating` ×2 — CI
runs them as scripts); `test_rcn_detail.TestRcnLiveBannerVerification` needs the
gitignored FFIEC creds tool; `test_macro_calendar.TestLiveSmoke` needs `FRED_API_KEY`.

Network under discovery (a socket/`requests` guard recorded every outbound
call with the owning test; the full run passed in 88 s). **13 tests in 9
modules make 20 real HTTP requests** — all pass on CI only because the fetch
fails fast there or the data happens to match:

| Test | Real request(s) | Why it matters |
|---|---|---|
| `tests/test_deep_history.py:149 TestLoaderDeepPath.test_deep_request_never_live_fetches_deep` | `api.fdic.gov/banks/institutions` ×2 | a test named "never live fetches" live-fetches (the cert-group resolve in `deep_group_history`, §1.4) — the stub sits at the wrong seam |
| `tests/test_company_facts_cache_key.py:56 test_fetch_serves_blob_under_helper_key` | `data.sec.gov/submissions/CIK0001423869.json` | PR #131's "isolated, no network" pin reaches EDGAR through `_overlay` |
| `tests/test_sec_client.py TestFetchCompanyFactsOk.test_cache_hit_is_ok_true` | `data.sec.gov/submissions/CIK0000320193.json` | "cache hit" test downloads submissions |
| `tests/test_sec_facts_lag.py test_rides_the_computed_row` | `data.sec.gov/submissions/CIK0000707179.json` | |
| `tests/test_poll_events_budget.py` (4 tests, `:112 _run_main`) | `sec.gov/cgi-bin/browse-edgar …type=4…atom` ×8 on worker threads, `company_tickers.json` ×2 | the Form 4 firehose adapter runs for real inside the budget tests — the 29.7 s of "inside tests" time is partly live EDGAR |
| `tests/test_audit_regressions.py:52`, `tests/test_cr_sweep_fixes.py:349`, `tests/test_fmp_news.py:52` | `sec.gov/files/company_tickers.json` | universe/ticker resolution not stubbed |
| `tests/test_ir_q4.py:126 test_discover_probes_subdomains` | `https://nonq4bank.com/` | probes a made-up domain for real (DNS) |
| `tests/test_q4_calls.py test_picks_soonest_upcoming_and_skips_empty` | `https://investor.none.com/…/rss` | same |

Fix (S each): stub the exact seam (`data.http.get_with_retry` / `requests.get`)
in these modules and add a discovery-wide guard that fails any test opening a
socket unless it is marked live (the memory note "local env live keys" records
three prior incidents of this class). Test: the guard itself.

No-store diff (discovery re-run from a `git archive` copy with no `cache.db`
and no per-source cache dirs): NOT COMPLETED — the run stalled on test 19,
`tests/test_adapters_ignore_since.py TestGoogleNewsIgnoresSince.test_per_ticker_item_older_than_since_kept`,
which through `data/events/wire_base.py:505 → :184` triggers the **~6.5-minute
live universe build** when no snapshot exists (stdout shows the
`[universe] dropping … foreign-domiciled filer` walk). Two sibling modules
(`tests/test_events_dedup_and_tagging.py:72`, `tests/test_feed_mistag_and_dupes.py:66`)
guard that seam with `skipTest`; this one does not, so with the warm store
present it silently runs against the real 435-bank index. Fix (S): stub the
universe seam in that module the way its siblings do.

Structural / source-string tests (49 sites classified by the sweep): 13 GENUINE
(repo-wide single-owner scans, Dockerfile/deploy.yml contracts, AST-lift-and-execute),
31 WEAK (a literal that a same-bug refactor evades — e.g. the two named above),
5 THEATRE: `tests/test_tbv_small_bank_coverage.py:145` (exact version-string pins),
`tests/test_frontier_from_events.py:100`, `tests/test_ui_p3_guards.py:117`
(asserts a docstring), `tests/test_audit_regressions.py:397` and
`tests/test_audit_20260727_p3.py:93,:121` (assert on an expression the test
itself computes). Two assert-free tests (both must-not-raise smokes); zero
tautological assertions by AST scan.

---

## 2. Findings

### P0 — a defect that can show a wrong number

All seven were re-read in source by the reviewer after the sweep; the code
quoted is verbatim at `e9278c7`.

**P0-1 Valuation Model seeds a hard-coded 12 % ROATCE and labels it "trailing-4Q ROATCE from FDIC".**
`ui/valuation_model.py:144-145` — `if roatce_raw is None: roatce_raw = 12.0`; `:509` — `roatce_pct = defaults.get("roatce_pct") or 12.0`.
Trigger: SEC holdco ROATCE unresolvable AND FDIC `NETINC`/`EQTOT` absent (or TCE ≤ 0, or no FDIC history).
Display: the ROATCE seed card at `:238-239` shows `12.00%` under the sub-label
"trailing-4Q ROATCE from FDIC, one-time spikes winsorized" with a Call Report
link; `warranted_ptbv(12.0, …)` = 1.27× drives the Warranted P/TBV and Warranted
price headline and the ±4 pp sensitivity grid. `base_eps`/`tbvps` already got the
None-not-placeholder treatment (comment at `:192-196`, audit 2026-07-02 #30);
ROATCE did not.
Fix (S): `_derive_defaults` returns `roatce_pct: None`; add ROATCE to the
"cannot compute" gate at `:490`; delete the `or 12.0`.
Test: `_derive_defaults("X", [rec without NETINC/EQTOT], {})["roatce_pct"] is None`;
AppTest asserts the "could not be derived" state and no Warranted price.

**P0-2 TTM dividends per share sums a 370-day window with no quarter-count guard.**
`data/sec_client.py:471-481` (`_extract_ttm_dividend`, step 2): every 3-month
DPS fact with `window_start < end ≤ E` where `window_start = E − 370 days` is
summed. E − 365 days is inside the window, so an issuer that tags a discrete Q4
DPS in its 10-K gets **five** quarters summed (+25 % on a flat dividend); an
issuer that does not tag Q4 gets the year-ago quarter substituted for the
missing one; a YTD-only tagger gets **one** quarter. Step 3 (`:484-488`) then
returns the latest cumulative YTD figure (e.g. nine months) under the same TTM
label.
Display: `dividends_per_share` → `compute_dividend_yield` → "Div Yield" on the
screen (`config.py:189`), Bank Detail, the Home sector-valuation medians, and the
Valuation Model payout seed. Contrast `_extract_ttm_value` (`:335-419`), which
requires four consecutive ~90-day quarter-ends — the guard this function lacks.
Fix (M): mirror `_extract_ttm_value` (derive Q4 from FY − 9M, require exactly four
quarter-ends ending at E with 80–100-day gaps; else None); drop step 3 or label
it "YTD".
Test: five quarterly 0.25 facts ending 2025-09-30 … 2026-09-30 → assert 1.00
(currently 1.25); Q1-only 3-month + 9M YTD → assert None or the YTD-derived value
(currently 0.25). Size it first with one `tools/verify_metrics.py` oracle pass.

**P0-3 `compute_roatce_4q` annualises from 1–3 quarters and is displayed as "ROATCE 4Q".**
`analysis/valuation.py:245-266` — `for i in range(min(4, len(fdic_hist)))`, skips
a quarter when `ni_q is None or eq is None`, then `if count < 4: ttm_ni = ttm_ni * (4 / count)`.
No consecutive-REPDTE check; `fdic_hist[:4]` is positional.
Display: "ROATCE 4Q (Sub)" (`config.py:269`); feeds `compute_roatce_blended` →
Fair P/TBV and the undervalued flag (`:559-560`) whenever holdco ROATCE is None,
and the Valuation Model seed (`ui/valuation_model.py:141-143`). Same class as
audit A21 ("3 quarters presented as twelve months"), which
`analysis/capital_return._full_window_sum` already fixes for dividends/buybacks.
Fix (S): require `count == 4` and consecutive quarters; delete the `4/count` scaling.
Test: four consecutive quarters with Q2 `NETINC` None → `compute_roatce_4q(hist) is None`
(existing tests at `tests/test_valuation_quarterly_derive.py:48`,
`tests/test_metric_formulas.py:122` pin only clean series).

**P0-4 `compute_4q_avg` averages whatever exists and labels it "4Q".**
`analysis/valuation.py:216-226` — `values = [q.get(field) for q in fdic_hist[:4] if q.get(field) is not None]; return sum(values)/len(values)`.
Display: "ROAA 4Q" (`config.py:257`), "NIM 4Q" (`:287`), tooltips "Trailing-4-quarter …"
(`ui/bank_detail.py:1093,1098`), peer-comparison metric set. Multi-charter
consolidated records carry None for every average-based ratio
(`analysis/capital_dynamics.py:147-150`), so this fires for the 11 multi-charter holdcos.
Fix (S): four non-None consecutive values or None (owner call: or return `(value, n)` and label "NIM 2Q").
Test: `compute_4q_avg([{"NIMY":3.0},{"NIMY":None},{"NIMY":3.2},{"NIMY":3.1}], "NIMY") is None`.

**P0-5 Capital Dynamics coerces missing `NETINC` / `LNLSNET` to 0 three lines after refusing to do so for `EQTOT`.**
`analysis/capital_dynamics.py:50-51` — `net_income = r.get("NETINC") or 0`,
`total_loans = r.get("LNLSNET") or 0`, immediately after the `EQTOT` guard at
`:41-46` whose comment explains why coercing a missing input to 0 is wrong.
Display: `_compute_quarterly_ni` yields 0 (Q1) or `0 − prior YTD` (a large
negative "quarterly NI") → `capital_returned_k`, `retention_ratio`, the Capital
Generation waterfall ("Net income $0"), the `high_payout` alert, `payout_ratio_4q`,
the Buyback-capacity explainer, and `loan_growth_qoq_pct` = −100 % / +inf on the
adjacent quarter.
Fix (S): None → NaN for both; the quarterly derivation already propagates None.
Test: `NETINC` None in Q2 → `net_income_k_qtr` NaN for Q2 and Q3, no `high_payout` alert, waterfall skipped.

**P0-6 TCE/TA trend coerces missing equity/assets to 0 and renders a signed percentage.**
`ui/financial_highlights.py:785-787` — `eq = _num(recs[k].get("EQTOT")) or 0`,
`asset = _num(recs[k].get("ASSET")) or 0`; `raw = tce/ta*100 if ta else None`.
With `EQTOT` absent and `INTAN` present the row prints `−INTAN/(ASSET−INTAN)`
(e.g. `-0.85%`) and the popup says "Equity 0 ($000)". `ui/bank_detail.py:291-295`
computes the same ratio and returns "—" when either is None.
Fix (S): mirror the bank_detail guard. Test: `_tce_ta_builder([{"INTAN":100,"ASSET":None,"EQTOT":None}], …)(0)` renders "—".

**P0-7 Rate-sensitivity growth rates return 0.0 for absent balances and the UI prints "+0.0% YoY".**
`analysis/rate_sensitivity.py:93-99` `_safe(val, default=0.0)`; `:284-289`
`_growth` returns `0.0` when `v0 <= 0` and `-1.0` when the latest field is
absent; `:281-282` `year_ago = fdic_hist[4]` is positional (wrong span across a gap).
Display: `ui/rate_sensitivity.py:667-671` caption "Historical YoY: loans +0.0% ·
deposits +0.0% · …" and the same 0.0 becomes the volume-effects growth
assumption (`:310-319`), holding every projection year's earning assets flat.
Fix (S): `_growth` → None when either endpoint is None; UI prints "—" and tags
the fallback "(default)"; pick `year_ago` by REPDTE.
Test: `hist[4]["DEP"] is None` → `compute_historical_growth_rates(hist)["deposits_growth"] is None`.

Suspicious (not P0 because the triggering input is normally present, or the
effect stays in a popup/chart) — 25 items in the sweep; the five most actionable:

| # | file:line | Pattern | Why it matters |
|---|---|---|---|
| S1 | `analysis/capital_return.py:465-473` | `compute_yoy_growth._ttm` checks `len == 4` + all-notna but not the quarterly-cadence gap that `_full_window_sum` (`:398-407`) enforces | four rows spanning a gap → YoY dividend/buyback/DPS growth from a mixed window; the 2026-08-19 fix's sibling. Fix S: route through `_full_window_sum`. |
| S2 | 11 sites (`analysis/valuation.py:148,:248`; `ui/financials_statements.py:663,:675,:718`; `ui/financial_highlights.py:526`; `ui/bank_detail.py:229`; `ui/earnings.py:780`; `data/deal_comps.py:82`; `analysis/capital_dynamics.py:48-49`; `ui/valuation_model.py:173`) | house-wide `INTAN or 0` | an FDIC record with `INTAN` *absent* (not 0) → TCE = equity → ROATCE understated, TBVPS/P-TBV overstated, popup "Intangibles 0". FDIC returns literal 0 for banks without intangibles, so absence should be rare, but nothing asserts it. Fix S: one `_intan(rec)` helper returning None when absent. |
| S7 | `ui/valuation_model.py:166` | `equity = latest.get("EQTOT") or 0` in the FDIC TBVPS fallback | `EQTOT` None with `shares > 0` → negative TBVPS that passes the `tbvps is None` gate at `:490` and drives a negative Warranted price. |
| S11 | `ui/financials_statements.py:3727-3728` | Company Reported `_tce`: `goodwill or 0.0`, `other intangibles or 0.0` | a parser miss on the goodwill line (memory: a parser miss is a bug) → CR TCE = equity; the sibling `securities` row at `:3629` has a both-absent → None guard, TCE has none. |
| S13 | `data/sec_client.py:411-419` | `_extract_ttm_value` Path 2 returns the latest ~365-day fact up to 2 years old | a real 12-month number, but up to two years stale under a "Trailing 12 months (4 consecutive quarters …)" provenance note (`:1306`). |

Crash-class side findings (present key, None value into `:.2f`):
`ui/rate_sensitivity.py:114-115` (`current_nim_pct` None), `ui/capital_dynamics.py:1023`
(`dividend_yield_pct` None while `total_shareholder_yield_pct` is set). These raise
rather than mis-state, so they are P2.

Clean by inspection: `analysis/metrics.py` (pure None passthrough, no `or 0`);
`analysis/capital_return.py`'s full-window-or-None fix intact; Home sector
medians drop None and enforce `n ≥ 5`; the screen engine returns a None verdict
on a None value; `data/sec_statements.py` never sums subtotals (the registrant's
own R-file rows); FDIC `ppnr`/`_revenue` require every component.

### P1 — measured performance problem > 3 s, or a cache that never hits

**P1-1 Market & Macro cold: 20.8 s of sequential live FRED fetches on the render thread.**
Measured `macro.render` 20,778 ms cold / 117 ms warm. `data/fred_client.py:104-135`
`fetch_series` is `@st.cache_data(ttl=3600)` over a per-series JSON file with a
1 h `cached_at`; on a miss it calls `requests.get` (`:64`, bypassing
`data/http`) synchronously, and `ui/macro.py` walks ~80 series. Prod: the
`refresh-macro` job runs every 30 min, so the file cache is fresh, but every
*new Cloud Run instance* still pays one `load_json` per series (GCS `blob.exists()`
+ `download_as_text()`, `data/cloud_storage.py:139-147`, two round trips × ~80
series — **inference** ~8–16 s cold-instance Macro even when nothing is stale;
confirm with `[timing] macro.render` in Cloud Logging).
Fix (M): one snapshot blob for the whole series set (the job already fetches
them all) served via `served_snapshot`; render never calls `_fetch_csv`.
Test: patch `requests.get` to raise; seed 80 series files; assert
`render_macro_dashboard` completes and `macro.render` makes zero HTTP calls.

**P1-2 Company Reported / Capital Adequacy / Asset Quality: filings are downloaded and parsed on the render thread, several times per filing, once per day.**
Measured: Capital Adequacy 16.9–17.9 s cold, 2.4 s warm; CR Financial
Highlights 21.7 s cold, 3.7 s warm; six CR leaves 3.1–7.1 s cold each. Profile
in §1.4. Three compounding causes:
(a) every accession-keyed extraction cache is read at the 24 h default —
`sec_filing_scraper.py:543 fyemonth`, `:597 holdco_cap`, `:877/:898 fair_value`,
`:1112/:1133 securities`, `:1451 credit_quality`, `:2017 asset_quality_nim`,
`:2188 performance`, `:2257 highlights`, `:2466 segments`, `:2593 rate_risk`,
plus `data/sec_statements.py:847/:1540/:1642 asreported*`,
`ui/financials_statements.py:2576 compositions`, `data/sec_composition.py:635`,
`data/xbrl_dimensional.py:267/:572` — for immutable per-accession payloads, so
the first view each day re-downloads (`tests/test_cache_read_ceilings.py`
fixed exactly this class for `earnings_8k`/`reported_tbvps`/`otc_release`/M&A
and was never extended);
(b) `instance_facts(meta)` (`:498`) has no in-process memo, so each page's
extractor and `_fye_month_for` (`:546`) re-download and re-parse the same
filing (2× per filing in the holdco walk, ~8× across the CR basis);
(c) the extraction runs inline on the render thread instead of reading a
job-built snapshot (`refresh-capital` 07:00 daily and `refresh-cr-quarterly`
exist, but the 24 h ceiling makes their output expire before the next run).
The warm cost is not parsing: a py-spy sample of a **warm** CR Financial
Highlights rerun (FHB, 3.4 s wall, 162 samples) is 70 % `get_with_retry` —
`data/sec_statements.py:892 _recent_filing_metas` (→ `:908 _recent_10k_metas`
→ `as_reported_statement_multiyear`, 32 %), `data/sec_filing_scraper.py:1920
_list_10k_filings` (17 %), `:65 latest_filing` (12 %), `:576 _fdic_cet1`
(10 %), `:2003 company_asset_quality_nim` (9 %): the filing-index and
submissions lookups behind every CR page are plain `_get` calls with **no cache
at any layer**, so every rerun of a CR leaf (any widget interaction) re-issues
them to EDGAR. Prod pays this on every CR rerun regardless of cache state.
Fix (M): `max_age_s=None` at every accession-keyed read (spec versions already
live in the keys); a per-process `functools.lru_cache`/`st.cache_data(max_entries=8)`
on `instance_facts` keyed by accession; memoise `latest_filing`/`_list_10k_filings`/
`_recent_filing_metas` on `cik` for the session (15 min, the way `get_filing_info` is);
CR pages read the snapshot only.
Tests: extend `TestCallSitesReadWithoutCeiling.SITES` with each family; behavioural
pin per module (put payload aged 72 h, patch `get_with_retry` to raise, assert served);
`instance_facts` called twice for one accession → one `_get`.

**P1-3 Corporate Profile prefetch demands 44 quarters from a 20-quarter cache: unsatisfiable, refetches FDIC live on every render.**
`ui/bank_detail.py:815` — `load_fdic_hist(ticker, min_quarters=44, limit=44)`.
`data/loaders.py:34-60`: with an unpopulated deep store (`deep_group_history` → [])
the warm `fdic_hist:{ticker}` row holds 20 quarters, `20 >= 44` is False, the
live fallback is capped at 20 (`:57`) and rewrites the same 20 — so the next
call misses again. Measured 1.74 s then 0.75 s per call; in-app `cp.prefetch`
832 ms warm on every Corporate Profile rerun. `data/loaders.py:69-75` documents
this exact trap for `load_fdic_hist_df` ("the unsatisfiable refetch the old
threshold caused") and caps `min_quarters` at 20 — the prefetch call bypasses
that wrapper. In prod it bites every bank whose deep history is shorter than 44
quarters (listed < 11 years ago) or whenever the backfill is behind.
The 33.6 s `cp.prefetch` on COLB was the process's first Company page and did
not reproduce: a third bank (TCBK) measured `cp.prefetch` 2,582 ms cold
(`render:Company/Corporate Profile` 3,940 ms, page load 10.3 s), consistent with
the direct task timings. The recurring cost is the 0.8–2.6 s live refetch.
Fix (S): `min_quarters=min(44, 20)` (or call `load_fdic_hist_df`), and a
`min_quarters <= 20` assertion inside `load_fdic_hist`'s live path.
Test: seed `fdic_hist:T` with 20 rows and an empty deep store; patch
`fetch_group_history` to count; call `_prefetch_profile_data` twice; assert 0 fetches.

**P1-4 Two cache keys that can never hit: `cached_at` stamped with the data's quarter date, not the write time.**
`data/as_of_metrics.py:78` — `cache.put(key, {"metrics": out, "cached_at": q.isoformat()})`
read at `:54-56` via `is_fresh(cached, _CACHE_TTL_S)`; `data/entity_graph.py:170`
— `cache.put(key, {"map": out, "cached_at": q.isoformat()})` read at `:140-141`
with `_LINEAGE_TTL_S`. For any as-of quarter older than the TTL the entry is
always stale, so every Run of the As-of screen (`app.py:1033-1035`, behind a
spinner that says "then cached") re-fetches 20 FDIC quarter slices for the
cohort plus a paginated FDIC history crawl. `cache.db` has no `as_of_metrics:` rows.
Fix (S): `cached_at: datetime.now().isoformat()` at both sites (the quarter is already in the key).
Test: in-memory SQLite; patch `fetch_quarter_financials` to count; call twice for the same past quarter; assert one fetch round.

**P1-5 24 h default read ceiling on job-produced snapshots flips on nightly jitter (PR #150's class, five more instances).**
`data/estimates.py:234,:255` read `earnings_calendar_snap` at the default 24 h
although the docstring (`:225-229`) promises "whatever its age" — Home's Alert
Inbox (`ui/home.py:766`) and the Earnings agenda go "unavailable" in the window
between 24 h-after-last-write and tonight's write (every night the run finishes
later than the previous one; all day when it fails).
`jobs/refresh_home_snapshot.py:247` reads `sector_val_hist` at 24 h before
read-modify-write → a >24 h scheduler outage rewrites the 365-day YoY history as
one record. `data/events/ir_site.py:469` reads `ir_q4_endpoints` at 24 h → poll-events
silently drops every discovered IR endpoint past 24 h. `data/earnings_call.py:484`
(`pr_call_snap`), `ir_site.py:769/:792` (`q4_calls_snap` and its no-clobber
guard) are the odd siblings of the already-fixed `call_info_snap`/`announcement_call_snap`.
`data/as_of_metrics.py:139` and `data/sec_per_share.py:283` read the ALLBANKS
grids at 24 h but judge them fresh to 36 h → Trends shows "not warmed" in the 24–36 h window.
`data/cache.py:189 get_multi(keys)` has no `max_age_s` parameter at all, so
`fdic:`/`sec:`/`fdic_hist:` (nightly) cannot opt out; `docs/FRESHNESS-POLICY.md:24-25` encodes the jitter.
Fix (S each): `max_age_s=None` at the snapshot reads (the values carry their own
`cached_at`); add `max_age_s` to `get_multi`. Note `tests/test_earnings_calendar_nonblocking.py:47`
stubs `cache.get = lambda k: …` with one positional arg and must accept the kwarg.
Test: put each snapshot, age the row to 30 h via `UPDATE cache SET timestamp`, assert it is served.

**P1-6 `load_all_data_fast` still gates on `n_tickers == len(tickers)`.**
`app.py:756-757`. Any nightly universe count change makes every Home / Screen /
Compare render rebuild the full-universe metrics inline (~60 s per the comment
at `:740-742`, single `st.cache_data` key → every session queues behind it)
until `refresh-home-snapshot` next writes. `fetch_earnings_calendar` dropped
exactly this guard on 2026-06-13 (`data/estimates.py:218-224`). Locally it is
what forced the 364-bank live rebuild before the store was re-stamped.
Fix (S): drop the count guard, serve stale with the as-of badge.
Test: `tests/test_screen_run_gating.py:99` seeds `n_tickers: 2` — add a case
with `n_tickers ≠ len(tickers)` and assert `load_all_data` is not called.

**P1-7 Instance-lifetime memos freeze the universe.**
`data/bank_universe.py:752 _UNIVERSE_CACHE`, `:779 _NONCOMMON_CACHE`,
`:794 _NONCOMMON_PRIMARY_CACHE`, `data/bank_mapping.py:496 _SNAPSHOT_MAP`,
`data/release_metrics.py:788 _CIK_TICKER`, `data/events/wire_base.py:261-264`
are built once and never reset (no assignment back to None anywhere). A Cloud
Run instance that outlives one nightly never sees banks added or removed; the
1 h `build_universe` memo, the stacked `@st.cache_data(ttl=86400)` over
`(ttl=3600)` on `get_universe_tickers` (`bank_universe.py:848-849` — the outer
memo makes the inner 1 h dead) and `app.py:119`'s 30-min memo are all moot.
Fix (S): key `_UNIVERSE_CACHE` on the snapshot's `cached_at` (one cheap row read);
delete line 848.
Test: patch `_load_lastgood` to return snapshot A then B; assert `get_universe()` reflects B.

**P1-8 `get_filing_info` keyed on five different `max_filings` values → up to five live `submissions` downloads of the same JSON per CIK per 15 min.**
`data/sec_client.py:1523` (`@st.cache_data(ttl=900)`) called with 1
(`bank_mapping.py:748`, `bank_universe.py:1410`), 50 (`ui/bank_detail.py:349/403/1028`,
`ui/corporate_governance.py:100`), 80 (`ui/filings.py:241`, `ui/key_exhibits.py:70`),
200 (`data/people.py:122`), 1000 (`ui/recent_documents.py:578`). Measured
Filings & Reports 2.9 s cold on a bank whose profile was just rendered.
Fix (S): memoise the raw fetch on `cik`, slice outside. Test: patch `get_with_retry` counter; call with 50 then 80; assert one download.

**P1-9 Earnings results board built inline every 15 min; no job warms it.**
`data/earnings_results.py:633-688` (`earnings_results_board_v12:*`, a 900 s
`served_snapshot`): FMP calendar + 800-row events scan + `_fill_release_metrics`
(EDGAR per reporting bank) + a full FDIC institutions walk when
`fdic_assets_by_cert_v1` is 24 h+ old — on the Earnings tab and inside
`release_call_info_map._build`. Measured `render:Earnings` 1.85 s and
`render:Company/Earnings` 4.8 s locally with FMP *unavailable* (the build
failed fast); prod pays the whole build on the first render each 15 min in
earnings season. Same pattern the memory note "jobs build, renders read" has
fixed four times.
Fix (M): warm `earnings_results_board_v12` from `poll-events` or `refresh-home-snapshot`; render serves at any age.
Test: patch the FMP call to raise; seed a 2 h-old board; assert the Earnings tab renders it.

**P1-10 Ownership › Institutional (13F): 86 s cold — a live EDGAR full-text search plus per-filer fetches on the render thread, once per bank per day.**
Measured `render:Company/Institutional (13F)` **85,996 ms** for FHB (wall 87.1 s);
`form13f_cache/FHB.json` was written at the end of that render, i.e. the page
built it. `data/form13f_client.py:551-593 fetch_institutional_holdings` serves
the file only when `_is_fresh` (`:37-39`, `CACHE_TTL_SECONDS = 86400`, `:29`)
and otherwise runs `_search_13f_for_ticker` (`:42-67`, EDGAR FTS via raw
`requests.get`) and one info-table fetch per filer (`:126`, `:143`, `:225`).
The warming job is **quarterly** (`tools/create_13f_job.py:33`, cron
`0 6 19 2,5,8,11 *`), so for ~89 of every 90 days the first Ownership view of a
bank each day pays the full crawl. 13F-HRs land quarterly; a 24 h file TTL is
the wrong cadence by two orders of magnitude.
Fix (S): serve the file at any age when the job owns freshness (≥ 7 d, or
`cache_only` on render with the job as the only writer).
Test: seed a 25 h-old `form13f_cache/T.json`; patch `requests.get` to raise;
assert `fetch_institutional_holdings("T")` returns the cached holders.

**P1-11 Overview › People Summary downloads the full DEF 14A and calls the Claude API on the render thread; an API failure is retried on every view.**
`data/people.py:157-200 get_proxy_people`: on an accession-cache miss it fetches
the proxy text (`fetch_filing_text(…, max_chars=400_000)`, `:179`), slices it,
and calls `_extract_via_claude` (`:129-155`, `anthropic.Anthropic(...)` inline)
before rendering. `raw is None` (no key, throttle, timeout) returns None
**without caching** (`:186-187`, "retry next view"), so a bank whose extraction
fails pays the proxy download again on every People Summary view. Measured
`render:Company/People Summary` 67,168 ms on COLB (no `ANTHROPIC_API_KEY`
locally → download + slice, then the None path); the TCBK repeat in the clean
sweep is in §1.3. Prod pays the download and a multi-thousand-token LLM call on
first view per proxy (annual), on the request path.
Fix (M): move extraction to a job (`refresh-universe` already walks every CIK's
filings) and serve the `people_cache` file only; cache a negative marker with a
short TTL so a failed extraction is not re-attempted per view.
Test: patch `fetch_filing_text` to count and `_extract_via_claude` to return
None; call `get_proxy_people` twice; assert one text fetch.

**P1-12 (inference, needs a prod number) per-rerun 1.7 MB blob write and 364-row peer loops.**
`app.py:807` writes `watchlist_metrics_last` (1.77 MB) on every Home/Screen/Compare
rerun (29–48 ms on SQLite; a Cloud SQL upsert per widget interaction in prod);
`ui/capital_dynamics.py:106-117` / `ui/credit_dynamics.py:30-44` read 364
`fdic_hist:{t}` rows one at a time on every Capital Adequacy / Asset Quality
rerun (0.08 s on SQLite; ~364 round trips in prod) although the same medians
are one 13 ms read of `watchlist_metrics_last`. Fix (S): write only when the
snapshot changed; compute the medians from the blob. Test: call twice with the
same metrics, assert one `put`.

### P2 — duplication, dead code, test hygiene, smaller cache hygiene

**Hard bug: `tools/verify_pending_deals.py:63` does not compile** (`SyntaxError:
unterminated string literal` — nested same-quote f-string across a line break;
verified with `py_compile`). Neither linter could analyse the file. S.

**Dead code** (vulture ≥ 60 %, every function/method hit caller-verified across
the whole repo including tests, tools, docs, registries and `patch.object`
strings; raw lists in the review's scratch output are reproduced below in full):

- 47 CONFIRMED-DEAD functions, ~1,500 lines, including two orphaned modules
  (`ui/fdic_click_table.py` 121 lines, `data/nport_client.py` 362 lines) and
  **`ui/data_quality.py` (457 lines) — not in `COMPANY_NAV` nor `app.py`**
  (zero hits for "data_quality|Data Quality" in either), reachable only from
  `tests/smoke_views.py:184` and `tests/test_export_sites_highlights.py:475`;
  `tests/test_cert_group.py:363` carves a seam exemption for a page nobody can open.
  Owner call: wire it into the nav or delete it with its two tests (M).
  The rest (all S): `data/cache.py:269 backend_info`; `data/call_report_store.py:676 get_all_latest_ladders`;
  `data/consensus.py:202 parse_consensus_pdf`, `:1153 load_consensus`;
  `data/fdic_client.py:420 get_latest_financials`, `:482 get_historical_financials`
  (per-cert readers the cert-group test *forbids* ui from calling);
  `data/ibkr_client.py:83/:117/:125/:178`; `data/nim_assumptions_store.py:188`;
  `data/branches_store.py:683`; `data/price_cache_store.py:444`;
  `data/provenance.py:46 Source.describe`, `:118 provenance_of`;
  `data/sec_earnings_8k.py:646 reported_tbvps` (wrapper);
  the seven single-filing scrapers `data/sec_filing_scraper.py:1100 securities_for`,
  `:1439 credit_quality_for`, `:2177 performance_for`, `:2247 financial_highlights_for`,
  `:2481 segments_for`, `:2581 rate_risk_for` (+ `:860 fair_value_for` test-only) — the UI
  uses the `*_multiyear_for`/`*_multiquarter_for` variants and
  `docs/COMPANY-REPORTED-PLAN.md:87-98` still documents the dead ones;
  `data/xbrl_dimensional.py:505`; `data/events/ir_site.py:287 _q4_apikey`
  (a "back-compat shim" whose only reference is an inert `patch.object` in
  `tests/test_ir_q4.py:123`); `ui/bank_scope.py:51`; `ui/company_nav.py:405 _cr_todo`;
  six of nine functions in `ui/components.py` (`:50 delta_chip`, `:125`, `:144`, `:161`, `:181 alert_row`, `:191`);
  `ui/credit_dynamics.py:47 _render_credit_headline` (72 lines); `ui/deposit_dynamics.py:43 _render_deposit_headline` (93);
  `ui/deposit_lookup.py:48 render_deposit_lookup`; `ui/filings.py:191 render_filings`;
  `ui/financials_statements.py:2130 _render_as_reported_statement`, `:3293 render_fair_value`;
  `ui/macro.py:19 _trend_arrow`, `:818 _render_surprise_summary`;
  `utils/formatting.py:266 get_color`, `:349 style_dataframe`, `:366 format_dataframe_display`;
  `ui/bank_detail.py:1041 render_bank_detail`.
- 28 TEST-ONLY functions (~700 lines; deleting requires deleting the test):
  notably `analysis/ownership_analytics.py` (whole module, SNL §13 not wired — keep),
  `data/bank_universe.py:897/:904/:926/:971` (`get_universe_count*`, `search_universe`,
  `get_universe_bank`), `data/fmp_client.py:1002 get_insider_trading` (replaced by the
  Form 4 lane), `data/call_report_store.py:629 get_stored_rcn_detail` (RC-N detail is
  written nightly by `jobs/refresh_ffiec.py:89` and never read), and two stale
  docstrings claiming a job caller: `data/estimates.py:229`
  (`refresh_earnings_calendar_snapshot` — no `jobs/` caller) and
  `jobs/refresh_avg_volume.py:11` (names `upsert_avg_volumes`; the job calls `upsert_derived_metrics`).
- 100 %-confidence dead parameters/branches: `data/sec_client.py:288 prefer_quarterly`,
  `ui/generic_table.py:105 table_key`, `ui/rate_sensitivity.py:1232` (`if False` literal).
- pyflakes: 84 unused imports (data 25, ui 28, tests 15, tools 12), 16 unused
  locals, 6 placeholder-less f-strings (`app.py:1120`, `ui/deposit_dynamics.py:95`, …),
  one redefinition (`ui/earnings.py:755` shadows `:19`), **0 undefined names**.
  Unused imports that mask dead functions: `ui/credit_dynamics.py:13`,
  `ui/data_quality.py:21`, `ui/deposit_lookup.py:13`, `app.py:71 COMPANY_LEAVES`
  (also unused at its definition `ui/company_nav.py:66`).

**Parallel implementations** (CLAUDE.md's one-shared-home rule):

| Family | Canonical | Duplicates | Effort |
|---|---|---|---|
| HTTP retry | `data/http.get_with_retry` | 36 raw `requests.get` sites in 14 data/ui files (`data/events/ir_site.py` ×7, `data/ma_announcements.py` ×5, `data/form4_client.py` ×4, `data/form13f_client.py` ×4, `data/filing_summarizer.py` ×3, `data/ma_summary.py` ×3, `data/bank_mapping.py` ×2, `data/fred_client.py` ×2, `data/events/wire_base.py:610 fetch_rss` — feeds every wire adapter, `ui/historicals.py:73` — also per-cert, bypassing the group join) + 13 in tools/; **the deploy gate `tests/test_universe_coverage.py:30` carries a diverged copy of `get_with_retry`** (max 4 attempts, returns None instead of raising); 5 separate SEC ≤10 req/s throttles (`sec_filing_scraper.py:39`, `xbrl_dimensional.py:65`, `_SecThrottle` byte-identical in `jobs/refresh_capital.py:42`, `refresh_company_financials.py:38`, `refresh_cr_quarterly.py:48`) | S (gate, throttle), M (36 sites) |
| Numeric formatters | `utils/formatting.fmt_dollars*` | 9 local dollar auto-scalers each with a display divergence: `ui/geo_view.py:121` (no `abs()` → `$-N`), `ui/bank_detail.py:31/:43` (returns None, no T/K tiers), `ui/transactions.py:794` (`$0.5M`), `ui/macro.py:129`, `ui/earnings.py:1536`, `ui/data_quality.py:435`, `ui/capital_dynamics.py:120` (pure alias), `ui/macro.py:111`, `ui/transactions.py:1006`; 19 inline `f"${x/1e9:.1f}B"` literals; three near-identical `_num` parsers (`data/fmp_client.py:769`, `fmp_compensation.py:30`, `econ_calendar.py:34`) | M (each swap changes rendered text → mock first) |
| Freshness | `data/freshness.is_fresh` | 14 identical 3-line `_is_fresh` module wrappers (harmless); a second value-embedded TTL layer `data/fmp_client.py:58-88 _cache_get/_cache_put` (shared by `fmp_compensation`, `fmp_transcripts`) and four hand-rolled `_ts` envelopes (`fdic_client.py:249-342` ×3, `live_rates.py:74-88`, `bank_geography.py:55-63`) that `put_snapshot_if_fresher` cannot guard; `ui/home.py:934-940` is a verbatim second copy of the clobber guard (`data/cache.py:122-124` admits it) | M |
| Junk-news filter | `wire_base.is_junk_news` | clean, except `ui/bank_detail.py:341` filters with `is_routine_noise` only → Corporate Profile can show headlines the Home feed rejects | S |
| Chart styling | `utils/chart_style` + `ui/styles.py` tokens | ~304 hex literals in 27 ui files (`macro.py` 59, `home.py` 58, `financial_highlights.py` 33, `peer_rank.py` 22, `rate_sensitivity.py` 18, `valuation_model.py` 17); 24 raw `update_layout(`; `ui/charts.py` is a half-migrated second chart module (14 hex + 9 layout overrides beside 24 `apply_standard_layout` calls) | S per plotly file, L for the HTML colours (must become `var(--…)` and be pixel-verified) |
| Table export | `ui/export.table_export` | clean | — |
| DB engine | `data/db.get_engine` | `tools/ladder_coverage.py:37-45` and `tools/backtest_small_banks.py:76-84` (near-identical Cloud SQL connectors, both citing the gitignored `verify_ffiec_e2e.py` as origin); `tools/verify_pending_deals.py:43` raw `sqlite3.connect`; four byte-identical `_get_engine` shims (`cache.py:35`, `call_report_store.py:99`, `price_cache_store.py:50`, `branches_store.py:81`) | S |
| Cache | `data/cache.py` | `data/cloud_storage.save_json/load_json` used as a fetch cache by form4/form13f/macro/estimates (484 + 325 + 81 local files) beside the store; 76 `@st.cache_data` decorators in 23 files; six frozen module memos (P1-7) | L (data migration) |
| `load_fdic_hist` seam | `data/loaders` → `cert_group.fetch_group_history` | compliant, except `ui/historicals.py:57-75 fetch_historical` (raw FDIC `requests.get` per cert — one charter of a multi-charter holdco, under the Financial Highlights "Trend charts" expander) and the dead `ui/data_quality.py:58`; `data/fdic_history_store.py:189-200` mirrors `cert_group.py:312-324`'s aggregate loop (documented) | S |

Copy-paste (≥ 8 normalised lines, cross-file): the three wire adapters'
byte-identical 22-line fetch→dedup→noise→match→junk→Event loop
(`data/events/businesswire.py:53-96`, `prnewswire.py:38-71`, `globenewswire.py:42-75`);
`_SecThrottle` ×3 (above); the alerts block ×3 (`ui/capital_dynamics.py:147-160`,
`credit_dynamics.py:157-170`, `deposit_dynamics.py:164-177`) while
`ui/components.alert_row` sits dead; Anthropic client bootstrap ×2
(`data/governance.py:118-130`, `data/people.py:132-144`); cell-shading builder ×2
(`ui/generic_table.py:182-190`, `ui/trends_table.py:53-61`); "does not file with
the SEC" empty state ×4; FMP quote fallback ×2 (`app.py:670-677`,
`jobs/refresh_home_snapshot.py:112-119`); identical six-None result dict ×2
(`analysis/credit_dynamics.py:278-284`, `analysis/valuation.py:1210-1216`). All S.

**Modules > 1,500 lines and the mechanical seams** (AST spans):

| File | Lines | Largest function | Seam that would pay |
|---|---|---|---|
| `ui/financials_statements.py` | 4,752 | `render_statement` `:429-1491` (**1,063** lines) | Company Reported block `:2130-4739` (~2,600 lines: 17 `_cr_*` fns, `_render_company_*`, fair value → rate risk) + its spec dicts `:1524-2129` → `ui/financials_company_reported.py`; keep `_V/_usd/_pct` (`:32-60`) in a small common module. `render_statement` itself is not mechanically splittable. L |
| `ui/earnings.py` | 2,870 | `_render_surprise_heatmap` `:1063-1258` (196) | uploads `:2597-2870` → `ui/earnings_upload.py` (S); calendar + heatmap `:1016-1507` → `ui/earnings_calendar.py` (M) |
| `data/sec_filing_scraper.py` | 2,606 | `extract_holdco_capital` `:245-392` (148) | already sectioned by table (capital `:227-706`, fair value, securities, credit, NIM, performance/highlights, segments, rate risk) around a shared core `:39-225`; delete the 7 dead `*_for` first (−190 lines). M |
| `ui/macro.py` | 1,935 | `_render_credit_spreads` `:1754-1935` (182) | rates board+curve `:1384-1707` and credit spreads `:1725-1935` are self-contained. M |
| `app.py` | 1,722 | 82 % top-level script flow | no mechanical seam (session-state coupling); only `_screen_*` `:330-417`. L |
| `ui/home.py` | 1,716 | `_af_calendar_table` `:751-880` (130) | 31 `_af_*` fns / 931 lines pair `_af_pane_X` with `_af_X_table` — one file per pane. M |
| `data/sec_statements.py` | 1,692 | `parse_rfile` `:378-478` | multi-period stitching `:1162-1692` → `sec_statements_stitch.py`. M |
| `data/sec_client.py` | 1,624 | `get_latest_fundamentals` `:530-833` (**304**) and `get_fundamentals_with_provenance` `:1130-1416` (**287**) are parallel extractions of the same fundamentals | de-duplicate before splitting; verify field by field. L |
| `data/bank_universe.py` | 1,548 | `_build_universe_live` `:468-748` (281) | NAMEHCR guard cluster `:1132-1548` (~415 lines) → `data/namehcr_guard.py`. S-M |

**Cache hygiene, smaller items** (from the full inventory: 60 `@st.cache_data`
sites, ~180 store call sites over ~95 key families, all resolved to literal key
patterns; every key written in current code has a reader and vice-versa):

- `ui/branch_analytics.py:64 _dep_usd(v)` is `@st.cache_data(ttl=3600)` on a number formatter called once per table row → one memo entry per distinct deposit value. Delete the decorator. S.
- `fmp_exec_comp:v1:{T}` (`data/fmp_compensation.py:48`) and `fmp_exec_comp:v2:{T}` (`data/fmp_client.py:970`): two modules cache the same FMP endpoint under different keys and parsers. Delete one. S.
- `data/form13f_client.py:431 _holders_from_candidates(candidates: list[dict], …)` is memoised on a list rebuilt per call → hits only on a byte-identical search result; the real cache is the 24 h file at `:560`. Drop the decorator. S.
- Design TTLs longer than 24 h silently capped by the default read: `nic:tree/parent` 30 d (`nic_client.py:489/569` — re-parses the monthly NIC bulk files daily per RSSD), `census:acs5_*` 30 d, `entity_lifespan` 30 d, `fdic_structure` 7 d, `q4_site:v2`/`irapp_site:v1` 7 d (one HTTP probe per IR host per day instead of per week; 76 + 52 hosts), `fmp_profile_name`/`fmp_tx:v1` via `_cache_get` (`fmp_client.py:71`). `max_age_s=None` where an `is_fresh`/`_ts` check follows. S each.
- File caches shorter than their warm cadence → live fetch on render: `estimates_cache/{T}.json` 6 h vs nightly warm (from ~noon `fetch_all_estimates(tuple(watchlist[:30]))` at `ui/earnings.py:913/:1072/:2328/:2494` runs live yfinance under the `st.cache_data` key lock); `form4_cache` 24 h vs 04:30 job; `form13f_cache` 24 h vs a **quarterly** job (EFTS search + per-filer fetches inline on the first Ownership view every day). Owner call on serve-stale. M.
- "Refresh all data" (`app.py:150-151`) calls `st.cache_data.clear()` **and** `cache.clear_all()` — wipes every immutable accession extraction, the universe snapshot and the nightly baseline for every instance, then the 6.5-min universe bootstrap lands on the request path. Invalidate per-page keys the way "Refresh this view" (`:786-800`) already does. S.
- Store never GC'd: 42 % of local rows and **70 % of bytes (645 of 923 MB)** are version-bump residue — five `sec_facts` generations (1,428 rows), `reported_tbvps:v2-v4` (1,040), `release_metrics:v3-v17` (261), `asreported_my:v3-v5` (573), `otc_release:v1-v8` (126), plus `fmp_earn_cal:v2:{from}:{to}` accumulating one 1.1 MB row per daily window with a 1 h design TTL. Prod Postgres has the same `put`-only shape. A nightly sweep by age with an immutable-prefix exemption. S.
- `fetch_company_facts` (1 h memo, ~1 MB slim dict per CIK, largest local row 5.2 MB) has no `max_entries`; a Screen/Compare run touching many CIKs holds hundreds of MB per instance for an hour. S.
- `tests/test_snapshot_clobber_guard.py:85-96` says "both `watchlist_metrics_snap` writers must route through the guard" and checks the two jobs — `app.py:762` is a third writer using plain `cache.put(_METRICS_SNAP_KEY, …)`, invisible to the test because it greps for the string literal. Not a clobber today (the inline rebuild is always the freshest), but the structural test does not cover the site it names.

**Test hygiene** — measured in §1.5: full discovery green (2,982 tests, 64 s),
but 13 tests in 9 modules reach the real network, two budget suites burn 60 s
of a 211 s serial wall on real sleeps, and two `test_audit_regressions` tests
are green only because another module's stub runs first. Structural tests that
grep source for a literal (`tests/test_snapshot_clobber_guard.py:85-96`,
`tests/test_company_facts_cache_key.py` "key literal exists only in sec_client")
pin the spelling, not the behaviour — the third `watchlist_metrics_snap` writer
in `app.py:762` is invisible to the first because it uses the constant name.

---

## 3. Suggested order (smallest diff, biggest correctness win first)

1. P0-1, P0-3, P0-4, P0-5, P0-6, P0-7 — six S-effort None-not-zero fixes, each with a one-assert test.
2. P0-2 — DPS TTM window (M); size it with the FMP oracle first.
3. P1-4, P1-5, P1-6, P1-8 and the P2 decorator items — one-line cache fixes with behavioural pins; extend `tests/test_cache_read_ceilings.py`.
4. P1-3 — `min_quarters=44` (S) once the cold 33 s is profiled.
5. P1-2 — accession-keyed reads at `None` + an `instance_facts` memo (M): turns 17–22 s cold pages into the 2–4 s warm cost, and removes the daily re-download.
6. P1-1, P1-9 — snapshot-served Macro and Earnings board (M each).
7. P2 dead-code deletion (S, ~1,500 lines), the syntax error, the deploy-gate retry copy, `is_junk_news` parity on Corporate Profile.
8. Formatter and HTTP consolidation, the CR split of `financials_statements.py` — M/L, schedule after go-live.
