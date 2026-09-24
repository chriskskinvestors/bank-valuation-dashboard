# Review 2026-09-24 — Lane 1: numbers vs ground truth

Owner directive: "I want more review of the platform." This lane compares what the
live site (https://dashboard.kskinvestor.com, captured 10:47–11:25 ET on
2026-09-24, prices as of those timestamps) displays against primary sources read
by hand, never from the pipeline. Nothing was fixed in this pass; every item
below is a candidate for the owner to pick.

Cardinal rule applied: a number that cannot be tied to a primary source is a
finding; an n/a where the source clearly discloses the figure is a finding.

## Banks and why

| Ticker | Class | Why it discriminates |
|---|---|---|
| JPM | mega | $ millions filer; now a 2-charter FDIC "group" (Dearborn, $71M) |
| C | mega | SEC companyfacts lags two quarters (Q1-26 and Q2-26 10-Qs absent) |
| ONB | regional | companyfacts lags one quarter; Q2 figures overlaid from the 10-Q's own iXBRL |
| HBAN | regional | companyfacts lags one quarter; Cadence merger 2026-02-01 |
| WTFC | multi-charter | 16 FDIC charters aggregated |
| CZWI | community <$2B | $1.8B, $ thousands filer |
| LARK | community <$2B | $1.6B, late 10-K, 5% stock dividend |
| PBAM | OTC, non-SEC | CalPrivate; TBV only from the wire release |
| FBIZ | preferred stock | TBVPS must exclude $12.0M preferred |
| BBT | recent merger | Beacon = Berkshire (legal acquirer) + Brookline (accounting acquirer), 2025-09-01; ASU 2025-08 restatement |

## Ground truth used

Pulled by ten independent agents with plain `requests` (no repo imports), saved
under the session scratchpad `gt/` (gt_<TICKER>.md/.json + raw R-files,
EX-99.1, companyfacts, FDIC financials, SOD):

* SEC: latest 10-Q FilingSummary → consolidated balance sheet / income statement
  R-files; companyfacts quarterly series (Q4 = FY − 9M, 10-K/10-Q only);
  earnings 8-K EX-99.1 (+ EX-99.2 supplements) for the company's own EPS, TBVPS,
  BVPS, NIM, efficiency, ROTCE, CET1, shares.
* FDIC: `financials` at 20260630/20260331/20251231/20250630 (values $ thousands;
  REPDTE YYYYMMDD); `institutions` for RSSDHCR charter groups; SOD 2026 (published
  2026-09-18, as of 2026-06-30) and 2025.
* PBAM: Q2-2026 earnings press release (wire).

Notes for future pulls: FDIC field `INTEXP` does not exist (it is `EINTEXP`);
unknown field names are silently dropped, not 400'd; the API rate-limits (429)
under parallel pulls. ONB's earnings exhibit is named `onb_exhibit991er2q26.htm`.
companyfacts lag status today: C (max end 2025-12-31), ONB and HBAN (2026-03-31)
still lag; JPM, WTFC, CZWI, LARK, FBIZ, BBT are current.

## Summary — bank × page (OK = every captured number tied; n = finding count; — = not captured)

| Page | JPM | C | ONB | HBAN | WTFC | CZWI | LARK | PBAM | FBIZ | BBT |
|---|---|---|---|---|---|---|---|---|---|---|
| Corporate Profile (market/valuation/performance/profile/highlights) | 3 | 3 | 2 | 1 | 2 | 1 | 1 | 2 | 1 | 3 |
| Financials › Templated › Balance Sheet (quarterly) | 2 | 1 | OK | OK | 1 | OK | OK | OK | — | OK |
| Financials › Templated › Income Statement (quarterly) | 1 | 1 | 1 | — | — | — | — | — | — | — |
| Financials › Templated › Capital Adequacy | 3 | 2 | 2 | 1 | — | — | — | — | — | — |
| Financials › Templated › Asset Quality Detail | CRASH | OK | — | — | — | — | — | — | — | — |
| Financials › Company Reported › Income Statement | 1 | 2 | 1 | 3 | 2 | OK | 2 | n/a (no filer) | 1 | 4 |
| Financials › Company Reported › Balance Sheet | OK | OK | 1 | OK | — (blank at 10s) | — | — | n/a | — | 1 |
| Valuation › Valuation Model | note | — | — | — | — | — | — | — | — | — |
| Estimates / Earnings | 1 | 1 | OK | OK | 1 | OK | 1 | 1 | — | 1 |
| Ownership › Institutional (13F) | — (still fetching at 10s) | — | — | — | — | — | — | — | — | — |
| Market Analysis › Market Share & Branches | OK* | OK | OK | OK | OK | OK | OK | OK | — | OK |

\* JPM branch count 5,142 not tied (JPM FDIC/SOD ground-truth pull was still
rate-limited when this report was written); C 667, ONB 354, HBAN 1,452, WTFC 213,
CZWI 21, LARK 29, BBT 150 all equal the SOD-2026 row counts, and every deposit
total equals the SOD-2026 DEPSUMBR sum.

Findings that recur across banks are counted once per bank above but listed once
below. Severity: P0 = a wrong number is displayed; P1 = unlabeled stale or
wrong-entity number, or a page that cannot render; P2 = n/a where the source
discloses, label/unit issues.

---

## P0 — wrong numbers displayed

### P0-1  Templated Income Statement "Quarterly" view shows calendar-YTD flows under single-quarter labels (every bank)

* Page: Financials › Templated › Income Statement, period = Quarterly.
* Shown: JPM column "Q2 '26" Net income **$31.34B**, "Q1 '26" $13.97B; Interest
  & fees on loans Q1'25 $20.78B → Q2'25 $42.07B → Q3'25 $64.29B → Q4'25 $86.74B
  (monotone within each year, resets at Q1). C "Q2 '26" Net income **$10.12B**
  ("Q1 '26" $5.01B). ONB "Q2 '26" Net income **$511.6M** ("Q1 '26" $244.0M).
* Source: FDIC `NETINC` is calendar-YTD. Cert 628 at 20260630 NETINC =
  31,340,000 $K (the 6-month figure); the single quarter is 31.34 − 13.97 =
  $17.37B, which the same site's Capital Adequacy page correctly uses ("Quarterly
  NI $17.36B"). Cert 3832 (ONB) NETINCQ = 267,524 $K vs the 511.6M shown.
  Cert 7213 (C) NETINCQ = 5,107,000 $K vs 10.12B shown.
* Code path: `ui/financials_statements.py:1536-1546` — every `_INCOME` row is
  kind `"dollar"`, and the `"dollar"` branch (`:604-607`) returns the raw FDIC
  field regardless of `period`. Only the `"flow"` kind (`:1193-1230`, used for
  provision/charge-off/dividend rows) de-cumulates. Effective tax rate is a
  YTD ratio and is fine; every $ row is not.
* Fix: route the `_INCOME` dollar rows through the `"flow"` machinery in the
  Quarterly view (filed `*Q` field when FDIC has one, else YTD(q) − YTD(q−1);
  dead cell when the prior quarter is missing), and say "single quarter" in the
  click-through.
* Test to pin: fixture history with NETINC Q1=100, Q2=250, Q3=400 → Quarterly
  view Q2 cell renders 150 and Q3 renders 150; Annual view Q4 renders the 12/31
  YTD. Add a structural assertion that no `_INCOME` row is kind `"dollar"`.

### P0-2  Company Reported Q4 = FY − 9M derivation subtracts across mismatched bases (BBT, HBAN)

* Page: Financials › Company Reported › Income Statement, Quarterly.
* BBT shown Q4'23: Net interest income **$59.1M**, Total non-interest expense
  **$17.0M**, Professional services **($1.0M)**. Source (companyfacts CIK
  1108134, 10-K FY2023 3-month facts): Q4-2023 NII 88,421 $K, non-interest
  expense 78,992 $K. The 59.1 = 339,711 (FY2023 as RECAST for Brookline in the
  FY2025 10-K) − 280,626 (legacy Berkshire's own 9M-2023 10-Q). Same for
  17.0 = 239,524 − 222,516.
* BBT shown Q4'25: "Net interest income after provision" **$255.1M** while the
  same column's NII is $196.0M (impossible). Source: 461,714 (FY2025 10-K,
  restated for ASU 2025-08) − 206,607 (original Q3-25 10-Q 9M) = 255.1; the
  10-K's own tagged Q4-2025 value is 191,635 $K.
* HBAN shown "Income after income taxes" Q4'25 **$2.21B** and Q4'24 **$1.94B**
  (Net income attributable rows in the same columns are $519M / $530M).
  Source: `ProfitLoss` FY2025 = 2,229 $M, 9M-2025 = 1,706 $M → Q4 = 523. The
  full-year value is being shown under a quarter label.
* Code path: `data/sec_statements.py:1450-1516` (`Q4 = FY − 9M`): the FY column
  is taken from the latest 10-K that reports the year, the 9M column from the
  original Q3 10-Q, with no check that both belong to the same registrant basis /
  restatement vintage; the HBAN case passes the FY value through when the 10-Q's
  row key differs (label variant) instead of blanking. Multi-quarter stitch
  `as_reported_statement_multiquarter` (`:1519+`).
* Fix: derive Q4 only when the FY and 9M columns come from filings that share
  the same "prior-period basis" (same registrant, and the 10-K's own tagged
  3-month Q4 fact exists — prefer that fact directly, it is on the face for most
  filers); never pass a 12-month value through; when the 10-K restates 9M,
  use the 10-K's restated 9M (it tags it) or blank.
* Test to pin: BBT fixtures FY2023 (recast 339,711) + 9M-2023 (280,626) must
  render blank or 88,421, never 59,129; HBAN FY 2,229 with a 9M row under a
  different label must render blank, never 2,229.

### P0-3  Company Reported EPS rows labeled bare "Basic" / "Diluted" render as whole dollars (LARK, FBIZ)

* Page: Financials › Company Reported › Income Statement (Annual).
* Shown: LARK "Basic **$3** / Diluted **$3**" for FY2025 and "**$2**" for
  FY2022–FY2024; FBIZ "Basic **$4 $5 $4 $5 $6**", "Diluted … **$6**" for FY2025.
* Source: LARK 10-K FY2025 diluted EPS **3.07** (companyfacts, filed
  2026-04-14); FBIZ FY2025 diluted EPS **5.94**, FY2024 **5.20**.
* Code path: `ui/financials_statements.py:2331` `_eps = re.compile(r"per share|per
  common share")` — the row label is just "Basic"/"Diluted" under an "EARNINGS
  PER SHARE" header, so `_m()` (`:2345-2352`) falls to the dollar-compact
  branch and prints `$3`. The parser already knows the row is
  `perShareItemType` (`data/sec_statements.py:74-96`) but the renderer keys off
  the label.
* Fix: carry the XBRL data type (per-share / shares / monetary) from
  `parse_rfile` into the row dict and format on it; the label regex stays as a
  fallback only.
* Test to pin: R4 fixture with `Basic` under an `Earnings per share` header and
  data type `perShareItemType`, value 3.07 → cell "$3.07".

### P0-4  Company Reported weighted-average share counts are off by 1,000× or 1,000,000× (JPM, C, ONB, HBAN, WTFC, BBT legacy quarters)

* Page: Financials › Company Reported › Income Statement.
* Shown: JPM "Weighted-average diluted shares **0.0M**" (FY2025 true
  2,781.5M); WTFC "**0.1M**" (FY2025 true 67.9M); ONB Q2'26 "**0.4M**" (true
  ~382.5M); HBAN Q2'26 "**2.0M**" (true 2,048.3M); C "**0.0M**"; BBT legacy
  Berkshire quarters "**0.0M**" while post-merger quarters (filed in units) show
  the correct 83.8M.
* Source: R4 headers "shares in Thousands" (ONB, HBAN, WTFC, C) / "shares in
  Millions" (JPM); companyfacts `WeightedAverageNumberOfDilutedSharesOutstanding`.
* Code path: `data/sec_statements.py:67, :320-322` deliberately do not scale
  share rows by the "$ in Thousands" factor (correct) but also do not apply the
  separate "shares in Thousands/Millions" clause; `ui/financials_statements.py:2349`
  and `:2434` then divide the raw number by 1e6 assuming units.
* Fix: parse the "shares in <unit>" clause in `parse_rfile` and scale
  `sharesItemType` rows to units before display.
* Test to pin: R4 fixture "shares in Thousands, $ in Millions" with Diluted =
  2,048,311 → "2,048.3M"; JPM-style "shares in Millions" 2,781.5 → "2,781.5M";
  a units-filer 83,816,086 → "83.8M".

### P0-5  Citigroup EPS (TTM) $6.99 is FY2025 EPS, labeled "(TTM, co. 10-Q)"; P/E 18.8x is wrong

* Page: Corporate Profile › Valuation; Estimates / Earnings › Key reported metrics.
* Shown: "EPS (TTM, co. 10-Q) **$6.99**", "P/E (LTM) **18.8x**"; the same
  page's Company Reported income statement shows Q3'25 $1.86, Q1'26 $3.06,
  Q2'26 $3.15.
* Source: Citi Q2-2026 supplement p.1 diluted EPS 1.96 / 1.86 / 1.19 / 3.06 /
  3.15 (2Q25…2Q26) → TTM through 2Q26 = **9.26**; FY2025 = 6.99. At $131.12
  the P/E is 14.2x.
* Why: companyfacts still holds nothing past 2025-12-31 for C (both Q1-26 and
  Q2-26 10-Qs absent). The iXBRL overlay (`data/sec_facts_overlay`) adds only
  the latest filing's facts, so `data/sec_client._extract_ttm_value` (`:335-420`)
  cannot find 4 consecutive quarters (Q1-26 missing) and falls back to the FY
  value, while `ui/bank_detail._eps_label` (`:120-131`) labels the figure by the
  overlay form. Same mechanism likely behind the profile's ROATCE 7.73% (the
  company's 2Q26 RoTCE is 13.0%).
* Fix: when the overlay is active, derive the missing interim quarter from the
  overlaid YTD (6M − 3M = Q1) before the consecutive-quarter test; if a
  consecutive window still cannot be formed, render n/a + "(companyfacts lags
  N quarters)" rather than a FY figure with a 10-Q label.
* Test to pin: slim facts with quarters through 2025-12-31 plus an overlay
  carrying 3M and 6M 2026 facts → TTM = 9.26 (or n/a), never 6.99 labeled
  "co. 10-Q".

---

## P1 — unlabeled stale / wrong-entity numbers, unrenderable pages

### P1-1  Asset Quality Detail crashes for JPM

* Page: Financials › Templated › Asset Quality Detail (JPM). Renders a Python
  traceback instead of the page: `TypeError: unsupported operand type(s) for -:
  'NoneType' and 'NoneType'` at `analysis/credit_dynamics.py:104`
  (`df[col].diff()` on an object column that holds None) via
  `ui/credit_dynamics.py:142` → `summarize_bank_credit` → `build_credit_timeline`.
  C renders fine. Likely the group-aggregated history (JPM is now a 2-charter
  group, see P1-4) carries None for the dropped average-based ratio columns.
* Fix: coerce the ratio columns with `pd.to_numeric(errors="coerce")` before
  `.diff()`, and render "n/a — ratio not available for this bank" instead of
  raising.
* Test: `build_credit_timeline` on records whose `nco_ratio` is None must not
  raise.

### P1-2  Beacon (BBT) Book value / share $30.10 and Shares Outstanding 84,364,733 use the wrong share count

* Page: Corporate Profile › Market Data and Valuation.
* Shown: Shares Outstanding **84,364,733**; "BV / Share **$30.10**" (no label);
  Market Cap $2.35B.
* Source: 10-Q cover (dei) 83,816,086 shares as of 2026-07-31; EX-99.1 per-share
  data BVPS **$30.30**, TBVPS $23.98 (2,539,796 / 83,816,086); the company
  excludes 548,647 unvested restricted shares. 84,364,733 = issued 89,576,403 −
  treasury 5,211,670.
* Code path: `data/sec_client.py:650-696` share resolution prefers
  `CommonStockSharesIssued − TreasuryStockCommonShares` (derived) when the
  issued/treasury pair is fresh; dei is path 2.
* Fix: prefer dei `EntityCommonStockSharesOutstanding` when it is dated on/after
  the balance-sheet date; keep issued − treasury as the fallback. Label BVPS
  "(co. release)" when the release figure is used, as TBVPS already is.
* Test: BBT facts fixture → shares 83,816,086, BVPS 30.30.

### P1-3  TBV / Share not taken from the company's reported figure when the release states it (JPM, ONB)

* JPM shown "TBV / Share **$112.69**", P/TBV 2.99x (no "(co. release)" label).
  Source EX-99.1 p.1 / EX-99.2 p.2: TBVPS **$113.35** (JPM nets DTLs on
  intangibles; 301,314 / 2,658.2 = 113.35). P/TBV should be 2.97x.
* ONB shown "TBV / Share (co. 10-Q) **$14.35**", "BV / Share (co. 10-Q) $21.84".
  Source release Table 14: TBVPS **$14.32**, BVPS **$21.80** (the release deducts
  preferred at $243,719K liquidation value, the 10-Q carries $230,500K).
* The design (`data/sec_earnings_8k.reported_tbvps`, `analysis/valuation._resolve_tbvps`)
  is release-first; for both banks the EX-99.1 exists and states the figure, so
  either the 8-K location (`_ex991_document`, `:116-135`; ONB's exhibit is
  `onb_exhibit991er2q26.htm`, JPM's release is EX-99.1 with the figure in
  prose) or a sanity gate rejected it. C, HBAN, WTFC, CZWI, LARK, FBIZ, BBT,
  PBAM all correctly show the release figure.
* Fix: log the rejection reason per bank; JPM's "tangible book value per share
  of $113.35" prose pattern and ONB's Table 14 row "Tangible common book value"
  should be admitted.
* Test: `extract_reported_tbvps_status` on the saved JPM and ONB EX-99.1 bytes
  returns 113.35 and 14.32.

### P1-4  JPM's $71M Dearborn charter turns JPM into a "group" and blanks ROAA, ROAE, NIM, NPL, NCO, Reserves/Loans, Tier-1 leverage and the Asset Quality page

* Page: Corporate Profile › Financial Highlights and Performance; Estimates /
  Earnings › Key reported metrics; Templated Balance Sheet "Loan Loss Reserves /
  Gross Loans —"; Capital Adequacy "Tier 1 Leverage Ratio —" (all years).
* Shown: "ROAA (Ann. YTD) —", "ROAE —", "Net Interest Margin —", "NPL Ratio —",
  "NCO Ratio —", "Reserves / Loans —" for both Jun 2025 and Jun 2026.
* Source: FDIC cert 628 at 20260630: ROA 1.585, ROE 18.55, NIMY 2.88,
  RBC1AAJ present; the sibling cert 21761 (JPMorgan Chase Bank, Dearborn) has
  ASSET 71,467 $K = 0.0017% of the group.
* Code path: `data/cert_group.py:55-60` `AVERAGE_BASED_RATIOS` drops `ROA ROE
  NIMY RBCT1JR … NCLNLSR NTLNLSR LNATRESR NPERFV …` for any group. `NCLNLSR`
  (NCLNLS/LNLSGR), `LNATRESR` (LNATRES/LNLSGR) and `NPERFV` are pure ratios of
  summed period-end levels and are exactly recomputable; the truly
  average-based ones (ROA/ROE/NIMY) could be taken from the lead charter when
  siblings are below a materiality threshold, with a footnote. WTFC shows the
  same blanks (NPL 0.32% and ACL/loans 0.72% are computable from the summed
  levels: 179,271 / 56,108,448 and 403,023 / 56,108,448).
* Fix: recompute level ratios for groups; add a materiality rule (sibling
  assets < 1% of group → lead-charter average-based ratios, labeled).
* Test: two-cert group where cert B is 0.002% of assets → ROA equals cert A's
  ROA (labeled), NCLNLSR = ΣNCLNLS/ΣLNLSGR.

### P1-5  Corporate Profile PERFORMANCE card shows bank-subsidiary ratios under company labels, and ROATCE changes definition by bank

* Shown vs company-reported (holding company) figure:
  * CET1: JPM 15.03% vs 14.1% Std / 14.2% Adv; C 13.79% vs 12.8%; HBAN 11.82%
    vs 10.0%; WTFC 11.17% vs 10.4%; FBIZ 11.17% vs 9.54%; LARK 13.45% vs 11.89%.
  * NIM: C 3.02% vs 2.54% (TE); HBAN 3.50% vs 3.21% (FTE); LARK 4.27% vs 4.22%.
  * Efficiency: WTFC 43.00% vs 54.0% (both shown, second labeled "(co.
    release)"; the 43.00% ties exactly to Σ NONIX / (Σ NII + Σ NONII) across the
    16 charters — right math, bank-level).
  * ROATCE: WTFC **18.50%** (company 14.91%) and JPM 21.24% are the
    `ui/bank_detail.py:222-237` fallback = bank-level YTD NETINC annualized ÷
    bank tangible equity; C 7.73% is a holding-company TTM on a stale window
    (P0-5); FBIZ 14.65% vs company 16.89%.
* Only the Efficiency row and the Valuation block carry "(co. release)" /
  "(co. 10-Q)" labels; CET1/NIM/ROAA/NPL/ROATCE have no entity label on the
  card, unlike the Templated pages ("BANK SUBSIDIARY values, not
  holding-company").
* Fix: label the card "(bank sub, FDIC)" or add the holdco twin (the 10-Q
  capital table already extracts holdco CET1: JPM 14.20%, ONB 11.08%), and make
  ROATCE one definition (TTM NI-to-common ÷ average TCE, holdco) or label the
  fallback.
* Test: card label snapshot; ROATCE for a fixture with both SEC and FDIC data
  must equal the holdco 4-quarter figure.

### P1-6  Beacon (BBT) Company Reported statements silently mix three entities; Q3'25 shows the superseded figure

* Page: Financials › Company Reported › Income Statement and Balance Sheet
  (Quarterly); Corporate Profile › Financial Highlights.
* Shown: columns Q2'24–Q2'25 are legacy Berkshire Hills' own 10-Qs (Q2'25 EPS
  $0.66, NI $30.4M, total assets $12.03B); Q4'24 is the Brookline recast
  (goodwill $241.2M, loans $9.78B — Berkshire Bank's goodwill was $34.1M);
  Q3'25 onward is Beacon. Q3'25 shows the ORIGINAL 10-Q values (EPS **-0.57**,
  NI **($50.2M)**) while the 10-K restated them to -0.05 / -4.2M (ASU 2025-08) —
  and the site's own TTM EPS 1.91 already uses the restated value. Duplicate
  section headers ("INTEREST EXPENSE" twice, two NON-INTEREST INCOME blocks).
  Corporate Profile "Jun 2025" column = pre-merger Berkshire Bank ($6.16B
  assets vs $22.20B), unlabeled; Templated BS "Asset Growth Q3'25" column reads
  the merger jump as growth.
* Source: 10-Q Q2-2026 prior-period columns are legacy Brookline (accounting
  acquirer): Q2-2025 NI 22,026 $K, EPS 0.25; 8-K Item 2.01 closing 2025-09-01;
  FDIC certs 23621/34147/15995 merged into 17798 on 2025-09-02.
* Fix: for a reverse-merger registrant, take prior-period columns from the
  post-merger filings (recast) or label each column's source entity; when a
  10-K restates a quarter, show the restated value (or both, labeled).
* Test: BBT stitch → Q2'25 column equals the recast 22,026 or is labeled
  "Berkshire (legacy)"; Q3'25 NI equals -4,221 (restated) with the original in
  the click-through.

### P1-7  HBAN Company Reported income statement drops "Total noninterest income" and all fee lines from Q2'25 onward

* Shown: "Total noninterest income" blank for Q2'25, Q3'25, Q4'25, Q1'26,
  Q2'26 (only Q2'24 $491M, Q3'24 $523M, Q1'25 $494M); "Payments and cash
  management revenue" etc. blank after Q1'25; a bare "Noninterest income" row
  (5.0M, 6.0M, 45.0M, 20.0M, (58.0M) …) is actually "Other noninterest income".
* Source: Q2-2026 release Table 7 total noninterest income **$785M** (Q2-25
  $471M, tagged `NoninterestIncome` in the 10-Q).
* Suspected: HBAN moved the fee lines to dimensioned members in 2025; the
  stitch keys the undimensioned total under a label variant and the
  `_consolidate_variants` guard (correctly) refuses to merge. Memory's
  "parser miss is a bug" standard applies.
* Test: HBAN Q2-2026 R4 fixture → "Total noninterest income" = 785.

---

## P2 — n/a where disclosed, labels, units

* **P2-1 Capital Adequacy holdco table stale for companyfacts-lagged banks.**
  C and ONB show "Source: SEC 10-K filed 2026-02-20/-19" with FY columns only,
  although the Q2-26 10-Qs (filed 2026-08-06 / 07-29) tag the ratios (ONB
  release: CET1 11.09%). The iXBRL overlay covers per-share facts only. JPM
  (current) does show a "Q2 '26" column. Labeled, so P2.
* **P2-2 Templated statement section headers say "($000)" while cells are
  $-compact** ("ASSETS ($000) … $4,091.39B"). Units contract: say "$" or
  drop the unit from the header; the click-through already gives $000.
* **P2-3 PBAM EPS / P/E blank** although the wire release (already parsed
  for TBVPS 49.57) states quarterly diluted EPS; the Earnings page's own
  history shows 2.27 / 2.07 / 1.71 / 1.65 → TTM 7.70, P/E 11.1x.
* **P2-4 Earnings-surprise "EPS Act" is FMP adjusted EPS, unlabeled.** JPM
  2Q26 shows $6.14 (GAAP diluted $7.70; the 6.14 is "excluding significant
  items"); WTFC 3.28 vs GAAP 3.30; BBT 3Q25 0.44 vs GAAP -0.57 (restated
  -0.05). The KEY REPORTED EPS beside it is GAAP TTM.
* **P2-5 Company Reported row order scrambled.** WTFC and LARK: "Total
  non-interest expense" sits inside the NON-INTEREST INCOME block before
  "Income before taxes", and "Total non-interest income" appears after the EPS
  rows; BBT/HBAN show duplicated headers. Numbers are right, placement is not.
* **P2-6 ONB Company Reported balance sheet formats the stated-value common
  stock row as raw dollars**: "Common stock … **$389,662,000.00**" (FY2021
  "$165,838,000.00") among $-compact rows.
* **P2-7 Annualized growth compounds acquisition jumps.** ONB "Asset Growth"
  Q2'25 **203.30%** (Bremer), HBAN Q1'26 **158.67%** (Cadence), BBT Q3'25.
  Formula is as documented (`(1+QoQ)^4 − 1`), but a reader gets a
  plausible-wrong growth rate; flag when the quarter contains an acquisition
  (the cert-group / structure data knows).
* **P2-8 C Company Reported IS derived Q4 columns** leave "Provision for
  income taxes", "Net income before attribution to NCI" and "Citigroup's net
  income" blank (Q4'24, Q4'25) while pretax and continuing-ops income are
  populated — the 10-K discloses all of them.
* **P2-9 Capital Return "Dividend data not available in SEC filings"** for
  JPM and ONB; JPM tags `CommonStockDividendsPerShareDeclared` 1.50 (used one
  line lower for "DPS YoY +13.2%") and `PaymentsOfDividendsCommonStock`.
* **P2-10 FTE adjustment / NII (FTE) "—" for the latest quarter** (JPM, C, ONB
  "Q2 '26") because the FFIEC store is one quarter behind; the cell carries no
  "not yet ingested" note, unlike the deposit-cost rows.
* **P2-11 LARK earnings-surprise chart axis** renders "23:59:59.9996Jul 28,
  2026 / 00:00:00.0002" tick labels when there is a single data point.
* **P2-12 CR balance sheet Quarterly toggle** did not switch views on the
  first click for ONB and WTFC (annual 10-K view stayed); Company Reported IS
  for CZWI did not render within 18 s on a cold load. UX lane may already have
  these.

---

## Checked and found correct (a clean page is a result)

* **Templated Balance Sheet levels tie to FDIC at 20260630 for all ten banks**
  (ASSET, DEP, LNLSNET, LNLSGR, LNATRES, INTAN, INTANGW, EQ/EQTOT, SC, SUBND),
  including the WTFC 16-charter sums (74,846,093 / 62,458,831 / 55,705,425 /
  403,023 / 7,487,135 / 1,013,166 $K — and the site correctly excludes cert
  35063 Wintrust Private Trust Co, which the FDIC financials endpoint returns
  under the same RSSDHCR) and the JPM group (4,091,315,000 + 71,467 $K =
  $4,091.39B shown).
* **Company Reported balance sheets tie to the 10-Q face to the last digit** for
  JPM (Q2'26 total assets 5,015.07B, equity 374.60B, preferred 21.04B), C
  (2,894.65B / 214.45B / preferred 19.55B), HBAN (283.98B / 32.66B / NCI 41M),
  ONB FY2025 (72.15B / 8.49B / preferred 230.5M).
* **Corporate Profile valuation inputs tie** for C (TBVPS 100.89, BVPS 114.74,
  shares 1,677,436,783 exact), HBAN (TBVPS 9.65 from the supplement's TCE
  reconciliation; BVPS 14.72 correctly derived because the company states no
  BVPS; TTM EPS 1.29), WTFC (TBVPS 92.13, BVPS 105.26, TTM EPS 12.45, shares
  67,455,414), CZWI (16.55 / 19.83 / 1.31 / 9,650,231), LARK (21.76 / 27.35 /
  3.22), FBIZ (TBVPS **44.38 excludes the $11,992K preferred** — the naive
  figure would be 45.81; BVPS 45.81; TTM EPS 6.56; shares 8,368,320), BBT
  (TBVPS 23.98, TTM EPS 1.91 on the restated basis), JPM (TTM EPS 23.35 =
  5.07 + 4.63 + 5.94 + 7.70 — the 2Q26 GAAP EPS is $7.70; shares
  2,658,186,195), ONB (TTM EPS 2.26 vs sum-of-quarters 2.25, rounding).
* **Staleness labeling works where designed**: C, ONB, HBAN carry "(co. 10-Q)"
  / "(co. release)" labels and the footnote "read from the filing's own XBRL
  because SEC companyfacts has not yet published it"; ONB's overlaid TBVPS is
  within $0.03 of the release (P1-3 covers the residual).
* **Corporate Profile Financial Highlights (FDIC) tie** for single-charter banks:
  ONB ROAA 1.41 / ROAE 12.44 / NIM 3.60 / Eff 45.98 / CET1 11.34 / NPL 0.92 /
  NCO 0.26 / Res 1.14; C 1.06 / 11.69 / 3.02 / 51.39 / 13.79 / 0.66 / 1.06 / 2.33;
  HBAN, CZWI (NPL 2.28 = company 2.28), LARK, FBIZ (Res/Loans 1.04 =
  37,393 / 3,585,615), PBAM, BBT — all equal the FDIC fields.
* **Holding-company capital table (JPM)**: CET1 14.20% / T1 15.10% / Total
  17.00% / leverage 6.60%, CET1 capital $302.62B, RWA $2,132.43B match EX-99.2
  p.9 (Advanced 14.2%; CET1 capital 302,620). "Total common equity 353.56B" =
  374,598 − 21,040 preferred, correct. FFIEC RC-R bank tables (JPM, C) are
  internally consistent and labeled bank-subsidiary.
* **Market Share & Branches** summaries equal SOD 2026 (published 2026-09-18)
  for C, ONB, HBAN, WTFC (with sibling charters listed), CZWI, LARK, PBAM, BBT;
  deposits equal DEPSUMBR sums, which equal FDIC DEP at 6/30/2026.
* **Earnings page consensus/next-report blocks** are market data and were not
  tied to a primary source (out of scope); dates and "(proj.)" flags are
  labeled.

## Not covered in this pass

* Ownership › Institutional (13F): still "Fetching 13F filings from SEC
  EDGAR…" after 10 s on JPM; not captured for any bank.
* Valuation › Valuation Model: outputs are model-derived; the "Model inputs"
  number fields could not be read through the browser bridge. Warranted P/TBV
  uses the profile ROATCE, so P1-5 propagates into it.
* Templated Income Statement quarterly captured for JPM, C, ONB only (P0-1 is
  structural, so the other seven are affected identically); Capital Adequacy
  captured for JPM, C, ONB, HBAN.
* JPM FDIC/SOD ground-truth pull (rate-limited) — the JPM FDIC figures above
  were taken directly from the FDIC API by the reviewer instead.

## Suggested order of fixes

1. P0-1 (every bank's quarterly income statement is YTD) and P0-4 (share
   counts) — both are one-place structural fixes with clear pins.
2. P0-5 + P1-3 + P1-2 (the Corporate Profile valuation block: Citi TTM, JPM/ONB
   TBVPS, BBT shares).
3. P0-2 / P1-6 (Q4 derivation and merger basis) — needs a design decision on
   restated vs original.
4. P1-4 (JPM group blanks) + P1-1 (crash) together.
5. P1-5 entity labels on the Performance card; then the P2 list.
