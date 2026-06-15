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
1. **SECFilingProvider core** — given a CIK: locate latest 10-K/10-Q, fetch the
   primary doc, parse iXBRL facts (value + unit + scale + dimensional members),
   return a clean fact map. Verify capital facts == SNL.
2. **Capital-table extractor** — map the iXBRL facts to CET1/T1/T2/Total/RWA +
   the four ratios + the capital walk; pick the consolidated (holdco) member.
3. **Source resolver + provenance** — wire SEC provider into a resolver; FDIC
   bank-sub provider as labeled complement; as-of + doc-link in click-through.
4. **Capital Adequacy tab** — render SNL's highlights block + holdco walk from
   the resolver; LCR/HQLA items n/a with provenance note.
5. **IRProvider** — earnings-release (8-K Ex-99) table parse as the freshest
   layer, added once the SEC path is solid.
6. **Generalize** — the same iXBRL/document scraper unlocks the other
   dimensional tabs (criticized loans, fair value, as-reported loan comp).

## Non-negotiables
- Verify every scraped value against the actual filing for a known bank
  (Regions/Banner) before shipping — see [[derived-metric-sourcing-not-math]].
- Prefer n/a + provenance over a guess.
- Multi-bank robustness: filing layouts vary; the iXBRL tag/member match must be
  tolerant, and a bank whose filing can't be parsed renders n/a, never wrong.
