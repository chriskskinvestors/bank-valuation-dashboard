# Company-Reported Financials — Build Plan to Full Coverage

Tracked plan for the **Company Reported** basis of the Financials section: an
inventory of the 12 sub-tabs as they exist today, and the build order to bring
every one to full, multi-year, trend-charted, timely coverage.

> Status note (2026-06-29, on `main`): the multi-year **Financial Highlights**
> rebuild **is SHIPPED** — it now renders 5 FY with section bands + trend charts
> (ROAA/ROAE/Efficiency/ROATCE), and NIM / NPLs / net-charge-offs fill from the
> bank's own 10-K via `company_asset_quality_nim()` (`data/sec_filing_scraper.py`),
> company-scraped, never FDIC. So in the inventory below, the "Financial
> Highlights = single-period / in-flight" notes are **superseded** (that row is
> DONE), and Phase 2's NIM/NPL/NCO extractor already **exists** — Phase 2 is now
> just *wiring* it into Performance Analysis + Credit Quality.
>
> Status note (2026-06-29, this session — multi-year extractors): the
> **Phase-1 multi-year stitch** now also exists for **Securities Portfolio**
> (`securities_multiyear_for`), **Fair Value** (`fair_value_multiyear_for`),
> **Segment Reporting** (`segments_multiyear_for`) and **Loan/Deposit
> Composition** (`compositions_for` returns every reconciling period). These
> extractors are built and (per the sampled coverage run below, §5) hold up
> across the universe without crashing. The inventory rows below are updated to
> mark them DONE-multiyear. This phase MEASURED only — no fixes were applied; the
> EMPTY patterns in §5 are the follow-up backlog.

---

## 1. Principle

**Company Reported = the bank's OWN filings, only.**

Every figure on a Company-Reported sub-tab comes from the holding company's own
SEC documents:

- **10-K / 10-Q inline XBRL** (the tagged facts), or
- **10-K table / MD&A scrapes** (the average-balance table, the allowance
  rollforward, the as-reported composition tables) where the number isn't
  cleanly tagged.

**Never FDIC / FFIEC.** Call-report-sourced figures are the *Templated* basis
(the parallel tab tree). The two bases differ structurally — HoldCo
(consolidated, SEC) vs bank-subsidiary (FDIC) — and must never be cross-fed.
The one permitted cross-reference is the FDIC CET1 **anchor** used to validate a
scraped holdco capital ratio (`_fdic_cet1`); it gates display, it is never
shown as a Company-Reported value.

**Cardinal rule (platform-wide):** a metric the filing doesn't cleanly disclose
renders **n/a + reason**, never a guess. Every Company-Reported extractor is
reconcile-gated — if the tagged components don't tie to the disclosed total, the
row is n/a, not a component-summed plug.

---

## 2. Current-state inventory

Dispatch: `ui/company_nav.py` → `_CR_RENDERERS` (12 entries) → renderers in
`ui/financials_statements.py` (+ `_render_holdco_capital` in
`ui/capital_dynamics.py`). Underlying extractors live in
`data/sec_statements.py`, `data/sec_composition.py`,
`data/sec_filing_scraper.py`.

| Sub-tab | Renderer (file:line) | Scraper / extractor (file:line) | Source | Single / multi-year | Trend charts | Reliably scraped vs common n/a |
|---|---|---|---|---|---|---|
| Financial Highlights | `_render_financial_highlights` — `financials_statements.py:1789` | `financial_highlights_for` — `sec_filing_scraper.py:1289` (+ FDIC CET1 anchor `_fdic_cet1:497`) | Latest **10-K** iXBRL, one page | **Single-period** on this branch (latest 10-K). *In-flight rebuild: 5-FY multi-year, filling NIM/NPL/NCO via `company_asset_quality_nim` — see status note.* | None | Reliable: assets/loans/deposits/equity, NI, EPS, ROA/ROE, efficiency, CET1 (anchored), ACL%, NPL%. n/a: any headline the latest 10-K didn't tag. |
| Income Statement | `_cr_income` → `_render_company_statement(t,"income")` — `financials_statements.py:1449` | `as_reported_statement_multiyear(cik,"income",5)` — `sec_statements.py:399` | **10-K R-files** (financial-statement R-tables), 4 filings stitched | **Multi-year** (up to 5 FY) | None | Reliable: the filer's own IS line items, stitched across years; blank where a line wasn't reported a given year. n/a: per-share trailer omitted pending unit handling. |
| Balance Sheet | `_cr_balance` → `_render_company_statement(t,"balance")` — `financials_statements.py:1449` | `as_reported_statement_multiyear(cik,"balance",5)` — `sec_statements.py:399` (reaches back 6 filings — BS carries ~2 periods each) | **10-K R-files** | **Multi-year** (up to 5 FY) | None | Reliable: as-reported BS lines stitched across years. n/a: lines a filer didn't separately report. |
| Performance Analysis | `_cr_performance` → `_render_performance` — `financials_statements.py:1851` | `performance_for` — `sec_filing_scraper.py:1219` → `extract_performance:1117` | Latest **10-K** iXBRL (full-year) | **Single-period** (latest FY only) | None | Reliable: revenue, NII, noninterest inc/exp, PPNR, provision, NI, diluted EPS, efficiency, ROA/ROE (avg balances, `_avg_computed` flagged). n/a: when core IS lines aren't tagged for the latest FY. **No NIM / yield / cost-of-funds rows yet** (that lives in `company_asset_quality_nim`, not yet wired here). |
| Regulatory Capital | `_cr_reg_capital` → `_render_holdco_capital` — `capital_dynamics.py:404` | `holdco_capital_for(cik, cert)` — `sec_filing_scraper.py` (anchored to FDIC CET1) | Latest **10-K/10-Q** iXBRL, holdco capital schedule | **Multi-year** (up to 5 periods, `sorted(...)[:5]`) | None | Reliable: CET1/T1/Total/Leverage ratios + capital $ + RWA, anchored to FDIC CET1; bank-sub basis fallback labeled. n/a: LCR/HQLA/SLR (large-bank FR 2052a, not in filing). |
| Credit Quality / Allowance | `_cr_credit` → `_render_credit_quality` — `financials_statements.py:1728` | `credit_quality_for` — `sec_filing_scraper.py:1060` → `extract_credit_quality` (+ composition loan-total fallback) | Timeliest **10-Q**, then **10-K** iXBRL | **Single-period** (latest filing) | None | Reliable: gross loans, ACL, ACL/loans, nonaccrual, NCO, provision — when allowance + gross-loans tag as a reconciling pair. n/a: filers that tag loans only by segment (partial dimensional fallback exists for some, e.g. FFIN). |
| Loan Composition | `_cr_loan` → `_render_company_composition(t,"loan")` — `financials_statements.py:1535` | `compositions_for` — `sec_composition.py:545` (via `_compositions_cached`) | Latest **10-K** iXBRL composition note (filer's own categories) | **Multi-year** — `compositions_for` returns every reconciling period (DONE this session; 100% multi-year in the §5 sample) | None | Reliable: each line the filer's own category, reconciled to the disclosed total. n/a: when no clean, reconciling loan-composition table is disclosed. |
| Deposit Composition | `_cr_deposit` → `_render_company_composition(t,"deposit")` — `financials_statements.py:1535` | `compositions_for` — `sec_composition.py:545` (same cached fetch as loans) | Latest **10-K** iXBRL composition note | **Multi-year** (DONE this session; shares the loan fetch) | None | Reliable: filer's own deposit categories, reconciled to total. n/a: when deposit mix isn't disclosed as a clean reconciling table. |
| Securities Portfolio | `_cr_securities` → `_render_securities_portfolio` — `financials_statements.py:1654` | `securities_multiyear_for` — `sec_filing_scraper.py:994` → `extract_securities` | **10-K** iXBRL, stitched FY-ends | **Multi-year** (DONE this session; **100% multi-year**, 0 empty in the §5 sample) | None | Reliable: AFS/HTM amortized cost, gross gain/loss, fair value, net unrealized, underwater %. n/a: when no reconciling amortized-cost ↔ fair-value pair is tagged. |
| Fair Value | `_cr_fair_value` → `_render_fair_value_hierarchy` — `financials_statements.py:1585` | `fair_value_multiyear_for` — `sec_filing_scraper.py:760` → `extract_fair_value` | **10-K** iXBRL (ASC 820), stitched FY-ends | **Multi-year** (DONE this session; 67% multi-year, **33% empty** in the §5 sample — see follow-up) | None | Reliable: recurring L1/L2/L3 assets & liabilities + Level-3 %; counterparty/collateral netting shown as a reconciling line when the grand total differs from the level sum. n/a: filers that don't tag a FY-END hierarchy rollup (8 of 24 sampled returned none — parser-miss candidate, §5). |
| Segment Reporting | `_cr_segments` → `_render_segments` — `financials_statements.py:1914` | `segments_multiyear_for` — `sec_filing_scraper.py:1914` → `extract_segments` | **10-K** iXBRL segment footnote, stitched FY-ends | **Multi-year** (DONE this session; 54% multi-year, **42% empty** — many genuine single-segment banks, §5) | None | Reliable: per-segment NI/revenue/assets + a Corporate/other residual reconciling to consolidated NI. n/a: single-segment banks (fewer than two reportable segments) — by design. |
| Interest Rate Risk | `_cr_rate_risk` → `_render_rate_risk` — `financials_statements.py:1961` | `rate_risk_for` — `sec_filing_scraper.py:1489` → `extract_rate_risk` (+ FDIC CET1 anchor) | Timeliest **10-Q**, then **10-K** iXBRL (securities marks vs capital) | **Single-period** (latest filing) | None | Reliable: AFS+HTM unrealized gain/(loss), vs equity and vs CET1 — the *embedded* (already-on-the-books) rate risk. n/a: forward NII/EVE rate-shock sensitivity (narrative Item 7A, not standardized XBRL) — linked, not scraped. |

**Summary of the gap (updated 2026-06-29):** 9 of 12 sub-tabs now have a
multi-year extractor — Income, Balance, Regulatory Capital, Financial Highlights
(shipped earlier), plus **Securities, Fair Value, Segments, Loan/Deposit
Composition** (the four built THIS session, confirmed across the §5 sample). The
remaining single-period tabs are **Performance Analysis, Credit Quality, and
Interest Rate Risk**. Financial Highlights, ROAA/ROAE/Efficiency/ROATCE have
trend charts; the other tabs are still table-only.

---

## 3. Build phases to full coverage

### Phase 1 — Multi-year everywhere
*Rationale: a single period can't show a trend; SNL-grade tables are 5-FY. The
multi-year stitch pattern already exists (`as_reported_statement_multiyear`,
`holdco_capital_for[:5]`, `company_asset_quality_nim` by_year) — apply it to the
single-period extractors.*

- [ ] **Performance Analysis** — extend `extract_performance` to return
      `{fy_end: {...}}` across the last ~5 fiscal years (it already computes one
      FY; loop the fiscal-year detection), and render the multi-FY table.
- [ ] **Credit Quality / Allowance** — stitch `credit_quality_for` across the
      last ~5 filings (it currently takes the single latest 10-Q/10-K), keyed by
      period, render multi-column.
- [x] **Loan Composition** — `compositions_for` now returns every reconciling
      period (DONE this session). 100% multi-year in the §5 sample.
- [x] **Deposit Composition** — DONE this session (shares the loan fetch).
- [x] **Securities Portfolio** — `securities_multiyear_for` stitches the AFS/HTM
      bridge across FY-ends (DONE this session). 100% multi-year, 0 empty in §5.
- [x] **Fair Value** — `fair_value_multiyear_for` stitches the L1/L2/L3 hierarchy
      across FY-ends (DONE this session). 67% multi-year in §5; the 33% empty are
      a parser-miss candidate to chase (no FY-END hierarchy returned).
- [x] **Segment Reporting** — `segments_multiyear_for` stitches segment
      NI/revenue/assets across FY-ends (DONE this session). 54% multi-year in §5;
      the 42% empty are mostly genuine single-segment banks (by design).
- [ ] **Interest Rate Risk** — collect the embedded unrealized-vs-capital
      snapshot across the last ~5 period-ends.

### Phase 2 — Wire `company_asset_quality_nim` into Performance + Credit Quality
*Rationale: NIM, NPL and NCO are the headline bank metrics and they're already
extracted (10-K MD&A average-balance table + allowance rollforward, company data
never FDIC) — they just aren't shown on the detailed tabs yet.*

- [ ] **Performance Analysis** — add NIM (and, where the average-balance table
      supports it, earning-asset yield / cost of funds) rows from
      `company_asset_quality_nim(cik).by_year`, multi-FY.
- [ ] **Credit Quality** — add the multi-year NPL/loans and NCO/loans trend from
      the same extractor (the allowance rollforward), so the tab stops being a
      single-period snapshot.
- [ ] Merge the in-flight 5-FY **Financial Highlights** rebuild (already wires
      `company_asset_quality_nim`) onto this branch and confirm the highlights
      NIM/NPL/NCO cells reconcile to the detailed tabs.
- [ ] Pin each with a hand-computed test (one bank, one FY) — NIM, NPL%, NCO%
      against the raw 10-K MD&A / rollforward.

### Phase 3 — Trend charts (Performance Analysis + Regulatory Capital)
*Rationale: the known open tail — every Company-Reported tab is table-only.
Performance and Regulatory Capital are the two with the cleanest multi-year
series to chart first.*

- [ ] **Performance Analysis** — trend charts (ROA/ROE/NIM %, efficiency %, PPNR
      $) using the multi-FY series from Phases 1–2, styled via
      `utils/chart_style.py`.
- [ ] **Regulatory Capital** — trend chart of CET1/T1/Total/Leverage ratios over
      the multi-period holdco series.
- [ ] Extend trend charts to the other multi-year tabs once their series land.

### Phase 4 — 8-K earnings-supplement scrape (timeliness layer)
*Rationale: there is a ~4-week gap between an earnings 8-K and the 10-Q. Banks
release a full financial supplement (EX-99.1) on the 8-K; scraping it closes the
latest-quarter gap. 8-Ks are already ingested for News/Earnings via
`data/events/` (incl. `sec_8k.py`) but are NOT used for the financial tables.*

- [ ] Locate the most-recent earnings 8-K and its **EX-99.1** financial
      supplement (reuse the `data/events/` 8-K discovery; do not re-ingest).
- [ ] Parse the supplement **defensively**: it is **not XBRL** — free-form HTML,
      per-bank layout, often **non-GAAP / preliminary**. Tolerant label/table
      matching; extract only what's unambiguous.
- [ ] Surface the latest-quarter figures on the relevant tabs labeled clearly
      **"as-released / preliminary (8-K EX-99.1)"**, visually distinct from
      audited columns.
- [ ] **Reconcile to the 10-Q once filed**: when the 10-Q lands, the audited
      figure replaces the preliminary one. **Never overwrite an audited
      10-K/10-Q figure with an 8-K number** — the 8-K layer fills *only* the
      not-yet-filed period.
- [ ] Test: a quarter where 8-K preliminary and the later 10-Q differ — confirm
      the preliminary is labeled, then superseded, never silently merged.

### Phase 5 — Universe-coverage validation
*Rationale: n/a must mean "the bank didn't disclose it," not "our parser missed
the label." The only way to know is to run every extractor across the whole
universe and measure.*

- [~] Run all Company-Reported extractors across the universe; record per-metric,
      per-tab coverage % (value vs n/a). **Started 2026-06-29:** sampled 24 banks
      via `tools/cr_coverage_report.py` (§5 above). Full ~367-bank sweep is the
      remaining work.
- [~] Triage the high-impact misses: distinguish genuine non-disclosure from
      parser gaps (label variants, custom taxonomy extensions, split-filer
      doc-sets, dimensional-only tagging). **Started:** §5 backlog — KEY statement
      stitch, fair-value FY-end matching, latest-FY NIM.
- [ ] Fix the high-impact label/structure misses (tolerant matchers), re-measure.
- [ ] Add a small **coverage report / test** (akin to
      `tests/test_universe_coverage.py`) that records the coverage baseline and
      fails on regression, with the residual n/a explained per metric.

---

## 4. Definition of done — "fully scraped"

A sub-tab is done when:

1. **Multi-year** wherever the filings support it (single-period only where the
   data genuinely is, e.g. a one-time disclosure).
2. **Trend charts** present on the metric tabs (Performance, Regulatory Capital,
   and the other multi-year series).
3. **8-K timeliness layer live** — the latest quarter shows as-released figures,
   clearly labeled preliminary, auto-superseded by the 10-Q, never overwriting
   audited data.
4. **Per-metric universe coverage report** exists, every residual n/a is
   explained (genuine non-disclosure vs. a known parser limitation), and the
   coverage baseline is pinned by a test.

Throughout: every Company-Reported value sources to a company document, every
gate is reconcile-checked, and any figure the filing doesn't cleanly disclose
renders **n/a + reason** — never a plausible-wrong number.

---

## 5. Universe coverage (sampled 2026-06-29)

Measured by `tools/cr_coverage_report.py` over a fixed, hand-picked sample of
**24 diverse banks** (big money-center + small regional, different filers):
ABCB, PNFP, FFIN, WSFS, CBSH, FHN, WAL, CFR, ONB, UMBF, BOKF, HWC, ASB, FNB,
VLY, WTFC, COLB, GBCI, TFC, FITB, RF, KEY, ZION, EWBC. (SNV was skipped — no CIK
from `get_bank_info`.) Each extractor call is wrapped in try/except, so a
per-bank failure is recorded, never fatal. This phase MEASURES only — no fixes.

Classification: **OK-multiyear** = ≥2 fiscal years/reconciling periods;
**OK-single** = exactly 1; **EMPTY** = None/0 (parser-miss candidate OR genuine
non-discloser); **ERROR** = raised (a real bug).

| function | n | multiyr | single | empty | error |
|---|---|---|---|---|---|
| `_cr_highlights_by_year` | 24 | 79% | 0% | 21% | 0% |
| `company_asset_quality_nim` | 24 | 96% | 0% | 4% | 0% |
| `as_reported_statement(income)` | 24 | 92% | 0% | 8% | 0% |
| `as_reported_statement(balance)` | 24 | 88% | 0% | 12% | 0% |
| `securities_multiyear_for` | 24 | 100% | 0% | 0% | 0% |
| `fair_value_multiyear_for` | 24 | 67% | 0% | 33% | 0% |
| `segments_multiyear_for` | 24 | 54% | 4% | 42% | 0% |
| `compositions_for` | 24 | 100% | 0% | 0% | 0% |

**ERROR banks (real bugs):** none — **no extractor raised on any sampled bank.**
The multi-year scrapers built this session are crash-clean across the sample.

**Latest-year metric fill (per-metric functions):**

- `_cr_highlights_by_year`: overall latest-year fill **90/133 (68%)** across 19
  non-empty banks. Most-missing latest-year metrics: nim(14), efficiency(11),
  roaa(6), net_income(5), cet1(3), npl_loans(2), total_assets(2).
- `company_asset_quality_nim`: overall latest-year fill **43/69 (62%)** across 23
  non-empty banks. Most-missing: **nim(17)**, nco_loans(8), npl_loans(1).

### EMPTY patterns — genuine non-disclosure vs parser-miss (follow-up backlog)

- **`segments_multiyear_for` — 42% empty** (PNFP, FFIN, WSFS, CBSH, ONB, HWC,
  VLY, COLB, +2). Mostly **genuine**: single-segment community/regional banks
  have no reportable-segment footnote by design (the extractor requires ≥2
  reportable segments + a consolidated total). Low-priority; spot-check a couple
  against their 10-Ks before assuming all are genuine.
- **`fair_value_multiyear_for` — 33% empty** (FFIN, WAL, CFR, ONB, UMBF, BOKF,
  RF, EWBC). **Likely parser-miss, not non-disclosure** — every bank tags a
  recurring ASC 820 hierarchy somewhere; these returned no *FY-END* hierarchy
  (the multi-year function keeps only `period[5:7]=="12"`). RF/EWBC/WAL are large
  filers that certainly disclose the table, so the miss is probably a FY-end
  tagging/period-shape issue (Dec-31 vs the filing's own period key) or a
  split-filer doc-set. **Top follow-up: chase the FY-end period match here.**
- **`_cr_highlights_by_year` — 21% empty** (CBSH, FNB, COLB, FITB, KEY). Driven
  by its dependency on `as_reported_statement_multiyear` (income+balance both
  required) — CBSH/KEY are EMPTY on the income statement and FNB/FITB/KEY on the
  balance statement, so highlights goes empty for the same banks. **Parser-miss
  candidate** — these are real, large filers; the R-file statement stitch is
  missing their income/balance tables (split-filer doc-set or R-table layout).
  Fixing the statement extractor lifts highlights with it.
- **`as_reported_statement` income 8% / balance 12% empty** (CBSH, KEY income;
  FNB, FITB, KEY balance). Same parser-miss candidates as above — **KEY is empty
  on income, balance, AND highlights**, so KEY is the single highest-value bank
  to debug first (one root cause likely clears three functions).
- **NIM most-missing latest-year metric** (missing for 14 banks in highlights,
  17 in asset-quality). `company_asset_quality_nim` itself is 96% non-empty, but
  the *latest-year NIM cell* is frequently None — the MD&A average-balance table
  is scraped per fiscal year and the newest 10-K's latest FY NIM often isn't
  landing. **Parser-miss candidate worth a follow-up** (NIM is a headline metric).

**Read:** EMPTY splits two ways. Segment emptiness is mostly **genuine**
(single-segment banks). Fair-value, statement/highlights, and the NIM cell are
**parser-miss candidates** — large disclosers returning nothing where the data
demonstrably exists. The concrete follow-up backlog, highest-value first:
**(1) KEY** statement stitch (clears income+balance+highlights); **(2)
fair-value FY-end period matching** (8 banks incl. RF/EWBC/WAL); **(3) latest-FY
NIM** landing in `company_asset_quality_nim`.

Re-run anytime: `python -m tools.cr_coverage_report`
(`--slow-sub N` caps the slow extractors at the first N banks; a comma-separated
ticker list as argv[1] overrides the sample). Scrapers cache, so re-runs are fast.
