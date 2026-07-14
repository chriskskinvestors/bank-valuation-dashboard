"""
13D/G stake filings on one issuer (docs/SNL-BUILD-PLAN.md §14 Private
Equity Transactions; layout owner-confirmed 2026-07-13).

The AUTHORITATIVE filing list comes from the issuer's own EDGAR
submissions history (SC 13D / SC 13G families appear under the subject
company — complete, including pre-2001). HOLDER names come from EDGAR
full-text search with the issuer CIK filter, whose display_names carry
[subject, holder] per accession (live-verified on Banner: 104 hits,
holders like "Klaue David A" on the lone SC 13D) — joined by accession.
EFTS coverage starts 2001, so older filings keep an honest "—" holder
with a working link to the filing itself.

13D (activist/control intent) vs 13G (passive schedule) is the FORM'S OWN
distinction — no classification of ours. The UI shows 13Ds prominently
and collapses the 13G pile (index managers file 13Gs on every bank).

Cache: ``stake_filings:v1:{cik}`` for 7 days; fetch failure returns None
(uncacheable). Rows newest-first.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime

import requests

from data.ma_announcements import EDGAR_FTS, _EFTS_FLOOR, brand_token
from data.ma_summary import iter_submission_filings

CACHE_TTL_SECONDS = 7 * 86400
_PAUSE_S = 0.15
_EFTS_PAGE_CAP = 600
_NAME_CIK_RE = re.compile(r"^(.*?)\s*(?:\([A-Z.,\- ]{1,12}\)\s*)?\(CIK\s+(\d+)\)")


def _headers() -> dict:
    from config import SEC_USER_AGENT
    return {"User-Agent": SEC_USER_AGENT}


def _is_fresh(cached: dict | None) -> bool:
    from data.freshness import is_fresh
    return is_fresh(cached, CACHE_TTL_SECONDS)


def _holder_names(issuer_cik: int, issuer_name: str) -> tuple[dict, bool]:
    """{accession: {name, cik}} for the issuer's 13D/G filings via EFTS —
    the display_names entry that is NOT the issuer. (map, ok)."""
    out: dict[str, dict] = {}
    offset = 0
    # Single distinctive brand token, not the full corporate name — a quoted
    # "Banner Corp" phrase misses covers that write "Banner Corporation"
    # (live-verified: the 13D holders only joined with the token query).
    tok = brand_token(issuer_name or "")
    q = f'"{tok}"' if tok else '"schedule 13"'
    while offset < _EFTS_PAGE_CAP:
        try:
            resp = requests.get(EDGAR_FTS, params={
                "q": q, "forms": "SC 13D,SC 13G",
                "ciks": f"{int(issuer_cik):010d}",
                "dateRange": "custom", "startdt": _EFTS_FLOOR,
                "enddt": date.today().isoformat(), "from": offset,
            }, headers=_headers(), timeout=30)
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])
        except Exception as e:
            print(f"[stake_filings] efts cik {issuer_cik}: "
                  f"{type(e).__name__}: {e}")
            return out, False
        if not hits:
            break
        for h in hits:
            src = h.get("_source", {})
            adsh = src.get("adsh") or (h.get("_id", "").split(":")[0])
            if not adsh or adsh in out:
                continue
            for dn in (src.get("display_names") or []):
                m = _NAME_CIK_RE.match((dn or "").strip())
                if m and int(m.group(2)) != int(issuer_cik):
                    out[adsh] = {"name": m.group(1).strip(),
                                 "cik": int(m.group(2))}
                    break
        offset += len(hits)
        time.sleep(_PAUSE_S)
    return out, True


def get_stake_filings(cik, issuer_name: str = "") -> list[dict] | None:
    """
    All SC 13D/13G filings ON an issuer, newest-first, or None on fetch
    failure (uncacheable):

      [{date, form, holder_name | None, holder_cik | None, url, accession}]
    """
    if not cik:
        return None
    cik = int(cik)
    from data import cache

    key = f"stake_filings:v1:{cik}"
    cached = cache.get(key)
    if _is_fresh(cached) and isinstance(cached.get("rows"), list):
        return cached["rows"]

    filings, ok = iter_submission_filings(cik)
    if not ok:
        return None
    sc = [f for f in filings if f["form"].startswith("SC 13D")
          or f["form"].startswith("SC 13G")]
    holders, h_ok = _holder_names(cik, issuer_name) if sc else ({}, True)
    if not h_ok:
        return None

    rows = []
    for f in sc:
        acc = f["accession"]
        h = holders.get(acc) or {}
        url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/"
               f"{acc.replace('-', '')}/{f['doc']}" if f["doc"] else
               f"https://www.sec.gov/Archives/edgar/data/{cik}/"
               f"{acc.replace('-', '')}/") if acc else None
        rows.append({"date": f["date"], "form": f["form"],
                     "holder_name": h.get("name"),
                     "holder_cik": h.get("cik"),
                     "url": url, "accession": acc})
    rows.sort(key=lambda r: r["date"], reverse=True)
    cache.put(key, {"rows": rows, "cached_at": datetime.now().isoformat()})
    return rows


if __name__ == "__main__":
    # LIVE smoke — Banner Corp: submissions carry the SC 13* list, EFTS names
    # the holders; the lone SC 13D (Klaue David A, 2007-06-26) must surface
    # with its holder, and 13Gs must include the index-manager pile.
    rows = get_stake_filings(946673, "Banner Corp")
    assert rows is not None, "fetch failed"
    d13 = [r for r in rows if r["form"].startswith("SC 13D")]
    g13 = [r for r in rows if r["form"].startswith("SC 13G")]
    print(f"Banner: {len(rows)} stake filings ({len(d13)} 13D, {len(g13)} 13G)")
    for r in d13:
        print("  13D:", r["date"], r["form"], r["holder_name"])
    for r in g13[:5]:
        print("  13G:", r["date"], r["form"], r["holder_name"])
    assert any(r["date"] == "2007-06-26" and "Klaue" in (r["holder_name"] or "")
               for r in d13), d13
    assert len(g13) > 10
    named = sum(1 for r in rows if r["holder_name"])
    print(f"holders named on {named}/{len(rows)}")
    assert named > len(rows) // 2
    print("SMOKE OK")
