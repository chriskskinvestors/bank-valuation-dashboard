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
            v, e = latest_instant(usd_facts(facts, tag))
            if v is not None:
                intan, intan_tag, intan_end = v, tag, e
                break
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
        print(f"  Shares outstanding    = {sh:,.0f}   as of {sh_end}")

        if eq is not None and sh:
            tce = eq - (gw or 0) - (intan or 0)
            print(f"  -> TCE                = {tce/1e9:.3f}B")
            print(f"  -> TBVPS              = {tce/sh:.2f}")
            if ni_ttm is not None:
                print(f"  -> ROATCE (NI/TCE)    = {ni_ttm/tce*100:.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
