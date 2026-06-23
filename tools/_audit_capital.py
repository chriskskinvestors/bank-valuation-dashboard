"""Full-universe capital audit — every SEC-mapped bank's holdco regulatory-capital
extraction, checked for CORRECTNESS (the wrong-number flags) and coverage.

Runs the FIXED extraction cache-free (instance_facts -> extract_holdco_capital) so
it reflects the current logic, not stale cache. Resumable (skip-done + append) and
SEC-throttled (~7 req/s) so a crash / machine-sleep can't lose progress.

Flags:
  INCONSISTENT  cet1_cap / RWA disagrees with the tagged CET1 ratio (>0.15pp) -> real wrong number
  IMPLAUSIBLE   a ratio outside [3%, 40%]
  FDIC_DIVERGE  holdco CET1 differs from FDIC bank-sub CET1 by >3pp (review: wrong entity/methodology?)
Coverage (honest n/a, not bugs):
  missing-CET1  filer tags no CET1 ratio;  no-capital  filer tags no capital at all
Output: tools/_audit_capital.jsonl (one row/bank) + tools/_audit_capital.md
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

from data.sec_filing_scraper import (instance_facts, extract_holdco_capital,  # noqa: E402
                                     latest_filing, _fdic_cet1)
from data.bank_mapping import get_fdic_cert  # noqa: E402


def audit_one(cik, cert):
    m = latest_filing(cik, ("10-K",))
    if not m:
        return {"status": "no-filing"}
    facts = instance_facts(m)
    anchor = _fdic_cet1(cert)
    d = extract_holdco_capital(facts, anchor_cet1=anchor)
    if not d:
        return {"status": "no-capital", "facts": len(facts)}
    pd = d[max(d)]
    cr, cc, rwa = pd.get("cet1_ratio"), pd.get("cet1_cap"), pd.get("rwa")
    rec = {"status": "ok", "cet1": cr, "t1": pd.get("t1_ratio"),
           "total": pd.get("total_ratio"), "lev": pd.get("lev_ratio"),
           "cblr": pd.get("_cblr", False), "basis": pd.get("_basis", "holdco"),
           "anchor": anchor}
    flags = []
    if cr is None and not pd.get("_cblr"):
        flags.append("missing-CET1")
    if cr is not None:
        if not (0.03 <= cr <= 0.40):
            flags.append("IMPLAUSIBLE")
        if cc and rwa and abs(cc / rwa * 100 - cr * 100) > 0.15:
            flags.append("INCONSISTENT")
        if anchor and abs(cr * 100 - anchor) > 3.0 and pd.get("_basis") != "bank":
            flags.append(f"FDIC_DIVERGE({cr * 100:.1f}vs{anchor:.1f})")
    if flags:
        rec["flags"], rec["status"] = flags, "flag"
    return rec


def main():
    bm = json.loads((ROOT / "data" / "bank_map_resolved.json").read_text())
    banks = sorted((t, d["cik"]) for t, d in bm.items() if d.get("cik"))
    jpath = ROOT / "tools" / "_audit_capital.jsonl"
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
            r = audit_one(cik, get_fdic_cert(t))
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
    hard = [r for r in flagged if any("INCONSISTENT" in f or "IMPLAUSIBLE" in f for f in r.get("flags", []))]
    md = ["# Full-universe capital audit", f"\nBanks: {len(recs)}\n",
          f"- ok: {st.get('ok', 0)}", f"- flagged: {len(flagged)}  (hard wrong-number: {len(hard)})",
          f"- no-capital: {st.get('no-capital', 0)}", f"- error: {st.get('error', 0)}", "", "## Flagged"]
    for r in flagged:
        md.append(f"- {r['ticker']}: {r.get('flags')}  cet1={r.get('cet1')}")
    (ROOT / "tools" / "_audit_capital.md").write_text("\n".join(md))
    print(f"\nSTATUS {dict(st)}  | flagged {len(flagged)} | HARD wrong-number {len(hard)}", flush=True)
    for r in hard[:40]:
        print("  HARD", r["ticker"], r.get("flags"))


if __name__ == "__main__":
    main()
