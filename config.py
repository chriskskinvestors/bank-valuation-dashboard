"""
Central configuration for the Bank Valuation Dashboard.

TO ADD A NEW METRIC: append a dict to the METRICS list below.
TO REMOVE A METRIC:  delete or comment out its dict entry.
No other code changes needed.
"""

import os

# ---------------------------------------------------------------------------
# IBKR connection
# ---------------------------------------------------------------------------
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "7497"))
IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "1"))

# ---------------------------------------------------------------------------
# SEC EDGAR (requires a User-Agent with your name/email)
# ---------------------------------------------------------------------------
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "BankValuationDashboard admin@company.com")

# ---------------------------------------------------------------------------
# Refresh / auto-rerun
# ---------------------------------------------------------------------------
PRICE_REFRESH_SECONDS = int(os.getenv("PRICE_REFRESH_SECONDS", "5"))
FUNDAMENTAL_CACHE_TTL_HOURS = int(os.getenv("FUNDAMENTAL_CACHE_TTL_HOURS", "24"))

# ---------------------------------------------------------------------------
# Default watchlist (users can edit in the UI)
# ---------------------------------------------------------------------------
DEFAULT_WATCHLIST = [
    "SFST", "CFFI", "CBNK", "FBIZ", "CCBG", "IBOC", "CARE", "RNST",
    "HTB", "FRBA", "SMBK", "BFST", "TCBX", "FMBH", "HBNC", "PLBC",
    "FSRL", "BANR", "MCBI", "BRBS", "FSBW", "SPFI", "ALRS", "FMNB",
    "HFWA", "BAFN", "CBAN", "OVBC", "FRME", "LNKB", "WAL", "TSBK",
    "BKU", "PGC", "SBFG", "FNWB", "INBC", "CCNB", "JMSB", "RVSB",
    "SLBK", "INBK", "RMBI", "TFSL", "CBK", "IBTN", "FGBI", "PNFP",
    "OZK", "FHN", "UBSI", "FBNC", "FFWM", "FFIN", "HOMB", "EGBN",
    "ISBA", "BANC", "FLG", "VLY", "KEY", "HBAN", "ZION",
]

DEFAULT_PORTFOLIO = []  # User populates via sidebar

# ---------------------------------------------------------------------------
# Metric registry
# ---------------------------------------------------------------------------
# Each entry drives: what data is fetched, how it's displayed, and how it's
# color-coded.  The UI reads this list to build columns dynamically.
#
# Fields:
#   key          – unique internal id
#   label        – column header shown in the UI
#   source       – "fdic" | "sec" | "ibkr" | "computed"
#   fdic_field   – (if source=fdic) FDIC API field name
#   sec_concept  – (if source=sec) XBRL concept name
#   format       – "pct" | "currency" | "ratio" | "number" | "millions" | "billions"
#   decimals     – decimal places (default 2)
#   color_rule   – "higher_better" | "lower_better" | None
#   thresholds   – {"good": val, "warn": val} for color-coding
#   category     – grouping header in the UI
# ---------------------------------------------------------------------------

METRICS = [
    # ── Market ──────────────────────────────────────────────────────────
    {
        "key": "price", "label": "Price", "source": "ibkr",
        "format": "currency", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Market",
    },
    {
        "key": "change_pct", "label": "Chg %", "source": "ibkr",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 0, "warn": -2},
        "category": "Market",
    },
    {
        "key": "volume", "label": "Volume", "source": "ibkr",
        "format": "number", "decimals": 0,
        "color_rule": None, "thresholds": {},
        "category": "Market",
    },
    {
        "key": "market_cap", "label": "Mkt Cap ($B)", "source": "computed",
        "format": "billions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Market",
    },

    # ── Valuation ───────────────────────────────────────────────────────
    {
        "key": "eps", "label": "EPS", "source": "sec", "sec_concept": "eps",
        "format": "currency", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 2.0, "warn": 0.5},
        "category": "Valuation",
    },
    {
        "key": "pe_ratio", "label": "P/E", "source": "computed",
        "format": "ratio", "decimals": 1,
        "color_rule": "lower_better", "thresholds": {"good": 12, "warn": 18},
        "category": "Valuation",
    },
    {
        "key": "tbvps", "label": "TBV/Sh", "source": "sec", "sec_concept": "tangible_book_value_per_share",
        "format": "currency", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Valuation",
    },
    {
        "key": "ptbv_ratio", "label": "P/TBV", "source": "computed",
        "format": "ratio", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 1.2, "warn": 2.0},
        "category": "Valuation",
    },
    {
        "key": "dividend_yield", "label": "Div Yield", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 3.0, "warn": 1.5},
        "category": "Valuation",
    },

    # ── Fair Value Screen ──────────────────────────────────────────────
    # Blended ROATCE (75% 4Q avg + 25% current) drives a fair P/TBV.
    # 10% ROATCE = 1.0x TBV, 12% = 1.2x, etc. (linear: ROATCE/10).
    # Discount > 15% flags a potential buying opportunity.
    {
        "key": "roatce_blended", "label": "ROATCE Bl.", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 12, "warn": 7},
        "category": "Fair Value",
    },
    {
        "key": "fair_ptbv", "label": "Fair P/TBV", "source": "computed",
        "format": "ratio", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Fair Value",
    },
    {
        "key": "fair_price", "label": "Fair Price", "source": "computed",
        "format": "currency", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Fair Value",
    },
    {
        "key": "ptbv_discount", "label": "Discount", "source": "computed",
        "format": "pct", "decimals": 1,
        "color_rule": "higher_better", "thresholds": {"good": 15, "warn": 0},
        "category": "Fair Value",
    },

    # ── Profitability ───────────────────────────────────────────────────
    {
        "key": "roaa", "label": "ROAA", "source": "fdic", "fdic_field": "ROA",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 1.0, "warn": 0.5},
        "category": "Profitability",
    },
    {
        "key": "roaa_4q", "label": "ROAA 4Q", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 1.0, "warn": 0.5},
        "category": "Profitability",
    },
    {
        "key": "roatce", "label": "ROATCE (Sub)", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 12, "warn": 7},
        "category": "Profitability",
    },
    {
        "key": "roatce_4q", "label": "ROATCE 4Q (Sub)", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 12, "warn": 7},
        "category": "Profitability",
    },
    {
        "key": "roatce_holdco", "label": "ROATCE (HoldCo)", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 14, "warn": 8},
        "category": "Profitability",
    },
    {
        "key": "nim", "label": "NIM", "source": "fdic", "fdic_field": "NIMY",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 3.0, "warn": 2.0},
        "category": "Profitability",
    },
    {
        "key": "nim_4q", "label": "NIM 4Q", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 3.0, "warn": 2.0},
        "category": "Profitability",
    },
    {
        "key": "efficiency_ratio", "label": "Efficiency", "source": "fdic", "fdic_field": "EEFFR",
        "format": "pct", "decimals": 1,
        "color_rule": "lower_better", "thresholds": {"good": 55, "warn": 65},
        "category": "Profitability",
    },

    # ── Credit Quality ──────────────────────────────────────────────────
    {
        "key": "npl_ratio", "label": "NPL Ratio", "source": "fdic", "fdic_field": "NCLNLSR",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 1.0, "warn": 2.0},
        "category": "Credit Quality",
    },
    {
        "key": "nco_ratio", "label": "NCO Ratio", "source": "fdic", "fdic_field": "NTLNLSR",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 0.5, "warn": 1.0},
        "category": "Credit Quality",
    },
    {
        "key": "allowance_loans", "label": "ALL/Loans", "source": "fdic", "fdic_field": "ELNANTR",
        "format": "pct", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Credit Quality",
    },

    # ── Capital ─────────────────────────────────────────────────────────
    {
        "key": "cet1_ratio", "label": "CET1", "source": "fdic", "fdic_field": "IDT1CER",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 10, "warn": 7},
        "category": "Capital",
    },
    {
        "key": "total_capital_ratio", "label": "Total Capital", "source": "fdic", "fdic_field": "RBCRWAJ",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 12, "warn": 8},
        "category": "Capital",
    },
    {
        "key": "leverage_ratio", "label": "T1 Leverage", "source": "fdic", "fdic_field": "RBCT1JR",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 8, "warn": 5},
        "category": "Capital",
    },

    # ══════════════════════════════════════════════════════════════════════
    # BALANCE SHEET TAB — full granularity
    # ══════════════════════════════════════════════════════════════════════

    # ── Balance Sheet Summary ────────────────────────────────────────────
    {
        "key": "total_assets", "label": "Total Assets ($B)", "source": "fdic", "fdic_field": "ASSET",
        "format": "billions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Balance Sheet",
    },
    {
        "key": "total_loans", "label": "Loans ($B)", "source": "fdic", "fdic_field": "LNLSNET",
        "format": "billions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Balance Sheet",
    },
    {
        "key": "total_loans_gross", "label": "Loans Gross ($B)", "source": "fdic", "fdic_field": "LNLSGR",
        "format": "billions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Balance Sheet",
    },
    {
        "key": "securities", "label": "Securities ($B)", "source": "fdic", "fdic_field": "SC",
        "format": "billions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Balance Sheet",
    },
    {
        "key": "cash_balances", "label": "Cash ($B)", "source": "fdic", "fdic_field": "CHBAL",
        "format": "billions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Balance Sheet",
    },
    {
        "key": "fed_funds_sold", "label": "Fed Funds ($M)", "source": "fdic", "fdic_field": "FREPO",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Balance Sheet",
    },
    {
        "key": "total_liab", "label": "Liabilities ($B)", "source": "fdic", "fdic_field": "LIAB",
        "format": "billions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Balance Sheet",
    },
    {
        "key": "total_equity", "label": "Equity ($B)", "source": "fdic", "fdic_field": "EQTOT",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Balance Sheet",
    },
    {
        "key": "intangibles", "label": "Intangibles ($M)", "source": "fdic", "fdic_field": "INTAN",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Balance Sheet",
    },
    {
        "key": "ore", "label": "OREO ($M)", "source": "fdic", "fdic_field": "ORE",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Balance Sheet",
    },
    {
        "key": "trading_assets", "label": "Trading ($M)", "source": "fdic", "fdic_field": "TRADE",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Balance Sheet",
    },

    # ── Loan Mix ($ amounts) ─────────────────────────────────────────────
    {
        "key": "ln_re_total", "label": "RE Loans ($B)", "source": "fdic", "fdic_field": "LNRE",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Loan Mix",
    },
    {
        "key": "ln_re_residential", "label": "1-4 Fam RE ($B)", "source": "fdic", "fdic_field": "LNRERES",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Loan Mix",
    },
    {
        "key": "ln_re_nres_oo", "label": "CRE Ownr Occ ($B)", "source": "fdic", "fdic_field": "LNRENROW",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Loan Mix",
    },
    {
        "key": "ln_re_nres_noo", "label": "CRE Non-OO ($B)", "source": "fdic", "fdic_field": "LNRENROT",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Loan Mix",
    },
    {
        "key": "ln_re_nres", "label": "CRE Total ($B)", "source": "fdic", "fdic_field": "LNRENRES",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Loan Mix",
    },
    {
        "key": "ln_re_multifam", "label": "Multifamily ($B)", "source": "fdic", "fdic_field": "LNREMULT",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Loan Mix",
    },
    {
        "key": "ln_re_construct", "label": "Construction ($B)", "source": "fdic", "fdic_field": "LNRECONS",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Loan Mix",
    },
    {
        "key": "ln_re_ag", "label": "Ag RE ($M)", "source": "fdic", "fdic_field": "LNREAG",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Loan Mix",
    },
    {
        "key": "ln_ci", "label": "C&I ($B)", "source": "fdic", "fdic_field": "LNCI",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Loan Mix",
    },
    {
        "key": "ln_consumer", "label": "Consumer ($M)", "source": "fdic", "fdic_field": "LNCON",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Loan Mix",
    },
    {
        "key": "ln_auto", "label": "Auto ($M)", "source": "fdic", "fdic_field": "LNAUTO",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Loan Mix",
    },
    {
        "key": "ln_credit_card", "label": "Credit Card ($M)", "source": "fdic", "fdic_field": "LNCRCD",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Loan Mix",
    },
    {
        "key": "ln_ag", "label": "Ag Prod ($M)", "source": "fdic", "fdic_field": "LNAG",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Loan Mix",
    },

    # ── Loan Mix (% of total loans, computed) ────────────────────────────
    {
        "key": "ln_re_pct", "label": "RE %", "source": "computed",
        "format": "pct", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Loan Concentration",
    },
    {
        "key": "ln_cre_pct", "label": "CRE %", "source": "computed",
        "format": "pct", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Loan Concentration",
    },
    {
        "key": "ln_resi_pct", "label": "1-4 Fam %", "source": "computed",
        "format": "pct", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Loan Concentration",
    },
    {
        "key": "ln_multifam_pct", "label": "Multifam %", "source": "computed",
        "format": "pct", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Loan Concentration",
    },
    {
        "key": "ln_construct_pct", "label": "Construct %", "source": "computed",
        "format": "pct", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Loan Concentration",
    },
    {
        "key": "ln_ci_pct", "label": "C&I %", "source": "computed",
        "format": "pct", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Loan Concentration",
    },
    {
        "key": "ln_consumer_pct", "label": "Consumer %", "source": "computed",
        "format": "pct", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Loan Concentration",
    },
    {
        "key": "cre_to_capital", "label": "CRE/Capital", "source": "computed",
        "format": "pct", "decimals": 0,
        "color_rule": "lower_better", "thresholds": {"good": 250, "warn": 300},
        "category": "Loan Concentration",
    },

    # ── Deposits ─────────────────────────────────────────────────────────
    {
        "key": "total_deposits", "label": "Deposits ($B)", "source": "fdic", "fdic_field": "DEP",
        "format": "billions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Deposits",
    },
    {
        "key": "core_deposits", "label": "Core Dep ($B)", "source": "fdic", "fdic_field": "COREDEP",
        "format": "billions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Deposits",
    },
    {
        "key": "insured_deposits", "label": "Insured ($B)", "source": "fdic", "fdic_field": "DEPINS",
        "format": "billions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Deposits",
    },
    {
        "key": "uninsured_deposits", "label": "Uninsured ($B)", "source": "fdic", "fdic_field": "DEPUNINS",
        "format": "billions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Deposits",
    },
    {
        "key": "brokered_deposits", "label": "Brokered ($B)", "source": "fdic", "fdic_field": "BRO",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Deposits",
    },
    {
        "key": "demand_deposits", "label": "Demand ($B)", "source": "fdic", "fdic_field": "DDT",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Deposits",
    },
    {
        "key": "mmda_savings", "label": "MMDA/Savings ($B)", "source": "fdic", "fdic_field": "NTRSMMDA",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Deposits",
    },
    {
        "key": "large_time_dep", "label": "Large Time ($B)", "source": "fdic", "fdic_field": "DEPLGAMT",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Deposits",
    },
    {
        "key": "dep_lt_250k", "label": "Dep < $250K ($B)", "source": "fdic", "fdic_field": "DEPSMAMT",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Deposits",
    },
    {
        "key": "int_bearing_dep", "label": "Int-Bear Dep ($B)", "source": "fdic", "fdic_field": "DEPIDOM",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Deposits",
    },
    {
        "key": "nonint_dep", "label": "Non-Int Dep ($B)", "source": "fdic", "fdic_field": "DEPNIDOM",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Deposits",
    },

    # ── Deposit Ratios (computed) ────────────────────────────────────────
    {
        "key": "uninsured_pct", "label": "Uninsured %", "source": "computed",
        "format": "pct", "decimals": 1,
        "color_rule": "lower_better", "thresholds": {"good": 30, "warn": 50},
        "category": "Deposit Ratios",
    },
    {
        "key": "core_dep_pct", "label": "Core Dep %", "source": "computed",
        "format": "pct", "decimals": 1,
        "color_rule": "higher_better", "thresholds": {"good": 80, "warn": 60},
        "category": "Deposit Ratios",
    },
    {
        "key": "brokered_pct", "label": "Brokered %", "source": "computed",
        "format": "pct", "decimals": 1,
        "color_rule": "lower_better", "thresholds": {"good": 10, "warn": 25},
        "category": "Deposit Ratios",
    },
    {
        "key": "nonint_dep_pct", "label": "Non-Int Dep %", "source": "computed",
        "format": "pct", "decimals": 1,
        "color_rule": "higher_better", "thresholds": {"good": 30, "warn": 15},
        "category": "Deposit Ratios",
    },

    # ── Capital Dynamics (computed from history + SEC shares) ────────────
    {
        "key": "cet1_current", "label": "CET1 %", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 10.0, "warn": 8.0},
        "category": "Capital Dynamics",
    },
    {
        "key": "cet1_qoq_pp", "label": "CET1 QoQ (pp)", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 0, "warn": -0.25},
        "category": "Capital Dynamics",
    },
    {
        "key": "tbv_cagr_1y", "label": "TBV CAGR 1Y", "source": "computed",
        "format": "pct", "decimals": 1,
        "color_rule": "higher_better", "thresholds": {"good": 8.0, "warn": 3.0},
        "category": "Capital Dynamics",
    },
    {
        "key": "payout_ratio_4q", "label": "Payout 4Q %", "source": "computed",
        "format": "pct", "decimals": 0,
        "color_rule": "lower_better", "thresholds": {"good": 50, "warn": 80},
        "category": "Capital Dynamics",
    },
    {
        "key": "buyback_capacity_usd", "label": "Free Capital", "source": "computed",
        "format": "dollars_auto", "decimals": 1,
        "color_rule": "higher_better", "thresholds": {"good": 0, "warn": -1_000_000},
        "category": "Capital Dynamics",
    },
    {
        "key": "capital_alerts_count", "label": "⚠ Cap Alerts", "source": "computed",
        "format": "number", "decimals": 0,
        "color_rule": "lower_better", "thresholds": {"good": 0, "warn": 1},
        "category": "Capital Dynamics",
    },

    # ── Capital Return Attribution (SEC-sourced) ─────────────────────────
    {
        "key": "shareholder_yield", "label": "Shareholder Yield", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 5.0, "warn": 2.0},
        "category": "Capital Return",
    },
    {
        "key": "dividend_yield_sec", "label": "Div Yield", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 3.0, "warn": 1.0},
        "category": "Capital Return",
    },
    {
        "key": "buyback_yield", "label": "Buyback Yield", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 3.0, "warn": 1.0},
        "category": "Capital Return",
    },
    {
        "key": "payout_ratio_ttm", "label": "Payout % (TTM)", "source": "computed",
        "format": "pct", "decimals": 0,
        "color_rule": None, "thresholds": {},
        "category": "Capital Return",
    },
    {
        "key": "total_return_ratio_ttm", "label": "Total Ret % (TTM)", "source": "computed",
        "format": "pct", "decimals": 0,
        "color_rule": "higher_better", "thresholds": {"good": 80, "warn": 40},
        "category": "Capital Return",
    },
    {
        "key": "share_change_pct_ttm", "label": "Share Δ TTM", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": -2.0, "warn": 0.0},
        "category": "Capital Return",
    },
    {
        "key": "dps_yoy_pct", "label": "DPS YoY", "source": "computed",
        "format": "pct", "decimals": 1,
        "color_rule": "higher_better", "thresholds": {"good": 8.0, "warn": 0.0},
        "category": "Capital Return",
    },
    {
        "key": "dividends_ttm", "label": "Divs TTM", "source": "computed",
        "format": "dollars_auto", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Capital Return",
    },
    {
        "key": "buybacks_ttm", "label": "Buybacks TTM", "source": "computed",
        "format": "dollars_auto", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Capital Return",
    },

    # ── Credit Dynamics (computed from history) ──────────────────────────
    {
        "key": "nco_4q_trend_bps", "label": "NCO Δ 4Q (bps)", "source": "computed",
        "format": "number", "decimals": 0,
        "color_rule": "lower_better", "thresholds": {"good": 0, "warn": 15},
        "category": "Credit Dynamics",
    },
    {
        "key": "npl_trend_bps", "label": "NPL QoQ (bps)", "source": "computed",
        "format": "number", "decimals": 0,
        "color_rule": "lower_better", "thresholds": {"good": 0, "warn": 10},
        "category": "Credit Dynamics",
    },
    {
        "key": "pd_migration_bps", "label": "PD 30-89 QoQ (bps)", "source": "computed",
        "format": "number", "decimals": 0,
        "color_rule": "lower_better", "thresholds": {"good": 0, "warn": 10},
        "category": "Credit Dynamics",
    },
    {
        "key": "reserve_coverage_pct", "label": "Rsv/NPL %", "source": "computed",
        "format": "pct", "decimals": 0,
        "color_rule": "higher_better", "thresholds": {"good": 200, "warn": 100},
        "category": "Credit Dynamics",
    },
    {
        "key": "worst_segment_npl", "label": "Worst Seg NPL %", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 1.0, "warn": 2.0},
        "category": "Credit Dynamics",
    },
    {
        "key": "credit_alerts_count", "label": "⚠ Credit Alerts", "source": "computed",
        "format": "number", "decimals": 0,
        "color_rule": "lower_better", "thresholds": {"good": 0, "warn": 1},
        "category": "Credit Dynamics",
    },

    # ── Deposit Dynamics (computed from history) ─────────────────────────
    {
        "key": "deposit_cycle_beta", "label": "Cycle β", "source": "computed",
        "format": "ratio", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 0.30, "warn": 0.50},
        "category": "Deposit Dynamics",
    },
    {
        "key": "deposit_rolling_beta", "label": "Rolling β (4Q)", "source": "computed",
        "format": "ratio", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 0.30, "warn": 0.50},
        "category": "Deposit Dynamics",
    },
    {
        "key": "dep_qoq_growth", "label": "Dep QoQ %", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 0, "warn": -2},
        "category": "Deposit Dynamics",
    },
    {
        "key": "cod_qoq_bps", "label": "CoD QoQ (bps)", "source": "computed",
        "format": "number", "decimals": 0,
        "color_rule": "lower_better", "thresholds": {"good": 0, "warn": 15},
        "category": "Deposit Dynamics",
    },
    {
        "key": "deposit_alerts_count", "label": "⚠ Alerts", "source": "computed",
        "format": "number", "decimals": 0,
        "color_rule": "lower_better", "thresholds": {"good": 0, "warn": 1},
        "category": "Deposit Dynamics",
    },

    # ── Securities Portfolio ─────────────────────────────────────────────
    {
        "key": "sec_afs", "label": "AFS ($B)", "source": "fdic", "fdic_field": "SCAF",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Securities",
    },
    {
        "key": "sec_htm", "label": "HTM ($B)", "source": "fdic", "fdic_field": "SCHA",
        "format": "billions", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Securities",
    },
    {
        "key": "sec_ust", "label": "UST ($M)", "source": "fdic", "fdic_field": "SCUST",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Securities",
    },
    {
        "key": "sec_agency", "label": "Agency ($M)", "source": "fdic", "fdic_field": "SCAGE",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Securities",
    },
    {
        "key": "sec_uso", "label": "USG Oblig ($M)", "source": "fdic", "fdic_field": "SCUSO",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Securities",
    },
    {
        "key": "sec_muni", "label": "Muni ($M)", "source": "fdic", "fdic_field": "SCMUNI",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Securities",
    },
    {
        "key": "sec_abs", "label": "ABS ($M)", "source": "fdic", "fdic_field": "SCABS",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Securities",
    },
    {
        "key": "sec_unreal_gl", "label": "Unreal G/L ($M)", "source": "fdic", "fdic_field": "IGLSEC",
        "format": "millions", "decimals": 1,
        "color_rule": "higher_better", "thresholds": {"good": 0, "warn": -50},
        "category": "Securities",
    },
    {
        "key": "sec_htm_unreal", "label": "HTM Unreal ($M)", "source": "fdic", "fdic_field": "SCSNHAA",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Securities",
    },
    {
        "key": "sec_to_assets_pct", "label": "Sec/Assets %", "source": "computed",
        "format": "pct", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Securities",
    },
    {
        "key": "htm_pct", "label": "HTM % of Sec", "source": "computed",
        "format": "pct", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Securities",
    },

    # ── Composition Ratios ───────────────────────────────────────────────
    {
        "key": "loans_to_deposits", "label": "Loans/Dep", "source": "fdic", "fdic_field": "LNLSDEPR",
        "format": "pct", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Composition",
    },
    {
        "key": "loans_to_assets", "label": "Loans/Assets", "source": "fdic", "fdic_field": "LNLSNTV",
        "format": "pct", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Composition",
    },
    {
        "key": "deposits_to_assets", "label": "Dep/Assets", "source": "fdic", "fdic_field": "DEPDASTR",
        "format": "pct", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Composition",
    },
    {
        "key": "earning_assets_pct", "label": "Earning Assets %", "source": "fdic", "fdic_field": "ERNASTR",
        "format": "pct", "decimals": 1,
        "color_rule": "higher_better", "thresholds": {"good": 90, "warn": 85},
        "category": "Composition",
    },
    {
        "key": "equity_to_assets", "label": "Eq/Assets", "source": "fdic", "fdic_field": "EQV",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 9, "warn": 7},
        "category": "Composition",
    },

    # ── Credit Detail ────────────────────────────────────────────────────
    {
        "key": "npl_cre", "label": "NPL CRE %", "source": "fdic", "fdic_field": "NCRER",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 1.0, "warn": 3.0},
        "category": "Credit Detail",
    },
    {
        "key": "npl_resi", "label": "NPL Resi %", "source": "fdic", "fdic_field": "NCRECONR",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 1.0, "warn": 3.0},
        "category": "Credit Detail",
    },
    {
        "key": "npl_multifam", "label": "NPL MF %", "source": "fdic", "fdic_field": "NCREMULR",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 1.0, "warn": 3.0},
        "category": "Credit Detail",
    },
    {
        "key": "npl_nres_re", "label": "NPL NR RE %", "source": "fdic", "fdic_field": "NCRENRER",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 1.0, "warn": 3.0},
        "category": "Credit Detail",
    },
    {
        "key": "npl_ci", "label": "NPL C&I %", "source": "fdic", "fdic_field": "IDNCCIR",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 1.0, "warn": 3.0},
        "category": "Credit Detail",
    },
    {
        "key": "npl_consumer", "label": "NPL Consumer %", "source": "fdic", "fdic_field": "IDNCCONR",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 1.0, "warn": 3.0},
        "category": "Credit Detail",
    },
    {
        "key": "nco_re", "label": "NCO RE %", "source": "fdic", "fdic_field": "NTRER",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 0.3, "warn": 1.0},
        "category": "Credit Detail",
    },
    {
        "key": "nco_ci", "label": "NCO C&I %", "source": "fdic", "fdic_field": "NTCOMRER",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 0.3, "warn": 1.0},
        "category": "Credit Detail",
    },
    {
        "key": "past_due_30_89", "label": "PD 30-89 ($M)", "source": "fdic", "fdic_field": "P3ASSET",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Credit Detail",
    },
    {
        "key": "past_due_90", "label": "PD 90+ ($M)", "source": "fdic", "fdic_field": "P9ASSET",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Credit Detail",
    },
    {
        "key": "reserve_coverage", "label": "Rsv/NPL", "source": "fdic", "fdic_field": "IDERNCVR",
        "format": "pct", "decimals": 1,
        "color_rule": "higher_better", "thresholds": {"good": 100, "warn": 50},
        "category": "Credit Detail",
    },
    {
        "key": "reserve_to_loans", "label": "Rsv/Loans %", "source": "fdic", "fdic_field": "LNATRESR",
        "format": "pct", "decimals": 2,
        "color_rule": None, "thresholds": {},
        "category": "Credit Detail",
    },
    {
        "key": "nco_to_reserve", "label": "NCO/Rsv %", "source": "fdic", "fdic_field": "IDLNCORR",
        "format": "pct", "decimals": 1,
        "color_rule": "lower_better", "thresholds": {"good": 30, "warn": 60},
        "category": "Credit Detail",
    },
    {
        "key": "reserve_nco_coverage", "label": "Rsv/NCO yrs", "source": "fdic", "fdic_field": "LNRESNCR",
        "format": "ratio", "decimals": 1,
        "color_rule": "higher_better", "thresholds": {"good": 3, "warn": 1.5},
        "category": "Credit Detail",
    },

    # ── Income ───────────────────────────────────────────────────────────
    {
        "key": "net_income", "label": "Net Inc ($M)", "source": "fdic", "fdic_field": "NETINC",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Income",
    },
    {
        "key": "int_income", "label": "Int Inc ($M)", "source": "fdic", "fdic_field": "INTINC",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Income",
    },
    {
        "key": "int_expense", "label": "Int Exp ($M)", "source": "fdic", "fdic_field": "EINTEXP",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Income",
    },
    {
        "key": "net_interest_income", "label": "NII ($M)", "source": "fdic", "fdic_field": "NIM",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Income",
    },
    {
        "key": "nonint_income", "label": "Non-Int Inc ($M)", "source": "fdic", "fdic_field": "NONII",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Income",
    },
    {
        "key": "nonint_expense", "label": "Non-Int Exp ($M)", "source": "fdic", "fdic_field": "NONIX",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Income",
    },
    {
        "key": "provision", "label": "Provision ($M)", "source": "fdic", "fdic_field": "ELNATR",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Income",
    },
    {
        "key": "pretax_income", "label": "Pretax Inc ($M)", "source": "fdic", "fdic_field": "PTAXNETINC",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Income",
    },
    {
        "key": "taxes", "label": "Taxes ($M)", "source": "fdic", "fdic_field": "ITAX",
        "format": "millions", "decimals": 1,
        "color_rule": None, "thresholds": {},
        "category": "Income",
    },
    {
        "key": "int_income_yield", "label": "Int Inc Yield", "source": "fdic", "fdic_field": "INTINCY",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 5.0, "warn": 3.5},
        "category": "Income",
    },
    {
        "key": "int_expense_yield", "label": "Int Exp Cost", "source": "fdic", "fdic_field": "INTEXPY",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 2.0, "warn": 3.0},
        "category": "Income",
    },
    {
        "key": "nonint_inc_assets", "label": "Non-Int/Assets", "source": "fdic", "fdic_field": "NONIIAY",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 0.5, "warn": 0.2},
        "category": "Income",
    },
    {
        "key": "nonint_exp_assets", "label": "Non-Int Exp/Assets", "source": "fdic", "fdic_field": "NONIXAY",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 2.0, "warn": 3.0},
        "category": "Income",
    },
    {
        "key": "pretax_roa", "label": "Pretax ROA", "source": "fdic", "fdic_field": "ROAPTX",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 1.2, "warn": 0.7},
        "category": "Income",
    },
    {
        "key": "employees", "label": "Employees", "source": "fdic", "fdic_field": "NUMEMP",
        "format": "number", "decimals": 0,
        "color_rule": None, "thresholds": {},
        "category": "Operational",
    },
    {
        "key": "assets_per_employee", "label": "Assets/Emp ($M)", "source": "fdic", "fdic_field": "ASTEMPM",
        "format": "number", "decimals": 1,
        "color_rule": "higher_better", "thresholds": {"good": 10, "warn": 5},
        "category": "Operational",
    },
    {
        "key": "branches", "label": "Branches", "source": "fdic", "fdic_field": "OFFDOM",
        "format": "number", "decimals": 0,
        "color_rule": None, "thresholds": {},
        "category": "Operational",
    },

    # ── NIM Metrics ──────────────────────────────────────────────────────
    {
        "key": "nim_spread", "label": "NIM Spread", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 3.0, "warn": 2.0},
        "category": "NIM Metrics",
    },
    {
        "key": "provision_to_assets", "label": "Prov/Assets", "source": "fdic", "fdic_field": "ELNATRY",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 0.15, "warn": 0.40},
        "category": "NIM Metrics",
    },
    {
        "key": "net_op_income_assets", "label": "Net Op Inc/Assets", "source": "fdic", "fdic_field": "NOIJY",
        "format": "pct", "decimals": 2,
        "color_rule": "higher_better", "thresholds": {"good": 1.0, "warn": 0.5},
        "category": "NIM Metrics",
    },
    {
        "key": "nonint_burden", "label": "Non-Int Burden", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 1.5, "warn": 2.5},
        "category": "NIM Metrics",
    },
    {
        "key": "cost_of_funds", "label": "Cost of Funds", "source": "computed",
        "format": "pct", "decimals": 2,
        "color_rule": "lower_better", "thresholds": {"good": 2.0, "warn": 3.0},
        "category": "NIM Metrics",
    },
]

# Build lookup helpers
METRICS_BY_KEY = {m["key"]: m for m in METRICS}
METRIC_CATEGORIES = list(dict.fromkeys(m["category"] for m in METRICS))

def get_fdic_fields():
    """Return set of FDIC field names needed."""
    return {m["fdic_field"] for m in METRICS if m.get("fdic_field")}

def get_sec_concepts():
    """Return set of SEC XBRL concept names needed."""
    return {m["sec_concept"] for m in METRICS if m.get("sec_concept")}

def get_metrics_for_category(category):
    """Return metrics belonging to a category."""
    return [m for m in METRICS if m["category"] == category]

# ═══════════════════════════════════════════════════════════════════════════
# TAB DEFINITIONS — each tab has a name, title, and default column list
# ═══════════════════════════════════════════════════════════════════════════

TABS = [
    {
        "key": "valuation",
        "label": "Valuation & Performance",
        "title": "Bank Valuation & Performance",
        "columns": [
            "price", "change_pct", "market_cap",
            "eps", "pe_ratio", "tbvps", "ptbv_ratio", "dividend_yield",
            "roatce_blended", "fair_ptbv", "fair_price", "ptbv_discount",
            "roaa", "roaa_4q", "roatce", "roatce_4q", "nim", "nim_4q", "efficiency_ratio",
            "npl_ratio", "cet1_ratio", "total_assets",
        ],
    },
    {
        "key": "balance_sheet",
        "label": "Balance Sheet",
        "title": "Balance Sheet Summary",
        "columns": [
            "total_assets", "total_loans", "total_loans_gross", "securities",
            "cash_balances", "fed_funds_sold", "total_liab", "total_equity",
            "intangibles", "ore", "trading_assets",
            "loans_to_deposits", "loans_to_assets", "deposits_to_assets",
            "earning_assets_pct", "equity_to_assets",
        ],
    },
    {
        "key": "loan_mix",
        "label": "Loan Mix ($)",
        "title": "Loan Mix — Dollar Amounts",
        "columns": [
            "total_loans", "ln_re_total", "ln_re_residential",
            "ln_re_nres", "ln_re_nres_oo", "ln_re_nres_noo",
            "ln_re_multifam", "ln_re_construct", "ln_re_ag",
            "ln_ci", "ln_consumer", "ln_auto", "ln_credit_card", "ln_ag",
        ],
    },
    {
        "key": "loan_concentration",
        "label": "Loan Concentration (%)",
        "title": "Loan Concentration — % of Total Loans",
        "columns": [
            "total_loans", "ln_re_pct", "ln_cre_pct", "ln_resi_pct",
            "ln_multifam_pct", "ln_construct_pct", "ln_ci_pct",
            "ln_consumer_pct", "cre_to_capital",
        ],
    },
    {
        "key": "deposits",
        "label": "Deposits ($)",
        "title": "Deposit Composition — Dollar Amounts",
        "columns": [
            "total_deposits", "core_deposits", "insured_deposits", "uninsured_deposits",
            "brokered_deposits", "demand_deposits", "mmda_savings",
            "large_time_dep", "dep_lt_250k", "int_bearing_dep", "nonint_dep",
        ],
    },
    {
        "key": "deposit_ratios",
        "label": "Deposit Ratios (%)",
        "title": "Deposit Ratios",
        "columns": [
            "total_deposits", "uninsured_pct", "core_dep_pct",
            "brokered_pct", "nonint_dep_pct",
            "deposits_to_assets",
        ],
    },
    {
        "key": "deposit_dynamics",
        "label": "Deposit Dynamics",
        "title": "Deposit Dynamics — Beta, Flows & Alerts",
        "columns": [
            "total_deposits", "dep_qoq_growth", "cod_qoq_bps",
            "deposit_cycle_beta", "deposit_rolling_beta",
            "nonint_dep_pct", "uninsured_pct", "brokered_pct",
            "deposit_alerts_count",
        ],
    },
    {
        "key": "securities",
        "label": "Securities",
        "title": "Securities Portfolio",
        "columns": [
            "securities", "sec_afs", "sec_htm",
            "sec_ust", "sec_agency", "sec_uso", "sec_muni", "sec_abs",
            "sec_unreal_gl", "sec_htm_unreal",
            "sec_to_assets_pct", "htm_pct",
        ],
    },
    {
        "key": "credit_detail",
        "label": "Credit Detail",
        "title": "Credit Quality Detail",
        "columns": [
            "npl_ratio", "npl_cre", "npl_resi", "npl_multifam",
            "npl_nres_re", "npl_ci", "npl_consumer",
            "nco_ratio", "nco_re", "nco_ci",
            "past_due_30_89", "past_due_90",
            "reserve_coverage_pct", "reserve_to_loans",
            "nco_to_reserve", "reserve_nco_coverage",
            "allowance_loans",
        ],
    },
    {
        "key": "credit_dynamics",
        "label": "Credit Dynamics",
        "title": "Credit Dynamics — Trends, Coverage & Alerts",
        "columns": [
            "npl_ratio", "npl_trend_bps", "nco_ratio", "nco_4q_trend_bps",
            "pd_migration_bps", "reserve_coverage_pct",
            "worst_segment_npl", "credit_alerts_count",
        ],
    },
    {
        "key": "capital",
        "label": "Capital",
        "title": "Capital Adequacy",
        "columns": [
            "total_equity", "equity_to_assets",
            "cet1_ratio", "total_capital_ratio", "leverage_ratio",
            "total_assets",
        ],
    },
    {
        "key": "capital_dynamics",
        "label": "Capital Dynamics",
        "title": "Capital Dynamics — Trends, TBV Growth & Buyback Capacity",
        "columns": [
            "cet1_current", "cet1_qoq_pp", "tbv_cagr_1y",
            "payout_ratio_4q", "buyback_capacity_usd",
            "capital_alerts_count",
        ],
    },
    {
        "key": "capital_return",
        "label": "Capital Return",
        "title": "Capital Return Attribution — Dividends, Buybacks, Shareholder Yield",
        "columns": [
            "shareholder_yield", "dividend_yield_sec", "buyback_yield",
            "total_return_ratio_ttm", "payout_ratio_ttm",
            "share_change_pct_ttm", "dps_yoy_pct",
            "dividends_ttm", "buybacks_ttm",
        ],
    },
    {
        "key": "income",
        "label": "Income",
        "title": "Income Statement",
        "columns": [
            "net_income", "int_income", "int_expense", "net_interest_income",
            "nonint_income", "nonint_expense", "provision",
            "pretax_income", "taxes",
            "nonint_inc_assets", "nonint_exp_assets",
            "pretax_roa", "efficiency_ratio",
            "employees", "assets_per_employee", "branches",
        ],
    },
    {
        "key": "nim_metrics",
        "label": "NIM Metrics",
        "title": "NIM — Yields, Costs & Spreads",
        "columns": [
            "nim", "nim_4q", "int_income_yield", "int_expense_yield",
            "nim_spread", "cost_of_funds",
            "nonint_inc_assets", "nonint_exp_assets", "nonint_burden",
            "provision_to_assets", "net_op_income_assets",
            "earning_assets_pct", "efficiency_ratio",
        ],
    },
]

TAB_LABELS = [t["label"] for t in TABS]
TABS_BY_KEY = {t["key"]: t for t in TABS}

# Legacy aliases
DEFAULT_TABLE_COLUMNS = TABS[0]["columns"]
