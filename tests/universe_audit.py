"""
Universe-wide data audit.

Runs the validation layer against EVERY bank in the universe (~480 banks)
and produces a report showing:
  - How many banks fetch successfully
  - How many have clean data (no findings)
  - How many have warnings (range violations, reconciliation gaps)
  - How many have errors (critical — wrong data)
  - A breakdown of which metrics most commonly fail

Output: universe_audit_report.csv with per-bank status + findings.
"""

from __future__ import annotations
import sys
import csv
import traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def audit_ticker(ticker: str) -> dict:
    """Run the full validation pipeline on one ticker. Returns a report row."""
    import warnings; warnings.filterwarnings("ignore")

    row = {
        "ticker": ticker,
        "status": "OK",
        "cik": None,
        "cert": None,
        "sec_fetched": False,
        "fdic_fetched": False,
        "errors": 0,
        "warnings": 0,
        "error_messages": [],
        "warning_messages": [],
        "critical_missing": [],
    }

    try:
        from data.bank_mapping import get_cik, get_fdic_cert
        from data.fdic_client import fetch_financials
        from data import sec_client
        from analysis.metrics import build_bank_metrics
        from data.validation import validate_bank_metrics

        cik = get_cik(ticker)
        cert = get_fdic_cert(ticker)
        row["cik"] = cik
        row["cert"] = cert

        if not cik and not cert:
            row["status"] = "NO_MAPPING"
            return row

        # Try FDIC
        fdic_data = {}
        fdic_hist = []
        if cert:
            try:
                df = fetch_financials(cert, limit=8)
                if not df.empty:
                    fdic_hist = df.to_dict("records")
                    fdic_data = fdic_hist[0]
                    row["fdic_fetched"] = True
            except Exception as e:
                row["warning_messages"].append(f"FDIC fetch error: {str(e)[:100]}")

        # Try SEC
        sec_data = {}
        if cik:
            try:
                sec_data = sec_client.get_latest_fundamentals(cik) or {}
                row["sec_fetched"] = bool(sec_data)
            except Exception as e:
                row["warning_messages"].append(f"SEC fetch error: {str(e)[:100]}")

        # Critical missing fields
        if cik and not row["sec_fetched"]:
            row["critical_missing"].append("SEC data (no XBRL filings found)")
        if cert and not row["fdic_fetched"]:
            row["critical_missing"].append("FDIC data (no Call Report found)")

        if not sec_data and not fdic_data:
            row["status"] = "NO_DATA"
            return row

        # Build metrics
        try:
            m = build_bank_metrics(ticker, fdic_data, sec_data, {"price": 50}, fdic_hist)
        except Exception as e:
            row["status"] = "BUILD_ERROR"
            row["error_messages"].append(f"build_bank_metrics: {type(e).__name__}: {str(e)[:150]}")
            return row

        # Validate — skip price-dependent fields because the audit uses a
        # synthetic $50 price (real prices come from IBKR at runtime, not a
        # batch audit). pe_ratio, ptbv_ratio, dividend_yield, shareholder_yield
        # all depend on market price and would be noise here.
        PRICE_DEPENDENT = {
            "pe_ratio", "ptbv_ratio", "fair_ptbv", "ptbv_discount",
            "dividend_yield", "shareholder_yield",
            "buyback_yield", "dividend_yield_sec",
            "market_cap",
        }
        metrics_no_price = {k: v for k, v in m.items() if k not in PRICE_DEPENDENT}
        findings = validate_bank_metrics(metrics_no_price, sec_data=sec_data, fdic_data=fdic_data)
        for f in findings:
            if f.severity == "error":
                row["errors"] += 1
                row["error_messages"].append(f"{f.field}: {f.message}")
            elif f.severity == "warning":
                row["warnings"] += 1
                row["warning_messages"].append(f"{f.field}: {f.message}")

        if row["errors"] > 0:
            row["status"] = "ERRORS"
        elif row["warnings"] > 0:
            row["status"] = "WARNINGS"

    except Exception as e:
        row["status"] = "CRASH"
        row["error_messages"].append(f"Top-level: {type(e).__name__}: {str(e)[:200]}")
        row["error_messages"].append(traceback.format_exc()[-400:])

    return row


def run():
    import warnings; warnings.filterwarnings("ignore")
    from data.bank_universe import get_universe_tickers
    from config import DEFAULT_WATCHLIST

    # Use the universe + watchlist (union, dedup)
    universe = set(get_universe_tickers()) | set(DEFAULT_WATCHLIST)
    tickers = sorted(universe)
    print(f"Auditing {len(tickers)} banks in parallel...")

    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(audit_ticker, t): t for t in tickers}
        done = 0
        for f in as_completed(futures):
            results.append(f.result())
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(tickers)} complete...")

    # Categorize
    by_status = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r)

    # Report
    print("\n" + "=" * 70)
    print("UNIVERSE AUDIT RESULTS")
    print("=" * 70)
    for status, items in sorted(by_status.items()):
        print(f"{status:<15} {len(items):>4} banks ({len(items)/len(results)*100:.1f}%)")

    # Most common error messages
    all_warnings = []
    for r in results:
        for msg in r["warning_messages"]:
            all_warnings.append(msg.split(":")[0])  # just the field
    from collections import Counter
    warn_counts = Counter(all_warnings)
    print("\nTop warning fields:")
    for field, count in warn_counts.most_common(10):
        print(f"  {field:<35} {count:>4}")

    all_errors = []
    for r in results:
        for msg in r["error_messages"]:
            all_errors.append(msg.split(":")[0])
    err_counts = Counter(all_errors)
    if err_counts:
        print("\nTop error fields:")
        for field, count in err_counts.most_common(10):
            print(f"  {field:<35} {count:>4}")

    # Save CSV
    out_path = Path(__file__).parent / "universe_audit_report.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "status", "cik", "cert", "sec_fetched", "fdic_fetched",
                    "errors", "warnings", "error_messages", "warning_messages", "critical_missing"])
        for r in sorted(results, key=lambda x: (x["status"], x["ticker"])):
            w.writerow([r["ticker"], r["status"], r["cik"] or "", r["cert"] or "",
                        r["sec_fetched"], r["fdic_fetched"],
                        r["errors"], r["warnings"],
                        " | ".join(r["error_messages"][:5]),
                        " | ".join(r["warning_messages"][:5]),
                        " | ".join(r["critical_missing"]),
                    ])
    print(f"\nFull report written to: {out_path}")

    return results


if __name__ == "__main__":
    run()
