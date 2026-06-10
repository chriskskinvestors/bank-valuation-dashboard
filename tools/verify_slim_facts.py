"""
Verify the slim SEC companyfacts projection (data.sec_client._slim_facts) is
loss-free: it must yield byte-identical fundamentals to the full blob for every
extractor the dashboard uses. Run this after changing SLIM_USGAAP_CONCEPTS or
adding any new XBRL concept reference.

    python tools/verify_slim_facts.py            # default sample
    python tools/verify_slim_facts.py BANR JPM   # specific tickers

Exit 0 = slim is safe (zero diffs); exit 1 = a concept is missing from the
projection (the diff shows which field broke).
"""
from __future__ import annotations
import sys
import math
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT = ["BANR", "JPM", "BKU", "BFST", "PNFP", "WAL", "CARE", "WAFD", "COLB",
           "CASH", "HWC", "ONB", "UMBF", "SFNC", "FFIN", "CFR", "GBCI", "TCBI",
           "FULT", "HOMB"]


def _eq(a, b):
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
        return abs(a - b) < 1e-6
    return a == b


def _cmp(a, b):
    return [(k, a.get(k), b.get(k)) for k in set(a) | set(b) if not _eq(a.get(k), b.get(k))]


def main(tickers):
    import warnings
    warnings.filterwarnings("ignore")
    import data.sec_client as sc
    import ui.financial_highlights as fh
    from data.bank_mapping import get_bank_info

    orig = sc.fetch_company_facts
    total = 0
    tested = 0
    for tk in tickers:
        info = get_bank_info(tk)
        if not info or not info.get("cik"):
            print(f"  -- {tk}: no CIK")
            continue
        cik = info["cik"]
        full = sc._download_company_facts(cik)
        if not full:
            print(f"  -- {tk}: download failed")
            continue
        slim = sc._slim_facts(full)

        sc.fetch_company_facts = lambda c, _f=full: _f
        a1 = sc.get_latest_fundamentals(cik)
        a2 = sc.get_fundamentals_with_provenance(cik)
        a3 = fh._per_share_for_ends(cik, [datetime(2024, 12, 31), datetime(2025, 12, 31)])
        sc.fetch_company_facts = lambda c, _s=slim: _s
        b1 = sc.get_latest_fundamentals(cik)
        b2 = sc.get_fundamentals_with_provenance(cik)
        b3 = fh._per_share_for_ends(cik, [datetime(2024, 12, 31), datetime(2025, 12, 31)])
        sc.fetch_company_facts = orig

        d = _cmp(a1, b1)
        d += _cmp({k: (v if not isinstance(v, dict) else None) for k, v in a2.items()},
                  {k: (v if not isinstance(v, dict) else None) for k, v in b2.items()})
        for end in a3:
            for kk in ("eps", "dps", "bvps", "tbvps", "shares"):
                if not _eq(a3[end].get(kk), b3[end].get(kk)):
                    d.append((f"{end:%Y}/{kk}", a3[end].get(kk), b3[end].get(kk)))

        total += len(d)
        tested += 1
        fm = len(json.dumps(full)) / 1e6
        sk = len(json.dumps(slim)) / 1e3
        flag = "OK  " if not d else "DIFF"
        print(f"  {flag} {tk:6} full={fm:.1f}MB slim={sk:.0f}KB diffs={len(d)}"
              + (f"  {d[:3]}" if d else ""))

    print(f"\nTested {tested} banks — total diffs {total}: "
          + ("SLIM IS SAFE" if total == 0 else "CONCEPT MISSING from SLIM_USGAAP_CONCEPTS"))
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or DEFAULT))
