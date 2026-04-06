"""
Mapping between bank tickers, FDIC certificate numbers, and SEC CIK numbers.

To add a bank: add an entry to BANK_MAP below.
The FDIC cert number and CIK are used to pull data from FDIC and SEC APIs.
"""

import requests

# ── Static mapping ──────────────────────────────────────────────────────
# Format: ticker -> {name, fdic_cert, cik}
# fdic_cert: FDIC certificate number (for Call Report data)
# cik: SEC Central Index Key (for EDGAR filings)
BANK_MAP = {
    # ── User watchlist banks ────────────────────────────────────────────
    "SFST":  {"name": "Southern First Bancshares",          "fdic_cert": 35295,  "cik": 1090009},
    "CFFI":  {"name": "C&F Financial Corp.",                "fdic_cert": 10363,  "cik": 913341},
    "CBNK":  {"name": "Capital Bancorp Inc.",               "fdic_cert": 35278,  "cik": 1419536},
    "FBIZ":  {"name": "First Business Financial Services",  "fdic_cert": 15229,  "cik": 1521951},
    "CCBG":  {"name": "Capital City Bank Group",            "fdic_cert": 9622,   "cik": 726601},
    "IBOC":  {"name": "International Bancshares Corp.",     "fdic_cert": 19629,  "cik": 315709},
    "CARE":  {"name": "Carter Bankshares Inc.",             "fdic_cert": 58596,  "cik": 1829576},
    "RNST":  {"name": "Renasant Corp.",                     "fdic_cert": 12437,  "cik": 715072},
    "HTB":   {"name": "HomeTrust Bancshares",               "fdic_cert": 27677,  "cik": 1538263},
    "FRBA":  {"name": "First Bank (NJ)",                    "fdic_cert": 58481,  "cik": None},
    "SMBK":  {"name": "SmartFinancial Inc.",                "fdic_cert": 58463,  "cik": 1038773},
    "BFST":  {"name": "Business First Bancshares",          "fdic_cert": 58228,  "cik": 1624322},
    "TCBX":  {"name": "Third Coast Bancshares",             "fdic_cert": 58716,  "cik": 1781730},
    "FMBH":  {"name": "First Mid Bancshares",               "fdic_cert": 3705,   "cik": 700565},
    "HBNC":  {"name": "Horizon Bancorp Inc.",               "fdic_cert": 14327,  "cik": 706129},
    "PLBC":  {"name": "Plumas Bancorp",                     "fdic_cert": 23275,  "cik": 1168455},
    "FSRL":  {"name": "First Savings Financial Group",      "fdic_cert": None,   "cik": None},
    "BANR":  {"name": "Banner Corp.",                       "fdic_cert": 28489,  "cik": 946673},
    "MCBI":  {"name": "Mountain Commerce Bancshares",       "fdic_cert": 4931,   "cik": None},
    "BRBS":  {"name": "Blue Ridge Bankshares",              "fdic_cert": 17773,  "cik": 842717},
    "FSBW":  {"name": "FS Bancorp Inc.",                    "fdic_cert": 57633,  "cik": 1530249},
    "SPFI":  {"name": "South Plains Financial Inc.",        "fdic_cert": 25103,  "cik": 1163668},
    "ALRS":  {"name": "Alerus Financial Corp.",             "fdic_cert": 3931,   "cik": 903419},
    "FMNB":  {"name": "Farmers National Banc Corp.",        "fdic_cert": 3732,   "cik": 709337},
    "HFWA":  {"name": "Heritage Financial Corp.",           "fdic_cert": 29012,  "cik": 1046025},
    "BAFN":  {"name": "BayFirst Financial Corp.",           "fdic_cert": 34997,  "cik": 1649739},
    "CBAN":  {"name": "Colony Bankcorp Inc.",               "fdic_cert": 22257,  "cik": 711669},
    "OVBC":  {"name": "Ohio Valley Banc Corp.",             "fdic_cert": 384,    "cik": 894671},
    "FRME":  {"name": "First Merchants Corp.",              "fdic_cert": 4365,   "cik": 712534},
    "LNKB":  {"name": "LINKBANCORP Inc.",                   "fdic_cert": 9889,   "cik": 1756701},
    "WAL":   {"name": "Western Alliance Bancorporation",    "fdic_cert": 57512,  "cik": 1212545},
    "TSBK":  {"name": "Timberland Bancorp",                 "fdic_cert": 28453,  "cik": 1046050},
    "BKU":   {"name": "BankUnited Inc.",                    "fdic_cert": 58979,  "cik": 1504008},
    "PGC":   {"name": "Peapack-Gladstone Financial",        "fdic_cert": 11035,  "cik": 1050743},
    "SBFG":  {"name": "SB Financial Group",                 "fdic_cert": 13339,  "cik": 767405},
    "FNWB":  {"name": "First Northwest Bancorp",            "fdic_cert": 28405,  "cik": 1556727},
    "INBC":  {"name": "Independent Bank Corp.",             "fdic_cert": 9712,   "cik": None},
    "CCNB":  {"name": "Coastal Carolina Bancshares",        "fdic_cert": 58864,  "cik": 1437213},
    "JMSB":  {"name": "John Marshall Bancorp",              "fdic_cert": 58243,  "cik": 1710482},
    "RVSB":  {"name": "Riverview Bancorp Inc.",             "fdic_cert": 29922,  "cik": 1041368},
    "SLBK":  {"name": "Skyline Bankshares",                 "fdic_cert": 6861,   "cik": 1657642},
    "INBK":  {"name": "First Internet Bancorp",             "fdic_cert": 34607,  "cik": 1562463},
    "RMBI":  {"name": "Richmond Mutual Bancorporation",     "fdic_cert": 28533,  "cik": 1767837},
    "TFSL":  {"name": "TFS Financial Corp.",                "fdic_cert": 30012,  "cik": 1381668},
    "CBK":   {"name": "Commercial Bancgroup Inc.",          "fdic_cert": None,   "cik": 1981546},
    "IBTN":  {"name": "iBank Financial Corp.",              "fdic_cert": None,   "cik": None},
    "FGBI":  {"name": "First Guaranty Bancshares",          "fdic_cert": 14028,  "cik": 1408534},
    "PNFP":  {"name": "Pinnacle Financial Partners",        "fdic_cert": 35583,  "cik": 2082866},
    "OZK":   {"name": "Bank OZK",                           "fdic_cert": 110,    "cik": 1569650},
    "FHN":   {"name": "First Horizon Corp.",                "fdic_cert": 4977,   "cik": 36966},
    "UBSI":  {"name": "United Bankshares Inc.",             "fdic_cert": 22858,  "cik": 729986},
    "FBNC":  {"name": "First Bancorp (NC)",                 "fdic_cert": 15019,  "cik": 811589},
    "FFWM":  {"name": "First Foundation Inc.",              "fdic_cert": 58647,  "cik": 1413837},
    "FFIN":  {"name": "First Financial Bankshares",         "fdic_cert": 19440,  "cik": 36029},
    "HOMB":  {"name": "Home BancShares Inc.",               "fdic_cert": 11241,  "cik": 1331520},
    "EGBN":  {"name": "Eagle Bancorp Inc.",                 "fdic_cert": 34742,  "cik": 1050441},
    "ISBA":  {"name": "Isabella Bank Corp.",                "fdic_cert": 1005,   "cik": 842517},
    "BANC":  {"name": "Banc of California Inc.",            "fdic_cert": 24045,  "cik": 1169770},
    "FLG":   {"name": "Flagstar Financial Inc.",            "fdic_cert": 32541,  "cik": 910073},
    "VLY":   {"name": "Valley National Bancorp",            "fdic_cert": 9396,   "cik": 714310},
    "KEY":   {"name": "KeyCorp",                            "fdic_cert": 17534,  "cik": 91576},
    "HBAN":  {"name": "Huntington Bancshares",              "fdic_cert": 6560,   "cik": 49196},
    "ZION":  {"name": "Zions Bancorporation",               "fdic_cert": 2270,   "cik": 109380},

    # ── Large US banks (for reference/comparison) ──────────────────────
    "JPM":   {"name": "JPMorgan Chase & Co.",               "fdic_cert": 628,    "cik": 19617},
    "BAC":   {"name": "Bank of America Corp.",              "fdic_cert": 3510,   "cik": 70858},
    "WFC":   {"name": "Wells Fargo & Co.",                  "fdic_cert": 3511,   "cik": 72971},
    "C":     {"name": "Citigroup Inc.",                     "fdic_cert": 7213,   "cik": 831001},
    "USB":   {"name": "U.S. Bancorp",                       "fdic_cert": 6548,   "cik": 36104},
    "PNC":   {"name": "PNC Financial Services",             "fdic_cert": 6384,   "cik": 713676},
    "TFC":   {"name": "Truist Financial Corp.",              "fdic_cert": 9846,   "cik": 92230},
    "COF":   {"name": "Capital One Financial",               "fdic_cert": 4297,   "cik": 927628},
    "GS":    {"name": "Goldman Sachs Group",                 "fdic_cert": 33124,  "cik": 886982},
    "MS":    {"name": "Morgan Stanley",                      "fdic_cert": 32992,  "cik": 895421},
}


# ── IR page URLs (separate to keep BANK_MAP clean) ──────────────────────
# Maps ticker -> investor relations URL. Not every bank is listed; the UI
# falls back to the SEC EDGAR company page when missing.
IR_URLS = {
    "SFST":  "https://www.southernfirst.com/investor-relations",
    "CFFI":  "https://www.cffc.com/investor-relations/",
    "CBNK":  "https://ir.capitalbankmd.com/",
    "FBIZ":  "https://ir.firstbusiness.bank/",
    "CCBG":  "https://www.ccbg.com/investor-relations/",
    "IBOC":  "https://www.ibc.com/investors",
    "CARE":  "https://www.carterbankshares.com/investor-relations/",
    "RNST":  "https://investors.renasant.com/",
    "HTB":   "https://ir.htb.com/",
    "SMBK":  "https://ir.smartfinancialinc.com/",
    "BFST":  "https://ir.b1bank.com/",
    "TCBX":  "https://ir.thirdcoast.bank/",
    "FMBH":  "https://www.firstmidbancshares.com/investor-relations/",
    "HBNC":  "https://investor.horizonbank.com/",
    "PLBC":  "https://www.plumasbank.com/investor-relations",
    "BANR":  "https://www.bannerbank.com/investor-relations",
    "BRBS":  "https://www.mybrb.com/investor-relations/",
    "FSBW":  "https://ir.fsbwa.com/",
    "SPFI":  "https://ir.southplains.com/",
    "ALRS":  "https://ir.alerus.com/",
    "FMNB":  "https://www.farmersbankgroup.com/investor-relations",
    "HFWA":  "https://ir.hf-wa.com/",
    "CBAN":  "https://ir.colony.bank/",
    "FRME":  "https://www.firstmerchants.com/investor-relations",
    "WAL":   "https://ir.westernalliancebancorporation.com/",
    "BKU":   "https://ir.bankunited.com/",
    "PGC":   "https://www.pgbank.com/investor-relations",
    "INBK":  "https://ir.firstib.com/",
    "FGBI":  "https://www.firstguaranty.com/investor-relations",
    "PNFP":  "https://ir.pnfp.com/",
    "OZK":   "https://ir.ozk.com/",
    "FHN":   "https://ir.firsthorizon.com/",
    "UBSI":  "https://www.ubsi-wv.com/investor-relations",
    "FBNC":  "https://ir.localfirstbank.com/",
    "FFWM":  "https://ir.firstfoundationinc.com/",
    "FFIN":  "https://www.ffin.com/investor-relations",
    "HOMB":  "https://ir.homebancshares.com/",
    "EGBN":  "https://ir.eaglebankcorp.com/",
    "BANC":  "https://ir.bancofcal.com/",
    "FLG":   "https://ir.flagstar.com/",
    "VLY":   "https://www.valley.com/investor-relations",
    "KEY":   "https://ir.key.com/",
    "HBAN":  "https://ir.huntington.com/",
    "ZION":  "https://www.zionsbancorporation.com/investor-relations",
    "JPM":   "https://www.jpmorganchase.com/ir",
    "BAC":   "https://investor.bankofamerica.com/",
    "WFC":   "https://www.wellsfargo.com/about/investor-relations/",
    "C":     "https://www.citigroup.com/global/investors",
    "USB":   "https://ir.usbank.com/",
    "PNC":   "https://investor.pnc.com/",
    "TFC":   "https://ir.truist.com/",
    "COF":   "https://ir.capitalone.com/",
    "GS":    "https://www.goldmansachs.com/investor-relations/",
    "MS":    "https://www.morganstanley.com/about-us-ir",
}


def get_ir_url(ticker: str) -> str | None:
    """Return the IR page URL for a ticker, or None if unknown."""
    return IR_URLS.get(ticker.upper())


def get_bank_info(ticker: str) -> dict | None:
    """Return bank info dict for a ticker. Resolves dynamically if needed."""
    ticker = ticker.upper()
    info = BANK_MAP.get(ticker) or _RESOLVED_CACHE.get(ticker)
    if info:
        return info
    # Trigger resolution
    resolved = resolve_ticker(ticker)
    return resolved if resolved.get("cik") or resolved.get("fdic_cert") else None


def get_name(ticker: str) -> str:
    """Return bank display name. Resolves dynamically if not in static map."""
    ticker = ticker.upper()
    info = BANK_MAP.get(ticker) or _RESOLVED_CACHE.get(ticker)
    if info:
        return info["name"]
    resolved = resolve_ticker(ticker)
    return resolved.get("name", ticker)


def get_fdic_cert(ticker: str) -> int | None:
    """Return FDIC cert number. Resolves dynamically if not in static map."""
    ticker = ticker.upper()
    info = BANK_MAP.get(ticker) or _RESOLVED_CACHE.get(ticker)
    if info:
        return info.get("fdic_cert")
    resolved = resolve_ticker(ticker)
    return resolved.get("fdic_cert")


def get_cik(ticker: str) -> int | None:
    """Return SEC CIK. Resolves dynamically if not in static map."""
    ticker = ticker.upper()
    info = BANK_MAP.get(ticker) or _RESOLVED_CACHE.get(ticker)
    if info:
        return info.get("cik")
    resolved = resolve_ticker(ticker)
    return resolved.get("cik")


# ── Runtime cache for dynamically resolved tickers ───────────────────────
# Tickers added at runtime that aren't in BANK_MAP get resolved once
# via SEC/FDIC API lookups and cached here for the session.
_RESOLVED_CACHE: dict[str, dict] = {}


def search_sec_by_ticker(ticker: str) -> tuple[int | None, str | None]:
    """
    Look up SEC CIK and company name by ticker using company_tickers.json.
    Returns (cik, company_name) tuple.
    """
    try:
        url = "https://www.sec.gov/files/company_tickers.json"
        headers = {"User-Agent": "BankValuationDashboard admin@company.com"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        ticker_upper = ticker.upper()
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker_upper:
                return entry.get("cik_str"), entry.get("title", "")
        return None, None
    except Exception:
        return None, None


def search_fdic_by_name(name: str) -> int | None:
    """
    Search FDIC for the primary bank subsidiary CERT number using the
    holding company name (NAMEHCR) from SEC filings.

    Strategy: search NAMEHCR by first distinctive word with wildcard,
    sort by assets descending to get the primary (largest) subsidiary.
    """
    if not name:
        return None
    try:
        # Clean SEC holding company name
        clean = name.upper()
        for suffix in [", INC.", ", INC", " INC.", " INC", " CORP.", " CORP",
                       " CO.", " CO", " LTD.", " LTD", "/DE", "/MD", "/NJ",
                       "/RI", "/PA", "/OH", "/NC"]:
            clean = clean.replace(suffix, "")
        clean = clean.strip()

        # Use only the first word for wildcard search — multi-word wildcards
        # in the FDIC API treat spaces as OR, leading to false matches.
        # Sorting by ASSET DESC ensures we get the primary bank subsidiary.
        words = clean.split()
        # Skip very generic first words
        search_word = words[0]
        if search_word in ("FIRST", "UNITED", "AMERICAN", "NATIONAL") and len(words) > 1:
            search_word = f"{words[0]}*%20AND%20NAMEHCR:{words[1]}"

        params = {
            "filters": f"NAMEHCR:{search_word}*",
            "fields": "CERT,NAME,NAMEHCR,ASSET",
            "sort_by": "ASSET",
            "sort_order": "DESC",
            "limit": 1,
        }
        resp = requests.get(
            "https://banks.data.fdic.gov/api/financials",
            params=params, timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("data"):
            return int(data["data"][0]["data"]["CERT"])

        return None
    except Exception:
        return None


def resolve_ticker(ticker: str) -> dict:
    """
    Resolve a ticker to full bank info. Uses static map first, then runtime
    cache, then falls back to SEC + FDIC API lookups. Results are cached
    so subsequent calls are instant.

    Returns dict with ticker, name, fdic_cert, cik (any may be None).
    """
    ticker = ticker.upper()

    # 1. Static map
    info = BANK_MAP.get(ticker)
    if info:
        return {"ticker": ticker, **info}

    # 2. Runtime cache
    if ticker in _RESOLVED_CACHE:
        return {"ticker": ticker, **_RESOLVED_CACHE[ticker]}

    # 3. Dynamic resolution
    cik, sec_name = search_sec_by_ticker(ticker)

    # Clean up SEC name to title case
    name = sec_name.title() if sec_name else ticker
    # Fix common title-case issues
    for bad, good in [("'S", "'s"), ("Ii", "II"), ("Iii", "III"),
                      ("Llc", "LLC"), ("N.A.", "N.A.")]:
        name = name.replace(bad, good)

    # Try to find FDIC cert from the SEC company name
    fdic_cert = search_fdic_by_name(sec_name) if sec_name else None

    resolved = {"name": name, "fdic_cert": fdic_cert, "cik": cik}
    _RESOLVED_CACHE[ticker] = resolved
    return {"ticker": ticker, **resolved}
