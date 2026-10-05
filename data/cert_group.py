"""Multi-charter holding companies: one ticker → MANY FDIC certs.

The platform's mapping is one ticker → one cert, which silently represents a
multi-bank holdco by its LEAD charter alone. Measured 2026-08-02 across the
universe, 11 banks are affected and the understatement is severe:

    WTFC  12.8%   $9.3B of $72.4B   16 charters
    QCRH  30.6%   $3.1B of $10.1B    4
    ATLO  50.7%   $1.1B of  $2.2B    6
    IBOC  57.2%   $9.9B of $17.3B    5
    MS    61.8%   $391B of  $633B    2
    GCBC  69.3%   $3.2B of  $4.6B    2
    BPOP  80.2%  $60.6B of $75.6B    2
    GOVB  84.3%   $199M of  $236M    2
    BANF  85.6%  $12.8B of $14.9B    3
    MBWM  91.8%   $6.4B of  $6.9B    2
    BNY   93.5%   $467B of  $500B    4

Every FDIC-sourced level (assets, deposits, loans, equity, income) was that
fraction of the real banking operation, and every FDIC ratio was the lead
charter's rather than the consolidated bank's.

WHAT AGGREGATES, AND WHAT DELIBERATELY DOES NOT
-----------------------------------------------
Levels (stocks and flows) sum: a group's assets ARE the sum of its charters'
assets. Ratios do not, and this module refuses to fake them:

  * RECOMPUTED from summed components — the formula is a pure ratio of two
    summed levels, so it is exact:
        EEFFR     efficiency  = EEFF   / IEFF   (EEFF = NONIX - EAMINTAN,
                  IEFF = NIM + NONII; until 2026-09-30 this was NONIX /
                  revenue — amortization of intangibles left IN — which
                  missed FDIC's figure by >0.02pp on 924 of 4,294 banks)
        EEFFQR    single-quarter efficiency = EEFFQ / IEFFQ (was dropped
                  as "average-based" until 2026-09-30 — it never was)
        RBCRWAJ   total RBC   = RBC    / RWAJ
        RBC1RWAJ  tier 1 RBC  = RBCT1J / RWAJ   (RBCT1J = total Tier 1 $)
        IDT1CER   CET1 ratio  = RBCT1C / RWAJ   (RBCT1C = CET1 $; added
                  2026-09-22 — it had been dropped as "average-based", which
                  left every multi-charter bank's CET1 blank on Capital
                  Adequacy and in the screens; verified live on JPM/MS/BNY/
                  WTFC/QCRH charters that RBCT1C/RWAJ reproduces IDT1CER)
    plus every other ratio in _EXACT_QUOTIENTS (2026-09-25). Before that,
    every FDIC ratio NOT listed anywhere here fell through to the summing
    loop and rendered as the SUM of the charters' ratios (WFC loans/deposits
    233.3%). Each quotient there is the FDIC risview dictionary's own
    numerator/denominator, verified live to reproduce the reported ratio on
    23 banks x 2 quarters (6/30/2026, 3/31/2026) before it went in. A
    quotient is n/a unless EVERY charter reported both components — a sum
    over some charters is a partial numerator, not a group figure.
  * n/a — FDIC computes these against AVERAGE balances over the period, which
    period-end levels cannot reconstruct. Carrying the lead charter's figure
    would be a plausible-wrong number on a consolidated label, so they are
    dropped and flagged instead:
        ROA ROE NIMY NTLNLSR NOIJY ELNATRY NTRER NTCOMRER IDNTCIR ... and
        the single-quarter *Q variants (full list: AVERAGE_BASED_RATIOS)

A future pass can restore the averaged ratios by aggregating each charter's
HISTORY and forming 2-point averages, the way ui/financials_statements already
does for a single bank. That is deliberately not attempted here: it needs
per-charter history for all 16 of Wintrust's banks, and a wrong NIM is worse
than an absent one.

Single-charter banks (the other ~350) are untouched: get_cert_group returns
[cert] and aggregation is a no-op passthrough.
"""
from __future__ import annotations

# Fields FDIC computes against AVERAGE balances — unreconstructable from
# period-end levels, so they are dropped for a group rather than guessed.
# RBCT1JR NCLNLSR LNATRESR NPERFV were listed here until 2026-09-25, but the
# dictionary defines each as a quotient of period-end levels — they moved to
# _EXACT_QUOTIENTS. NOIJY ELNATRY NTRER NTCOMRER IDNTCIR (annualized flow ÷
# 5-point average balance) were being SUMMED; they belong here. EEFFQR was
# listed here until 2026-09-30 — it is a flow ÷ flow quotient (EEFFQ/IEFFQ).
AVERAGE_BASED_RATIOS = frozenset({
    "ROA", "ROE", "NIMY", "INTEXPY", "INTINCY",
    "NONIIAY", "NONIXAY", "ROAPTX", "NTLNLSR",
    "ROAQ", "ROEQ", "NIMYQ", "NTLNLSQR",
    "NOIJY", "ELNATRY", "NTRER", "NTCOMRER", "IDNTCIR",
})

# Ratios that ARE a quotient of summable components, so a group's ratio is
# exactly Σnumerator / Σdenominator × scale: {ratio: (num, den, scale)}.
# Formulas are the FDIC risview dictionary's (x-source-mapping), each
# verified live 2026-09-25 to reproduce the reported ratio (to its 2-dp
# rounding or exactly) on 23 banks x 2 quarters — JPM WFC BAC USB PNC TFC
# FITB KEY HBAN RF MTB CFG ZION OZK WAL FHN BOKF CFR EWBC CBSH TCBK BANR WTFC
# lead charters (the three RWA ratios: 12 of them, plus 17 charters on
# 2026-09-22). Then end-to-end: fetch_group_history for WFC (3 charters),
# BAC (2), BK (4) equals Σnum/Σden of the raw per-charter records to 1e-9 on
# every ratio. Components are period-end levels, except ELNANTR (YTD
# flows) and IDERNCVR (annualized YTD flows) — flow ÷ flow over the same
# period, so the sum-of-charters quotient is exact with no averaging.
_EXACT_QUOTIENTS = {
    # Efficiency (2026-09-30): the dictionary's own EEFF/IEFF — reproduces
    # EEFFR exactly on every institution at 12/31/2025, 3/31/2026, 6/30/2026
    # (~4,300 each, max diff 0.0) and on JPM/WFC 1990-2005. EEFF = NONIX -
    # EAMINTAN and IEFF = NIM + NONII to the $K on all of them; YTD flows,
    # so flow ÷ flow over the same period.
    "EEFFR": ("EEFF", "IEFF", 100),
    # Single-quarter efficiency (2026-09-30): EEFFQ/IEFFQ reproduces EEFFQR
    # — which FDIC stores rounded half-up to 2 dp — on every institution at
    # the same three quarters, and on JPM/WFC 1988-2005.
    "EEFFQR": ("EEFFQ", "IEFFQ", 100),
    "RBCRWAJ": ("RBC", "RWAJ", 100),        # total RBC ratio
    "RBC1RWAJ": ("RBCT1J", "RWAJ", 100),    # tier 1 RBC ratio
    "IDT1CER": ("RBCT1C", "RWAJ", 100),     # CET1 ratio (2026-09-22)
    "RBCT1JR": ("RBCT1J", "ASSET", 100),    # platform leverage ratio
    "RBC1AAJ": ("RBCT1", "AVASSETJ", 100),  # PCA leverage (quarter-avg assets
                                            # as REPORTED — a summable level)
    "LNLSDEPR": ("LNLSNET", "DEP", 100),
    "LNLSNTV": ("LNLSNET", "ASSET", 100),
    "IDLNCORR": ("LNLSNET", "COREDEP", 100),
    "DEPDASTR": ("DEPDOM", "ASSET", 100),   # DEP/ASSET misses by up to 14pp
    "ERNASTR": ("ERNAST", "ASSET", 100),
    "EQV": ("EQ", "ASSET", 100),            # EQ, not EQTOT (minority int.)
    "ASTEMPM": ("ASSET", "NUMEMP", 0.001),  # $K → $M per employee
    "NPERFV": ("NPERF", "ASSET", 100),
    "NCLNLSR": ("NCLNLS", "LNLSGRJ", 100),
    "LNATRESR": ("LNATRES", "LNLSGR", 100),
    "LNRESNCR": ("LNATRESJ", "NCLNLS", 100),  # reserve ÷ noncurrent loans
    "IDNCCIR": ("NCCI", "LNCI", 100),
    "IDNCCONR": ("NCCON", "LNCON", 100),
    "NCRER": ("NCRE", "LNREJ", 100),
    "NCRECONR": ("NCRECONS", "LNRECONS", 100),
    "NCRELOCR": ("NCRELOC", "LNRELOC", 100),
    "NCREMULR": ("NCREMULT", "LNREMULT", 100),
    "NCRENRER": ("NCRENRES", "LNRENRES", 100),
    "NCRERESR": ("NCRERES", "LNRERES", 100),
    "ELNANTR": ("ELNLOS", "NTTOT", 100),    # provision ÷ net charge-offs
    "IDERNCVR": ("CHFLA", "NTLNLSA", 1),    # earnings coverage of NCO (x)
}

# Identity/metadata — carried from the LEAD (largest) charter, never summed.
_IDENTITY = frozenset({"CERT", "REPNM", "REPDTE", "NAME", "STALP", "CITY",
                       "RSSDHCR", "FED_RSSD", "ID"})

_GROUP_TTL_S = 30 * 86400          # bank structure changes rarely

# ONE cache row holding {str(cert): [group certs largest-first]} for every
# cert under a holdco with 2+ active charters, built from the universe
# snapshot's full-institutions walk (data/bank_universe._fetch_fdic_banks).
# Why bulk: the per-cert path costs 2 FDIC API calls per bank, and inside the
# nightly's 8-worker burst those get 429-throttled — get_with_retry returns
# None, and the group silently degraded to the lead charter for EVERY bank
# (2026-08-04: a "successful" run wrote lead-charter data universe-wide).
# Absence from the map means single-charter; the API path is only a fallback
# for an environment where the map has never been built.
_GROUP_MAP_KEY = "cert_groups:v2"


# FORMER charters (2026-10-05): a holdco that folds subsidiary charters into
# one — TMP merged Bank of Castile, Mahopac and VIST into Tompkins Trust on
# 2022-01-01; CBC merged 12 charters on 2021-10-01 — or buys a bank, holds
# it as its own charter, then merges it (PNC/BBVA USA Jun→Oct 2021, USB/MUFG
# Union Dec 2022→May 2023) has no ACTIVE charter for that history, so the
# active-charter group silently dropped it: TMP FY2021 total assets showed
# $2.45B of the holdco's $7.82B. 48 such charters sat under 20 universe
# holdcos (ended 2021+). {str(active cert): {"hc": holdco RSSD, "certs":
# [inactive certs whose LAST high holder is that holdco], "ends": {cert:
# YYYYMMDD}}}, every active cert of the holdco keyed. Most banks absorbed
# SOMETHING since 1992 (820 active certs; WFC ~300 charters), so readers ask
# only for charters that ended inside their window — the live 20-quarter
# path touches the few recent ones, the deep store all of them once. Their
# records count only for quarters whose own
# RSSDHCR is the holdco (FDIC stamps the holder per REPDTE: BBVA reads
# 1391237 through Q1-21, PNC's 1069778 from Q2-21) — pre-acquisition history
# stays out. Built from the universe build's institutions walks, read
# cache-only.
_ABSORBED_MAP_KEY = "cert_absorbed:v1"


def build_absorbed_map(active: list[dict], inactive: list[dict]) -> dict[str, dict]:
    """{str(active cert): {"hc", "certs"}} for every active cert whose holdco
    (rssdhcr) is the last high holder of 1+ inactive charters. Rows need
    cert / rssdhcr (lowercase, as data/bank_universe builds them)."""
    def _ids(b):
        try:
            return int(b.get("rssdhcr") or 0), int(b.get("cert") or 0)
        except (TypeError, ValueError):
            return 0, 0
    by_hc: dict[int, dict[int, str]] = {}
    for b in inactive:
        hc, c = _ids(b)
        if hc and c:
            by_hc.setdefault(hc, {})[c] = _yyyymmdd(b.get("end"))
    out: dict[str, dict] = {}
    for b in active:
        hc, c = _ids(b)
        if hc and c and hc in by_hc:
            ends = by_hc[hc]
            out[str(c)] = {"hc": str(hc), "certs": sorted(ends),
                           "ends": {str(k): v for k, v in ends.items()}}
    return out


def _yyyymmdd(raw) -> str:
    """FDIC institutions dates are MM/DD/YYYY; '' when unparseable (such a
    charter is never window-filtered out — over-fetching beats dropping)."""
    s = str(raw or "").strip()
    if len(s) == 10 and s[2] == "/" and s[5] == "/":
        return s[6:] + s[:2] + s[3:5]
    return ""


def warm_absorbed_map(active: list[dict], inactive: list[dict]) -> int:
    """Build and persist the former-charter map; returns how many active
    certs carry one."""
    m = build_absorbed_map(active, inactive)
    from data import cache
    cache.put(_ABSORBED_MAP_KEY, m)
    return len(m)


def get_absorbed_charters(cert: int | None,
                          since: str | None = None) -> tuple[str | None, list[int]]:
    """(holdco RSSD, former charters) for an active cert, from the persisted
    map only — no network. `since` (YYYYMMDD) keeps only charters that ended
    on or after it: one that ended earlier has no record in a window that
    starts there. Charters cannot un-merge, so a stale map is still right
    about what it lists (read with no age limit)."""
    if not cert:
        return None, []
    from data import cache
    try:
        m = cache.get(_ABSORBED_MAP_KEY, max_age_s=None)
    except Exception:
        m = None
    rec = m.get(str(int(cert))) if isinstance(m, dict) else None
    if not rec:
        return None, []
    ends = rec.get("ends") or {}
    certs = [int(c) for c in rec.get("certs") or []
             if not since or not ends.get(str(c)) or ends[str(c)] >= since]
    return (str(rec.get("hc")), certs) if certs else (None, [])


def held_by(rec: dict, hc: str | None) -> bool:
    """True when a former charter's record was filed while `hc` held it."""
    return bool(hc) and str(rec.get("RSSDHCR") or "").strip() == hc


def get_cert_group(ticker: str, cert: int | None = None) -> list[int]:
    """Every ACTIVE FDIC cert under `ticker`'s holding company, largest first.

    Returns [cert] for a single-charter bank (the overwhelming majority) and
    [] when the ticker has no cert at all. Falls back to [cert] on any lookup
    failure — never fewer charters than we already had, so a bad FDIC day
    degrades to today's behaviour rather than losing the bank entirely.
    """
    from data.bank_mapping import get_fdic_cert
    if cert is None:
        try:
            cert = get_fdic_cert(ticker)
        except Exception:
            cert = None
    if not cert:
        return []
    cert = int(cert)

    from data import cache
    # Bulk map first: no API call, and a cert absent from the map IS the
    # single-charter answer (the map only lists multi-charter groups).
    try:
        m = cache.get(_GROUP_MAP_KEY, max_age_s=_GROUP_TTL_S)
    except Exception:
        m = None
    if isinstance(m, dict):
        g = m.get(str(cert))
        return [int(c) for c in g] if g else [cert]

    # No bulk map yet (fresh environment): legacy per-cert resolution.
    key = f"cert_group:v1:{cert}"
    try:
        hit = cache.get(key, max_age_s=_GROUP_TTL_S)
        if hit and isinstance(hit.get("certs"), list) and hit["certs"]:
            return [int(c) for c in hit["certs"]]
    except Exception:
        pass

    certs = _resolve_group(cert)
    if not certs:
        return [cert]
    try:
        cache.put(key, {"certs": certs})
    except Exception:
        pass
    return certs


def build_group_map(institutions: list[dict]) -> dict[str, list[int]]:
    """{str(cert): [group certs largest-first]} for every cert whose holdco
    (rssdhcr) has 2+ active charters. Single-charter certs are OMITTED —
    absence from the map means [cert]. Keys are strings because the map
    round-trips through JSON in the cache.

    `institutions` rows need cert / rssdhcr / asset (lowercase, as
    data/bank_universe._fetch_fdic_banks builds them). RSSDHCR of 0/None/""
    means no high holder."""
    by_hc: dict[int, list[dict]] = {}
    for b in institutions:
        try:
            hc = int(b.get("rssdhcr") or 0)
            cert = int(b.get("cert") or 0)
        except (TypeError, ValueError):
            continue
        if not hc or not cert:
            continue
        by_hc.setdefault(hc, []).append(b)
    out: dict[str, list[int]] = {}
    for members in by_hc.values():
        if len(members) < 2:
            continue
        certs = [int(mm["cert"]) for mm in
                 sorted(members, key=lambda mm: -(float(mm.get("asset") or 0)))]
        for c in certs:
            out[str(c)] = certs
    return out


def warm_group_map(institutions: list[dict]) -> int:
    """Build and persist the bulk group map. Returns the number of certs that
    belong to a multi-charter group. Called from the universe snapshot build,
    which already walked every ACTIVE institution — so this costs zero extra
    FDIC API calls."""
    m = build_group_map(institutions)
    from data import cache
    cache.put(_GROUP_MAP_KEY, m)
    return len(m)


def get_cert_group_cached(cert: int | None) -> list[int]:
    """The charter group from the persisted bulk map ONLY — no per-cert
    resolution, no network. A cert absent from the map (or no map yet) is
    its own single-charter group; None/0 is []. For render paths that must
    never block on FDIC (the growth-row acquisition flag)."""
    if not cert:
        return []
    cert = int(cert)
    from data import cache
    try:
        m = cache.get(_GROUP_MAP_KEY, max_age_s=None)
    except Exception:
        m = None
    if isinstance(m, dict):
        g = m.get(str(cert))
        if g:
            return [int(c) for c in g]
    return [cert]


def _resolve_group(cert: int) -> list[int]:
    """Live FDIC lookup: the cert's holdco RSSD, then every active cert under
    it, ordered by assets descending. [] on any failure (caller falls back)."""
    from data.http import get_with_retry
    from data.fdic_client import FDIC_INSTITUTIONS_URL

    try:
        r = get_with_retry(FDIC_INSTITUTIONS_URL, {
            "filters": f"CERT:{int(cert)}",
            "fields": "CERT,RSSDHCR,ASSET", "limit": 1, "format": "json",
        }, timeout=30)
        if r is None:
            # get_with_retry returns None ONLY when every attempt ate a 429.
            # This must be loud: the silent version of this line let a
            # rate-limited nightly degrade every bank to its lead charter
            # with nothing in the logs (2026-08-04).
            print(f"[cert_group] cert {cert}: institutions lookup "
                  f"rate-limited past retries — degrading to single cert")
            return []
        rows = r.json().get("data", [])
        if not rows:
            return []
        hc = (rows[0].get("data") or {}).get("RSSDHCR")
        if not hc:
            return [int(cert)]              # no holding company: itself only
        r2 = get_with_retry(FDIC_INSTITUTIONS_URL, {
            "filters": f"RSSDHCR:{int(hc)} AND ACTIVE:1",
            "fields": "CERT,ASSET", "limit": 100, "format": "json",
            "sort_by": "ASSET", "sort_order": "DESC",
        }, timeout=30)
        if r2 is None:
            print(f"[cert_group] cert {cert}: group lookup rate-limited "
                  f"past retries — degrading to single cert")
            return []
        out = []
        for d in r2.json().get("data", []):
            c = (d.get("data") or {}).get("CERT")
            if c is not None:
                out.append(int(c))
        if int(cert) not in out:            # mapped cert must always be present
            out.insert(0, int(cert))
        return out
    except Exception as e:
        print(f"[cert_group] resolve failed for cert {cert}: "
              f"{type(e).__name__}: {e}")
        return []


def aggregate_records(records: list[dict]) -> dict:
    """Consolidate one FDIC financials record per charter into one record.

    Levels are summed; average-based ratios are dropped (see module docstring);
    the exactly-recomputable ratios are rebuilt from the sums. Identity
    fields come from the LEAD charter — records must be largest-first.

    A single record passes through unchanged, so single-charter banks are
    bit-for-bit unaffected.
    """
    records = [r for r in records if r]
    if not records:
        return {}
    if len(records) == 1:
        return dict(records[0])

    lead = records[0]
    out: dict = {k: lead.get(k) for k in _IDENTITY if k in lead}

    keys: set = set()
    for r in records:
        keys.update(r.keys())

    for k in keys:
        if k in _IDENTITY or k in AVERAGE_BASED_RATIOS:
            continue
        total, seen = 0.0, False
        for r in records:
            v = r.get(k)
            if v is None:
                continue
            try:
                total += float(v)
                seen = True
            except (TypeError, ValueError):
                seen = False
                break
        out[k] = total if seen else None

    # Explicitly n/a rather than absent, so a consumer reading the key gets
    # None (render n/a) instead of a KeyError or a stale lead-charter value.
    for k in AVERAGE_BASED_RATIOS:
        if k in keys:
            out[k] = None

    # A charter that did not report a component (a cache/deep-store row
    # written before the field was fetched) leaves a PARTIAL sum above, and a
    # partial numerator over a full denominator is a plausible-wrong ratio.
    incomplete = {k for k in keys
                  if any(_num(r.get(k)) is None for r in records)}
    _recompute_exact_ratios(out, incomplete)
    out["_charter_count"] = len(records)
    out["_aggregated"] = True
    return out


def fetch_group_history(ticker: str, limit: int = 20,
                        cert: int | None = None) -> list[dict]:
    """`limit` quarters of FDIC financials for the ticker's WHOLE banking
    operation, newest first — one consolidated record per REPDTE.

    This is the single seam the wiring goes through. Every producer of the
    `fdic_hist:{ticker}` cache entry calls it, so every consumer (the metrics
    build, the statement/credit/capital/deposit/rate tabs, the trends grid)
    becomes correct without touching a line of their code.

    Aggregating the HISTORY rather than one record is what keeps the averaged
    ratios alive: downstream code already derives ROA/NIM/ROATCE from levels
    plus a prior quarter (ui/financials_statements._avg,
    analysis.valuation.compute_roatce_4q), so with a correct per-quarter series
    those come out right for a group too. Only the FDIC-REPORTED ratio columns
    are dropped, because those specific numbers are the lead charter's.

    Quarters are aggregated over whatever charters reported them. A bank that
    acquired a charter mid-history therefore steps up at the acquisition — that
    IS what happened, and each record carries _charter_count so a consumer can
    see the composition.

    Single-charter banks take the same path as before (one cert, no
    aggregation), so the ~350 of them are unaffected.
    """
    from data import fdic_client

    certs = get_cert_group(ticker, cert=cert)
    if not certs:
        return []
    from datetime import date, timedelta
    window = (date.today() - timedelta(days=92 * (limit + 1))).strftime("%Y%m%d")
    hc, former = get_absorbed_charters(certs[0], since=window)
    if len(certs) == 1 and not former:
        df = fdic_client.fetch_financials(certs[0], limit=limit)
        return [] if df is None or df.empty else df.to_dict("records")

    per_cert: dict[int, list[dict]] = {}
    for attempt in (1, 2):                    # one retry for failed charters
        for c in certs + former:
            if c in per_cert:
                continue
            try:
                df = fdic_client.fetch_financials(c, limit=limit)
            except Exception as e:
                print(f"[cert_group] {ticker}: cert {c} history failed: "
                      f"{type(e).__name__}: {e}")
                continue
            if df is not None and not df.empty:
                per_cert[c] = df.to_dict("records")
    missing = [c for c in certs + former if c not in per_cert]
    if missing:
        # An ACTIVE member always has recent filings, and a former charter
        # absorbed inside the window filed while held: an empty result is a
        # failed fetch, not "no history". Summing the rest presented 15 of
        # WTFC's 16 charters as the group (equity 6.93B vs 7.49B, REVIEW
        # 2026-10-05 P0-4) — no group history beats a short one; callers keep
        # their last good copy.
        print(f"[cert_group] {ticker}: charters {missing} returned no history "
              f"— group history withheld (never a partial sum)")
        return []
    held = {c: [r for r in per_cert.pop(c) if held_by(r, hc)] for c in former}
    return _aggregate_complete_periods(per_cert, held)[:limit]


def _aggregate_complete_periods(per_cert: dict[int, list[dict]],
                                former: dict[int, list[dict]] | None = None
                                ) -> list[dict]:
    """One consolidated record per REPDTE, newest first — only for periods
    EVERY active member covers. A member legitimately lacks periods before it
    joined the group (older than its first record: pro forma, captioned); a
    member missing a period inside or after its own history (a gap, or a
    member whose stored rows weren't refreshed) makes that period incomplete,
    and an incomplete period is omitted, never summed short. `former` (held
    records of merged-away charters) adds to its periods but never gates one:
    its filings END at the merger."""
    spans = {}
    by_period: dict[str, list[dict]] = {}
    for c, recs in per_cert.items():
        periods = {str(r.get("REPDTE") or "") for r in recs} - {""}
        if not periods:
            continue
        spans[c] = (min(periods), periods)
    for c, recs in list(per_cert.items()) + list((former or {}).items()):
        for r in recs:
            p = str(r.get("REPDTE") or "")
            if p:
                by_period.setdefault(p, []).append(r)
    out = []
    for period in sorted(by_period, reverse=True):
        if any(period >= first and period not in have
               for first, have in spans.values()):
            continue
        recs = sorted(by_period[period],
                      key=lambda r: -(float(r.get("ASSET") or 0)))
        out.append(aggregate_records(recs))
    return out


def _num(v) -> float | None:
    """float(v), or None for absent / NaN / non-numeric."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _recompute_exact_ratios(out: dict, incomplete: frozenset | set = frozenset()) -> None:
    """Rebuild the ratios that ARE a pure quotient of summed components.
    `incomplete` names components some charter did not report."""
    def _n(k):
        return None if k in incomplete else _num(out.get(k))

    # Every branch assigns: the summing loop in aggregate_records has already
    # ADDED the charters' reported ratios into these keys (a ratio is not in
    # AVERAGE_BASED_RATIOS), so leaving one untouched would ship a sum of
    # percentages as the group's ratio. n/a when a component is absent, and
    # when the denominator is not positive (FDIC's own quotient would be
    # meaningless or undefined there, e.g. net recoveries under ELNANTR).
    for ratio, (num_k, den_k, scale) in _EXACT_QUOTIENTS.items():
        num, den = _n(num_k), _n(den_k)
        out[ratio] = (num / den * scale) if (
            num is not None and den is not None and den > 0) else None
