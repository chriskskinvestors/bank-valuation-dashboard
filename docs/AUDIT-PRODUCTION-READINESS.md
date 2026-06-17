# Production-Readiness Audit — 2026-06-13

The 2026-06-11 audit (`docs/AUDIT-2026-06-11.md`) covered **application
correctness**: math, data provenance, plausible-wrong-number guards. It was
deep on that and remains valid. It did **not** cover the **build & release
layer** — dependency management, build reproducibility, deploy safety,
rollback, monitoring. That blind spot is what let the 2026-06-13 incidents
happen (a silent-looking failure chain that took the dashboard down for
hours). This document is that missing audit: findings, what's fixed, and the
gaps that still need closing before go-live (~2026-07-24).

Severity: **P0** = blocks go-live. **P1** = fix before launch. **P2** =
hardening.

---

## What actually went wrong on 2026-06-13 (root causes, not symptoms)

1. **Unpinned dependencies (P0 — FIXED).** `requirements.txt` listed loose
   minimums (`streamlit>=1.31.0`, `pandas>=2.1.0`, …). Every container
   rebuild resolved the *newest* versions from PyPI, so the same code
   installed different libraries on different days. The stack had silently
   drifted to `pandas==3.0.3` (a major version the code was never written
   for) and `streamlit==1.58.0`.
   → **Fixed:** `requirements.txt` is now a lock — all 92 packages pinned to
   exact `==` versions captured from the production build. Builds are
   byte-for-byte reproducible.

2. **A blocking data build on the request path (P0 — FIXED).**
   `build_universe()` ran a ~6.5-minute live rebuild on a user request
   whenever the snapshot was stale; Cloud Run's 300s request timeout killed
   it before it could persist, so every cold load retried and died.
   → **Fixed:** the interactive path now serves the persisted snapshot
   whatever its age; only the nightly job rebuilds (`data/bank_universe.py`).

3. **A self-inflicted CSS regression (P1 — FIXED).** While mis-diagnosing
   the above as a Streamlit-version problem, an edit replaced a *working*
   nav-CSS rule and blanked the top nav. The version theory was wrong — the
   nav had been fine on streamlit 1.58 all day.
   → **Fixed:** reverted to the known-good rule; added an AppTest nav guard.

4. **No way to verify a deploy except "the user opens it" (P0 — OPEN).**
   Every failure on 2026-06-13 was discovered by the user, not by us. CI
   checks Python imports and headless renders, but nothing loads the *real*
   deployed page and asserts it's not visually broken. See Gap A.

5. **No rollback runbook (P1 — FIXED via this doc).** When prod was down
   there was no documented one-command rollback to the last-good revision;
   time was lost. See the Runbook below.

6. **Verification tooling that lied (process — DOCUMENTED).** The assistant's
   browser cached an old frontend/service-worker and repeatedly reported the
   nav "working" when it was broken for the user. **Rule:** a UI change is
   not verified until checked in a **fresh incognito window** (no cache, no
   service worker, no extensions). My own browser readings are corroborating
   evidence only.

---

## Fixed (2026-06-13)

| # | Item | Where |
|---|---|---|
| 1 | All 92 deps pinned to exact versions (reproducible builds) | `requirements.txt` |
| 2 | Interactive path never live-builds the universe | `data/bank_universe.py` |
| 3 | Nav CSS reverted to known-good + AppTest guard | `ui/styles.py`, `tests/test_nav_renders.py` |
| 4 | Pre-build CI gate: pyflakes + populated-branch render smoke | `.github/workflows/deploy.yml`, `tests/test_render_smoke.py` |
| 5 | Earnings calendar never blocks render (serves stale) | `data/estimates.py` |
| 6 | Warm instance — `min-instances=1` (no scale-to-zero cold-start hangs) | Cloud Run service |
| 7 | Scheduler-invoker binding guard on every deploy | `.github/workflows/deploy.yml` |

---

## Open gaps (must close before go-live)

### Gap A (P0) — Post-deploy live smoke check
Nothing today loads the *deployed* page and confirms it's healthy, so a
broken deploy reaches the user. **Action:** add a post-deploy job that drives
the live URL with a headless browser (Playwright) and asserts: page reaches
"Markets & Rates", the 8 nav tabs are present AND have visible text (non-zero
box), no Python traceback in the DOM. Fail the deploy (and alert) if not.
This is the single highest-value gap — it turns "the user finds it" into "CI
finds it." Lighter interim: a script that opens the URL post-deploy and
checks the rendered DOM for the nav tabs + absence of `Traceback`.

### Gap B (P1) — Pin the Docker base image — ✅ FIXED 2026-06-17
`Dockerfile` used `python:3.11-slim` (floating tag). A base-image refresh can
change the Python patch and OS libs under us — the same class of drift as the
deps. **Done:** pinned by digest
`python:3.11-slim@sha256:ae52c5bef62a6bdd42cd1e8dffef86b9cd284bde9427da79839de7a4b983e7ca`
(multi-arch manifest list, verified to resolve + include amd64). Re-pin
deliberately when upgrading — see the Dockerfile comment for the command.

### Gap C (P1) — Evaluate the pandas 3.0 / numpy 2.4 majors — ✅ RESOLVED 2026-06-17 (KEEP 3.0.3)
The lock froze the stack at `pandas==3.0.3`; the concern was that pandas 3.0's
breaking changes (copy-on-write default, dtype changes) could silently alter
computed values vs the pandas 2.x the code was written for. **Decision: keep
`pandas==3.0.3` (option b).** Verified by GROUND TRUTH rather than a 2.x diff —
a stronger guarantee, since the expectations are independent hand math, not
another pandas version's output:
- **80** hand-computed value tests (`tests.test_audit_regressions` +
  `tests.test_dcf_and_models`) pass on 3.0.3.
- `tests/test_metric_formulas.py` (ROATCE 4Q, fair-value chain) passes on 3.0.3.
- **Golden dataset 38/38 pass** on 3.0.3 — the full pipeline (shares, equity,
  TBVPS, NI-TTM, ROATCE × 8 banks) ties the hand-verified EDGAR pins.
- **Zero** silent-drift risk patterns in `data/` + `analysis/`: no chained
  assignment (`df[..][..] =`), no `inplace=True`, no chained `.loc`. The only
  `["x"]["y"] =` hits are dict assignments, not DataFrames.
So pandas 3.0 computes every covered value correctly; no downgrade. (numpy: pin
`==2.4.6`, both are 2.4.x patches — immaterial.) Re-run this verification before
any future pandas major bump.

### Gap D (P2) — Deploy health alerting
No alert fires when a deploy regresses or an instance crash-loops; discovery
is manual. **Action:** Cloud Monitoring alert on the service's 5xx rate and
on revision health, routed to email. Pairs with Gap A.

### Gap E (P2) — Dependency update policy
With pins, deps go stale (security). **Action:** a scheduled monthly job that
opens a PR bumping pins, which then must pass the full suite + Gap A smoke
before merge. Updates become deliberate and tested, never silent.

---

## Rollback runbook (tested commands)

When prod is broken and a fix isn't immediate, roll back first, debug second.

1. List recent revisions (newest first):
   ```
   gcloud.cmd run revisions list --service=bank-dashboard \
     --region=us-central1 --project=ace-beanbag-486220-a8 \
     --format="table(metadata.name, metadata.creationTimestamp)" \
     --sort-by="~metadata.creationTimestamp" --limit=10
   ```
2. Send 100% traffic to the last-known-good revision:
   ```
   gcloud.cmd run services update-traffic bank-dashboard \
     --region=us-central1 --project=ace-beanbag-486220-a8 \
     --to-revisions=<GOOD_REVISION_NAME>=100
   ```
   This is instant (no rebuild) and reverts ALL of a bad deploy at once.
3. Re-point at latest after the fix is verified:
   ```
   gcloud.cmd run services update-traffic bank-dashboard \
     --region=us-central1 --project=ace-beanbag-486220-a8 --to-latest
   ```

Note: `gcloud.cmd` (not `gcloud`) on this machine; the operator runs these
interactively because the Workspace reauth policy blocks non-interactive
token refresh.

---

## Standing rules (added to prevent recurrence)

- **Dependencies are locked.** Never reintroduce `>=`. A version changes only
  via a deliberate, tested edit to `requirements.txt`.
- **A UI change is verified only in a fresh incognito window**, never from a
  long-lived browser tab.
- **Roll back first** when prod is down; debug on a branch, not on main.
- **Every deploy is watched to green AND smoke-checked live** (Gap A) before
  it's called done.
