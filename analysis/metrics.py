"""
Assembles a unified metrics dict for each bank from all data sources.

Reads the metric registry in config.py and pulls values from the
appropriate source (fdic_data, sec_data, price_data, or computed).
"""

from config import METRICS
from analysis.valuation import compute_all_valuations


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
    # Compute derived valuations first
    computed = compute_all_valuations(price_data, sec_data, fdic_data, fdic_hist)

    result = {"ticker": ticker}

    for m in METRICS:
        key = m["key"]
        source = m["source"]

        if source == "fdic":
            field = m.get("fdic_field")
            val = fdic_data.get(field) if field else None
            # FDIC dollar amounts are in thousands — convert to raw dollars
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
                "P3ASSET", "P9ASSET",
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
    for ticker in watchlist:
        fdic = fdic_all.get(ticker, {})
        sec = sec_all.get(ticker, {})
        price = prices_all.get(ticker, {})
        fdic_hist = fdic_hist_all.get(ticker, [])
        row = build_bank_metrics(ticker, fdic, sec, price, fdic_hist)
        rows.append(row)
    return rows
