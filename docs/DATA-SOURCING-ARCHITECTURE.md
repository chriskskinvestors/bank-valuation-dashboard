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
> **STATUS RECONCILED 2026-08-20** (supersedes 2026-08-04) — ALL SIX increments
> closed: 1/2/4/5/6 DONE, 3 done-as-evolved (freshest-wins layer + basis split;
> see its entry). This build plan is COMPLETE.

1. **SECFilingProvider core** — ✅ DONE. `latest_filing()` + `instance_facts()` +
   `parse_inline_xbrl[_documentset]()` in `data/sec_filing_scraper.py`; handles
   multi-document mega-filers (USB/WFC/TFC). Used by every downstream extractor.
2. **Capital-table extractor** — ✅ DONE. `extract_holdco_capital()` /
   `_build_capital_walk()`; FDIC-CET1-anchored, walk rendered only when it reconciles.
3. **Source resolver + provenance** — ✅ DONE-AS-EVOLVED (closed 2026-08-20).
   The increment's substance ships: provenance (source + as-of + doc-link) per
   tab, and a freshest-wins layer in data/ir_provider — fresh_capital feeds
   Regulatory Capital (ui/capital_dynamics.py) and fresh_diluted_eps feeds
   Performance, each shown ONLY when the release quarter is fresher than the
   latest periodic filing. The original "one merged holdco+bank-sub view" is
   SUPERSEDED by the deliberate Templated / Company-Reported basis split the
   whole Financials section is built on (holdco-SEC and bank-sub-FDIC are
   different entities — merging them into one column risks the wrong-entity
   joins data/validation.py exists to catch). No remaining work.
4. **Capital Adequacy tab** — ✅ DONE. `_render_holdco_capital()` (highlights block +
   walk, n/a when not reconciling, LCR/HQLA n/a w/ note) wired in company_nav.
5. **IRProvider** — ✅ DONE (verified 2026-08-04; this entry was stale). The 8-K
   EX-99.1 earnings-release layer SHIPPED as `data/sec_earnings_8k.py` (exhibit
   located from the filing index's TYPE table; exact-label match; dollar scale
   anchored to the prior 10-Q; per-figure sanity gate — n/a over a guess) plus
   `data/ir_provider.py` (release/call/webcast discovery, `fresh_diluted_eps`).
   Surfaced as the clearly-labeled PRELIMINARY banner on Financial Highlights
   and the preliminary-EPS note on Performance — additive only, never merged
   into audited columns. See COMPANY-REPORTED-PLAN Phase 4 + §6 for the
   measured hit rates and gate rules.
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

---

## Ops runbook: NIC / FR Y-9C mirror refresh (manual, dev-box only)

The Corporate Structure sub-tab (`data/nic_client.py`) and the Regulatory
Filings Y-9C download (`fetch_y9c_pdf`) source from the Fed NIC / NPW site
(`ffiec.gov/npw`). NPW sits behind Cloudflare bot management that **403s Cloud
Run egress AND GitHub-hosted-runner egress outright** (curl included, proven
2026-07-14: prod logs + `refresh-nic-bulk` run 29357551545 — every fetch
403'd, first request from a fresh runner IP). There is no unblocked egress in
our cloud, so **production and CI can never download from NPW.**

**Architecture.** Prod reads two GCS mirrors under `gs://ksk-bank-dashboard-data/`:
- `nic_bulk/{attributes_active,relationships}.zip` — the org-hierarchy bulk
  files (`nic_client._bulk_path` ladder: local `/tmp` → GCS mirror → NPW
  direct → stale copies; NPW leg only ever succeeds off-cloud).
- `y9c/{rssd}_{yyyymmdd}.pdf` — latest-quarter FR Y-9C facsimiles
  (`fetch_y9c_pdf`; on Cloud Run it never falls through to NPW — per-click
  403s burn the per-IP bot score).

Both serve the **stale** mirror indefinitely when a refresh is overdue, so a
late refresh degrades gracefully and is never an outage.

**Refresh procedure** — run from the dev box (the only network proven
unblocked; back-to-back NPW hits trip bot scoring, so the tools space fetches
≥30s and retry a 403 once):

```powershell
# gcloud on this box is PowerShell-blocked as gcloud.ps1 — use gcloud.cmd.
# ADC (not the gcloud CLI creds) is what the Python GCS client uses; refresh
# it interactively when it expires (Workspace reauth blocks non-interactive):
gcloud.cmd auth application-default login
$env:GCS_BUCKET = 'ksk-bank-dashboard-data'
python -m tools.refresh_nic_bulk     # ~2 zips, seconds
python -m tools.refresh_y9c_mirror   # ~370 PDFs first run of a quarter (~3h); later runs skip mirrored, minutes
```

Both tools **validate before uploading** (bulk: CSV header vs the exact
columns the parser needs; Y-9C: `%PDF-` magic), so a Cloudflare challenge page
or truncated fetch can never clobber a good mirror object.

**Cadence.** Org structure and Y-9C filings change slowly — refresh monthly,
plus once ~mid-Feb/May/Aug/Nov (just after each Y-9C filing deadline) to pick
up the new quarter. `.github/workflows/refresh-nic-bulk.yml` is kept
**`workflow_dispatch`-only** (its scheduled crons were removed — they would
only spam red failures since the runner can't fetch NPW); it exists so that
IF Cloudflare ever unblocks GitHub egress, re-adding a `schedule:` block
restores automation in one line.

**Verify after a refresh.** The workflow's last step deploys+executes the
`nic-mirror-verify` Cloud Run job (live service image, wiped `/tmp`) which
must serve+parse both bulk files AND one Y-9C from the mirrors
(`tools/verify_nic_mirror.py`) — this step does **not** touch NPW, so it runs
fine from CI or via `gcloud.cmd run jobs execute nic-mirror-verify --wait`.
Locally you can prove the bulk read path with an empty `/tmp` against the real
mirror (ADC): wipe `nic_client._BULK_DIR`, call `_bulk_path(...)`, and build a
known tree — Banner Corp (RSSD 2126977) should render holdco → statutory
trusts + Banner Bank → subsidiaries.
