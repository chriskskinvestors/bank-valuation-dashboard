# KSK Design System — Redesign Spec (user-decided 2026-06-12)

The visible redesign the audit's P3 plumbing was building toward. All
decisions below are the user's; the bar: must not look like a Streamlit
app, must read as an institutional terminal. Mockup sign-off REQUIRED
before implementation ships. **SIGNED OFF by user 2026-06-12** ("looks good"
on the Income Statement mockup) — that mockup is the binding visual reference.

## Identity
- **Theme:** refined light, sharpened. No dark mode for now.
- **Emojis:** REMOVED everywhere — nav, headers, alerts. No exceptions.
- **Title bars:** SNL pattern, one dense line per page:
  `East West Bancorp (EWBC) | INCOME STATEMENT` with a small identifier
  row under it (exchange · ticker · CIK · FDIC cert — each a link).
  Non-company pages: `KSK INVESTORS | HOME`.
- **Accent:** deeper navy/steel (#1e40af family) replaces bright blue for
  links/active states/primary series. Semantic green/red/amber unchanged.
- **Numbers:** full financial convention — tabular numerals, right-aligned,
  negatives as red (1,234), thousands separators, units in column headers
  ($000 / % / x), never in cells.

## Components
- **KPI blocks:** ledger rows (label-value hairline rows), NO boxed cards,
  no shadows. st.metric is banned; replace with the ledger component.
- **Tables:** FULL GRID — horizontal + vertical hairlines, SNL spreadsheet
  look. Header row: small caps, hairline underline, units stated.
- **Navigation:** TOP NAV BAR replaces the sidebar entirely. Sections left,
  utilities right (refresh icon-button, coverage/freshness status chip,
  connection dot). Second-level (company sections / macro sections) stays
  as the existing radio-as-tabs row under the title bar.
- **Charts:** tightened in place — smaller titles, thinner axes, tighter
  margins, smaller legends. Chart system itself unchanged.
- **Status marks:** colored dot + plain label (● Elevated risk) — semantic
  colors, no icon fonts, no emoji.

## Layout
- **Width:** full-bleed wide, small gutters; prose-only blocks may cap.
- **Density:** terminal-tight — ~12px table text, 4-6px row padding.
- **Exports:** EVERY data table gets a small right-aligned Export
  (CSV/Excel) action.

## Execution order (after mockup sign-off)
1. styles.py overhaul: navy accent token flip, density tokens, full-grid
   table CSS, top-nav CSS, kill sidebar styles.
2. ui/chrome.py: title_bar(), top_nav(), ledger(), status_dot(),
   table_export() components.
3. app.py: sidebar → top nav structural swap.
4. Page-by-page migration (every page = title bar + de-emoji + ledger +
   grid tables + exports), render-verified per page, committed in batches.
5. Charts pass: tightened apply_standard_layout defaults.

Verification: preview render per page against this spec + the user's eye
before each batch ships. The SNL screenshots in chat are the reference.
