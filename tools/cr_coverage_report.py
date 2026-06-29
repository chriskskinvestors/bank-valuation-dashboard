"""Company-Reported pipeline coverage report (MEASURE-only, no fixes).

Runs each Company-Reported multi-year extractor over a fixed, hand-picked sample
of diverse banks and records per-bank coverage WITHOUT crashing on any single
bank (every extractor call is wrapped in try/except; errors are recorded, not
fatal). The goal is to confirm the multi-year scrapers built this session hold
up across MANY banks (so far only ABCB was verified per-tab) and to distinguish
genuine n/a (the filer didn't disclose) from parser misses.

Each function/bank is classified as:
  OK-multiyear  — returned >= 2 fiscal years (or >= 2 reconciling periods)
  OK-single     — returned exactly 1 fiscal year/period only
  EMPTY         — None / 0 periods (candidate parser miss OR genuine non-discloser)
  ERROR         — raised an exception (a real bug to fix)

For the two per-metric functions (_cr_highlights_by_year, company_asset_quality_nim)
we also record the fraction of key latest-year metrics that are non-None.

Run:  python -m tools.cr_coverage_report
This hits SEC EDGAR; the scrapers already cache, so re-runs are fast. Pass a
comma-separated ticker list as argv[1] to override the sample; pass --slow-sub N
to cap the slow extractors (statements/securities/fair-value/segments) at the
first N banks.
"""
from __future__ import annotations

import sys
import time
import traceback

# Fixed, hand-picked sample: big + small, money-center + regional, different
# filers/auditors. Order roughly large->small. Banks without a CIK are skipped.
SAMPLE = [
    # ── original 24-bank sample (2026-06-29 first run) ──
    "ABCB", "PNFP", "FFIN", "WSFS", "CBSH", "FHN", "WAL", "CFR", "ONB", "UMBF",
    "BOKF", "SNV", "HWC", "ASB", "FNB", "VLY", "WTFC", "COLB", "GBCI", "TFC",
    "FITB", "RF", "KEY", "ZION", "EWBC",
    # ── broader re-measure additions (2026-06-29 second run): megacaps,
    #    Puerto-Rico charters, thrifts/WAFD-style, small regionals, a
    #    non-December fiscal-year-end filer (FHB? no — these are all Dec;
    #    AX = Axos has a JUNE fiscal-year-end, the one off-cycle filer) ──
    "USB", "PNC", "MTB", "CMA", "WBS", "OZK", "HOMB", "FHB", "BPOP", "CADE",
    "UCBI", "TBBK", "AX", "NBHC", "FBP", "INDB", "WAFD", "BANR", "PPBI", "TCBI",
    "SFNC", "FBK", "CASH", "AUB", "FULT", "BKU", "CFG", "HBAN",
]

# Latest-year key metrics for the two per-metric functions. A non-None value
# means the metric was actually extracted for the newest fiscal year.
HL_KEY_METRICS = ["total_assets", "net_income", "roaa", "efficiency",
                  "nim", "npl_loans", "cet1"]
AQ_KEY_METRICS = ["nim", "npl_loans", "nco_loans"]


def _classify(n_periods: int) -> str:
    if n_periods >= 2:
        return "OK-multiyear"
    if n_periods == 1:
        return "OK-single"
    return "EMPTY"


# ── per-function adapters: each returns (n_periods, extra) or raises ──────────
# extra is an optional dict of side facts (e.g. metric-fill fraction) for the
# per-metric functions; None otherwise.

def _run_highlights(ticker, cik):
    from ui.financials_statements import _cr_highlights_by_year
    years, dicts, _src = _cr_highlights_by_year(ticker)
    if not years or not dicts:
        return 0, None
    latest = dicts[0]
    present = sum(1 for k in HL_KEY_METRICS if latest.get(k) is not None)
    extra = {"metric_present": present, "metric_total": len(HL_KEY_METRICS),
             "missing": [k for k in HL_KEY_METRICS if latest.get(k) is None]}
    return len(years), extra


def _run_asset_quality(ticker, cik):
    from data.sec_filing_scraper import company_asset_quality_nim
    res = company_asset_quality_nim(cik)
    if not res:
        return 0, None
    by_year = res.get("by_year", {}) or {}
    if not by_year:
        return 0, None
    latest_yr = max(by_year.keys())
    latest = by_year.get(latest_yr, {}) or {}
    present = sum(1 for k in AQ_KEY_METRICS if latest.get(k) is not None)
    extra = {"metric_present": present, "metric_total": len(AQ_KEY_METRICS),
             "missing": [k for k in AQ_KEY_METRICS if latest.get(k) is None]}
    return len(by_year), extra


def _run_statement(stype):
    def _inner(ticker, cik):
        from data.sec_statements import as_reported_statement_multiyear
        res = as_reported_statement_multiyear(cik, stype, 5)
        if not res:
            return 0, None
        periods = (res.get("statement") or {}).get("periods") or []
        return len(periods), None
    return _inner


def _run_periods_key(import_path, key):
    """Generic adapter for functions returning {key: {period: {...}}}."""
    mod_name, fn_name = import_path.rsplit(".", 1)

    def _inner(ticker, cik):
        import importlib
        fn = getattr(importlib.import_module(mod_name), fn_name)
        res = fn(cik)
        if not res:
            return 0, None
        periods = res.get(key) or {}
        return len(periods), None
    return _inner


def _run_compositions(ticker, cik):
    """compositions_for returns {"loan": {period:...}|None, "deposit": {...}|None}.
    Count the max periods across loan/deposit (either table reconciling counts)."""
    from data.sec_composition import compositions_for
    res = compositions_for(cik)
    if not res:
        return 0, None
    loan = res.get("loan") or {}
    dep = res.get("deposit") or {}
    n = max(len(loan), len(dep))
    extra = {"loan_periods": len(loan), "deposit_periods": len(dep)}
    return n, extra


# (display name, callable, slow?) — slow ones are sub-sampled when --slow-sub set.
FUNCTIONS = [
    ("_cr_highlights_by_year", _run_highlights, True),
    ("company_asset_quality_nim", _run_asset_quality, True),
    ("as_reported_statement(income)", _run_statement("income"), True),
    ("as_reported_statement(balance)", _run_statement("balance"), True),
    ("securities_multiyear_for",
     _run_periods_key("data.sec_filing_scraper.securities_multiyear_for", "securities"), True),
    ("fair_value_multiyear_for",
     _run_periods_key("data.sec_filing_scraper.fair_value_multiyear_for", "fair_value"), True),
    ("segments_multiyear_for",
     _run_periods_key("data.sec_filing_scraper.segments_multiyear_for", "segments"), True),
    ("compositions_for", _run_compositions, False),
]


def _resolve_sample(tickers):
    """Map tickers -> (ticker, cik), skipping any without a cik."""
    from data.bank_mapping import get_bank_info
    out = []
    for t in tickers:
        try:
            info = get_bank_info(t)
        except Exception:
            info = None
        cik = info.get("cik") if info else None
        if not cik:
            print(f"  [skip] {t}: no cik from get_bank_info")
            continue
        out.append((t, cik))
    return out


def main(argv):
    tickers = SAMPLE
    slow_sub = None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--slow-sub":
            slow_sub = int(argv[i + 1])
            i += 2
            continue
        if not a.startswith("--"):
            tickers = [x.strip().upper() for x in a.split(",") if x.strip()]
        i += 1

    print(f"Resolving {len(tickers)} tickers ...")
    sample = _resolve_sample(tickers)
    print(f"Sample: {len(sample)} banks with CIK -> "
          f"{', '.join(t for t, _ in sample)}\n")

    # results[fn_name][ticker] = {"class","n","extra","err"}
    results: dict[str, dict[str, dict]] = {name: {} for name, _, _ in FUNCTIONS}

    for name, fn, slow in FUNCTIONS:
        run_sample = sample
        if slow and slow_sub is not None:
            run_sample = sample[:slow_sub]
        print(f"--- {name}  (n={len(run_sample)}{' [sub-sampled]' if run_sample is not sample else ''})")
        for ticker, cik in run_sample:
            t0 = time.time()
            try:
                n, extra = fn(ticker, cik)
                cls = _classify(n)
                results[name][ticker] = {"class": cls, "n": n, "extra": extra, "err": None}
                tag = cls
                if extra and "metric_present" in extra:
                    tag += f" [{extra['metric_present']}/{extra['metric_total']} metrics]"
            except Exception as e:  # noqa: BLE001 — per-bank errors are CAUGHT, not fatal
                results[name][ticker] = {
                    "class": "ERROR", "n": 0, "extra": None,
                    "err": f"{type(e).__name__}: {e}",
                    "tb": traceback.format_exc(),
                }
                tag = f"ERROR {type(e).__name__}: {e}"
            dt = time.time() - t0
            print(f"    {ticker:6s} {tag}  ({dt:.1f}s)")
        print()

    _print_summary(results)
    return results


def _print_summary(results):
    print("=" * 78)
    print("PER-FUNCTION COVERAGE SUMMARY")
    print("=" * 78)
    header = f"{'function':<34} {'n':>3} {'multiyr':>8} {'single':>7} {'empty':>6} {'error':>6}"
    print(header)
    print("-" * len(header))
    for name, _, _ in FUNCTIONS:
        rows = results[name]
        n = len(rows)
        if n == 0:
            continue
        c = {"OK-multiyear": 0, "OK-single": 0, "EMPTY": 0, "ERROR": 0}
        for r in rows.values():
            c[r["class"]] += 1
        pct = lambda k: f"{100.0 * c[k] / n:5.0f}%"
        print(f"{name:<34} {n:>3} {pct('OK-multiyear'):>8} {pct('OK-single'):>7} "
              f"{pct('EMPTY'):>6} {pct('ERROR'):>6}")

    # ERROR banks (real bugs) per function.
    print("\n" + "=" * 78)
    print("ERROR BANKS (real bugs to fix)")
    print("=" * 78)
    any_err = False
    for name, _, _ in FUNCTIONS:
        errs = [(t, r["err"]) for t, r in results[name].items() if r["class"] == "ERROR"]
        if errs:
            any_err = True
            print(f"\n{name}:")
            for t, msg in errs:
                print(f"    {t:6s} {msg}")
    if not any_err:
        print("  (none — no extractor raised on any sampled bank)")

    # EMPTY examples per function (candidate parser-miss vs genuine non-discloser).
    print("\n" + "=" * 78)
    print("EMPTY EXAMPLES (parser-miss candidates OR genuine non-disclosers)")
    print("=" * 78)
    for name, _, _ in FUNCTIONS:
        empties = [t for t, r in results[name].items() if r["class"] == "EMPTY"]
        if empties:
            shown = empties[:8]
            more = f" (+{len(empties) - len(shown)} more)" if len(empties) > len(shown) else ""
            print(f"  {name:<34} {', '.join(shown)}{more}")

    # Metric-fill detail for the two per-metric functions: which latest-year
    # metrics are most often missing (a parser-miss fingerprint).
    print("\n" + "=" * 78)
    print("LATEST-YEAR METRIC FILL (per-metric functions)")
    print("=" * 78)
    for name in ("_cr_highlights_by_year", "company_asset_quality_nim"):
        rows = results.get(name, {})
        oks = [r for r in rows.values() if r["class"] in ("OK-multiyear", "OK-single")]
        if not oks:
            continue
        miss_count: dict[str, int] = {}
        for r in oks:
            for k in (r["extra"] or {}).get("missing", []):
                miss_count[k] = miss_count.get(k, 0) + 1
        total_present = sum((r["extra"] or {}).get("metric_present", 0) for r in oks)
        total_slots = sum((r["extra"] or {}).get("metric_total", 0) for r in oks)
        fillpct = (100.0 * total_present / total_slots) if total_slots else 0.0
        print(f"\n  {name}: overall latest-year fill {total_present}/{total_slots} "
              f"({fillpct:.0f}%) across {len(oks)} non-empty banks")
        if miss_count:
            ordered = sorted(miss_count.items(), key=lambda kv: -kv[1])
            print("    most-missing metrics: " +
                  ", ".join(f"{k}({v})" for k, v in ordered))


if __name__ == "__main__":
    main(sys.argv)
