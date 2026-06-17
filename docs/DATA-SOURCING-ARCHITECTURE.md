# Flexible Data Sourcing Architecture (user directive, 2026-06-14)

**Goal:** every displayed number sources from the FRESHEST available primary
source, updates as soon as the company discloses, and never depends on delayed
Fed aggregates. Flexible across sources — IR sites, SEC filings, FDIC.

## Freshness order (fastest → slowest)
1. **IR site / earnings release (8-K Item 2.02, Ex-99)** — ~2-3 weeks after
   quarter-end. FIRST disclosure. Semi-structured (HTML/PDF tables).
2. **SEC 10-Q / 10-K** — ~40-60 days. Structured: the filing HTML carries
   **inline XBRL (iXBRL)** with full dimensional context.
3. **FDIC SDI / FFIEC call report** — ~30 days, but AFTER the earnings release.
   Fast and clean for bank-subsidiary data.
4. **FR Y-9C bulk** — slowest (filing ~45d + Fed distribution lag). **NEVER
   depend on this** for displayed data.

## Source resolver (the flexible layer)
Per metric/dataset, an ordered list of **providers**; each returns
`(value, as_of, source, doc_link)` or `None`. The resolver takes the freshest
available; the chosen source + as-of date flow into the existing click-through
provenance. New providers plug in without touching the UI.

```
providers = [IRProvider, SECFilingProvider, FDICProvider]   # freshness order
value, asof, source, link = resolve(metric, ticker, providers)
```

## KEY technical decision: SCRAPE THE FILING DOCUMENTS
The flat SEC **companyfacts API drops XBRL dimensions**, so dimensional data
(regulatory capital, credit-quality grades, fair-value levels, as-reported loan
comp) is unavailable or ambiguous there — a concept can carry consolidated +
bank + required values with no way to tell them apart. So we **scrape the
filing document** (10-K/10-Q HTML) and parse its **inline XBRL**, which keeps
the dimensional context. IR earnings-release tables are parsed similarly.

**Proven (2026-06-14):** scraping Regions' FY2025 10-K (`rf-20251231.htm`)
yields Tier 1 ratio 11.99%, Total capital ratio 13.89%, Tier 1 capital
$14,859M, leverage 9.68% — EXACT to the SNL screenshot. Values are iXBRL-tagged
in the document.

## First application: Capital Adequacy (holdco)
SNL's Capital Adequacy is holding-company basis. Source it from the SEC filing
(timely, holdco, structured), NOT FR Y-9C (delayed Fed). The existing bank-sub
RC-R walk stays as a labeled complement.

## Build increments (each verified vs Regions/Banner before wiring)
> **STATUS RECONCILED 2026-06-17** — verified against `data/sec_filing_scraper.py`,
> `data/sec_composition.py`, `ui/company_nav.py`. Increments 1, 2, and 6 are DONE and
> live across the full Company Reported tab set; 3 is PARTIAL; 5 is NOT STARTED.

1. **SECFilingProvider core** — ✅ DONE. `latest_filing()` + `instance_facts()` +
   `parse_inline_xbrl[_documentset]()` in `data/sec_filing_scraper.py`; handles
   multi-document mega-filers (USB/WFC/TFC). Used by every downstream extractor.
2. **Capital-table extractor** — ✅ DONE. `extract_holdco_capital()` /
   `_build_capital_walk()`; FDIC-CET1-anchored, walk rendered only when it reconciles.
3. **Source resolver + provenance** — 🔧 PARTIAL. Provenance (source + as-of +
   doc-link) IS surfaced per tab, but there is **no unified `resolve()` freshest-wins
   layer**: holdco (SEC) shows in Company Reported → Regulatory Capital, bank-sub
   (FDIC) in Templated → Capital Adequacy — two tabs, not one merged view. Remaining
   work = the resolver refactor + IRProvider (below).
4. **Capital Adequacy tab** — ✅ DONE. `_render_holdco_capital()` (highlights block +
   walk, n/a when not reconciling, LCR/HQLA n/a w/ note) wired in company_nav.
5. **IRProvider** — ❌ NOT STARTED. No 8-K Item 2.02 / Ex-99 earnings-release table
   parser. 8-K infra exists for the news feed only. SEC 10-K/10-Q is currently the
   freshest active source; this increment would add the ~2-3-week-earlier layer.
6. **Generalize** — ✅ DONE. iXBRL/document scraper unlocked the dimensional tabs:
   credit quality / criticized-classified (XBRL grades), fair value (ASC 820 L1/L2/L3
   with ASC 825-disclosure guard), as-reported loan + deposit composition
   (`sec_composition.py`), performance, segments, rate risk — all reconcile-gated.

## Non-negotiables
- Verify every scraped value against the actual filing for a known bank
  (Regions/Banner) before shipping — see [[derived-metric-sourcing-not-math]].
- Prefer n/a + provenance over a guess.
- Multi-bank robustness: filing layouts vary; the iXBRL tag/member match must be
  tolerant, and a bank whose filing can't be parsed renders n/a, never wrong.
