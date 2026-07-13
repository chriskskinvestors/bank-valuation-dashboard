# SNL-Depth Tables — Build Plan (from BANR screenshots, 2026-06-12)

User direction: **keep all existing graphics; add tables at SNL depth; where
SNL shows NA but regulatory data exists, fill it — our data should be better
than theirs.** Confirm mapping per tab before building (process from the
Performance Analysis build).

Sources legend: **FDIC** = SDI financials field (probed live against BANR
cert 28489); **calc** = computed in the statement engine (computed kinds);
**RC-x/RI** = FFIEC bulk schedule parse (downloader infra exists; new
schedule parser needed); **10-K** = company-disclosure only (no regulatory
source); **n/a** = honest gap, shown as such.

> **STATUS RECONCILED 2026-06-17 (supersedes the "one tab at a time" framing below).**
> The original plan tracked a single FDIC-sourced "Templated" build, ~1 of 9 tabs done.
> Reality: the Financials section now ships **two parallel bases** for nearly every tab —
> **Templated** (FDIC/FFIEC regulatory) and **Company Reported** (faithful per-bank SEC
> 10-K/10-Q iXBRL extraction, reconcile-gated). Verified against `ui/company_nav.py`
> (renderer registry) + `data/sec_filing_scraper.py` / `data/sec_composition.py`:
> - **BUILT (both bases):** Income Statement, Balance Sheet, Performance Analysis,
>   Capital Adequacy (Templated) / Regulatory Capital holdco walk (Company Reported),
>   Asset Quality Detail, Deposit/Loan Composition, Interest Rate Risk.
> - **BUILT (Templated-only or Company-Reported-only by design):** Asset Quality by
>   Loan Type (FDIC RC-N), Fair Value Analysis (Company Reported, ASC 820 L1/L2/L3 with
>   ASC 825-disclosure guard), Securities Portfolio, Segment Reporting.
> - **DECIDED 2026-07-09 (owner):** (a) criticized/classified stays **XBRL-only** — the
>   MD&A HTML parser is DEFERRED indefinitely (fragile per-bank HTML for incremental
>   coverage; revisit only if coverage gaps bite). (b) Interest Rate Risk source =
>   **phased-NIM model** (built, backtested, FFIEC-ladder-driven; consistent across the
>   universe) — no MD&A rate-shock scrape. Both plan questions are CLOSED.
> - **GENUINELY OPEN:** (c) expanded **Overview** (only Corporate Profile built),
>   expanded **Market Analysis** (only Market Share & Branches), expanded **Ownership**
>   (13F + Insider only), and a top-level **Transactions** tab (not in nav) — all deferred
>   pending owner content decisions; (d) **Census client** needs `CENSUS_API_KEY` (user
>   signup). The per-tab line-item tables below remain the reference for field mapping;
>   treat their build status as "shipped unless listed under GENUINELY OPEN."

## 1. Income Statement
| SNL line | Source |
|---|---|
| Interest income / expense / NII | FDIC INTINC, EINTEXP, calc diff |
| FTE NII | calc: NII + tax-equiv adjustment (RI Mem / statutory-rate estimate) — flag method |
| Provision for credit losses (total) | FDIC ELNATR |
| Provision split (loans vs unfunded) | RI / RI-B detail |
| Trading income | FDIC TRADE |
| Trust revenue | FDIC IFIDUC |
| Service charges on deposits | FDIC ISERCHG |
| Gain on sale of loans | RI (RIAD5416) — not in SDI |
| Loan fees & charges | 10-K (embedded in interest income per call report) |
| BOLI income | RI (RIADC014/C016) — not in SDI |
| Insurance revenue | FDIC IINSOTH |
| Investment banking & brokerage | FDIC IINVFEE |
| Other noninterest income / total | FDIC IOTHII, NONII |
| Realized gain on securities | FDIC IGLSEC |
| Nonrecurring rev/exp | n/a (SNL analyst classification, no regulatory line) |
| Comp & benefits | FDIC ESAL |
| Occupancy & equipment | FDIC EPREMAGG |
| Marketing / Professional / Tech & comms / Foreclosure | RI-E memoranda items |
| Amort intangibles (+ goodwill impair) | FDIC EAMINTAN (+ RI RIADC216) |
| Other expense / total NIE | FDIC EOTHNINT, NONIX |
| PPNR / Non-FTE PPNR | calc: NII(+FTE) + NONII − NONIX |
| Pre-tax NI / taxes / eff rate | FDIC PTAXNETINC, ITAX, calc ratio |
| Minority int / extraordinary | FDIC NETIMIN, EXTRA |
| Net income → avail to common | FDIC NETINC; pref divs from RI-A; calc chain |

## 2. Balance Sheet
| SNL line | Source |
|---|---|
| Cash & due / int-bearing deposits at FIs | FDIC CHBAL, CHBALI, calc split |
| Fed funds sold / resell | FDIC FREPO (combined; split = RC-B/RC) |
| Trading securities | RC (RCFD3545) — not in SDI probe |
| AFS / HTM / other securities | FDIC SCAF, SC−SCAF calc (verify SCHTM* exact name during build), SCEQ |
| Gross loans HFI / LLR / HFS / net | FDIC LNLSGR, LNATRES, (HFS: RC RCFD5369), LNLSNET |
| REO | FDIC ORE |
| Goodwill / CDI / other intangibles | FDIC INTANGW; CDI split = RC-M; other = calc INTAN−INTANGW−MSA |
| Servicing rights | FDIC MSA / INTANMSR |
| Fixed assets | FDIC BKPREM |
| Interest receivable / prepaid / other assets | RC-F (other assets detail) |
| BOLI asset | RC-F (RCFDK201) |
| Total assets | FDIC ASSET |
| Deposits / FHLB / senior / sub debt / TruPS | FDIC DEP, OTHBFHLB, SUBND; TruPS = RC-M |
| Other liabilities / total | FDIC calc, LIAB |
| Preferred / common equity / NCI / AOCI | FDIC EQPP, calc, (NCI rare), EQUPTOT |
| Total equity | FDIC EQTOT |

## 3. Capital Adequacy
| SNL line | Source |
|---|---|
| CET1 / T1 / T2 / Total capital ($) | FDIC RBCT1J, RBCT1, RBCT2, RBC |
| RWA | FDIC RWAJ |
| CET1 / T1 / Total / Leverage ratios | FDIC RBCRWAJ, RBC1RWAJ, calc RBC/RWAJ, RBC1AAJ |
| T1 component walk (intangibles, AOCI, DTA adj) | RC-R Part I parse |
| T2 components (hybrid/sub debt, reserves) | RC-R Part I parse |
| LCR/HQLA | n/a for community banks (large-bank only; SNL also NA) |

## 4. Asset Quality Detail
| SNL line | Source |
|---|---|
| Nonaccrual / restructured / NPLs | FDIC NCLNLS, restructured (RSLNLTOT?) — verify field at build |
| OREO (net) / NPAs / 90+PD accruing | FDIC ORE, calc, P9LNLS-equivalent (verify) |
| LLR / NCOs / all the ratios | FDIC LNATRES, NTLNLS, calc ratios (engine has most) |
| Criticized/classified (pass/SM/substandard/doubtful) | 10-K/10-Q credit-quality footnote (FinancingReceivableCreditQualityIndicator dimensional XBRL) — see Decisions; the dimensional filing parser sources this |

## 5. Asset Quality by Loan Type  ← we BEAT SNL here
30-89 PD, 90+ PD, Nonaccrual × loan category (1-4 fam, multifam, CRE, C&D,
HELOC, cards, other consumer, C&I, other): **RC-N parse** gives the full
matrix SNL shows as NA. FDIC SDI may expose partial P3*/P9*/NA* category
fields — probe at build; RC-N is the complete source.

## 6. Deposit/Loan Composition
Almost entirely FDIC now: loan mix (LNRENRES/LNREMULT/LNRECONS/LNRERES/
LNCI/consumer fields), % of gross loans calc, growth rows calc; deposit mix
(transaction/savings/MMDA/time/jumbo/brokered/core/NIB fields — engine
already uses several). Largely an extension of the existing tab's table.

## 7. Interest Rate Risk
SNL shows each bank's OWN 10-K MD&A rate-shock disclosure (NII & EVE,
immediate + gradual). Not in XBRL → unreliable to scrape. Our platform has a
MODEL-driven equivalent (phased NIM scenarios). Options decided with user.

## 8. Fair Value Analysis
| SNL block | Source |
|---|---|
| FAS 157 Level 1/2/3 assets & liabilities | RC-Q parse (banks >$1B file it) |
| HTM securities fair vs carrying | RC-B (fair value columns) — partially have via securities work |
| Fair vs carrying: loans, deposits, debt (ASC 825 table) | 10-K footnote; dimensional XBRL (companyfacts API is flat — needs frames API or filing-instance parse) |
| MTM equity adj / MTM BV / MTM EPS | calc from the above once sourced |

## 9. Loan Composition (As Reported)
The bank's OWN 10-K/10-Q portfolio table (e.g. BANR: CRE owner-occupied /
investment / small-balance, multifamily, construction splits, ag, 1-4 fam,
consumer HELOC/other). Source: dimensional company XBRL — member names vary
per issuer (custom taxonomy extensions), so this needs a per-filing
instance parse with a tolerant member matcher, NOT companyfacts. Distinct
from tab 6 (regulatory composition, standardized categories, buildable now).

## 10. Recent Documents (News & Filings → Filings upgrade)
SNL's categorized document hub. Sources:
| SNL panel | Source |
|---|---|
| Annuals/Interims (10-K/10-Q/AR + earnings releases) | EDGAR (have) + 8-K earnings exhibits |
| Current Reports (8-K, press releases) | EDGAR (have) + wire feeds (have) |
| Proxies (DEF/DEFA/PRE 14A) | EDGAR (have) |
| Merger Documents (S-4, merger 8-Ks) | EDGAR (have) |
| Prospectus (424B, S-1/S-3) | EDGAR (have) |
| Key Exhibits (EX-21 subsidiaries, cap-stock descriptions, comp plans) | EDGAR filing-index exhibit parse (new) |
| Transcripts & Presentations | DECIDED (user 2026-06-12): free sources for now — Presentations from 8-K EX-99 (have) + link-outs to free transcript sources per call; FMP full-text transcripts re-verified IN-PLAN on the current Premium key (2026-06-24) and BUILT — quarter picker over earning-call-transcript-dates + full content via earning-call-transcript (ui/transcripts.py, data/fmp_transcripts.py); the old "needs Ultimate (+$127/mo)" note is stale. Analyst Coverage + Compensation verified IN-PLAN on FMP Starter (price targets, grades, ratings, executive-compensation w/ DEF 14A links) — build on FMP now |
Layout (user, 2026-06-12): five SUB-TABS under News & Filings —
"Filings & Reports", "Key Exhibits", "Press Releases", "Transcripts &
Investor Presentations", "Events Calendar" — added via COMPANY_NAV +
the renderer registry in ui/company_nav.py (structural test keeps them
in sync). Existing Filings/Activity content folds into the new structure;
keep the form-type filter inside Filings & Reports.

## IS tab — MDRM codes CONFIRMED by value-matching Banner Bank 12/31/2025
## call report against the SNL FY-2025 screenshot (tools/probe_ri_codes.py):
- RIADC014 = BOLI income (exact holdco match 10,152)
- RIAD4230 = provision: loans (11,637); RIADJJ33 = provision total (13,045);
  unfunded split = JJ33 − 4230 = 1,408 exact
- RIAD4080 = service charges (25,433); RIAD4135 = comp & benefits (243,487)
- RIADC232 = amort intang & GW impair (1,567); RIADC216 = GW impair alone
- RIADC887 = insurance rev (763); RIADC886 + RIADC888 = inv banking +
  brokerage (732+1,101 = 1,833 exact)
- RIAD5416 = gain on sale of loans — bank-sub 11,491 vs holdco 9,108:
  STRUCTURAL holdco-vs-sub gap, label provenance accordingly
- RIADC017 = data processing (30,787; SNL "tech & comms" 33,067 adds telecom)
- Bank-sub totals (RIAD4107/4073/4340) differ from holdco top lines by
  design — the table is regulatory (sub) with click-through provenance;
  holdco totals available from SEC side.
- FTE adjustment + RI-E write-ins (marketing/professional/foreclosure):
  derive at build (RIAD4313/4507 tax-exempt income probed; formula TBD).

## 11. Market Analysis section sub-tabs (user, 2026-06-12)
SNL nav: Branch List · Branch & Mortgage Map · U.S. Branch Competitors ·
U.S. Branch Proximity · U.S. Market Demographics · U.S. Deposit Market
Share · Residential & Commercial Mortgage Analytics · HMDA Mortgages ·
U.S. Branch Analytics (Merger Planning/HHI, Market Share, Market Overlap).
| Sub-tab | Source |
|---|---|
| Branch List / Map / Competitors / Proximity | FDIC SOD store (HAVE: branches table + geographic view) — geoqueries |
| Deposit Market Share | SOD (HAVE: market share view) — extend to county/MSA tables |
| Merger Planning / HHI / Market Overlap | calc on SOD deposits (deposit HHI per market, pro-forma overlap) |
| Market Demographics | Census API (new client) |
| HMDA Mortgages / Mortgage Analytics | CFPB HMDA public API (new client) |
Nav via COMPANY_NAV + registry. Existing "Market Share & Branches" content
splits into the new sub-tabs.

## 12. Overview section sub-tabs (user, 2026-06-12)
SNL nav: Corporate Profile · Stock Chart · Corporate Structure · ~~Long
Business Description~~ (SKIP, user) · Corporate Governance · People
Summary · Analyst Coverage · Compensation.
| Sub-tab | Source / decision |
|---|---|
| Corporate Profile | our current Overview page re-homed unchanged |
| Stock Chart | ✅ SHIPPED 2026-07-11 (ui/stock_chart.py): price+volume subplot, peer multiselect (nearest-by-assets first from peer_cohort) switching the price pane to indexed-% comparison, period stats ledger; KEEP BOTH honored — Price & Trends unchanged under Valuation |
| Corporate Structure | ✅ SHIPPED 2026-07-11 (ui/corporate_structure.py on the pre-built data/nic_client): FDIC cert→FED_RSSD (new fdic_client.get_rssd_for_cert, 30d cache) → climb to top holder (cycle-safe) → full NIC tree with ownership %/control, subject bank highlighted, EX-21 cross-check pointer to Key Exhibits. NIC bulk fetch needs curl (python TLS 403s — client falls back automatically; curl is in the image). Per-RSSD trees cache 30d; first view per instance downloads ~9MB bulk (spinner). Pre-warm job = later lever if latency bites |
| Corporate Governance | ✅ SHIPPED 2026-07-13 (ui/corporate_governance.py + data/governance.py + data/state_corp_law.py): (1) charter/bylaw provisions from the latest DEF 14A via the summarizer with the EVIDENCE-QUOTE guard (a status renders only when its verbatim supporting quote verifies against the filing; silence → n/a; accession-keyed permanent cache, API failure uncached); (2) curated citation-first state-law reference (21 states: BC/control-share/fair-price statutes + cumulative-voting default, every asserted statute cites its section, uncurated states say so honestly, "not legal advice" caption); (3) fixed federal banking control overlay (CIBC 10%, BHCA 25%/5%, Riegle-Neal 10% deposit cap). Live extraction prod-only (no local key) |
| People Summary | ✅ SHIPPED 2026-07-13 (ui/people_summary.py + data/people.py): directors & officers extracted from the latest DEF 14A via the Claude summarizer (guarded: surnames verified verbatim in the filing, ages 21-100, years range-checked, nulls never inferred; accession-keyed permanent cache in cloud storage — one call per bank per proxy season, on first view) + Section 16 activity roster from the existing Form 4 cache. Labeled AI-extracted + source-linked per the 2026-06-12 approval. API-failure path returns None uncached (retries next view); prod-only live verification (no local ANTHROPIC key) |
| Analyst Coverage | ✅ SHIPPED 2026-07-11 (ui/analyst_coverage.py): FMP price-target consensus + windowed summary + grade actions + composite rating (caveated as generic model) + compact yfinance street-consensus block; honest empty state for uncovered banks |
| Compensation | ✅ SHIPPED 2026-07-11: merged page under Overview — NEO Summary Comp Table (moved from Ownership, was "Executive Compensation") + Pay-versus-Performance from proxy inline XBRL (data/sec_pvp.py: ecd taxonomy via slimmed companyfacts, newest-proxy-wins per year, multi-PEO years kept verbatim, net-income ladder NetIncomeLoss→ProfitLoss). CEO pay ratio NOT tagged in ecd XBRL — deliberately omitted (a text parse would be per-bank fragile; revisit only on ask). Some large filers (USB) have no ecd in companyfacts → honest empty note |

Census client (data/census_client.py) built for tab 11 demographics —
NEEDS CENSUS_API_KEY (free signup api.census.gov/data/key_signup.html;
user action) in env + Secret Manager + deploy.yml secrets list.

## 13. Ownership section sub-tabs (CONFIRMED via BANR screenshots 2026-06-12)
SNL nav: Ownership Summary · Ownership Detailed · Ownership History ·
Ownership Crossholdings (still name-only) · Insider Activity.
| Page element | Source |
|---|---|
| Summary: type breakdown (Institutions/Insiders/State/Public × shares, %CSO, mkt val) + pie | 13F agg (HAVE) + Form 3/4 insider holdings + calc residual |
| Summary: Float Summary walk (insider + state + untraded → excluded → free float → float %) | calc from the above; click-through formula |
| Summary view sub-tabs (Top Holders, Top MF Holders, Owner Type, Country, Style, Mkt Cap, Turnover, Top Buyers/Sellers, Activity) | 13F agg + holder-metadata analytics (below) |
| Detailed: holder table (shares, %CSO, mkt val, Δshares/%, position date, source, turnover cat+%, orientation Active/Passive, equity assets, city/state, style, cap emphasis) — 370 holders | ✅ PHASE 1 SHIPPED 2026-07-13: "Detailed" sub-tab (facts-only columns: shares, QoQ Δ+% vs stored snapshot incl. New, %CSO, current mkt value, 13F-reported value, filed date, EDGAR filing links; guards render n/a on missing shares-out/price/prior-snapshot). Style/turnover/orientation/equity-assets = phase 2 (needs per-holder FULL 13F book + N-PORT) |
| History: holder × quarter matrix, 5y, expandable (shares, %CSO, mkt val, Δ, %Δ) | ✅ PHASE 1 SHIPPED 2026-07-10: "Holder History" sub-tab (holder × quarter shares matrix from the quarterly snapshots + QoQ Top Buyers/Sellers incl. New/Exited, honest sample caveats). ✅ EDGAR BACKFILL SHIPPED 2026-07-13: backfill_quarter searches a past quarter's own filing season (quarter-end+1d..+75d) and persists via the merge-only writer (stored quarters never clobbered; empty results not persisted so retries stay possible); fire per quarter with `gcloud.cmd run jobs execute refresh-13f --region=us-central1 --args="-m,jobs.refresh_13f,--backfill,2026Q1"` (one quarter ≈ one full pass; 5400s timeout fits one, maybe two). reported-value matrix view SHIPPED 2026-07-13 (Shares | Reported value toggle straight from stored snapshots); %CSO column remains phase 2 (needs per-quarter historical shares outstanding) |
| Insider Activity: volume/price graph w/ buy-sell markers, 3M/1Y/5Y aggregates (value bought/sold, buyers:sellers), full Form 4 table w/ filing links | Form 4 ingest (HAVE) + price store (HAVE) — computation + layout |
Crossholdings ✅ SHIPPED 2026-07-10 as designed — inferred cross-join of stored
13F quarter snapshots ("Crossholdings" sub-tab: subject's top holders × other
banks each also holds, coverage/sample caveats explicit; coverage grows with
stored snapshots — a universe-wide 13F warm job is a later lever).
Plus 13D/G for activist panel.

**DECIDED 2026-07-13 (owner):** (a) Transactions = the §14 five-sub-tab SNL
structure, with the existing universe insider feed KEPT as its own sub-tab
(nothing removed); (b) Ownership Summary (type breakdown + float walk) is
HELD for phase 2 — don't ship sample-floor institution percentages; revisit
after EDGAR 13F backfill makes coverage census-grade.

## 14. Transactions section (NEW top-level tab; user, 2026-06-12)
SNL nav: Transactions Summary · Detailed M&A History · Detailed Offerings ·
Private Equity Transactions · Comparable Deal Analysis.
| Sub-tab | Source |
|---|---|
| Transactions Summary (CONFIRMED via screenshots) | Aggregate/Details toggle; transaction volume chart (count line + value bars, multi-decade); Top Transactions by Value table (announce/completion dates, target, buyer, type, $M); transaction-type pie (M&A / ECM / DCM / Shelf / Buyback counts); buyback announcement feed. ~~Top Advisers panels~~ SKIPPED (user X). Type classification = our own from filing type + PR text |
| Detailed M&A History (CONFIRMED) | deal table: announce + completion dates, target, buyer, seller, value $M, acquisition vs sale, type (whole company / asset-or-branch), TARGET TOTAL ASSETS at announcement (computable from FDIC — incl. branch deals + terminated/withdrawn). Sources: FDIC structure events (completed) + events-store M&A detection + 8-K/S-4/PR (announcements, values, terminations). **FDIC completed-deals leg BUILT 2026-07-13** (`data/ma_history.py` `get_ma_history(cert)`): whole-company deals both directions (810/811/812 + terminal 2xx) with target total assets at last REPDTE ≤ completion ($k→raw dollars at boundary; non-SDI targets n/a), branch-package purchases AND sales with exact branch counts (712 header + 722 office rows — both recorded on the BUYER's cert; sales recovered via `OUT_CERT:{cert}` reverse query; taxonomy verified live: 713 = whole-bank echo, not a deal). Value-verified: Umpqua/Columbia 2023 ($20,258,988k at 2022-12-31), Banner↔Umpqua 2014 six-branch divestiture from both certs, Banner's four SNL-screenshot deals. NEXT: announcements leg (announce date, value $M, terminated/withdrawn from events store + 8-K/S-4/PR), then the UI sub-tab |
| Detailed Offerings | EDGAR S-1/S-3/424B prospectuses + capital-raise 8-Ks (have filings ingestion) |
| Private Equity Transactions | 13D/G + private-placement 8-Ks — public coverage is thin; honest sparse table |
| Comparable Deal Analysis | COMPUTED deal comps: announced bank M&A (price from 8-K/PR) ÷ target financials at announcement (FDIC/SEC) → P/TBV paid, premium, deal-size multiples across our universe — a beat-SNL analytic |

**Process note (user question, 2026-06-12):** for sections where only nav
NAMES were supplied (Ownership, parts of Overview/Transactions), page
contents are INFERRED from SNL conventions — marked as assumptions. At
build time each tab gets either a content screenshot from the user (the
Financials process) or a proposed layout confirmed before building.

## Decisions (user, 2026-06-12)
- **One tab END-TO-END at a time**, screenshot order: IS → BS → Capital →
  AQ Detail → AQ by Loan Type → Composition → IRR → FV → Loan Comp (As Rptd).
- **IRR**: OUR phased-NIM model rendered in SNL's table layout, labeled
  model-derived (10-K MD&A tables not scraped).
- **Source missing data ourselves**: criticized/classified loans come from
  10-K/10-Q credit-quality footnotes (FinancingReceivableCreditQualityIndicator
  dimensional XBRL) — build the dimensional filing parser (also unlocks FV
  ASC-825 + As-Reported loan comp). "Nonrecurring" = our own one-time-item
  classification from the normalization engine, labeled as ours. Only
  filing-absent lines stay n/a.
- **XBRL scope finding (verified live on BANR FY2025, 2026-06-12)**: the
  tagged credit-quality footnote grades only the COMMERCIAL book (9 classes,
  $6.98B of $11.72B; SM 81,101 / Substandard 135,640 — internally exact to
  the dollar). SNL's whole-portfolio 82,060 / 193,077 comes from the
  UNTAGGED MD&A "Loans by Grade" HTML table — structurally unreachable via
  XBRL. AQ Detail must label the XBRL grades "commercial portfolio (graded
  classes)" and NEVER present them as whole-portfolio; whole-portfolio
  criticized stays n/a unless/until an MD&A table parser is built (queued,
  separate decision — HTML scraping was previously ruled out for IRR).
- Keep ALL existing graphics; tables added alongside.

Rules: engine "computed kinds" for every derived row; n/a + flag for
truly unsourceable lines (never imputed); every new field verified against
BANR's actuals from these screenshots before shipping.

**Universal linking rule (user, 2026-06-12): SNL-blue = navigable, ours
too.** Every entity reference is a working link, not decoration:
- bank/company names & tickers → their company page (?bank= deep link, HAVE)
- filings/documents/exhibits → the EDGAR/FDIC/FFIEC document itself
- deals/transactions → the source 8-K / press release / S-4
- holders → holder detail view (cross-holdings filter at minimum)
- branches/markets → the geographic view filtered to them
- competitor/peer names in any report → their company page
No dead blue text: if we can't link it yet, it renders as plain text until
we can.

**Provenance requirement (user, 2026-06-12): every number click-through like
SNL** — (a) the formula popup showing the arithmetic WITH component values
(e.g. SNL's ROATE: (Amrt of Intang × (100−21%) + NI) / Avg TCE × 100), and
(b) source links to the underlying filing (FDIC SDI / FFIEC schedule /
EDGAR). Smart per row: DIRECT fields open straight to their filing source;
CALCULATED rows show the formula with each component value, and each
component is itself click-sourced (SNL's "Int Cost: Total Deposits" =
Int Exp / Avg Total Deposits x 100 pattern). The engine's row kinds decide
which popup a row gets — no orphan numbers.
