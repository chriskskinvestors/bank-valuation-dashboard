"""Agent-dataset export: earnings-call transcripts (FMP).

Unlike the other export tables (which read what the dashboard already
computed), this one MUST fetch from FMP — transcripts are otherwise only pulled
when someone opens the Transcripts tab. It does so incrementally:

  * the job passes ``have`` = (ticker, fiscal_year, quarter) already in the
    durable export; only NEW calls are returned (the writer appends);
  * content fetches are capped by a global ``max_fetch`` budget, spent
    newest-call-first ACROSS all banks (one global sort on call date), so a
    budget cut never leaves a recent call missing while an old one loads —
    the backlog drains oldest-last over successive nights;
  * every FMP call is paced serially at ``_MIN_INTERVAL_S`` (<= 150/min),
    well under the Premium plan's ~300/min, leaving headroom for other jobs
    sharing the key (fmp_client._get itself has no throttle).

Reuses data.fmp_transcripts (same cache keys as the Transcripts tab, so a
transcript fetched here is a cache hit in the UI and vice versa).

fiscal_year / quarter are FMP's fiscalYear / quarter AS GIVEN — fiscal, not
calendar: for a non-December fiscal-year bank, fiscal Q1 is not Jan–Mar. Use
call_date for calendar ordering.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone

import pandas as pd

from data import fmp_transcripts

COLUMNS_TRANSCRIPTS: dict[str, str] = {
    "ticker": "Dashboard ticker (== FMP symbol for listed banks).",
    "fiscal_year": "FMP fiscalYear of the call, as given — FISCAL, not "
                   "calendar (differs for non-December fiscal-year banks).",
    "quarter": "FMP fiscal quarter of the call (1-4), as given.",
    "call_date": "Date of the earnings call (FMP transcript date, else the "
                 "availability-list date). n/a (None) when FMP's date "
                 "is >21 days off the bank's own lag for that fiscal "
                 "quarter (FMP mis-dates some calls) or is absent.",
    "word_count": "Whitespace-delimited word count of content.",
    "content": "Full transcript text as published by FMP.",
    "fetched_at": "UTC timestamp this row was fetched from FMP.",
    "source": "Data source — always 'FMP'.",
}

# Serial pacing between FMP calls: 0.4s -> <= 150 requests/min.
_MIN_INTERVAL_S = 0.4
_last_call = 0.0


def _pace() -> None:
    """Block until _MIN_INTERVAL_S has elapsed since the previous FMP call."""
    global _last_call
    wait = _MIN_INTERVAL_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _to_date(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


_QUARTER_DAYS = 365.25 / 4
# A genuine call lands within days of the bank's usual lag for THAT fiscal
# quarter (Q4 lags are compared with Q4s, so a bank that reports Q4 late is
# not penalized); FMP's mis-dates seen live are 31-366 days off (JPM FY2016 Q4
# dated 2016-12-13 vs a 01-14 cadence; JPM FY2022 Q1 dated 2022-03-08 vs
# 04-13; HFWA FY2024 Q4 a year early).
_DATE_TOL_DAYS = 21


def _median(xs: list[float]) -> float | None:
    return sorted(xs)[len(xs) // 2] if len(xs) >= 3 else None


def _date_checker(calls: list[dict]):
    """Return f(call, date) -> (date_or_None, sort_key) validating FMP's call
    date against the bank's OWN cadence.

    FMP's date field is sometimes wrong while the fiscal label + content are
    right (HFWA "FY2024 Q4" is dated 2024-01-23; the transcript is the Jan-2025
    Q4-2024 call), or wildly off (HFWA "FY2023 Q1" dated 2018-03-09). A call's
    offset = date - (4*year+quarter) quarters is the reporting lag plus a
    per-bank constant (any fiscal-year end). A date more than _DATE_TOL_DAYS
    from the median offset of the same fiscal quarter (else of all quarters,
    when that quarter has < 3 dated calls) gets call_date n/a — never a
    plausible-wrong date. Fewer than 3 dated calls in total -> nothing to
    check against, dates pass through. The sort key falls back to the
    cadence-implied date so a flagged call still queues by its true recency.
    """
    by_q: dict[int, list[float]] = {}
    for c in calls:
        d = _to_date(c.get("date"))
        if d is not None:
            by_q.setdefault(int(c["quarter"]), []).append(
                d.toordinal()
                - (4 * int(c["year"]) + int(c["quarter"])) * _QUARTER_DAYS)
    overall = _median([o for v in by_q.values() for o in v])

    def check(c: dict, d: date | None):
        q = int(c["quarter"])
        med = _median(by_q.get(q, []))
        if med is None:
            med = overall
        if med is None:
            return d, (d.toordinal() if d else 0)
        expected = med + (4 * int(c["year"]) + q) * _QUARTER_DAYS
        if d is not None and abs(d.toordinal() - expected) <= _DATE_TOL_DAYS:
            return d, d.toordinal()
        return None, expected
    return check


def _noncommon() -> set[str]:
    try:
        from data.bank_universe import get_noncommon_tickers
        return {t.upper() for t in get_noncommon_tickers()}
    except Exception as e:  # universe unavailable: proceed, FMP just misses
        print(f"[export.transcripts] non-common filter unavailable: "
              f"{type(e).__name__}: {e}")
        return set()


def build_transcripts(tickers: list[str],
                      have: set[tuple[str, int, int]],
                      max_fetch: int = 800,
                      max_quarters: int = 12) -> pd.DataFrame:
    """New earnings-call transcripts for ``tickers`` (append semantics).

    Per ticker: list available calls, keep the newest ``max_quarters``, drop
    those in ``have``. Then fetch content newest-call-first across all banks
    until ``max_fetch`` content requests are spent. A listed call whose content
    comes back empty/None is NOT emitted (retried next night) and counted.

    df.attrs: fetched (content requests made), emitted, skipped_have,
    missing_content, budget_exhausted, not_fetched (calls left for a later
    night), banks_no_coverage (count; empty list = no FMP coverage OR a failed
    list call — fmp_transcripts does not distinguish), banks_no_coverage_list,
    excluded_noncommon, call_date_na / call_date_na_list (rows whose FMP date
    contradicts the bank's own call cadence or is absent -> call_date n/a;
    list items are (ticker, fiscal_year, quarter, FMP's raw date)).
    """
    have_n = {(str(t).upper(), int(y), int(q)) for t, y, q in (have or set())}
    noncommon = _noncommon()

    seen: set[str] = set()
    todo: list[tuple[str, dict, object, float]] = []
    skipped_have = 0
    no_cov: list[str] = []
    excluded = 0
    for raw in tickers or []:
        t = str(raw or "").strip().upper()
        if not t or t in seen:
            continue
        seen.add(t)
        if t in noncommon:
            excluded += 1
            continue
        _pace()
        calls = fmp_transcripts.get_transcript_dates(t)
        if not calls:
            no_cov.append(t)
            continue
        check = _date_checker(calls)   # cadence from the FULL listed history
        # get_transcript_dates returns newest first (fiscal year, quarter).
        labels: set[tuple[int, int]] = set()
        for c in calls[:max_quarters]:
            label = (int(c["year"]), int(c["quarter"]))
            if label in labels:          # a repeated FMP label = one call
                continue
            labels.add(label)
            if (t, *label) in have_n:
                skipped_have += 1
            else:
                _, key = check(c, _to_date(c.get("date")))
                todo.append((t, c, check, key))

    # Newest call first across all banks (cadence-checked date; undated calls
    # of an unverifiable bank last); ties keep input order (stable sort).
    todo.sort(key=lambda x: x[3], reverse=True)

    rows: list[dict] = []
    fetched = missing = 0
    date_na: list[tuple[str, int, int, str | None]] = []
    budget = max(0, int(max_fetch))
    for t, c, check, _ in todo[:budget]:
        y, q = int(c["year"]), int(c["quarter"])
        _pace()
        rec = fmp_transcripts.get_transcript(t, y, q)
        fetched += 1
        content = (rec or {}).get("content")
        if not content or not str(content).strip():
            missing += 1
            continue
        content = str(content)
        raw_d = _to_date(rec.get("date")) or _to_date(c.get("date"))
        call_date, _ = check(c, raw_d)
        if call_date is None:
            date_na.append((t, y, q, raw_d.isoformat() if raw_d else None))
        rows.append({
            "ticker": t,
            "fiscal_year": y,
            "quarter": q,
            "call_date": call_date,
            "word_count": len(content.split()),
            "content": content,
            "fetched_at": datetime.now(timezone.utc),
            "source": "FMP",
        })

    df = pd.DataFrame(rows, columns=list(COLUMNS_TRANSCRIPTS))
    not_fetched = max(0, len(todo) - budget)
    df.attrs.update({
        "fetched": fetched,
        "emitted": len(rows),
        "skipped_have": skipped_have,
        "missing_content": missing,
        "budget_exhausted": not_fetched > 0,
        "not_fetched": not_fetched,
        "banks_no_coverage": len(no_cov),
        "banks_no_coverage_list": no_cov,
        "excluded_noncommon": excluded,
        "call_date_na": len(date_na),
        "call_date_na_list": date_na,
    })
    return df
