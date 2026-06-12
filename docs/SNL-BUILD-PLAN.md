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
| Transcripts & Presentations | FMP transcript endpoint (verify plan tier); investor decks via 8-K EX-99 |
Layout (user, 2026-06-12): five SUB-TABS under News & Filings —
"Filings & Reports", "Key Exhibits", "Press Releases", "Transcripts &
Investor Presentations", "Events Calendar" — added via COMPANY_NAV +
the renderer registry in ui/company_nav.py (structural test keeps them
in sync). Existing Filings/Activity content folds into the new structure;
keep the form-type filter inside Filings & Reports.

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
- Keep ALL existing graphics; tables added alongside.

Rules: engine "computed kinds" for every derived row; n/a + flag for
truly unsourceable lines (never imputed); every new field verified against
BANR's actuals from these screenshots before shipping.

**Provenance requirement (user, 2026-06-12): every number click-through like
SNL** — (a) the formula popup showing the arithmetic WITH component values
(e.g. SNL's ROATE: (Amrt of Intang × (100−21%) + NI) / Avg TCE × 100), and
(b) source links to the underlying filing (FDIC SDI / FFIEC schedule /
EDGAR). Smart per row: DIRECT fields open straight to their filing source;
CALCULATED rows show the formula with each component value, and each
component is itself click-sourced (SNL's "Int Cost: Total Deposits" =
Int Exp / Avg Total Deposits x 100 pattern). The engine's row kinds decide
which popup a row gets — no orphan numbers.
