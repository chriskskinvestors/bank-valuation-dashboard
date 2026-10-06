"""Why does a bank have no P/TBV, P/E or dividend yield? — coverage audit.

The Home page's sector medians count only banks with a REAL value
(2026-10-06: P/TBV n=330, P/E n=291, yield n=164 of 597). This classifies
every bank in the persisted metrics snapshot (`watchlist_metrics_snap`, the
same rows every page reads) by the FIRST precondition that failed, in the
order the valuation engine checks them — so a bank is counted once, under
the reason that actually blanked its figure. Read-only.

Run against prod from the "Run Cloud Run Job" workflow with
  job = live-audit, args = -m,tools.valuation_coverage
(any job whose container has the DB env works; the metrics snapshot lives
in Postgres). Locally it reads the local SQLite cache.
"""
from __future__ import annotations
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

EXAMPLES = 12


def classify(row: dict, cik, sec: dict | None) -> dict[str, str]:
    """{stat: reason} for ptbv / pe / divyield; reason "ok" when present."""
    out = {}
    price = row.get("price")
    no_price = "no price (no quote, or frozen >5d and dropped)"

    tb = row.get("tbvps")
    if row.get("ptbv_ratio") is not None:
        out["ptbv"] = "ok"
    elif price is None:
        out["ptbv"] = no_price
    elif tb is None:
        if not cik:
            out["ptbv"] = "non-SEC filer and no TBV in its wire release"
        elif not sec:
            out["ptbv"] = "SEC filer but companyfacts empty/unavailable"
        elif sec.get("preferred_present") and sec.get("preferred_stock") is None:
            out["ptbv"] = "preferred outstanding, value unresolved (n/a by rule)"
        elif sec.get("shares_outstanding") is None:
            out["ptbv"] = "share count unresolved (share/equity incoherent)"
        elif sec.get("book_value_total") is None:
            out["ptbv"] = "equity total not tagged (or NCI unseparated)"
        else:
            out["ptbv"] = "TBV n/a — other (intangibles untagged at date?)"
    elif tb <= 0:
        out["ptbv"] = "negative tangible book"
    else:
        out["ptbv"] = "other"

    eps = row.get("eps")
    if row.get("pe_ratio") is not None:
        out["pe"] = "ok"
    elif price is None:
        out["pe"] = no_price
    elif eps is None:
        out["pe"] = ("non-SEC filer (no XBRL EPS)" if not cik
                     else "no 12-month EPS derivable (TTM window broken)")
    elif eps <= 0:
        out["pe"] = "trailing loss (EPS <= 0)"
    else:
        out["pe"] = "other"

    if row.get("dividend_yield") is not None:
        out["divyield"] = "ok"
    elif price is None:
        out["divyield"] = no_price
    elif not cik:
        out["divyield"] = "non-SEC filer (no XBRL dividends)"
    else:
        out["divyield"] = "no declared-dividend tag in the last four quarters"
    return out


def run(rows: list[dict], cik_of, sec_of) -> int:
    counts = {s: Counter() for s in ("ptbv", "pe", "divyield")}
    examples = {s: defaultdict(list) for s in counts}
    for r in rows:
        t = r.get("ticker")
        cik = cik_of(t)
        sec = sec_of(t) if cik else None
        for stat, reason in classify(r, cik, sec).items():
            counts[stat][reason] += 1
            if reason != "ok" and len(examples[stat][reason]) < EXAMPLES:
                examples[stat][reason].append(t)
    n = len(rows)
    print(f"metrics snapshot: {n} banks")
    for stat, label in (("ptbv", "P/TBV"), ("pe", "P/E"), ("divyield", "Dividend yield")):
        ok = counts[stat].get("ok", 0)
        print(f"\n{label}: {ok}/{n} have a value")
        for reason, c in counts[stat].most_common():
            if reason == "ok":
                continue
            ex = ", ".join(examples[stat][reason])
            print(f"  {c:>4}  {reason}")
            print(f"        e.g. {ex}")
    return 0


def main() -> int:
    import warnings
    warnings.filterwarnings("ignore")
    from data import cache
    from data.bank_mapping import get_cik
    snap = cache.get("watchlist_metrics_snap", max_age_s=None)
    rows = (snap or {}).get("metrics") or []
    if not rows:
        print("no metrics snapshot in this store")
        return 2
    print(f"snapshot built {snap.get('cached_at')}")

    def _cik(t):
        try:
            return get_cik(t)
        except Exception:
            return None

    return run(rows, _cik, cache.get_sec)


if __name__ == "__main__":
    sys.exit(main())
