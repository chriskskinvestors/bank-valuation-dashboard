# Guard dependency review — 2026-07-22 (pre-launch)

Prompted by two verification-layer failures in two days: a transient FDIC 429
killed a whole nightly rebuild (4aa79b2), and a transient SEC 429 made the
wrong-entity guard audit nothing while printing only `skipped` (007207b). Both
were failures of the machinery that *checks* the data, not of the data. This is
an audit of every external dependency the guard and the join gate rely on.

The question asked of each: **when this source is slow, throttled, or down,
what exactly do we lose — and can we tell?**

## Findings

| # | Dependency | Before | Severity |
|---|---|---|---|
| F1 | phase 1.5 SEC submissions | bare `requests.get`; a 429 left `sub=None`, so `_choose_cert` got no state and **silently fell back to name-only** — the exact bug the gate exists to prevent | **Critical** |
| F2 | phase 2 SEC submissions | bare `requests.get`; non-200 `continue`d, **silently dropping the bank** from the universe | High |
| F3 | guard SEC frames | raised → whole guard `skipped`; lost name/state/dup-cert too (fixed 007207b) | Critical (fixed) |
| F4 | guard per-CIK `get_filing_info` | per-bank failures silent; state-key coverage could collapse invisibly | Medium |
| F5 | `_fetch_sec_companies` | bare `requests.get`; a transient 429 fails the whole nightly | Medium |
| F6 | retry cost vs task timeout | full retry budget on a 750-call walk = ~40 min vs a 30-min timeout — the retry itself **converts a throttle into a total failure** | High |

F1 is the one that mattered most. The build makes ~450 sequential submissions
calls at ~8 req/s against SEC's 10 req/s limit, so throttling is expected, not
hypothetical — and its effect was to quietly disable the state key per ticker
while the build reported success.

## What changed

**Correctness under an unverifiable state.** `_choose_cert` no longer falls back
to "largest candidate" unconditionally. It does so only when the holdco name is
**unambiguous** (exactly one bank under it). Where 2+ same-name banks exist the
state *is* the identity, and guessing is what produced the wrong-entity corpus.
Measured: **53 of 248 phase-1 name matches (21%) are ambiguous** — CBSH alone
has four candidates in MO/MN/OK/LA. Ambiguous + unverifiable = no cert.

**Retries where they were missing** — F1, F2, F5 now use `data/http.py`'s shared
policy, so a bursty 429 costs seconds instead of correctness or coverage.

**A bounded retry budget** (`_BULK_SEC_ATTEMPTS = 2`) plus `_SecOutageBreaker`:
after 10 consecutive unreachable fetches, drop to a single attempt for the rest
of the build. Measured cost of a throttled call at 2 attempts is ~3.2s, so the
unbounded version would have blown the task timeout. Failing fast is safe here
*precisely because* of the ambiguity rule — correctness no longer depends on the
fetch succeeding, only coverage does. One success resets the streak, so bursty
throttling never trips it.

**Degradation is now loud.** Every partial state is reported:

- `[universe] WARNING: N/M phase-1 joins had NO verifiable registrant state`
- `[universe] WARNING: N/M phase-2 candidates unreachable at SEC`
- `[universe] <T>: no cert — registrant state UNVERIFIABLE and '<HCR>' has N candidate banks; refusing to guess`
- `[namehcr-guard] WARNING: STATE KEY DEGRADED — only N/M joins had a registrant state`
- guard success line names the live keys: `OK — 536 joins corroborated on name, state(534), size(291)`

The principle behind all of it: **an optional key's data source must degrade
that key only, never the whole check — and a degraded run must never look like
a clean one.**

## Verified

- Simulated total SEC outage through the real code path: ambiguous CBSH twins
  **refused**, unambiguous solo bank **kept**, per-call cost measured at 3.24s.
- Simulated frames-only outage: state key alive (MO, PA resolved), size off.
- 58 offline tests, including the breaker's trip/reset and a proof that
  tripping cannot cost correctness.

## Not addressed

- `RF` / Regions cert 12368 absent from FDIC (separate investigation).
- The guard remains **observe-only**. Baseline is ONE clean production run
  (2026-07-20); 07-21 audited nothing. Arm only after a real green streak.
