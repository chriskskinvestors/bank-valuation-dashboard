"""Full-universe coverage + correctness audit for the AFS/HTM debt-securities
bridge extractor (data/sec_filing_scraper.extract_securities), which feeds the
Company Reported -> Securities Portfolio tab.

The extractor is reconcile-gated (a portfolio renders only with a tagged amortized
cost AND fair value; the gain/loss split shows only when it ties the bridge), so
this is belt-and-suspenders. It checks across the universe:
  - coverage: how many banks render an AFS and/or HTM portfolio
  - IMPLAUSIBLE_UW: net-unrealized % of amortized cost outside [-45%, +20%]
    (a wrong amortized-cost or fair-value concept grabbed -> nonsensical bridge)
  - AC_OVER_ASSETS: amortized cost exceeds the filer's total assets (wrong concept)
  - SPLIT_BREAK: a portfolio flagged _reconciles=True whose split does NOT tie
    (must be 0 — the gate is supposed to prevent this)

Runs cache-free (instance_facts -> extract_securities). SEC-throttled ~7 req/s.
Resumable. Output (gitignored): tools/_audit_securities.jsonl + .md
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

from data.sec_filing_scraper import (instance_facts, extract_securities,  # noqa: E402
                                     latest_filing, _undimensioned_total)


def audit_one(cik):
    for forms in (("10-Q",), ("10-K",)):
        m = latest_filing(cik, forms)
        if not m:
            continue
        facts = instance_facts(m)
        sec = extract_securities(facts)
        if not sec:
            continue
        period = max(sec)
        assets = _undimensioned_total(facts, "Assets", period)
        rec = {"status": "ok", "period": period, "form": forms[0]}
        flags = []
        for port in ("afs", "htm"):
            d = sec[period].get(port)
            if not d:
                continue
            ac, fv, uw = d["amortized_cost"], d["fair_value"], d["underwater_pct"]
            rec[port] = {"ac": ac, "fv": fv, "uw": uw, "reconciles": d["_reconciles"]}
            if uw is not None and not (-0.45 <= uw <= 0.20):
                flags.append(f"IMPLAUSIBLE_UW_{port}({uw * 100:.0f}%)")
            if assets and ac and abs(ac) > abs(assets):
                flags.append(f"AC_OVER_ASSETS_{port}")
            if d["_reconciles"] and d["unrealized_gain"] is not None:
                if abs((ac + d["unrealized_gain"] - d["unrealized_loss"]) - fv) > max(abs(fv) * 0.01, 5e6):
                    flags.append(f"SPLIT_BREAK_{port}")
        if flags:
            rec["flags"], rec["status"] = flags, "flag"
        return rec
    return {"status": "no-securities"}


def main():
    bm = json.loads((ROOT / "data" / "bank_map_resolved.json").read_text())
    banks = sorted((t, d["cik"]) for t, d in bm.items() if d.get("cik"))
    jpath = ROOT / "tools" / "_audit_securities.jsonl"
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
    afs_n = sum(1 for r in rendered if r.get("afs"))
    htm_n = sum(1 for r in rendered if r.get("htm"))
    md = ["# Full-universe securities (AFS/HTM) audit", f"\nBanks: {len(recs)}\n",
          f"- rendered (AFS and/or HTM): {len(rendered)}",
          f"- AFS rendered: {afs_n}   HTM rendered: {htm_n}",
          f"- flagged: {len(flagged)}",
          f"- no-securities (honest n/a): {st.get('no-securities', 0)}",
          f"- error: {st.get('error', 0)}", "", "## Flagged"]
    for r in flagged:
        md.append(f"- {r['ticker']}: {r.get('flags')}")
    (ROOT / "tools" / "_audit_securities.md").write_text("\n".join(md))
    print(f"\nSTATUS {dict(st)} | rendered {len(rendered)} (AFS {afs_n}/HTM {htm_n}) | flagged {len(flagged)}", flush=True)
    for r in flagged[:40]:
        print("  FLAG", r["ticker"], r.get("flags"))


if __name__ == "__main__":
    main()
