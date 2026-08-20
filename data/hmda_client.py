"""
CFPB/FFIEC HMDA public-API client — residential-mortgage origination activity.

Powers the planned "HMDA Mortgages" sub-tab: annual origination counts and
dollar volume for one lender, plus a by-state / by-county breakdown for the
latest year. All endpoints are public and keyless.

Endpoints (verified live 2026-08-20 against ffiec.cfpb.gov/documentation):
  Reporter Panel (RSSD → LEI):
    https://files.ffiec.cfpb.gov/static-data/snapshot/{year}/{year}_public_panel_csv.zip
    CSV columns include ``lei`` and ``respondent_rssd`` (the bank-subsidiary
    RSSD, i.e. what data.fdic_client.get_rssd_for_cert returns). The public
    institutions API blanks its rssd field (-1 sentinel), so the panel file
    is the only public RSSD↔LEI join. Panels publish ~18 months behind
    (2023 is the newest as of Aug 2026); years are merged newest-wins.
  Aggregations (per-year totals):
    https://ffiec.cfpb.gov/v2/data-browser-api/view/aggregations
      ?leis={lei}&years={year}&actions_taken=1
    Geography params are filters only — the API never groups by state/county,
    and multiple years collapse into one aggregate, hence one request/year.
  Loan-level CSV (geographic breakdown):
    https://ffiec.cfpb.gov/v2/data-browser-api/view/csv
      ?leis={lei}&years={year}&actions_taken=1
    One request; grouped client-side by state_code / county_code.

Units contract: HMDA 2018+ ``loan_amount`` (and the aggregation ``sum``) is
RAW DOLLARS — no conversion at this boundary (unlike FDIC $thousands).
Verified: WaFd 2023 aggregation sum 1,101,120,000 == Σ loan_amount over its
1,950 loan-level rows (avg $565K/loan), and nationwide 2023 count 5,710,399
matches CFPB's published "5.7 million" originations. Caveat: public HMDA
rounds each loan_amount to the midpoint of a $10k bucket for privacy, so
counts are exact but dollar volumes are approximate by design.

Cardinal rule: a bank that never filed HMDA resolves to None (n/a) — never
an error, never a zero. Fetch failures return None/{} and are never cached;
a genuine count-0 year is real data (omitted from results, cacheable).

Functions:
  resolve_lei(rssd)                       — HMDA LEI, or None if never filed
  originations_by_year(lei, years)        — {year: {count, volume_usd}}
  latest_breakdown(lei, year, by="state") — [{state[, county], count,
                                             volume_usd}] or None on failure
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime

HMDA_AGG_URL = "https://ffiec.cfpb.gov/v2/data-browser-api/view/aggregations"
HMDA_CSV_URL = "https://ffiec.cfpb.gov/v2/data-browser-api/view/csv"
PANEL_URL_TPL = ("https://files.ffiec.cfpb.gov/static-data/snapshot/"
                 "{year}/{year}_public_panel_csv.zip")

# LEI-keyed HMDA reporting began with the 2018 collection year; earlier
# years use a different respondent-ID scheme the data browser doesn't serve.
FIRST_HMDA_YEAR = 2018

ACTION_ORIGINATED = "1"          # HMDA action_taken=1: "Loan originated"
CACHE_TTL_SECONDS = 7 * 86400


# Shared freshness check (data/freshness) bound to this module's TTL.
def _is_fresh(cached: dict | None) -> bool:
    from data.freshness import is_fresh
    return is_fresh(cached, CACHE_TTL_SECONDS)


# ──────────────────────────────────────────────────────────────────────────
# Reporter Panel — RSSD → LEI
# ──────────────────────────────────────────────────────────────────────────

def _parse_panel_zip(content: bytes) -> dict[str, str]:
    """{respondent_rssd: lei} from one panel zip (rows without a positive
    RSSD are skipped — credit unions/independent mortgage cos carry 0/-1)."""
    out: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        with z.open(z.namelist()[0]) as f:
            rdr = csv.reader(io.TextIOWrapper(f, encoding="utf-8",
                                              errors="replace"))
            header = next(rdr, None) or []
            idx = {c.strip().lower(): i for i, c in enumerate(header)}
            i_rssd, i_lei = idx.get("respondent_rssd"), idx.get("lei")
            if i_rssd is None or i_lei is None:
                raise ValueError("panel csv missing respondent_rssd/lei")
            for row in rdr:
                if len(row) <= max(i_rssd, i_lei):
                    continue
                rssd, lei = row[i_rssd].strip(), row[i_lei].strip()
                if lei and rssd.isdigit() and int(rssd) > 0:
                    out[rssd] = lei
    return out


def _panel_lei_map() -> dict[str, str] | None:
    """{respondent_rssd(str): lei} merged across every published panel year,
    newest year winning (LEIs can change across charter events).

    Cached 7 days under ``hmda:panel_map:v1`` — but ONLY when every year in
    range either loaded or 404'd (not yet published). A transient failure
    yields an uncached partial map: positive hits are still trustworthy,
    while a miss must not be frozen into a false "never filed".
    Returns None only when nothing at all could be built.
    """
    from data import cache

    key = "hmda:panel_map:v1"
    # Freshness judged by _is_fresh below (7d design TTL) — no 24h read ceiling.
    cached = cache.get(key, max_age_s=None)
    if _is_fresh(cached) and isinstance(cached.get("map"), dict):
        return cached["map"]

    from data.http import get_with_retry, is_http_404

    mapping: dict[str, str] = {}
    complete = True
    # Oldest → newest so the newest published panel wins on conflicts.
    for year in range(FIRST_HMDA_YEAR, date.today().year):
        url = PANEL_URL_TPL.format(year=year)
        try:
            resp = get_with_retry(url, timeout=60)
        except Exception as e:
            if is_http_404(e):
                continue          # panel for this year not published — normal
            print(f"[hmda] panel {year} fetch failed: {type(e).__name__}: {e}")
            complete = False
            continue
        if resp is None:          # retries exhausted on 429s
            print(f"[hmda] panel {year}: retries exhausted (429)")
            complete = False
            continue
        try:
            mapping.update(_parse_panel_zip(resp.content))
        except Exception as e:
            print(f"[hmda] panel {year} parse failed: {type(e).__name__}: {e}")
            complete = False

    if not mapping:
        return None
    if complete:
        cache.put(key, {"map": mapping,
                        "cached_at": datetime.now().isoformat()})
    return mapping


def resolve_lei(rssd: int) -> str | None:
    """HMDA LEI for a bank's FED_RSSD (data.fdic_client.get_rssd_for_cert),
    or None when the bank has never filed HMDA — many small banks genuinely
    haven't; that is n/a, never an error."""
    if not rssd:
        return None
    mapping = _panel_lei_map()
    if mapping is None:
        return None
    return mapping.get(str(int(rssd)))


# ──────────────────────────────────────────────────────────────────────────
# Originations by year
# ──────────────────────────────────────────────────────────────────────────

def _fetch_year_aggregate(lei: str, year: int) -> dict | None:
    """{"count": int, "volume_usd": float} of originations for one year,
    cached under ``hmda:orig:v1:{lei}:{year}``. count 0 means the lender
    genuinely recorded no originations that year (real data, cached).
    None means the fetch failed or the response shape was unexpected —
    never cached."""
    from data import cache

    key = f"hmda:orig:v1:{lei}:{year}"
    # Freshness judged by _is_fresh below (7d design TTL) — no 24h read ceiling.
    cached = cache.get(key, max_age_s=None)
    if (_is_fresh(cached) and isinstance(cached.get("count"), int)
            and "volume_usd" in cached):
        return {"count": cached["count"], "volume_usd": cached["volume_usd"]}

    try:
        from data.http import get_with_retry
        resp = get_with_retry(HMDA_AGG_URL, params={
            "leis": lei,
            "years": str(year),
            "actions_taken": ACTION_ORIGINATED,
        }, timeout=90)
        if resp is None:
            print(f"[hmda] aggregations {lei} {year}: retries exhausted (429)")
            return None
        aggs = resp.json().get("aggregations")
    except Exception as e:
        print(f"[hmda] aggregations {lei} {year} error: {type(e).__name__}: {e}")
        return None

    if not isinstance(aggs, list) or not aggs:
        print(f"[hmda] aggregations {lei} {year}: unexpected response shape")
        return None
    count, volume = 0, 0.0
    for a in aggs:
        c, s = a.get("count"), a.get("sum")
        if not isinstance(c, (int, float)) or not isinstance(s, (int, float)):
            print(f"[hmda] aggregations {lei} {year}: non-numeric row — "
                  "refusing to guess")
            return None
        count += int(c)
        # HMDA aggregation `sum` is RAW dollars (verified: WaFd 2023 sum
        # 1,101,120,000 == Σ loan-level loan_amount) — no unit conversion.
        volume += float(s)

    cache.put(key, {"count": count, "volume_usd": volume,
                    "cached_at": datetime.now().isoformat()})
    return {"count": count, "volume_usd": volume}


def originations_by_year(lei: str, years: list[int]) -> dict:
    """{year: {"count": int, "volume_usd": float}} of HMDA originations
    (action_taken=1). Only years with data present: count-0 years (lender
    didn't file / recorded nothing) and failed fetches are omitted — a
    failed year is logged and retried next call, never cached."""
    out: dict[int, dict] = {}
    if not lei:
        return out
    for year in years:
        year = int(year)
        if year < FIRST_HMDA_YEAR:
            continue              # pre-LEI era — not served by this API
        rec = _fetch_year_aggregate(lei, year)
        if rec is None:
            continue              # fetch failed — already logged, not cached
        if rec["count"] > 0:
            out[year] = rec
    return out


# ──────────────────────────────────────────────────────────────────────────
# Geographic breakdown
# ──────────────────────────────────────────────────────────────────────────

def latest_breakdown(lei: str, year: int, by: str = "state") -> list[dict] | None:
    """Origination breakdown for one year, grouped by state or county.

    Returns rows sorted by count desc —
      by="state":  {"state": "WA", "count": 794, "volume_usd": ...}
      by="county": {"county": "53073", "state": "WA", "count": ..., ...}
    (county is 5-digit FIPS; loans with no reported geography group under
    None). Empty list = the lender genuinely recorded no originations that
    year. None = fetch/parse failure — never cached, never a partial result.
    """
    if by not in ("state", "county"):
        raise ValueError(f"by must be 'state' or 'county', got {by!r}")
    if not lei:
        return None
    from data import cache

    year = int(year)
    key = f"hmda:breakdown:v1:{lei}:{year}:{by}"
    # Freshness judged by _is_fresh below (7d design TTL) — no 24h read ceiling.
    cached = cache.get(key, max_age_s=None)
    if _is_fresh(cached) and isinstance(cached.get("rows"), list):
        return cached["rows"]

    try:
        from data.http import get_with_retry
        # Loan-level rows for this lender-year (originations only); the
        # aggregation endpoint can't group by geography, so we group here.
        resp = get_with_retry(HMDA_CSV_URL, params={
            "leis": lei,
            "years": str(year),
            "actions_taken": ACTION_ORIGINATED,
        }, timeout=180)
        if resp is None:
            print(f"[hmda] csv {lei} {year}: retries exhausted (429)")
            return None
        rdr = csv.reader(io.StringIO(resp.text))
        header = next(rdr, None) or []
    except Exception as e:
        print(f"[hmda] csv {lei} {year} error: {type(e).__name__}: {e}")
        return None

    idx = {c.strip().lower(): i for i, c in enumerate(header)}
    i_st, i_cty = idx.get("state_code"), idx.get("county_code")
    i_amt = idx.get("loan_amount")
    if i_st is None or i_cty is None or i_amt is None:
        print(f"[hmda] csv {lei} {year}: expected columns missing")
        return None

    groups: dict = {}
    needed = max(i_st, i_cty, i_amt)
    for row in rdr:
        if not row or len(row) <= needed:
            continue
        # HMDA 2018+ loan_amount is RAW dollars (see module docstring) — no
        # unit conversion. An unparseable amount would make every total
        # silently partial, so it fails the whole breakdown (n/a, not wrong).
        try:
            amt = float(row[i_amt])
        except ValueError:
            print(f"[hmda] csv {lei} {year}: unparseable loan_amount "
                  f"{row[i_amt]!r} — refusing partial totals")
            return None
        st = row[i_st].strip() or None
        gkey = st if by == "state" else (row[i_cty].strip() or None, st)
        cur = groups.setdefault(gkey, [0, 0.0])
        cur[0] += 1
        cur[1] += amt

    rows = []
    for gkey, (n, vol) in groups.items():
        if by == "state":
            rows.append({"state": gkey, "count": n, "volume_usd": vol})
        else:
            county, st = gkey
            rows.append({"county": county, "state": st,
                         "count": n, "volume_usd": vol})
    rows.sort(key=lambda r: -r["count"])

    cache.put(key, {"rows": rows, "cached_at": datetime.now().isoformat()})
    return rows
