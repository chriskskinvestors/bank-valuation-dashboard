"""
13F (Institutional Holdings) client.

Uses SEC EDGAR full-text search to find institutional investors that
report holding a given ticker in their 13F-HR filings. Then parses
the XML info tables to extract exact shares + value.

This is a reverse-lookup problem: 13Fs are filed per-institution, not per-stock.
We search for the CUSIP of the ticker in recent 13F filings.

Cached for 24 hours per ticker.
"""

import os
import json
import requests
import re
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET
from pathlib import Path

import pandas as pd
import streamlit as st

from data.cloud_storage import save_json, load_json
from config import SEC_USER_AGENT

FORM13F_CACHE_PREFIX = "form13f_cache"
CACHE_TTL_SECONDS = 86400

HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}

EDGAR_FTS = "https://efts.sec.gov/LATEST/search-index"


def _is_fresh(cached: dict | None) -> bool:
    if not cached:
        return False
    ts = cached.get("cached_at", "")
    if not ts:
        return False
    try:
        age = (datetime.now() - datetime.fromisoformat(ts)).total_seconds()
        return age < CACHE_TTL_SECONDS
    except Exception:
        return False


def _search_13f_for_ticker(ticker: str, limit: int = 40) -> list[dict]:
    """
    Search EDGAR full-text for recent 13F-HR filings mentioning the ticker.
    Returns list of {cik, accession, filer_name, date_filed}.
    """
    since_date = (datetime.now() - timedelta(days=130)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")

    # Try quoted exact-match search first; fall back to unquoted for rare tickers
    attempts = [f'"{ticker}"', ticker]
    data = {}
    for q in attempts:
        params = {
            "q": q,
            "forms": "13F-HR",
            "dateRange": "custom",
            "startdt": since_date,
            "enddt": end_date,
        }
        try:
            resp = requests.get(EDGAR_FTS, params=params, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            if data.get("hits", {}).get("hits"):
                break
        except Exception as e:
            print(f"[13F] Search error for query '{q}': {e}")
            continue

    if not data:
        return []

    hits = data.get("hits", {}).get("hits", [])
    results = []
    for hit in hits[:limit]:
        src = hit.get("_source", {})
        adsh = hit.get("_id", "").split(":")[0]
        filer_ciks = src.get("ciks", [])
        filer_names = src.get("display_names", [])
        filer = filer_names[0] if filer_names else "—"
        # Strip the "(CIK ...)" suffix that EDGAR appends
        filer_clean = re.sub(r"\s*\(CIK \d+\)\s*$", "", filer)
        if filer_ciks:
            results.append({
                "cik": filer_ciks[0],
                "accession": adsh,
                "filer_name": filer_clean,
                "date_filed": src.get("file_date"),
            })
    return results


def _fetch_13f_info_table(cik: str, accession: str, target_ticker: str) -> list[dict]:
    """
    Parse the 13F infoTable.xml to extract holdings for a specific ticker/CUSIP.
    """
    acc_no_hyphens = accession.replace("-", "")
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_hyphens}/index.json"
    try:
        r = requests.get(index_url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        items = r.json().get("directory", {}).get("item", [])
    except Exception:
        return []

    # Find the infoTable XML (contains the holdings)
    info_file = next(
        (it["name"] for it in items
         if it["name"].lower().endswith(".xml") and "info" in it["name"].lower()),
        None,
    )
    if not info_file:
        return []

    try:
        xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_hyphens}/{info_file}"
        resp = requests.get(xml_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except Exception:
        return []

    # Namespace handling — 13F uses various namespaces
    ns_match = re.match(r"\{(.+)\}", root.tag)
    ns = {"n": ns_match.group(1)} if ns_match else {}

    positions = []
    for info in root.iter():
        if not info.tag.endswith("infoTable"):
            continue
        name = None
        cusip = None
        shares = None
        value = None
        class_ = None
        for child in info:
            tag = child.tag.split("}")[-1]
            if tag == "nameOfIssuer":
                name = (child.text or "").strip()
            elif tag == "cusip":
                cusip = (child.text or "").strip()
            elif tag == "titleOfClass":
                class_ = (child.text or "").strip()
            elif tag == "value":
                try:
                    # Pre-2023 values in $ thousands; post-2023 in $ 1s
                    value = float(child.text or 0)
                except (ValueError, TypeError):
                    pass
            elif tag == "shrsOrPrnAmt":
                for sub in child:
                    if sub.tag.split("}")[-1] == "sshPrnamt":
                        try:
                            shares = float(sub.text or 0)
                        except (ValueError, TypeError):
                            pass

        if not name:
            continue
        # Match the ticker's issuer name (loose matching) but EXCLUDE preferred
        # stock, depositary shares, warrants, convertibles, and other non-common
        # instruments — these have different prices/economics than common shares
        # and shouldn't be aggregated as "ownership".
        name_upper = name.upper()
        class_upper = (class_ or "").upper()

        is_match = (
            target_ticker.upper() in name_upper
            or target_ticker.upper() == name_upper
        )
        if not is_match:
            continue

        # Exclude non-common instruments
        NON_COMMON_KEYWORDS = (
            "PREFERRED", "PREF ", "PFD", "DEPOSITARY", "DEP SHARE",
            "WARRANT", "CONVERTIBLE", "NOTE ", "BOND", "DEBT",
            "RIGHTS", "UNIT",
        )
        combined = f"{name_upper} {class_upper}"
        if any(kw in combined for kw in NON_COMMON_KEYWORDS):
            continue

        positions.append({
            "issuer": name, "cusip": cusip, "class": class_,
            "shares": shares, "value_thousands": value,
        })

    return positions


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_institutional_holdings(ticker: str, company_name: str = "",
                                   max_filers: int = 25) -> list[dict]:
    """
    Find 13F filings holding this ticker's stock, return list of holders.
    """
    if not ticker:
        return []

    cached = load_json(FORM13F_CACHE_PREFIX, f"{ticker.upper()}.json")
    if _is_fresh(cached) and "holders" in cached:
        return cached["holders"]

    # Search for 13Fs mentioning this ticker (and optionally the company name)
    search_term = ticker
    if company_name:
        # Strip generic suffixes
        co_clean = re.sub(r"(Inc\.|Corp\.|Corporation|Company|Co\.|Ltd\.).*$", "", company_name).strip()
        search_term = co_clean or ticker

    candidates = _search_13f_for_ticker(search_term, limit=max_filers * 2)

    # For each candidate, fetch the info table and filter to positions matching this ticker
    all_holders = []
    seen_filers = set()
    for c in candidates:
        if len(all_holders) >= max_filers:
            break
        filer_id = c["cik"]
        if filer_id in seen_filers:
            continue
        seen_filers.add(filer_id)
        positions = _fetch_13f_info_table(filer_id, c["accession"], search_term)
        if not positions:
            continue

        # Aggregate this filer's positions in this ticker
        total_shares = sum(p.get("shares") or 0 for p in positions)
        total_raw_value = sum(p.get("value_thousands") or 0 for p in positions)

        if total_shares <= 0:
            continue

        # 13F reporting changed Q4 2022 (filings after ~Feb 2023):
        #   - Pre-Q4 2022: <value> is in $ thousands
        #   - Q4 2022+: <value> is in raw $
        # Detect which: check filing date, OR sanity-check value-per-share
        file_date = c.get("date_filed", "")
        post_2023 = file_date >= "2023-02-01"

        if post_2023:
            total_value_usd = total_raw_value
        else:
            total_value_usd = total_raw_value * 1000

        # Sanity check: value/share should be reasonable vs typical equity prices
        if total_shares > 0:
            implied_price = total_value_usd / total_shares
            # If implied price < $0.50, we likely guessed wrong — flip the scale up
            if implied_price < 0.50 and total_raw_value > 0:
                total_value_usd = total_raw_value * 1000
            # If implied price > $100,000, flip the scale down
            elif implied_price > 100_000 and total_raw_value > 0:
                total_value_usd = total_raw_value

        all_holders.append({
            "filer_cik": filer_id,
            "filer_name": c["filer_name"],
            "date_filed": c["date_filed"],
            "accession": c["accession"],
            "shares": total_shares,
            "value_usd": total_value_usd,
            "positions": positions,
        })

    # Sort by value desc
    all_holders.sort(key=lambda h: h.get("value_usd", 0), reverse=True)

    try:
        save_json(FORM13F_CACHE_PREFIX, f"{ticker.upper()}.json", {
            "ticker": ticker.upper(),
            "cached_at": datetime.now().isoformat(),
            "holders": all_holders,
        })
    except Exception:
        pass

    return all_holders


def summarize_holdings(holders: list[dict]) -> dict:
    """Summary stats: total institutional $, top holder, concentration."""
    if not holders:
        return {
            "total_filers": 0, "total_shares": 0, "total_value_usd": 0,
            "top_holder": None, "top_5_concentration": 0,
        }
    total_shares = sum(h.get("shares") or 0 for h in holders)
    total_value = sum(h.get("value_usd") or 0 for h in holders)
    top_5_value = sum(h.get("value_usd") or 0 for h in holders[:5])
    return {
        "total_filers": len(holders),
        "total_shares": total_shares,
        "total_value_usd": total_value,
        "top_holder": holders[0] if holders else None,
        "top_5_concentration": (top_5_value / total_value * 100) if total_value > 0 else 0,
    }
