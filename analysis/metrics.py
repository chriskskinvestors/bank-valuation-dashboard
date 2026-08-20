"""
Assembles a unified metrics dict for each bank from all data sources.

Reads the metric registry in config.py and pulls values from the
appropriate source (fdic_data, sec_data, price_data, or computed).
"""

from config import METRICS
from analysis.valuation import compute_all_valuations, _infer_quarter, _annualize_ytd


# FDIC income-statement fields are YTD cumulative within the calendar year.
# We annualize these when displaying as "annual" dollar values in screens.
# IMPORTANT: FDIC "NIM" field = NET INTEREST INCOME (dollars, YTD). The NIM
# RATIO is "NIMY" — which is already annualized as a %, do not annualize.
FDIC_YTD_INCOME_FIELDS = {
    "NETINC", "INTINC", "EINTEXP", "NIM", "NONII", "NONIX",
    "ELNATR", "PTAXNETINC", "ITAX",
}


def build_bank_metrics(
    ticker: str,
    fdic_data: dict,
    sec_data: dict,
    price_data: dict,
    fdic_hist: list[dict] | None = None,
) -> dict:
    """
    Build the full set of metrics for a single bank.

    Returns {metric_key: value} for every metric in the registry.
    """
    # Compute derived valuations first (pass ticker so SEC-sourced capital
    # return metrics can look up CIK)
    computed = compute_all_valuations(price_data, sec_data, fdic_data, fdic_hist, ticker=ticker)

    result = {"ticker": ticker}

    for m in METRICS:
        key = m["key"]
        source = m["source"]

        if source == "fdic":
            field = m.get("fdic_field")
            val = fdic_data.get(field) if field else None

            # Step 1: annualize YTD income-statement fields BEFORE unit conversion
            # so displayed values reflect full-year-equivalent regardless of quarter.
            if field in FDIC_YTD_INCOME_FIELDS and val is not None:
                quarter = _infer_quarter(fdic_data.get("REPDTE"))
                val = _annualize_ytd(val, quarter)

            # Step 2: FDIC dollar amounts are in thousands — convert to raw dollars
            FDIC_THOUSANDS_FIELDS = {
                "ASSET", "DEP", "LNLSNET", "LNLSGR", "SC", "CHBAL", "EQTOT",
                "INTAN", "ORE", "COREDEP", "DEPINS", "DEPUNINS", "DEPDOM",
                "DEPNIDOM", "DEPIDOM", "DEPLGAMT", "DEPSMAMT",
                "NETINC", "NIM", "NONII", "NONIX", "LIAB", "FREPO", "TRADE",
                "LNRE", "LNRERES", "LNRENRES", "LNRENROW", "LNRENROT",
                "LNREMULT", "LNRECONS", "LNREAG", "LNCI", "LNCON",
                "LNAUTO", "LNCRCD", "LNAG",
                "BRO", "DDT", "NTRSMMDA",
                "SCAF", "SCHA", "SCUST", "SCAGE", "SCUSO", "SCMUNI",
                "SCABS", "IGLSEC", "SCSNHAA",
                "P3LNLS", "P9LNLS",
                "INTINC", "EINTEXP", "ELNATR", "PTAXNETINC", "ITAX",
            }
            if field in FDIC_THOUSANDS_FIELDS and val is not None:
                val = val * 1000  # FDIC reports in thousands
        elif source == "sec":
            concept = m.get("sec_concept")
            val = sec_data.get(concept) if concept else None
        elif source == "ibkr":
            val = price_data.get(key)
        elif source == "computed":
            val = computed.get(key)
        else:
            val = None

        result[key] = val

    # Diagnostic passthrough (NOT config.py columns — tables render only
    # declared columns): the release-vs-reconstruction conflict flags must
    # reach validation so the nightly gate alerts on them (FSUN class: one of
    # the two numbers IS wrong). See analysis/valuation._resolve_tbvps/_bvps.
    result["tbvps_conflict"] = computed.get("tbvps_conflict")
    result["bvps_conflict"] = computed.get("bvps_conflict")
    # eps siblings (release-first increment 2): eps_conflict feeds the same
    # validation loop; eps_source labels the bank-detail EPS row
    # ("release_ttm" = release-anchored composite TTM,
    #  "reconstructed" = the XBRL TTM from data/sec_client).
    result["eps_source"] = computed.get("eps_source")
    result["eps_conflict"] = computed.get("eps_conflict")
    # efficiency_release rides as a declared column; its quarter-end tags
    # along so the release figure's staleness is visible (increment 3 —
    # no conflict flag by design: holdco vs bank-sub are different bases).
    result["efficiency_release_qend"] = computed.get("efficiency_release_qend")

    return result


def build_all_bank_metrics(
    watchlist: list[str],
    fdic_all: dict[str, dict],
    sec_all: dict[str, dict],
    prices_all: dict[str, dict],
    fdic_hist_all: dict[str, list[dict]] | None = None,
) -> list[dict]:
    """
    Build metrics for all banks in the watchlist.

    Returns a list of dicts (one per bank), suitable for DataFrame construction.
    """
    fdic_hist_all = fdic_hist_all or {}
    rows = []
    # Per-bank wall clock. The refresh-home-snapshot build has sat at a ~1618s
    # median across 24 consecutive runs (2026-08-02) while the one measured
    # warm-cache run finished in 201s, and TWO fixes aimed at the presumed
    # bottleneck moved it not at all. Rather than guess a third time, report
    # where the time actually goes: the slowest banks by name, and the frontier
    # fast-path/fallback split that says whether the events-store route engages.
    import time as _t
    per_bank: list[tuple[float, str]] = []
    t_start = _t.time()
    for ticker in watchlist:
        fdic = fdic_all.get(ticker, {})
        sec = sec_all.get(ticker, {})
        price = prices_all.get(ticker, {})
        fdic_hist = fdic_hist_all.get(ticker, [])
        _t0 = _t.time()
        row = build_bank_metrics(ticker, fdic, sec, price, fdic_hist)
        per_bank.append((_t.time() - _t0, ticker))
        rows.append(row)

    try:
        total = _t.time() - t_start
        per_bank.sort(reverse=True)
        slowest = ", ".join(f"{tk} {s:.1f}s" for s, tk in per_bank[:10])
        over_1s = sum(1 for s, _ in per_bank if s >= 1.0)
        top10 = sum(s for s, _ in per_bank[:10])
        print(f"[metrics] {len(per_bank)} banks in {total:.0f}s | "
              f"{over_1s} took >=1s | slowest 10 = {top10:.0f}s "
              f"({top10 / total * 100:.0f}% of total)", flush=True)
        print(f"[metrics] slowest: {slowest}", flush=True)
        from data.release_metrics import FRONTIER_STATS
        print(f"[metrics] frontier path: {dict(FRONTIER_STATS)}", flush=True)
    except Exception as e:                      # never let telemetry break a build
        print(f"[metrics] timing report failed: {type(e).__name__}: {e}",
              flush=True)
    return rows
