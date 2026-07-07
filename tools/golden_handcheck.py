"""
Independent hand-check for golden-dataset re-pinning.

Pulls raw XBRL facts straight from SEC companyfacts with requests —
deliberately does NOT import data/sec_client or any pipeline code, so the
numbers here are an independent read of the filings (non-circular). Prints
quarter-by-quarter components so each derived value can be eyeballed
against the May-filed Q1-2026 10-Qs before re-pinning baselines.

Run: python -m tools.golden_handcheck
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date

import requests

HEADERS = {"User-Agent": "KSK Investors research kris@kskinvestors.com"}

BANKS = {
    "JPM":  19617,
    "BAC":  70858,
    "WFC":  72971,
    "C":    831001,
    "USB":  36104,
    "PNC":  713676,
    "HBAN": 49196,
    "SFST": 1090009,
}

NI_TAGS = ["NetIncomeLoss"]
NI_COMMON_TAGS = ["NetIncomeLossAvailableToCommonStockholdersBasic"]
EQUITY_TAGS = ["StockholdersEquity"]
GOODWILL_TAGS = ["Goodwill"]
INTAN_TAGS = [
    "IntangibleAssetsNetExcludingGoodwill",
    "FiniteLivedIntangibleAssetsNet",
    "OtherIntangibleAssetsNet",
]
# Preferred carrying-value tags, in the same resolution order the pipeline uses.
# A par-only tag reading exactly 0 (PNC) is treated as unresolved, not $0.
PREFERRED_VALUE_TAGS = [
    "PreferredStockValue",
    "PreferredStockIncludingAdditionalPaidInCapital",
    "PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount",
    "PreferredStockValueOutstanding",
    "PreferredStockLiquidationPreferenceValue",
]
PREFERRED_SHARE_TAGS = ["PreferredStockSharesOutstanding", "PreferredStockSharesIssued"]


def fetch_facts(cik: int) -> dict:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def usd_facts(facts: dict, tag: str) -> list[dict]:
    node = facts.get("facts", {}).get("us-gaap", {}).get(tag)
    if not node:
        return []
    units = node.get("units", {})
    rows = units.get("USD") or []
    # keep only 10-Q/10-K originals (8-K earnings exhibits can restate)
    return [r for r in rows if r.get("form") in ("10-Q", "10-K", "10-K/A", "10-Q/A")]


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def quarterly_series(rows: list[dict]) -> dict[str, float]:
    """end-date -> 3-month value. Q4 derived as FY minus the three quarters."""
    q: dict[str, float] = {}
    fy: dict[tuple[str, str], float] = {}
    for r in rows:
        s, e, v = r.get("start"), r.get("end"), r.get("val")
        if not (s and e) or v is None:
            continue
        d = _days(s, e)
        if 80 <= d <= 100:
            q[e] = v  # later filings overwrite (restatements win)
        elif 350 <= d <= 380:
            fy[(s, e)] = v
    # derive missing Q4s: FY − sum of the three quarters inside it
    for (s, e), v in fy.items():
        if e in q:
            continue
        inside = {k: x for k, x in q.items() if s < k < e}
        if len(inside) == 3:
            q[e] = v - sum(inside.values())
    return q


def ttm(q: dict[str, float]) -> tuple[float | None, list[str]]:
    ends = sorted(q)[-4:]
    if len(ends) < 4:
        return None, ends
    return sum(q[e] for e in ends), ends


def latest_instant(rows: list[dict]) -> tuple[float | None, str]:
    best_end, best_val = "", None
    for r in rows:
        e, v = r.get("end"), r.get("val")
        if e and v is not None and e >= best_end:
            best_end, best_val = e, v
    return best_val, best_end


def latest_current(rows: list[dict], max_age_days: int = 400) -> tuple[float | None, str]:
    """Like latest_instant but rejects end-dates older than max_age_days from
    today — mirrors the pipeline's max_age_years=1 staleness guard so stale tags
    (PNC's 2016 intangibles, USB's 2013 preferred) don't leak in."""
    cutoff = date.today().isoformat()
    best_end, best_val = "", None
    for r in rows:
        e, v = r.get("end"), r.get("val")
        if not e or v is None:
            continue
        if _days(e, cutoff) > max_age_days:
            continue
        if e >= best_end:
            best_end, best_val = e, v
    return best_val, best_end


def resolve_preferred(facts: dict) -> tuple[float | None, bool, str]:
    """(carrying_value, has_preferred, source_tag). A value tag reading exactly
    0 (par-only, PNC) is treated as 'keep looking', never a resolved $0."""
    value, src = None, ""
    for tag in PREFERRED_VALUE_TAGS:
        v, _ = latest_current(usd_facts(facts, tag))
        if v:  # nonzero
            value, src = v, tag
            break
    pref_sh = 0.0
    for tag in PREFERRED_SHARE_TAGS:
        node = facts.get("facts", {}).get("us-gaap", {}).get(tag)
        if not node:
            continue
        rows = [r for u in node.get("units", {}).values() for r in u]
        v, _ = latest_current(rows)
        if v:
            pref_sh = v
            break
    has_preferred = bool(value) or (pref_sh > 0)
    if not has_preferred:
        return 0.0, False, ""
    return value, True, src  # value None => unresolved (cardinal-rule n/a)


def shares_outstanding(facts: dict) -> tuple[float | None, str]:
    node = facts.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding")
    if not node:
        return None, ""
    rows = [r for u in node.get("units", {}).values() for r in u]
    return latest_instant(rows)


def main() -> int:
    for ticker, cik in BANKS.items():
        facts = fetch_facts(cik)
        time.sleep(0.15)

        print(f"\n{'=' * 78}\n{ticker}  (CIK {cik})  — {facts.get('entityName')}")

        ni_q = quarterly_series(usd_facts(facts, NI_TAGS[0]))
        ni_ttm, ni_ends = ttm(ni_q)
        nic_q = quarterly_series(usd_facts(facts, NI_COMMON_TAGS[0]))
        nic_ttm, nic_ends = ttm(nic_q)

        eq, eq_end = latest_instant(usd_facts(facts, EQUITY_TAGS[0]))
        gw, gw_end = latest_instant(usd_facts(facts, GOODWILL_TAGS[0]))
        intan, intan_tag, intan_end = None, None, ""
        for tag in INTAN_TAGS:
            v, e = latest_current(usd_facts(facts, tag))
            if v is not None:
                intan, intan_tag, intan_end = v, tag, e
                break
        pref, pref_present, pref_src = resolve_preferred(facts)
        sh, sh_end = shares_outstanding(facts)

        print(f"  NetIncomeLoss quarters: "
              + ", ".join(f"{e}={ni_q[e]/1e9:.3f}B" for e in ni_ends))
        if ni_ttm is not None:
            print(f"  NI TTM                = {ni_ttm/1e9:.3f}B")
        print(f"  NI-to-common quarters:  "
              + ", ".join(f"{e}={nic_q[e]/1e9:.3f}B" for e in nic_ends))
        if nic_ttm is not None:
            print(f"  NI-to-common TTM      = {nic_ttm/1e9:.3f}B")
        print(f"  StockholdersEquity    = {eq/1e9:.3f}B   as of {eq_end}")
        gw_b = (gw or 0) / 1e9
        print(f"  Goodwill              = {gw_b:.3f}B   as of {gw_end}")
        if intan is not None:
            print(f"  Intangibles ({intan_tag}) = {intan/1e9:.3f}B   as of {intan_end}")
        else:
            print("  Intangibles           = (no tag found; 0 assumed)")
        if pref_present:
            if pref is not None:
                print(f"  Preferred ({pref_src}) = {pref/1e9:.3f}B")
            else:
                print("  Preferred             = present but UNRESOLVED (par-zero/stale) -> n/a")
        else:
            print("  Preferred             = none")
        print(f"  Shares outstanding    = {sh:,.0f}   as of {sh_end}")

        if eq is not None and sh:
            # COMMON tangible book: subtract preferred first, then full intangibles.
            preferred_unresolved = pref_present and pref is None
            common_eq = eq - (pref or 0)
            tce = common_eq - (gw or 0) - (intan or 0)
            print(f"  -> Common equity      = {common_eq/1e9:.3f}B")
            print(f"  -> Common TCE         = {tce/1e9:.3f}B")
            if preferred_unresolved:
                print("  -> COMMON TBVPS       = n/a (preferred present but unresolved)")
            else:
                print(f"  -> COMMON TBVPS       = {tce/sh:.2f}")
            # ROATCE = return on tangible COMMON equity: NI-available-to-common
            # over common TCE (both common-basis). n/a when preferred is present
            # but unresolved (common basis unknowable) or the to-common TTM is
            # unavailable. No preferred → NI-to-common == NI, so fall back to it.
            if preferred_unresolved:
                print("  -> ROATCE             = n/a (preferred present but unresolved)")
            elif tce <= 0:
                print("  -> ROATCE             = n/a (non-positive TCE)")
            elif nic_ttm is not None:
                print(f"  -> ROATCE (NIC/TCE)   = {nic_ttm / tce * 100:.2f}%")
            elif ni_ttm is not None and not pref_present:
                print(f"  -> ROATCE (NI/TCE)    = {ni_ttm / tce * 100:.2f}%")
            else:
                print("  -> ROATCE             = n/a (NI-to-common unavailable)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
