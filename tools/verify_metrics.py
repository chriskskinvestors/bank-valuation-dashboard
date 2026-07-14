"""
Independent ground-truth verification harness.

For each bank, re-derives every key metric DIRECTLY from the primary FDIC /
SEC fields using a minimal, self-contained reference implementation — then
diffs it against what the dashboard's build_bank_metrics() actually produces.
Any divergence beyond tolerance is reported.

The point is independence: this file deliberately does NOT import
analysis.valuation / analysis.metrics compute helpers for its oracle. If a
bug ever creeps into those (a unit slip, a wrong field, a scaling error like
the FFIEC thousands bug), the dashboard value and this oracle will disagree
and the divergence lights up.

Checks per bank:
  • FDIC passthrough ratios  — nim, roaa, npl, cet1, total/leverage capital
      → dashboard value must equal the raw FDIC field (no scaling)
  • FDIC dollar fields       — total_assets/deposits/equity/loans
      → dashboard value must equal raw FDIC field × 1000 (thousands→USD)
  • Computed (price/SEC)     — market_cap, pe, ptbv, dividend_yield, roatce_holdco
      → re-derived from price + raw SEC fields

Usage:
  PYTHONIOENCODING=utf-8 python -X utf8 tools/verify_metrics.py            # watchlist sample
  PYTHONIOENCODING=utf-8 python -X utf8 tools/verify_metrics.py --all      # full universe
  PYTHONIOENCODING=utf-8 python -X utf8 tools/verify_metrics.py AAPL JPM   # explicit tickers

Exit codes: 0 = all within tolerance, 1 = divergences found, 2 = harness error.
"""
from __future__ import annotations
import sys
import csv
import warnings
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
warnings.filterwarnings("ignore")

# Relative tolerance for computed/dollar metrics; absolute for ratio passthroughs.
REL_TOL = 0.005      # 0.5%
ABS_TOL_RATIO = 0.01  # 1 bp on a percentage value
# FMP cross-check is a different professional source whose TTM window /
# restatement timing can legitimately differ from ours by a bit, so only
# MATERIAL disagreement (a real derivation gap) trips this.
FMP_REL_TOL = 0.05   # 5%

# FDIC passthrough ratio fields: dashboard metric key → raw FDIC field.
FDIC_RATIO_PASSTHROUGH = {
    "nim": "NIMY",
    "roaa": "ROA",
    "npl_ratio": "NCLNLSR",
    "cet1_ratio": "IDT1CER",
    "total_capital_ratio": "RBCRWAJ",
    "leverage_ratio": "RBCT1JR",
    "efficiency_ratio": "EEFFR",
}

# FDIC dollar fields (reported in thousands): metric key → raw FDIC field.
FDIC_DOLLAR_FIELDS = {
    "total_assets": "ASSET",
    "total_deposits": "DEP",
    "total_equity": "EQTOT",
    "total_loans": "LNLSNET",
}


def _rel_close(a: float, b: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom <= REL_TOL


def _abs_close(a: float, b: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= ABS_TOL_RATIO


def _oracle(fdic: dict, sec: dict, price: float | None) -> dict:
    """Minimal, independent re-derivation of each metric from primary fields."""
    out: dict[str, float | None] = {}

    # FDIC passthrough ratios — value should equal the raw field as-is.
    for key, field in FDIC_RATIO_PASSTHROUGH.items():
        out[key] = fdic.get(field)

    # FDIC dollar fields — thousands → USD.
    for key, field in FDIC_DOLLAR_FIELDS.items():
        v = fdic.get(field)
        out[key] = v * 1000 if v is not None else None

    # SEC-derived inputs.
    equity = sec.get("book_value_total")
    gw = sec.get("goodwill") or 0
    intang = sec.get("intangibles") or 0
    shares = sec.get("shares_outstanding")
    ni = sec.get("net_income")
    eps = sec.get("eps")
    dps = sec.get("dividends_per_share")

    # Tangible book uses the robust intangible adjustment resolved by
    # sec_client (goodwill + other-intangibles across alternate XBRL concepts).
    # The independent TBV check is the FMP cross-check below, not a naive
    # re-derivation that would miss the same concepts.
    tbvps = sec.get("tangible_book_value_per_share")
    adj = sec.get("intangible_adjustment")
    tce = (equity - adj) if (equity is not None and adj is not None) else None

    # Computed metrics.
    out["market_cap"] = price * shares if (price and shares) else None
    out["pe_ratio"] = (price / eps) if (price and eps and eps > 0) else None
    # dps is None-checked, NOT truthiness-checked: a bank that DECLARES $0
    # dividends (KFFB tags 0 in every 10-Q) has a real 0.0% yield — `dps and`
    # collapsed declared-zero into "no data" and the nightly gate flagged the
    # dashboard's honest 0.0 as a divergence (2026-07-13 alert).
    out["dividend_yield"] = ((dps / price) * 100
                             if (price is not None and dps is not None
                                 and price > 0) else None)

    # ROATCE — house COMMON basis (owner call 2026-07-07, audit #24b): NI
    # available to common ÷ (common equity − intangible adjustment). Re-derived
    # here from the primary fields so it still catches wiring bugs, but on the
    # SAME convention the dashboard displays — the old total-NI ÷ total-equity
    # oracle diverged on 116 banks purely by convention after the fix.
    # Preferred present but unresolved → None (the dashboard renders n/a;
    # _rel_close treats None==None as OK).
    pref_present = sec.get("preferred_present")
    pref = sec.get("preferred_stock")
    ni_common = sec.get("net_income_to_common_ttm")
    if pref_present and pref is None:
        out["roatce_holdco"] = None
    else:
        num = ni_common if ni_common is not None else (None if pref_present else ni)
        common_eq = (equity - (pref or 0)) if equity is not None else None
        tce_common = (common_eq - adj) if (common_eq is not None and adj is not None) else None
        out["roatce_holdco"] = (num / tce_common * 100) if (
            num is not None and tce_common and tce_common > 0) else None

    # P/TBV moves to the WARN tier in verify_ticker: the dashboard prefers the
    # bank's own REPORTED TBVPS (Company-Reported principle) while this oracle
    # reconstructs from XBRL — a legitimate reported-vs-reconstructed gap, so
    # material drift is surfaced, not failed.
    out_warn = {"ptbv_ratio": (price / tbvps) if (price and tbvps and tbvps > 0) else None}

    return out, out_warn


def verify_ticker(ticker: str) -> dict:
    """Build dashboard metrics + independent oracle, return list of divergences."""
    from data.bank_mapping import get_cik, get_fdic_cert
    from data import sec_client, fdic_client, fmp_client
    from analysis.metrics import build_bank_metrics

    row = {"ticker": ticker, "status": "OK", "divergences": [], "note": ""}
    cik = get_cik(ticker)
    cert = get_fdic_cert(ticker)
    if not cik and not cert:
        row["status"] = "NO_MAPPING"
        return row

    fdic, fdic_hist, sec = {}, [], {}
    try:
        if cert:
            df = fdic_client.fetch_financials(cert, limit=8)
            if not df.empty:
                fdic_hist = df.to_dict("records")
                fdic = fdic_hist[0]
        if cik:
            sec = sec_client.get_latest_fundamentals(cik) or {}
    except Exception as e:
        row["status"] = "FETCH_ERROR"
        row["note"] = f"{type(e).__name__}: {str(e)[:80]}"
        return row

    if not fdic and not sec:
        row["status"] = "NO_DATA"
        return row

    price = None
    try:
        q = fmp_client.get_quote(ticker)
        price = q.get("price")
    except Exception:
        pass

    try:
        dash = build_bank_metrics(ticker, fdic, sec, {"price": price}, fdic_hist)
    except Exception as e:
        row["status"] = "BUILD_ERROR"
        row["note"] = f"{type(e).__name__}: {str(e)[:80]}"
        return row

    oracle, oracle_warn = _oracle(fdic, sec, price)

    for key, oref in oracle.items():
        dval = dash.get(key)
        is_ratio = key in FDIC_RATIO_PASSTHROUGH
        ok = _abs_close(dval, oref) if is_ratio else _rel_close(dval, oref)
        if not ok:
            row["divergences"].append({
                "metric": key,
                "dashboard": dval,
                "oracle": oref,
            })

    # ── WARN tier: cross-SOURCE / cross-CONVENTION drift ─────────────────
    # These compare against a DIFFERENT source or basis, so disagreement is a
    # look-at-me signal, not a derivation failure — they never fail the job
    # (2026-07-09: the hard exit-1 on FMP-convention drift made the nightly
    # alert cry wolf; red must mean a real regression).
    row["warnings"] = []

    def _warn(label, ours, other):
        if ours is None or other is None:
            return
        denom = max(abs(ours), abs(other), 1e-9)
        if abs(ours - other) / denom > FMP_REL_TOL:
            row["warnings"].append({"metric": label, "dashboard": ours,
                                    "oracle": other})

    # Reported-vs-reconstructed P/TBV (dashboard prefers the bank's own
    # reported TBVPS; the oracle reconstructs from XBRL).
    _warn("ptbv_ratio", dash.get("ptbv_ratio"), oracle_warn.get("ptbv_ratio"))

    # Third source: FMP's pre-computed TTM fundamentals. These isolate OUR
    # SEC-derivation — TTM-EPS, goodwill/intangible handling, share count —
    # against a pro source. FMP's tangible book is TOTAL-basis (no preferred
    # subtraction), so it's compared against our total-basis reconstruction,
    # not the displayed common-basis TBVPS.
    try:
        from data import fmp_client
        fmp = fmp_client.get_fundamentals(ticker)
    except Exception:
        fmp = {}
    if fmp:
        equity = sec.get("book_value_total")
        adj = sec.get("intangible_adjustment")
        shares = sec.get("shares_outstanding")
        tbvps_total = ((equity - adj) / shares
                       if (equity is not None and adj is not None and shares)
                       else None)
        _warn("fmp:tbvps", tbvps_total, fmp.get("tbvps"))
        _warn("fmp:pe_ratio", dash.get("pe_ratio"), fmp.get("pe_ratio"))
        _warn("fmp:dividend_yield", dash.get("dividend_yield"),
              fmp.get("dividend_yield"))

    if row["divergences"]:
        row["status"] = "DIVERGENCE"
    elif row["warnings"]:
        row["status"] = "WARN"
    return row


def run(tickers: list[str]) -> int:
    print(f"Verifying {len(tickers)} banks against independent oracle "
          f"(rel tol {REL_TOL*100:.1f}%, ratio tol {ABS_TOL_RATIO})...")
    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(verify_ticker, t): t for t in tickers}
        done = 0
        for f in as_completed(futs):
            try:
                results.append(f.result())
            except Exception as e:
                results.append({"ticker": futs[f], "status": "CRASH",
                                "divergences": [], "note": str(e)[:120]})
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(tickers)}...", flush=True)

    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    diverged = [r for r in results if r["status"] == "DIVERGENCE"]
    warned = [r for r in results if r.get("warnings")]

    print("\n" + "=" * 68)
    print("METRIC VERIFICATION RESULTS")
    print("=" * 68)
    for status, n in sorted(by_status.items()):
        print(f"  {status:<14} {n:>4}")

    if diverged:
        # Tally which metrics diverge most often.
        from collections import Counter
        metric_counts = Counter(
            d["metric"] for r in diverged for d in r["divergences"])
        print("\nMetrics diverging (count):")
        for metric, n in metric_counts.most_common():
            print(f"  {metric:<22} {n:>4}")
        print("\nSample divergences (first 15):")
        shown = 0
        for r in diverged:
            for d in r["divergences"]:
                print(f"  {r['ticker']:<6} {d['metric']:<20} "
                      f"dashboard={d['dashboard']!r}  oracle={d['oracle']!r}")
                shown += 1
                if shown >= 15:
                    break
            if shown >= 15:
                break

    if warned:
        # Cross-source / cross-convention drift — surfaced, never job-failing.
        from collections import Counter
        warn_counts = Counter(
            d["metric"] for r in warned for d in r.get("warnings", []))
        print("\nCross-source drift (WARN-only, does not fail the job):")
        for metric, n in warn_counts.most_common():
            print(f"  {metric:<22} {n:>4}")

    out_path = Path(__file__).parent.parent / "tests" / "verify_metrics_report.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "status", "tier", "metric", "dashboard", "oracle", "note"])
        for r in sorted(results, key=lambda x: (x["status"], x["ticker"])):
            wrote = False
            for d in r["divergences"]:
                w.writerow([r["ticker"], r["status"], "FAIL", d["metric"],
                            d["dashboard"], d["oracle"], r.get("note", "")])
                wrote = True
            for d in r.get("warnings", []):
                w.writerow([r["ticker"], r["status"], "WARN", d["metric"],
                            d["dashboard"], d["oracle"], r.get("note", "")])
                wrote = True
            if not wrote:
                w.writerow([r["ticker"], r["status"], "", "", "", "", r.get("note", "")])
    print(f"\nFull report: {out_path}")

    # Only DERIVATION divergences fail the job; WARN-tier drift never does —
    # the nightly alert (audit #42) must mean a real regression.
    return 1 if diverged else 0


def main() -> int:
    args = [a for a in sys.argv[1:]]
    if "--all" in args:
        from data.bank_universe import get_universe_tickers
        from config import DEFAULT_WATCHLIST
        tickers = sorted(set(get_universe_tickers()) | set(DEFAULT_WATCHLIST))
    elif args:
        tickers = [a.upper() for a in args]
    else:
        from config import DEFAULT_WATCHLIST
        tickers = sorted(DEFAULT_WATCHLIST)
    try:
        return run(tickers)
    except Exception as e:
        import traceback
        print(f"[FATAL] {type(e).__name__}: {e}")
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
