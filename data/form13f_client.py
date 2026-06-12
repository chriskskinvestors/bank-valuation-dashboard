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

from data.cloud_storage import save_json, load_json, list_files
from config import SEC_USER_AGENT

FORM13F_CACHE_PREFIX = "form13f_cache"
CACHE_TTL_SECONDS = 86400

HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}

EDGAR_FTS = "https://efts.sec.gov/LATEST/search-index"


# Shared freshness check (data/freshness) bound to this module's TTL.
def _is_fresh(cached: dict | None) -> bool:
    from data.freshness import is_fresh
    return is_fresh(cached, CACHE_TTL_SECONDS)


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


def filing_index_url(cik: str | int, accession: str) -> str:
    """Human-readable EDGAR filing-index URL for a 13F-HR accession."""
    acc_no_hyphens = str(accession).replace("-", "")
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{acc_no_hyphens}/{accession}-index.htm")


def _filer_13f_history(cik: str | int) -> list[tuple[str, str]]:
    """List a filer's 13F-HR accessions as (accession, filing_date), newest first."""
    try:
        url = f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        recent = r.json().get("filings", {}).get("recent", {})
    except Exception:
        return []
    forms = recent.get("form", [])
    accs = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    out = [(accs[i], dates[i]) for i in range(len(forms))
           if forms[i] in ("13F-HR", "13F-HR/A")]
    # submissions API is already newest-first, but sort defensively by date
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def _prior_quarter_shares(cik: str, current_date: str, target_ticker: str):
    """Shares this filer held of target_ticker in their 13F-HR filed *before*
    current_date. Returns float shares, or None if no prior filing / not held."""
    history = _filer_13f_history(cik)
    prior_acc = next((acc for acc, dt in history if dt < (current_date or "")), None)
    if not prior_acc:
        return None
    positions = _fetch_13f_info_table(cik, prior_acc, target_ticker)
    if not positions:
        return None
    return sum(p.get("shares") or 0 for p in positions) or None


def _classify_change(current_shares, prior_shares) -> dict:
    """Position-change status vs prior quarter."""
    if prior_shares is None:
        return {"change_status": "New", "change_pct": None}
    if prior_shares <= 0:
        return {"change_status": "New", "change_pct": None}
    delta = (current_shares - prior_shares) / prior_shares * 100
    if abs(delta) < 0.5:
        status = "Unchanged"
    elif delta > 0:
        status = "Added"
    else:
        status = "Trimmed"
    return {"change_status": status, "change_pct": delta}


# ──────────────────────────────────────────────────────────────────────────
# Quarterly history retention (SNL-BUILD-PLAN §13, Ownership History tab)
#
# The latest-window store ({TICKER}.json) is overwritten on every refresh.
# To build a holder × quarter matrix we ALSO persist each refresh into a
# quarter-keyed snapshot ({TICKER}_{YYYYQn}.json) keyed by the calendar
# quarter each filing covers. Quarter files are merged by filer CIK, never
# overwritten wholesale — history accumulates going forward from when this
# shipped. Backfilling older quarters from EDGAR is a separate later task.
# ──────────────────────────────────────────────────────────────────────────

_QUARTER_FILE_RE = re.compile(r"_(\d{4}Q[1-4])\.json$")


def _report_quarter(date_filed: str) -> str | None:
    """
    Calendar quarter a 13F-HR covers, as "YYYYQn".

    13Fs are filed up to 45 days AFTER quarter-end, so the report period is
    the last quarter-end strictly before the filing date (filed 2026-05-15
    → 2026Q1; filed 2026-02-10 → 2025Q4).
    """
    try:
        d = datetime.strptime(str(date_filed)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    completed = (d.month - 1) // 3  # quarters fully ended this calendar year
    if completed == 0:
        return f"{d.year - 1}Q4"
    return f"{d.year}Q{completed}"


def _quarter_filename(ticker: str, quarter: str) -> str:
    return f"{ticker.upper()}_{quarter}.json"


def _save_quarter_snapshots(ticker: str, holders: list[dict]) -> None:
    """
    Persist holders into quarter-keyed snapshot files alongside the latest
    window. Each holder lands in the quarter its own filing covers (a refresh
    during a filing window can straddle two quarters). Merge is by filer CIK
    with the fresh fetch winning per filer, so re-running the same refresh is
    idempotent and a holder seen in an earlier refresh of the same quarter is
    never dropped.
    """
    by_quarter: dict[str, list[dict]] = {}
    for h in holders:
        q = _report_quarter(h.get("date_filed") or "")
        if q:
            by_quarter.setdefault(q, []).append(h)

    for quarter, fresh in by_quarter.items():
        fname = _quarter_filename(ticker, quarter)
        existing = load_json(FORM13F_CACHE_PREFIX, fname) or {}
        merged = {h.get("filer_cik"): h
                  for h in existing.get("holders", []) if h.get("filer_cik")}
        for h in fresh:
            if h.get("filer_cik"):
                merged[h["filer_cik"]] = h
        out = sorted(merged.values(),
                     key=lambda h: h.get("value_usd") or 0, reverse=True)
        save_json(FORM13F_CACHE_PREFIX, fname, {
            "ticker": ticker.upper(),
            "quarter": quarter,
            "cached_at": datetime.now().isoformat(),
            "holders": out,
        })


def get_holder_history(ticker: str, quarters: int = 20) -> dict[str, dict[str, dict]]:
    """
    Holder × quarter matrix assembled from stored quarterly snapshots.

    Returns {holder_name: {quarter: {"shares": float, "value_usd": float}}}
    with quarter keys like "2026Q1". Only quarters that have a stored
    snapshot appear — history accumulates going forward from when quarterly
    retention shipped; backfilling older quarters from EDGAR is a later
    task. ``quarters`` caps the result to the N most recent stored quarters.
    """
    if not ticker or quarters <= 0:
        return {}
    t = ticker.upper()
    found = set()
    for name in list_files(FORM13F_CACHE_PREFIX, f"{t}_*.json"):
        m = _QUARTER_FILE_RE.search(name)
        if m and name == _quarter_filename(t, m.group(1)):
            found.add(m.group(1))
    recent = sorted(found, reverse=True)[:quarters]

    history: dict[str, dict[str, dict]] = {}
    for quarter in recent:
        snap = load_json(FORM13F_CACHE_PREFIX, _quarter_filename(t, quarter)) or {}
        for h in snap.get("holders", []):
            holder_name = h.get("filer_name")
            if not holder_name:
                continue
            history.setdefault(holder_name, {})[quarter] = {
                "shares": h.get("shares"),
                "value_usd": h.get("value_usd"),
            }
    return history


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_institutional_holdings(ticker: str, company_name: str = "",
                                   max_filers: int = 25,
                                   with_changes: bool = True) -> list[dict]:
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
            "filing_url": filing_index_url(filer_id, c["accession"]),
            "shares": total_shares,
            "value_usd": total_value_usd,
            "positions": positions,
        })

    # Sort by value desc
    all_holders.sort(key=lambda h: h.get("value_usd", 0), reverse=True)

    # Quarter-over-quarter position change vs each filer's prior 13F-HR. Best
    # effort and bounded to what we display (one extra EDGAR fetch per filer).
    if with_changes:
        for h in all_holders[:max_filers]:
            try:
                prior = _prior_quarter_shares(
                    h["filer_cik"], h.get("date_filed", ""), search_term)
            except Exception as e:
                # A failed lookup must not masquerade as a confident "New"
                # position — that's a wrong label, not missing data.
                print(f"[13F] prior-quarter lookup failed for "
                      f"{h.get('filer_name', h['filer_cik'])}: {type(e).__name__}: {e}")
                h["prior_shares"] = None
                h.update({"change_status": "Unknown", "change_pct": None})
                continue
            h["prior_shares"] = prior
            h.update(_classify_change(h["shares"], prior))

    try:
        save_json(FORM13F_CACHE_PREFIX, f"{ticker.upper()}.json", {
            "ticker": ticker.upper(),
            "cached_at": datetime.now().isoformat(),
            "holders": all_holders,
        })
    except Exception:
        pass

    # Quarterly history retention: also persist this refresh under
    # quarter-keyed entries so the Ownership History tab can build a
    # holder × quarter matrix (see _save_quarter_snapshots).
    if all_holders:
        try:
            _save_quarter_snapshots(ticker, all_holders)
        except Exception as e:
            print(f"[13F] quarter-snapshot write failed for {ticker}: "
                  f"{type(e).__name__}: {e}")

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
