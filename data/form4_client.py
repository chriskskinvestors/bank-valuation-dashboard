"""
SEC Form 4 (insider trades) client.

Form 4 is filed whenever an officer, director, or 10%+ shareholder trades
company stock. Accessed via SEC EDGAR's full-text search + per-CIK filings.

Each Form 4 XML contains:
  - Reporting person name + officer/director flag
  - Transaction date
  - Transaction code (P=purchase, S=sale, A=award, M=option exercise, etc.)
  - Shares transacted
  - Share price
  - Shares owned after transaction

Cached for 24 hours per ticker.
"""

import os
import re
import json
import requests
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from xml.etree import ElementTree as ET

import pandas as pd
import streamlit as st

from data.cloud_storage import save_json, load_json
from config import SEC_USER_AGENT

FORM4_CACHE_PREFIX = "form4_cache"
CACHE_TTL_SECONDS = 86400  # 24 hours

HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# Transaction codes
TRANSACTION_CODES = {
    "P": "Open-Market Purchase",
    "S": "Open-Market Sale",
    "A": "Grant/Award",
    "D": "Disposition (non-market)",
    "M": "Option Exercise",
    "F": "Tax Withhold (net settle)",
    "G": "Gift",
    "J": "Other Acquisition",
    "K": "Other Disposition",
}


def _pad_cik(cik: int) -> str:
    return str(cik).zfill(10)


# Shared freshness check (data/freshness) bound to this module's TTL.
def _is_fresh(cached: dict | None) -> bool:
    from data.freshness import is_fresh
    return is_fresh(cached, CACHE_TTL_SECONDS)


def _fetch_form4_xml(accession: str, cik: int) -> str | None:
    """Fetch the raw Form 4 XML file for an accession."""
    acc_no_hyphens = accession.replace("-", "")
    # The Form 4 XML is typically the primary document, named like wf-form4_NNNN.xml
    # Listing the directory to find it
    index_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=4"
    try:
        # Try direct index.json
        index_json_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_hyphens}/index.json"
        r = requests.get(index_json_url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        items = r.json().get("directory", {}).get("item", [])
        xml_file = next(
            (it["name"] for it in items if it["name"].endswith(".xml") and "form4" in it["name"].lower()),
            None,
        )
        if not xml_file:
            # Try any .xml
            xml_file = next((it["name"] for it in items if it["name"].endswith(".xml")), None)
        if not xml_file:
            return None
        xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_hyphens}/{xml_file}"
        resp = requests.get(xml_url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


def _parse_form4(xml_text: str) -> list[dict]:
    """Parse Form 4 XML → list of transaction dicts."""
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    # Reporting person info
    owner = root.find(".//reportingOwner/reportingOwnerId")
    owner_name = owner.findtext("rptOwnerName") if owner is not None else None

    relationship = root.find(".//reportingOwner/reportingOwnerRelationship")
    is_director = False
    is_officer = False
    officer_title = None
    if relationship is not None:
        is_director = relationship.findtext("isDirector") == "1"
        is_officer = relationship.findtext("isOfficer") == "1"
        officer_title = relationship.findtext("officerTitle")

    role = []
    if is_officer and officer_title:
        role.append(officer_title)
    elif is_officer:
        role.append("Officer")
    if is_director:
        role.append("Director")
    role_str = ", ".join(role) if role else "Insider"

    transactions = []

    # Non-derivative transactions (common stock buys/sells)
    for t in root.findall(".//nonDerivativeTransaction"):
        date = t.findtext(".//transactionDate/value")
        code = t.findtext(".//transactionCoding/transactionCode")
        acq_disp = t.findtext(".//transactionCoding/transactionAcquiredDisposedCode")
        shares = t.findtext(".//transactionAmounts/transactionShares/value")
        price = t.findtext(".//transactionAmounts/transactionPricePerShare/value")
        shares_after = t.findtext(".//postTransactionAmounts/sharesOwnedFollowingTransaction/value")

        try:
            shares_f = float(shares) if shares else None
            price_f = float(price) if price else None
        except ValueError:
            continue

        transactions.append({
            "date": date,
            "insider": owner_name,
            "role": role_str,
            "type": TRANSACTION_CODES.get(code, code or "—"),
            "code": code,
            "shares": shares_f,
            "price": price_f,
            "value_usd": (shares_f * price_f) if (shares_f and price_f) else None,
            "shares_after": float(shares_after) if shares_after else None,
            "direction": "Buy" if acq_disp == "A" else "Sell",
            "form_type": "non-derivative",
        })

    # Derivative transactions (option exercises, etc.)
    # Note: for code "M" (option exercise), the price in Form 4 is the STRIKE
    # price, not market value. We flag value_usd as None for exercises to avoid
    # misleading display in the "Value" column. Market value would require a
    # separate price lookup on the exercise date.
    for t in root.findall(".//derivativeTransaction"):
        date = t.findtext(".//transactionDate/value")
        code = t.findtext(".//transactionCoding/transactionCode")
        shares = t.findtext(".//transactionAmounts/transactionShares/value")
        price = t.findtext(".//transactionAmounts/transactionPricePerShare/value")

        try:
            shares_f = float(shares) if shares else None
            price_f = float(price) if price else None
        except ValueError:
            continue

        is_exercise = code == "M"
        value_usd = None
        if shares_f and price_f and not is_exercise:
            value_usd = shares_f * price_f

        transactions.append({
            "date": date,
            "insider": owner_name,
            "role": role_str,
            "type": TRANSACTION_CODES.get(code, code or "—"),
            "code": code,
            "shares": shares_f,
            "price": price_f,  # strike price for exercises
            "strike_price": price_f if is_exercise else None,
            "value_usd": value_usd,  # None for exercises (strike != market value)
            "shares_after": None,
            "direction": "Exercise" if is_exercise else code,
            "form_type": "derivative",
        })

    return transactions


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_insider_trades(cik: int, months_back: int = 12) -> list[dict]:
    """
    Fetch all Form 4 filings for a CIK and parse into transactions.
    """
    if not cik:
        return []

    # Check cache
    cached = load_json(FORM4_CACHE_PREFIX, f"{cik}.json")
    if _is_fresh(cached) and "transactions" in cached:
        return cached["transactions"]

    try:
        url = SEC_SUBMISSIONS_URL.format(cik=_pad_cik(cik))
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[Form4] Submissions error for CIK {cik}: {e}")
        return []

    recent = data.get("filings", {}).get("recent", {})
    if not recent:
        return []

    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])

    cutoff_date = (datetime.now() - timedelta(days=30 * months_back)).date()

    # Collect Form 4 accessions within window
    form4_accessions = []
    for i, form in enumerate(forms):
        if form != "4":
            continue
        try:
            fdate = datetime.strptime(filing_dates[i], "%Y-%m-%d").date()
        except (ValueError, IndexError):
            continue
        if fdate < cutoff_date:
            continue
        form4_accessions.append({
            "accession": accessions[i],
            "filing_date": filing_dates[i],
            "report_date": report_dates[i] if i < len(report_dates) else None,
        })

    # Limit to most recent 30 to avoid hammering SEC (each filing = 1-2 requests)
    form4_accessions = form4_accessions[:30]

    all_transactions = []
    for entry in form4_accessions:
        xml = _fetch_form4_xml(entry["accession"], cik)
        if not xml:
            continue
        txs = _parse_form4(xml)
        for tx in txs:
            tx["filing_date"] = entry["filing_date"]
            tx["accession"] = entry["accession"]
        all_transactions.extend(txs)

    # Sort by transaction date desc
    all_transactions.sort(key=lambda x: x.get("date") or "", reverse=True)

    # Cache
    try:
        save_json(FORM4_CACHE_PREFIX, f"{cik}.json", {
            "cik": cik,
            "cached_at": datetime.now().isoformat(),
            "transactions": all_transactions,
        })
    except Exception:
        pass

    return all_transactions


def recent_open_market_transactions(ticker_ciks: dict, days: int = 30,
                                     limit: int = 60) -> list[dict]:
    """Recent OPEN-MARKET insider trades (codes P/S only) across the given
    {ticker: cik} map, for the Home news feed's BUY/SELL rows.

    Reads ONLY the already-cached Form 4 JSON (never triggers a live SEC
    fetch — far too slow for a feed render; the nightly refresh_insider job
    populates the cache). Excludes grants/awards/option-exercises/tax — only
    real market buys and sells, the SNL "VP sells N shares" convention.

    Returns rows newest-first:
      {ticker, cik, insider, role, direction ('Buy'|'Sell'), code ('P'|'S'),
       shares, value_usd, date}
    """
    cutoff = (datetime.now() - timedelta(days=days)).date()
    out: list[dict] = []
    pairs = [(t, c) for t, c in (ticker_ciks or {}).items() if c]
    # One GCS object read per bank. These were issued SERIALLY, so the walk cost
    # ~len(pairs) round-trips end to end — ~380 of them once the universe reached
    # 533 banks, on the critical path of a job scheduled every 15 minutes (which
    # was overrunning to 13-28 min and overlapping itself). They're independent
    # reads of distinct objects with no ordering requirement, so a small pool
    # collapses that to ~len(pairs)/16 round-trips. Bounded at 16: GCS is happy
    # with far more, but this runs inside a job that is already doing other I/O
    # and the win is mostly gone past this point.
    from concurrent.futures import ThreadPoolExecutor

    def _read(pair):
        ticker, cik = pair
        try:
            return ticker, cik, load_json(FORM4_CACHE_PREFIX, f"{cik}.json")
        except Exception:
            return ticker, cik, None      # a bad/missing object skips this bank

    with ThreadPoolExecutor(max_workers=16) as ex:
        fetched = list(ex.map(_read, pairs))

    for ticker, cik, cached in fetched:
        if not cached:
            continue
        for tx in cached.get("transactions", []):
            if tx.get("form_type") != "non-derivative":
                continue
            if tx.get("code") not in ("P", "S"):
                continue
            try:
                d = datetime.strptime(tx.get("date"), "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            if d < cutoff:
                continue
            out.append({
                "ticker": ticker, "cik": cik,
                "insider": tx.get("insider"), "role": tx.get("role"),
                "direction": tx.get("direction"), "code": tx.get("code"),
                "shares": tx.get("shares"), "value_usd": tx.get("value_usd"),
                "date": tx.get("date"),
            })
    out.sort(key=lambda r: r["date"], reverse=True)
    return out[:limit]


# Postgres cache key for the pre-aggregated universe insider feed. Bumped (_v1)
# if the row shape changes so a stale aggregate is rebuilt, not mis-read.
_OPEN_MARKET_UNIVERSE_KEY = "form4_open_market_universe_v1"


def build_open_market_universe_cache(ticker_ciks: dict, days: int = 14,
                                     limit: int = 60) -> int:
    """Run the heavy per-CIK Form-4 scan ONCE and persist the result as a single
    aggregate row, so the Home feed reads one cache hit instead of fanning out a
    GCS object read per bank on the render thread. Run by a background job
    (jobs/refresh_home_snapshot), NEVER on the render path. Returns the row count.
    """
    from data import cache
    rows = recent_open_market_transactions(ticker_ciks, days=days, limit=limit)
    if not rows:
        # A transient empty scan (e.g. the per-CIK GCS reads briefly failing)
        # must NOT clobber a good aggregate — that would blank the feed's insider
        # rows until the next clean run. Keep last-known when present; only seed
        # an empty row if nothing has ever been built.
        prior = cache.get(_OPEN_MARKET_UNIVERSE_KEY)
        if prior and (prior.get("value") or []):
            return len(prior["value"])
    cache.put(_OPEN_MARKET_UNIVERSE_KEY,
              {"cached_at": datetime.now().isoformat(), "value": rows})
    return len(rows)


def recent_open_market_universe(limit: int = 40) -> list[dict]:
    """Fast read of the pre-built universe insider feed (see
    build_open_market_universe_cache). Returns [] until a job has built it — the
    feed degrades to disclosures-only rather than fanning out per-CIK reads. Does
    ZERO per-bank I/O, so it is safe to call on the render thread."""
    from data import cache
    snap = cache.get(_OPEN_MARKET_UNIVERSE_KEY)
    rows = (snap or {}).get("value") or []
    return rows[:limit]


def summarize_insider_activity(transactions: list[dict]) -> dict:
    """Compute summary stats: 6M buy/sell totals, net $ flow, by-insider summary."""
    if not transactions:
        return {
            "total_transactions": 0,
            "buys_6m_usd": 0, "sells_6m_usd": 0, "net_flow_6m_usd": 0,
            "buyer_count_6m": 0, "seller_count_6m": 0,
            "insiders": [],
        }

    cutoff_6m = (datetime.now() - timedelta(days=180)).date()

    buys_6m_usd = 0.0
    sells_6m_usd = 0.0
    buyers_6m = set()
    sellers_6m = set()

    # Aggregate by insider across all time
    by_insider = {}

    for tx in transactions:
        if tx["form_type"] != "non-derivative":
            continue
        if tx["code"] not in ("P", "S"):  # only real market trades (not grants/taxes)
            continue
        val = tx.get("value_usd") or 0

        try:
            tx_date = datetime.strptime(tx["date"], "%Y-%m-%d").date()
            in_6m = tx_date >= cutoff_6m
        except (ValueError, TypeError):
            in_6m = False

        name = tx.get("insider", "Unknown")
        if name not in by_insider:
            by_insider[name] = {
                "name": name,
                "role": tx.get("role"),
                "buy_usd": 0, "sell_usd": 0, "txn_count": 0,
            }
        if tx["direction"] == "Buy":
            by_insider[name]["buy_usd"] += val
            if in_6m:
                buys_6m_usd += val
                buyers_6m.add(name)
        else:
            by_insider[name]["sell_usd"] += val
            if in_6m:
                sells_6m_usd += val
                sellers_6m.add(name)
        by_insider[name]["txn_count"] += 1

    return {
        "total_transactions": len(transactions),
        "buys_6m_usd": buys_6m_usd,
        "sells_6m_usd": sells_6m_usd,
        "net_flow_6m_usd": buys_6m_usd - sells_6m_usd,
        "buyer_count_6m": len(buyers_6m),
        "seller_count_6m": len(sellers_6m),
        "insiders": sorted(
            by_insider.values(),
            key=lambda x: x["buy_usd"] + x["sell_usd"],
            reverse=True,
        ),
    }
