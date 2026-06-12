# Performance Program — CapIQ-grade loads (user directive 2026-06-12)

The bar: **the dashboard must feel like a website** — warm page loads in
~2-3s, cold (post-deploy) first paint under ~10s, refresh never bounces
you off your page. This doc is the budget, the known costs, and the
remaining levers, in priority order. The nightly shift may pick items.

## Budgets (measured at the prod URL, warm instance)
| Surface | Budget | Notes |
|---|---|---|
| Home | ≤ 3s warm; first-load-after-deploy ≤ 10s | aggregate snapshot serves instantly; sections stream after |
| Company → any tab | ≤ 3s warm per tab switch | single-bank caches 1h |
| Screening | ≤ 4s | full-universe table |
| Statement tabs | ≤ 3s | FDIC hist cached |

## Shipped levers (2026-06-12)
1. Cross-instance aggregate snapshot (6h) + nightly job warm — killed the
   60s cold rebuild that every deploy triggered.
2. URL state — refresh restores the exact view (no Home bounce).
3. Targeted "Refresh this view" — re-pull one page, never a global nuke.
4. Universe/get_name snapshot tier — killed the 174s resolver cold start.

## Remaining levers (priority order)
1. **st.fragment per heavy section** (Home sections, statement tables):
   section-level reruns instead of whole-script; also enables partial
   refresh UX. Verify fragment behavior with our radio-as-tabs CSS first.
2. **Precompute-at-write**: move remaining read-time aggregates into jobs
   (screening table frames, peer percentile contexts, sector medians) and
   serve stamped snapshots like the Home aggregate.
3. **FRED snapshot table**: get_macro_snapshot + chart series persisted by
   a 15-min job slice instead of live FRED calls on first paint (FRED is
   the slowest external dependency; one timeout = +15s on a section).
4. **Single-bank prefetch**: when a bank is picked, prefetch its other
   tabs' data in the background (st.cache warming thread) so tab switches
   are instant.
5. **Cloud Run**: startup CPU boost on; consider min-instances=2 during
   market hours (scheduler-driven) so deploys never leave zero warm
   instances; measure container import time (heavy pandas/plotly imports
   — consider lazy plotly import per page).
6. **Measure, don't guess**: add a lightweight per-section timing log
   (print path + ms) so prod logs show where seconds actually go; review
   before each next lever.

## Rules
- Freshness stays honest: every served snapshot carries its as-of stamp;
  speed never silently serves stale data as live.
- No lever ships without a before/after measurement in the commit message.
