"""Pre-warm the all-banks quarterly Trends grid (data/as_of_metrics.quarterly_series).

The full-universe × 20-quarter build recomputes the metric engine ~7,500 times
(~300s) — over the Cloud Run SERVICE's 300s request timeout, so the Trends view
cannot build it in a live page request. This JOB builds it (well within the 900s job
timeout) and writes the full series to the cross-instance cache under the stable
"ALLBANKS" scope id; the Trends view's all-banks path reads it (build_if_missing=
False) and is instant. One engine pass computes every TREND_METRIC, so the single
cached payload serves the whole view.

Run nightly AFTER refresh-universe so the warmed cohort matches the current universe.
"""
import sys
import time


def main() -> int:
    from data.bank_universe import get_universe_tickers
    from data.bank_mapping import get_fdic_cert, get_cik
    from data.as_of_metrics import quarterly_series
    from data.sec_per_share import sec_per_share_grid

    tickers = sorted(get_universe_tickers())
    cert_to_id: dict[int, str] = {}
    cik_to_id: dict[int, str] = {}
    for t in tickers:
        c = get_fdic_cert(t)
        if c:
            cert_to_id[int(c)] = t
        k = get_cik(t)
        if k:
            cik_to_id[int(k)] = t

    rc = 0

    # 1) FDIC fundamentals grid (the heavy ~300s build).
    print(f"▶ Warming all-banks FDIC Trends grid — {len(cert_to_id)} banks × 20q",
          flush=True)
    t0 = time.time()
    data = quarterly_series(cert_to_id, 20, build_if_missing=True, scope_id="ALLBANKS")
    if data and data.get("rows"):
        print(f"✓ FDIC grid: {len(data['rows'])} banks in {time.time() - t0:.0f}s",
              flush=True)
    else:
        print("✗ FDIC grid returned no rows", flush=True)
        rc = 1

    # 2) SEC per-share grid (companyfacts per CIK; TBV/share, book value/share).
    print(f"▶ Warming all-banks SEC per-share grid — {len(cik_to_id)} banks × 20q",
          flush=True)
    t0 = time.time()
    sec = sec_per_share_grid(cik_to_id, 20, build_if_missing=True, scope_id="ALLBANKS")
    if sec and sec.get("rows"):
        print(f"✓ SEC per-share grid: {len(sec['rows'])} banks in "
              f"{time.time() - t0:.0f}s", flush=True)
    else:
        print("✗ SEC per-share grid returned no rows", flush=True)
        rc = 1

    return rc


if __name__ == "__main__":
    sys.exit(main())
