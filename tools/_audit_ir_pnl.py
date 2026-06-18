"""Full-universe correctness audit for the IR earnings-release P&L extractor
(data.ir_provider.extract_pnl): total net income + diluted EPS.

Ground truth = the EVENTUAL filing's iXBRL facts. For the release's own quarter,
the matching 10-Q/10-K carries the quarterly `NetIncomeLoss` and
`EarningsPerShareDiluted` facts (undimensioned, ~3-month duration ending at the
quarter-end). Where that quarter is already filed, the IR value must MATCH:
- net income within 3% (the prose figure is rounded to 1 decimal of $bn/$mn, so a
  faithful value rounds close; a WRONG grab — revenue, prior period — is far off);
- diluted EPS within $0.015 (both are stated to the cent).
A MISMATCH must be 0 before the extractor is wired to any display. Releases
fresher than the latest filing have no same-quarter fact → FRESH (recorded, not
verifiable — net income swings too much QoQ for a plausibility check).

Output gitignored + resumable; SEC-throttled. IR_AUDIT_LIMIT=N to sample.
Run: python -m tools._audit_ir_pnl
"""
import json
import os
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.ir_provider import latest_earnings_release, extract_pnl
from data.bank_mapping import get_cik
from data.bank_universe import get_universe_tickers
from data.sec_filing_scraper import latest_filing, instance_facts

OUT = Path(__file__).parent / "_audit_ir_pnl.jsonl"
_QENDS = [(3, 31), (6, 30), (9, 30), (12, 31)]
_NI_TOL = 0.03      # relative
_EPS_TOL = 0.015    # absolute $


def _period_from_filed(filed_iso: str) -> str | None:
    try:
        y, m, d = (int(x) for x in filed_iso.split("-"))
        filed = date(y, m, d)
    except Exception:
        return None
    cands = [date(yy, qm, qd) for yy in (y, y - 1) for (qm, qd) in _QENDS
             if date(yy, qm, qd) < filed]
    return max(cands).isoformat() if cands else None


def _dur(f):
    try:
        return (date.fromisoformat(f.period_end) - date.fromisoformat(f.period_start)).days
    except Exception:
        return None


def _quarterly(facts, suffix, qend):
    """Undimensioned ~quarterly fact for `suffix` ending exactly at qend."""
    for f in facts:
        if (f.concept.endswith(suffix) and not f.members and f.period_end == qend
                and (_dur(f) or 0) >= 80 and (_dur(f) or 0) <= 100):
            try:
                return float(f.value)
            except (TypeError, ValueError):
                return None
    return None


def _classify(ir, facts, qend):
    have = {k: v for k, v in ir.items() if v is not None}
    if not have:
        return "NA", {}
    if facts is None or qend is None:
        return "NO_GROUNDTRUTH", {"ir": have}
    fni = _quarterly(facts, "NetIncomeLoss", qend)
    feps = _quarterly(facts, "EarningsPerShareDiluted", qend)
    if fni is None and feps is None:
        return "FRESH", {"ir": have, "qend": qend}  # quarter not yet filed
    info, breaks = {"qend": qend}, []
    if ir["net_income"] is not None and fni:
        rel = abs(ir["net_income"] - fni) / abs(fni)
        info["ni"] = {"ir": round(ir["net_income"] / 1e6, 1), "filed": round(fni / 1e6, 1),
                      "rel%": round(rel * 100, 2)}
        if rel > _NI_TOL:
            breaks.append("net_income")
    if ir["diluted_eps"] is not None and feps is not None:
        d = abs(ir["diluted_eps"] - feps)
        info["eps"] = {"ir": ir["diluted_eps"], "filed": feps, "d": round(d, 3)}
        if d > _EPS_TOL:
            breaks.append("diluted_eps")
    if breaks:
        info["break"] = breaks
        return "MISMATCH", info
    return "MATCH", info


def main() -> int:
    done = set()
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            try:
                done.add(json.loads(line)["ticker"])
            except Exception:
                pass
    tickers = [t for t in get_universe_tickers() if t not in done]
    limit = int(os.environ.get("IR_AUDIT_LIMIT", "0") or 0)
    if limit:
        tickers = tickers[:limit]
    print(f"[ir-pnl] {len(tickers)} to audit ({len(done)} done)", flush=True)

    counts = Counter()
    with OUT.open("a") as fh:
        for i, tk in enumerate(tickers, 1):
            cik = get_cik(tk)
            row = {"ticker": tk}
            if not cik:
                row["verdict"] = "NO_CIK"
            else:
                try:
                    res = latest_earnings_release(cik)
                    if not res:
                        row["verdict"] = "NO_RELEASE"
                    else:
                        ir = extract_pnl(res["html"])
                        qend = _period_from_filed(res.get("filed_date", ""))
                        facts = None
                        meta = latest_filing(cik, ("10-Q", "10-K"))
                        if meta:
                            facts = instance_facts(meta)
                        verdict, info = _classify(ir, facts, qend)
                        row.update({"verdict": verdict, "filed": res.get("filed_date"), **info})
                except Exception as e:
                    row["verdict"] = f"ERR:{type(e).__name__}"
            counts[row["verdict"].split(":")[0]] += 1
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            if row["verdict"] == "MISMATCH":
                print(f"  [MISMATCH] {tk}: {row}", flush=True)
            if i % 20 == 0:
                print(f"  {i}/{len(tickers)}  {dict(counts)}", flush=True)
            time.sleep(0.12)

    print("\n" + "=" * 60)
    print("IR P&L AUDIT —", dict(counts))
    print(f"  MISMATCH (MUST be 0): {counts['MISMATCH']}")
    print("=" * 60)
    return 1 if counts["MISMATCH"] else 0


if __name__ == "__main__":
    sys.exit(main())
