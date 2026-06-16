"""Full-universe coverage + sanity audit for the business-segment extractor
(data/sec_filing_scraper.extract_segments), which feeds the Company Reported ->
Segment Reporting tab.

Each segment's net income is directly tagged and filtered to the OperatingSegments
consolidation member (totals/eliminations excluded); a residual reconciles them to
consolidated. This checks across the universe:
  - coverage: how many banks render (>=2 reportable segments)
  - LARGE_RESIDUAL: |consolidated - Σ reportable| exceeds |consolidated| — the
    reconciling residual dwarfs the total, suggesting a missed/double-counted
    segment, worth a look (a large but sub-100% corporate/other residual is normal)

Runs cache-free (instance_facts -> extract_segments, 10-K). SEC-throttled ~7 req/s.
Resumable. Output (gitignored): tools/_audit_segments.jsonl + .md
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import data.sec_filing_scraper as sfs  # noqa: E402

_MIN, _last, _orig = 1 / 7.0, [0.0], sfs._get


def _throttled(url, *a, **k):
    dt = time.time() - _last[0]
    if dt < _MIN:
        time.sleep(_MIN - dt)
    _last[0] = time.time()
    return _orig(url, *a, **k)


sfs._get = _throttled

from data.sec_filing_scraper import (instance_facts, extract_segments,  # noqa: E402
                                     latest_filing)


def audit_one(cik):
    m = latest_filing(cik, ("10-K",))
    if not m:
        return {"status": "no-filing"}
    sg = extract_segments(instance_facts(m))
    if not sg:
        return {"status": "no-segments"}
    period = max(sg)
    d = sg[period]
    consol = d["consolidated_net_income"]
    resid = d["reconciling_residual"]
    rec = {"status": "ok", "period": period, "n_segments": len(d["segments"]),
           "consol": consol, "residual": resid, "measure": d["ni_measure"]}
    flags = []
    if consol and abs(resid) > abs(consol):
        flags.append(f"LARGE_RESIDUAL(resid={resid / 1e6:.0f}M vs consol={consol / 1e6:.0f}M)")
    if flags:
        rec["flags"], rec["status"] = flags, "flag"
    return rec


def main():
    bm = json.loads((ROOT / "data" / "bank_map_resolved.json").read_text())
    banks = sorted((t, d["cik"]) for t, d in bm.items() if d.get("cik"))
    jpath = ROOT / "tools" / "_audit_segments.jsonl"
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
    md = ["# Full-universe segment-reporting audit", f"\nBanks: {len(recs)}\n",
          f"- rendered (>=2 segments): {len(rendered)}", f"- flagged: {len(flagged)}",
          f"- no-segments (single-segment / untagged): {st.get('no-segments', 0)}",
          f"- no-filing: {st.get('no-filing', 0)}", f"- error: {st.get('error', 0)}",
          "", "## Flagged"]
    for r in flagged:
        md.append(f"- {r['ticker']}: {r.get('flags')}")
    (ROOT / "tools" / "_audit_segments.md").write_text("\n".join(md))
    print(f"\nSTATUS {dict(st)} | rendered {len(rendered)} | flagged {len(flagged)}", flush=True)
    for r in flagged[:40]:
        print("  FLAG", r["ticker"], r.get("flags"))


if __name__ == "__main__":
    main()
