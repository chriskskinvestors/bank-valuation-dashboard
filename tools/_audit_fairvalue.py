"""Full-universe coverage + correctness audit for the recurring fair-value
hierarchy extractor (data/sec_filing_scraper.extract_fair_value), which feeds the
Company Reported -> Fair Value tab.

The extractor is structurally safe: `total` is the arithmetic sum of the actually
tagged L1/L2/L3 levels (a side with no clean level total is omitted = n/a, never
guessed). The thing to verify across the universe is therefore:
  - coverage: how many banks render an assets side at all
  - NONRECONCILE: a rendered side whose level sum does NOT tie the filer's tagged
    grand total within tolerance AND whose `netting` delta is large relative to
    the book -> suspicious (wrong total concept grabbed), worth a look. A modest
    netting on a derivatives dealer is legitimate, so we rank by |netting|/total.
  - IMPLAUSIBLE: an L3 percentage outside [0, 1] (would mean a bad level mix).

Runs cache-free (instance_facts -> extract_fair_value) so it reflects current
logic. SEC-throttled ~7 req/s. Resumable (skip-done + append).
Output (gitignored): tools/_audit_fairvalue.jsonl + tools/_audit_fairvalue.md
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import data.sec_filing_scraper as sfs  # noqa: E402

from tools._audit_common import install_throttle

# Polite ~7 req/s SEC throttle + a hard per-request timeout so a transient socket
# stall can't hang a full-universe run (see tools/_audit_common).
install_throttle(sfs)

from data.sec_filing_scraper import (instance_facts, extract_fair_value,  # noqa: E402
                                     latest_filing)


def audit_one(cik):
    # Mirror fair_value_for's form preference: timeliest 10-Q, then 10-K.
    for forms in (("10-Q",), ("10-K",)):
        m = latest_filing(cik, forms)
        if not m:
            continue
        fv = extract_fair_value(instance_facts(m))
        if fv:
            period = max(fv)
            sides = fv[period]
            rec = {"status": "ok", "period": period, "form": forms[0]}
            flags = []
            for side in ("assets", "liabilities"):
                d = sides.get(side)
                if not d:
                    continue
                rec[side] = {"total": d["total"], "grand": d.get("grand"),
                             "l3_pct": d.get("l3_pct"), "reconciles": d.get("_reconciles"),
                             "netting": d.get("netting")}
                l3p = d.get("l3_pct")
                if l3p is not None and not (0.0 <= l3p <= 1.0):
                    flags.append(f"IMPLAUSIBLE_L3PCT_{side}({l3p:.2f})")
                if (not d.get("_reconciles")) and d.get("netting") and d["total"]:
                    ratio = abs(d["netting"]) / abs(d["total"])
                    if ratio > 0.05:  # >5% of the book unexplained by netting -> look
                        flags.append(f"NONRECONCILE_{side}({ratio*100:.0f}%)")
            if flags:
                rec["flags"], rec["status"] = flags, "flag"
            return rec
    return {"status": "no-fairvalue"}


def main():
    bm = json.loads((ROOT / "data" / "bank_map_resolved.json").read_text())
    banks = sorted((t, d["cik"]) for t, d in bm.items() if d.get("cik"))
    jpath = ROOT / "tools" / "_audit_fairvalue.jsonl"
    done = set()
    if jpath.exists():
        for line in jpath.open():
            try:
                done.add(json.loads(line)["ticker"])
            except Exception:
                pass
    out = jpath.open("a")
    for i, (t, cik) in enumerate(banks):
        if t in done:
            continue
        try:
            r = audit_one(cik)
            r["ticker"], r["cik"] = t, cik
        except Exception as e:
            r = {"ticker": t, "cik": cik, "status": "error", "error": f"{type(e).__name__}: {e}"}
        out.write(json.dumps(r) + "\n")
        out.flush()
        if i % 10 == 0:
            print(f"[{i + 1}/{len(banks)}] {t}", flush=True)
    out.close()

    recs = [json.loads(line) for line in jpath.open()]
    st = Counter(r.get("status") for r in recs)
    flagged = [r for r in recs if r.get("status") == "flag"]
    rendered = [r for r in recs if r.get("status") in ("ok", "flag")]
    md = ["# Full-universe fair-value audit", f"\nBanks: {len(recs)}\n",
          f"- rendered (assets and/or liabilities): {len(rendered)}",
          f"- ok: {st.get('ok', 0)}", f"- flagged: {len(flagged)}",
          f"- no-fairvalue (honest n/a): {st.get('no-fairvalue', 0)}",
          f"- error: {st.get('error', 0)}", "", "## Flagged"]
    for r in flagged:
        md.append(f"- {r['ticker']}: {r.get('flags')}")
    (ROOT / "tools" / "_audit_fairvalue.md").write_text("\n".join(md))
    print(f"\nSTATUS {dict(st)}  | rendered {len(rendered)} | flagged {len(flagged)}", flush=True)
    for r in flagged[:40]:
        print("  FLAG", r["ticker"], r.get("flags"))


if __name__ == "__main__":
    main()
