"""
Batch backtest the NIM rate-sensitivity model across the bank universe,
broken out by ASSET SIZE so we can see how it behaves on small / community
banks (where we actually hunt for value) vs. the megabanks.

For each bank in the universe (BANK_MAP + resolved JSON):
  • pull ~24 quarters of FDIC financials
  • run analysis.rate_sensitivity.backtest_bank()
  • record directional correlation, hit-rate, levels-R², RMSE, n_quarters

Then aggregate the distribution within each asset-size bucket:
  micro   < $1B
  small   $1B – $10B   (FFIEC community-bank line is $10B)
  mid     $10B – $100B
  large   > $100B

Usage:
  PYTHONIOENCODING=utf-8 python -X utf8 tools/backtest_small_banks.py
  # optional: --limit N  (only test first N banks, for a quick smoke run)
  # optional: --max-asset 10  (only banks under $10B, skip the big ones)

No prod DB needed — FDIC + FRED are public. Securities ladders (prod-only)
are NOT applied here, so this measures the GENERIC repricing path. A second
pass with --use-ladders (prod Postgres) can layer the bank-specific ladder
in once we know the generic baseline.
"""
from __future__ import annotations
import sys
import argparse
import statistics
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _load_universe() -> dict[str, int]:
    """{ticker: fdic_cert} from BANK_MAP + resolved JSON."""
    from data.bank_mapping import BANK_MAP
    certs: dict[str, int] = {}
    for t, info in BANK_MAP.items():
        c = info.get("fdic_cert")
        if c:
            certs[t] = int(c)
    try:
        from data.bank_mapping import _RESOLVED_FROM_JSON
        for t, info in _RESOLVED_FROM_JSON.items():
            c = info.get("fdic_cert")
            if c and t not in certs:
                certs[t] = int(c)
    except Exception:
        pass
    return certs


def _connect_prod_ladders():
    """Point call_report_store at prod Postgres via Cloud SQL Connector.
    Returns the get_latest_ladder fn, or None if connection fails.
    Mirrors the proven pattern in tools/verify_ffiec_e2e.py."""
    import subprocess
    PROJECT = "ace-beanbag-486220-a8"
    SQL_INSTANCE = f"{PROJECT}:us-central1:bank-dashboard-db"
    try:
        r = subprocess.run(
            ["gcloud.cmd", "secrets", "versions", "access", "latest",
             "--secret=db-password", f"--project={PROJECT}"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  [warn] gcloud secrets failed: {r.stderr[:120]}", flush=True)
            return None
        db_pass = r.stdout.strip()

        from sqlalchemy import create_engine
        from google.cloud.sql.connector import Connector
        connector = Connector()

        def getconn():
            return connector.connect(
                SQL_INSTANCE, "pg8000",
                user="dashboard", password=db_pass, db="dashboard",
            )

        engine = create_engine("postgresql+pg8000://", creator=getconn)
        from data import call_report_store
        call_report_store._engine = engine
        call_report_store._USE_POSTGRES = True
        cov = call_report_store.coverage_summary()
        print(f"  [prod] connected — {cov.get('n_banks', '?')} ladders in DB",
              flush=True)
        return call_report_store.get_latest_ladder
    except Exception as e:
        print(f"  [warn] prod ladder connect failed: {type(e).__name__}: "
              f"{str(e)[:120]}", flush=True)
        return None


def _bucket(asset_thousands: float) -> str:
    """ASSET field is in $thousands. Return size bucket label."""
    b = (asset_thousands or 0) / 1e6  # $thousands -> $billions
    if b < 1:
        return "micro (<$1B)"
    if b < 10:
        return "small ($1-10B)"
    if b < 100:
        return "mid ($10-100B)"
    return "large (>$100B)"


def main() -> int:
    import warnings
    warnings.filterwarnings("ignore")

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="only test first N banks (0 = all)")
    ap.add_argument("--max-asset", type=float, default=0,
                    help="skip banks with assets above this many $B (0 = no cap)")
    ap.add_argument("--min-rate-move", type=float, default=0,
                    help="only score quarters where |Fed Funds change over the "
                         "lookback year| >= this many bps (0 = all quarters)")
    ap.add_argument("--use-ladders", action="store_true",
                    help="apply each bank's prod FFIEC securities ladder "
                         "(needs gcloud ADC + Cloud SQL access)")
    ap.add_argument("--floating-share", type=float, default=None,
                    help="override floating-rate loan share (0-1). Low (~0.15) "
                         "simulates a community-bank fixed-rate book = slower "
                         "asset repricing.")
    ap.add_argument("--deposit-beta", type=float, default=None,
                    help="override deposit beta (0-1). High (~0.50) simulates "
                         "small banks paying up for deposits during hikes.")
    args = ap.parse_args()

    # Optional: wire the prod call_report_securities table so backtest uses each
    # bank's real FFIEC maturity ladder instead of the generic ~29%/yr default.
    get_latest_ladder = None
    if args.use_ladders:
        get_latest_ladder = _connect_prod_ladders()

    from data.fdic_client import fetch_financials
    from analysis.rate_sensitivity import backtest_bank

    universe = _load_universe()
    items = sorted(universe.items())
    if args.limit:
        items = items[: args.limit]

    print(f"Universe: {len(universe)} banks; testing {len(items)}", flush=True)
    print("Pulling FDIC history + backtesting (generic repricing path)...\n",
          flush=True)

    # results[bucket] = list of row dicts
    from collections import defaultdict
    rows_by_bucket: dict[str, list[dict]] = defaultdict(list)
    skipped_short = 0
    skipped_fetch = 0
    skipped_bt = 0
    done = 0

    for ticker, cert in items:
        done += 1
        try:
            df = fetch_financials(cert, limit=24)
        except Exception:
            skipped_fetch += 1
            continue
        if df is None or df.empty:
            skipped_fetch += 1
            continue

        hist = df.to_dict("records")
        latest = hist[0]
        asset = latest.get("ASSET") or 0
        bucket = _bucket(asset)

        if args.max_asset and (asset / 1e6) > args.max_asset:
            continue
        if len(hist) < 8:
            skipped_short += 1
            continue

        ladder = None
        if get_latest_ladder is not None:
            try:
                ladder = get_latest_ladder(cert)
            except Exception:
                ladder = None

        try:
            bt = backtest_bank(
                hist,
                beta_mode=("custom" if args.deposit_beta is not None
                           else "historical"),
                custom_deposit_beta=args.deposit_beta,
                floating_loan_share=args.floating_share,
                securities_ladder=ladder,
                min_abs_rate_change_bps=args.min_rate_move,
            )
        except Exception:
            skipped_bt += 1
            continue
        if bt is None:
            skipped_bt += 1
            continue

        rows_by_bucket[bucket].append({
            "ticker": ticker,
            "cert": cert,
            "asset_b": asset / 1e6,
            "dir_corr": bt.get("directional_corr"),
            "hit_rate": bt.get("directional_hit_rate"),
            "r2": bt.get("r_squared"),
            "rmse_bps": bt.get("rmse_bps"),
            "bias_bps": bt.get("bias_bps"),
            "n_q": bt.get("n_quarters"),
        })

        if done % 25 == 0 or done == len(items):
            tested = sum(len(v) for v in rows_by_bucket.values())
            print(f"  {done}/{len(items)} processed "
                  f"(tested={tested} short={skipped_short} "
                  f"nofetch={skipped_fetch} btfail={skipped_bt})", flush=True)

    # ── Aggregate ────────────────────────────────────────────────────────
    def _stats(vals: list[float]) -> str:
        v = [x for x in vals if x is not None]
        if not v:
            return "n/a"
        med = statistics.median(v)
        mean = statistics.mean(v)
        return f"median={med:+.3f} mean={mean:+.3f}"

    order = ["micro (<$1B)", "small ($1-10B)", "mid ($10-100B)", "large (>$100B)"]
    print("\n" + "=" * 72)
    print("BACKTEST BY ASSET SIZE  (directional skill = the metric that matters)")
    print("=" * 72)

    for bucket in order:
        rows = rows_by_bucket.get(bucket, [])
        if not rows:
            print(f"\n{bucket}: no banks tested")
            continue
        dc = [r["dir_corr"] for r in rows]
        hr = [r["hit_rate"] for r in rows]
        r2 = [r["r2"] for r in rows]
        rmse = [r["rmse_bps"] for r in rows]
        dc_valid = [x for x in dc if x is not None]
        pos = sum(1 for x in dc_valid if x > 0)
        print(f"\n{bucket}  —  {len(rows)} banks")
        print(f"  directional corr:  {_stats(dc)}   "
              f"({pos}/{len(dc_valid)} positive)")
        print(f"  hit rate:          {_stats(hr)}")
        print(f"  levels R²:         {_stats(r2)}")
        print(f"  RMSE (bps):        {_stats(rmse)}")

    # Spotlight: worst & best small banks so we can eyeball them
    small = rows_by_bucket.get("micro (<$1B)", []) + rows_by_bucket.get("small ($1-10B)", [])
    small = [r for r in small if r["dir_corr"] is not None]
    if small:
        small.sort(key=lambda r: r["dir_corr"], reverse=True)
        print("\n" + "-" * 72)
        print("SMALL-BANK DETAIL (< $10B), best directional corr first")
        print("-" * 72)
        print(f"{'ticker':<8}{'asset$B':>8}{'dir_corr':>10}{'hit':>7}"
              f"{'R2':>8}{'rmse':>7}{'nq':>5}")
        for r in small:
            hr = f"{r['hit_rate']:.2f}" if r["hit_rate"] is not None else "  -"
            r2 = f"{r['r2']:+.2f}" if r["r2"] is not None else "   -"
            print(f"{r['ticker']:<8}{r['asset_b']:>8.1f}{r['dir_corr']:>+10.3f}"
                  f"{hr:>7}{r2:>8}{r['rmse_bps']:>7.0f}{r['n_q']:>5}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
