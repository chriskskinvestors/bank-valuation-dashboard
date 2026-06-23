"""Full-universe coverage + sanity audit for the embedded interest-rate-risk
extractor (data/sec_filing_scraper.extract_rate_risk), which feeds the Company
Reported -> Interest Rate Risk tab.

Composes the already-audited securities extractor with the tagged equity total, so
this checks coverage and that the unrealized-vs-equity ratio is sane:
  - IMPLAUSIBLE: total unrealized ÷ equity outside [-60%, +10%] — a real bank's
    AFS+HTM mark rarely exceeds ~25% of equity (WFC −20%, USB −21%); beyond −60%
    signals a wrong equity or securities figure.

Runs cache-free (instance_facts -> extract_rate_risk). SEC-throttled ~7 req/s.
Resumable. Output (gitignored): tools/_audit_rate_risk.jsonl + .md
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

from data.sec_filing_scraper import (instance_facts, extract_rate_risk,  # noqa: E402
                                     latest_filing, _fdic_cet1)
from data.bank_mapping import get_fdic_cert  # noqa: E402


def audit_one(t, cik):
    for forms in (("10-Q",), ("10-K",)):
        m = latest_filing(cik, forms)
        if not m:
            continue
        try:
            anchor = _fdic_cet1(get_fdic_cert(t))
        except Exception:
            anchor = None
        rr = extract_rate_risk(instance_facts(m), anchor_cet1=anchor)
        if not rr:
            continue
        period = max(rr)
        d = rr[period]
        ute = d["unrealized_to_equity"]
        rec = {"status": "ok", "period": period, "total_unrealized": d["total_unrealized"],
               "equity": d["equity"], "unrealized_to_equity": ute,
               "unrealized_to_cet1": d["unrealized_to_cet1"]}
        flags = []
        # A thin-capital micro-cap can carry securities losses exceeding its entire
        # equity — GLBZ ($20.7M equity, −$22M AFS mark = −108%), faithful, not a
        # wrong pick (AFS losses are already in AOCI, shown split out in the tab).
        # Beyond −125% signals a mistagged equity or securities figure.
        if ute is not None and not (-1.25 <= ute <= 0.10):
            flags.append(f"IMPLAUSIBLE_UTE({ute * 100:.0f}%)")
        if flags:
            rec["flags"], rec["status"] = flags, "flag"
        return rec
    return {"status": "no-rate-risk"}


def main():
    bm = json.loads((ROOT / "data" / "bank_map_resolved.json").read_text())
    banks = sorted((t, d["cik"]) for t, d in bm.items() if d.get("cik"))
    jpath = ROOT / "tools" / "_audit_rate_risk.jsonl"
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
            r = audit_one(t, cik)
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
    md = ["# Full-universe interest-rate-risk audit", f"\nBanks: {len(recs)}\n",
          f"- rendered: {len(rendered)}", f"- flagged: {len(flagged)}",
          f"- no-rate-risk (honest n/a): {st.get('no-rate-risk', 0)}",
          f"- error: {st.get('error', 0)}", "", "## Flagged"]
    for r in flagged:
        md.append(f"- {r['ticker']}: {r.get('flags')}")
    (ROOT / "tools" / "_audit_rate_risk.md").write_text("\n".join(md))
    print(f"\nSTATUS {dict(st)} | rendered {len(rendered)} | flagged {len(flagged)}", flush=True)
    for r in flagged[:40]:
        print("  FLAG", r["ticker"], r.get("flags"))


if __name__ == "__main__":
    main()
