# Company-Reported Financials — Build Plan to Full Coverage

Tracked plan for the **Company Reported** basis of the Financials section: an
inventory of the 12 sub-tabs as they exist today, and the build order to bring
every one to full, multi-year, trend-charted, timely coverage.

> **STATUS 2026-07-14 — QUARTERLY TRACK SHIPPED (supersedes the §2 inventory's
> period notes).** Every CR tab now has an Annual/Quarterly toggle except
> Segment Reporting (segment notes are annual-only disclosures — honest skip):
> - Income/Balance already had the 12-quarter 10-Q stitch
>   (`as_reported_statement_multiquarter`).
> - Securities Portfolio + Fair Value: `securities_multiquarter_for` /
>   `fair_value_multiquarter_for` (10-Q+10-K walk, 8 quarter-ends, same
>   reconcile gates; `_recent_filing_metas` generalizes the 10-K lister).
> - Financial Highlights / Performance / Credit Quality:
>   `_cr_highlights_by_year(quarterly=True)` — discrete quarters, returns
>   annualized ×4, NIM/NPL/NCO (10-K-only MD&A/rollforward) blank quarterly.
> - Regulatory Capital: `holdco_capital_quarterly_for` (full 8-quarter series).
> - Interest Rate Risk: quarterly marks vs equity AND CET1 every quarter.
> - Loan/Deposit Composition: `compositions_multiquarter_for` (per-filing
>   extraction accession-cached `compositions_filing:v1`).
> Known follow-ups: Q4 income cells blank where FY−9M isn't additive (by
> design); a 10-K's own label wording can split a category row in its Q4
> composition column (faithful-label union — only a GUARDED merge may ever
> change this); 10-Q label forms (hyphenated "non-interest", "(in USD per
> share)", "Available for sale debt securities…" word order) were added to
> the highlights-engine match lists 2026-07-14 — extend those lists, never
> loosen to fuzzy matching.

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

- [x] Locate the most-recent earnings 8-K and its **EX-99.1** financial
      supplement. SHIPPED in `data/sec_earnings_8k.py` — locates the EX-99.1 from
      the filing index's exhibit-TYPE table (not by filename guess).
- [x] Parse the supplement **defensively** — SHIPPED. Exact label match + per-
      figure sanity gate (see §6 for the feasibility results and the gate rules).
- [x] Surface the latest-quarter figures labeled **preliminary** — SHIPPED as a
      visually-distinct orange banner at the top of **Financial Highlights**
      (`_render_preliminary_quarter`), separate from the audited FY columns.
- [x] **Never overwrite an audited figure** — by construction: the banner is a
      separate block keyed off the 8-K, never written into the FY-column dicts.
- [x] Tests: `tests/test_sec_earnings_8k.py` pins the gate, incl. the mis-parse-
      rejected case (a segment subtotal that must NOT surface as the consolidated
      total) and the no-scale / out-of-band / bare-"Diluted" rejections.

> **Reconcile-to-10-Q nuance (as-built):** the layer is keyed off the *latest
> earnings 8-K*, and it is shown on the Financial Highlights tab whose audited
> table is FY-based. The banner therefore always shows the latest-released
> quarter regardless of whether a 10-Q for it exists yet; it never merges into,
> nor overwrites, the audited multi-year table. A "labeled-then-superseded-when-
> the-later-10-Q-differs" flow (auto-hide once the matching 10-Q lands) is a
> possible follow-up but was NOT built — the as-built design is strictly
> additive and cannot corrupt an audited figure, which satisfies the cardinal
> rule. Deferred, low risk.

### §6 — 8-K EX-99.1 feasibility (measured 2026-06-29)

Sample: **ABCB, PNFP, FFIN, CBSH, FHN, WAL, ONB, FITB, RF, KEY** (latest earnings
8-K each). **EX-99.1 located: 10/10** via the index exhibit-type table.

The releases are clean HTML tables (no XBRL) but layouts vary widely: small/mid
banks (ABCB/FFIN/ONB/CBSH) lead with a tidy 5-quarter table, latest quarter in
the first numeric column; large filers (KEY/FITB/FHN/WAL/PNFP) interleave percent-
change columns, GAAP/non-GAAP reconciliations and **segment tables that repeat the
same labels** with different values. Units split: ~half report $thousands, half
$millions. So broad label-matching is unsafe for big filers — the cardinal rule
demands a gate. **Strategy shipped:** exact label match → first numeric column;
detect the dollar scale ONCE by anchoring total assets/deposits to the prior
10-Q, apply it to all dollar figures; gate every figure (balance-sheet anchored to
the 10-Q ±30/40% — this REJECTS a segment subtotal; ratios 0–60; EPS |x|<100;
flows positive after scaling). Anything that fails → n/a, never a guess.

**Hit rate AFTER the gate (n=10), i.e. clean values shipped:**

| figure | ok | notes |
|---|---|---|
| `total_deposits` | 10/10 | anchored exact |
| `net_interest_income` | 10/10 | scaled by detected release scale |
| `net_income` | 9/10 | KEY n/a — its "Net income (loss) attributable to Key" label self-excluded (avoids the segment trap) |
| `total_assets` | 8/10 | KEY **rejected** a $37B segment subtotal (anchor $189B) → n/a; RF label-miss → n/a |
| `nim` | 7/10 | matches (TE)/(FTE)/(GAAP) variants |
| `roae` | 6–7/10 | label variant coverage |
| `roaa` | 6/10 | label variant coverage |
| `diluted_eps` | 6/10 | bare "Diluted" (= share count) deliberately excluded; only explicit per-share labels match |

**Read — production-ready?** The two **balance-sheet items are production-grade**
(cross-source anchored, proven exact). `net_income`, `net_interest_income`, the
ratios and EPS are **good and safe** (every shown value verified correct on the
sample; the gate rejected the one dangerous mis-parse) but their COVERAGE is
label-variant-limited — large filers with bespoke labels go n/a rather than wrong.
That is the correct trade under the cardinal rule. **Verified ABCB:** 8-K assets
$28.11B / deposits $22.64B tie the Q1-26 10-Q (2026-03-31) to the dollar; NI
$110.5M, EPS $1.63, NIM 3.88%, ROAA 1.62% are trend-plausible vs audited FY2025
(NI $412M, EPS $6.00, ROAA 1.53%). **Follow-up to lift coverage:** widen the label
sets (esp. EPS, ROAA/ROAE, net income for the "attributable to common" filers) and
add an anchored gate for `net_income`/`NII` (a prior-10Q single-quarter value) so
they're as hard as the balance-sheet items. Re-measure across the universe before
relying on the income/ratio rows broadly.

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

---

## 6. Universe coverage re-measure (47 banks, 2026-06-29)

Re-ran `tools/cr_coverage_report.py` over a **broader, more diverse sample** after
the statement-stitch, table-NIM, prose-NIM and KEY performance-status-nonaccrual
fixes shipped, to surface parser misses the original 24-bank sample hid. The
`SAMPLE` list in the tool was expanded to **53 tickers** spanning megacaps → small,
multiple regions/charters, and — critically — **non-December fiscal-year-end
filers** (AX = Jun-30; WAFD, CASH = Sep-30). Six tickers had no CIK from
`get_bank_info` and were skipped (**SNV, CMA, OZK, CADE, UCBI, PPBI**), leaving
**47 banks measured**. Each extractor call is try/except-wrapped — a per-bank
failure is recorded, never fatal. MEASURE + CLASSIFY only; **no scraper/renderer
edits this run** (the parser-miss backlog below is the next defect queue).

| function | n | multiyr | single | empty | error |
|---|---|---|---|---|---|
| `_cr_highlights_by_year` | 47 | 98% | 0% | 2% | 0% |
| `company_asset_quality_nim` | 47 | 100% | 0% | 0% | 0% |
| `as_reported_statement(income)` | 47 | 98% | 0% | 2% | 0% |
| `as_reported_statement(balance)` | 47 | 100% | 0% | 0% | 0% |
| `securities_multiyear_for` | 47 | 94% | 0% | 6% | 0% |
| `fair_value_multiyear_for` | 47 | 57% | 0% | 43% | 0% |
| `segments_multiyear_for` | 47 | 47% | 2% | 51% | 0% |
| `compositions_for` | 47 | 89% | 2% | 9% | 0% |

**ERROR banks (real bugs):** **none** — no extractor raised on any of the 47 banks.
The multi-year scrapers stay crash-clean at this breadth.

**Latest-year metric fill (per-metric functions):**

- `_cr_highlights_by_year`: latest-year fill **259/322 (80%)** across 46 non-empty
  banks (was 68% on the 24-bank run). Most-missing: efficiency(31), roaa(8),
  cet1(8), net_income(6), npl_loans(5), total_assets(3), **nim(2)**.
- `company_asset_quality_nim`: latest-year fill **125/141 (89%)** across 47 banks
  (was 62%). Most-missing: nco_loans(8), npl_loans(6), **nim(2)**.

**What the fixes cleared (vs the 24-bank run):** the NIM fixes WORKED — latest-year
NIM, the worst offender before (missing for 14/17 banks), is now missing for **just
2**. `company_asset_quality_nim` went 96%→**100%** non-empty. The statement-stitch
fix cleared **KEY** (previously empty on income, balance AND highlights — now
OK-multiyear on all three). Income/balance/highlights emptiness collapsed from
8/12/21% to **2/0/2%**.

### EMPTY classification — PARSER-MISS BACKLOG vs GENUINE NON-DISCLOSURE

Empties were spot-checked against the actual filings (FilingSummary R-files and raw
iXBRL facts). Full empty lists per function:
- highlights: PNC · income: PNC · balance: none · asset-quality-NIM: none
- securities: AX, WAFD, CASH
- fair_value (20): FFIN, WAL, CFR, ONB, UMBF, BOKF, RF, EWBC, HOMB, FHB, AX, FBP,
  INDB, WAFD, TCBI, SFNC, FBK, CASH, AUB, HBAN
- segments (24): PNFP, FFIN, WSFS, CBSH, ONB, HWC, VLY, COLB, GBCI, ZION, HOMB,
  BPOP, AX, NBHC, FBP, INDB, WAFD, BANR, TCBI, FBK, CASH, AUB, FULT, BKU
- compositions: MTB, BANR, FBK, CASH

#### PARSER-MISS BACKLOG (bugs to fix next — data IS in the filing)

1. **PNC income statement — ShortName word-order miss** (clears income + highlights).
   PNC names its income statement R-file **"Consolidated Income Statement"** (R3.htm,
   confirmed in FilingSummary). The matcher `_STMT_PATTERNS["income"]` =
   `statements?\s+of\s+(income|operations|earnings)` requires the "statement **of**
   income" word order and does NOT match "Income Statement". Its balance sheet
   ("Consolidated Balance Sheet") matches fine — which is exactly why PNC is empty on
   income+highlights but OK on balance. Fix: widen the income pattern to also match
   `income\s+statement`. Evidence: regex returns False on the literal ShortName
   "Consolidated Income Statement"; R3.htm exists and is the income statement.

2. **Non-December fiscal-year-end filers dropped by the `period[5:7]=="12"` FY-end
   filter** (securities, and contributes to fair-value/segments empties). The
   securities/fair-value/segments multi-year stitchers hard-filter
   `if period[5:7] != "12": continue` (sec_filing_scraper.py lines 1020, 787, 2114),
   i.e. **December year-ends only**. The extractors DO return the data:
   - **AX** (Axos, Jun-30 FYE): securities periods `2025-06-30, 2024-06-30` — both
     dropped by the `=="12"` filter → EMPTY.
   - **WAFD** (Sep-30): `2025-09-30, 2024-09-30` → dropped.
   - **CASH** (Pathward, Sep-30): `2025-09-30, 2024-09-30` → dropped.
   Fix: derive each filer's fiscal-year-end month from its 10-K cover/period instead
   of assuming December (the per-filing extractors already produce the right
   periods; only the multi-year FY-end gate is wrong). This is the cleanest,
   highest-value fix — it's a one-assumption bug affecting every off-cycle filer.

3. **Fair-value recurring hierarchy not tagged under the rollup concept the extractor
   keys on** (the largest empty bucket — 20/47, incl. large disclosers RF, EWBC,
   WAL, HBAN, ONB). `extract_fair_value` requires `Assets/Liabilities
   FairValueDisclosure[Recurring]` tagged with ONLY the hierarchy-level axis. Two
   sub-patterns found in the empties:
   - **RF**: tags `AssetsFairValueDisclosure` only as **nonrecurring** Level-3
     sub-rows (CommercialRealEstate $93M / ResidentialMortgage $970M, each carrying
     a `FinancialInstrumentAxis` member) — correctly rejected by `_fv_clean_total`;
     no clean recurring rollup under that concept.
   - **EWBC**: **zero** `AssetsFairValueDisclosure*` facts at all — tags the recurring
     hierarchy per-line (AFS securities, derivatives, …) sliced by the hierarchy
     axis, with no rollup concept.
   In both the recurring ASC 820 table IS disclosed in the 10-K; the extractor's
   concept list just doesn't reach it. This is the documented "filers tagging only
   per-instrument sub-rows (RF) … yield n/a" limitation (sec_filing_scraper.py:566).
   Recoverable but HARDER (needs a per-line-concept × hierarchy-axis reconstruction
   with a reconcile gate). Big coverage prize given the count.

4. **Segments — multi-segment banks whose per-segment profit is NOT tagged as
   NetIncomeLoss/ProfitLoss** (ZION, VLY confirmed; check the rest). `extract_segments`
   keys on per-segment `NetIncomeLoss`/`ProfitLoss` (≥2 segments). The new ASC 280
   disaggregated-expense disclosure makes some filers tag segment **revenue and
   expense lines** under the segment axis but NOT a segment net-income line:
   - **ZION**: 7 segment members (Amegy, California B&T, NBAZ, Nevada State Bank,
     Vectra, …) with per-segment Revenues / NoninterestIncome / InterestIncomeExpense
     Net / labor & occupancy expense tagged — but **0** per-segment NetIncomeLoss
     facts → EMPTY.
   - **VLY**: 3 segment members (Commercial Banking, Consumer Banking, Consumer
     Lending) with per-segment NII / noninterest income / expenses tagged — **0**
     per-segment NetIncomeLoss → EMPTY.
   The segment table IS disclosed; profit just isn't the tagged measure. Recoverable
   (sum tagged components under a reconcile gate) but moderate difficulty.

5. **Loan/deposit composition note-picker misses a clean by-type table** (BANR, FBK,
   CASH; MTB borderline). The filings disclose a by-type composition the
   `_note_rfile` picker didn't select:
   - **BANR**: has "LOANS RECEIVABLE … (**Loans by Type**) (Details)" and "DEPOSITS
     (**Deposit Liabilities**) (Details)" — both clean composition tables. "Loans by
     Type" doesn't match any `prefer` pattern (`composition of loan|loan portfolio|
     portfolio by|by loan class`), so the picker fell to a wrong/rejected sibling.
   - **FBK**: has "Schedule of **Loans Outstanding by Class** of Financing
     Receivable (Details)" (clean loan composition) — missed; deposit side genuinely
     only maturities.
   - **CASH**: has "LOANS AND LEASES, NET - **Summary of Loans** (Details)" — missed;
     deposit side only time-certificate maturities (genuine n/a on deposits).
   Fix: broaden the `prefer` synonym set ("by type", "loans by type", "summary of
   loans", "loans outstanding by class") for the loan note picker, re-gate.
   - **MTB**: borderline — its FilingSummary exposes only lease-related loan Details
     R-files and zero deposit Details; M&T's by-type loan composition likely lives in
     a differently-named/primary-note table. Needs a deeper look before classifying
     firmly; lean parser-miss (the data is certainly disclosed in a $200B+ filer).

#### GENUINE NON-DISCLOSURE (acceptable n/a — by design)

- **Single-segment banks** (segments empty, no segment axis tagged at all,
  confirmed `seg-axis members=0`): **PNFP, GBCI, BKU, BANR, HWC** (and, by the same
  pattern, the community/regional names among FFIN, WSFS, CBSH, ONB, COLB, HOMB,
  BPOP, NBHC, FBP, INDB, WAFD, TCBI, FBK, CASH, AUB — most run a single reportable
  segment). The extractor requires ≥2 reportable segments + a consolidated total;
  one-segment banks correctly render n/a. **FULT** is borderline-genuine — it tags a
  single aggregated `ReportableSegmentMember`, not ≥2 distinct named segments.
- **Deposit composition where only a maturity ladder is disclosed**: FBK and CASH
  disclose no by-type deposit-mix table (only time-deposit maturities) — correct n/a
  on the deposit side (their LOAN side is the parser miss in backlog item 5).

### Honest read — are parser misses eliminated at this breadth?

**Mostly, for the near-universal metrics; the wider net found two real new buckets.**
The fixes verifiably landed: income/balance/highlights are now ~98-100% multi-year
(KEY cleared), `company_asset_quality_nim` is 100% non-empty, and latest-year NIM —
the prior worst miss — is down to 2 banks. Those are essentially solved.

The broader, more diverse sample DID surface misses the 24-bank set hid, all
concentrated in three extractors:
1. **Non-December FYE filers** (AX/WAFD/CASH) — a clean, previously-invisible bug
   because the original 24 were all December filers. Highest-value, easiest fix.
2. **PNC income ShortName word-order** — only surfaced because PNC entered the
   sample; a one-line regex widening.
3. **Fair-value (43% empty) and segments (51% empty)** remain the two soft spots.
   Segments is *largely genuine* (single-segment banks) with a real recoverable
   tail (ZION/VLY-style component-only tagging). Fair-value is *mostly parser-miss*
   (the recurring table is disclosed but tagged per-line, not under the rollup
   concept) and is the biggest remaining coverage prize.

So: parser misses are **eliminated for the headline statement/highlights/NIM
metrics**, but **not** for fair-value and (the recoverable slice of) segments, plus
the two structural bugs (non-Dec FYE, PNC word-order) the wider net exposed. Those
five items are the next defect queue.

Re-run: `python -m tools.cr_coverage_report` (53-ticker `SAMPLE`; cached, fast).

### RESOLUTION (2026-06-29, sec_filing_scraper backlog items #2/#3/#4)

- **#2 non-December FYE — FIXED.** `securities_/fair_value_/segments_multiyear_for`
  no longer hard-filter `period[5:7]=="12"`; they derive the filer's real FY-end
  month from the filing's own annual-duration facts (`_fye_month_from_facts`, cached
  per accession via `_fye_month_for`) and gate on that. Verified live: AX returns
  securities at `2025-06-30…2021-06-30`; WAFD & CASH at `2025-09-30…2021-09-30`
  (AC/FV/net all sane — CASH AFS −$0.19B underwater). December filers (ABCB/CFG)
  unchanged. Pinned by `TestFyeMonth` + `test_non_december_fye_accepted` /
  `test_september_fye_accepted` in each multiyear test class.

- **#4 segments recoverable slice — FIXED (ZION 7-seg, VLY 2-seg).** When a filer
  tags NO per-segment NetIncomeLoss/ProfitLoss but DOES tag a reconciling
  per-segment dollar measure, `extract_segments` now surfaces the table on that
  measure (priority: pre-tax income → total revenue → net interest income),
  `ni_measure=None` + `disclosed_*` keys, clearly labelled (never relabelled net
  income). Only OperatingSegments leaf members enter the sum (`_seg_of`), so
  totals/eliminations can't double-count; the same residual-< consolidated gate
  applies. Verified live: ZION 7 segments on pre-tax income, Σ(1203M)+residual(−28M)
  = consolidated 1175M (delta 0); VLY 2 segments Σ(762M)+residual(−18M) = 744M
  (delta 0). The clean-OperatingSegments filter yields 2 (not 3) for VLY this year —
  honest. Single-segment banks (PNFP/GBCI/BKU/BANR/HWC) stay n/a. Renderer
  (`ui/financials_statements.py::_render_segments`) updated to title the band by the
  disclosed measure and pull residual/consolidated from the disclosed keys.

- **#3 fair-value per-line tagging — INVESTIGATED, LEFT n/a (genuine, by the
  cardinal rule).** Confirmed the empties split into two non-recoverable shapes:
  (a) RF/ONB tag `AssetsFairValueDisclosure` ONLY as nonrecurring per-instrument
  Level-3 sub-rows (RF: CRE $93M + ResidentialMortgage $970M with a
  FinancialInstrumentAxis member) — correctly rejected, no clean recurring rollup
  (already pinned by `test_instrument_only_rows_yield_na`); (b) HBAN/WAL/EWBC tag
  ZERO `Assets/LiabilitiesFairValueDisclosure` facts — the recurring hierarchy is
  tagged entirely per-line under individual class concepts. A per-line
  reconstruction is NOT shippable: there is **no recurring grand total tagged to
  tie against** (the plan's own "ship only when it ties" bar can't be met), the
  leaf set **mixes asset and liability concepts** and **mixes ASC 820 recurring
  with the ASC 825 fair-value-OF-financial-instruments disclosure** (loans/long-term
  debt — billions the extractor already guards against surfacing as recurring
  marks), and **leaf-vs-subtotal can't be distinguished without the presentation
  linkbase** (WAL: 0 clean single-axis leaf classes; HBAN: a naive single-axis sum
  wrongly folds in $1.16B long-term debt + $0.52B derivative liabilities). Any
  reconstruction would risk a double-counted / wrong-entity total → n/a is correct.
  So #3 is **mostly genuine non-recoverability** at the tie-or-n/a bar, not a parser
  miss we can safely close.
