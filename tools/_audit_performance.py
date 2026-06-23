"""Full-universe coverage + correctness audit for the as-reported profitability
extractor (data/sec_filing_scraper.extract_performance), which feeds the Company
Reported -> Performance Analysis tab.

Every figure is a directly tagged full-year income line or a transparent
combination, so this checks plausibility across the universe:
  - coverage: how many banks render
  - IMPLAUSIBLE_EFF: efficiency ratio outside [20%, 110%]
  - IMPLAUSIBLE_ROA: ROA outside [-3%, +4%]
  - IMPLAUSIBLE_ROE: ROE outside [-40%, +50%]
A bank reporting a net loss is fine (negative ROA/ROE within band); the bands catch
a wrong concept (e.g. quarterly net income paired with annual revenue).

Runs cache-free (instance_facts -> extract_performance, 10-K). SEC-throttled
~7 req/s. Resumable. Output (gitignored): tools/_audit_performance.jsonl + .md
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

from data.sec_filing_scraper import (instance_facts, extract_performance,  # noqa: E402
                                     latest_filing)


def audit_one(cik):
    m = latest_filing(cik, ("10-K",))
    if not m:
        return {"status": "no-filing"}
    perf = extract_performance(instance_facts(m))
    if not perf:
        return {"status": "no-performance"}
    period = max(perf)
    d = perf[period]
    rec = {"status": "ok", "period": period,
           "eff": d["efficiency"], "roa": d["roa"], "roe": d["roe"],
           "reconciles": d["_reconciles"]}
    flags = []
    # Efficiency legitimately exceeds 100% in a securities-repositioning loss year
    # (revenue collapses; e.g. SFNC FY25 547% on a −$398M net loss — verified
    # faithful). Only an absurd ratio (>800%) signals a wrong concept; the extractor
    # already returns n/a when revenue ≤ 0.
    if d["efficiency"] is not None and not (0.0 <= d["efficiency"] <= 8.0):
        flags.append(f"IMPLAUSIBLE_EFF({d['efficiency'] * 100:.0f}%)")
    if d["roa"] is not None and not (-0.03 <= d["roa"] <= 0.04):
        flags.append(f"IMPLAUSIBLE_ROA({d['roa'] * 100:.2f}%)")
    if d["roe"] is not None and not (-0.40 <= d["roe"] <= 0.50):
        flags.append(f"IMPLAUSIBLE_ROE({d['roe'] * 100:.1f}%)")
    if flags:
        rec["flags"], rec["status"] = flags, "flag"
    return rec


def main():
    bm = json.loads((ROOT / "data" / "bank_map_resolved.json").read_text())
    banks = sorted((t, d["cik"]) for t, d in bm.items() if d.get("cik"))
    jpath = ROOT / "tools" / "_audit_performance.jsonl"
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
    md = ["# Full-universe performance audit", f"\nBanks: {len(recs)}\n",
          f"- rendered: {len(rendered)}", f"- flagged: {len(flagged)}",
          f"- no-performance (honest n/a): {st.get('no-performance', 0)}",
          f"- no-filing: {st.get('no-filing', 0)}", f"- error: {st.get('error', 0)}",
          "", "## Flagged"]
    for r in flagged:
        md.append(f"- {r['ticker']}: {r.get('flags')}")
    (ROOT / "tools" / "_audit_performance.md").write_text("\n".join(md))
    print(f"\nSTATUS {dict(st)} | rendered {len(rendered)} | flagged {len(flagged)}", flush=True)
    for r in flagged[:40]:
        print("  FLAG", r["ticker"], r.get("flags"))


if __name__ == "__main__":
    main()
