"""Ground-truth verification battery for §14 pending-deal detection.

RUN THIS (live, ~5-10 min) whenever data/ma_pending.py, the pending legs
of data/ma_announcements.py, or ma_history's pending merge/dedup change.
The 2026-07-15 revert happened because the first cut was verified only on
freshly-announced deals — this battery pins BOTH directions:

  known-CLOSED deals (each confirmed against a primary source — an SEC
  Item 2.01/8.01 completion filing or an FDIC structure absorption event)
  must produce ZERO pending rows, and the known-OPEN control must keep
  exactly its row. A closed deal shown as pending is the cardinal-rule
  violation that forced the revert.

The pinned cases freeze the failure modes found live 2026-07-15/16:
  CLST/Lakeside     cash deal, closed via 2.01 the day after we shipped
  PB/Stellar        closed via 2.01 that also mentions other banks
  PB/American Bank  closed per FDIC 810 with NO 2.01 ever filed
                    (tokenless name -> leading-two-words FDIC dedup)
  FULT/Blue Foundry closed via 2.01 (425-leg detection)
  UMBF/Heartland    announce anchor was itself a post-close 8-K (slack)
  HOPE/Territorial  completion filed under item 8.01 ONLY (tense check)
  FHB + TCBK        the open control at rebuild time — RETIRE these two
                    assertions once that deal closes (they will then
                    correctly return zero pending rows) and PIN whatever
                    live open deal replaces them.

Usage (dev box; FMP_API_KEY in env for computed values):
  python -m tools.verify_pending_deals
Exit 0 = every case passes. Any assert = do NOT ship.
"""
from data.bank_mapping import get_cik, get_fdic_cert, get_name
from data.ma_history import get_ma_history

# (ticker, expectation) — expectation is a callable on the pending rows.
CASES = ["CLST", "PB", "FULT", "UMBF", "HOPE", "FHB", "TCBK", "BANR"]


def _clear_ma_history_cache():
    """A verify against cached rows proves nothing — drop local ma_history
    entries so every case re-fetches live (sqlite dev cache only)."""
    import sqlite3
    try:
        conn = sqlite3.connect("cache.db")
        n = conn.execute(
            "DELETE FROM cache WHERE key LIKE 'ma_history:%'").rowcount
        conn.commit()
        conn.close()
        print(f"cleared {n} cached ma_history entries", flush=True)
    except Exception as e:
        print(f"WARNING: could not clear cache ({e}) — results may be "
              "served from cache and prove nothing", flush=True)


def main() -> int:
    _clear_ma_history_cache()
    results = {}
    for tk in CASES:
        cert, cik = get_fdic_cert(tk), get_cik(tk)
        deals = get_ma_history(cert, cik=cik, name=get_name(tk) or tk)
        pend = [d for d in deals if d["status"] == "pending"]
        results[tk] = pend
        print(f"{tk:5} {len(deals):3} deals | pending: "
              f"{[(p['counterparty']['name'], p['value_usd'], p['direction'])
                  for p in pend]}", flush=True)

    # Known-closed: zero pending rows, forever.
    for tk in ("CLST", "PB", "FULT", "UMBF", "HOPE", "BANR"):
        assert results[tk] == [], (tk, results[tk])

    # Open control (RETIRE + REPLACE once FHB/TriCo closes — see docstring).
    assert len(results["FHB"]) == 1, results["FHB"]
    assert "TriCo" in results["FHB"][0]["counterparty"]["name"]
    assert results["FHB"][0]["value_usd"] == 2_014_271_431
    assert len(results["TCBK"]) == 1 and results["TCBK"][0]["direction"] == "sale"

    # Name hygiene: no 'About X. X' footer run-ons, no doubled suffixes.
    for tk, pend in results.items():
        for p in pend:
            nm = p["counterparty"]["name"]
            assert "About " not in nm and nm.count("Inc") <= 1, (tk, nm)
    print("PENDING-DEALS VERIFY: ALL GROUND-TRUTH CASES PASS")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
