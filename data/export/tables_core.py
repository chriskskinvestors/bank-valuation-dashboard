"""Agent dataset export — core tables: `banks`, `bank_metrics`, `events`.

Shared contract (data/export): per table a `build_<table>(tickers)` returning a
DataFrame whose columns are exactly `COLUMNS_<TABLE>` (name -> one-line
description with units + source), in dict order, `ticker` first.

READ-ONLY on persisted state. Nothing here fans out to SEC / FDIC / FMP /
Anthropic: the universe snapshot, the nightly sec_filer_status / cert-group /
successor maps, the Home metrics snapshot (`watchlist_metrics_snap`, written by
jobs/refresh_home_snapshot and jobs/refresh_universe), the SOD branch store
and the events table. Every run-to-run cache row is read with max_age_s=None.

Cardinal rule: absent stays absent. Nothing is fillna(0)'d, summed with
skipna, or defaulted with `or 0` — an unknown value is None/NaN in the frame.

Ticker scope (all three tables): each requested ticker is canonicalized to its
registrant's primary common (a preferred / ETN sibling such as VYLD maps to
JPM — the shared-registrant mis-tag class), and anything the dashboard hides
from every covered surface (non-common classes that don't remap, skip-listed
ADRs/ETNs: data.bank_universe.coverage_excluded) is dropped. One row per bank.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd


# ──────────────────────────────────────────────────────────────────────────
# Ticker scope
# ──────────────────────────────────────────────────────────────────────────

def _canonical_tickers(tickers) -> list[str]:
    """Requested tickers -> ordered, de-duplicated primary-common tickers,
    out-of-scope ones dropped (see module docstring)."""
    from data.bank_universe import coverage_excluded, get_noncommon_primary_map
    remap = get_noncommon_primary_map()
    excluded = coverage_excluded()
    out: list[str] = []
    seen: set[str] = set()
    for t in tickers or []:
        t = str(t or "").strip().upper()
        if not t:
            continue
        t = remap.get(t, t)
        if t in excluded or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _frame(rows: list[dict], columns: dict[str, str]) -> pd.DataFrame:
    """DataFrame with exactly `columns` in order. Missing keys are None —
    never a filled value."""
    return pd.DataFrame([{c: r.get(c) for c in columns} for r in rows],
                        columns=list(columns))


def _iso_date(v) -> date | None:
    """ISO date string / date / Timestamp -> datetime.date; None when absent
    or unparseable (never a guessed date)."""
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        return None


# ══════════════════════════════════════════════════════════════════════════
# 1. banks
# ══════════════════════════════════════════════════════════════════════════

COLUMNS_BANKS: dict[str, str] = {
    "ticker": "Primary common-stock ticker (canonical; preferred/ETN siblings "
              "are folded into it). Source: bank universe snapshot "
              "(data/bank_universe, rebuilt nightly by jobs/refresh_universe).",
    "name": "Holding-company (registrant) display name, normalized by "
            "utils.formatting.format_bank_name. Source: universe snapshot "
            "(SEC registrant name or curated mapping).",
    "bank_name": "Lead bank subsidiary's FDIC institution name (the brand the "
                 "bank trades under, e.g. 'Rockland Trust Company'). Source: "
                 "FDIC institutions via the universe snapshot; None when the "
                 "snapshot predates this field or there is no FDIC cert.",
    "registrant_cik": "SEC CIK of the registrant — IDENTITY only (filings "
                      "links, ownership). data.bank_mapping.get_cik: nightly "
                      "successor-registrant map > curated map > snapshot. "
                      "Present even for deregistered filers.",
    "sec_cik": "SEC CIK when it is a source of CURRENT XBRL data "
               "(data.bank_mapping.get_data_cik), else None. Deregistered / "
               "Regulation A filers (sec_filer_status not 'periodic') route "
               "like non-SEC banks and get None here.",
    "is_sec_filer": "True when sec_cik is not None, i.e. the dashboard treats "
                    "the bank's SEC XBRL as a primary data source; False = "
                    "FDIC call report + company releases only.",
    "sec_filer_status": "Nightly SEC filer status for the ticker: 'periodic' "
                        "(10-K/10-Q within ~15 months), 'deregistered', "
                        "'non-periodic' (never filed a 10-K/10-Q). None = not "
                        "in the nightly map (the dashboard then routes a CIK "
                        "as 'periodic'). Source: cache sec_filer_status:v1.",
    "fdic_cert": "FDIC certificate number of the lead bank subsidiary "
                 "(data.bank_mapping.get_fdic_cert: curated > resolved > "
                 "snapshot). None = no FDIC-insured subsidiary mapped.",
    "fdic_certs": "All ACTIVE FDIC certs under the holding company, "
                  "comma-separated, largest charter first (multi-charter "
                  "holdcos, e.g. WTFC). Single-charter banks list only "
                  "fdic_cert. None when fdic_cert is None or the nightly "
                  "charter-group map (cache cert_groups:v2) is absent. "
                  "Former (merged-away) charters are not listed.",
    "n_fdic_charters": "Count of certs in fdic_certs (integer); None when "
                       "fdic_certs is None.",
    "exchange": "Listing venue (NYSE / Nasdaq / OTC ...). Source: SEC "
                "company_tickers_exchange via the universe snapshot (curated "
                "mapping when SEC lists none).",
    "website": "Lead bank subsidiary's web address as filed with the FDIC "
               "(institutions WEBADDR). None when absent from the snapshot.",
    "main_office_city": "City of the LEAD BANK SUBSIDIARY's main office "
                        "(FDIC Summary of Deposits, branch number 0) — the "
                        "bank's home office, NOT necessarily the holding "
                        "company's headquarters. None when the SOD store has "
                        "no main-office row for fdic_cert in the serving year.",
    "main_office_state": "Two-letter state of the lead bank subsidiary's main "
                         "office (FDIC SOD, branch number 0); see "
                         "main_office_city.",
    "sod_year": "FDIC Summary of Deposits survey year (June 30 data) the "
                "main-office fields come from; None when they are None.",
}


def _cert_group_map() -> dict | None:
    """The nightly charter-group map, or None when it has never been built.
    Read directly (not get_cert_group_cached) because that helper answers
    [cert] when the map is absent — which would claim single-charter for a
    multi-charter holdco on an environment without the map."""
    from data import cache
    from data.cert_group import _GROUP_MAP_KEY
    try:
        m = cache.get(_GROUP_MAP_KEY, max_age_s=None)
    except Exception as e:
        print(f"[export] banks: charter-group map unreadable: "
              f"{type(e).__name__}: {e}")
        return None
    return m if isinstance(m, dict) else None


def _main_offices(certs: list[int]) -> tuple[int | None, dict[int, tuple]]:
    """(serving SOD year, {cert: (city, state)}) for the main office (brnum 0)
    of each cert, from the branch store — one query."""
    if not certs:
        return None, {}
    try:
        from sqlalchemy import text
        from data import branches_store
        year = branches_store.get_latest_year()
        if year is None:
            return None, {}
        with branches_store._get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT cert, city, state FROM branches "
                "WHERE brnum = 0 AND year = :y"), {"y": int(year)}).fetchall()
    except Exception as e:
        print(f"[export] banks: SOD main offices unavailable: "
              f"{type(e).__name__}: {e}")
        return None, {}
    want = {int(c) for c in certs}
    out = {}
    for cert, city, state in rows:
        if cert is not None and int(cert) in want:
            out[int(cert)] = ((city or None), (state or None))
    return int(year), out


def build_banks(tickers: list[str]) -> pd.DataFrame:
    """One row per covered bank in `tickers` (see module docstring for scope).
    A requested ticker absent from the universe snapshot is skipped (its
    identity cannot be read without a live resolution) and reported."""
    from data.bank_mapping import get_cik, get_data_cik, get_fdic_cert
    from data.bank_universe import get_universe
    from data.sec_filer_status import persisted_status
    from utils.formatting import format_bank_name

    universe = get_universe()
    scope = _canonical_tickers(tickers)
    missing = [t for t in scope if t not in universe]
    if missing:
        print(f"[export] banks: {len(missing)} requested tickers not in the "
              f"universe snapshot, skipped: {', '.join(missing[:20])}")
    scope = [t for t in scope if t in universe]

    groups = _cert_group_map()
    if groups is None:
        print("[export] banks: charter-group map absent — fdic_certs is None "
              "for every bank (never guessed as single-charter)")

    rows = []
    for t in scope:
        info = universe[t]
        cert = get_fdic_cert(t)
        cert = int(cert) if cert else None
        data_cik = get_data_cik(t)
        certs = None
        if cert is not None and groups is not None:
            certs = [int(c) for c in (groups.get(str(cert)) or [cert])]
        rows.append({
            "ticker": t,
            "name": format_bank_name(info.get("name") or t, t),
            "bank_name": info.get("bank_name") or None,
            "registrant_cik": get_cik(t),
            "sec_cik": data_cik,
            "is_sec_filer": data_cik is not None,
            "sec_filer_status": persisted_status(t),
            "fdic_cert": cert,
            "fdic_certs": ",".join(str(c) for c in certs) if certs else None,
            "n_fdic_charters": len(certs) if certs else None,
            "exchange": info.get("exchange") or None,
            "website": info.get("webaddr") or None,
        })

    year, offices = _main_offices([r["fdic_cert"] for r in rows if r["fdic_cert"]])
    for r in rows:
        city, state = offices.get(r["fdic_cert"], (None, None))
        r["main_office_city"] = city
        r["main_office_state"] = state
        r["sod_year"] = year if (city or state) else None

    df = _frame(rows, COLUMNS_BANKS)
    for c in ("registrant_cik", "sec_cik", "fdic_cert", "n_fdic_charters",
              "sod_year"):
        df[c] = df[c].astype("Int64")
    return df


# ══════════════════════════════════════════════════════════════════════════
# 2. bank_metrics — the persisted Home metrics snapshot, one row per bank
# ══════════════════════════════════════════════════════════════════════════

SNAPSHOT_KEY = "watchlist_metrics_snap"

_FDIC_BASIS = ("FDIC call report, BANK SUBSIDIARY basis (multi-charter "
               "holdcos: levels summed over the active charter group, exact "
               "ratios recomputed, average-based ratios n/a)")
_MARKET = ("market data quote (FMP via the warm price cache kept by "
           "jobs/refresh_prices; IBKR only when an interactive session built "
           "the snapshot) — never a filing figure")
_SEC_HOLDCO = "SEC XBRL companyfacts, HOLDING-COMPANY basis"

# Units of the FDIC-sourced registry metrics, by the vendored field catalog's
# KIND (data/fdic_field_catalog.json). analysis.metrics.build_bank_metrics
# converts every $K level to raw dollars (pinned per field by
# tests/test_export_core.TestFdicUnits).
_FDIC_UNIT = {
    "level": "raw US dollars (FDIC $K x 1000)",
    "ratio": "percent (1.25 = 1.25%)",
    "count": "count",
}
# Registry metrics whose unit differs from their catalog kind's default.
_FDIC_UNIT_OVERRIDE = {
    "reserve_nco_coverage": "multiple (x) — earnings coverage of net charge-offs",
    "assets_per_employee": "raw US dollars of assets per full-time employee "
                           "(FDIC ASTEMPM is $ millions; the export multiplies "
                           "by 1,000,000)",
}
# Export-side unit scaling (applied in build_bank_metrics, declared above).
_SCALE = {"assets_per_employee": 1_000_000}
# Registry keys whose NAME misdescribes the FDIC field behind them (the
# value follows the field — the description says so).
_FDIC_KEY_NOTE = {
    "allowance_loans": "NOTE: key name is historical — the value is credit-loss "
                       "PROVISION / net charge-offs, not allowance/loans.",
    "nco_to_reserve": "NOTE: key name is historical — the value is net loans "
                      "& leases / core deposits.",
    "npl_cre": "NOTE: covers ALL real-estate loans (noncurrent RE / RE "
               "loans), not CRE only.",
    "int_expense_yield": "Same FDIC field as cost_of_funds' primary input.",
}

# Hand-written descriptions for every registry metric with source "computed"
# (analysis/valuation.compute_all_valuations and helpers). A computed metric
# added to config.METRICS without an entry here fails
# tests/test_export_core.TestBankMetricsColumns.
_COMPUTED_DESC: dict[str, str] = {
    "market_cap": "Market capitalization, raw US dollars = price x "
                  "shares_outstanding (SEC cover-page count when newer than "
                  "the balance sheet, else balance-sheet count; non-SEC "
                  "filers: count from the company's own release — see "
                  "market_cap_source). Price is " + _MARKET + ".",
    "eps": "TTM diluted EPS, US dollars per share — RESOLVED value: the "
           "release-anchored composite TTM in the release-to-10-Q window, "
           "else the SEC XBRL TTM (4 derived quarters, holdco); non-SEC "
           "filers: 4-quarter sum from their own releases. See eps_source.",
    "pe_ratio": "Price / eps, multiple (x). n/a when eps <= 0 or either "
                "input is absent. Price is market data.",
    "tbvps": "Tangible book value per common share, US dollars — RESOLVED "
             "(company-reported figure when it passes the +/-15% gate, else "
             "the SEC holdco reconstruction; non-SEC filers: their wire/IR "
             "release). See tbvps_source / tbvps_conflict. n/a on a "
             "share-basis mismatch (ptbv_basis_mismatch).",
    "tbvps_source": "Provenance of tbvps: 'reported_8k' (earnings 8-K "
                    "exhibit), 'company_release' (wire/IR release), "
                    "'reconstructed' (SEC XBRL holdco reconstruction), "
                    "'reconstructed_company_shares' (reconstruction on the "
                    "company's own share basis), 'derived_no_holdco' (release "
                    "BVPS x FDIC tangible/total equity, no-holdco banks), "
                    "None = no figure.",
    "ptbv_ratio": "Price / tbvps, multiple (x); n/a when tbvps <= 0 or "
                  "absent. Price is market data.",
    "bvps": "Book value per common share, US dollars — RESOLVED with the same "
            "release-first discipline as tbvps (bvps_source / "
            "bvps_conflict).",
    "bvps_source": "Provenance of bvps; same vocabulary as tbvps_source.",
    "pb_ratio": "Price / bvps, multiple (x); n/a when bvps <= 0 or absent.",
    "dividend_yield": "Dividend yield, percent (3.1 = 3.1%) = SEC XBRL TTM "
                      "dividends declared per common share / price x 100.",
    "roatce_blended": "ROATCE input to the fair-value model, percent: the "
                      "SEC holdco TTM ROATCE (roatce_holdco) when it "
                      "resolves, else FDIC bank-sub blend 0.75 x roatce_4q + "
                      "0.25 x roatce. n/a (flagged) when a merger sits in the "
                      "holdco window.",
    "roatce_normalized": "roatce_blended x one-time-earnings factor (0.2-1.0: "
                         "each of the last 4 single-quarter NIs winsorized at "
                         "3x the trailing-8Q median; holdco NI for the holdco "
                         "figure, FDIC NETINC for the bank-sub blend), "
                         "percent.",
    "earnings_distorted": "Boolean: the one-time-earnings normalization "
                          "removed more than 15% of TTM earnings (factor < "
                          "0.85).",
    "fair_ptbv": "Model fair P/TBV, multiple (x) = (roatce_normalized - 2.5) "
                 "/ (10 - 2.5) (Gordon form, cost of equity 10%, growth "
                 "2.5%, analysis/dcf). n/a when <= 0 (model not applicable). "
                 "Dashboard model output, not a market figure.",
    "fair_price": "Model fair price, US dollars per share = fair_ptbv x "
                  "tbvps.",
    "ptbv_discount": "Upside to model fair price, percent = (fair_price - "
                     "price) / price x 100; positive = trades below fair "
                     "value. Key name is historical.",
    "roaa_4q": "Mean of FDIC single-quarter annualized ROA (ROAQ) over the "
               "last 4 consecutive quarters, percent; " + _FDIC_BASIS +
               "; n/a unless all 4 quarters present.",
    "roatce": "Current-quarter ROATCE, percent = YTD net income annualized "
              "(x 4/quarter) / (EQTOT - INTAN); " + _FDIC_BASIS +
              ". n/a when TCE <= 0 or an input is absent.",
    "roatce_4q": "TTM ROATCE, percent = sum of the last 4 single-quarter net "
                 "incomes / average TCE (EQTOT - INTAN) over those quarters; "
                 + _FDIC_BASIS + ". n/a unless 4 consecutive quarters.",
    "roatce_holdco": "Holding-company TTM ROATCE, percent = TTM net income "
                     "available to common / AVERAGE tangible common equity "
                     "over the TTM window; " + _SEC_HOLDCO + ". n/a (and "
                     "roatce_holdco_merger True) when a merger sits inside "
                     "the window.",
    "nim_4q": "Mean of FDIC single-quarter NIM (NIMYQ) over the last 4 "
              "consecutive quarters, percent; " + _FDIC_BASIS + ".",
    "efficiency_release": "The bank's OWN released efficiency ratio, percent "
                          "(holding-company basis, often FTE) from its "
                          "earnings release (8-K exhibit, or wire/IR release "
                          "for non-SEC filers); n/a when older than ~200 days "
                          "or not yet extracted. Different base than "
                          "efficiency_ratio (FDIC bank-sub) — never gated "
                          "against it. Quarter in efficiency_release_qend.",
    "ln_re_pct": "Real-estate loans (LNRE) / gross loans (LNLSGR, LNLSNET if "
                 "absent) x 100, percent; " + _FDIC_BASIS + ".",
    "ln_cre_pct": "Nonfarm nonresidential RE loans (LNRENRES, owner-occupied "
                  "+ non-owner-occupied) / gross loans x 100, percent; "
                  + _FDIC_BASIS + ".",
    "ln_resi_pct": "1-4 family residential RE loans (LNRERES) / gross loans "
                   "x 100, percent; " + _FDIC_BASIS + ".",
    "ln_multifam_pct": "Multifamily RE loans (LNREMULT) / gross loans x 100, "
                       "percent; " + _FDIC_BASIS + ".",
    "ln_construct_pct": "Construction & land development loans (LNRECONS) / "
                        "gross loans x 100, percent; " + _FDIC_BASIS + ".",
    "ln_ci_pct": "C&I loans (LNCI) / gross loans x 100, percent; "
                 + _FDIC_BASIS + ".",
    "ln_consumer_pct": "Consumer loans (LNCON) / gross loans x 100, percent; "
                       + _FDIC_BASIS + ".",
    "cre_to_capital": "Regulatory CRE concentration, percent = (LNRECONS + "
                      "LNREMULT + LNRENROT + LNCOMRE) / total risk-based "
                      "capital (RBC) x 100 (2006 interagency guidance; 300% "
                      "threshold); " + _FDIC_BASIS + ". n/a for "
                      "community-bank-leverage-ratio electors (RBC = 0).",
    "uninsured_pct": "Estimated uninsured / (insured + uninsured) deposits "
                     "x 100 (DEPUNINS / (DEPINS + DEPUNINS)), percent; "
                     + _FDIC_BASIS + ".",
    "core_dep_pct": "Core deposits / total deposits x 100 (COREDEP / DEP), "
                    "percent; " + _FDIC_BASIS + ".",
    "brokered_pct": "Brokered deposits / total deposits x 100 (BRO / DEP), "
                    "percent; " + _FDIC_BASIS + ".",
    "nonint_dep_pct": "Noninterest-bearing / total DOMESTIC deposits x 100 "
                      "(DEPNIDOM / (DEPIDOM + DEPNIDOM)), percent; "
                      + _FDIC_BASIS + ".",
    "cet1_current": "Latest-quarter CET1 ratio from the FDIC capital "
                    "timeline (analysis/capital_dynamics), percent; "
                    + _FDIC_BASIS + ".",
    "cet1_qoq_pp": "Quarter-over-quarter change in CET1 ratio, percentage "
                   "points; " + _FDIC_BASIS + ".",
    "tbv_cagr_1y": "Annualized 4-quarter growth of AGGREGATE tangible common "
                   "equity (EQTOT - INTAN; not per-share), percent; "
                   + _FDIC_BASIS + ". See tbv_cagr_1y_acq (acquisition in "
                   "window).",
    "payout_ratio_4q": "Implied payout, percent = 1 - mean retention over "
                       "the last 4 quarters, retention = 1 - (NI - change in "
                       "equity) / NI; equity moves include AOCI. Floored at "
                       "0; above 200 -> n/a. " + _FDIC_BASIS + ".",
    "buyback_capacity_usd": "'Free capital' estimate, raw US dollars = "
                            "latest-quarter net income - implied capital "
                            "returned (NI - change in equity) - capital to "
                            "fund loan growth at 10% CET1 (100% risk "
                            "weight); may be negative. " + _FDIC_BASIS + ".",
    "capital_alerts_count": "Count of capital-dynamics alerts "
                            "(analysis/capital_dynamics.detect_capital_alerts)"
                            ". None = no FDIC history; NOTE: 0 is also "
                            "returned when the history yields an empty "
                            "timeline.",
    "shareholder_yield": "TTM (dividends + buybacks) / market cap x 100, "
                         "percent; dollars from " + _SEC_HOLDCO +
                         " cash-flow concepts (4 consecutive quarters). n/a "
                         "when neither is tagged.",
    "dividend_yield_sec": "TTM total dividends PAID in dollars (incl. "
                          "preferred) / market cap x 100, percent; "
                          + _SEC_HOLDCO + ".",
    "buyback_yield": "TTM share repurchases ($) / market cap x 100, percent; "
                     + _SEC_HOLDCO + ".",
    "payout_ratio_ttm": "TTM dividends paid / TTM net income x 100, percent; "
                        + _SEC_HOLDCO + ".",
    "total_return_ratio_ttm": "TTM (dividends + buybacks) / TTM net income x "
                              "100, percent; " + _SEC_HOLDCO + ".",
    "share_change_pct_ttm": "Change in common shares outstanding across the "
                            "4-quarter window, percent (negative = net "
                            "buyback); " + _SEC_HOLDCO + ".",
    "dps_yoy_pct": "TTM declared dividends per share vs the prior TTM, "
                   "percent change; " + _SEC_HOLDCO + ".",
    "dividends_ttm": "TTM dividends paid, raw US dollars (full 4-quarter "
                     "window or n/a); " + _SEC_HOLDCO + ".",
    "buybacks_ttm": "TTM share repurchases, raw US dollars (full 4-quarter "
                    "window or n/a); " + _SEC_HOLDCO + ".",
    "nco_4q_trend_bps": "Net charge-off ratio now minus 4 quarters ago, "
                        "basis points; " + _FDIC_BASIS + ".",
    "npl_trend_bps": "Quarter-over-quarter change in the noncurrent-loan "
                     "ratio, basis points; " + _FDIC_BASIS + ".",
    "pd_migration_bps": "Quarter-over-quarter change in 30-89-day past-due "
                        "loans as % of loans, basis points; " + _FDIC_BASIS
                        + ".",
    "reserve_coverage_pct": "Loan-loss reserve / noncurrent loans x 100 from "
                            "the credit timeline, percent; " + _FDIC_BASIS
                            + ".",
    "worst_segment_npl": "Highest noncurrent rate among loan segments "
                         "flagged as hotspots (segment NPL >= 3x total NPL "
                         "or > 2%), percent; None = no hotspot OR no data. "
                         + _FDIC_BASIS + ".",
    "credit_alerts_count": "Count of credit-dynamics alerts "
                           "(analysis/credit_dynamics). None = no FDIC "
                           "history; NOTE: 0 is also returned when the "
                           "history yields an empty timeline.",
    "deposit_cycle_beta": "Cycle deposit beta, unitless = cumulative change "
                          "in cost of interest-bearing deposits / cumulative "
                          "change in Fed funds since the last Fed direction "
                          "change (FDIC bank-sub deposit cost; Fed funds "
                          "from FRED). n/a for cycles < 15bp.",
    "deposit_rolling_beta": "Rolling 4-quarter deposit beta, unitless "
                            "(regression of QoQ deposit-cost change on QoQ "
                            "Fed funds change); FDIC bank-sub + FRED.",
    "dep_qoq_growth": "Quarter-over-quarter total deposit growth, percent; "
                      + _FDIC_BASIS + ".",
    "cod_qoq_bps": "Quarter-over-quarter change in cost of interest-bearing "
                   "deposits (quarterly interest x 4 / average balance), "
                   "basis points; " + _FDIC_BASIS + ".",
    "deposit_alerts_count": "Count of deposit-dynamics alerts "
                            "(analysis/deposit_dynamics). None = no FDIC "
                            "history; NOTE: 0 is also returned when the "
                            "history yields an empty timeline.",
    "sec_unreal_gl": "AFS securities unrealized gain/loss, PRE-TAX, raw US "
                     "dollars = fair value - amortized cost (SCAF - SCAA); "
                     + _FDIC_BASIS + ".",
    "sec_htm_unreal": "HTM securities unrealized gain/loss, PRE-TAX, raw US "
                      "dollars = fair value - amortized cost (SCHF - SCHA); "
                      + _FDIC_BASIS + ".",
    "aoci_holdco_pct_tce": "Holdco AOCI / holdco tangible common equity x "
                           "100, percent; " + _SEC_HOLDCO + " (AOCI tagged "
                           "at the equity date).",
    "aoci_htm_holdco_pct_tce": "(Holdco AOCI + bank HTM mark, pre-tax) / "
                               "holdco TCE x 100, percent; SEC holdco AOCI "
                               "+ FDIC bank-sub HTM mark — n/a unless both "
                               "are the same quarter-end.",
    "aoci_gw_prior": "Boolean: the holdco TCE behind the AOCI ratios used "
                     "goodwill as last tagged at a prior fiscal year-end "
                     "(filer tags goodwill only annually). None when holdco "
                     "AOCI % TCE is n/a.",
    "aoci_sub_pct_tce": "Bank-sub AOCI (call report RC-R) / bank TCE (EQTOT "
                        "- INTAN) x 100, percent; " + _FDIC_BASIS + ".",
    "aoci_htm_sub_pct_tce": "(Bank-sub AOCI + HTM mark, pre-tax) / bank TCE "
                            "x 100, percent; " + _FDIC_BASIS + ".",
    "noncore_funding_pct": "Non-core funding, percent = (1 - core deposits / "
                           "total liabilities) x 100; " + _FDIC_BASIS + ".",
    "time_dep_pct": "Time deposits (NTRTIME) / domestic deposits x 100, "
                    "percent; " + _FDIC_BASIS + ".",
    "cd_reprice_3m_pct": "CDs maturing/repricing within 3 months / domestic "
                         "deposits x 100, percent; " + _FDIC_BASIS + ".",
    "cd_reprice_12m_pct": "CDs maturing/repricing within 12 months / "
                          "domestic deposits x 100, percent; " + _FDIC_BASIS
                          + ".",
    "cd_book_rate": "CD book rate, percent = quarter's time-deposit interest "
                    "x 4 / average of beginning and end time deposits; "
                    + _FDIC_BASIS + ".",
    "cd_rate_vs_6m_bill": "cd_book_rate minus the 6-month Treasury bill "
                          "yield (FRED DGS6MO) on the call-report date, "
                          "percentage points.",
    "sec_to_assets_pct": "Total securities / total assets x 100 (SC / "
                         "ASSET), percent; " + _FDIC_BASIS + ".",
    "htm_pct": "HTM securities / total securities x 100 (SCHA / SC), "
               "percent; " + _FDIC_BASIS + ".",
    "nim_spread": "Interest-income yield minus interest-expense cost "
                  "(INTINCY - INTEXPY), percentage points; " + _FDIC_BASIS
                  + ".",
    "nonint_burden": "Noninterest expense minus noninterest income, % of "
                     "average assets (NONIXAY - NONIIAY), percentage points; "
                     + _FDIC_BASIS + ".",
    "cost_of_funds": "Cost of funds, percent: FDIC INTEXPY (interest expense "
                     "/ earning assets); fallback when absent = annualized "
                     "EINTEXP / interest-bearing liabilities. "
                     + _FDIC_BASIS + ".",
}

# Snapshot keys analysis.metrics.build_bank_metrics adds OUTSIDE the
# config.METRICS registry (diagnostic passthroughs). A new passthrough fails
# tests/test_export_core.TestBankMetricsColumns until described here.
_PASSTHROUGH_DESC: dict[str, str] = {
    "tbvps_conflict": "Boolean: the company's own released TBVPS and the SEC "
                      "reconstruction disagree >= 15% — one of them is "
                      "wrong; the reconstruction is served in tbvps.",
    "bvps_conflict": "Boolean: as tbvps_conflict, for book value per share.",
    "eps_source": "Provenance of eps: 'release_ttm' (release-anchored "
                  "composite TTM), 'release_ttm_otc' (non-SEC filer, 4 "
                  "quarters of its own releases), 'reconstructed' (SEC XBRL "
                  "TTM), None = no EPS.",
    "eps_conflict": "Boolean: the release composite and the XBRL TTM "
                    "disagree >= 15% and the release quarter failed the "
                    "plausibility gate; the XBRL TTM is served.",
    "market_cap_source": "'company_release' when market_cap's share count "
                         "came from a non-SEC filer's own earnings release; "
                         "None = SEC share count (or no market cap).",
    "shares_outstanding": "Common share count market_cap is priced on "
                          "(count of shares); see market_cap.",
    "sec_facts_lag": "Boolean: SEC's companyfacts API lags the bank's latest "
                     "filed 10-Q/10-K — XBRL-derived figures are as of "
                     "sec_facts_as_of, older than sec_filed_period. None = "
                     "unknown (no filing index / non-SEC).",
    "sec_facts_as_of": "Period-end date of the freshest SEC companyfacts "
                       "fact behind the row's XBRL figures (date).",
    "sec_filed_period": "Period-end date of the bank's latest filed "
                        "10-Q/10-K per its SEC submissions index (date).",
    "sec_filed_date": "Filing date of that latest 10-Q/10-K (date).",
    "sec_filed_form": "Form of that latest periodic filing ('10-Q' / "
                      "'10-K').",
    "sec_facts_overlay": "JSON object {accession, form, report_date, filed} "
                         "when the SEC figures were overlaid from the "
                         "filing's own inline XBRL (companyfacts lagging); "
                         "None otherwise.",
    "efficiency_release_qend": "Quarter-end date the efficiency_release "
                               "figure covers (date).",
    "metric_notes": "JSON object {metric key: reason} — the dashboard's "
                    "per-cell n/a reasons / caveats (merger in the holdco "
                    "window, share-basis mismatch, untagged goodwill, "
                    "acquisition-driven TCE growth ...). None = no notes. "
                    "(Snapshot key '_notes'.)",
    "roatce_holdco_merger": "Boolean: a merger sits inside the holdco TTM "
                            "window, so roatce_holdco and the fair-value "
                            "chain (roatce_blended ... ptbv_discount) are "
                            "n/a; reason in metric_notes.",
    "ptbv_basis_mismatch": "Boolean: the latest cover-page share count "
                           "differs > 10% from the quarter-end count behind "
                           "book value per share, so tbvps/bvps and price "
                           "multiples on them are n/a.",
    "tbv_cagr_1y_acq": "Boolean: a whole-bank acquisition completed inside "
                       "the tbv_cagr_1y window (growth is bought, not "
                       "earned).",
}

# Snapshot key -> export column name (keys starting with '_' are renamed).
_RENAMES = {"_notes": "metric_notes"}
# Values that are dicts in the snapshot; exported as JSON strings.
_JSON_COLS = {"metric_notes", "sec_facts_overlay"}
# Date-valued columns (ISO strings in the snapshot) -> datetime.date.
_DATE_COLS = {"sec_facts_as_of", "sec_filed_period", "sec_filed_date",
              "efficiency_release_qend"}

_UNDOCUMENTED = "UNDOCUMENTED"


def _registry_description(m: dict, catalog: dict) -> str:
    """Description for one config.METRICS entry."""
    from analysis.metrics import FDIC_YTD_INCOME_FIELDS
    key, label, src = m["key"], m.get("label", m["key"]), m.get("source")
    if src == "fdic":
        f = m.get("fdic_field")
        cat = catalog.get(f) or {}
        unit = _FDIC_UNIT_OVERRIDE.get(key) or _FDIC_UNIT.get(cat.get("kind"))
        if unit is None:
            return f"{_UNDOCUMENTED}: FDIC field {f} has no catalog kind"
        desc = (f"Dashboard column '{label}' — {_FDIC_BASIS}. Field {f} "
                f"({cat.get('title', 'no catalog title')}). Units: {unit}.")
        if f in FDIC_YTD_INCOME_FIELDS:
            desc += (" Income-statement YTD figure ANNUALIZED (YTD x "
                     "4/quarter-of-year).")
        if key in _FDIC_KEY_NOTE:
            desc += " " + _FDIC_KEY_NOTE[key]
        return desc
    if src == "ibkr":
        unit = {"price": "US dollars per share",
                "change_pct": "percent change vs previous close (1.5 = 1.5%)",
                "volume": "shares traded (count)"}.get(key)
        if unit is None:
            return f"{_UNDOCUMENTED}: market-data key {key}"
        return f"Dashboard column '{label}' — {_MARKET}. Units: {unit}."
    if src == "computed":
        return _COMPUTED_DESC.get(key, f"{_UNDOCUMENTED}: computed metric {key}")
    return f"{_UNDOCUMENTED}: unknown source {src!r} for {key}"


def _build_metrics_columns() -> dict[str, str]:
    from pathlib import Path
    from config import METRICS
    catalog_path = Path(__file__).parent.parent / "fdic_field_catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    cols = {
        "ticker": "Primary common-stock ticker (canonical).",
        "as_of_date": "Export date this row was appended (date) — the daily "
                      "append key with ticker; the metric values are as of "
                      "snapshot_at.",
        "snapshot_at": "When the dashboard's Home metrics snapshot "
                       "(watchlist_metrics_snap) was built — job container "
                       "clock, UTC in prod (naive timestamp).",
    }
    for m in METRICS:
        cols[m["key"]] = _registry_description(m, catalog)
    for k, d in _PASSTHROUGH_DESC.items():
        cols.setdefault(k, d)
    return cols


COLUMNS_BANK_METRICS: dict[str, str] = _build_metrics_columns()


def undocumented_snapshot_keys(metric_rows: list[dict]) -> list[str]:
    """Snapshot keys (after renames) that have no declared column — each is a
    new dashboard metric that needs a description before it can export."""
    seen: dict[str, None] = {}
    for row in metric_rows or []:
        for k in row:
            c = _RENAMES.get(k, k)
            if c not in COLUMNS_BANK_METRICS:
                seen.setdefault(c, None)
    return list(seen)


def _export_value(col: str, v):
    if col in _JSON_COLS:
        if v is None or v == {} or (isinstance(v, float) and v != v):
            return None
        return json.dumps(v, sort_keys=True, default=str)
    if col in _DATE_COLS:
        return _iso_date(v)
    if col in _SCALE and isinstance(v, (int, float)) and v == v:
        return v * _SCALE[col]
    return v


def build_bank_metrics(tickers: list[str],
                       as_of: date | None = None) -> pd.DataFrame:
    """One row per requested bank present in the persisted Home metrics
    snapshot. A bank absent from the snapshot gets NO row (never an all-null
    placeholder); an empty/absent snapshot yields an empty frame. Snapshot
    keys without a declared column are dropped LOUDLY (the test suite makes
    that a merge blocker for new dashboard metrics)."""
    from data import cache
    as_of = as_of or date.today()
    snap = cache.get(SNAPSHOT_KEY, max_age_s=None)
    metrics = (snap or {}).get("metrics") if isinstance(snap, dict) else None
    if not metrics:
        print(f"[export] bank_metrics: {SNAPSHOT_KEY} absent or empty — "
              "no rows")
        return _frame([], COLUMNS_BANK_METRICS)
    snapshot_at = pd.to_datetime((snap or {}).get("cached_at"), errors="coerce")
    snapshot_at = None if pd.isna(snapshot_at) else snapshot_at

    extra = undocumented_snapshot_keys(metrics)
    if extra:
        print(f"[export] bank_metrics: {len(extra)} snapshot keys have no "
              f"declared column and are NOT exported: {', '.join(extra)}")

    by_ticker: dict[str, dict] = {}
    dupes = 0
    for row in metrics:
        t = str(row.get("ticker") or "").upper()
        if not t:
            continue
        if t in by_ticker:
            dupes += 1
            continue
        by_ticker[t] = row
    if dupes:
        print(f"[export] bank_metrics: {dupes} duplicate snapshot rows "
              "ignored (first kept)")

    scope = _canonical_tickers(tickers)
    absent = [t for t in scope if t not in by_ticker]
    if absent:
        print(f"[export] bank_metrics: {len(absent)} requested banks absent "
              f"from the snapshot (no row): {', '.join(absent[:20])}")

    rows = []
    for t in scope:
        src = by_ticker.get(t)
        if src is None:
            continue
        out = {"ticker": t, "as_of_date": as_of, "snapshot_at": snapshot_at}
        for k, v in src.items():
            c = _RENAMES.get(k, k)
            if c in COLUMNS_BANK_METRICS and c not in out:
                out[c] = _export_value(c, v)
        rows.append(out)
    return _frame(rows, COLUMNS_BANK_METRICS)


# ══════════════════════════════════════════════════════════════════════════
# 3. events
# ══════════════════════════════════════════════════════════════════════════

COLUMNS_EVENTS: dict[str, str] = {
    "ticker": "Primary common-stock ticker the event is attributed to "
              "(preferred/ETN sibling tags remapped to the common).",
    "event_id": "Events-store row id (stable per stored event; unique key).",
    "source": "Ingesting adapter: sec_8k (SEC EDGAR 8-K index), "
              "businesswire / prnewswire / globenewswire (first-party "
              "wires), ir_site (bank IR site), fmp_news / google_news / "
              "yfinance_news (aggregators).",
    "event_type": "Coarse type assigned at ingest: earnings, m_and_a, "
                  "capital_return, capital_raise, executive_change, "
                  "press_release, shareholder_vote, regulatory, news, filing; "
                  "'exhibit_only' = an 8-K whose only item is 9.01 (hidden on "
                  "dashboard feeds). Headline-keyword (wires/news) or 8-K "
                  "item (sec_8k) classification — not verified.",
    "headline": "Headline as ingested (8-K rows: a generated label of the "
                "form/items).",
    "summary": "Adapter summary / lede text; None when the source gave none.",
    "url": "Link to the source document.",
    "external_id": "Source-specific id (8-K accession, wire story id, URL "
                   "hash); unique together with source.",
    "published_at": "Publication time, UTC (tz-aware). Future-dated stamps "
                    "are clamped to ingest time by the store.",
    "ingested_at": "Time the row was first stored, UTC (tz-aware).",
}

_EVENT_SQL_COLS = ("id, ticker, source, event_type, headline, summary, url, "
                   "external_id, published_at, ingested_at")


def _utc(series: pd.Series) -> pd.Series:
    """Timestamps from either backend (Postgres tz-aware datetimes, SQLite
    ISO text with or without an offset) -> tz-aware UTC. Naive values are
    UTC by the store's convention (CURRENT_TIMESTAMP / UTC adapters)."""
    return pd.to_datetime(series, utc=True, format="ISO8601", errors="coerce")


def build_events(tickers: list[str], since_days: int = 400) -> pd.DataFrame:
    """Every stored bank event published in the last `since_days` days for the
    requested banks (raw_json excluded). Topic-feed rows (source
    google_news_topic, sentinel tickers 'TOPIC:<CATEGORY>') are EXCLUDED:
    they are macro/market headlines, not bank events, and carry no bank
    ticker. Rows are exported as stored — cross-source copies of one release
    are not collapsed (the dashboard's display-time dedup is a view choice)."""
    from sqlalchemy import text
    from data.events import store
    from data.bank_universe import get_noncommon_primary_map

    cutoff = datetime.now(timezone.utc) - timedelta(days=int(since_days))
    # SQLite stores ISO text: compare against a same-shaped string (the
    # pandas filter below is the exact one; this just bounds the read).
    bound = cutoff if store._USE_POSTGRES else cutoff.strftime("%Y-%m-%d %H:%M:%S")
    sql = (f"SELECT {_EVENT_SQL_COLS} FROM events "
           "WHERE published_at >= :cutoff AND source <> :topic_src "
           "AND ticker NOT LIKE :topic_tk")
    with store._get_engine().connect() as conn:
        rows = conn.execute(text(sql), {
            "cutoff": bound, "topic_src": store.TOPIC_SOURCE,
            "topic_tk": store._TOPIC_TICKER_PREFIX + "%",
        }).mappings().all()
    df = pd.DataFrame([dict(r) for r in rows],
                      columns=[c.strip() for c in _EVENT_SQL_COLS.split(",")])
    df = df.rename(columns={"id": "event_id"})

    remap = get_noncommon_primary_map()
    df["ticker"] = [remap.get(str(t or "").upper(), str(t or "").upper())
                    for t in df["ticker"]]
    scope = set(_canonical_tickers(tickers))
    df = df[df["ticker"].isin(scope)]

    df["published_at"] = _utc(df["published_at"])
    df["ingested_at"] = _utc(df["ingested_at"])
    bad = int(df["published_at"].isna().sum())
    if bad:
        print(f"[export] events: {bad} rows with unparseable published_at "
              "dropped")
    df = df[df["published_at"].notna() & (df["published_at"] >= cutoff)]
    df = df.sort_values(["ticker", "published_at", "event_id"],
                        ascending=[True, False, False])
    df = df[list(COLUMNS_EVENTS)].reset_index(drop=True)
    df["event_id"] = df["event_id"].astype("Int64")
    return df
