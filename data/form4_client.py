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
from datetime import datetime, timedelta, timezone
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


def _acceptance_to_utc_iso(acc: str | None) -> str | None:
    """EDGAR submissions acceptanceDateTime → UTC ISO string, or None.

    EDGAR QUIRK (pinned with evidence on the 8-K lane, 2026-09-14): the
    digits are EASTERN despite the .000Z suffix — parse as ET, store UTC."""
    if not acc:
        return None
    try:
        from zoneinfo import ZoneInfo
        return datetime.strptime(str(acc)[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=ZoneInfo("America/New_York")
        ).astimezone(timezone.utc).isoformat()
    except (ValueError, IndexError):
        return None


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
        # Filers spell the boolean both ways — "1" and "true" (RVSB's
        # 2026-09-15 filing used "true" and its director rendered as the
        # generic "Insider").
        _true = ("1", "true")
        is_director = (relationship.findtext("isDirector") or "").strip().lower() in _true
        is_officer = (relationship.findtext("isOfficer") or "").strip().lower() in _true
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
        # acquiredDisposedCode lives under transactionAmounts with a <value>
        # child (SEC ownershipDocument schema) — the old transactionCoding path
        # matched nothing, so every transaction (purchases included) rendered
        # as direction "Sell" (caught on the RVSB director buy, 2026-09-15).
        acq_disp = t.findtext(
            ".//transactionAmounts/transactionAcquiredDisposedCode/value")
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
            # A=acquired, D=disposed. If a filing omits the A/D flag, fall back
            # to the SEC's own code semantics (P = open-market purchase,
            # S = open-market sale); anything else stays None — never a guess.
            "direction": ("Buy" if acq_disp == "A" else
                          "Sell" if acq_disp == "D" else
                          {"P": "Buy", "S": "Sell"}.get(code)),
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


def dedupe_joint_filings(transactions: list[dict]) -> list[dict]:
    """Collapse co-filed Form 4s of the SAME economic transaction into one
    row — the SNL convention (one row per economic transaction).

    A jointly held position is reported by EVERY co-owner group: AMAL's
    Workers United bloc files two Form 4s per trade (EDGAR accessions
    0000902664-26-003825/-003826, verified 2026-09-16 — identical date,
    code, shares, price AND shares_after across the pair; the post-holding
    is the group total), so each sale rendered twice in the feed and the
    per-bank table.

    Merge rule: identical (form_type, date, code, shares, price,
    shares_after) AND a different insider name. Same-name rows never merge
    (IBCP's CEO legitimately sold ten same-size lots in one day), and
    unrelated insiders trading the same size at the same price stay apart
    because their post-holdings differ. Runs at READ time — the per-CIK
    cache keeps every filing raw, so the firehose's incremental merge and
    existing cache objects are untouched.

    A merged row keeps the first-seen row's metadata (accession,
    filing_date, filed_at), joins names into `insider` with " / " (the
    names themselves contain commas), and carries them in `insiders`.
    """
    out: list[dict] = []
    heads: dict[tuple, list[dict]] = {}
    names: dict[int, list[str]] = {}
    roles: dict[int, list[str]] = {}
    for tx in transactions:
        key = (tx.get("form_type"), tx.get("date"), tx.get("code"),
               tx.get("shares"), tx.get("price"), tx.get("shares_after"))
        name = tx.get("insider")
        head = None
        if name:
            # A row joins the first head its filer isn't already part of, so
            # co-owners who each report N identical lots pair up lot-for-lot
            # instead of collapsing N economic transactions into one.
            head = next((h for h in heads.get(key, [])
                         if names[id(h)] and name not in names[id(h)]), None)
        if head is None:
            head = dict(tx)
            out.append(head)
            heads.setdefault(key, []).append(head)
            names[id(head)] = [name] if name else []
            roles[id(head)] = [tx["role"]] if tx.get("role") else []
        else:
            names[id(head)].append(name)
            if tx.get("role") and tx["role"] not in roles[id(head)]:
                roles[id(head)].append(tx["role"])
    for head in out:
        ns = names[id(head)]
        if len(ns) > 1:
            head["insider"] = " / ".join(ns)
            head["insiders"] = ns
            head["role"] = " / ".join(roles[id(head)])
    return out


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
        return dedupe_joint_filings(cached["transactions"])

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
    acceptances = recent.get("acceptanceDateTime", [])

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
            "filed_at": _acceptance_to_utc_iso(
                acceptances[i] if i < len(acceptances) else None),
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
            # UTC acceptance instant — lets the Home feed rank an insider row
            # at its real filing time instead of midnight-of-transaction-date
            # (where it lost to every intraday news item and fell off the cap).
            tx["filed_at"] = entry.get("filed_at")
        all_transactions.extend(txs)

    # Sort by transaction date desc
    all_transactions.sort(key=lambda x: x.get("date") or "", reverse=True)

    # Cache RAW (one row per filing) — dedupe is a read-time policy, so the
    # firehose's per-accession incremental merge stays exact.
    try:
        save_json(FORM4_CACHE_PREFIX, f"{cik}.json", {
            "cik": cik,
            "cached_at": datetime.now().isoformat(),
            "transactions": all_transactions,
        })
    except Exception:
        pass

    return dedupe_joint_filings(all_transactions)


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
        for tx in dedupe_joint_filings(cached.get("transactions", [])):
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
                # UTC filing-acceptance instant when known (feed ranking);
                # rows parsed before this field existed simply lack it.
                "filed_at": tx.get("filed_at"),
            })
    out.sort(key=lambda r: r["date"], reverse=True)
    return out[:limit]


# ── Near-real-time delta: EDGAR's current-filings firehose ────────────────
# The nightly refresh-insider sweep discovers filings per bank (~640 CIK
# submissions walks), which is why it can only run nightly. Discovery is the
# expensive part — EDGAR's getcurrent Atom feed solves it in ONE request:
# the latest filings market-wide, newest first, each entry carrying the
# issuer CIK (title), the accession (<id>) and the exact form type
# (<category term>). Poll it each poll-events cycle, keep only true Form 4s
# for universe banks, fetch just those XMLs, and merge into the same per-CIK
# cache the nightly job owns. The nightly sweep stays the completeness
# backstop (the feed window is finite; `type=4` PREFIX-matches, so ~90% of
# entries are 424B2 structured-note noise to be filtered out here).

_GETCURRENT_FORM4_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4"
    "&company=&dateb=&owner=include&count=100&start={start}&output=atom"
)
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_ISSUER_CIK_RE = re.compile(r"\((\d{4,10})\)\s*\(Issuer\)", re.IGNORECASE)
_ACCESSION_RE = re.compile(r"accession-number=([\d-]+)")

# cached_at sentinel for a cache object the firehose CREATED (bank never yet
# swept nightly): always stale, so the next nightly run does the full
# 12-month pull; meanwhile the feed aggregate already sees the delta rows.
_EPOCH_STAMP = "1970-01-01T00:00:00"


def _recent_form4_filings(pages: int = 2) -> list[dict]:
    """Parse EDGAR's current-filings Atom feed → [{cik, accession, filed}]
    for true Form 4 issuer entries, newest first. One HTTP call per page."""
    out, seen = [], set()
    for p in range(pages):
        url = _GETCURRENT_FORM4_URL.format(start=p * 100)
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        try:
            root = ET.fromstring(r.text)
        except ET.ParseError:
            break
        for entry in root.findall(f"{_ATOM_NS}entry"):
            cat = entry.find(f"{_ATOM_NS}category")
            # Exact form match: `type=4` prefix-matches 424B2/424B3/425/…
            if cat is None or cat.get("term") != "4":
                continue
            # Each filing lists twice — (Reporting) person + (Issuer) company;
            # only the issuer entry carries the CIK we can map to a ticker.
            m = _ISSUER_CIK_RE.search(entry.findtext(f"{_ATOM_NS}title") or "")
            if not m:
                continue
            am = _ACCESSION_RE.search(entry.findtext(f"{_ATOM_NS}id") or "")
            if not am or am.group(1) in seen:
                continue
            seen.add(am.group(1))
            # <updated> carries a real UTC offset ("2026-09-15T10:50:27-04:00"),
            # unlike the fake-Z acceptanceDateTime — parse it directly.
            updated = entry.findtext(f"{_ATOM_NS}updated") or ""
            filed_at = None
            try:
                filed_at = datetime.fromisoformat(updated).astimezone(
                    timezone.utc).isoformat()
            except ValueError:
                pass
            out.append({
                "cik": int(m.group(1)),
                "accession": am.group(1),
                "filed": updated[:10] or datetime.now().strftime("%Y-%m-%d"),
                "filed_at": filed_at,
            })
    return out


def poll_form4_firehose(ticker_ciks: dict, pages: int = 2) -> tuple[int, int]:
    """Merge just-filed Form 4s for universe banks into the per-CIK cache,
    within minutes of EDGAR acceptance instead of the nightly sweep's
    next-morning latency (the RVSB director buy of 2026-09-15 sat invisible
    for a day). Returns (filings_merged, transactions_added); the caller
    rebuilds the feed aggregate when filings_merged > 0.

    Cost: `pages` feed requests per poll + 2 requests per matched filing
    (a handful per day universe-wide). Cache freshness is untouched — an
    existing object keeps its cached_at (the nightly full sweep still runs),
    and a created one is stamped permanently stale so nightly backfills it.
    """
    universe_ciks = {int(c) for c in (ticker_ciks or {}).values() if c}
    if not universe_ciks:
        return 0, 0
    tick_by_cik = {}
    for t, c in ticker_ciks.items():
        if c:
            tick_by_cik.setdefault(int(c), t)

    filings = [f for f in _recent_form4_filings(pages)
               if f["cik"] in universe_ciks]
    n_filings = n_tx = 0
    for f in filings:
        cik, accession = f["cik"], f["accession"]
        cached = None
        try:
            cached = load_json(FORM4_CACHE_PREFIX, f"{cik}.json")
        except Exception:
            pass
        existing = (cached or {}).get("transactions", [])
        if any(tx.get("accession") == accession for tx in existing):
            continue  # already merged (or the nightly sweep got it first)
        xml = _fetch_form4_xml(accession, cik)
        if not xml:
            continue
        txs = _parse_form4(xml)
        if not txs:
            continue
        for tx in txs:
            tx["filing_date"] = f["filed"]
            tx["accession"] = accession
            tx["filed_at"] = f.get("filed_at")
        merged = txs + existing
        merged.sort(key=lambda x: x.get("date") or "", reverse=True)
        try:
            save_json(FORM4_CACHE_PREFIX, f"{cik}.json", {
                "cik": cik,
                "cached_at": (cached or {}).get("cached_at") or _EPOCH_STAMP,
                "transactions": merged,
            })
        except Exception:
            continue
        n_filings += 1
        n_tx += len(txs)
        print(f"  [form4-delta] {tick_by_cik.get(cik, cik)}: merged {accession} "
              f"({len(txs)} tx)", flush=True)
    return n_filings, n_tx


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
