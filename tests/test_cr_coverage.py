"""
Company-Reported coverage baseline gate (COMPANY-REPORTED-PLAN.md Phase 5).

Measures every Company-Reported multi-year extractor over the fixed diverse
sample in tools/cr_coverage_report.py and compares per-bank results against
the checked-in baseline (tests/cr_coverage_baseline.json). The gate FAILS on
a coverage regression:
  - any extractor raising on any bank (ERROR is always a bug), or
  - a bank whose class rank dropped vs baseline
    (OK-multiyear > OK-single > EMPTY).
Banks EMPTY at baseline are the explained residual — genuine non-disclosure
(single-segment banks, no fair-value rollup, ...) or a known parser gap
recorded in the plan doc §5 — and do not fail the gate. Banks or functions
absent from the baseline (sample/extractor additions) are noted, never
failed: re-record to adopt them.

Run:      python tests/test_cr_coverage.py             (compare vs baseline)
Re-pin:   python tests/test_cr_coverage.py --record    (write a new baseline;
          refuses while any extractor ERRORs — fix the bug first)
Optional: --slow-sub N caps the slow extractors at the first N banks
          (a sub-sampled run compares only the banks it ran).

NOT part of unittest discovery or the deploy gate — it hits live SEC EDGAR
(fast when the local scraper cache is warm, hours cold). The comparison
logic is pure and pinned offline by tests/test_cr_coverage_gate.py.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

BASELINE_PATH = Path(__file__).parent / "cr_coverage_baseline.json"

# Coverage quality rank — a drop is a regression, a rise an improvement.
_RANK = {"OK-multiyear": 2, "OK-single": 1, "EMPTY": 0, "ERROR": 0}


def compare(baseline: dict | None, results: dict) -> tuple[list[str], list[str]]:
    """(regressions, notes) from measured `results` vs the pinned `baseline`.
    Non-empty regressions = gate fails. Pure — no I/O, no network."""
    regressions: list[str] = []
    notes: list[str] = []
    base_fns = (baseline or {}).get("functions", {})
    for fn in sorted(results):
        rows = results[fn]
        errs = sorted(t for t, r in rows.items() if r["class"] == "ERROR")
        if errs:
            regressions.append(f"{fn}: ERROR on {', '.join(errs)}")
        base_classes = (base_fns.get(fn) or {}).get("classes")
        if base_classes is None:
            notes.append(f"{fn}: not in baseline — re-record to adopt")
            continue
        for t in sorted(rows):
            cls = rows[t]["class"]
            if cls == "ERROR":
                continue                        # already a regression above
            base = base_classes.get(t)
            if base is None:
                notes.append(f"{fn}: {t} new to sample ({cls})")
            elif _RANK[cls] < _RANK.get(base, 0):
                regressions.append(f"{fn}: {t} regressed {base} -> {cls}")
            elif _RANK[cls] > _RANK.get(base, 0):
                notes.append(f"{fn}: {t} improved {base} -> {cls} — "
                             "re-record to pin")
    return regressions, notes


def main(argv) -> int:
    record = "--record" in argv
    slow_sub = None
    if "--slow-sub" in argv:
        slow_sub = int(argv[argv.index("--slow-sub") + 1])
    # --universe: measure the WHOLE universe (owner directive 2026-08-19:
    # the full universe, not the 47-bank sample, is the coverage standard).
    # --from-results <path>: record/compare from a sweep's saved results JSON
    # ({"results": {fn: {ticker: {...}}}}) instead of re-measuring — a full
    # cold sweep takes hours; re-running it to pin what it just measured
    # would be pure waste.
    from_results = None
    if "--from-results" in argv:
        from_results = argv[argv.index("--from-results") + 1]

    from tools.cr_coverage_report import (SAMPLE, _print_summary,
                                          _resolve_sample, measure)
    if from_results:
        results = json.loads(Path(from_results).read_text(encoding="utf-8"))["results"]
        print(f"Loaded measured results from {from_results}")
    else:
        if "--universe" in argv:
            from data.bank_universe import get_universe_tickers
            tickers = sorted(get_universe_tickers())
        else:
            tickers = SAMPLE
        print(f"Resolving {len(tickers)} tickers ...")
        sample = _resolve_sample(tickers)
        print(f"Sample: {len(sample)} banks with CIK\n")
        results = measure(sample, slow_sub=slow_sub)
    _print_summary(results)

    if record:
        errs = sorted({t for rows in results.values()
                       for t, r in rows.items() if r["class"] == "ERROR"})
        if errs:
            print(f"\nREFUSING to record a baseline with ERRORs ({', '.join(errs)}) "
                  "— an exception is a bug to fix, not a state to pin.")
            return 1
        baseline = {
            "recorded": time.strftime("%Y-%m-%d"),
            "sample": sorted({t for rows in results.values() for t in rows}),
            "functions": {
                fn: {"classes": {t: r["class"] for t, r in sorted(rows.items())}}
                for fn, rows in results.items()},
        }
        BASELINE_PATH.write_text(
            json.dumps(baseline, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"\nBaseline recorded -> {BASELINE_PATH}")
        return 0

    if not BASELINE_PATH.exists():
        print(f"\nNo baseline at {BASELINE_PATH} — run with --record first.")
        return 1
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    regressions, notes = compare(baseline, results)

    print("\n" + "=" * 78)
    print(f"BASELINE COMPARISON (recorded {baseline.get('recorded', '?')})")
    print("=" * 78)
    for n in notes:
        print(f"  note: {n}")
    if regressions:
        print("\nCOVERAGE REGRESSIONS:")
        for r in regressions:
            print(f"  {r}")
        return 1
    print("\n✓ No coverage regression vs baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
