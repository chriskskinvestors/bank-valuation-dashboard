# Home + "Market & Macro" Rebuild Plan (user, 2026-06-12)

## Home — guiding principle (user)
The page's job, in priority order:
1. **Important overnight news** — what moved while you slept, severity-ranked.
2. **Company-specific alerts/news** — our universe's events, not generic wires.
3. **What's happening TODAY that we care about** — earnings + macro prints.
Everything else (markets strip, valuations, leaderboards) is context below.

### Approved upgrades
- Macro half of Today's Calendar: FRED-based print days (CPI, FOMC, jobs,
  GDP) merged with the earnings calendar into one day view.
- Sector valuation snapshot: median P/TBV / P/E / div yield by size tier
  vs 1y ago, compact strip.
- Configurable extras menu: settings popover toggling optional sections.

### Proposed section order (confirm with user)
1. Overnight & Breaking — CATEGORIZED sections (user): Macro ·
   Geopolitical · Domestic · Large Markets Events Outside Banks · plus the
   bank/company alerts block. Non-bank categories need general-news topic
   feeds — extend the EXISTING Google News adapter with topic queries
   (macro/geopolitics/markets); bank news stays on current wires.
2. Today's Agenda (merged earnings + macro calendar + more to come —
   user will add)
3. Markets & Rates pills (existing)
4. Universe movers
5. Sector valuation snapshot
6. Sector M&A · leaderboards · industry valuations · rest
+ extras menu controls optional sections' visibility.

## "Market & Macro" — remake of the Macro tab WITH SUB-SECTIONS (user)
All four upgrades approved: bank-sector overlay, deposit-rate environment,
regime dashboard, layout/density reflow. Structure to confirm:

### Process (user): build out SLOWLY — user supplies inspo uploads per
part; talk through each section's contents before building it. Structure
below is approved as the skeleton.

### Approved sub-sections
1. **Rates & Curve** — existing treasury/curve charts (incl. recession
   highlight + lookbacks), curve-shape label.
2. **Bank Sector** — KRE/KBE vs 2s10s / fed funds / HY OAS shared-timeline
   overlays; relative performance vs SPX.
3. **Funding & Deposits** — FDIC weekly national deposit rates (savings,
   CD tenors) vs fed funds; industry deposit beta context.
4. **Credit & Spreads** — HY/IG OAS levels + regime bands.
5. **Economy & Calendar** — FRED prints (CPI, jobs, GDP, retail) with
   recent-vs-expected and the upcoming print calendar (shares data with
   Home's Today's Agenda).
6. **Regime** — labeled states: curve (steep/flat/inverted + direction),
   credit (tight/normal/stressed), Fed path direction; one glance panel.

Sources all existing or cheap: FRED (have), FDIC national rates (public
weekly series), FMP/ETF prices (have). Nav via a section radio like the
company pages; ui/macro.py splits per section. Dense, token-based, all
graphics kept.
