"""
Map NIM-prediction error vs. deposit-beta for SMALL banks (<$10B).

For each small bank: fetch FDIC history ONCE, then backtest at several fixed
deposit-beta values (plus the default trailing-"historical" estimator) over the
rate-active 2022-23 quarters. Report median RMSE / R² per beta so we can see
whether the accuracy gain is a robust plateau or a fragile point-minimum.

Goal: justify (or reject) switching small banks from trailing-historical beta
to a through-the-cycle beta. 0.50 == the model's existing TEXTBOOK constant.

Usage:
  PYTHONIOENCODING=utf-8 python -X utf8 tools/beta_curve.py
  # --limit N for a quick smoke; --max-asset to change the small-bank cutoff
"""
from __future__ import annotations
import sys
import argparse
import statistics
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

BETA_GRID = [None, 0.30, 0.35, 0.40, 0.45, 0.50, 0.60]  # None = trailing-historical
MIN_RATE_MOVE = 100  # rate-active quarters only


def _load_universe() -> dict[str, int]:
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


def main() -> int:
    import warnings
    warnings.filterwarnings("ignore")

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-asset", type=float, default=10.0,
                    help="small-bank cutoff in $B (default 10)")
    args = ap.parse_args()

    from data.fdic_client import fetch_financials
    from analysis.rate_sensitivity import backtest_bank

    items = sorted(_load_universe().items())
    if args.limit:
        items = items[: args.limit]

    # rmse_by_beta[beta_key] = list of per-bank rmse
    rmse_by_beta: dict[str, list[float]] = {str(b): [] for b in BETA_GRID}
    r2_by_beta: dict[str, list[float]] = {str(b): [] for b in BETA_GRID}
    n_small = 0
    done = 0

    print(f"Sweeping beta {BETA_GRID} on small banks (<${args.max_asset:.0f}B), "
          f"rate-active quarters only...\n", flush=True)

    for ticker, cert in items:
        done += 1
        try:
            df = fetch_financials(cert, limit=24)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        hist = df.to_dict("records")
        asset_b = (hist[0].get("ASSET") or 0) / 1e6
        if asset_b > args.max_asset or len(hist) < 8:
            continue
        n_small += 1

        for b in BETA_GRID:
            try:
                bt = backtest_bank(
                    hist,
                    beta_mode=("custom" if b is not None else "historical"),
                    custom_deposit_beta=b,
                    min_abs_rate_change_bps=MIN_RATE_MOVE,
                )
            except Exception:
                bt = None
            if not bt:
                continue
            if bt.get("rmse_bps") is not None:
                rmse_by_beta[str(b)].append(bt["rmse_bps"])
            if bt.get("r_squared") is not None:
                r2_by_beta[str(b)].append(bt["r_squared"])

        if done % 25 == 0:
            print(f"  {done}/{len(items)} processed (small tested={n_small})",
                  flush=True)

    print(f"\nSmall banks tested: {n_small}")
    print("=" * 60)
    print(f"{'beta':>14}{'median RMSE':>14}{'mean RMSE':>12}{'median R2':>12}{'n':>6}")
    print("-" * 60)
    for b in BETA_GRID:
        rl = rmse_by_beta[str(b)]
        r2l = r2_by_beta[str(b)]
        if not rl:
            continue
        label = "historical" if b is None else f"{b:.2f}"
        print(f"{label:>14}{statistics.median(rl):>14.1f}"
              f"{statistics.mean(rl):>12.1f}"
              f"{statistics.median(r2l):>12.2f}{len(rl):>6}")
    print("=" * 60)
    print("Lower RMSE = better magnitude accuracy. 0.50 == model's TEXTBOOK beta.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
