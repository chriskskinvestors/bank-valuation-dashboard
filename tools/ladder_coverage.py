"""
One-shot: report FFIEC ladder coverage in prod Postgres.

Answers: of the banks we map (BANK_MAP + resolved JSON), how many have a
stored securities ladder, and how many of those rows carry a non-NULL
floating_loan_share (the RC-C Memo 2 field we just wired in). Run after a
gcloud re-auth.

Usage:
  PYTHONIOENCODING=utf-8 python -X utf8 tools/ladder_coverage.py
"""
from __future__ import annotations
import sys
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

PROJECT = "ace-beanbag-486220-a8"
SQL_INSTANCE = f"{PROJECT}:us-central1:bank-dashboard-db"


def _connect():
    r = subprocess.run(
        ["gcloud.cmd", "secrets", "versions", "access", "latest",
         "--secret=db-password", f"--project={PROJECT}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"[FATAL] gcloud secret access failed: {r.stderr[:200]}")
        sys.exit(1)
    db_pass = r.stdout.strip()

    from sqlalchemy import create_engine
    from google.cloud.sql.connector import Connector
    connector = Connector()

    def getconn():
        return connector.connect(
            SQL_INSTANCE, "pg8000",
            user="dashboard", password=db_pass, db="dashboard",
        )

    engine = create_engine("postgresql+pg8000://", creator=getconn)
    from data import call_report_store
    call_report_store._engine = engine
    call_report_store._USE_POSTGRES = True
    return engine


def _mapped_certs() -> set[int]:
    from data.bank_mapping import BANK_MAP
    certs: set[int] = set()
    for info in BANK_MAP.values():
        c = info.get("fdic_cert")
        if c:
            certs.add(int(c))
    try:
        from data.bank_mapping import _RESOLVED_FROM_JSON
        for info in _RESOLVED_FROM_JSON.values():
            c = info.get("fdic_cert")
            if c:
                certs.add(int(c))
    except Exception:
        pass
    return certs


def main() -> int:
    from sqlalchemy import text
    eng = _connect()

    with eng.begin() as conn:
        total = conn.execute(text(
            "SELECT COUNT(DISTINCT cert) FROM call_report_securities"
        )).scalar()
        with_float = conn.execute(text(
            "SELECT COUNT(DISTINCT cert) FROM call_report_securities "
            "WHERE floating_loan_share IS NOT NULL"
        )).scalar()
        latest = conn.execute(text(
            "SELECT MAX(report_date) FROM call_report_securities"
        )).scalar()
        ladder_certs = {
            int(r[0]) for r in conn.execute(text(
                "SELECT DISTINCT cert FROM call_report_securities"))
        }

    mapped = _mapped_certs()
    covered = mapped & ladder_certs

    print("=" * 60)
    print("FFIEC LADDER COVERAGE  (prod Postgres)")
    print("=" * 60)
    print(f"Total banks with a securities ladder : {total}")
    print(f"  ...of which have floating_loan_share: {with_float}")
    print(f"Latest report_date in table          : {latest}")
    print("-" * 60)
    print(f"Banks we map (BANK_MAP + JSON)        : {len(mapped)}")
    print(f"  ...with a stored ladder             : {len(covered)} "
          f"({100*len(covered)/max(1,len(mapped)):.0f}%)")
    print(f"  ...without a ladder (generic ~29%/yr): {len(mapped - ladder_certs)}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
