# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Operating standard: Karpathy guidelines — always

Every change follows https://github.com/multica-ai/andrej-karpathy-skills/blob/main/skills/karpathy-guidelines/SKILL.md:

1. **Think before coding** — don't assume; surface tradeoffs and confusion; read the existing code (and its callers) before changing it.
2. **Simplicity first** — minimum code that solves the problem; nothing speculative; no new abstraction without a duplication count justifying it.
3. **Surgical changes** — touch only what you must; match existing style; remove only what your change orphaned.
4. **Goal-driven execution** — define success criteria first; every correctness fix lands with a test pinning the exact failure; loop until verified.

This platform runs real money. The cardinal rule: **never ship a plausible-wrong number** — when inputs violate preconditions, render n/a + flag, never a guess. The full audit and remediation log lives in `docs/AUDIT-2026-06-11.md`.

## Commands

```powershell
# Test suites (unittest-based; the ones CI does not run automatically)
python -m unittest tests.test_audit_regressions tests.test_dcf_and_models
python -m unittest tests.test_audit_regressions.TestTtmWindowIntegrity   # single class
# Legacy suites run as scripts and need UTF-8 on Windows (cp1252 chokes on '→')
$env:PYTHONIOENCODING='utf-8'; python tests/test_metric_formulas.py

# Golden dataset (live EDGAR fetch, ~2 min) — run after any data-pipeline change
python -m tests.golden_dataset

# Local app (preview tools use .claude/launch.json, port 8502)
python -m streamlit run app.py

# Deploy = push to main. GitHub Actions builds, deploys, syncs job images, then
# gates on tests/test_universe_coverage.py. ALWAYS watch the run to completion:
gh run watch $(gh run list --workflow "Deploy to Cloud Run" --limit 1 --json databaseId --jq '.[0].databaseId')
```

End commit messages with `Co-Authored-By:` trailer (current model name).

## Architecture

**Streamlit app on Cloud Run** (service `bank-dashboard`, project `ace-beanbag-486220-a8`, us-central1, IAP-gated to @kskinvestors.com). `app.py` is the entry script: sidebar section radio → per-section rendering. Company Analysis navigation is **data, not code**: `ui/company_nav.py` holds `COMPANY_NAV` (sections → sub-tabs) and a renderer registry; a structural test enforces they stay in sync. To add/move a company tab, edit that one module.

**Data provenance (non-negotiable):** displayed fundamentals come only from primary sources — SEC companyfacts XBRL (HoldCo) and FDIC/FFIEC (bank subsidiary). FMP supplies market prices and feeds ONLY the independent `tools/verify_metrics.py` oracle (cross-check, never display). yfinance supplies only analyst estimates, labeled as market data. SEC vs FDIC values differ structurally (HoldCo vs bank-sub) — `data/validation.py` encodes tuned bands whose purpose is catching wrong-entity joins, not accounting equality.

**Units contract:** FDIC reports $thousands; `analysis/metrics.py` converts ×1000 at the boundary. Downstream, `total_assets` etc. are ALWAYS raw dollars — never guess units from magnitude (audit bug A1). FDIC `INTAN` = total intangibles (the TCE convention used on statement pages); `INTANGW` = goodwill only.

**TTM invariant:** `data/sec_client.py` derives missing quarters from same-start YTD differences (FY − 9M = Q4) and requires 4 consecutive quarters; `net_income`/`eps` are 12-month values or None — never a single quarter (audit A21).

**Universe:** `data/bank_universe.py` discovers all publicly traded US-domiciled banks (FDIC `ACTIVE:1` × SEC tickers + SIC check + `stateOfIncorporation` US filter). The live build takes ~6.5 min, so interactive code serves a persisted snapshot (<26h fresh) that the nightly `jobs/refresh_universe` Cloud Run job rebuilds; stale snapshot is the fallback when sources fail. Curated `bank_map_resolved.json` mappings override fuzzy matches. There is no watchlist — everything is built for the full universe. Company pages are bank-agnostic: one generic template, never special-case a ticker.

**Shared infrastructure (use these, never re-add local copies):** `data/http.py` (THE retry policy), `data/db.py` (one SQLAlchemy engine), `data/freshness.py` (TTL checks), `data/loaders.py` (`load_fdic_hist`), `data/cache.py` (Postgres in prod / sqlite locally), `utils/formatting` (numeric formatters), `data/events/wire_base.is_junk_news` (one junk filter), `utils/chart_style.py` + `ui/styles.py` `:root` tokens (charts/CSS).

**Verification layers:** `tests/golden_dataset.py` (8 banks pinned to EDGAR — re-pin ONLY by hand-verifying raw companyfacts via `tools/golden_handcheck.py`, NEVER by copying pipeline output: that's circular); `data/validation.py` (range + cross-source + staleness, run per bank on every refresh); nightly growth gate in `jobs/refresh_universe` (a previously-clean bank failing validation fails the job); deploy-gate coverage test.

**FDIC API trap:** the *institutions* endpoint formats REPDTE as MM/DD/YYYY; the *financials* endpoint uses YYYYMMDD. Filtering institutions by `REPDTE:YYYYMMDD` matches zero rows silently.

## Environment & ops

- Secrets live in Google Cloud Secret Manager only — never in code or commits. `tools/verify_ffiec_e2e.py` is gitignored (hardcoded JWT) and must never be committed.
- PowerShell blocks `gcloud.ps1` — always use `gcloud.cmd`. Non-interactive shells often can't mint tokens (Workspace reauth policy); have the user run job executions interactively when needed.
- Streamlit renders `$…$` as LaTeX: escape `\$` in any `st.markdown`/`st.caption` containing two or more dollar amounts.
- Cloud Run jobs (`refresh-universe` nightly 6am ET, `refresh-sod`, `refresh-ffiec` quarterly, `poll-events`, `refresh-prices`, etc.) are auto-pinned to the deployed image by the deploy workflow.
- Never execute trades or move money.
