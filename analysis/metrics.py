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


def bill_yield_on(bill_6m, repdte, max_gap_days: int = 7) -> float | None:
    """The 6-month Treasury yield (%) on ``repdte``: the last observation on or
    before it, at most ``max_gap_days`` earlier (weekends/holidays). None when
    the series is absent or has no observation in that window."""
    import pandas as pd
    if bill_6m is None or len(bill_6m) == 0 or repdte is None:
        return None
    try:
        day = pd.Timestamp(repdte).normalize()
    except (TypeError, ValueError):
        return None
    s = bill_6m.dropna()
    s = s[(s.index <= day) & (s.index >= day - pd.Timedelta(days=max_gap_days))]
    return float(s.iloc[-1]) if len(s) else None


def _aoci_metrics(fdic_data: dict, sec_data: dict, aoci_bank) -> dict:
    """AOCI % TCE on both balance sheets (owner 2026-10-05; HTM mark PRE-TAX).

    HoldCo: SEC AOCI at the parent-equity date ÷ parent TCE (data/sec_client —
    the same TCE tangible book uses). Bank: RC-R Part I item 3 AOCI ($K,
    strict-summed over the charter group by build_all_bank_metrics) ÷ bank TCE
    (EQTOT − INTAN, the statement-page convention). The HTM mark is the bank's
    SCHF − SCHA in both (holdcos hold HTM at the bank). Any missing input or a
    non-positive TCE → n/a."""
    def n(d, k):
        v = (d or {}).get(k)
        try:
            return None if v is None or v != v else float(v)
        except (TypeError, ValueError):
            return None
    schf, scha = n(fdic_data, "SCHF"), n(fdic_data, "SCHA")
    htm_k = (schf - scha) if (schf is not None and scha is not None) else None

    a_h, tce_h = n(sec_data, "aoci_holdco"), n(sec_data, "tce_holdco")
    ok_h = a_h is not None and tce_h is not None and tce_h > 0
    eq, intan = n(fdic_data, "EQTOT"), n(fdic_data, "INTAN")
    tce_b = (eq - intan) if (eq is not None and intan is not None) else None
    a_b = n({"v": aoci_bank}, "v")
    ok_b = a_b is not None and tce_b is not None and tce_b > 0
    return {
        "aoci_holdco_pct_tce": a_h / tce_h * 100 if ok_h else None,
        "aoci_htm_holdco_pct_tce": (a_h + htm_k * 1000) / tce_h * 100
        if (ok_h and htm_k is not None) else None,
        "aoci_gw_prior": bool((sec_data or {}).get("tce_goodwill_prior")) if ok_h else None,
        "aoci_sub_pct_tce": a_b / tce_b * 100 if ok_b else None,
        "aoci_htm_sub_pct_tce": (a_b + htm_k) / tce_b * 100
        if (ok_b and htm_k is not None) else None,
    }


def _bank_aoci_by_ticker(watchlist, fdic_all, rcr_aoci) -> dict:
    """{ticker: RC-R AOCI $K} — one store read per quarter for every bank's
    charter group; a group sums strictly (any charter missing → None)."""
    import pandas as pd
    from data.cert_group import get_cert_group
    plan: dict[str, list] = {}
    for t in watchlist:
        rep = (fdic_all.get(t) or {}).get("REPDTE")
        try:
            iso = pd.Timestamp(rep).strftime("%Y-%m-%d") if rep is not None else None
        except (TypeError, ValueError):
            iso = None
        try:
            certs = [int(c) for c in (get_cert_group(t) or [])]
        except Exception:
            certs = []
        if iso and certs:
            plan.setdefault(iso, []).append((t, certs))
    out: dict = {}
    for iso, items in plan.items():
        try:
            vals = rcr_aoci([c for _, cs in items for c in cs], iso)
        except Exception as e:
            print(f"[metrics] RC-R AOCI unavailable for {iso}: {type(e).__name__}")
            continue
        for t, cs in items:
            got = [vals.get(c) for c in cs]
            out[t] = None if any(v is None for v in got) else sum(got)
    return out


def build_bank_metrics(
    ticker: str,
    fdic_data: dict,
    sec_data: dict,
    price_data: dict,
    fdic_hist: list[dict] | None = None,
    bill_6m=None,
    aoci_bank=None,
) -> dict:
    """
    Build the full set of metrics for a single bank.

    Returns {metric_key: value} for every metric in the registry.

    ``bill_6m``: the FRED DGS6MO series (date-indexed, %) for the CD-rate-vs-
    bill spread; None leaves that one column n/a (tests, as-of mode).
    """
    # Compute derived valuations first (pass ticker so SEC-sourced capital
    # return metrics can look up CIK)
    computed = compute_all_valuations(price_data, sec_data, fdic_data, fdic_hist, ticker=ticker)
    # CD book rate less the 6M bill on the SAME quarter-end (percentage points).
    bill = bill_yield_on(bill_6m, fdic_data.get("REPDTE"))
    cd_rate = computed.get("cd_book_rate")
    computed["cd_rate_vs_6m_bill"] = (cd_rate - bill) if (
        cd_rate is not None and bill is not None) else None
    computed.update(_aoci_metrics(fdic_data, sec_data, aoci_bank))

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
    # SEC XBRL-API lag diagnostics (analysis/valuation._sec_facts_lag): the
    # bank-detail card dates its reconstructed per-share values by these.
    for key in ("sec_facts_lag", "sec_facts_as_of", "sec_filed_period",
                "sec_filed_date", "sec_filed_form", "sec_facts_overlay"):
        result[key] = computed.get(key)
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
    bill_6m=None,
    rcr_aoci=None,
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
    # RC-R AOCI (bank-level) — injected loader; None (tests) → n/a.
    bank_aoci = (_bank_aoci_by_ticker(watchlist, fdic_all, rcr_aoci)
                 if rcr_aoci is not None else {})
    for ticker in watchlist:
        fdic = fdic_all.get(ticker, {})
        sec = sec_all.get(ticker, {})
        price = prices_all.get(ticker, {})
        fdic_hist = fdic_hist_all.get(ticker, [])
        _t0 = _t.time()
        row = build_bank_metrics(ticker, fdic, sec, price, fdic_hist, bill_6m,
                                 aoci_bank=bank_aoci.get(ticker))
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
