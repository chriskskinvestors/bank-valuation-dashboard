"""
Cloud Run Job: pre-warm the Company-Reported multi-year statement cache.

Financials → Company Reported → Income Statement / Balance Sheet stitch each
bank's recent 10-K R-files (data.sec_statements.as_reported_statement_multiyear)
into ~5 fiscal years. Cold, that's several MB of R-files + parse across 4-6
filings per bank (slow). This job warms the cache for the whole public universe
so the views are instant; re-runs pick up new 10-Ks automatically (the cache key
is the latest accession, so an unchanged filing is a no-op).

Runs nightly. SEC asks for <=10 req/s; we throttle the whole worker pool to 8/s.

Exit code: 0 normally (a "miss" just means a filer doesn't render an R-file we
can parse — a correct n/a, still cached); 1 only if the ERROR rate is high
(>10%), which signals an SEC/parse regression worth alerting on.

Run locally on a subset:  python -m jobs.refresh_company_financials 10
"""
from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

_SEC_MAX_RPS = 8


class _SecThrottle:
    """Thread-safe global rate limiter: reserves a time slot per call so the
    worker pool combined never issues SEC requests faster than max_rps."""

    def __init__(self, max_rps: int):
        self._min_interval = 1.0 / max_rps
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self._min_interval
        delay = start - now
        if delay > 0:
            time.sleep(delay)


def _install_sec_rate_limit(max_rps: int = _SEC_MAX_RPS) -> None:
    """Throttle every SEC fetch across the pool. data.sec_statements imported
    `_get` by value, so we rebind it in BOTH modules. Idempotent."""
    from data import sec_filing_scraper as sfs
    from data import sec_statements as ss
    if getattr(sfs._get, "_rate_limited", False):
        return
    throttle = _SecThrottle(max_rps)
    orig = sfs._get

    def _throttled(url):
        throttle.wait()
        return orig(url)

    _throttled._rate_limited = True
    sfs._get = _throttled
    ss._get = _throttled


def _warm_one(ticker: str, cik) -> tuple[str, str]:
    """Warm one bank's income + balance multi-year cache. Returns (ticker, status)."""
    from data.sec_statements import as_reported_statement_multiyear
    try:
        got = 0
        for stype in ("income", "balance"):
            res = as_reported_statement_multiyear(cik, stype)
            if res and res.get("statement", {}).get("rows"):
                got += 1
        return ticker, ("ok" if got == 2 else "partial" if got == 1 else "miss")
    except Exception as e:
        return ticker, f"err:{type(e).__name__}"


def main(limit: int | None = None):
    banks = json.load(open(REPO_ROOT / "data" / "bank_map_resolved.json"))
    items = [(t, v["cik"]) for t, v in banks.items() if v.get("cik")]
    if limit:
        items = items[:limit]
    print(f"[refresh_company_financials] warming {len(items)} banks…", flush=True)
    _install_sec_rate_limit()
    t0 = time.time()
    counts: dict[str, int] = {"ok": 0, "partial": 0, "miss": 0, "err": 0}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(_warm_one, t, cik) for t, cik in items]
        for i, fut in enumerate(as_completed(futs), 1):
            _ticker, status = fut.result()
            key = "err" if status.startswith("err") else status
            counts[key] = counts.get(key, 0) + 1
            if i % 25 == 0:
                print(f"  {i}/{len(items)} done…", flush=True)
    tot = max(len(items), 1)
    done = counts["ok"] + counts["partial"]
    print(f"[refresh_company_financials] done in {time.time() - t0:.0f}s — "
          f"ok={counts['ok']} partial={counts['partial']} miss={counts['miss']} "
          f"err={counts['err']}  ({100 * done // tot}% warmed)", flush=True)
    sys.exit(1 if counts["err"] > 0.10 * tot else 0)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
