"""Full-universe correctness audit for the IR earnings-release capital-ratio
extractor (data.ir_provider.extract_capital_ratios), the freshest source layer.

The hard part of auditing the freshest layer is that, by definition, nothing else
has reported the release's quarter yet. The trick: use the EVENTUAL filing as
ground truth. holdco_capital_for() returns the 10-K/10-Q capital ratios per
period. So:

  - STRICT (the proof that matters): if the 10-Q for the release's own quarter is
    already filed (the inferred release period is present in holdco_capital_for),
    the IR-extracted CET1 must MATCH it to <=6bp. A `MISMATCH` here is a genuine
    extraction error and MUST be 0 before the extractor is wired to any display.
  - FRESH: if the release is newer than the latest filing (no matching period),
    the IR value is plausibility-checked against the latest filed ratio (capital
    ratios move slowly). >1.5pp divergence -> FRESH_REVIEW (could be real M&A,
    e.g. FITB/Comerica, or a parse error — eyeball these).
  - NA_UNCONFIRMED: the in-document CET1 cross-check wasn't satisfied, so the
    extractor returned n/a. This is the safe outcome, not an error.

Output is gitignored + resumable; SEC-throttled. Set IR_AUDIT_LIMIT=N to sample.
Run: python -m tools._audit_ir_capital
"""
import json
import os
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.ir_provider import latest_earnings_release, extract_capital_ratios
from data.bank_mapping import get_cik, get_fdic_cert
from data.bank_universe import get_universe_tickers
from data.sec_filing_scraper import holdco_capital_for

OUT = Path(__file__).parent / "_audit_ir_capital.jsonl"
_QENDS = [(3, 31), (6, 30), (9, 30), (12, 31)]


def _period_from_filed(filed_iso: str) -> str | None:
    """The quarter-end the release covers — the most recent quarter-end strictly
    before the filing date (releases land ~2-4 weeks after quarter close)."""
    try:
        y, m, d = (int(x) for x in filed_iso.split("-"))
        filed = date(y, m, d)
    except Exception:
        return None
    cands = [date(yy, qm, qd) for yy in (y, y - 1) for (qm, qd) in _QENDS
             if date(yy, qm, qd) < filed]
    return max(cands).isoformat() if cands else None


_METRICS = ("cet1_ratio", "t1_ratio", "total_ratio", "lev_ratio")


def _compare(ir: dict, filed: dict, tol: float) -> tuple[list, dict]:
    """Per-metric diffs (IR percent vs filed fraction×100), and the list of
    metrics that break `tol`. Only metrics present (non-None) in BOTH sides are
    compared — a metric the filing doesn't tag can't be ground-truthed here."""
    diffs, breaks = {}, []
    for m in _METRICS:
        iv, fv = ir.get(m), filed.get(m)
        if iv is None or fv is None:
            continue
        g = fv * 100
        d = round(iv - g, 3)
        diffs[m] = {"ir": iv, "filed": round(g, 2), "d": d}
        if abs(iv - g) > tol:
            breaks.append(m)
    return breaks, diffs


def _classify(ir: dict, cap: dict | None, period: str | None) -> tuple[str, dict]:
    if ir.get("cet1_ratio") is None:
        return "NA_UNCONFIRMED", {}
    if not cap:
        return "NO_GROUNDTRUTH", {"ir": {k: ir[k] for k in _METRICS if ir.get(k) is not None}}
    # STRICT: the release's own quarter is already filed → every comparable ratio
    # must match to <=6bp. A break on ANY metric is a MISMATCH (record which).
    if period and period in cap:
        breaks, diffs = _compare(ir, cap[period], 0.06)
        info = {"period": period, "diffs": diffs}
        if breaks:
            info["break"] = breaks
            return "MISMATCH", info
        return "MATCH", info
    # FRESH: newer than any filing → plausibility (<=1.5pp) vs the latest filed.
    latest = max(cap)
    breaks, diffs = _compare(ir, cap[latest], 1.5)
    info = {"latest_period": latest, "diffs": diffs}
    if breaks:
        info["review"] = breaks
        return "FRESH_REVIEW", info
    return "FRESH_PLAUSIBLE", info


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
    print(f"[ir-audit] {len(tickers)} to audit ({len(done)} already done)", flush=True)

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
                        ir = extract_capital_ratios(res["html"])
                        cap = (holdco_capital_for(cik, get_fdic_cert(tk)) or {}).get("capital")
                        period = _period_from_filed(res.get("filed_date", ""))
                        verdict, info = _classify(ir, cap, period)
                        row.update({"verdict": verdict, "filed": res.get("filed_date"), **info})
                except Exception as e:
                    row["verdict"] = f"ERR:{type(e).__name__}"
            counts[row["verdict"].split(":")[0]] += 1
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            if row["verdict"] in ("MISMATCH", "FRESH_REVIEW"):
                print(f"  [{row['verdict']}] {tk}: {row}", flush=True)
            if i % 20 == 0:
                print(f"  {i}/{len(tickers)}  {dict(counts)}", flush=True)
            time.sleep(0.15)  # be polite to EDGAR

    print("\n" + "=" * 60)
    print("IR CAPITAL-RATIO AUDIT —", dict(counts))
    print(f"  MISMATCH (strict ground-truth failures, MUST be 0): {counts['MISMATCH']}")
    print("=" * 60)
    return 1 if counts["MISMATCH"] else 0


if __name__ == "__main__":
    sys.exit(main())
