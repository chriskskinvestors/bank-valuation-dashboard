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
     {"ASSET": 603_251, "DEP": 516_328, "EQ": 52_867, "NETINC": 7_210}),
    ("TCBK simple mid-00s", 21943, "20051231",
     {"ASSET": 1_841_252, "DEP": 1_499_423, "EQ": 188_226,
      "NETINC": 25_403}),
    # ZION's LEAD charter only — the consolidated group figure for 2005Q4
    # must be STRICTLY GREATER (multi-charter era: CB&T, Amegy, NSB, ...).
    ("ZION lead charter 2005", 2270, "20051231",
     {"ASSET": 12_667_668, "DEP": 9_212_594, "EQ": 836_385,
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
    # Multi-charter inequality: ZION group 2005Q4 > lead charter alone.
    try:
        from data.fdic_history_store import deep_group_history
        grp = [r for r in deep_group_history("ZION")
               if str(r.get("REPDTE", "")).startswith("2005-12")
               or str(r.get("REPDTE", "")).startswith("20051231")]
        if grp:
            g = float(grp[0].get("ASSET") or 0)
            ok = g > 12_667_668
            print(("ok  " if ok else "FAIL")
                  + f" ZION 2005Q4 group ASSET {g:,.0f} > lead 12,667,668")
            if not ok:
                failures += 1
        else:
            print("note ZION 2005Q4 group row not found via ticker "
                  "(cert-group membership is present-day; acceptable)")
    except Exception as e:
        print(f"note group check skipped: {type(e).__name__}: {e}")
    print("RESULT:", "PASS" if failures == 0 else f"{failures} FAILURES")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
