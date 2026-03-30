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


def get_bank_info(ticker: str) -> dict | None:
    """Return bank info dict for a ticker, or None if unknown."""
    return BANK_MAP.get(ticker.upper())


def get_name(ticker: str) -> str:
    """Return bank display name."""
    info = BANK_MAP.get(ticker.upper())
    return info["name"] if info else ticker.upper()


def get_fdic_cert(ticker: str) -> int | None:
    info = BANK_MAP.get(ticker.upper())
    return info["fdic_cert"] if info else None


def get_cik(ticker: str) -> int | None:
    info = BANK_MAP.get(ticker.upper())
    return info["cik"] if info else None


def search_sec_by_ticker(ticker: str) -> int | None:
    """Look up SEC CIK by ticker using the company_tickers.json endpoint."""
    try:
        url = "https://www.sec.gov/files/company_tickers.json"
        headers = {"User-Agent": "BankValuationDashboard admin@company.com"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        ticker_upper = ticker.upper()
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker_upper:
                return entry["cik_str"]
        return None
    except Exception:
        return None


def resolve_ticker(ticker: str) -> dict:
    """
    Resolve a ticker to full bank info. Uses static map first, falls back to
    API lookups. Returns dict with name, fdic_cert, cik (any may be None).
    """
    ticker = ticker.upper()
    info = BANK_MAP.get(ticker)
    if info:
        return {"ticker": ticker, **info}

    # Fallback: try SEC lookup for CIK
    cik = search_sec_by_ticker(ticker)
    return {
        "ticker": ticker,
        "name": ticker,
        "fdic_cert": None,
        "cik": cik,
    }
