"""
Cloud Run Job: pre-warm the SEC holding-company regulatory-capital cache.

The Capital Adequacy tab's holdco block scrapes each bank's latest 10-K/10-Q
inline XBRL (data.sec_filing_scraper.holdco_capital_for) and caches the result
by filing accession. On a cold cache the first page-load pays a ~7 MB fetch +
parse (~15 s). This job warms the cache for the whole public universe so the tab
is instant, and re-runs pick up new filings automatically (the cache key is the
accession, so an unchanged filing is a no-op).

Runs nightly. SEC asks for <=10 req/s; each bank does ~2-3 requests and the
downloads are slow, so a small worker pool keeps us comfortably under the limit.

Exit code: 0 normally (a "miss" just means the filing doesn't tag capital — a
correct n/a, still cached); 1 only if the ERROR rate is high (>10%), which
signals an SEC/parse regression worth alerting on.

Run locally on a subset:  python -m jobs.refresh_capital 10
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _warm_one(ticker: str, cik, cert) -> tuple[str, str]:
    """Warm one bank's holdco capital cache. Returns (ticker, status)."""
    from data.sec_filing_scraper import holdco_capital_for
    try:
        res = holdco_capital_for(cik, cert)
        if not res or not res.get("capital"):
            return ticker, "miss"
        cap = res["capital"]
        d = cap.get(max(cap), {})
        if d.get("cet1_ratio"):
            return ticker, "ok"
        return ticker, "cblr" if d.get("_cblr") else "miss"
    except Exception as e:
        return ticker, f"err:{type(e).__name__}"


def main(limit: int | None = None):
    banks = json.load(open(REPO_ROOT / "data" / "bank_map_resolved.json"))
    items = [(t, v["cik"], v.get("fdic_cert")) for t, v in banks.items() if v.get("cik")]
    if limit:
        items = items[:limit]
    print(f"[refresh_capital] warming holdco-capital cache for {len(items)} banks…", flush=True)
    t0 = time.time()
    counts: dict[str, int] = {"ok": 0, "cblr": 0, "miss": 0, "err": 0}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(_warm_one, t, cik, cert) for t, cik, cert in items]
        for i, fut in enumerate(as_completed(futs), 1):
            _ticker, status = fut.result()
            counts["err" if status.startswith("err") else status] = \
                counts.get("err" if status.startswith("err") else status, 0) + 1
            if i % 25 == 0:
                print(f"  {i}/{len(items)} done…", flush=True)
    tot = max(len(items), 1)
    resolved = counts["ok"] + counts["cblr"]
    print(f"[refresh_capital] done in {time.time() - t0:.0f}s — "
          f"ok={counts['ok']} cblr={counts['cblr']} miss={counts['miss']} "
          f"err={counts['err']}  ({100 * resolved // tot}% resolved)", flush=True)
    sys.exit(1 if counts["err"] > 0.10 * tot else 0)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
