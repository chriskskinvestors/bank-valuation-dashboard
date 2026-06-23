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
    from data.bank_mapping import get_fdic_cert
    from data.as_of_metrics import quarterly_series

    tickers = sorted(get_universe_tickers())
    cert_to_id: dict[int, str] = {}
    for t in tickers:
        c = get_fdic_cert(t)
        if c:
            cert_to_id[int(c)] = t

    print(f"▶ Warming all-banks Trends grid — {len(cert_to_id)} banks × 20 quarters",
          flush=True)
    t0 = time.time()
    data = quarterly_series(cert_to_id, 20, build_if_missing=True, scope_id="ALLBANKS")
    if not data or not data.get("rows"):
        print("✗ Trends grid build returned no rows", flush=True)
        return 1
    print(f"✓ Trends grid warmed: {len(data['rows'])} banks, "
          f"{len(data['labels'])} quarters in {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
