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
5. Per-section timing logs (`utils/timing.py`, `[timing]` in Cloud Run
   logs) — measured Home cold: alert_inbox 11.6s, markets_rates 3.6s,
   universe_tickers 1.5s, everything else ~250ms.
6. `data/cache.served_snapshot` — THE generic cross-instance snapshot
   helper (fresh-serve / guard-mismatch-rebuild / persist-on-build).
   Applied to the three measured costs: earnings calendar (6h, job-warmed
   nightly), FRED home-rates bundle (30 min), insider alerts (30 min).

## Remaining levers (status reconciled 2026-07-10 — most SHIPPED)
1. **st.fragment per heavy section** — ✅ DONE (perf tab-switch track:
   @st.fragment on the price/valuation panels + statement pages;
   chrome.lazy_tabs renders one pane, not all).
2. **Precompute-at-write** (screening table frames, peer percentile
   contexts, sector medians) — OPEN; belongs to the Screen & Compare track
   (its worktree/lane). The Trends grids + Home aggregate already follow
   this pattern.
3. **FRED snapshot** — ✅ DONE (jobs/refresh_macro.py, scheduled */30,
   warms fetch_series' cross-instance cache for the full series set; render
   threads do cache reads only).
4. **Single-bank prefetch** — ✅ DONE (_prefetch_profile_data ThreadPool
   warm of the company tabs on bank pick, ui/bank_detail.py).
5. **Cloud Run** — startup CPU boost ON; min-instances=2 during market
   hours DECIDED-NOT-TAKEN (owner, 2026-07-10): pre-warm-per-deploy + the
   */15 snapshot job cover the common case at current cost levels — revisit
   only if users report cold-start pain after go-live. (Container import
   timing / lazy plotly stays a nice-to-have.)
6. **Measure, don't guess** — ✅ DONE (utils/timing `?perf=1` per-section
   profiler; MEASURE-FIRST is the house rule for every perf change).

## Rules
- Freshness stays honest: every served snapshot carries its as-of stamp;
  speed never silently serves stale data as live.
- No lever ships without a before/after measurement in the commit message.
