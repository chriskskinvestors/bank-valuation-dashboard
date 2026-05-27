"""
Filter newly-discovered OTC tickers in bank_map_resolved.json: drop any
entry whose SEC entity isn't actually a bank (per SIC code).

The discovery pass uses name-overlap scoring which can match wrong companies
that happen to share a token with a bank holdco (e.g. ARROWHEAD PHARMA
matched a 'arrow' bank). This filter checks each ticker's SEC SIC code
and removes non-bank matches.

Run:   python tools/filter_otc_discoveries.py
"""

from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import requests

UA = {"User-Agent": "BankValuationDashboard chris@kskinvestors.com"}

# SIC codes that are bank-related (commercial banks, savings institutions,
# bank holdcos, savings holdcos). Anything else is dropped.
BANK_SIC_CODES = {
    "6020",  # State commercial banks
    "6021",  # National commercial banks
    "6022",  # State commercial banks (state-chartered)
    "6035",  # Savings institutions, federally chartered
    "6036",  # Savings institutions, state chartered
    "6710",  # Holding offices
    "6711",  # Bank holding companies
    "6712",  # Offices of bank holding companies
    "6770",  # Blank checks (sometimes used by bank de novos)
}


def get_sic(cik: int) -> str:
    try:
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
            headers=UA, timeout=10,
        )
        if r.status_code != 200:
            return ""
        return str(r.json().get("sic", "") or "")
    except Exception:
        return ""


def main():
    p = REPO_ROOT / "data" / "bank_map_resolved.json"
    data = json.loads(p.read_text())
    print(f"Loaded {len(data)} entries")

    # Check every entry with a CIK
    candidates = [(t, info) for t, info in data.items() if info.get("cik")]
    print(f"Checking SIC for {len(candidates)} entries with CIKs...")

    sic_map: dict[str, str] = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(get_sic, info["cik"]): t for t, info in candidates}
        done = 0
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                sic_map[t] = fut.result()
            except Exception:
                sic_map[t] = ""
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(candidates)} ({time.time()-t0:.0f}s)")

    # Filter
    to_drop_or_null = []
    for t, info in candidates:
        sic = sic_map.get(t, "")
        if sic and sic not in BANK_SIC_CODES:
            to_drop_or_null.append((t, info, sic))

    print()
    print(f"Found {len(to_drop_or_null)} entries with non-bank SIC codes:")
    for t, info, sic in to_drop_or_null[:30]:
        print(f"  {t:<6} cik={info.get('cik')}  SIC={sic}  {info.get('name','')[:50]}")

    if not to_drop_or_null:
        print("✓ Nothing to remove.")
        return

    # For each non-bank ticker, clear the CIK (keep cert + name so FDIC
    # data still flows; just disable the wrong SEC linkage). If both
    # cik and cert end up None, the ticker is dropped from the JSON.
    removed = []
    for t, info, sic in to_drop_or_null:
        old_cik = info.get("cik")
        info["cik"] = None
        if not info.get("fdic_cert"):
            # No fallback — drop the entire entry
            del data[t]
            removed.append((t, "deleted", old_cik))
        else:
            data[t] = info
            removed.append((t, "cik_cleared", old_cik))

    p.write_text(json.dumps(data, indent=2, sort_keys=True))
    print()
    print(f"✓ Cleaned: {sum(1 for _,a,_ in removed if a=='cik_cleared')} CIKs cleared, "
          f"{sum(1 for _,a,_ in removed if a=='deleted')} entries deleted.")
    print(f"  JSON now has {len(data)} entries.")


if __name__ == "__main__":
    main()
