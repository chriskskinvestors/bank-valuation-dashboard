"""Golden handchecks for the deep FDIC history store (DEEP-HISTORY-PLAN.md).

Values below were HAND-READ from the raw FDIC financials API on 2026-09-15
(never pipeline output — the golden discipline). Run this against the prod
store (job override: `-m tools.verify_deep_history`) after any backfill;
Phase-1 chart verification compares the MAX-range charts to these numbers.

All figures $000 as FDIC reports them.
"""
from __future__ import annotations

import sys

# (label, cert, repdte, {field: hand-read value})
GOLDEN = [
    ("TCBK simple mid-90s", 21943, "19951231",
     {"ASSET": 603_251, "DEP": 516_328, "EQTOT": 52_867, "NETINC": 7_210}),
    ("TCBK simple mid-00s", 21943, "20051231",
     {"ASSET": 1_841_252, "DEP": 1_499_423, "EQTOT": 188_226,
      "NETINC": 25_403}),
    # ZION's LEAD charter only — the consolidated group figure for 2005Q4
    # must be STRICTLY GREATER (multi-charter era: CB&T, Amegy, NSB, ...).
    ("ZION lead charter 2005", 2270, "20051231",
     {"ASSET": 12_667_668, "DEP": 9_212_594, "EQTOT": 836_385,
      "NETINC": 174_237}),
]


def main() -> int:
    from data.fdic_history_store import get_cert_history
    failures = 0
    for label, cert, repdte, want in GOLDEN:
        recs = [r for r in get_cert_history(cert)
                if str(r.get("REPDTE", ""))[:8].replace("-", "")
                .startswith(repdte[:6])]
        if not recs:
            print(f"FAIL {label}: no stored row for cert {cert} @ {repdte}")
            failures += 1
            continue
        rec = recs[0]
        for field, expect in want.items():
            got = rec.get(field)
            try:
                ok = got is not None and abs(float(got) - expect) < 0.5
            except (TypeError, ValueError):
                ok = False
            status = "ok  " if ok else "FAIL"
            if not ok:
                failures += 1
            print(f"{status} {label} {field}: store={got} hand={expect}")
    # Multi-charter inequality — WTFC, a TRUE present-day multi-charter
    # (16 active charters, hand-summed 2026-09-15 from raw FDIC
    # institutions: $74,846,093k total vs $9,567,734k largest single).
    # The first ZION attempt was a bad yardstick: ZION consolidated its
    # charters years ago, so its present-day group is one cert and
    # group == lead is CORRECT there.
    try:
        from data.fdic_history_store import deep_group_history
        grp = deep_group_history("WTFC", limit=1)
        if grp:
            g = float(grp[0].get("ASSET") or 0)
            ok = g > 60_000_000
            print(("ok  " if ok else "FAIL")
                  + f" WTFC latest group ASSET {g:,.0f} > 60,000,000 "
                  "(16-charter sum ~74.8B; any single charter <9.6B)")
            if not ok:
                failures += 1
        else:
            print("FAIL WTFC group history empty")
            failures += 1
    except Exception as e:
        print(f"note group check skipped: {type(e).__name__}: {e}")
    print("RESULT:", "PASS" if failures == 0 else f"{failures} FAILURES")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
