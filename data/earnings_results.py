"""
Reported-results board for the Earnings section ("Results" sub-tab).

Compiles, per universe bank that has REPORTED in the trailing window, the
release data in one row: actual vs estimated EPS/revenue with surprise %,
report timing, the price reaction, and a link to the results press release.

Sources (all existing pipelines — nothing new is fetched per render):
  - FMP earnings-calendar (with includeReportTimes) supplies date, timing,
    epsActual/epsEstimated, revenueActual/revenueEstimated, periodEnding —
    FMP fills the actuals the same day a bank reports.
  - The events store's 'earnings'-typed rows supply the results-PR link.
  - FMP EOD history supplies the price reaction over the release session
    (bmo → the report date's session; amc → the NEXT session). When that
    session is still in progress (reported this morning), the live 1D change
    stands in until the close lands in EOD history.

The row builder is pure and unit-tested; results_board() does the fetching
once and is cross-instance cached (served_snapshot, 15 min) so renders during
earnings week stay cheap. Missing values are None — rendered as '—', never
fabricated (see CLAUDE.md).
"""

from __future__ import annotations

from datetime import date, timedelta

# Matches _WHEN_LABEL in data/earnings_call.py (bmo/amc/dmh).
_WHEN_LABEL = {"bmo": "Before open", "amc": "After close", "dmh": "Midday"}


def _iso_date(s):
    """ISO 'YYYY-MM-DD' → date; None on anything unparseable."""
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def surprise_pct(actual, estimate) -> float | None:
    """Surprise as % of the estimate's magnitude ((act − est) / |est| × 100).
    None when either side is missing or the estimate is 0 (division would
    fabricate an infinite surprise)."""
    try:
        actual, estimate = float(actual), float(estimate)
    except (TypeError, ValueError):
        return None
    if estimate == 0:
        return None
    return (actual - estimate) / abs(estimate) * 100.0


def reaction_session(report_date: date, when: str | None) -> date:
    """The trading session whose move IS the market's reaction to the release:
    the report date's own session, except an after-close release — the market
    can only react the NEXT session. (Weekends/holidays resolve forward when
    the session is looked up against actual trading days.)"""
    if when == "After close":
        return report_date + timedelta(days=1)
    return report_date


def price_reaction(closes: list[tuple[date, float]], session: date,
                   max_forward_days: int = 4) -> float | None:
    """Close-over-prior-close % for the first trading day ≥ `session` from a
    (date, close) series sorted ascending. None when the session isn't in the
    series yet (still in progress / history gap), when there is no prior close,
    or when the first trading day lands more than `max_forward_days` after the
    target (a long gap means the series is stale, not that the market waited)."""
    if not closes:
        return None
    for i, (d, c) in enumerate(closes):
        if d >= session:
            if i == 0 or (d - session).days > max_forward_days:
                return None
            prev = closes[i - 1][1]
            if not prev or c is None:
                return None
            return (c / prev - 1.0) * 100.0
    return None


def pick_release_pr(events: list[dict], report_date: date) -> dict | None:
    """The results press release for a report: the newest 'earnings'-typed event
    published in [report date, report date + 3d] — results PRs go out on/after
    the report, while date-announcement PRs precede it by weeks, so the window
    itself separates the two. `events` is newest-first (store order). None when
    nothing falls in the window."""
    for e in events or []:
        pub = _iso_date(e.get("published_at"))
        if pub is None:
            continue
        if 0 <= (pub - report_date).days <= 3:
            return e
        if pub < report_date:        # newest-first: everything after is older
            break
    return None


def build_results_rows(fmp_rows, universe, events_by_ticker, today,
                       days_back: int = 30) -> list[dict]:
    """Pure row builder for the Results board. One row per universe ticker that
    has a reported (actual ≠ None) FMP calendar entry dated within
    [today − days_back, today], newest report first then ticker.

    `fmp_rows`: raw FMP earnings-calendar rows (must carry the actuals fields).
    `events_by_ticker`: {ticker: [earnings-typed events, newest-first]}.
    Price reaction is NOT computed here (needs history fetches) — rows carry
    `reaction_session` for the caller to fill `px_react` against real closes.

    Row: {ticker, date, when, period_ending, eps_act, eps_est, eps_surprise,
          rev_act, rev_est, rev_surprise, reaction_session, pr_headline, pr_url}
    """
    uni = set(universe or ())
    floor = today - timedelta(days=days_back)
    best: dict = {}
    for r in fmp_rows or []:
        tk = (r.get("symbol") or "").upper()
        if not tk or tk not in uni:
            continue
        d = _iso_date(r.get("date"))
        if d is None or not (floor <= d <= today):
            continue
        eps_act, rev_act = r.get("epsActual"), r.get("revenueActual")
        if eps_act is None and rev_act is None:
            continue                                  # not reported yet
        prev = best.get(tk)
        if prev is not None and prev["_d"] >= d:
            continue                                  # keep the newest report
        when = _WHEN_LABEL.get((r.get("time") or "").lower())
        pr = pick_release_pr(events_by_ticker.get(tk) or [], d)
        # FMP's periodEnding is unreliable for fiscal-year-odd banks (CARV
        # showed a period ending AFTER its report date; CPBI one a year old).
        # A real earnings report lands ~1-5 weeks after the period closes —
        # anything outside [7, 150] days is FMP junk → '—', never displayed.
        pe = _iso_date(r.get("periodEnding"))
        period = (pe.isoformat() if pe is not None and 7 <= (d - pe).days <= 150
                  else None)
        best[tk] = {
            "_d": d,
            "ticker": tk,
            "date": d.isoformat(),
            "when": when,
            "period_ending": period,
            "eps_act": eps_act,
            "eps_est": r.get("epsEstimated"),
            "eps_surprise": surprise_pct(eps_act, r.get("epsEstimated")),
            "rev_act": rev_act,
            "rev_est": r.get("revenueEstimated"),
            "rev_surprise": surprise_pct(rev_act, r.get("revenueEstimated")),
            "reaction_session": reaction_session(d, when).isoformat(),
            "pr_headline": (pr or {}).get("headline"),
            "pr_url": (pr or {}).get("url"),
        }
    rows = sorted(best.values(), key=lambda x: (-x["_d"].toordinal(), x["ticker"]))
    for r in rows:
        r.pop("_d")
    return rows


def _fill_price_reactions(rows, today, max_workers: int = 8) -> None:
    """Fill each row's `px_react` in place from FMP EOD history (1M window,
    already Postgres-cached 1h per ticker). When the reaction session is TODAY
    and today's close isn't in EOD yet (market open / just closed), the live 1D
    change stands in — labeled by `px_react_live`. None (→ '—') on any gap."""
    from concurrent.futures import ThreadPoolExecutor
    from data import fmp_client

    def _one(row):
        tk = row["ticker"]
        session = _iso_date(row["reaction_session"])
        row["px_react"] = None
        row["px_react_live"] = False
        try:
            df = fmp_client.get_history(tk, "1M")
            closes = [(d.date(), float(c)) for d, c in
                      zip(df["date"], df["close"])] if not df.empty else []
        except Exception:
            closes = []
        pct = price_reaction(closes, session)
        if pct is None and session is not None and session <= today and (
                not closes or closes[-1][0] < session):
            # Session underway / close not posted yet → live intraday change.
            try:
                pc = fmp_client.get_price_change(tk)
                pct = float(pc["1D"]) if pc and pc.get("1D") is not None else None
                row["px_react_live"] = pct is not None
            except Exception:
                pct = None
        row["px_react"] = pct

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(_one, rows))


def release_matches_report(filed_iso, report_iso) -> bool:
    """True when an 8-K's filing date belongs to THIS report: same day through
    +5 days (the 8-K can trail the wire PR slightly; EDGAR acceptance after
    16:00 ET stamps the next day, and a weekend can add two more). A release
    filed BEFORE the report date is last quarter's — never attached."""
    f, r = _iso_date(filed_iso), _iso_date(report_iso)
    if f is None or r is None:
        return False
    return 0 <= (f - r).days <= 5


def _fill_release_metrics(rows, max_workers: int = 6) -> None:
    """Attach each row's release-extracted metrics in place (`rel` = {metrics,
    capital, url} or None): the per-CIK cached 8-K extraction, attached ONLY
    when the release's filing date matches the row's report date — a stale
    prior-quarter release never shows on a new report row."""
    from concurrent.futures import ThreadPoolExecutor
    from data.bank_mapping import get_cik
    from data.release_metrics import release_metrics

    def _one(row):
        row["rel"] = None
        try:
            rm = release_metrics(get_cik(row["ticker"]))
        except Exception:
            return
        if rm and release_matches_report(rm.get("filed_date"), row["date"]):
            row["rel"] = {"metrics": rm.get("metrics") or {},
                          "capital": rm.get("capital") or {},
                          "url": rm.get("url")}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(_one, rows))


def results_board(days_back: int = 30) -> list[dict]:
    """The Results board rows, cross-instance cached 15 min (earnings-week
    freshness without per-render fetch storms). Empty list when nothing has
    reported in the window or on total source failure — a genuine FMP-calendar
    failure raises out of the build so it is never cached (house pattern)."""
    from data import cache as _cache

    def _build():
        from data import fmp_client
        from data.bank_universe import get_universe
        today = date.today()
        fmp_rows = fmp_client.get_earnings_calendar(
            (today - timedelta(days=days_back)).isoformat(), today.isoformat())
        if fmp_rows is None:
            raise RuntimeError("FMP earnings calendar unavailable")
        try:
            universe = set(get_universe().keys())
        except Exception:
            universe = set()
        try:
            from data.events.store import get_events_by_type
            events: dict = {}
            for e in get_events_by_type("earnings", limit=800):
                tk = e.get("ticker")
                if tk:
                    events.setdefault(tk, []).append(e)   # store order: newest-first
        except Exception:
            events = {}
        rows = build_results_rows(fmp_rows, universe, events, today,
                                  days_back=days_back)
        _fill_price_reactions(rows, today)
        _fill_release_metrics(rows)
        return rows

    try:
        # v2: v1 rows predate the attached release metrics (`rel`).
        return _cache.served_snapshot(f"earnings_results_board_v2:{days_back}",
                                      900, _build) or []
    except Exception:
        return []
