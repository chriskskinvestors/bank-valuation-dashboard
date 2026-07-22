"""
Cloud Run Job: pre-warm every Company-Reported QUARTERLY cache (2026-07-14
quarterly track) so the Annual/Quarterly toggles are instant instead of a
~10-filing SEC parse on first click.

Per SEC-filer bank it warms, in one pass:
  • as_reported_statement_multiquarter income + balance (12 quarters — also
    feeds the Financial Highlights / Performance / Credit Quality quarterly
    engine, whose ratios derive from these two statements)
  • securities_multiquarter_for + fair_value_multiquarter_for (8 quarter-ends)
  • holdco_capital_quarterly_for (8-quarter capital series)
  • compositions_multiquarter_for (loan + deposit mix, 8 quarter-ends)
  • credit_quality_history quarterly AND annual (the Templated Asset Quality
    Detail criticized block — its 5×10-K / 9-filing walks are equally cold)

Every underlying extract is keyed by filing accession (immutable), so re-runs
only pay for banks with NEW filings — the first full walk is hours, steady
state is minutes. A "miss" is a bank whose filings don't carry a given
disclosure (correct n/a, still cached); only a high ERROR rate exits non-zero
(SEC/parse regression worth alerting on).

Throttling: one global limiter over BOTH SEC fetch paths. sec_statements and
sec_composition bind `_get` at import time (`from sec_filing_scraper import
_get`), so the wrapper must be installed on ALL THREE module globals — patching
only sec_filing_scraper._get would leave the other two unthrottled.
xbrl_dimensional already self-throttles (~8 req/s).

Run locally on a subset:  python -m jobs.refresh_cr_quarterly 5
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

_SEC_MAX_RPS = 8          # SEC fair-access is ~10 req/s; margin for UI traffic


class _SecThrottle:
    """Thread-safe global rate limiter (same as jobs.refresh_capital)."""

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
    """Throttle every `_get` binding: sec_filing_scraper defines it, and
    sec_statements / sec_composition import it BY VALUE at module load —
    all three globals must point at the wrapped function."""
    from data import sec_composition, sec_filing_scraper, sec_statements
    if getattr(sec_filing_scraper._get, "_rate_limited", False):
        return
    throttle = _SecThrottle(max_rps)
    orig = sec_filing_scraper._get

    def _throttled(url):
        throttle.wait()
        return orig(url)

    _throttled._rate_limited = True
    sec_filing_scraper._get = _throttled
    sec_statements._get = _throttled
    sec_composition._get = _throttled


# (label, callable(cik, cert) -> truthy when the bank disclosed the data)
def _parts():
    from data.sec_composition import compositions_multiquarter_for
    from data.sec_filing_scraper import (fair_value_multiquarter_for,
                                         holdco_capital_quarterly_for,
                                         securities_multiquarter_for)
    from data.sec_statements import as_reported_statement_multiquarter
    from data.xbrl_dimensional import credit_quality_history
    return [
        ("inc_q", lambda cik, cert: as_reported_statement_multiquarter(cik, "income", 8)),
        ("bal_q", lambda cik, cert: as_reported_statement_multiquarter(cik, "balance", 8)),
        ("sec_q", lambda cik, cert: securities_multiquarter_for(cik, 8)),
        ("fv_q", lambda cik, cert: fair_value_multiquarter_for(cik, 8)),
        ("cap_q", lambda cik, cert: holdco_capital_quarterly_for(cik, cert, 8)),
        ("comp_q", lambda cik, cert: compositions_multiquarter_for(cik, 8)),
        ("crit_q", lambda cik, cert: credit_quality_history(int(cik), quarterly=True)),
        ("crit_a", lambda cik, cert: credit_quality_history(int(cik), quarterly=False)),
    ]


def _warm_one(ticker: str, cik, cert, parts) -> tuple[str, dict]:
    """Warm one bank across all parts. Returns (ticker, {part: status}) with
    status ok / miss / err:<Type> — one part failing never skips the rest."""
    out = {}
    for name, fn in parts:
        try:
            out[name] = "ok" if fn(cik, cert) else "miss"
        except Exception as e:
            out[name] = f"err:{type(e).__name__}"
    return ticker, out


def main(limit: int | None = None):
    banks = json.load(open(REPO_ROOT / "data" / "bank_map_resolved.json"))
    items = [(t, v["cik"], v.get("fdic_cert"))
             for t, v in banks.items() if v.get("cik")]
    if limit:
        items = items[:limit]
    print(f"[refresh_cr_quarterly] warming CR quarterly caches for "
          f"{len(items)} banks…", flush=True)
    _install_sec_rate_limit()
    parts = _parts()
    t0 = time.time()
    ok = miss = err = 0
    err_samples: list[str] = []
    # Per-part tally (2026-07-15): the aggregate alone can't say WHICH
    # extraction moved when a run's miss count shifts — after the v2
    # composition bump the totals drifted and the logs couldn't attribute it
    # without re-deriving locally. Keyed by part name, in _parts() order.
    by_part = {name: {"ok": 0, "miss": 0, "err": 0} for name, _fn in parts}
    # Heavy multi-MB parses per filing — a small pool bounds memory while the
    # global throttle bounds SEC request rate.
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = [ex.submit(_warm_one, t, cik, cert, parts) for t, cik, cert in items]
        for i, fut in enumerate(as_completed(futs), 1):
            ticker, statuses = fut.result()
            for name, s in statuses.items():
                bucket = "ok" if s == "ok" else ("miss" if s == "miss" else "err")
                by_part.setdefault(name, {"ok": 0, "miss": 0, "err": 0})[bucket] += 1
                if bucket == "ok":
                    ok += 1
                elif bucket == "miss":
                    miss += 1
                else:
                    err += 1
                    if len(err_samples) < 8:
                        err_samples.append(f"{ticker}/{name}:{s}")
            if i % 10 == 0:
                print(f"  {i}/{len(items)} banks done "
                      f"({time.time() - t0:.0f}s)…", flush=True)
    total_parts = max(ok + miss + err, 1)
    print(f"[refresh_cr_quarterly] done in {time.time() - t0:.0f}s — "
          f"parts ok={ok} miss={miss} err={err} "
          f"({100 * ok // total_parts}% ok)", flush=True)
    # Every summary line carries the [refresh_cr_quarterly] tag so ONE log
    # filter (textPayload:refresh_cr_quarterly) returns the whole picture —
    # the untagged "error samples" line was invisible to that filter and cost
    # a diagnosis round-trip on 2026-07-14.
    for name, _fn in parts:
        c = by_part.get(name, {})
        print(f"[refresh_cr_quarterly]   {name}: ok={c.get('ok', 0)} "
              f"miss={c.get('miss', 0)} err={c.get('err', 0)}", flush=True)
    if err_samples:
        print(f"[refresh_cr_quarterly] error samples: "
              f"{'; '.join(err_samples)}", flush=True)
    # miss = the bank doesn't disclose it (correct n/a, cached); only a high
    # error rate signals a regression.
    sys.exit(1 if err > 0.10 * total_parts else 0)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
