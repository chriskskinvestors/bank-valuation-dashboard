"""
READ-ONLY diagnostic: find Call Report rows that were stored under the wrong
quarter by the pre-fix jobs/refresh_ffiec (late-filer fallback bug).

The bug: fetch_call_report falls back one quarter when a bank hasn't filed
the requested one; the job stored that prior-quarter frame stamped with the
REQUESTED quarter. A mislabeled row therefore carries exactly the same
numbers as the bank's previous stored quarter.

Method: for each per-schedule table, compare every (cert, report_date) row
with the same cert's row exactly one quarter earlier, ignoring the period /
rssd copies inside the JSON. Identical payloads → the LATER row is suspect.

  • ri_income_detail, rie_detail, deposit_cost_detail hold calendar-YTD
    flows: Q2 YTD can't equal Q1 YTD, and Q1 (3 months) can't equal the
    prior full year, for any bank with activity — a match there is 'strong'.
  • rcn_detail, rcr_capital, call_report_securities are point-in-time
    balances: identical consecutive quarters are improbable but possible
    (e.g. an all-zero RC-N) — a match only there is 'weak'.
call_report_full is not scanned: its upsert has rejected period-mismatched
frames since it shipped (PR #183).

Remediation (NOT done here — this script never writes): re-run the
refresh-ffiec job with FFIEC_PERIOD=<suspect quarter>. With the fix, a bank
that has since filed overwrites the bad row with its real filing; a bank
that still hasn't filed stores its fallback under its own quarter, leaving
the suspect row in place — those need a deliberate, reviewed delete.

Usage (prints a table; exit 0 always):
  python tools/diagnose_ffiec_period_mislabels.py           # data/db engine
                                                            # (SQLite locally,
                                                            # Postgres if
                                                            # DATABASE_URL set)
  python tools/diagnose_ffiec_period_mislabels.py --prod    # Cloud SQL via
                                                            # gcloud secret
                                                            # (as ladder_coverage)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

_YTD_TABLES = ("ri_income_detail", "rie_detail", "deposit_cost_detail")
_DETAIL_TABLES = _YTD_TABLES + ("rcn_detail", "rcr_capital")
_SECURITIES_COLS = ("total_securities", "buckets_json", "amounts_json",
                    "weighted_dur_yrs", "floating_loan_share")


def _iso(d) -> str:
    """DATE column → 'yyyy-mm-dd' (Postgres returns date, SQLite a string)."""
    return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]


def _prev_quarter_iso(iso: str) -> str:
    from data.ffiec_client import _previous_quarter
    y, m, d = iso.split("-")
    pm, pd_, py = _previous_quarter(f"{m}/{d}/{y}").split("/")
    return f"{py}-{pm}-{pd_}"


def _detail_payload(detail_json) -> str | None:
    """The detail dict minus its period / rssd copies, canonicalized."""
    try:
        d = json.loads(detail_json or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    d.pop("reporting_period", None)
    d.pop("rssd_id", None)
    return json.dumps(d, sort_keys=True)


def _scan(rows) -> list[tuple[int, str, str]]:
    """rows: iterable of (cert, report_date, payload). Returns
    (cert, report_date, prior_date) for every row whose payload equals the
    same cert's row exactly one quarter earlier."""
    by_key = {}
    for cert, rd, payload in rows:
        if payload is not None:
            by_key[(int(cert), _iso(rd))] = payload
    out = []
    for (cert, rd), payload in sorted(by_key.items()):
        prior = _prev_quarter_iso(rd)
        if by_key.get((cert, prior)) == payload:
            out.append((cert, rd, prior))
    return out


def find_suspects(conn) -> list[dict]:
    """[{cert, report_date, prior_date, tables, strength}] — one entry per
    suspect (cert, report_date), newest first. Read-only SELECTs."""
    from sqlalchemy import inspect, text
    present = set(inspect(conn).get_table_names())
    hits: dict[tuple[int, str], dict] = {}

    def _add(table, matches):
        for cert, rd, prior in matches:
            e = hits.setdefault((cert, rd), {
                "cert": cert, "report_date": rd, "prior_date": prior,
                "tables": []})
            e["tables"].append(table)

    for table in _DETAIL_TABLES:
        if table not in present:
            continue
        rows = conn.execute(text(
            f"SELECT cert, report_date, detail_json FROM {table}"))
        _add(table, _scan((c, rd, _detail_payload(j)) for c, rd, j in rows))

    if "call_report_securities" in present:
        rows = conn.execute(text(
            f"SELECT cert, report_date, {', '.join(_SECURITIES_COLS)} "
            f"FROM call_report_securities"))
        _add("call_report_securities",
             _scan((r[0], r[1], json.dumps([str(v) for v in r[2:]]))
                   for r in rows))

    out = sorted(hits.values(),
                 key=lambda e: (e["report_date"], e["cert"]), reverse=True)
    for e in out:
        e["tables"].sort()
        e["strength"] = ("strong" if set(e["tables"]) & set(_YTD_TABLES)
                         else "weak")
    return out


def _engine(prod: bool):
    if prod:
        from tools.ladder_coverage import _connect
        return _connect()
    from data.db import get_engine
    return get_engine()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    eng = _engine("--prod" in argv)
    with eng.connect() as conn:
        suspects = find_suspects(conn)
    strong = sum(e["strength"] == "strong" for e in suspects)
    print(f"Suspect mislabeled (cert, report_date) rows: {len(suspects)} "
          f"({strong} strong, {len(suspects) - strong} weak)")
    print(f"{'cert':>8}  {'report_date':<11}  {'same as':<11}  "
          f"{'strength':<8}  tables")
    for e in suspects:
        print(f"{e['cert']:>8}  {e['report_date']:<11}  {e['prior_date']:<11}  "
              f"{e['strength']:<8}  {','.join(e['tables'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
