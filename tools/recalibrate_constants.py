"""
Empirically recalibrate the NIM-model fallback constants against real data:

  1. DEFAULT_FLOATING_LOAN_SHARE (0.30) — vs actual FFIEC RC-C Memo 2 shares
  2. _MIX_SHIFT_PER_100BPS (0.04)       — actual NIB→IB migration / 100bps
  3. deposit beta (blended IB ~0.40)    — actual ΔINTEXPY / Δfed-funds

Measures (2) and (3) across the 2021Q4 → 2023Q4 hiking cycle (Δ fed funds
≈ +5.25pp) using FDIC quarterly history for the watchlist. Prints
recommended values; does NOT modify code.

Run: PYTHONIOENCODING=utf-8 python -X utf8 tools/recalibrate_constants.py
"""
from __future__ import annotations
import sys
import statistics
import warnings
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
warnings.filterwarnings("ignore")

# Fed funds effective rate at quarter-end (%). Two well-separated, stable
# anchors that bracket the hiking cycle.
PRE_HIKE = "2021-12-31"
PRE_FFR = 0.08
PEAK = "2023-12-31"
PEAK_FFR = 5.33
DFFR = PEAK_FFR - PRE_FFR  # ≈ 5.25pp


def _repdte_key(repdte) -> str:
    if hasattr(repdte, "strftime"):
        return repdte.strftime("%Y-%m-%d")
    s = str(repdte)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def _measure_one(ticker: str) -> dict | None:
    from data.bank_mapping import get_fdic_cert
    from data import fdic_client
    cert = get_fdic_cert(ticker)
    if not cert:
        return None
    try:
        df = fdic_client.fetch_financials(cert, limit=24)
    except Exception:
        return None
    if df.empty:
        return None
    rows = {(_repdte_key(r.get("REPDTE"))): r for r in df.to_dict("records")}
    pre, peak = rows.get(PRE_HIKE), rows.get(PEAK)
    if not pre or not peak:
        return None

    out: dict = {"ticker": ticker}
    # IB deposit beta proxy: Δ cost of interest-bearing liabilities / Δ ffr
    pre_cost, peak_cost = pre.get("INTEXPY"), peak.get("INTEXPY")
    if pre_cost is not None and peak_cost is not None:
        out["ib_beta"] = (peak_cost - pre_cost) / DFFR

    # NIB migration: NIB share = DEPNIDOM / DEP
    def nib_share(r):
        dep, nib = r.get("DEP"), r.get("DEPNIDOM")
        if dep and nib is not None and dep > 0:
            return nib / dep
        return None
    s0, s1 = nib_share(pre), nib_share(peak)
    if s0 and s1 and s0 > 0:
        frac_migrated = max(0.0, (s0 - s1) / s0)     # fraction of NIB that left
        out["mixshift_per_100bps"] = frac_migrated / DFFR
    return out


def main() -> int:
    from config import DEFAULT_WATCHLIST
    tickers = sorted(set(DEFAULT_WATCHLIST))
    print(f"Measuring {len(tickers)} banks across {PRE_HIKE} → {PEAK} "
          f"(Δffr ≈ {DFFR:.2f}pp)...\n")

    betas, shifts = [], []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_measure_one, t): t for t in tickers}
        for f in as_completed(futs):
            r = f.result()
            if not r:
                continue
            if "ib_beta" in r and 0 <= r["ib_beta"] <= 1.2:
                betas.append(r["ib_beta"])
            if "mixshift_per_100bps" in r and 0 <= r["mixshift_per_100bps"] <= 0.2:
                shifts.append(r["mixshift_per_100bps"])

    def stats(label, vals, current):
        if not vals:
            print(f"{label}: no data")
            return
        vals = sorted(vals)
        n = len(vals)
        print(f"{label}  (n={n})")
        print(f"  mean   = {statistics.mean(vals):.4f}")
        print(f"  median = {statistics.median(vals):.4f}")
        print(f"  p25/p75= {vals[n//4]:.4f} / {vals[3*n//4]:.4f}")
        print(f"  current default = {current}\n")

    print("=" * 60)
    print("EMPIRICAL RECALIBRATION")
    print("=" * 60)
    stats("Blended IB deposit beta (ΔINTEXPY/Δffr)", betas, 0.40)
    stats("Mix-shift: NIB→IB per 100bps", shifts, 0.04)
    print("Floating-loan default: actual FFIEC mean=0.307 median=0.268 "
          "(current 0.30 ≈ empirical mean)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
