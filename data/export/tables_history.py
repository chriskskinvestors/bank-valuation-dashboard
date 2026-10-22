"""Agent dataset export — quarterly HISTORY tables.

  bank_quarterly    FDIC call-report history, one row per (ticker, cert,
                    report_date), from the deep store (data/fdic_history_store,
                    1992 →). Multi-charter holdcos get one row PER CHARTER —
                    never summed here (cert_group's aggregation classifies
                    ratios exact-quotient vs average-based; a sum of charter
                    ratios is a plausible-wrong number).
  holdco_quarterly  SEC companyfacts holding-company series, one row per
                    (ticker, period_end), every calendar quarter-end the cached
                    blob carries a balance sheet for.

READ-ONLY and CACHE-ONLY: no SEC / FDIC / FMP call is ever made. Identity
(ticker → cert / CIK) comes from the curated maps and the persisted universe
snapshot only; a ticker none of them knows is skipped and counted, never
resolved live. The companyfacts blob is read straight from the persistent
store (`max_age_s=None`) — the read-time filing overlays of
sec_client.fetch_company_facts (lagging 10-Q, per-series preferred, MSR,
multi-class shares) fetch on a miss, so they are NOT applied: the series is
companyfacts as SEC published it. Every derived figure goes through the
dashboard's own helpers (sec_client, analysis.valuation._holdco_tce_at,
data.sec_period); absent data is None, never 0.

The dashboard's resolvers were built for the LATEST balance sheet (and the
5-point TCE window); run over 17 years of history they meet shapes the
latest date never shows, so holdco rows add four guards — each n/a, never a
guess: a date before the filer's first tagged balance sheet (IBOC 2008), a
zero preferred share count beside a material preferred value (JPM
2014-03-31), untagged holdco goodwill beside material bank-sub goodwill
(holdco_tce_window's own guard, per quarter from the deep store), and a
reverse-merger basis break (MCHB: rows before it read only the restated
basis).

Skip counts ride on the returned frame: ``df.attrs["skipped"]``.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

# ── Curated FDIC fields: (column, FDIC code, unit, description) ──────────────
# unit: "usd" — FDIC reports $thousands; ×1000 here to raw dollars (the
#               analysis/metrics.py boundary rule). Never guessed from size.
#       "pct" — FDIC ratio as reported, in PERCENT (1.25 = 1.25%).
#       "count" — a count as reported.
# Income-statement levels are calendar-YEAR-TO-DATE cumulative (FDIC
# convention), not single-quarter, unless the name says _quarter.
_YTD = "calendar year-to-date cumulative"
_FDIC_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    # Balance sheet
    ("total_assets", "ASSET", "usd", "Total assets"),
    ("total_liabilities", "LIAB", "usd", "Total liabilities"),
    ("total_equity", "EQTOT", "usd", "Total equity capital incl. minority interests"),
    ("bank_equity_capital", "EQ", "usd", "Bank equity capital (excl. minority interests)"),
    ("intangibles_total", "INTAN", "usd",
     "Total intangible assets incl. goodwill (the statement pages' TCE deduction)"),
    ("goodwill", "INTANGW", "usd", "Goodwill only"),
    ("mortgage_servicing_assets", "INTANMSR", "usd", "Mortgage servicing assets"),
    ("cash_and_due", "CHBAL", "usd", "Cash and balances due from depository institutions"),
    ("fed_funds_repos_sold", "FREPO", "usd", "Fed funds sold and reverse repos"),
    ("securities_total", "SC", "usd", "Total investment securities"),
    ("securities_afs", "SCAF", "usd", "Available-for-sale securities (fair value)"),
    ("securities_afs_amortized_cost", "SCAA", "usd", "Available-for-sale securities at amortized cost"),
    ("securities_htm", "SCHA", "usd", "Held-to-maturity securities (amortized cost)"),
    ("securities_htm_fair_value", "SCHF", "usd", "Held-to-maturity securities at fair value"),
    ("trading_assets", "TRADE", "usd", "Trading account assets"),
    ("earning_assets", "ERNAST", "usd", "Total earning assets"),
    ("premises_fixed_assets", "BKPREM", "usd", "Premises and fixed assets"),
    ("loans_gross", "LNLSGR", "usd", "Total loans and leases, gross"),
    ("loans_net", "LNLSNET", "usd", "Loans and leases net of unearned income and allowance"),
    ("allowance_for_credit_losses", "LNATRES", "usd", "Allowance for loan and lease losses"),
    ("other_real_estate_owned", "ORE", "usd", "Other real estate owned"),
    ("fhlb_advances", "OTHBFHLB", "usd", "FHLB advances (other borrowed money)"),
    ("subordinated_debt", "SUBND", "usd", "Subordinated notes and debentures"),
    # Deposits
    ("total_deposits", "DEP", "usd", "Total deposits"),
    ("domestic_deposits", "DEPDOM", "usd", "Deposits held in domestic offices"),
    ("core_deposits", "COREDEP", "usd", "Core deposits"),
    ("insured_deposits_est", "DEPINS", "usd", "Estimated insured deposits"),
    ("uninsured_deposits_est", "DEPUNINS", "usd", "Estimated uninsured deposits"),
    ("brokered_deposits", "BRO", "usd", "Brokered deposits"),
    ("demand_deposits", "DDT", "usd", "Demand deposits"),
    ("transaction_deposits", "TRN", "usd", "Transaction accounts, total"),
    ("nontransaction_deposits", "NTR", "usd", "Nontransaction accounts, total"),
    ("mmda_deposits", "NTRSMMDA", "usd", "Money market deposit accounts (savings)"),
    ("other_savings_deposits", "NTRSOTH", "usd", "Other savings deposits"),
    ("time_deposits", "NTRTIME", "usd", "Time deposits, total"),
    ("deposits_accounts_over_250k", "DEPLGAMT", "usd",
     "Amount in deposit accounts greater than $250,000"),
    ("deposits_accounts_250k_or_less", "DEPSMAMT", "usd",
     "Amount in deposit accounts of $250,000 or less"),
    ("interest_bearing_deposits_domestic", "DEPIDOM", "usd", "Interest-bearing deposits, domestic offices"),
    ("noninterest_bearing_deposits_domestic", "DEPNIDOM", "usd",
     "Noninterest-bearing deposits, domestic offices"),
    # Loan mix
    ("loans_real_estate", "LNRE", "usd", "Real estate loans, total"),
    ("loans_1_4_family", "LNRERES", "usd", "1-4 family residential real estate loans"),
    ("loans_heloc", "LNRELOC", "usd", "1-4 family home equity lines of credit"),
    ("loans_construction", "LNRECONS", "usd", "Construction and land development loans"),
    ("loans_multifamily", "LNREMULT", "usd", "Multifamily real estate loans"),
    ("loans_cre_nonfarm_nonres", "LNRENRES", "usd", "Nonfarm nonresidential real estate loans, total"),
    ("loans_cre_owner_occupied", "LNRENROW", "usd", "Owner-occupied nonfarm nonresidential RE loans"),
    ("loans_cre_non_owner_occupied", "LNRENROT", "usd", "Other (non-owner-occupied) nonfarm nonresidential RE loans"),
    ("loans_farmland", "LNREAG", "usd", "Farmland (agricultural real estate) loans"),
    ("loans_cre_not_secured_by_re", "LNCOMRE", "usd",
     "Loans to finance CRE not secured by real estate (RC-C Memo 3)"),
    ("loans_c_and_i", "LNCI", "usd", "Commercial and industrial loans"),
    ("loans_consumer", "LNCON", "usd", "Consumer loans, total"),
    ("loans_credit_card", "LNCRCD", "usd", "Consumer credit card loans"),
    ("loans_auto", "LNAUTO", "usd", "Consumer auto loans"),
    ("loans_consumer_other", "LNCONOTH", "usd", "Other consumer loans"),
    ("loans_agricultural", "LNAG", "usd", "Agricultural production loans"),
    ("loans_municipal", "LNMUNI", "usd", "Loans to states and political subdivisions"),
    ("leases", "LS", "usd", "Lease financing receivables"),
    ("loans_other", "LNOTHER", "usd", "Loans to nondepository financial institutions and other loans"),
    # Income statement (YTD)
    ("net_income_ytd", "NETINC", "usd", f"Net income, {_YTD}"),
    ("interest_income_ytd", "INTINC", "usd", f"Total interest income, {_YTD}"),
    ("interest_expense_ytd", "EINTEXP", "usd", f"Total interest expense, {_YTD}"),
    ("net_interest_income_ytd", "NIM", "usd",
     f"Net interest income in DOLLARS (FDIC 'NIM' is the dollar line; the margin is NIMY), {_YTD}"),
    ("noninterest_income_ytd", "NONII", "usd", f"Total noninterest income, {_YTD}"),
    ("noninterest_expense_ytd", "NONIX", "usd", f"Total noninterest expense, {_YTD}"),
    ("provision_ytd", "ELNATR", "usd", f"Provision for credit losses, {_YTD}"),
    ("pretax_income_ytd", "PTAXNETINC", "usd", f"Pre-tax net operating income, {_YTD}"),
    ("income_taxes_ytd", "ITAX", "usd", f"Applicable income taxes, {_YTD}"),
    ("intangible_amortization_ytd", "EAMINTAN", "usd",
     f"Amortization and impairment of intangibles, {_YTD}"),
    ("salaries_benefits_ytd", "ESAL", "usd", f"Salaries and employee benefits, {_YTD}"),
    ("deposit_interest_expense_ytd", "EDEP", "usd", f"Interest expense on deposits, {_YTD}"),
    ("securities_gains_ytd", "IGLSEC", "usd", f"Realized securities gains (losses), {_YTD}"),
    ("cash_dividends_ytd", "EQCDIV", "usd", f"Cash dividends declared on common and preferred, {_YTD}"),
    # Credit
    ("net_charge_offs_ytd", "NTLNLS", "usd", f"Net charge-offs, loans and leases, {_YTD}"),
    ("gross_charge_offs_ytd", "DRLNLS", "usd", f"Gross charge-offs, loans and leases, {_YTD}"),
    ("recoveries_ytd", "CRLNLS", "usd", f"Recoveries, loans and leases, {_YTD}"),
    ("net_charge_offs_quarter", "NTLNLSQ", "usd", "Net charge-offs, loans and leases, single quarter"),
    ("noncurrent_loans", "NCLNLS", "usd", "Noncurrent loans and leases (90+ days past due + nonaccrual)"),
    ("nonaccrual_loans", "NALNLS", "usd", "Nonaccrual loans and leases"),
    ("past_due_30_89", "P3LNLS", "usd", "Loans and leases 30-89 days past due"),
    ("past_due_90_plus", "P9LNLS", "usd", "Loans and leases 90+ days past due (still accruing)"),
    ("nonperforming_assets", "NPERF", "usd", "Nonperforming assets"),
    # Capital (dollars)
    ("cet1_capital", "RBCT1C", "usd",
     "Common equity tier 1 capital (CET1 $ is RBCT1C, not RBCT1J); None before the Basel III line"),
    ("tier1_capital", "RBCT1J", "usd", "Tier 1 capital"),
    ("tier2_capital", "RBCT2", "usd", "Tier 2 capital"),
    ("total_risk_based_capital", "RBC", "usd", "Total risk-based capital"),
    ("risk_weighted_assets", "RWAJ", "usd", "Risk-weighted assets"),
    ("average_assets_for_leverage", "AVASSETJ", "usd", "Average total assets for the leverage ratio"),
    # Ratios (percent as reported)
    ("cet1_ratio", "IDT1CER", "pct", "CET1 ratio; None for pre-adoption quarters (FDIC literal 0 nulled on read)"),
    ("tier1_rbc_ratio", "RBC1RWAJ", "pct", "Tier 1 risk-based capital ratio"),
    ("total_rbc_ratio", "RBCRWAJ", "pct", "Total risk-based capital ratio"),
    ("leverage_ratio", "RBC1AAJ", "pct", "Tier 1 leverage ratio"),
    ("roa_ytd", "ROA", "pct", "Return on average assets, YTD annualized"),
    ("roe_ytd", "ROE", "pct", "Return on average equity, YTD annualized"),
    ("nim_ytd", "NIMY", "pct", "Net interest margin, YTD annualized"),
    ("efficiency_ratio_ytd", "EEFFR", "pct", "Efficiency ratio, YTD"),
    ("roa_quarter", "ROAQ", "pct", "Return on average assets, single quarter annualized"),
    ("roe_quarter", "ROEQ", "pct", "Return on average equity, single quarter annualized"),
    ("nim_quarter", "NIMYQ", "pct", "Net interest margin, single quarter annualized"),
    ("efficiency_ratio_quarter", "EEFFQR", "pct", "Efficiency ratio, single quarter"),
    ("noncurrent_loans_ratio", "NCLNLSR", "pct", "Noncurrent loans / gross loans"),
    ("net_charge_off_ratio_ytd", "NTLNLSR", "pct", "Net charge-offs / average loans, YTD annualized"),
    ("net_charge_off_ratio_quarter", "NTLNLSQR", "pct", "Net charge-offs / average loans, single quarter annualized"),
    ("reserve_to_loans", "LNATRESR", "pct", "Allowance / gross loans"),
    ("reserve_to_noncurrent", "LNRESNCR", "pct",
     "Allowance / noncurrent loans; None when noncurrent loans are zero (undefined)"),
    ("loans_to_deposits", "LNLSDEPR", "pct", "Net loans / deposits"),
    ("equity_to_assets", "EQV", "pct", "Bank equity capital / assets"),
    # Counts
    ("employees", "NUMEMP", "count", "Full-time equivalent employees"),
    ("domestic_offices", "OFFDOM", "count", "Number of domestic offices"),
)

_UNIT_TEXT = {"usd": "$ (FDIC {code} ×1000)",
              "pct": "percent, 1.25 = 1.25% (FDIC {code}, as reported)",
              "count": "count (FDIC {code})"}

COLUMNS_BANK_QUARTERLY: dict[str, str] = {
    "ticker": "Ticker of the listed holding company",
    "cert": "FDIC certificate number of the bank charter this row describes",
    "charter_name": "Charter's name as filed on this call report (FDIC REPNM)",
    "is_primary_charter": "True for the ticker's mapped (lead) FDIC charter; False for a "
                          "sibling charter under the same holdco",
    "charter_status": "'active' — a current charter of the holdco; 'former' — a charter "
                      "since merged away, included only for quarters the holdco held it",
    "report_date": "Call report quarter-end (datetime.date; FDIC REPDTE)",
    "holdco_rssd": "RSSD of the high holder on this report (FDIC RSSDHCR); None if none",
    **{col: f"{desc}, {_UNIT_TEXT[unit].format(code=code)}"
       for col, code, unit, desc in _FDIC_FIELDS},
    "fdic_fields_json": "ALL fields of this call-report row as JSON, RAW FDIC units "
                        "($ levels in THOUSANDS, ratios in percent), after the store's "
                        "read-time literal-zero fixes",
}

COLUMNS_HOLDCO_QUARTERLY: dict[str, str] = {
    "ticker": "Ticker of the listed holding company",
    "cik": "SEC CIK whose companyfacts supplied the row",
    "period_end": "Calendar quarter-end balance-sheet date (datetime.date)",
    "total_equity": "Equity attributable to the parent, $ (us-gaap StockholdersEquity, "
                    "else the NCI-inclusive total less same-date MinorityInterest — "
                    "sec_client._parent_equity_at); None when NCI is not separable",
    "preferred": "Preferred stock carrying value as of this date, $ "
                 "(sec_client._resolve_preferred_stock(as_of=)); 0.0 = the resolver found "
                 "no preferred outstanding; None = preferred outstanding but its value "
                 "is unresolved, or no balance sheet tagged at the date",
    "preferred_present": "True when the filer had preferred outstanding at this date; "
                         "None when no balance sheet is tagged at the date",
    "goodwill": "Goodwill tagged at exactly this date, $ (us-gaap Goodwill); None when "
                "not tagged here (tce may still carry the latest goodwill within a year)",
    "intangible_adjustment": "Goodwill + other intangibles deducted for tce, $ (MSRs kept "
                             "in tangible equity; year-end goodwill carried as the "
                             "dashboard does) = total_equity − preferred − tce; None when "
                             "tce is None",
    "tce": "Tangible common equity, $ — analysis.valuation._holdco_tce_at, the exact "
           "figure the dashboard's average-TCE ROATCE window uses; None when any leg "
           "is unresolvable (see tce_note)",
    "tce_note": "Why tce is None (None when tce resolved)",
    "total_assets": "Total assets, $ (us-gaap Assets at this date)",
    "shares_outstanding": "Common shares outstanding at this balance-sheet date: us-gaap "
                          "CommonStockSharesOutstanding tagged at the date, else "
                          "CommonStockSharesIssued − TreasuryStockCommonShares both tagged "
                          "at the date (latest filing wins; a same-date conflict is "
                          "arbitrated by the next dei cover count, as the dashboard does); "
                          "None when neither is tagged. Never a cover-page or weighted-"
                          "average count; per-class-only filers (multi-class) are None",
    "net_income_quarter": "Net income for the single quarter ending period_end, $ — "
                          "year-to-date differences (Q = YTD(q) − YTD(q−1); Q4 = FY − 9M), "
                          "else a directly tagged 3-month figure (data.sec_period); "
                          "calendar fiscal years only; a 10-Q's 12-month figure is never "
                          "used as the FY",
    "net_income_concept": "us-gaap concept behind net_income_quarter (NetIncomeLoss, else "
                          "NetIncomeLossAvailableToCommonStockholdersBasic, else ProfitLoss "
                          "— the dashboard's TTM fallback order, chosen once per bank)",
    "diluted_eps_quarter": "Diluted EPS for the single quarter, $/share — ONLY a directly "
                           "tagged 3-month figure (per-share amounts are not additive, so "
                           "Q4 = FY − 9M is never formed; usually None for Q4)",
    "dividends_per_share_quarter": "Common dividends declared per share for the single "
                                   "quarter, $/share (YTD differences, Q4 = FY − 9M, else "
                                   "a directly tagged 3-month figure)",
    "basis_note": "Set on dates before a reverse merger / entity restatement: the "
                  "row is read only from filings on the restated basis (dates that "
                  "basis never restated are omitted); None otherwise",
    "filed_form": "Form (10-K/10-Q) of the latest filing that tagged total_equity at "
                  "this date",
    "filed_date": "Filing date of that filing (datetime.date)",
}

# The dashboard's TTM net-income concept order (sec_client.get_latest_fundamentals).
_NI_CONCEPTS = ("NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
                "ProfitLoss")
_DPS_CONCEPT = "CommonStockDividendsPerShareDeclared"
_QUARTER_ENDS = {(3, 31), (6, 30), (9, 30), (12, 31)}
# holdco_tce_window's guard: a zero holdco intangible deduction beside bank-sub
# goodwill above 1% of the holdco's TCE means holdco goodwill is untagged.
_BANK_GOODWILL_MATERIAL = 0.01
# A same-date re-report of assets / equity moving more than this is a change
# of reporting entity (reverse merger), not a restatement (those move a few %).
_BASIS_BREAK_MOVE = 0.25
# sec_client._resolve_preferred_stock's carrying-value ladder.
_PREFERRED_VALUE_LADDER = (
    "PreferredStockValue",
    "PreferredStockIncludingAdditionalPaidInCapital",
    "PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount",
    "PreferredStockValueOutstanding",
    "PreferredStockLiquidationPreferenceValue",
)


# ── small helpers ─────────────────────────────────────────────────────────────
def _num(v) -> float | None:
    """float(v), or None for absent / NaN / non-numeric."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _iso_to_date(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _known_ticker(ticker: str) -> bool:
    """True when the curated maps or the persisted universe snapshot know the
    ticker — then bank_mapping's lookups resolve without a live call."""
    from data import bank_mapping as bm
    t = ticker.upper()
    return (t in bm.BANK_MAP or t in bm._RESOLVED_FROM_JSON
            or t in bm._universe_snapshot_map())


def _cert_for(ticker: str) -> int | None:
    """The ticker's mapped FDIC cert, cache-only (None if unknown/none)."""
    if not _known_ticker(ticker):
        return None
    from data.bank_mapping import get_fdic_cert
    c = get_fdic_cert(ticker)
    return int(c) if c else None


def _cik_for(ticker: str) -> int | None:
    """The ticker's data CIK (get_data_cik: deregistered / Reg-A filers are
    None, as on the dashboard), cache-only."""
    if not _known_ticker(ticker):
        return None
    from data.bank_mapping import get_data_cik
    c = get_data_cik(ticker)
    return int(c) if c else None


def _charters(cert: int) -> tuple[list[int], str | None, list[int]]:
    """(active group certs largest-first, holdco RSSD, former charters) from
    the persisted maps only — cert_group's own seams, no network."""
    from data.cert_group import get_absorbed_charters, get_cert_group_cached
    active = get_cert_group_cached(cert) or [cert]
    hc, former = get_absorbed_charters(active[0])
    return active, hc, [c for c in former if c not in active]


def _group_history(cert: int) -> list[tuple[int, str, dict]]:
    """[(cert, 'active'|'former', record)] for the cert's whole charter group
    from the deep store (read-time fixes applied by get_certs_history); a
    former charter's records only for quarters `hc` held it."""
    from data.cert_group import held_by
    from data.fdic_history_store import get_certs_history
    active, hc, former = _charters(cert)
    stored = get_certs_history(active + former)
    out = []
    for c in active:
        out += [(c, "active", r) for r in stored.get(c, [])]
    for c in former:
        out += [(c, "former", r) for r in stored.get(c, []) if held_by(r, hc)]
    return out


# ── bank_quarterly ────────────────────────────────────────────────────────────
def _bank_row(ticker: str, cert: int, status: str, primary: int, rec: dict) -> dict | None:
    from data.fdic_history_store import _norm_repdte, _strict_json
    repdte = _norm_repdte(rec.get("REPDTE"))
    if not repdte:
        return None
    hc = _num(rec.get("RSSDHCR"))
    row = {
        "ticker": ticker,
        "cert": int(cert),
        "charter_name": rec.get("REPNM") or None,
        "is_primary_charter": int(cert) == int(primary),
        "charter_status": status,
        "report_date": date(int(repdte[:4]), int(repdte[4:6]), int(repdte[6:])),
        "holdco_rssd": int(hc) if hc else None,
    }
    for col, code, unit, _ in _FDIC_FIELDS:
        v = _num(rec.get(code))
        row[col] = v * 1000 if (unit == "usd" and v is not None) else v
    row["fdic_fields_json"] = _strict_json(rec)
    return row


def build_bank_quarterly(tickers: list[str]) -> pd.DataFrame:
    """FDIC call-report history per charter per quarter (see module doc)."""
    rows: list[dict] = []
    skipped: dict[str, list[str]] = {"no_cert": [], "no_history": []}
    for ticker in tickers:
        t = ticker.upper()
        cert = _cert_for(t)
        if not cert:
            skipped["no_cert"].append(t)
            continue
        hist = _group_history(cert)
        if not hist:
            skipped["no_history"].append(t)
            continue
        for c, status, rec in hist:
            row = _bank_row(t, c, status, cert, rec)
            if row:
                rows.append(row)
    df = pd.DataFrame(rows, columns=list(COLUMNS_BANK_QUARTERLY))
    if not df.empty:
        df = df.sort_values(["ticker", "cert", "report_date"]).reset_index(drop=True)
    df.attrs["skipped"] = skipped
    print(f"[export] bank_quarterly: {len(df)} rows; skipped "
          + ", ".join(f"{k}={len(v)}" for k, v in skipped.items()), flush=True)
    return df


# ── holdco_quarterly ──────────────────────────────────────────────────────────
def _cached_facts(cik: int) -> dict | None:
    """The slim companyfacts blob from the persistent store, at any age —
    None when it was never cached (never fetched here)."""
    from data import cache, sec_client
    blob = cache.get(sec_client.company_facts_cache_key(cik), max_age_s=None)
    if not (blob and blob.get("facts")):
        return None
    # The dashboard's read-time overlays (lagging 10-Q, class shares,
    # per-series preferred, MSR, stock split) — per-accession caches that
    # refresh-universe warms for every bank before this job; they complete
    # the latest quarter exactly as the dashboard sees it.
    return sec_client._overlay(int(cik), blob)


def _quarter_end_dates(facts: dict) -> list[str]:
    """Every calendar quarter-end a 10-K/10-Q tagged a balance sheet for
    (Assets or either equity total) — sec_client._balance_sheet_date's
    concepts, all dates instead of the latest."""
    from data.sec_client import _SE, _SE_NCI
    ug = facts.get("facts", {}).get("us-gaap", {})
    ends = set()
    for c in ("Assets", _SE, _SE_NCI):
        for e in ug.get(c, {}).get("units", {}).get("USD", []):
            d = _iso_to_date(e.get("end"))
            if (e.get("form") in ("10-K", "10-Q") and d
                    and (d.month, d.day) in _QUARTER_ENDS):
                ends.add(d.isoformat())
    return sorted(ends)


def _without_10q_annuals(facts: dict, concepts) -> dict:
    """A view of `facts` without 10-Q facts spanning a full year for
    `concepts`: a 12-month duration is a 10-K fact, never a 10-Q's
    (sec_client._extract_ttm_value's rule — FCBC's Q1-2026 10-Q tags FY2025
    = its Q1 value). data.sec_period takes the latest-filed leg, so such a
    tag would otherwise become the FY in Q4 = FY − 9M."""
    ug = dict(facts.get("facts", {}).get("us-gaap", {}))
    for c in concepts:
        node = ug.get(c)
        if not node:
            continue
        units = {}
        for u, rows in node.get("units", {}).items():
            keep = []
            for e in rows:
                s, t = _iso_to_date(e.get("start")), _iso_to_date(e.get("end"))
                if (e.get("form") == "10-Q" and s and t
                        and 350 <= (t - s).days <= 380):
                    continue
                keep.append(e)
            units[u] = keep
        ug[c] = {**node, "units": units}
    return {**facts, "facts": {**facts.get("facts", {}), "us-gaap": ug}}


def _ni_concept(facts: dict) -> str | None:
    """First concept of the dashboard's net-income order with any 10-K/10-Q
    duration fact — chosen once per bank so quarters never mix concepts."""
    ug = facts.get("facts", {}).get("us-gaap", {})
    for c in _NI_CONCEPTS:
        for rows in ug.get(c, {}).get("units", {}).values():
            if any(e.get("form") in ("10-K", "10-Q") and e.get("start") for e in rows):
                return c
    return None


def _count_at(facts: dict, concept: str, end: str) -> float | None:
    """A share count tagged exactly at `end` by a 10-K/10-Q, latest filing
    winning (sec_client._instant_at's rule, shares unit)."""
    rows = [e for e in facts.get("facts", {}).get("us-gaap", {})
            .get(concept, {}).get("units", {}).get("shares", [])
            if e.get("form") in ("10-K", "10-Q") and e.get("end") == end
            and e.get("val") is not None]
    if not rows:
        return None
    return float(max(rows, key=lambda e: e.get("filed", ""))["val"])


def _shares_at(facts: dict, end: str) -> float | None:
    """Common shares outstanding AT balance-sheet date `end`, on the snapshot
    chain's same-date rules (sec_client.get_latest_fundamentals, which only
    resolves the latest date): CommonStockSharesOutstanding at `end`, unless
    it is a rounded 100M placeholder (USB); else issued − treasury, both
    tagged at `end` (JPM / IBOC tag the primary count annually or never).
    When both exist and disagree by > _SHARE_TAG_TOL (the FGBI shape), a dei
    cover count dated 0-120 days after `end` arbitrates; one backing neither
    → None; no cover → the direct tag stands (_reconcile_same_date_shares).
    No cover-page / weighted-average substitution: those are not
    balance-sheet-date counts."""
    from data.sec_client import _COVER_WITNESS_DAYS, _SHARE_TAG_TOL
    cso = _count_at(facts, "CommonStockSharesOutstanding", end)
    if cso is not None and cso % 100_000_000 == 0:
        cso = None
    issued = _count_at(facts, "CommonStockSharesIssued", end)
    treasury = _count_at(facts, "TreasuryStockCommonShares", end)
    derived = (issued - treasury if issued and issued > 0 and treasury is not None
               else None)
    if derived is not None and derived <= 0:
        derived = None
    if cso is None or derived is None:
        return cso if cso is not None else derived
    if abs(cso - derived) / derived <= _SHARE_TAG_TOL:
        return cso
    d = date.fromisoformat(end)
    covers = [r for u in facts.get("facts", {}).get("dei", {})
              .get("EntityCommonStockSharesOutstanding", {}).get("units", {}).values()
              for r in u if r.get("val") and _iso_to_date(r.get("end"))
              and 0 <= (_iso_to_date(r["end"]) - d).days <= _COVER_WITNESS_DAYS]
    if not covers:
        return cso
    cover = min(covers, key=lambda r: (r["end"], r.get("filed", "")))["val"]
    for x in (cso, derived):
        if abs(cover - x) / x <= _SHARE_TAG_TOL:
            return x
    return None


def _bank_goodwill_floor(cert: int | None) -> dict[str, float]:
    """{iso quarter-end: Σ INTANGW $ over the charters that reported it} — a
    LOWER bound on the bank subsidiaries' goodwill (goodwill is ≥ 0), used
    only to prove holdco goodwill untagged. Never displayed."""
    if not cert:
        return {}
    from data.fdic_history_store import _norm_repdte
    out: dict[str, float] = {}
    for _c, _s, rec in _group_history(cert):
        rep, gw = _norm_repdte(rec.get("REPDTE")), _num(rec.get("INTANGW"))
        if rep and gw is not None:
            iso = f"{rep[:4]}-{rep[4:6]}-{rep[6:]}"
            out[iso] = out.get(iso, 0.0) + gw * 1000
    return out


def _break_events(facts: dict) -> list[tuple[str, str, str, str, float, float]]:
    """(old filed, new filed, concept, end, old value, new value) for every
    10-K/10-Q re-report of total assets or an equity total at a date already
    on file that moved more than _BASIS_BREAK_MOVE."""
    from data.sec_client import _SE, _SE_NCI
    ug = facts.get("facts", {}).get("us-gaap", {})
    events = []
    for c in ("Assets", _SE, _SE_NCI):
        by_end: dict[str, list[tuple[str, float]]] = {}
        for e in ug.get(c, {}).get("units", {}).get("USD", []):
            if (e.get("form") in ("10-K", "10-Q") and e.get("end")
                    and e.get("val") is not None and e.get("filed")):
                by_end.setdefault(e["end"], []).append((e["filed"], float(e["val"])))
        for end, reports in by_end.items():
            reports.sort()
            for (f0, v0), (f1, v1) in zip(reports, reports[1:]):
                if v0 and abs(v1 / v0 - 1) > _BASIS_BREAK_MOVE:
                    events.append((f0, f1, c, end, v0, v1))
    return events


def _basis_break(facts: dict) -> tuple[str, str] | None:
    """(filed, period end) of the FIRST filing on the newest reporting basis,
    when a 10-K/10-Q re-reported total assets or an equity total at a date
    already on file at a value more than _BASIS_BREAK_MOVE away — a reverse
    merger or entity-level restatement, after which the registrant's earlier
    figures describe a different company. The old basis lasted through the
    latest filing any re-report contradicts; the new one starts with the
    next contradicting filing. MCHB: the Q2-2025 10-Q (2025-08-06) is the
    last HomeStreet-basis filing; the Q3-2025 10-Q (filed 2025-11-17,
    period 2025-09-30) restated 2024-12-31 equity $397.0M → $2,301.9M
    (Mechanics, the accounting acquirer). None when nothing moved that far
    (the overwhelming case: ordinary restatements move a few percent).

    A change of entity restates the balance sheet itself, so BOTH total
    assets and an equity total must have moved: an equity tag alone moving
    is a tagging artifact (Regions' 2020-05-06 10-Q tagged the NCI column
    as the NCI-inclusive total: $15.5B → $11M)."""
    from data.sec_client import _SE, _SE_NCI
    events = _break_events(facts)
    moved = {ev[2] for ev in events}
    if "Assets" not in moved or not moved - {"Assets"}:
        return None
    last_old = max(f0 for f0, *_ in events)
    best = min(f1 for _f0, f1, *_ in events if f1 > last_old)
    ug = facts.get("facts", {}).get("us-gaap", {})
    period = max(e["end"] for c in ("Assets", _SE, _SE_NCI)
                 for e in ug.get(c, {}).get("units", {}).get("USD", [])
                 if e.get("filed") == best and e.get("form") in ("10-K", "10-Q")
                 and e.get("end"))
    return best, period


def _filed_since(facts: dict, filed: str) -> dict:
    """A view of `facts` holding only entries filed on or after `filed` —
    the post-break basis (the registrant's restated history)."""
    out_facts = {}
    for ns, concepts in facts.get("facts", {}).items():
        out_facts[ns] = {
            c: {**node, "units": {u: [e for e in rows if (e.get("filed") or "") >= filed]
                                  for u, rows in node.get("units", {}).items()}}
            for c, node in concepts.items()}
    return {**facts, "facts": out_facts}


def _preferred_value_contradicts(facts: dict, end: str, equity: float | None) -> bool:
    """The resolver read NO preferred at `end`, yet a carrying-value tag of
    its own ladder sits at exactly `end` with a material value (>= the
    resolver's own immateriality floor of equity). JPM's Q1-2014 10-Q tags
    PreferredStockSharesIssued = 0 beside $15.083B of preferred at
    2014-03-31; the explicit-zero rule then read 'none' and TCE came out
    $15B high. A common line tagged as preferred (PLBC) is not a
    contradiction — the resolver rightly skips it."""
    from data import sec_client as sc
    if not equity or sc._common_as_preferred_at(facts, end):
        return False
    floor = sc._PREFERRED_IMMATERIAL_OF_EQUITY * abs(equity)
    return any((sc._usd_at(facts, c, end) or 0.0) > floor
               for c in _PREFERRED_VALUE_LADDER)


def _first_balance_sheet(facts: dict) -> str:
    """Earliest 10-K/10-Q total-assets date in `facts` ("9999" when none)."""
    ends = [e["end"] for e in facts.get("facts", {}).get("us-gaap", {})
            .get("Assets", {}).get("units", {}).get("USD", [])
            if e.get("form") in ("10-K", "10-Q") and e.get("end")]
    return min(ends, default="9999")


def _flow_context(facts: dict) -> tuple[str | None, dict]:
    """(net-income concept, flows view) — computed once per facts view."""
    ni_c = _ni_concept(facts)
    return ni_c, _without_10q_annuals(facts, tuple(c for c in (ni_c, _DPS_CONCEPT) if c))


def _holdco_row(ticker: str, cik: int, facts: dict, flow_ctx: tuple, end: str,
                bank_goodwill: dict[str, float], basis_note: str | None) -> dict:
    from analysis.valuation import _holdco_tce_at
    from data import sec_client as sc
    from data import sec_period as sp

    d = date.fromisoformat(end)
    year, q = d.year, d.month // 3
    eq_tup, _, eq_note = sc._parent_equity_at(facts, end)
    equity = float(eq_tup[0]) if eq_tup and eq_tup[0] is not None else None
    assets = sc._usd_at(facts, "Assets", end)
    note = None
    if end < _first_balance_sheet(facts):
        # An equity-statement comparative from before the filer's first
        # tagged balance sheet (IBOC 2008-12-31: TARP preferred issued that
        # month, goodwill first tagged 2009): the resolvers' "nothing tagged
        # = none" reading of preferred and intangibles is unfounded there.
        pfd, pfd_present, tce = None, None, None
        note = "before the filer's first tagged balance sheet (equity-statement comparative only)"
    else:
        pfd, pfd_present = sc._resolve_preferred_stock(facts, as_of=end)
        tce = _holdco_tce_at(facts, end)
        if equity is None:
            note = eq_note or "parent equity not tagged at this date"
        elif pfd_present and pfd is None:
            note = "preferred outstanding but its carrying value is unresolved"
        elif not pfd_present and _preferred_value_contradicts(facts, end, equity):
            pfd, pfd_present, tce = None, True, None
            note = ("preferred value tagged at this date but a zero share count "
                    "reads as none — unresolved")
    adj = (equity - (pfd or 0.0) - tce) if tce is not None else None
    gw_bank = bank_goodwill.get(end)
    if (tce is not None and not adj and gw_bank
            and gw_bank > _BANK_GOODWILL_MATERIAL * abs(tce)):
        tce, adj = None, None
        note = ("holdco goodwill untagged in XBRL while the bank subsidiary "
                "reports goodwill")
    ni_c, flows = flow_ctx
    ni = sp._flow_for(flows, (ni_c,), "Q", year, q) if ni_c else None
    eps = sp._standalone_quarter(facts, ("EarningsPerShareDiluted",), year, q,
                                 ("USD/shares",))
    dps = sp._flow_for(flows, (_DPS_CONCEPT,), "Q", year, q,
                       unit_types=("USD/shares", "USD"))
    return {
        "ticker": ticker,
        "cik": int(cik),
        "period_end": d,
        "total_equity": equity,
        "preferred": float(pfd) if pfd is not None else None,
        "preferred_present": None if pfd_present is None else bool(pfd_present),
        "goodwill": sc._usd_at(facts, "Goodwill", end),
        "intangible_adjustment": adj,
        "tce": tce,
        "tce_note": note if tce is None else None,
        "total_assets": assets,
        "shares_outstanding": _shares_at(facts, end),
        "net_income_quarter": _num(ni),
        "net_income_concept": ni_c if ni is not None else None,
        "diluted_eps_quarter": _num(eps),
        "dividends_per_share_quarter": _num(dps),
        "basis_note": basis_note,
        "filed_form": (eq_tup[3] or None) if equity is not None else None,
        "filed_date": _iso_to_date(eq_tup[2]) if equity is not None else None,
    }


_ROW_VALUES = ("total_equity", "total_assets", "shares_outstanding",
               "net_income_quarter", "diluted_eps_quarter",
               "dividends_per_share_quarter")


def _holdco_rows(ticker: str, cik: int, facts: dict,
                 bank_goodwill: dict[str, float]) -> list[dict]:
    """One row per calendar quarter-end. Dates before a basis break's period
    are read ONLY from filings on the new basis (_filed_since), so no row
    mixes the old registrant's figures with the restated company's (MCHB
    2025-06-30: HomeStreet's own 10-Q tagged $7.6B assets and 18.9M shares
    beside Mechanics' restated $2.42B equity). A date the new basis never
    restated is dropped rather than shown on the old basis."""
    brk = _basis_break(facts)
    ctx = _flow_context(facts)
    if brk:
        view = _filed_since(facts, brk[0])
        view_ctx = _flow_context(view)
        note = (f"pre-{brk[1]} figures from filings since {brk[0]} only — that "
                f"filing restated the company's history (reverse merger / "
                f"entity restatement)")
    rows = []
    for end in _quarter_end_dates(facts):
        if brk and end < brk[1]:
            row = _holdco_row(ticker, cik, view, view_ctx, end, bank_goodwill, note)
            if all(row[k] is None for k in _ROW_VALUES):
                continue
        else:
            row = _holdco_row(ticker, cik, facts, ctx, end, bank_goodwill, None)
        rows.append(row)
    return rows


def build_holdco_quarterly(tickers: list[str]) -> pd.DataFrame:
    """SEC holding-company series per quarter-end (see module doc)."""
    rows: list[dict] = []
    skipped: dict[str, list[str]] = {"no_cik": [], "no_cached_facts": []}
    breaks: list[tuple[str, str, str]] = []   # (ticker, filed, period) — reviewed per bank
    for ticker in tickers:
        t = ticker.upper()
        cik = _cik_for(t)
        if not cik:
            skipped["no_cik"].append(t)
            continue
        facts = _cached_facts(cik)
        if not facts:
            skipped["no_cached_facts"].append(t)
            continue
        brk = _basis_break(facts)
        if brk:
            breaks.append((t, *brk))
        rows += _holdco_rows(t, cik, facts, _bank_goodwill_floor(_cert_for(t)))
    df = pd.DataFrame(rows, columns=list(COLUMNS_HOLDCO_QUARTERLY))
    if not df.empty:
        df = df.sort_values(["ticker", "period_end"]).reset_index(drop=True)
    df.attrs["skipped"] = skipped
    # Every bank the reverse-merger basis-break rule fired on — the rule was
    # tuned on 37 cached blobs (BANC, MCHB); each prod firing gets reviewed.
    df.attrs["basis_breaks"] = breaks
    print(f"[export] holdco_quarterly: basis breaks {breaks}")
    print(f"[export] holdco_quarterly: {len(df)} rows; skipped "
          + ", ".join(f"{k}={len(v)}" for k, v in skipped.items()), flush=True)
    return df
