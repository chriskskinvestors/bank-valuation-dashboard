"""SEC filer status — is a bank's CIK a source of CURRENT XBRL, or only an
identity?

A CIK in SEC's ticker file does not mean the bank files 10-Ks/10-Qs: FOTB
and OSBK deregistered in 2012 (only Form D / Regulation A since), UBOH filed
Form 15-12G in March 2023 after its FY2022 10-K, DWNX is a Regulation A
issuer that never filed a periodic report. Routed as SEC filers, their
companyfacts came back empty or a decade stale, the staleness guard
blanked every HoldCo figure, the growth gate failed them every night
("sec_filings: Data is 5302 days old"), and the wire/IR-release path that
serves the ~235 genuine non-SEC filers never ran for them because its warm
is gated on having no CIK (found 2026-10-12).

Status, from the bank's submissions index (the ~2h-cached record
data/sec_earnings_8k already loads per bank):
  "periodic"      — a 10-K/10-Q filed within PERIODIC_MAX_DAYS: XBRL is
                    current enough to be the primary source.
  "deregistered"  — had periodic filings, none within the window.
  "non-periodic"  — never filed a 10-K/10-Q (Regulation A, Form 10 only).
Anything but "periodic" routes the bank like a bank with no CIK for DATA
purposes (data.bank_mapping.get_data_cik): wire/IR release for per-share
figures, FDIC for the rest, no XBRL staleness validation. The CIK itself
stays for identity — filings links, the Corporate Profile chips.

The nightly refresh (jobs/refresh_universe) computes the status per bank
and persists the map under _STATUS_KEY; interactive code and the metrics
builds read ONLY that map (one cached row, memoised ~10 min), never the
submissions index — a cold universe-wide lookup would be ~660 fetches on a
render path. An unknown ticker, or no map yet, reads as "periodic": the
status quo, never a demotion on missing data.
"""
from __future__ import annotations

import time
from datetime import date

PERIODIC_MAX_DAYS = 455          # ~15 months: a 10-K cycle plus a late filing
_STATUS_KEY = "sec_filer_status:v1"
_MEMO_TTL_S = 600
_memo: dict = {"at": 0.0, "map": None}


def sec_filer_status(cik) -> str:
    """Live status for a CIK from its cached submissions record. A lookup
    failure reads as "periodic" — never demote a bank on an error."""
    from data.sec_earnings_8k import latest_periodic_filing
    try:
        per = latest_periodic_filing(cik)
    except Exception:
        return "periodic"
    if not per or not per.get("date"):
        return "non-periodic"
    try:
        age = (date.today() - date.fromisoformat(per["date"])).days
    except ValueError:
        return "periodic"
    return "periodic" if age <= PERIODIC_MAX_DAYS else "deregistered"


def write_statuses(statuses: dict[str, str]) -> None:
    """Persist the nightly {ticker: status} map (job-owned; read at any age)."""
    from data import cache
    cache.put(_STATUS_KEY, {"date": date.today().isoformat(),
                            "statuses": dict(statuses)})
    _memo["map"], _memo["at"] = dict(statuses), time.time()


def _status_map() -> dict:
    if _memo["map"] is not None and time.time() - _memo["at"] < _MEMO_TTL_S:
        return _memo["map"]
    try:
        from data import cache
        rec = cache.get(_STATUS_KEY, max_age_s=None) or {}
        m = dict(rec.get("statuses") or {})
    except Exception:
        m = {}
    _memo["map"], _memo["at"] = m, time.time()
    return m


def persisted_status(ticker: str) -> str | None:
    """The nightly map's status for `ticker`, or None when unknown."""
    return _status_map().get((ticker or "").upper())


def is_data_filer(ticker: str) -> bool:
    """True unless the nightly map says the bank's CIK is not a source of
    current XBRL ("deregistered" / "non-periodic")."""
    return persisted_status(ticker) in (None, "periodic")
