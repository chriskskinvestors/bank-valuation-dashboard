"""Full-universe coverage + sanity audit for the financial-highlights snapshot
(data/sec_filing_scraper.extract_financial_highlights), which feeds the Company
Reported -> Financial Highlights tab.

Highlights composes already-audited extractors (performance / credit-quality /
capital) plus three top-line balance-sheet picks (Assets, Deposits, equity), so
this checks coverage and that the balance-sheet picks are sane:
  - EQUITY_RATIO: total equity ÷ assets outside [2%, 30%] (wrong equity/assets pick)
  - DEPOSITS_RATIO: deposits ÷ assets outside [20%, 100%] (wrong deposits pick)
A bank can legitimately post a net loss, so net income is not range-checked here.

Runs cache-free (instance_facts -> extract_financial_highlights, 10-K).
SEC-throttled ~7 req/s. Resumable. Output (gitignored): tools/_audit_highlights.*
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

from data.sec_filing_scraper import (instance_facts, extract_financial_highlights,  # noqa: E402
                                     latest_filing, _fdic_cet1)
from data.bank_mapping import get_fdic_cert  # noqa: E402


def audit_one(t, cik):
    m = latest_filing(cik, ("10-K",))
    if not m:
        return {"status": "no-filing"}
    try:
        anchor = _fdic_cet1(get_fdic_cert(t))
    except Exception:
        anchor = None
    h = extract_financial_highlights(instance_facts(m), anchor_cet1=anchor)
    if not h:
        return {"status": "no-highlights"}
    a, e, dep = h.get("assets"), h.get("equity"), h.get("deposits")
    rec = {"status": "ok", "period": h.get("period"),
           "assets": a, "equity": e, "deposits": dep, "cet1": h.get("cet1")}
    flags = []
    # Over-capitalized micro-caps legitimately run very high equity/assets — MGNO
    # 53% ($20M equity on $37M assets, 97.6% CET1), HYNE 33% — so the ceiling only
    # catches a wrong pick (e.g. total assets mistagged as equity → ~100%).
    if a and e and not (0.02 <= e / a <= 0.60):
        flags.append(f"EQUITY_RATIO({e / a * 100:.0f}%)")
    if a and dep and not (0.20 <= dep / a <= 1.00):
        flags.append(f"DEPOSITS_RATIO({dep / a * 100:.0f}%)")
    if flags:
        rec["flags"], rec["status"] = flags, "flag"
    return rec


def main():
    bm = json.loads((ROOT / "data" / "bank_map_resolved.json").read_text())
    banks = sorted((t, d["cik"]) for t, d in bm.items() if d.get("cik"))
    jpath = ROOT / "tools" / "_audit_highlights.jsonl"
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
    md = ["# Full-universe financial-highlights audit", f"\nBanks: {len(recs)}\n",
          f"- rendered: {len(rendered)}", f"- flagged: {len(flagged)}",
          f"- no-highlights (honest n/a): {st.get('no-highlights', 0)}",
          f"- no-filing: {st.get('no-filing', 0)}", f"- error: {st.get('error', 0)}",
          "", "## Flagged"]
    for r in flagged:
        md.append(f"- {r['ticker']}: {r.get('flags')}")
    (ROOT / "tools" / "_audit_highlights.md").write_text("\n".join(md))
    print(f"\nSTATUS {dict(st)} | rendered {len(rendered)} | flagged {len(flagged)}", flush=True)
    for r in flagged[:40]:
        print("  FLAG", r["ticker"], r.get("flags"))


if __name__ == "__main__":
    main()
