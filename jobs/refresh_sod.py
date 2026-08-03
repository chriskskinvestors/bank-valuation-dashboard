"""
Cloud Run Job: refresh Summary-of-Deposits branch data for EVERY active
FDIC institution (~4,500 banks), not just our public-ticker subset.

Runs nightly. For each active cert: pull SOD branches, upsert into the
`branches` table with ticker populated for the ~300 banks in our public
universe and ticker=None for the ~4,200 private/community banks.

This powers the Geographic view's full-landscape "By State" and "By MSA"
tabs — so a user can see which banks (public or private) hold the most
deposits in, e.g., the Atlanta MSA.

Exit code:
  0  — at least 95% of attempted banks ingested successfully
  1  — heavier failure (suggests FDIC outage worth alerting on)
"""

from __future__ import annotations
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _refresh_one(cert: int, ticker: str | None,
                 year: int) -> tuple[int, str | None, int, str]:
    """Refresh SOD for one institution. Returns (cert, ticker, n_branches, err).

    `year` is resolved ONCE by the caller: the per-cert default lookup ran
    get_latest_sod_year 4,259 times per pass, doubling FDIC call volume —
    on 2026-07-13 that amplified throttling until 28% of banks came back
    empty and the job failed its coverage gate."""
    from data.sod_client import fetch_branches
    from data.branches_store import upsert_branches
    if not cert:
        return cert, ticker, 0, "no_cert"
    try:
        df = fetch_branches(cert, year=year)
        n = upsert_branches(ticker, cert, df)
        return cert, ticker, n, ""
    except Exception as e:
        return cert, ticker, 0, f"{type(e).__name__}: {str(e)[:80]}"


def main() -> int:
    import warnings; warnings.filterwarnings("ignore")
    from data.branches_store import init_branches_schema
    from data.bank_mapping import BANK_MAP, get_fdic_cert
    from data.bank_universe import get_universe_tickers
    from data.fdic_client import list_all_active_institutions

    init_branches_schema()

    # Reverse lookup: cert → ticker (so we tag public banks even though we're
    # iterating by cert).
    #
    # Built from get_fdic_cert() over the live universe — the LOOKUP-TIME
    # resolver, which applies curated cert corrections. Reading BANK_MAP's and
    # _RESOLVED_FROM_JSON's raw dicts instead missed every bank whose cert comes
    # from a correction: 9 public banks including STT, BPOP, HWC, WAFD and NEWT
    # were ingested with ticker=None, so their branches joined the unmapped pool
    # and rendered on Geographic as if they were private banks — blank ticker,
    # no Company-page link, and (before the cert-keyed picker) unselectable in
    # By Bank(s). Deposits were never wrong, only unattributed.
    cert_to_ticker: dict[int, str] = {}
    for ticker in sorted(get_universe_tickers()):
        try:
            cert = get_fdic_cert(ticker)
        except Exception:
            cert = None
        if cert:
            cert_to_ticker.setdefault(int(cert), ticker)
    # Belt-and-braces: keep the raw maps as a floor so a universe-snapshot
    # hiccup can't shrink coverage below what the static mappings already give.
    for ticker, info in BANK_MAP.items():
        cert = info.get("fdic_cert")
        if cert:
            cert_to_ticker.setdefault(int(cert), ticker)
    try:
        from data.bank_mapping import _RESOLVED_FROM_JSON
        for t, info in _RESOLVED_FROM_JSON.items():
            cert = info.get("fdic_cert")
            if cert:
                cert_to_ticker.setdefault(int(cert), t)
    except Exception:
        pass

    print(f"[{time.strftime('%H:%M:%S')}] Enumerating all active FDIC institutions...",
          flush=True)
    institutions = list_all_active_institutions()
    print(f"[{time.strftime('%H:%M:%S')}] Found {len(institutions)} active banks "
          f"({len(cert_to_ticker)} with public tickers)", flush=True)

    if not institutions:
        print("⚠ No institutions returned — FDIC outage?", flush=True)
        return 1

    # Workers capped at 4 to respect FDIC rate limit. ~4500 banks @ 2s avg
    # = ~38 min total. The 7-day cache from cert_is_active doesn't help SOD,
    # but the per-call retry handles transient 429s.
    t0 = time.time()
    no_cert = 0
    errors: list[tuple[int, str]] = []
    total_rows = 0
    success = 0
    workers = 4

    from data.sod_client import get_latest_sod_year
    sod_year = get_latest_sod_year()
    print(f"  SOD survey year: {sod_year}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(_refresh_one, int(inst["cert"]),
                       cert_to_ticker.get(int(inst["cert"])), sod_year): inst
            for inst in institutions if inst.get("cert")
        }
        done = 0
        total = len(futures)
        for fut in as_completed(futures):
            cert, ticker, n, err = fut.result()
            done += 1
            if err == "no_cert":
                no_cert += 1
            elif err:
                errors.append((cert, err))
            elif n > 0:
                success += 1
                total_rows += n
            if done % 200 == 0 or done == total:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                print(f"  {done}/{total} ({elapsed:.0f}s, "
                      f"ok={success} err={len(errors)} "
                      f"~{eta:.0f}s remaining)", flush=True)

    elapsed = time.time() - t0
    print()
    print(f"✓ Done in {elapsed:.0f}s")
    print(f"  Banks with branches: {success}/{len(institutions)}")
    print(f"  Errors:              {len(errors)}")
    print(f"  Total branch rows:   {total_rows:,}")

    if errors:
        print("\nError sample (first 10):")
        for cert, err in errors[:10]:
            print(f"  cert={cert} {err}")

    # Tolerate <5% failure rate (FDIC's free API will hiccup at this volume)
    success_rate = success / max(1, len(institutions))
    return 0 if success_rate >= 0.95 else 1


if __name__ == "__main__":
    sys.exit(main())
