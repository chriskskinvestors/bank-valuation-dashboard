"""Full-universe coverage + correctness audit for the CECL allowance & asset-
quality extractor (data/sec_filing_scraper.extract_credit_quality), which feeds
the Company Reported -> Credit Quality / Allowance tab.

Reconcile-gated already (renders only with ACL + gross loans at the CURRENT date,
net + ACL tying gross). This is belt-and-suspenders across the universe:
  - coverage: how many banks render
  - IMPLAUSIBLE_ACL: ACL ÷ loans outside [0.2%, 6%] (a wrong concept grabbed)
  - ACL_OVER_LOANS: allowance exceeds gross loans (impossible)
  - IMPLAUSIBLE_NPL: nonaccrual ÷ loans > 25% (broken pairing / mistag)

Runs cache-free (instance_facts -> extract_credit_quality). SEC-throttled ~7 req/s.
Resumable. Output (gitignored): tools/_audit_credit_quality.jsonl + .md
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

from data.sec_filing_scraper import (instance_facts, extract_credit_quality,  # noqa: E402
                                     latest_filing)


def audit_one(cik):
    for forms in (("10-K",), ("10-Q",)):
        m = latest_filing(cik, forms)
        if not m:
            continue
        cq = extract_credit_quality(instance_facts(m))
        if not cq:
            continue
        period = max(cq)
        d = cq[period]
        rec = {"status": "ok", "period": period, "form": forms[0],
               "acl": d["acl"], "loans": d["loans_gross"],
               "acl_to_loans": d["acl_to_loans"], "npl": d["nonaccrual_to_loans"]}
        flags = []
        atl = d["acl_to_loans"]
        # 0.10% floor: NB Bancorp (NPB) faithfully reports a 0.17% allowance (pristine
        # newly-converted thrift, large fair-value loan book) — a real outlier, not a
        # mistag (gross − net ties ACL, corroborated by tiny charge-offs). Below ~0.1%
        # signals a wrong concept.
        if atl is not None and not (0.001 <= atl <= 0.06):
            flags.append(f"IMPLAUSIBLE_ACL({atl * 100:.2f}%)")
        if d["acl"] and d["loans_gross"] and abs(d["acl"]) > abs(d["loans_gross"]):
            flags.append("ACL_OVER_LOANS")
        npl = d["nonaccrual_to_loans"]
        if npl is not None and npl > 0.25:
            flags.append(f"IMPLAUSIBLE_NPL({npl * 100:.0f}%)")
        if flags:
            rec["flags"], rec["status"] = flags, "flag"
        return rec
    return {"status": "no-credit-quality"}


def main():
    bm = json.loads((ROOT / "data" / "bank_map_resolved.json").read_text())
    banks = sorted((t, d["cik"]) for t, d in bm.items() if d.get("cik"))
    jpath = ROOT / "tools" / "_audit_credit_quality.jsonl"
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
    md = ["# Full-universe credit-quality (ACL) audit", f"\nBanks: {len(recs)}\n",
          f"- rendered: {len(rendered)}", f"- flagged: {len(flagged)}",
          f"- no-credit-quality (honest n/a): {st.get('no-credit-quality', 0)}",
          f"- error: {st.get('error', 0)}", "", "## Flagged"]
    for r in flagged:
        md.append(f"- {r['ticker']}: {r.get('flags')}  acl/loans={r.get('acl_to_loans')}")
    (ROOT / "tools" / "_audit_credit_quality.md").write_text("\n".join(md))
    print(f"\nSTATUS {dict(st)} | rendered {len(rendered)} | flagged {len(flagged)}", flush=True)
    for r in flagged[:40]:
        print("  FLAG", r["ticker"], r.get("flags"))


if __name__ == "__main__":
    main()
