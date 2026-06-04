"""
Decompose the small-bank NIM-prediction error to answer one question:
is the ~42 bps RMSE *reducible model error* or an irreducible *noise floor*?

For each small bank (<$10B), over rate-active quarters (|Fed Funds change over
the lookback year| >= 100 bps), we compare three predictors of NIM(t):

  1. MODEL       — backtest_bank at deposit beta 0.50 (best from the sweep)
  2. PERSISTENCE — NIM(t) = NIM(t-4)  (the dumb "no change" benchmark; the
                   model MUST beat this to be worth anything)
  3. SMOOTHED TARGET — same model error but measured against a 3-quarter
                   centered average of actual NIMY, to strip the quarter-to-
                   quarter measurement noise in single-quarter NIMY.

We also report:
  • the per-bank RMSE distribution (p25/p50/p75) — is 42 a median or a tail?
  • median BIAS — is the error systematic (fixable by calibration) or random?
  • target NOISE — stdev of actual 1-quarter NIM changes (the irreducible floor)

Usage:
  PYTHONIOENCODING=utf-8 python -X utf8 tools/error_decomp.py
"""
from __future__ import annotations
import sys
import math
import statistics
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

MIN_RATE_MOVE = 100.0
LOOKBACK = 4
BEST_BETA = 0.50
MAX_ASSET_B = 10.0


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


def _rmse(errs: list[float]) -> float | None:
    e = [x for x in errs if x is not None]
    if not e:
        return None
    return math.sqrt(sum(x * x for x in e) / len(e))


def _safe(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def main() -> int:
    import warnings
    warnings.filterwarnings("ignore")

    from data.fdic_client import fetch_financials
    from data.fred_client import fetch_series
    from analysis.rate_sensitivity import backtest_bank

    # FRED FedFunds, monthly -> date-indexed for nearest lookup
    import pandas as pd
    ff = fetch_series("FEDFUNDS", years=12)
    ff = ff.copy()
    ff["date"] = pd.to_datetime(ff["date"])
    ff = ff.sort_values("date").reset_index(drop=True)

    def ff_at(d) -> float | None:
        if not hasattr(d, "strftime"):
            return None
        before = ff[ff["date"] <= pd.Timestamp(d)]
        return float(before.iloc[-1]["value"]) if not before.empty else None

    items = sorted(_load_universe().items())

    # Per-bank aggregate RMSEs
    model_rmse_list: list[float] = []
    persist_rmse_list: list[float] = []
    smooth_rmse_list: list[float] = []
    bias_list: list[float] = []
    noise_list: list[float] = []
    n_small = 0
    done = 0

    print(f"Decomposing small-bank (<${MAX_ASSET_B:.0f}B) NIM error, "
          f"rate-active quarters, beta={BEST_BETA}...\n", flush=True)

    for ticker, cert in items:
        done += 1
        try:
            df = fetch_financials(cert, limit=24)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        hist = df.to_dict("records")
        asset_b = (_safe(hist[0].get("ASSET")) or 0) / 1e6
        if asset_b > MAX_ASSET_B or len(hist) < 8:
            continue

        # oldest-first NIMY series with dates
        def _q(rec):
            d = rec.get("REPDTE")
            return d.strftime("%Y%m%d") if hasattr(d, "strftime") else str(d)
        hs = sorted(hist, key=_q)
        nim = [_safe(r.get("NIMY")) for r in hs]
        dates = [r.get("REPDTE") for r in hs]
        n = len(hs)

        # 1-quarter NIM-change noise (target volatility)
        qoq = [nim[i] - nim[i - 1] for i in range(1, n)
               if nim[i] is not None and nim[i - 1] is not None]
        if len(qoq) >= 3:
            noise_list.append(statistics.pstdev(qoq))

        # Persistence + smoothed-target errors over rate-active scored quarters
        persist_errs: list[float] = []
        smooth_errs_model: list[float] = []
        scored_idx: list[int] = []
        for i in range(LOOKBACK, n):
            fb, ft = ff_at(dates[i - LOOKBACK]), ff_at(dates[i])
            if fb is None or ft is None:
                continue
            if abs(ft - fb) * 100 < MIN_RATE_MOVE:
                continue
            a = nim[i]
            base = nim[i - LOOKBACK]
            if a is None or base is None or a <= 0:
                continue
            persist_errs.append(base - a)  # persistence predicts a = base
            scored_idx.append(i)

        # Model errors via backtest_bank (same filter); also smoothed-target
        try:
            bt = backtest_bank(
                hist, beta_mode="custom", custom_deposit_beta=BEST_BETA,
                min_abs_rate_change_bps=MIN_RATE_MOVE,
            )
        except Exception:
            bt = None
        if not bt or not bt.get("predicted_nim_pct"):
            continue

        n_small += 1
        if bt.get("rmse_bps") is not None:
            model_rmse_list.append(bt["rmse_bps"])
        if bt.get("bias_bps") is not None:
            bias_list.append(bt["bias_bps"])

        pr = _rmse(persist_errs)
        if pr is not None:
            persist_rmse_list.append(pr * 100)  # pp -> bps

        # Smoothed target: rebuild model error vs centered 3Q avg of actual.
        # Use backtest's own quarter list + predicted; map back to indices.
        preds = bt["predicted_nim_pct"]
        acts = bt["actual_nim_pct"]
        # Align: backtest scored the same rate-active quarters in order.
        sm_errs = []
        for k, i in enumerate(scored_idx[:len(preds)]):
            lo, hi = max(0, i - 1), min(n - 1, i + 1)
            window = [nim[j] for j in range(lo, hi + 1) if nim[j] is not None]
            if not window:
                continue
            smooth_actual = sum(window) / len(window)
            sm_errs.append(preds[k] - smooth_actual)
        sr = _rmse(sm_errs)
        if sr is not None:
            smooth_rmse_list.append(sr * 100)

        if done % 25 == 0:
            print(f"  {done}/{len(items)} (small tested={n_small})", flush=True)

    # ── Report ───────────────────────────────────────────────────────────
    def _pct(vals, p):
        v = sorted(x for x in vals if x is not None)
        if not v:
            return float("nan")
        idx = min(len(v) - 1, int(p / 100 * len(v)))
        return v[idx]

    print(f"\nSmall banks tested: {n_small}")
    print("=" * 64)
    print(f"{'predictor':<22}{'p25':>9}{'median':>9}{'p75':>9}{'mean':>9}")
    print("-" * 64)

    def _row(label, vals):
        v = [x for x in vals if x is not None]
        if not v:
            print(f"{label:<22}  (no data)")
            return
        print(f"{label:<22}{_pct(v,25):>9.1f}{_pct(v,50):>9.1f}"
              f"{_pct(v,75):>9.1f}{statistics.mean(v):>9.1f}")

    _row("MODEL RMSE (bps)", model_rmse_list)
    _row("PERSISTENCE RMSE", persist_rmse_list)
    _row("MODEL vs SMOOTHED", smooth_rmse_list)
    print("-" * 64)
    _row("model BIAS (bps)", bias_list)
    _row("target NOISE 1Q std", [x * 100 for x in noise_list])
    print("=" * 64)

    mm = statistics.median([x for x in model_rmse_list if x is not None]) \
        if model_rmse_list else float("nan")
    pm = statistics.median([x for x in persist_rmse_list if x is not None]) \
        if persist_rmse_list else float("nan")
    sm = statistics.median([x for x in smooth_rmse_list if x is not None]) \
        if smooth_rmse_list else float("nan")
    print(f"Model median {mm:.0f} bps  vs  Persistence {pm:.0f} bps  "
          f"=> model {'BEATS' if mm < pm else 'LOSES TO'} dumb benchmark "
          f"by {pm - mm:+.0f} bps")
    print(f"Against noise-smoothed target, model median = {sm:.0f} bps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
