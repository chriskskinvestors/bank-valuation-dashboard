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

# v2 (2026-10-02): snapshots written before the common-CUSIP / report-period
# fixes hold option-inflated, non-common and wrong-quarter rows, and the
# merge-only writer never drops a filer — a new namespace keeps every reader
# off them. The nightly job re-seeds the current quarter; `--backfill`
# rebuilds past quarters with the corrected code.
FORM13F_CACHE_PREFIX = "form13f_cache_v2"
# Instrument words that mark a non-common row (whole words, see the filter).
_NON_COMMON_WORDS = frozenset({
    "PREFERRED", "PREF", "PFD", "DEPOSITARY", "DEP", "WARRANT", "WARRANTS",
    "WTS", "CONVERTIBLE", "CONV", "NOTE", "NOTES", "BOND", "BONDS", "DEBT",
    "DEBENTURE", "DEBENTURES", "RIGHTS", "RTS", "UNIT", "UNITS",
})
CACHE_TTL_SECONDS = 86400
# Render-path file TTL: 13F-HRs change quarterly (trickling in over the 45 days
# after quarter-end), so a week bounds staleness without a crawl per bank per day.
RENDER_TTL_SECONDS = 7 * 86400

HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}

EDGAR_FTS = "https://efts.sec.gov/LATEST/search-index"


# Shared freshness check (data/freshness) bound to this module's TTL.
def _is_fresh(cached: dict | None) -> bool:
    from data.freshness import is_fresh
    return is_fresh(cached, RENDER_TTL_SECONDS)


def _search_13f_for_ticker(ticker: str, limit: int = 40,
                           startdt: str | None = None,
                           enddt: str | None = None,
                           quarter: str | None = None) -> list[dict]:
    """
    Search EDGAR full-text for 13F-HR filings mentioning the ticker.
    Returns list of {cik, accession, filer_name, date_filed, period_ending,
    form} — ONE filing per filer, all covering the same report quarter (see
    _current_period_filings). Defaults to the trailing ~130-day window (the
    current-holders path, quarter = the live filing season); pass explicit
    startdt/enddt (YYYY-MM-DD) + quarter ("YYYYQn") to search a past
    quarter's filing season (the backfill path).
    """
    since_date = startdt or (datetime.now() - timedelta(days=130)).strftime("%Y-%m-%d")
    end_date = enddt or datetime.now().strftime("%Y-%m-%d")

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
    for hit in hits:
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
                "period_ending": src.get("period_ending"),
                "form": src.get("form"),
            })
    # Period filter BEFORE the limit, so catch-up filings for old quarters
    # can't eat the candidate budget; rank order (first hit per filer) kept.
    return _current_period_filings(results, quarter)[:limit]


def _current_period_filings(results: list[dict],
                            quarter: str | None = None) -> list[dict]:
    """Keep one filing per filer, all covering the same report quarter.

    EDGAR full-text hits mix the live filing season with catch-up filings for
    old periods (a manager filing 2019–2025 13Fs in Aug 2026) — taken by
    filing date they were shown as current holders. Target quarter = the
    given ``quarter``, else the most common period_ending among the hits (the
    live season; a tie goes to the later quarter). Hits for any other period,
    or with no period_ending, are dropped. Per filer the latest-filed filing
    wins, except that a 13F-HR/A whose amendmentType is not RESTATEMENT (a
    "NEW HOLDINGS" add-on, or unreadable) never replaces the original. When
    NO hit carries period_ending the list is returned unfiltered (legacy
    filing-date routing applies downstream)."""
    quarters = [_period_quarter(r.get("period_ending")) for r in results]
    if not any(quarters):
        return results
    if quarter is None:
        counts: dict[str, int] = {}
        for q in quarters:
            if q:
                counts[q] = counts.get(q, 0) + 1
        top = max(counts.values())
        tied = sorted(q for q, n in counts.items() if n == top)
        if len(tied) > 1:
            print(f"[13F] period tie {tied} ({top} hits each) — using {tied[-1]}")
        quarter = tied[-1]

    by_filer: dict[str, list[dict]] = {}
    for r, q in zip(results, quarters):
        if q == quarter:
            by_filer.setdefault(r["cik"], []).append(r)

    # dict order = first-hit rank
    return [_pick_filing(group) for group in by_filer.values()]


def _pick_filing(group: list[dict]) -> dict:
    """One filer's filing for one report period, from dicts carrying cik,
    accession, date_filed, form. The latest-filed original 13F-HR, unless a
    13F-HR/A RESTATEMENT supersedes it (newest first); a "NEW HOLDINGS" (or
    unreadable) amendment never replaces an original. Amendment-only → the
    latest amendment."""
    def _filed(r):
        return (r.get("date_filed") or "", r.get("accession") or "")

    originals = [r for r in group if r.get("form") != "13F-HR/A"]
    amendments = sorted((r for r in group if r.get("form") == "13F-HR/A"),
                        key=_filed, reverse=True)
    if not originals:
        return amendments[0]
    choice = max(originals, key=_filed)
    for a in amendments:
        kind = _amendment_type(a["cik"], a["accession"])
        if kind == "RESTATEMENT":
            return a
        print(f"[13F] keeping original over {a['accession']} "
              f"(amendmentType={kind!r})")
    return choice


def _amendment_type(cik: str, accession: str) -> str | None:
    """amendmentType of a 13F-HR/A from its primary_doc.xml ("RESTATEMENT" /
    "NEW HOLDINGS"), upper-cased; None when unreadable."""
    acc_no_hyphens = accession.replace("-", "")
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{acc_no_hyphens}/primary_doc.xml")
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
    except Exception:
        return None
    m = re.search(r"<(?:\w+:)?amendmentType>\s*([^<]+?)\s*<", r.text)
    return m.group(1).upper() if m else None


def _issuer_matches(target: str, name_upper: str) -> bool:
    """Token-boundary match of a search term against a 13F issuer name.

    NOT a naive substring: a bare ticker like "KEY" or "BANC" must match the
    whole issuer word "KEY"/"BANC" and never bleed into "KEYSIGHT" or
    "BANCOLOMBIA". The issuer name is split into upper-cased alphanumeric word
    tokens and the target must equal one of them (or the full name). A
    multi-word company-name search term matches only when its exact phrase
    appears in the issuer name.
    """
    target_upper = target.upper()
    if not target_upper:
        return False
    if target_upper == name_upper:
        return True
    if " " in target_upper:
        return target_upper in name_upper
    return target_upper in set(re.findall(r"[A-Z0-9]+", name_upper))


def _fetch_13f_info_table(cik: str, accession: str, target_ticker: str,
                          cusip: str | None = None) -> list[dict] | None:
    """
    Parse the 13F infoTable.xml to extract holdings for a specific ticker/CUSIP.
    Rows match by issuer name, or — when ``cusip`` is given — by CUSIP alone
    (a filer's issuer spelling drifts between quarters: "OLD NATIONAL
    BANCORP" vs "OLD NATL BANCORP IND"). Returns the matched rows ([] = the
    table holds no such security), or None when the filing's information
    table could not be fetched/parsed — a failed read is not evidence of
    "not held".
    """
    acc_no_hyphens = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_hyphens}"
    try:
        r = requests.get(f"{base}/index.json", headers=HEADERS, timeout=10)
        r.raise_for_status()
        items = r.json().get("directory", {}).get("item", [])
    except Exception:
        return None

    # The information table is the filing's non-cover XML. Its filename is
    # free-form ("13f_Filer.xml", "57123.xml", "jun26.xml") — matching on
    # "info" dropped ~half of the holders. If several XMLs remain, the one
    # whose root element is informationTable wins.
    xml_files = [it["name"] for it in items
                 if it["name"].lower().endswith(".xml")
                 and it["name"].lower() != "primary_doc.xml"]
    root = None
    for info_file in xml_files:
        try:
            resp = requests.get(f"{base}/{info_file}", headers=HEADERS, timeout=15)
            resp.raise_for_status()
            candidate = ET.fromstring(resp.text)
        except Exception:
            continue
        if len(xml_files) == 1 or candidate.tag.endswith("informationTable"):
            root = candidate
            break
    if root is None:
        return None

    positions = []
    for info in root.iter():
        if not info.tag.endswith("infoTable"):
            continue
        name = None
        cusip_row = None
        shares = None
        value = None
        class_ = None
        put_call = None
        for child in info:
            tag = child.tag.split("}")[-1]
            if tag == "nameOfIssuer":
                name = (child.text or "").strip()
            elif tag == "cusip":
                cusip_row = (child.text or "").strip()
            elif tag == "titleOfClass":
                class_ = (child.text or "").strip()
            elif tag == "putCall":
                put_call = (child.text or "").strip()
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
        # Options (a putCall row's sshPrnamt is the UNDERLYING share count,
        # its value the option's) are never common-share ownership.
        if put_call:
            continue
        # Match the ticker's issuer name (loose matching) but EXCLUDE preferred
        # stock, depositary shares, warrants, convertibles, and other non-common
        # instruments — these have different prices/economics than common shares
        # and shouldn't be aggregated as "ownership".
        name_upper = name.upper()
        class_upper = (class_ or "").upper()

        if cusip:
            if _norm_cusip(cusip_row) != _norm_cusip(cusip):
                continue
        elif not _issuer_matches(target_ticker, name_upper):
            continue

        # Exclude non-common instruments
        # WHOLE-WORD match (a substring "UNIT" dropped every COMMUNITY /
        # UNITED bank's holders; "PREFERRED" dropped Preferred Bank's), and
        # the search term's own words never count against the issuer name.
        # Class "Pref" (IAT Reinsurance's WTFC row) is a whole word.
        words = (set(re.findall(r"[A-Z0-9]+", name_upper))
                 - set(re.findall(r"[A-Z0-9]+", target_ticker.upper())))
        words |= set(re.findall(r"[A-Z0-9]+", class_upper))
        if words & _NON_COMMON_WORDS:
            continue

        positions.append({
            "issuer": name, "cusip": cusip_row, "class": class_,
            "shares": shares, "value_thousands": value,
        })

    return positions


def filing_index_url(cik: str | int, accession: str) -> str:
    """Human-readable EDGAR filing-index URL for a 13F-HR accession."""
    acc_no_hyphens = str(accession).replace("-", "")
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{acc_no_hyphens}/{accession}-index.htm")


def _filer_13f_history(cik: str | int) -> list[tuple[str, str, str, str]]:
    """List a filer's 13F-HR accessions as (accession, filing_date,
    report_date, form), newest first. report_date is the period the filing
    covers (submissions' reportDate, e.g. "2026-03-31")."""
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
    periods = recent.get("reportDate", [])
    out = [(accs[i], dates[i], periods[i] if i < len(periods) else "", forms[i])
           for i in range(len(forms)) if forms[i] in ("13F-HR", "13F-HR/A")]
    # submissions API is already newest-first, but sort defensively by date
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def _prior_quarter_shares(cik: str, quarter: str, target_ticker: str,
                          cusip: str | None = None):
    """Shares this filer held of target_ticker in its 13F-HR for the quarter
    BEFORE ``quarter`` ("YYYYQn") — the previous report PERIOD, never merely
    the previous filing (that can be the same quarter's original).

    Returns float shares (0.0 = the filer reported that quarter without the
    stock → a genuine New position), or None when the filer has NO 13F-HR for
    the prior quarter (no basis → change n/a). Filing choice = _pick_filing,
    the same rule as the current period: a 13F-HR/A RESTATEMENT supersedes
    the original (JPMorgan's own Q1-2026 original omits its JPM rows; the
    same-day restatement carries them), a NEW HOLDINGS /A never replaces it.
    Rows match by the common ``cusip`` when known (never the name: Quadrant's
    and PanAgora's Q1 tables spell ONB "OLD NATL BANCORP IND" → a name match
    made them false "New"); option rows excluded as in the current table.
    Raises when the prior table can't be read."""
    prior_q = _prev_quarter(quarter)
    if not prior_q:
        return None
    same = [{"cik": cik, "accession": acc, "date_filed": filed, "form": form}
            for acc, filed, period, form in _filer_13f_history(cik)
            if _period_quarter(period) == prior_q]
    if not same:
        return None
    prior_acc = _pick_filing(same)["accession"]
    positions = _fetch_13f_info_table(cik, prior_acc, target_ticker, cusip)
    if positions is None:
        raise RuntimeError(f"prior 13F-HR {prior_acc} information table unreadable")
    return float(sum(p.get("shares") or 0 for p in positions))


def _classify_change(current_shares, prior_shares) -> dict:
    """Position-change status vs prior quarter. prior_shares None = no
    prior-quarter 13F-HR to compare against → "n/a", never "New"."""
    if prior_shares is None:
        return {"change_status": "n/a", "change_pct": None}
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


def _period_quarter(period_ending: str | None) -> str | None:
    """Report period end date ("2026-06-30") → "2026Q2"; None if unparseable."""
    try:
        d = datetime.strptime(str(period_ending)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def _prev_quarter(quarter: str | None) -> str | None:
    """"2026Q2" → "2026Q1"; "2026Q1" → "2025Q4"; None if malformed."""
    m = re.fullmatch(r"(\d{4})Q([1-4])", str(quarter or ""))
    if not m:
        return None
    year, q = int(m.group(1)), int(m.group(2))
    return f"{year - 1}Q4" if q == 1 else f"{year}Q{q - 1}"


def _holder_quarter(h: dict) -> str | None:
    """Quarter a holder's filing covers: its own period_ending; the
    filing-date heuristic only when the period is absent."""
    return (_period_quarter(h.get("period_ending"))
            or _report_quarter(h.get("date_filed") or ""))


def _quarter_filename(ticker: str, quarter: str) -> str:
    return f"{ticker.upper()}_{quarter}.json"


def _save_quarter_snapshots(ticker: str, holders: list[dict]) -> None:
    """
    Persist holders into quarter-keyed snapshot files alongside the latest
    window. Each holder lands in the quarter its own filing covers (its
    period_ending; filing-date heuristic only when absent — a refresh during
    a filing window can straddle two quarters). Merge is by filer CIK
    with the fresh fetch winning per filer, so re-running the same refresh is
    idempotent and a holder seen in an earlier refresh of the same quarter is
    never dropped.
    """
    by_quarter: dict[str, list[dict]] = {}
    for h in holders:
        q = _holder_quarter(h)
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


def get_crossholdings(ticker: str, top_holders: int = 25) -> dict:
    """Inferred crossholdings (SNL plan §13): for the subject bank's largest
    stored holders, which OTHER universe banks does each institution also hold?
    Pure cross-join of the stored quarterly snapshots — no new fetches, so
    coverage equals the set of banks whose 13F snapshots have been stored
    (grows as 13F tabs are viewed; a universe-wide warm job is a later task).

    Returns {"quarter": q, "coverage": n_banks_scanned, "rows": [
        {"holder": name, "subject_value_usd": v,
         "others": [{"ticker": tk, "shares": s, "value_usd": v}, ...]}]}
    or {} when the subject has no stored snapshot.
    """
    if not ticker:
        return {}
    t = ticker.upper()
    # Subject's latest stored quarter (same discovery as get_holder_history).
    found = set()
    for name in list_files(FORM13F_CACHE_PREFIX, f"{t}_*.json"):
        m = _QUARTER_FILE_RE.search(name)
        if m and name == _quarter_filename(t, m.group(1)):
            found.add(m.group(1))
    if not found:
        return {}
    quarter = max(found)
    subj = load_json(FORM13F_CACHE_PREFIX, _quarter_filename(t, quarter)) or {}
    subj_holders = sorted((h for h in subj.get("holders", []) if h.get("filer_name")),
                          key=lambda h: -(h.get("value_usd") or 0))[:top_holders]
    if not subj_holders:
        return {}

    # Every OTHER bank's snapshot for the same quarter → holder-name index.
    by_holder: dict[str, list[dict]] = {}
    scanned = 0
    for name in list_files(FORM13F_CACHE_PREFIX, f"*_{quarter}.json"):
        other = name[: -len(f"_{quarter}.json")]
        if other == t or not other.isalpha():
            continue
        snap = load_json(FORM13F_CACHE_PREFIX, name) or {}
        holders = snap.get("holders")
        if not holders:
            continue
        scanned += 1
        for h in holders:
            hn = h.get("filer_name")
            if hn:
                by_holder.setdefault(hn, []).append(
                    {"ticker": other, "shares": h.get("shares"),
                     "value_usd": h.get("value_usd")})

    rows = []
    for h in subj_holders:
        others = sorted(by_holder.get(h["filer_name"], []),
                        key=lambda o: -(o.get("value_usd") or 0))
        rows.append({"holder": h["filer_name"],
                     "subject_value_usd": h.get("value_usd"),
                     "others": others})
    return {"quarter": quarter, "coverage": scanned, "rows": rows}


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _holders_from_candidates(candidates: list[dict], search_term: str,
                             max_filers: int) -> list[dict]:
    """Fetch + filter each candidate filer's info table into holder dicts,
    value-desc sorted. Shared by the current-holders path and the backfill
    path — no caching or QoQ side effects here.

    Name matching also catches the issuer's CDs, ETNs, structured notes and
    preferreds (JPMorgan Chase Bank NA CDs, JPMorgan Chase Financial notes).
    Rows are therefore restricted to the bank's COMMON CUSIP — the CUSIP held
    by the most distinct filers among the matched rows (see _common_cusip);
    when that is ambiguous (tie / no CUSIPs) all matched rows are kept, as
    before, and the ambiguity is logged."""
    queue = []
    seen_filers = set()
    for c in candidates:
        if c["cik"] not in seen_filers:
            seen_filers.add(c["cik"])
            queue.append(c)

    def _fetch(c):
        return _fetch_13f_info_table(c["cik"], c["accession"], search_term)

    # Phase 1: up to max_filers candidates with matched rows → common CUSIP.
    fetched = []
    nxt = 0
    while nxt < len(queue) and len(fetched) < max_filers:
        c = queue[nxt]
        nxt += 1
        positions = _fetch(c)
        if positions:
            fetched.append((c, positions))
    cusip = _common_cusip([p for _, p in fetched])
    if cusip is None and fetched:
        print(f"[13F] no unambiguous common CUSIP for {search_term!r} — "
              f"keeping all name-matched rows")

    def _common_only(positions):
        if cusip is None:
            return positions
        return [p for p in positions if _norm_cusip(p.get("cusip")) == cusip]

    all_holders = []

    def _add(c, positions):
        h = _holder_row(c, _common_only(positions), cusip)
        if h:
            all_holders.append(h)

    for c, positions in fetched:
        _add(c, positions)
    # Phase 2: the CUSIP filter can drop filers whose only match was a
    # non-common instrument — top up from the remaining candidates.
    while nxt < len(queue) and len(all_holders) < max_filers:
        c = queue[nxt]
        nxt += 1
        positions = _fetch(c)
        if positions:
            _add(c, positions)

    all_holders.sort(key=lambda h: h.get("value_usd", 0), reverse=True)
    return all_holders


def _norm_cusip(cusip) -> str:
    return str(cusip or "").strip().upper()


def _common_cusip(position_lists: list[list[dict]]) -> str | None:
    """The CUSIP held by the most distinct filers (one vote per filer per
    CUSIP). None when no row carries a CUSIP or the top count is tied."""
    votes: dict[str, int] = {}
    for positions in position_lists:
        for cu in {_norm_cusip(p.get("cusip")) for p in positions} - {""}:
            votes[cu] = votes.get(cu, 0) + 1
    if not votes:
        return None
    ranked = sorted(votes.values(), reverse=True)
    if len(ranked) > 1 and ranked[0] == ranked[1]:
        return None
    return max(votes, key=votes.get)


def _holder_row(c: dict, positions: list[dict], cusip: str | None) -> dict | None:
    """One filer's holder dict from its (filtered) matched rows; None when
    nothing positive remains."""
    if not positions:
        return None
    filer_id = c["cik"]
    # Aggregate this filer's positions in this ticker
    total_shares = sum(p.get("shares") or 0 for p in positions)
    total_raw_value = sum(p.get("value_thousands") or 0 for p in positions)

    if total_shares <= 0:
        return None

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

    return {
        "filer_cik": filer_id,
        "filer_name": c["filer_name"],
        "date_filed": c["date_filed"],
        "accession": c["accession"],
        "filing_url": filing_index_url(filer_id, c["accession"]),
        "shares": total_shares,
        "value_usd": total_value_usd,
        "positions": positions,
        "period_ending": c.get("period_ending"),
        "cusip": cusip,
    }


def _quarter_filing_window(quarter: str) -> tuple[str, str] | None:
    """Filing-season window for a report quarter: 13F-HRs covering quarter Q
    are due 45 days after Q's end — search [end+1d, end+75d] (buffer for
    late filers). Returns (startdt, enddt) or None on a malformed quarter."""
    m = re.fullmatch(r"(\d{4})Q([1-4])", str(quarter).strip().upper())
    if not m:
        return None
    year, q = int(m.group(1)), int(m.group(2))
    end_month = q * 3
    # Last day of the quarter's final month.
    if end_month == 12:
        q_end = datetime(year, 12, 31)
    else:
        q_end = datetime(year, end_month + 1, 1) - timedelta(days=1)
    return ((q_end + timedelta(days=1)).strftime("%Y-%m-%d"),
            (q_end + timedelta(days=75)).strftime("%Y-%m-%d"))


def backfill_quarter(ticker: str, company_name: str = "",
                     quarter: str = "", max_filers: int = 25) -> int | None:
    """Backfill one past quarter's 13F snapshot from EDGAR (plan §13 phase 2).

    Searches the quarter's own filing season and persists holders through the
    same merge-only snapshot writer the live path uses. Merge-only contract:
    a quarter that already has a stored snapshot is SKIPPED (returns None) —
    backfill never overwrites accumulated history. Returns the number of
    holders found (0 = quarter searched, no coverage — normal for small
    banks; the empty result is not persisted so a retry stays possible).
    """
    if not ticker or not quarter:
        return None
    window = _quarter_filing_window(quarter)
    if window is None:
        print(f"[13F] backfill: malformed quarter {quarter!r}")
        return None
    existing = load_json(FORM13F_CACHE_PREFIX,
                         _quarter_filename(ticker, quarter)) or {}
    if existing.get("holders"):
        return None  # already have history for this quarter — never clobber

    search_term = ticker
    if company_name:
        co_clean = re.sub(r"(Inc\.|Corp\.|Corporation|Company|Co\.|Ltd\.).*$",
                          "", company_name).strip()
        search_term = co_clean or ticker

    startdt, enddt = window
    candidates = _search_13f_for_ticker(search_term, limit=max_filers * 2,
                                        startdt=startdt, enddt=enddt,
                                        quarter=quarter.strip().upper())
    holders = _holders_from_candidates(candidates, search_term, max_filers)
    # Route strictly by each filing's own covered quarter (an amended or
    # late-window filing lands in ITS quarter, never mislabeled into this one).
    if holders:
        _save_quarter_snapshots(ticker, holders)
    return len(holders)


def fetch_institutional_holdings(ticker: str, company_name: str = "",
                                   max_filers: int = 25,
                                   with_changes: bool = True, *,
                                   force: bool = False) -> list[dict]:
    """
    Find 13F filings holding this ticker's stock, return list of holders.

    force=True skips the cached-file read and refetches + persists — the
    warming job's path (a fresh file would otherwise be handed back unrefreshed).
    """
    if not ticker:
        return []

    cached = None if force else load_json(FORM13F_CACHE_PREFIX,
                                          f"{ticker.upper()}.json")
    if _is_fresh(cached) and "holders" in cached:
        return cached["holders"]

    # Search for 13Fs mentioning this ticker (and optionally the company name)
    search_term = ticker
    if company_name:
        # Strip generic suffixes
        co_clean = re.sub(r"(Inc\.|Corp\.|Corporation|Company|Co\.|Ltd\.).*$", "", company_name).strip()
        search_term = co_clean or ticker

    candidates = _search_13f_for_ticker(search_term, limit=max_filers * 2)
    all_holders = _holders_from_candidates(candidates, search_term, max_filers)

    # Quarter-over-quarter position change vs each filer's 13F-HR for the
    # PREVIOUS report quarter. Best effort and bounded to what we display.
    if with_changes:
        for h in all_holders[:max_filers]:
            try:
                prior = _prior_quarter_shares(
                    h["filer_cik"], _holder_quarter(h), search_term,
                    h.get("cusip"))
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
