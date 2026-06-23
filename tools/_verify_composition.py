"""Full-universe coverage + correctness harness for the as-reported loan/deposit
COMPOSITION engine (data/sec_composition.py).

Runs the loan and deposit composition reconstruction across every SEC-mapped bank
(data/bank_map_resolved.json `cik`) and, for every RENDERED composition, RE-CHECKS
that the rows actually sum to the disclosed total within 1%. The engine itself is
reconcile-gated, so this is a belt-and-suspenders verification: the headline
number that MUST be zero is `nonreconciling` — a rendered composition whose rows
do not tie to its total. Anything that can't be reconstructed cleanly is `na`
(the honest result), never a wrong number.

Per bank x kind it records: ok (reconciles), na (engine returned nothing),
nonreconciling (rendered but rows != total — a BUG), or error (exception).

Output (gitignored, underscore-prefixed -> tools/_*.json is in .gitignore):
  tools/_verify_composition.jsonl  — one row per (ticker, kind)
  tools/_verify_composition.md     — summary + the n/a and nonreconciling lists

SEC-throttled to ~7 req/s. RESUMABLE: skip (ticker, kind) pairs already recorded
and append the rest, so a crash / machine-sleep doesn't lose progress and a re-run
finishes the remainder. Optional argv: ticker filter (substring) or a max count.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Polite ~7 req/s SEC throttle + a hard per-request timeout so a transient socket
# stall can't hang a full-universe run (see tools/_audit_common).
import data.sec_filing_scraper as sfs  # noqa: E402
from tools._audit_common import install_throttle

_throttled = install_throttle(sfs)
# sec_composition imported _get by name at module load — rebind that reference too.
import data.sec_composition as sc  # noqa: E402

sc._get = _throttled

KINDS = ("loan", "deposit")
JPATH = ROOT / "tools" / "_verify_composition.jsonl"
MDPATH = ROOT / "tools" / "_verify_composition.md"
TOL = 0.01


def _check(result):
    """(status, total, n_rows, sum_rows) for an engine result."""
    if not result:
        return "na", None, 0, None
    _p, d = next(iter(result["composition"].items()))
    total = d["total"]
    s = sum(v for _label, v in d["rows"])
    ok = total and abs(s - total) / abs(total) <= TOL
    return ("ok" if ok else "nonreconciling"), total, len(d["rows"]), s


def _load_done():
    done = set()
    if JPATH.exists():
        for line in JPATH.open():
            try:
                r = json.loads(line)
                done.add((r["ticker"], r["kind"]))
            except Exception:
                pass
    return done


def _write_summary():
    """Aggregate the FULL jsonl (existing + appended) into the markdown report."""
    agg = {k: {"ok": 0, "na": 0, "nonreconciling": 0, "error": 0} for k in KINDS}
    na_list = {k: [] for k in KINDS}
    bad_list = {k: [] for k in KINDS}
    seen = set()
    for line in JPATH.open():
        try:
            r = json.loads(line)
        except Exception:
            continue
        k, st = r.get("kind"), r.get("status")
        if k not in agg:
            continue
        seen.add(r["ticker"])
        agg[k][st] = agg[k].get(st, 0) + 1
        if st == "na":
            na_list[k].append(r["ticker"])
        elif st == "nonreconciling":
            bad_list[k].append(f"{r['ticker']} (total={r.get('total')}, sum={r.get('sum_rows')})")
    md = ["# Composition engine — full-universe coverage & reconcile audit", ""]
    for k in KINDS:
        a = agg[k]
        rendered = a["ok"] + a["nonreconciling"]
        denom = rendered + a["na"] + a["error"] or 1
        rate = 100.0 * a["ok"] / denom
        md += [f"## {k}",
               f"- rendered & reconcile (ok): **{a['ok']}**",
               f"- n/a (no clean disclosure): {a['na']}",
               f"- NON-RECONCILING (must be 0): **{a['nonreconciling']}**",
               f"- error: {a['error']}",
               f"- reconcile-rate over attempted: **{rate:.1f}%**", ""]
        if bad_list[k]:
            md += ["### NON-RECONCILING (investigate — should be empty):"] + \
                  [f"- {x}" for x in bad_list[k]] + [""]
    for k in KINDS:
        md += [f"### {k} n/a banks ({len(na_list[k])}):", ", ".join(sorted(na_list[k])), ""]
    MDPATH.write_text("\n".join(md), encoding="utf-8")
    return md


def main():
    bm = json.loads((ROOT / "data" / "bank_map_resolved.json").read_text(encoding="utf-8"))
    banks = sorted((t, d["cik"]) for t, d in bm.items() if d.get("cik"))
    # optional argv: substring filter and/or an integer cap (for a quick sample)
    cap = None
    for a in sys.argv[1:]:
        if a.isdigit():
            cap = int(a)
        else:
            banks = [(t, c) for t, c in banks if a.upper() in t.upper()]
    if cap:
        banks = banks[:cap]

    done = _load_done()
    jsonl = JPATH.open("a", encoding="utf-8")
    n_bad = 0
    for i, (t, cik) in enumerate(banks):
        todo = [k for k in KINDS if (t, k) not in done]
        if not todo:
            continue
        # ONE filing fetch serves both loan and deposit (compositions_for).
        try:
            both = sc.compositions_for(cik) or {}
        except Exception as e:
            both = {"_error": f"{type(e).__name__}: {e}"}
        for kind in todo:
            rec = {"ticker": t, "cik": cik, "kind": kind}
            if "_error" in both:
                rec.update(status="error", error=both["_error"])
            else:
                result = {"composition": both[kind]} if both.get(kind) else None
                status, total, nrows, srows = _check(result)
                rec.update(status=status, total=total, n_rows=nrows, sum_rows=srows)
                if status == "nonreconciling":
                    n_bad += 1
            jsonl.write(json.dumps(rec) + "\n")
            jsonl.flush()
        if i % 10 == 0:
            print(f"[{i + 1}/{len(banks)}] {t}  (nonreconciling so far this run: {n_bad})",
                  flush=True)
    jsonl.close()
    md = _write_summary()
    print("\n" + "\n".join(md), flush=True)


if __name__ == "__main__":
    main()
