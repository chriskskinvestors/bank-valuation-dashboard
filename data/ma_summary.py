"""
Transactions Summary aggregates for one holdco (docs/SNL-BUILD-PLAN.md §14).

Supplies the non-M&A legs of the Summary sub-tab (M&A deals come from
data/ma_history):

  shelf / offering counts — FULL EDGAR filing history via the submissions
      API (the "recent" block plus every archived page listed under
      filings.files), bucketed by form type. Factual filing-type counts:
      Shelf = S-1/S-3 registration family; Offerings = 424B* takedown
      prospectuses (ECM vs DCM split needs the Detailed Offerings leg —
      deliberately UNSPLIT here, labeled as such).
  buyback-program 8-Ks — EDGAR full-text search (2001+ floor) for the
      holdco's own 8-Ks quoting "repurchase program", EXCLUDING earnings
      filings (item 2.02 — earnings PRs routinely mention the standing
      program; a new authorization files under 8.01/7.01/1.01). This is
      our own classification from filing type + item codes, per the plan,
      and is labeled as such in the UI.

Cache: ``ma_summary:v1:{cik}`` for 7 days. Any fetch failure returns None
(uncacheable) — never a partial summary frozen as truth.
"""

from __future__ import annotations

import time
from datetime import date, datetime

import requests

from data.ma_announcements import EDGAR_FTS, _EFTS_FLOOR

CACHE_TTL_SECONDS = 7 * 86400
_PAUSE_S = 0.15

# Registration-statement family = "shelf" bucket (S-3 shelf + S-1 primary).
_SHELF_FORMS = {"S-1", "S-1/A", "S-3", "S-3/A", "S-3ASR", "S-3D", "S-3DPOS",
                "S-3MEF", "S-1MEF"}
_EFTS_PAGE_CAP = 400            # runaway guard; banks have far fewer hits


def _headers() -> dict:
    from config import SEC_USER_AGENT
    return {"User-Agent": SEC_USER_AGENT}


def _is_fresh(cached: dict | None) -> bool:
    from data.freshness import is_fresh
    return is_fresh(cached, CACHE_TTL_SECONDS)


def _bucket(form: str) -> str | None:
    form = (form or "").strip().upper()
    if form in _SHELF_FORMS:
        return "shelf"
    if form.startswith("424B"):
        return "offerings"
    return None


def _count_filing_pages(cik: int) -> tuple[dict, bool]:
    """{year: {shelf: n, offerings: n}} over the FULL submissions history
    (recent block + archived pages). (counts, ok) — ok=False on any failure."""
    cik10 = f"{int(cik):010d}"
    counts: dict[int, dict] = {}

    def _tally(block: dict):
        forms = block.get("form", [])
        dates = block.get("filingDate", [])
        for i, form in enumerate(forms):
            b = _bucket(form)
            if not b or i >= len(dates):
                continue
            try:
                yr = int(str(dates[i])[:4])
            except (TypeError, ValueError):
                continue
            counts.setdefault(yr, {"shelf": 0, "offerings": 0})[b] += 1

    try:
        resp = requests.get(f"https://data.sec.gov/submissions/CIK{cik10}.json",
                            headers=_headers(), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        _tally(data.get("filings", {}).get("recent", {}))
        for page in data.get("filings", {}).get("files", []):
            name = page.get("name")
            if not name:
                continue
            time.sleep(_PAUSE_S)
            r2 = requests.get(f"https://data.sec.gov/submissions/{name}",
                              headers=_headers(), timeout=30)
            r2.raise_for_status()
            _tally(r2.json())
    except Exception as e:
        print(f"[ma_summary] submissions cik {cik}: {type(e).__name__}: {e}")
        return counts, False
    return counts, True


def _buyback_8ks(cik: int) -> tuple[list[dict], bool]:
    """The holdco's 8-Ks quoting "repurchase program", earnings filings
    (item 2.02) excluded. ([{date, form, adsh, url}] newest-first, ok)."""
    cik10 = f"{int(cik):010d}"
    by_adsh: dict[str, dict] = {}
    offset = 0
    while offset < _EFTS_PAGE_CAP:
        try:
            resp = requests.get(EDGAR_FTS, params={
                "q": '"repurchase program"', "forms": "8-K", "ciks": cik10,
                "dateRange": "custom", "startdt": _EFTS_FLOOR,
                "enddt": date.today().isoformat(), "from": offset,
            }, headers=_headers(), timeout=30)
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])
        except Exception as e:
            print(f"[ma_summary] buyback efts cik {cik}: {type(e).__name__}: {e}")
            return [], False
        if not hits:
            break
        for h in hits:
            src = h.get("_source", {})
            adsh = src.get("adsh") or (h.get("_id", "").split(":")[0])
            if not adsh or not src.get("file_date"):
                continue
            items = {str(i) for i in (src.get("items") or [])}
            if "2.02" in items:      # earnings PR mentioning the program
                continue
            doc = h.get("_id", "").split(":")[-1]
            is_ex99 = str(src.get("file_type") or "").upper().startswith("EX-99")
            cur = by_adsh.get(adsh)
            if cur is None or (is_ex99 and not cur["_ex99"]):
                by_adsh[adsh] = {
                    "date": src.get("file_date"),
                    "form": src.get("root_forms", ["8-K"])[0]
                            if src.get("root_forms") else "8-K",
                    "adsh": adsh,
                    "url": (f"https://www.sec.gov/Archives/edgar/data/"
                            f"{int(cik)}/{adsh.replace('-', '')}/{doc}"),
                    "_ex99": is_ex99,
                }
        offset += len(hits)
        time.sleep(_PAUSE_S)
    rows = [{k: v for k, v in r.items() if k != "_ex99"}
            for r in by_adsh.values()]
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows, True


def get_summary(cik) -> dict | None:
    """
    Summary aggregates for a holdco CIK, or None when a source fetch failed
    (never cached partial):

      {"filings_by_year": {year: {"shelf": n, "offerings": n}},
       "buybacks": [{date, form, adsh, url}, ...]}   # newest-first, 2001+
    """
    if not cik:
        return None
    cik = int(cik)
    from data import cache

    key = f"ma_summary:v1:{cik}"
    cached = cache.get(key)
    if _is_fresh(cached) and isinstance(cached.get("summary"), dict):
        return cached["summary"]

    filings, f_ok = _count_filing_pages(cik)
    buybacks, b_ok = _buyback_8ks(cik)
    if not (f_ok and b_ok):
        return None
    summary = {"filings_by_year": {str(y): c for y, c in sorted(filings.items())},
               "buybacks": buybacks}
    cache.put(key, {"summary": summary,
                    "cached_at": datetime.now().isoformat()})
    return summary


if __name__ == "__main__":
    # LIVE smoke: Banner Corp (CIK 946673). Hand-checkable expectations:
    # Banner has S-3 shelf registrations and 424B takedowns on EDGAR, and
    # announced repurchase programs repeatedly (8.01/7.01 8-Ks, 2001+).
    s = get_summary(946673)
    assert s is not None, "fetch failed"
    fb = s["filings_by_year"]
    shelf_total = sum(c["shelf"] for c in fb.values())
    off_total = sum(c["offerings"] for c in fb.values())
    print(f"Banner: {shelf_total} shelf filings, {off_total} 424B filings, "
          f"{len(s['buybacks'])} buyback 8-Ks")
    for r in s["buybacks"][:5]:
        print("  ", r["date"], r["form"], r["url"][-40:])
    assert shelf_total > 0 and off_total > 0, (shelf_total, off_total)
    assert s["buybacks"], "expected at least one buyback-program 8-K"
    assert all(r["date"] >= "2001" for r in s["buybacks"])
    print("SMOKE OK")
