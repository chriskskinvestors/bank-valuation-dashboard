"""
Master bank universe — all publicly traded and OTC-traded US banks.

Two-phase discovery:
  Phase 1: Cross-reference SEC company names with FDIC holding company names
  Phase 2: For remaining candidates with bank-like names, verify SIC code
           via EDGAR submissions API (6020-6036 = commercial banks / savings)

The universe is cached for 24 hours via Streamlit's @st.cache_data.
"""

import time
import requests
import streamlit as st

SEC_HEADERS = {"User-Agent": "BankValuationDashboard admin@company.com"}
BANK_SIC_CODES = {"6020", "6021", "6022", "6035", "6036", "6710", "6712"}
BANK_NAME_KEYWORDS = {
    "BANK", "BANCORP", "BANCSHARES", "BANC", "SAVINGS", "THRIFT",
    "FINANCIAL", "BK ", "NATIONAL ASSN",
}

# ETF/ETP tickers from bank issuers (e.g. Deutsche Bank issues ~30 ETNs
# that share CIK 1159508 with DB itself, polluting the bank universe).
_SKIP_TICKERS = {
    "BERZ", "BNKD", "BNKU", "BULZ", "CARD", "CARU", "CONL",
    "FLBL", "FLRT", "HERD", "NRGD", "NRGU", "OILK", "TSLZ",
    "FNGG", "FNGO", "HIBL", "HIBS", "WEBL", "WEBS", "ZSL",
    "BACRP",
    # Deutsche Bank-issued PowerShares/X-trackers ETNs
    "DGP", "DGZ", "DZZ", "DEE", "DEENF", "ADZCF", "OLOXF",
    "DBA", "DBB", "DBC", "DBE", "DBEU", "DBJP", "DBMV",
    "DBO", "DBP", "DBS", "DBV", "DJCI", "DJCB", "UDN",
    "UUP", "USDU", "BNO", "PPLT", "PALL",

    # Foreign-bank ADRs — our pipeline depends on SEC XBRL + FDIC Call
    # Reports, neither of which covers foreign banks. They legitimately
    # have no US-regulator data, so the universe filter would drop them
    # anyway — listing here makes the intent explicit.
    "BBD", "BBDO",  # Banco Bradesco (Brazil)
    "BNS",          # Bank of Nova Scotia (Canada)
    "ITUB",         # Itaú Unibanco (Brazil)
    "DB",           # Deutsche Bank (Germany) — covered above too via ETN dedup
    "BAWAY", "BWAGF",  # BAWAG Group AG (Austria) — ADR + F-share, CIK 1968385
    # Foreign parents of US bank subsidiaries — matched FDIC holding-company
    # names in phase 1 (HSBC Bank USA, City National/RBC, Santander Bank NA…)
    # but are foreign-domiciled filers (6-K/20-F, no US-GAAP facts). Also
    # excluded structurally by _is_us_domestic_filer; listed for intent.
    "HSBC",  # HSBC Holdings (UK)
    "RY",    # Royal Bank of Canada
    "BMO",   # Bank of Montreal (Canada)
    "SAN",   # Banco Santander (Spain)
    "BCS",   # Barclays (UK)
    "UBS",   # UBS Group (Switzerland)
    "MFG",   # Mizuho Financial Group (Japan) — NYSE ADR
    "MZHOF", # Mizuho Financial Group (Japan) — OTC ordinary twin of MFG.
             # MFG (the ADR) is skipped, which leaves MZHOF alone under CIK
             # 1335730, so it slips past the share-class CIK rule as a lone
             # "common". List both twins, as with BBD/BBDO and BAWAY/BWAGF.
    "WF",    # Woori Financial Group (Korea)
    "SHG",   # Shinhan Financial Group (Korea)

    # Credit-card / consumer-finance issuers — excluded by business scope (owner
    # directive 2026-06-16). They hold bank charters but are card/consumer-lending
    # companies, not deposit-taking banks in spirit; out of place in a bank
    # dashboard. Card identity over charter.
    "AXP",   # American Express
    "COF",   # Capital One
    "DFS",   # Discover Financial Services
    "SYF",   # Synchrony Financial
    "BFH",   # Bread Financial (Comenity)
    "ALLY",  # Ally Financial (auto lender)

    # Broker-dealers / non-deposit financial holdings — excluded by business
    # scope (owner directive 2026-06-25). They carry a bank charter (so they
    # match bank-SIC / FDIC) but are brokerages / investment banks in spirit,
    # not deposit-taking commercial banks. Same "identity over charter" rule as
    # the card issuers above.
    "RJF",   # Raymond James Financial (broker-dealer / wealth management)
    "FRHC",  # Freedom Holding Corp (brokerage holding; Freedom Finance)
}

# US-domiciled only (explicit scope: US banks traded on exchanges or OTC).
# SEC submissions mark incorporation with state codes; foreign issuers carry
# country codes (BAWAG: "C4" = Austria). US states + DC + territories:
_US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY", "PR", "GU", "VI", "AS", "MP",
    # EDGAR code for "United States" without a specific state — used by
    # federally chartered entities (e.g. ATLO / Ames National Corp, a
    # national-bank holding company). NOT foreign.
    "X1",
}

# Foreign-private-issuer forms. A US domestic issuer MUST file 10-K/10-Q;
# foreign issuers file these instead (and some, like HSBC, carry an EMPTY
# stateOfIncorporation — so the state check alone is not sufficient).
_FOREIGN_ISSUER_FORMS = {"20-F", "40-F", "6-K"}
_DOMESTIC_FORMS = {"10-K", "10-Q"}


def _is_us_domestic_filer(sub: dict) -> bool:
    """US-domicile check from a SEC submissions JSON, using two signals:
    (1) stateOfIncorporation is a US state/territory code when present;
    (2) the recent filing forms include 10-K/10-Q (domestic) rather than
    only 20-F/40-F/6-K (foreign private issuer). Empty state + domestic
    forms passes (some US filers omit the state)."""
    state = (sub.get("stateOfIncorporation") or "").strip().upper()
    if state and state not in _US_STATE_CODES:
        return False
    forms = set(sub.get("filings", {}).get("recent", {}).get("form", []))
    if forms & _FOREIGN_ISSUER_FORMS and not forms & _DOMESTIC_FORMS:
        return False
    return True


def _clean(n: str) -> str:
    """Normalize a company name for matching."""
    n = n.upper()
    for s in [", INC.", ", INC", " INC.", " INC", " CORP.", " CORP",
              " CO.", " CO", " LTD.", " LTD", "/DE", "/MD", "/NJ",
              "/RI", "/PA", "/OH", "/NC", "/NY", "/VA", "/CA",
              "/WI", "/MI", "/MN", "/TX", "/FL", "/GA", "/IL",
              " N.A.", " NA", ".", ",", "&"]:
        n = n.replace(s, "")
    return n.strip()


def _fetch_fdic_banks() -> dict[str, dict]:
    """Fetch all ACTIVE FDIC institutions and build the HC lookup.

    Filter is ACTIVE:1 — institution records are current-state, so no report
    vintage is needed. (The original REPDTE:YYYYMMDD filter matched ZERO rows
    from the day it was written: the institutions endpoint formats REPDTE as
    MM/DD/YYYY. Combined with the old `except: break`, that meant this
    function silently returned an empty lookup forever and the universe was
    the curated map alone.)

    Raises on fetch failure or an empty result — a partial list must never be
    silently used and cached for 24h."""
    fdic_banks: list[dict] = []
    offset = 0
    while True:
        params = {
            "filters": "ACTIVE:1",
            "fields": "CERT,NAME,NAMEHCR,ASSET,WEBADDR",
            "sort_by": "ASSET",
            "sort_order": "DESC",
            "limit": 1000,
            "offset": offset,
        }
        resp = requests.get(
            "https://banks.data.fdic.gov/api/institutions",
            params=params, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data", [])
        if not rows:
            break
        for r in rows:
            d = r["data"]
            fdic_banks.append({
                "cert": int(d["CERT"]),
                "name": d.get("NAME", ""),         # subsidiary bank brand (e.g. "Provident Bank")
                "namehcr": d.get("NAMEHCR", ""),
                "webaddr": d.get("WEBADDR", ""),   # bank website — seed for IR-site discovery
                "asset": d.get("ASSET") or 0,
            })
        offset += len(rows)
        if offset >= data.get("totals", {}).get("count", 0):
            break
        time.sleep(0.05)
    if not fdic_banks:
        raise RuntimeError("FDIC institutions endpoint returned no active institutions")

    # Deduplicate: HC name -> largest bank cert
    hc_lookup = {}
    for b in fdic_banks:
        hc = b["namehcr"].upper().strip()
        if not hc or len(hc) < 3:
            continue
        if hc not in hc_lookup or b["asset"] > hc_lookup[hc]["asset"]:
            hc_lookup[hc] = b
    return hc_lookup


def _fetch_sec_companies() -> list[list]:
    """Fetch all SEC public companies with tickers. Raises on failure — an
    empty list here would silently collapse the universe to the curated map."""
    resp = requests.get(
        "https://www.sec.gov/files/company_tickers_exchange.json",
        headers=SEC_HEADERS, timeout=15,
    )
    resp.raise_for_status()
    rows = resp.json().get("data", [])
    if not rows:
        raise RuntimeError("SEC company_tickers_exchange.json returned no rows")
    return rows


def _load_lastgood() -> tuple[dict | None, bool]:
    """(universe, is_fresh) from the persisted snapshot. Handles both the
    stamped format ({cached_at, universe}) and the legacy bare-dict format
    (treated as present-but-stale)."""
    from data import cache as _cache
    from data.freshness import is_fresh
    snap = _cache.get("bank_universe_lastgood")
    if not snap:
        return None, False
    if isinstance(snap, dict) and "universe" in snap:
        return snap["universe"], is_fresh(snap, 26 * 3600)
    return snap, False  # legacy format: usable as fallback, never "fresh"


@st.cache_data(ttl=3600, show_spinner="Loading bank universe...")
def build_universe() -> dict[str, dict]:
    """
    The full universe of publicly traded US banks (~470).

    Returns dict: ticker -> {name, cik, fdic_cert, exchange}

    Serving strategy: the LIVE build (full FDIC pagination + per-candidate SEC
    SIC verification) takes ~6-7 minutes — far too slow for a user request —
    so interactive processes serve the snapshot persisted by the nightly
    jobs/refresh_universe run when it's fresh (<26h). A live build happens
    here only when the snapshot is missing/stale, and a source failure falls
    back to a stale snapshot rather than raising (a shrunken universe is never
    silently built — _fetch_* raise on partial data).
    """
    lastgood, fresh = _load_lastgood()
    # Serve the persisted snapshot WHATEVER its age on the interactive path.
    # The live build (_build_universe_live) takes ~6.5 min; on a web request
    # that either hangs the whole page or — worse — is killed by Cloud Run's
    # request timeout BEFORE it can persist, so every cold load re-attempts it
    # and dies (root cause of the 2026-06-13 multi-minute Home hang / blank
    # page with no nav). The nightly refresh-universe JOB has no request
    # timeout and owns all rebuilds; a stale list is a fine, honest fallback
    # (at most a handful of banks off until the next nightly run).
    if lastgood:
        if not fresh:
            print(f"[universe] serving STALE snapshot ({len(lastgood)} banks) "
                  "— interactive path never live-builds; nightly job refreshes")
        return lastgood
    # No snapshot at all (fresh DB / first ever boot) — bootstrap once. This
    # is the only request path that can be slow, and only until the first
    # nightly run persists a snapshot.
    return refresh_universe_snapshot()


def refresh_universe_snapshot() -> dict[str, dict]:
    """Run the live build and persist it as the snapshot interactive
    processes serve. Called nightly by jobs/refresh_universe."""
    from datetime import datetime
    universe = _build_universe_live()
    try:
        from data import cache as _cache
        _cache.put("bank_universe_lastgood",
                   {"cached_at": datetime.now().isoformat(), "universe": universe})
    except Exception as e:
        print(f"[universe] could not persist snapshot: {type(e).__name__}: {e}")
    return universe


def _build_universe_live() -> dict[str, dict]:
    """The actual SEC×FDIC fetch + match. ~6-7 minutes; never call from an
    interactive request path — use build_universe()."""
    sec_rows = _fetch_sec_companies()
    hc_lookup = _fetch_fdic_banks()

    # Authoritative ticker -> exchange from SEC, used as the reconcile fallback
    # below: a curated BANK_MAP entry carries no exchange, and defaulting it to
    # "OTC" mislabels NYSE/NASDAQ banks (e.g. FNB, FFWM), which then breaks any
    # OTC-based logic. SEC's listing is the source of truth.
    sec_exchange = {
        str(t).upper(): e for (_cik, _n, t, e) in sec_rows if t and e
    }

    universe = {}

    # ── Phase 1: Strong name matching ────────────────────────────────────
    for row in sec_rows:
        cik, name, ticker, exchange = row
        if not ticker or not name:
            continue
        ticker = ticker.upper()
        if any(c in ticker for c in ["-", "+"]) or len(ticker) > 5:
            continue
        if ticker in _SKIP_TICKERS:
            continue

        sec_clean = _clean(name)

        for hc_raw, bank_info in hc_lookup.items():
            hc_clean = _clean(hc_raw)

            # Exact match
            if sec_clean == hc_clean:
                universe[ticker] = {
                    "name": name.title() if name.isupper() else name,
                    "cik": int(cik),
                    "fdic_cert": bank_info["cert"],
                    "exchange": exchange or "OTC",
                }
                break

            # Strong prefix match (both names >= 8 chars, >= 65% overlap)
            if len(sec_clean) >= 8 and len(hc_clean) >= 8:
                if hc_clean.startswith(sec_clean) or sec_clean.startswith(hc_clean):
                    overlap = min(len(sec_clean), len(hc_clean))
                    ratio = overlap / max(len(sec_clean), len(hc_clean))
                    if ratio >= 0.65:
                        universe[ticker] = {
                            "name": name.title() if name.isupper() else name,
                            "cik": int(cik),
                            "fdic_cert": bank_info["cert"],
                            "exchange": exchange or "OTC",
                        }
                        break

    # ── Phase 1.5: US-domicile enforcement on name matches ──────────────
    # Phase-1 matches on FDIC holding-company NAMES, which foreign parents
    # of US bank subsidiaries also carry (HSBC Holdings ↔ HSBC Bank USA,
    # Royal Bank of Canada ↔ City National, Santander ↔ Santander Bank NA).
    # Those parents are foreign-domiciled filers (6-K/20-F, no US-GAAP
    # facts) — out of scope ("US-domiciled banks on exchanges or OTC").
    # ~1 fetch per phase-1 match (+~50s nightly), same source as phase 2.
    for t in sorted(universe):
        cik = universe[t].get("cik")
        if not cik:
            continue
        try:
            time.sleep(0.12)  # SEC rate limit: 10 req/sec
            resp = requests.get(
                f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json",
                headers=SEC_HEADERS, timeout=8,
            )
            if resp.status_code != 200:
                continue  # transient — keep; nightly rebuild retries tomorrow
            if not _is_us_domestic_filer(resp.json()):
                print(f"[universe] dropping {t}: foreign-domiciled filer")
                del universe[t]
        except requests.RequestException:
            continue

    # ── Phase 2: SIC code verification for bank-named candidates ─────────
    candidates = []
    for row in sec_rows:
        cik, name, ticker, exchange = row
        if not ticker or not name:
            continue
        ticker = ticker.upper()
        if ticker in universe:
            continue
        if any(c in ticker for c in ["-", "+"]) or len(ticker) > 5:
            continue
        if ticker in _SKIP_TICKERS:
            continue

        name_upper = name.upper()
        if any(kw in name_upper for kw in BANK_NAME_KEYWORDS):
            candidates.append({
                "cik": int(cik), "name": name,
                "ticker": ticker, "exchange": exchange,
            })

    for c in candidates:
        try:
            time.sleep(0.12)  # SEC rate limit: 10 req/sec
            cik_str = str(c["cik"]).zfill(10)
            resp = requests.get(
                f"https://data.sec.gov/submissions/CIK{cik_str}.json",
                headers=SEC_HEADERS, timeout=8,
            )
            if resp.status_code != 200:
                continue
            sub = resp.json()
            sic = sub.get("sic", "")
            if sic not in BANK_SIC_CODES:
                continue
            if not _is_us_domestic_filer(sub):
                continue

            # Confirmed bank — try to find FDIC cert
            fdic_cert = None
            name_clean = _clean(c["name"])
            first_word = name_clean.split()[0] if name_clean else ""
            if first_word and len(first_word) >= 4:
                for hc_raw, bank_info in hc_lookup.items():
                    hc_clean = _clean(hc_raw)
                    if hc_clean.startswith(first_word) or first_word in hc_clean.split():
                        fdic_cert = bank_info["cert"]
                        break

            universe[c["ticker"]] = {
                "name": c["name"].title() if c["name"].isupper() else c["name"],
                "cik": c["cik"],
                "fdic_cert": fdic_cert,
                "exchange": c["exchange"] or "OTC",
            }
        except Exception:
            continue

    # ── Reconcile with curated mappings ──────────────────────────────────
    # The live name-matching above silently drops banks whose SEC and FDIC
    # names don't align — including mega-caps (JPM, C, WFC, GS, MS) and many
    # mid-caps. We have curated, verified CIK+CERT mappings for those, so fold
    # every one in. Skip ETNs (_SKIP_TICKERS) and the handful of foreign ADRs /
    # acquired banks our SEC-XBRL + FDIC-Call-Report pipeline genuinely can't
    # cover (no US-regulator data).
    _NON_COVERABLE = {"DB", "BBD", "BBDO", "BNS", "UBS", "ITUB", "WF", "MCBI"}
    from data.bank_mapping import BANK_MAP
    curated = dict(BANK_MAP)
    try:
        from data.bank_mapping import _RESOLVED_FROM_JSON
        for t, info in _RESOLVED_FROM_JSON.items():
            curated.setdefault(t, info)
    except Exception:
        pass
    for ticker, info in curated.items():
        t = ticker.upper()
        if t in _SKIP_TICKERS or t in _NON_COVERABLE:
            continue
        cik = info.get("cik")
        cert = info.get("fdic_cert")
        if not cik and not cert:
            continue
        # Curated mappings are verified CIK+CERT pairs — they OVERWRITE any
        # phase-1 fuzzy name-match for the same ticker (verified beats fuzzy;
        # previously the fuzzy match would have shadowed the curated one).
        # Keep the SEC-derived exchange when the curated entry lacks one.
        existing = universe.get(t) or {}
        universe[t] = {
            "name": info.get("name") or existing.get("name") or t,
            "cik": int(cik) if cik else None,
            "fdic_cert": int(cert) if cert else None,
            "exchange": (info.get("exchange") or existing.get("exchange")
                         or sec_exchange.get(t) or "OTC"),
        }

    # ── Share-class classification ───────────────────────────────────────
    # Tag each ticker common/preferred so non-common classes (preferred series,
    # baby bonds, redundant second common classes) are dropped from valuation
    # screens — a preferred ticker's ~$25 price against the registrant's common
    # TBVPS produces a garbage ~0.01x P/TBV. FMP verifies the structural pick.
    try:
        from data.share_class import annotate_share_classes
        from data.fmp_client import get_company_name
        annotate_share_classes(universe, name_lookup=get_company_name)
    except Exception as e:
        print(f"[universe] share-class annotation skipped: {type(e).__name__}: {e}")

    # ── Subsidiary bank brand ────────────────────────────────────────────
    # Stamp each entry with its FDIC institution NAME (the bank-subsidiary brand,
    # e.g. PFS→"Provident Bank", INDB→"Rockland Trust Company"). Holdcos publish
    # news under this brand, which the SEC holdco name never matches — the news
    # matcher (data/events/wire_base.build_name_index) indexes it alongside the
    # holdco name. Sourced from the FDIC certs already fetched (no extra calls).
    cert_name = {bi["cert"]: bi.get("name", "")
                 for bi in hc_lookup.values() if bi.get("name")}
    cert_web = {bi["cert"]: bi.get("webaddr", "")
                for bi in hc_lookup.values() if bi.get("webaddr")}
    for info in universe.values():
        cert = info.get("fdic_cert")
        nm = cert_name.get(cert)
        if nm:
            info["bank_name"] = nm.title() if nm.isupper() else nm
        web = cert_web.get(cert)
        if web:
            info["webaddr"] = web

    return universe


# Module-level cache to avoid re-deserializing the universe dict on every call
_UNIVERSE_CACHE: dict | None = None


def universe_is_cached() -> bool:
    """True if the universe dict is already built this process. Read paths that
    want to filter/canonicalize against the universe (e.g. the news feed) check
    this first so they never trigger the ~174s cold build on a render thread —
    they just skip the enrichment until some other surface has built it."""
    return _UNIVERSE_CACHE is not None


def get_universe() -> dict[str, dict]:
    """Get the universe dict, cached at module level for maximum speed.

    This is the RAW discovered set (~439) — it keeps every ticker, including
    non-common share classes, because jobs and data.bank_mapping resolve CIK/
    cert against it (a preferred ticker must still resolve). User-facing
    surfaces use the covered set instead (see get_noncommon_tickers,
    search_universe, get_universe_count)."""
    global _UNIVERSE_CACHE
    if _UNIVERSE_CACHE is None:
        _UNIVERSE_CACHE = build_universe()
    return _UNIVERSE_CACHE


# Memoized non-common set (preferred series, baby bonds, redundant/stale dup
# listings). Cheap to compute but recomputed nowhere — pinned to the universe.
_NONCOMMON_CACHE: set[str] | None = None


def get_noncommon_tickers() -> set[str]:
    """Universe tickers that are NOT a registrant's primary common stock, so
    they carry no valid per-common metrics. Hidden from search + the covered
    count and excluded from the valuation scope. See data/share_class.py."""
    global _NONCOMMON_CACHE
    if _NONCOMMON_CACHE is None:
        from data.share_class import noncommon_tickers
        _NONCOMMON_CACHE = noncommon_tickers(get_universe())
    return _NONCOMMON_CACHE


# Memoized sibling -> primary-common remap (inverse of the non-common set).
_NONCOMMON_PRIMARY_CACHE: dict[str, str] | None = None


def get_noncommon_primary_map() -> dict[str, str]:
    """Map each non-common sibling ticker -> its registrant's primary common
    (e.g. VYLD/AMJB -> JPM, FRMEP -> FRME). Canonicalizes a ticker that was
    attributed to a preferred/ETN sibling. See data/share_class.py."""
    global _NONCOMMON_PRIMARY_CACHE
    if _NONCOMMON_PRIMARY_CACHE is None:
        from data.share_class import noncommon_to_primary
        _NONCOMMON_PRIMARY_CACHE = noncommon_to_primary(get_universe())
    return _NONCOMMON_PRIMARY_CACHE


def coverage_excluded() -> set[str]:
    """Tickers hidden from every covered/display surface (screens, leaderboard,
    search, count):
      • non-common share classes (preferred series, baby bonds, dup listings), +
      • explicitly skipped foreign ADRs / ETNs that leaked into the raw snapshot
        (e.g. MZHOF, the OTC twin of the skipped ADR MFG).
    Runtime enforcement of _SKIP_TICKERS here means a skip-list addition takes
    effect on the served snapshot immediately, without waiting for a rebuild."""
    universe = get_universe()
    return get_noncommon_tickers() | (set(universe) & _SKIP_TICKERS)


@st.cache_data(ttl=86400, show_spinner=False)
@st.cache_data(ttl=3600, show_spinner=False)
def get_universe_tickers() -> list[str]:
    """
    Return sorted list of all bank tickers in the universe — FILTERED to
    only those that resolve to at least one ACTIVE data source:
      • SEC CIK with XBRL data (validated by the resolver), OR
      • FDIC cert pointing to an ACTIVE institution (not acquired/closed).

    Tickers that can't resolve, or whose only data source is an inactive
    FDIC cert (acquired bank like MCBI), are dropped here so they never
    appear empty or stale in the UI.
    """
    from data.bank_mapping import get_cik, get_fdic_cert
    from data.fdic_client import cert_is_active

    universe = get_universe()
    all_tickers = sorted(universe.keys())

    # Non-common share classes (preferred series, baby bonds, redundant second
    # common classes) resolve to data but share their registrant's CIK/cert, so
    # they'd carry the common's TBVPS against their own ~$25 price — a garbage
    # P/TBV. Plus skipped foreign ADRs/ETNs that leaked into the snapshot. Drop
    # both here, the single scope feeding screens + leaderboard.
    excluded = coverage_excluded()

    def _resolves(t: str) -> bool:
        cik = get_cik(t)
        if cik is not None:
            return True  # SEC path; resolver already validated XBRL recency
        cert = get_fdic_cert(t)
        if cert is None:
            return False
        return cert_is_active(cert)  # Drop acquired/closed institutions

    resolved = [t for t in all_tickers if t not in excluded and _resolves(t)]
    dropped_nc = excluded & set(all_tickers)
    if dropped_nc:
        print(f"[universe] Excluded {len(dropped_nc)} non-common / out-of-scope "
              f"tickers: {', '.join(sorted(dropped_nc)[:12])}"
              f"{'...' if len(dropped_nc) > 12 else ''}")
    dropped = set(all_tickers) - set(resolved) - dropped_nc
    if dropped:
        print(f"[universe] Dropped {len(dropped)} unresolvable/inactive tickers: "
              f"{', '.join(sorted(dropped)[:10])}"
              f"{'...' if len(dropped) > 10 else ''}")
    return resolved


def get_universe_count() -> int:
    """Number of banks we COVER = raw universe minus non-common share classes
    and out-of-scope tickers (a registrant is counted once, by its common stock
    — not once per preferred series)."""
    return len(get_universe()) - len(coverage_excluded())


def get_universe_count_fast() -> str:
    """
    Return the covered universe size for the header WITHOUT forcing an expensive
    build. Prefers the real built count (minus non-common share classes) when
    the universe is already cached this process; otherwise falls back to the
    curated mapping count (cheap, static). Never returns a hardcoded guess.
    """
    if _UNIVERSE_CACHE is not None:
        return str(len(_UNIVERSE_CACHE) - len(coverage_excluded()))
    try:
        from data.bank_mapping import BANK_MAP
        certs = set(BANK_MAP)
        try:
            from data.bank_mapping import _RESOLVED_FROM_JSON
            certs |= set(_RESOLVED_FROM_JSON)
        except Exception:
            pass
        return str(len(certs))
    except Exception:
        return "—"


def search_universe(query: str, limit: int = 25) -> list[dict]:
    """
    Search the bank universe by ticker or company name.
    Returns list of {ticker, name, cik, fdic_cert, exchange}.
    """
    from utils.formatting import format_bank_name
    universe = get_universe()
    query_upper = query.upper().strip()

    if not query_upper:
        return []

    def _row(ticker, info):
        return {"ticker": ticker, **info,
                "name": format_bank_name(info.get("name") or ticker, ticker)}

    # Exact ticker match — honored even for a non-common class (explicit lookup
    # of, say, FCNCP), but those are hidden from browse/prefix/name discovery
    # below so they don't clutter results.
    if query_upper in universe:
        return [_row(query_upper, universe[query_upper])]

    excluded = coverage_excluded()
    results = []
    seen_tickers = set()

    # Ticker prefix match first
    for ticker, info in universe.items():
        if ticker in excluded:
            continue
        if ticker.startswith(query_upper):
            results.append(_row(ticker, info))
            seen_tickers.add(ticker)

    # Then name match — match against the raw stored name (broadest), display
    # the normalized one.
    for ticker, info in universe.items():
        if ticker in seen_tickers or ticker in excluded:
            continue
        if query_upper in info["name"].upper():
            results.append(_row(ticker, info))

    return results[:limit]


def get_universe_bank(ticker: str) -> dict | None:
    """
    Look up a single bank in the universe.
    Falls back to dynamic resolution if not in prebuilt universe.
    """
    from utils.formatting import format_bank_name
    ticker = ticker.upper()
    universe = get_universe()
    info = universe.get(ticker)
    if info:
        return {"ticker": ticker, **info,
                "name": format_bank_name(info.get("name") or ticker, ticker)}

    # Fallback to dynamic resolution
    from data.bank_mapping import resolve_ticker
    resolved = resolve_ticker(ticker)
    if resolved.get("cik") or resolved.get("fdic_cert"):
        return {**resolved,
                "name": format_bank_name(resolved.get("name") or ticker, ticker)}
    return None
