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

import re
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


# Headline cues marking an UPCOMING-earnings date announcement — such a PR
# near a projected date must not mark the bank "reported" (the results PR
# says "Reports Q2 Results"; the announcement says "Will Report … on July 23").
_UPCOMING_CUE_RE = re.compile(
    r"\b(?:will (?:report|announce|release|host)|to (?:report|announce|release|"
    r"host)|schedul|sets? (?:the )?date)", re.I)


def build_results_rows(fmp_rows, universe, events_by_ticker, today,
                       days_back: int = 30) -> list[dict]:
    """Pure row builder for the Results board, one row per universe ticker
    dated within [today − days_back, today], newest report first then ticker:
      - FMP actuals present → a reported row, OR
      - actuals still null BUT a results press release exists on/after the
        scheduled date → a `pending` row (BKSC-class micro-caps: FMP actuals
        lag or never fill; deregistered banks have no 8-K — the bank's own PR
        in the news feed is the only same-day signal). Estimate/actual cells
        fill whenever FMP catches up.

    `fmp_rows`: raw FMP earnings-calendar rows (must carry the actuals fields).
    `events_by_ticker`: {ticker: [earnings-typed events, newest-first]}.
    Price reaction is NOT computed here (needs history fetches) — rows carry
    `reaction_session` for the caller to fill `px_react` against real closes.

    Row: {ticker, date, when, period_ending, eps_act, eps_est, eps_surprise,
          rev_act, rev_est, rev_surprise, reaction_session, pr_headline,
          pr_url, pending}
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
        if rev_act is not None and rev_act < 0:
            rev_act = None       # negative bank revenue = FMP junk (JPM -47.8B)
        if rev_act is not None and eps_act is None and (today - d).days <= 2:
            # Revenue posted before EPS on report day = FMP mid-ingestion; the
            # early figure is junk-prone (MS 2026-07-15: H1 revenue $36.29B
            # posted as the quarter, "+84% surprise", EPS still null). Hold
            # revenueActual until FMP's own EPS settles the row or two days
            # pass (some micro-caps have revenue-only coverage for good); the
            # release fill below still supplies confirmable actuals meanwhile.
            rev_act = None
        pr = pick_release_pr(events_by_ticker.get(tk) or [], d)
        pending = False
        if eps_act is None and rev_act is None:
            # No FMP actuals: reported anyway IF the bank's own results PR is
            # out (never an upcoming-date announcement) — else not reported.
            if pr is None or _UPCOMING_CUE_RE.search(pr.get("headline") or ""):
                continue
            pending = True
        prev = best.get(tk)
        if prev is not None and prev["_d"] >= d:
            continue                                  # keep the newest report
        when = _WHEN_LABEL.get((r.get("time") or "").lower())
        # FMP's periodEnding is unreliable for fiscal-year-odd banks (CARV
        # showed a period ending AFTER its report date; CPBI one a year old).
        # A real earnings report lands ~1-5 weeks after the period closes —
        # anything outside [7, 150] days is FMP junk → '—', never displayed.
        # And US banks report CALENDAR quarters (Call Reports pin them), so a
        # non-quarter-end date is junk too (FBK rendered "2026-06-01" live).
        pe = _iso_date(r.get("periodEnding"))
        period = (pe.isoformat()
                  if pe is not None and 7 <= (d - pe).days <= 150
                  and (pe.month, pe.day) in ((3, 31), (6, 30), (9, 30), (12, 31))
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
            "pending": pending,
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


def q_label(qend_iso) -> str | None:
    """ISO quarter-end → the trends grids' 'Qn YYYY' label; None unparseable."""
    d = _iso_date(qend_iso)
    if d is None:
        return None
    return f"Q{(d.month - 1) // 3 + 1} {d.year}"


# Exhibit history keys fillable from the PLATFORM grids. SEC per-share only
# (holdco, point-in-time — the correct basis for TBV/BV per share): the FDIC
# entries moved to EXHIBIT_FDIC_Q_MAP's direct single-quarter fetch — the
# grids carry YTD-annualized ratios (NIMY/ROA/EEFFR/roatce), which are the
# wrong quantity for a quarter column (2Q25A filled with an H1-annualized
# value). Capital ratios are NEVER mapped (holdco ≠ bank-sub).
PLATFORM_HIST_MAP = {
    "tbv_ps": ("sec", "tbvps_hist"),
    "bv_ps": ("sec", "bvps_hist"),
}


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
            metrics = rm.get("metrics") or {}
            row["rel"] = {"qend": rm.get("qend"),
                          "metrics": metrics,
                          "capital": rm.get("capital") or {},
                          "prior_metrics": rm.get("prior_metrics") or {},
                          "prior_qend": rm.get("prior_qend"),
                          "yoy_metrics": rm.get("yoy_metrics") or {},
                          "yoy_qend": rm.get("yoy_qend"),
                          "url": rm.get("url")}
            # Actuals fill (owner, 2026-07-13): FMP's consensus feed lags a
            # fresh report ("pending") — the bank's own release already
            # states EPS and total revenue, so fill from it, LABELED via
            # *_src. Adjusted EPS preferred (the street's comparison basis);
            # GAAP marked as such. Values are extraction-guarded upstream.
            if row.get("eps_act") is None:
                if metrics.get("eps_adj") is not None:
                    row["eps_act"] = metrics["eps_adj"]
                    row["eps_act_src"] = "release, adj."
                elif metrics.get("eps_diluted") is not None:
                    row["eps_act"] = metrics["eps_diluted"]
                    row["eps_act_src"] = "release, GAAP"
                if row.get("eps_act") is not None:
                    row["pending"] = False
                    if row.get("eps_surprise") is None:
                        row["eps_surprise"] = surprise_pct(
                            row["eps_act"], row.get("eps_est"))
            if row.get("rev_act") is None and \
                    metrics.get("total_revenue") is not None:
                row["rev_act"] = metrics["total_revenue"]
                row["rev_act_src"] = "release"
                if row.get("rev_surprise") is None:
                    row["rev_surprise"] = surprise_pct(
                        row["rev_act"], row.get("rev_est"))

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(_one, rows))


# Exhibit-history keys fillable from FDIC's single-QUARTER ratio fields
# (bank-sub basis, gap-fill only — the bank's own comparative columns always
# win). The Q variants, not the YTD-annualized defaults the Trends grid
# carries: exhibit columns are quarters (verified 2026-07-16: at a Q1
# quarter-end every Q field equals its YTD twin exactly, so the Q semantics
# are sound). DELIBERATELY absent: cost_of_deposits (banks state interest-
# bearing vs total-deposit cost inconsistently — a cross-definition delta is
# a plausible-wrong number); loan_yield (ILNDOMQR renders CTBI ~3.6% vs its
# real ~6.4% loan yield — unverifiable semantics, the ELNANTR title-lie
# class); capital ratios and TCE/TA (holdco ≠ bank-sub, pinned by test);
# rotce (no FDIC quarterly source — release/AI only).
EXHIBIT_FDIC_Q_MAP = {
    "nim": "NIMYQ",
    "efficiency": "EEFFQR",
    "roa": "ROAQ",
    "roe": "ROEQ",
    "nco_ratio": "NTLNLSQR",
    "acl_loans": "LNATRESR",
    "npa_assets": "NPERFV",
}


def _fill_fdic_history(rows) -> None:
    """Attach ``row["fdic_hist"] = {"prior": {key: val}, "yoy": {...}}`` from
    FDIC quarterly ratios for each reported bank's two history quarter-ends —
    one batched financials call per unique quarter-end. Any failure leaves
    rows without the attribute (exhibit cells stay blank, never wrong)."""
    try:
        from data import fdic_client
        from data.bank_universe import get_universe
        uni = get_universe()
    except Exception:
        return
    # repdte (YYYYMMDD) -> {cert: [(row, bucket), ...]}
    need: dict = {}
    for row in rows:
        rel = row.get("rel") or {}
        cert = (uni.get(row.get("ticker")) or {}).get("fdic_cert")
        if not cert:
            continue
        for bucket, qend in (("prior", rel.get("prior_qend")),
                             ("yoy", rel.get("yoy_qend"))):
            if qend:
                rd = str(qend).replace("-", "")
                need.setdefault(rd, {}).setdefault(int(cert), []).append(
                    (row, bucket))
    for rd, by_cert in need.items():
        try:
            recs = fdic_client.fetch_quarter_financials(rd, certs=by_cert)
        except Exception:
            continue
        for cert, targets in by_cert.items():
            rec = recs.get(cert)
            if not rec:
                continue
            vals = {}
            for key, field in EXHIBIT_FDIC_Q_MAP.items():
                v = rec.get(field)
                try:
                    v = float(v) if v is not None else None
                except (TypeError, ValueError):
                    v = None
                if v is not None:
                    vals[key] = v
            if not vals:
                continue
            for row, bucket in targets:
                row.setdefault("fdic_hist", {})[bucket] = vals


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
            # Common shares only: preferred/ETN listings share the parent's
            # CIK+name and FMP carries junk rows for them (AMJB rendered as
            # "Jpmorgan Chase" with a negative revenue, 2026-07-14).
            universe = {tk for tk, v in get_universe().items()
                        if (v or {}).get("share_class", "common") == "common"}
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
        _fill_fdic_history(rows)
        return rows

    try:
        # v3: rows gained `pending` (PR-signaled reports without FMP actuals).
        # v4: common-shares-only universe + negative-revenue junk guard.
        # v5: rows gained `fdic_hist` (quarterly-ratio exhibit history).
        return _cache.served_snapshot(f"earnings_results_board_v5:{days_back}",
                                      900, _build) or []
    except Exception:
        return []
