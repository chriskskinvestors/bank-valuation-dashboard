"""Agent-dataset export: Company-Reported statements + the bank's own release
figures, read CACHE-ONLY from the seams the nightly jobs warm.

Two tables (shared export contract: ``build_<table>(tickers) -> DataFrame`` and
``COLUMNS_<TABLE>``; frame columns == dict keys, same order; misses attached as
``df.attrs["misses"]`` {reason: count} + ``df.attrs["miss_tickers"]``):

* ``statements_as_reported`` — the Financials tab's Company-Reported income
  statement + balance sheet (data.sec_statements), LONG: one row per (ticker,
  statement, period_type, period, line). Quarterly = the discrete-quarter
  10-Q/10-K stitch (``as_reported_statement_multiquarter``), annual = the
  5-fiscal-year 10-K stitch (``as_reported_statement_multiyear``). Labels are
  the bank's own, verbatim; values are the stitched result's own values (the
  seam already scales "$ in thousands/millions" onto monetary rows only — raw
  dollars; per-share in $; share counts raw).

* ``company_reported`` — the bank's OWN figures from its earnings releases,
  LONG: one row per (ticker, quarter_end, metric, extractor). Sources are the
  persisted envelopes only — release_metrics.cached_release_metrics (SEC
  filers' latest 8-K release), otc_release.otc_release_metrics(allow_fetch=
  False) (wire / IR-site releases), the sec_earnings_8k headline payload, and
  otc_release.get_eps_history (per-quarter diluted EPS history). NEVER
  release_metrics() itself (in prod a no-key AI state locks retries).

READ-ONLY. Nothing here fetches SEC/FMP/wire/Anthropic or writes the cache:

* The as-reported results are cached under the bank's LATEST filing accession,
  which the seam learns from the live submissions index (in-memory only, never
  persisted). So the cached results themselves are the index: one scan of the
  ``asreported_m*`` keys reads each entry's leading ``{"meta": {...}}`` (the
  filing that keyed it: accession / date / form / cik), and per (cik,
  statement, period_type) the newest cached filing wins. A bank whose newest
  filing has not been warmed yet exports its previous cached stitch — the
  ``source_*`` columns say which filing that is.
* Cache-key versions are read from the seams' own source (``_seam_version``),
  never spelled here, so a version bump can't silently deaden this reader; a
  refactor that moves the key literal raises instead of reading nothing.

Absent stays absent: a blank stitched cell, a metric the extractor refused, or
a bank with nothing cached produces NO value row (or value=None with a status),
never a 0.
"""
from __future__ import annotations

import calendar
import inspect
import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

import pandas as pd

# ── Column contracts ─────────────────────────────────────────────────────────

COLUMNS_STATEMENTS_AS_REPORTED: dict[str, str] = {
    "ticker": "Bank ticker (upper case).",
    "cik": "SEC CIK of the filer whose statements these are (int).",
    "statement": "'income' (income statement) or 'balance' (balance sheet).",
    "period_type": "'quarter' (discrete single quarter for income — Q1-Q3 the "
                   "10-Q's three-month column, Q4 = 10-K FY minus the nine-month "
                   "10-Q; quarter-end snapshot for balance) or 'annual' (fiscal "
                   "year from the 10-Ks).",
    "period_end": "Period end (datetime.date). Annual: the filing's own column "
                  "date. Quarter: the calendar quarter-end of the dashboard's "
                  "column label (e.g. Q2'26 -> 2026-06-30).",
    "period_label": "The dashboard's column label: 'Q2\\'26' (quarter) or "
                    "'FY2025' (annual).",
    "line_order": "0-based position of the line in the stitched statement "
                  "(section headers included), i.e. the bank's own line order.",
    "line_label": "The bank's own line label, verbatim from the filing's R-file "
                  "(newest filing's wording); never re-mapped.",
    "section": "Label of the nearest section-header line above this line in the "
               "bank's statement, or None.",
    "xbrl_element": "XBRL element id the line reports (e.g. us-gaap_Goodwill), "
                    "or None when the R-file carries none.",
    "unit": "'USD' (raw dollars) | 'USD/share' | 'shares' (raw count) | 'other' "
            "(non-monetary XBRL type, value as printed, unscaled).",
    "value": "The value the Company-Reported tab displays for this line and "
             "period, in `unit` (float). Rows exist only for reported cells.",
    "restated": "True when a later filing's value differs from the one "
                "originally filed (the dashboard's * cell).",
    "as_originally_reported": "Value as originally filed when restated, else "
                              "None (same unit).",
    "originally_reported_in": "Filing that originally reported it, e.g. "
                              "'10-Q filed 2025-08-05', when restated, else None.",
    "legacy_registrant": "Pre-merger registrant whose own filings supplied this "
                         "column (dashboard ‡ column), else None.",
    "period_note": "Column-level caveat from the stitch (e.g. a recast column), "
                   "else None.",
    "source_form": "Form of the newest filing anchoring the stitch (10-Q/10-K).",
    "source_accession": "Accession (no dashes) of that newest filing. Older "
                        "periods may come from older filings: see "
                        "stitched_accessions.",
    "source_filed": "Filing date of that newest filing (datetime.date).",
    "source_url": "EDGAR URL of that newest filing's primary document.",
    "stitched_accessions": "';'-joined accessions of every filing the stitch "
                           "read, newest first.",
}

COLUMNS_COMPANY_REPORTED: dict[str, str] = {
    "ticker": "Bank ticker (upper case).",
    "cik": "SEC CIK used for the SEC-keyed caches (int), or None (non-SEC bank).",
    "quarter_end": "Calendar quarter-end the figure is for (datetime.date).",
    "metric": "The extractor's own metric key (e.g. tbv_ps, bv_ps, eps_diluted, "
              "eps_adj, nim, efficiency, roa, roe, rotce, nco_ratio, npa_assets, "
              "acl_loans, cet1_ratio, t1_ratio, total_ratio, lev_ratio, "
              "tce_ratio, total_revenue, ni_common; earnings_8k: total_assets, "
              "total_deposits, net_income, net_interest_income, diluted_eps, "
              "nim, roaa, roae).",
    "value": "The bank's own stated figure in `unit` (float); None only on a "
             "status other than 'ok'.",
    "unit": "'percent' (3.42 = 3.42%) | 'USD/share' | 'USD' (raw dollars) | "
            "'shares' (raw count).",
    "period_role": "'current' (the release's own quarter) | 'prior_quarter' / "
                   "'year_ago' (the same release's comparative columns) | "
                   "'history' (otc_eps_history quarter record).",
    "extractor": "'release_metrics' (SEC 8-K release) | 'otc_release' (wire / "
                 "IR-site release) | 'earnings_8k' (8-K EX-99.1 headline "
                 "figures, preliminary / as-released) | 'otc_eps_history'.",
    "status": "'ok' | 'refused_basis_split' (release table vs headline "
              "disagree — no provable value) | 'conflict' (two releases "
              "disagree on the quarter — refused).",
    "release_date": "Release / 8-K filing date (datetime.date), or None.",
    "source_accession": "8-K accession, or None for a wire/IR release.",
    "source_url": "URL of the release document.",
    "detail": "Extractor provenance detail (ai_state, transport, via, anchor), "
              "or None. release_metrics values may include guarded-AI fills "
              "(verbatim-evidence verified); per-value provenance is not stored.",
}

# Quarterly windows the dashboard reads: the Company-Reported tab renders 12
# quarters (ui/cr_statements), the nightly warm + the CR highlights engine use 8
# (jobs/refresh_cr_quarterly, ui/financials_statements). Same stitch, same
# filings — only the number of columns differs.
_MQ_WINDOWS = (12, 8)
_MY_YEARS = 5
_STYPES = ("income", "balance")
# as_reported_statement_multiyear's freshness gate (deregistered filers' last
# 10-K is years old): the dashboard renders n/a past this age, so do we.
_MY_MAX_AGE_DAYS = 540

_KIND_UNIT = {"monetary": "USD", "pershare": "USD/share", "shares": "shares",
              "other": "other"}
_SPEC_UNIT = {"%": "percent", "%r": "percent", "$": "USD/share",
              "$s": "USD/share", "$K": "USD"}     # "#" (scale unknown) excluded
_ROLES = (("metrics", "qend", "current"),
          ("prior_metrics", "prior_qend", "prior_quarter"),
          ("yoy_metrics", "yoy_qend", "year_ago"))


# ── Shared helpers ───────────────────────────────────────────────────────────

def _seam_version(fn, prefix: str) -> str:
    """The cache-key version (e.g. 'v10') a seam spells for `prefix`, read from
    the seam's own source so this reader follows every bump. Exactly one
    distinct version must appear; anything else is a refactor this reader
    must be updated for — raise, never read a wrong/dead key."""
    found = set(re.findall(r'f"' + re.escape(prefix) + r':(v\d+):',
                           inspect.getsource(fn)))
    if len(found) != 1:
        raise RuntimeError(f"cache-key version for {prefix!r} not found "
                           f"unambiguously in {fn.__qualname__}: {found}")
    return found.pop()


def _cik_for(ticker: str) -> int | None:
    """The CIK the Company-Reported tab uses (bank_mapping.get_bank_info's
    tiers: static map, curated JSON, universe snapshot, runtime cache) WITHOUT
    get_bank_info's last-resort live SEC/FDIC resolution — read-only."""
    from data import bank_mapping as bm
    t = ticker.upper()
    info = (bm.BANK_MAP.get(t) or bm._RESOLVED_FROM_JSON.get(t)
            or bm._universe_snapshot_map().get(t) or bm._RESOLVED_CACHE.get(t))
    try:
        cik = int((info or {}).get("cik") or 0)
    except (TypeError, ValueError):
        return None
    return cik or None


def _iso_date(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _num(v) -> float | None:
    """A finite float, or None — bools / strings / NaN are not values."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f == f and abs(f) != float("inf") else None


def _frame(rows: list[dict], columns: dict, misses: dict) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(columns))
    df.attrs["misses"] = {k: len(v) for k, v in sorted(misses.items())}
    df.attrs["miss_tickers"] = {k: sorted(v) for k, v in sorted(misses.items())}
    return df


# ── statements_as_reported ───────────────────────────────────────────────────

_META_HEAD = re.compile(r'^\{"meta":\s*(\{[^{}]*\})')
_Q_LABEL = re.compile(r"^Q([1-4])'(\d{2})$")
_PERIOD_DATE = re.compile(r"([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2}),\s+(\d{4})")
_MON = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}


def _statement_keys() -> dict:
    """{(prefix, version)} for the two statement seams, read from their source."""
    from data import sec_statements as ss
    return {"quarter": ("asreported_mq", _seam_version(
                ss.as_reported_statement_multiquarter, "asreported_mq")),
            "annual": ("asreported_my", _seam_version(
                ss.as_reported_statement_multiyear, "asreported_my"))}


def _scan_statement_index(keys: dict) -> dict:
    """{(cik, period_type, stype): [(filed, accession, window, key), …]} from
    ONE scan of the cache's as-reported keys, reading only each value's
    leading meta (the filing that keyed it). A head that doesn't parse falls
    back to a full read of that entry (correct, just slower)."""
    from sqlalchemy import text
    from data import cache
    want = {}
    for ptype, (prefix, ver) in keys.items():
        windows = _MQ_WINDOWS if ptype == "quarter" else (_MY_YEARS,)
        for stype in _STYPES:
            for n in windows:
                want[(prefix, ver, stype, str(n))] = (ptype, stype, n)
    with cache._get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT key, substr(value, 1, 600) FROM cache "
                 "WHERE key LIKE :p"), {"p": "asreported_m%"}).fetchall()
    index: dict = defaultdict(list)
    for key, head in rows:
        parts = key.split(":")
        if len(parts) != 5:
            continue
        hit = want.get((parts[0], parts[1], parts[2], parts[4]))
        if hit is None:
            continue
        ptype, stype, n = hit
        meta = None
        m = _META_HEAD.match(head or "")
        if m:
            try:
                meta = json.loads(m.group(1))
            except ValueError:
                meta = None
        if meta is None:
            meta = (cache.get(key, max_age_s=None) or {}).get("meta")
        if not isinstance(meta, dict) or not meta.get("cik"):
            continue
        try:
            cik = int(meta["cik"])
        except (TypeError, ValueError):
            continue
        index[(cik, ptype, stype)].append(
            (str(meta.get("date") or ""), parts[3], n, key))
    return index


def _period_end(ptype: str, period: str) -> tuple[date | None, str]:
    """(period_end, dashboard label) for one stitched column."""
    if ptype == "quarter":
        m = _Q_LABEL.match(period or "")
        if not m:
            return None, period
        y, mo = 2000 + int(m.group(2)), 3 * int(m.group(1))
        return date(y, mo, calendar.monthrange(y, mo)[1]), period
    m = _PERIOD_DATE.search(period or "")
    if not m or m.group(1).lower() not in _MON:
        yr = re.search(r"\d{4}", period or "")
        return None, f"FY{yr.group()}" if yr else period
    y, mo, d = int(m.group(3)), _MON[m.group(1).lower()], int(m.group(2))
    try:
        return date(y, mo, d), f"FY{y}"
    except ValueError:
        return None, f"FY{y}"


def _statement_rows(ticker: str, cik: int, stype: str, ptype: str,
                    res: dict) -> tuple[list[dict], int]:
    """Long rows for one cached stitched result; also the count of data cells
    skipped for an unknown value kind (never guessed into a unit)."""
    stmt = res.get("statement") or {}
    meta = res.get("meta") or {}
    periods = list(stmt.get("periods") or [])
    n = len(periods)
    ents = (list(stmt.get("period_entity") or []) + [None] * n)[:n]
    pnotes = (list(stmt.get("period_notes") or []) + [None] * n)[:n]
    cols = [_period_end(ptype, p) for p in periods]
    accs = list(dict.fromkeys(f.get("accession") for f in res.get("filings") or []
                              if f.get("accession")))
    src_url = (f"https://www.sec.gov/Archives/edgar/data/{int(meta['cik'])}/"
               f"{meta['accession']}/{meta['doc']}"
               if meta.get("cik") and meta.get("accession") and meta.get("doc")
               else None)
    base = {"ticker": ticker, "cik": cik, "statement": stype, "period_type": ptype,
            "source_form": meta.get("form"), "source_accession": meta.get("accession"),
            "source_filed": _iso_date(meta.get("date")), "source_url": src_url,
            "stitched_accessions": ";".join(accs) or None}
    out, unknown, section = [], 0, None
    for order, r in enumerate(stmt.get("rows") or []):
        if r.get("header"):
            section = r.get("label") or None
            continue
        unit = _KIND_UNIT.get(r.get("kind"))
        vals = list(r.get("values") or [])
        notes = (list(r.get("restated") or []) + [None] * n)[:n]
        for i, (pend, plabel) in enumerate(cols):
            v = _num(vals[i]) if i < len(vals) else None
            if v is None:
                continue                      # blank on the dashboard: absent
            if unit is None:
                unknown += 1
                continue
            note = notes[i] if isinstance(notes[i], dict) else None
            out.append({**base, "period_end": pend, "period_label": plabel,
                        "line_order": order, "line_label": r.get("label"),
                        "section": section,
                        "xbrl_element": r.get("element_id") or None,
                        "unit": unit, "value": v,
                        "restated": note is not None,
                        "as_originally_reported": _num((note or {}).get("original")),
                        "originally_reported_in":
                            (note or {}).get("original_source") or None,
                        "legacy_registrant": ents[i] or None,
                        "period_note": pnotes[i] or None})
    return out, unknown


def build_statements_as_reported(tickers: list[str],
                                 today: date | None = None) -> pd.DataFrame:
    """Company-Reported income + balance statements (quarterly + annual), long,
    cache-only. Misses per (statement, period_type): no_cik, not_cached,
    stale_filer (annual: newest 10-K older than the dashboard's 540-day gate),
    unknown_kind (cells skipped for an unrecognized value kind)."""
    from data import cache
    keys = _statement_keys()
    index = _scan_statement_index(keys)
    cutoff = ((today or date.today()) - timedelta(days=_MY_MAX_AGE_DAYS)).isoformat()
    rows: list[dict] = []
    misses: dict = defaultdict(set)
    for raw in tickers:
        ticker = str(raw).upper()
        cik = _cik_for(ticker)
        if cik is None:
            misses["no_cik"].add(ticker)
            continue
        for ptype in ("quarter", "annual"):
            for stype in _STYPES:
                tag = f"{stype}_{ptype}"
                cands = index.get((cik, ptype, stype))
                if not cands:
                    misses[f"not_cached:{tag}"].add(ticker)
                    continue
                filed, _acc, _n, key = max(cands)  # newest filing; 12q over 8q
                if ptype == "annual" and filed < cutoff:
                    misses[f"stale_filer:{tag}"].add(ticker)
                    continue
                res = cache.get(key, max_age_s=None)
                if not res or not (res.get("statement") or {}).get("rows"):
                    misses[f"not_cached:{tag}"].add(ticker)
                    continue
                got, unknown = _statement_rows(ticker, cik, stype, ptype, res)
                if unknown:
                    misses[f"unknown_kind:{tag}"].add(ticker)
                rows.extend(got)
    return _frame(rows, COLUMNS_STATEMENTS_AS_REPORTED, misses)


# ── company_reported ─────────────────────────────────────────────────────────

def _release_units() -> dict:
    """metric -> unit for the release extractors, from the extractor's OWN
    spec kinds ('%' percent, '$' per share, '$K' raw dollars post-scale). The
    '#' kind (dil_shares: the raw PRINTED count, scale unknown) is excluded —
    exporting it would ship a possibly 1000x-off number."""
    from data import release_metrics as rm
    kinds = {k: s[1] for k, s in {**rm._TABLE_SPECS, **rm._ADJ_TABLE_SPECS}.items()}
    kinds["rev_sum"] = rm._REV_TABLE_SPECS["nii"][1]   # nii + nonii, both $K
    units = {k: _SPEC_UNIT[v] for k, v in kinds.items() if v in _SPEC_UNIT}
    # otc_release: common shares at qend, served only when BV/share x shares
    # reproduces the release's own equity (raw count, scale proven).
    units["shares_outstanding"] = "shares"
    return units


def _qend(s) -> date | None:
    d = _iso_date(s)
    return d if d and (d.month, d.day) in ((3, 31), (6, 30), (9, 30), (12, 31)) else None


def _envelope_rows(ticker, cik, env: dict, extractor: str, units: dict,
                   unknown: set) -> list[dict]:
    """Rows for one release_metrics / otc_release envelope: current, prior-
    quarter and year-ago buckets (+ current capital), non-None values only,
    plus refused rows for the basis-split keys."""
    if extractor == "release_metrics":
        detail = f"ai_state={env.get('ai_state')}" if env.get("ai_state") else None
    else:
        detail = (f"transport={env.get('transport')}"
                  if env.get("transport") else None)
    base = {"ticker": ticker, "cik": cik, "extractor": extractor,
            "release_date": _iso_date(env.get("filed_date")),
            "source_accession": env.get("accession") or None,
            "source_url": env.get("url") or None, "detail": detail}
    split = set(env.get("basis_split") or ())
    out = []
    for bucket, qkey, role in _ROLES:
        qe = _qend(env.get(qkey))
        if qe is None:
            continue
        vals = dict(env.get(bucket) or {})
        if role == "current":
            for k, v in (env.get("capital") or {}).items():
                if vals.get(k) is None:
                    vals[k] = v
            if env.get("shares_outstanding") is not None:
                vals["shares_outstanding"] = env.get("shares_outstanding")
        for k in sorted(vals):
            v = _num(vals[k])
            refused = role == "current" and k in split and v is None
            if v is None and not refused:
                continue
            if k not in units:
                unknown.add(k)
                continue
            out.append({**base, "quarter_end": qe, "metric": k, "value": v,
                        "unit": units[k], "period_role": role,
                        "status": "refused_basis_split" if refused else "ok"})
    return out


def _earnings_8k_rows(ticker, cik, version: str, unknown: set) -> list[dict]:
    """The 8-K EX-99.1 headline payload for the bank's latest earnings 8-K, via
    the persisted submissions record (read at any age) — no fetch."""
    from data import cache
    from data import sec_earnings_8k as e8
    rec = cache.get(e8._submissions_ckey(cik), max_age_s=None) or {}
    f8k = rec.get("f8k") or {}
    if not f8k.get("accession"):
        return []
    payload = cache.get(f"earnings_8k:{version}:{f8k['accession']}",
                        max_age_s=None) or {}
    figs = payload.get("figures") or {}
    qe = e8._release_quarter_end(payload.get("filed") or "")
    if not figs or qe is None:
        return []
    units = {**{k: "USD" for k in e8._DOLLAR_BS + e8._DOLLAR_FLOW},
             **{k: "percent" for k in e8._RATIO}, "diluted_eps": "USD/share"}
    y, m = qe
    acc = payload.get("accession") or ""
    doc = payload.get("doc") or ""
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{acc.replace('-', '')}/{doc}") if acc and doc else None
    out = []
    for k in sorted(figs):
        v = _num(figs[k])
        if v is None:
            continue
        if k not in units:
            unknown.add(k)
            continue
        out.append({"ticker": ticker, "cik": cik,
                    "quarter_end": date(y, m, calendar.monthrange(y, m)[1]),
                    "metric": k, "value": v, "unit": units[k],
                    "period_role": "current", "extractor": "earnings_8k",
                    "status": "ok", "release_date": _iso_date(payload.get("filed")),
                    "source_accession": acc.replace("-", "") or None,
                    "source_url": url,
                    "detail": (f"anchored_on_periodic={payload['period']}"
                               if payload.get("period") else None)})
    return out


def _eps_history_rows(ticker, cik) -> list[dict]:
    from data.otc_release import get_eps_history
    out = []
    for q, rec in sorted(((get_eps_history(ticker) or {}).get("quarters")
                          or {}).items()):
        qe = _qend(q)
        if qe is None or not isinstance(rec, dict):
            continue
        conflict = bool(rec.get("conflict"))
        v = None if conflict else _num(rec.get("eps_diluted"))
        if v is None and not conflict:
            continue
        out.append({"ticker": ticker, "cik": cik, "quarter_end": qe,
                    "metric": "eps_diluted", "value": v, "unit": "USD/share",
                    "period_role": "history", "extractor": "otc_eps_history",
                    "status": "conflict" if conflict else "ok",
                    "release_date": None, "source_accession": None,
                    "source_url": rec.get("url") or None,
                    "detail": f"via={rec.get('via')}" if rec.get("via") else None})
    return out


def build_company_reported(tickers: list[str]) -> pd.DataFrame:
    """The bank's own release figures, long, cache-only. Misses: no_release_data
    (nothing cached from any extractor), call_notice (the stored release is a
    call/webcast notice, not results — skipped), unknown_metric:<key> (a
    stored key with no proven unit — skipped, never guessed)."""
    from data import release_metrics as rm
    from data import sec_earnings_8k as e8
    from data.otc_release import otc_release_metrics
    units = _release_units()
    e8_ver = _seam_version(e8.latest_earnings_8k_figures, "earnings_8k")
    rows: list[dict] = []
    misses: dict = defaultdict(set)
    for raw in tickers:
        ticker = str(raw).upper()
        cik = _cik_for(ticker)
        unknown: set = set()
        got: list[dict] = []
        envs = []
        if cik is not None:
            envs.append(("release_metrics", rm.cached_release_metrics(cik)))
        envs.append(("otc_release", otc_release_metrics(ticker, allow_fetch=False)))
        for extractor, env in envs:
            if not env:
                continue
            if env.get("call_notice"):
                misses[f"call_notice:{extractor}"].add(ticker)
                continue
            got += _envelope_rows(ticker, cik, env, extractor, units, unknown)
        if cik is not None:
            got += _earnings_8k_rows(ticker, cik, e8_ver, unknown)
        got += _eps_history_rows(ticker, cik)
        for k in unknown:
            misses[f"unknown_metric:{k}"].add(ticker)
        if not got:
            misses["no_release_data"].add(ticker)
        rows.extend(got)
    return _frame(rows, COLUMNS_COMPANY_REPORTED, misses)
