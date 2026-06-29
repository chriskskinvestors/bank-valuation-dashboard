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
> just *wiring* it into Performance Analysis + Credit Quality. Everything else in
> the inventory is current.

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
| Loan Composition | `_cr_loan` → `_render_company_composition(t,"loan")` — `financials_statements.py:1535` | `compositions_for` — `sec_composition.py:523` (via `_compositions_cached:1512`) | Latest **10-K** iXBRL composition note (filer's own categories) | **Single-period** (one period from latest 10-K) | None | Reliable: each line the filer's own category, reconciled to the disclosed total. n/a: when no clean, reconciling loan-composition table is disclosed. |
| Deposit Composition | `_cr_deposit` → `_render_company_composition(t,"deposit")` — `financials_statements.py:1535` | `compositions_for` — `sec_composition.py:523` (same cached fetch as loans) | Latest **10-K** iXBRL composition note | **Single-period** | None | Reliable: filer's own deposit categories, reconciled to total. n/a: when deposit mix isn't disclosed as a clean reconciling table. |
| Securities Portfolio | `_cr_securities` → `_render_securities_portfolio` — `financials_statements.py:1654` | `securities_for` — `sec_filing_scraper.py:869` → `extract_securities` | Timeliest **10-Q**, then **10-K** iXBRL | **Single-period** (latest filing) | None | Reliable: AFS/HTM amortized cost, gross gain/loss, fair value, net unrealized, underwater %. n/a: when no reconciling amortized-cost ↔ fair-value pair is tagged. |
| Fair Value | `_cr_fair_value` → `_render_fair_value_hierarchy` — `financials_statements.py:1585` | `fair_value_for` — `sec_filing_scraper.py:707` → `extract_fair_value` | Timeliest **10-Q**, then **10-K** iXBRL (ASC 820) | **Single-period** (latest filing) | None | Reliable: recurring L1/L2/L3 assets & liabilities + Level-3 %; counterparty/collateral netting shown as a reconciling line when the grand total differs from the level sum. n/a: filers that don't tag a hierarchy rollup (per-instrument extraction is planned). |
| Segment Reporting | `_cr_segments` → `_render_segments` — `financials_statements.py:1914` | `segments_for` — `sec_filing_scraper.py:1427` → `extract_segments` | Latest **10-K** iXBRL segment footnote | **Single-period** (latest FY) | None | Reliable: per-segment NI/revenue/assets + a Corporate/other residual reconciling to consolidated NI. n/a: single-segment banks (fewer than two reportable segments) — by design. |
| Interest Rate Risk | `_cr_rate_risk` → `_render_rate_risk` — `financials_statements.py:1961` | `rate_risk_for` — `sec_filing_scraper.py:1489` → `extract_rate_risk` (+ FDIC CET1 anchor) | Timeliest **10-Q**, then **10-K** iXBRL (securities marks vs capital) | **Single-period** (latest filing) | None | Reliable: AFS+HTM unrealized gain/(loss), vs equity and vs CET1 — the *embedded* (already-on-the-books) rate risk. n/a: forward NII/EVE rate-shock sensitivity (narrative Item 7A, not standardized XBRL) — linked, not scraped. |

**Summary of the gap:** 3 of 12 sub-tabs are multi-year today (Income, Balance,
Regulatory Capital). Financial Highlights is single-period on this branch (a
5-FY rebuild is in-flight). The remaining 8 (Performance, Credit Quality,
Loan/Deposit Composition, Securities, Fair Value, Segments, Rate Risk) are
single-period — latest filing only. **Zero sub-tabs have trend charts.**

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
- [ ] **Loan Composition** — `compositions_for` returns `{period: {...}}` but the
      renderer shows only `next(iter(...))`; widen the extract to multiple 10-Ks
      and render the category × year matrix (blank where a category appears/drops).
- [ ] **Deposit Composition** — same stitch as Loan Composition (shares the
      cached fetch).
- [ ] **Securities Portfolio** — collect AFS/HTM bridge across the last ~5
      period-ends (10-Q + 10-K), render the underwater trend.
- [ ] **Fair Value** — collect the L1/L2/L3 hierarchy across periods; render
      Level-3 % trend.
- [ ] **Segment Reporting** — stitch segment NI/revenue/assets across the last
      ~5 FYs (segment labels drift — match tolerantly, blank on absence).
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

- [ ] Run all Company-Reported extractors across all ~367 universe banks; record
      per-metric, per-tab coverage % (value vs n/a).
- [ ] Triage the high-impact misses: distinguish genuine non-disclosure from
      parser gaps (label variants, custom taxonomy extensions, split-filer
      doc-sets, dimensional-only tagging).
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
