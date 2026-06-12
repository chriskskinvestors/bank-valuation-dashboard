# Data Freshness Policy

One page answering: **how old is each number on screen allowed to be, and
what keeps it fresh?** The audit (docs/AUDIT-2026-06-11.md §E) found these
policies scattered across files as folklore; this is the canonical record.
If you change a TTL, change it here too.

## The three cache layers

1. **DB store** (`data/cache.py`, Postgres in prod / SQLite locally) — the
   durable layer. Entries carry a write timestamp; fundamentals expire after
   `FUNDAMENTAL_CACHE_TTL_HOURS` (default **24h**, env-overridable). This is
   what the nightly jobs warm and what survives restarts.
2. **`@st.cache_data`** (per-process, in-memory) — request-path memoization
   so reruns don't refetch. Always shorter-or-equal to the DB TTL.
3. **Client-local helpers** (`data/freshness.is_fresh`) — stamped-blob checks
   for clients that store `{cached_at, payload}` dicts (universe snapshot,
   13F, Form 4, FRED, estimates).

## TTLs by data type (the contract)

| Data | TTL | Refreshed by | Rationale |
|---|---|---|---|
| SEC fundamentals (companyfacts) | 24h DB + 1h `st.cache` | nightly refresh-universe (6am ET); invalidated early when a new filing lands | filings change quarterly; 24h bounds staleness after a filing day |
| FDIC call-report data | 24h DB + 1h `st.cache` | nightly refresh-universe | FDIC updates quarterly, ~45 days after quarter-end |
| Universe snapshot | **26h** stamped blob | nightly refresh-universe rebuild (~7 min live build) | >26h means the nightly job failed — serve stale + log, never rebuild inline |
| Market prices | 15min `st.cache` (whole-watchlist metrics) | refresh-prices job + FMP on demand | display freshness; not used in any filing-derived metric |
| Analyst estimates / earnings calendar | 6h (`data/estimates.py`) | on-demand | consensus moves slowly; calendar shifts intraday on announcement days |
| FRED macro series | 24h | on-demand | daily series; nothing intraday |
| 13F holdings | 24h | on-demand | quarterly filings; 130-day lookback window |
| Form 4 insider activity | 24h | on-demand | filed within 2 business days; 24h is the staleness ceiling |
| FFIEC call-report schedules | quarterly | refresh-ffiec job (quarterly scheduler) | source updates quarterly |
| SOD branch/deposit data | annual data, nightly job | refresh-sod | FDIC SOD is an annual survey |
| News/events | 30min poll | poll-events job (market hours) | wire freshness |

## Staleness alarms (what fires when freshness fails)

- `validation.check_staleness`: FDIC call report older than **135 days**
  (a missed quarter) or SEC filing older than **200 days** (a missed 10-Q
  cycle) → validation finding on that bank, surfaced by the nightly job.
- Universe snapshot: served-stale events are logged with age; the nightly
  job's failure is visible in Cloud Run job history (growth gate, exit 1).
- The morning dashboard shows per-section as-of stamps (prices, rates) —
  an honest "as of" beats a hidden stale number.

## Rules

1. **Never widen a TTL to hide a fetch failure** — fix the fetch; the retry
   policy lives in `data/http.py`.
2. **A stale snapshot beats an inline rebuild** in interactive paths; only
   jobs pay multi-minute build costs.
3. **Prices are display data** — no filing-derived metric may silently mix
   a stale price into a "fundamental" number; price-dependent metrics carry
   the price timestamp.
4. New cached data sources must: stamp `cached_at`, check with
   `data/freshness.is_fresh`, document their TTL in this table.
