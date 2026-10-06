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
    r"\b(?:will (?:report|announce|release|host|issue)|to (?:report|announce|"
    r"release|host|issue)|schedul|sets? (?:the )?dates?|"
    # WAL 2026-07-20: "Announces Second Quarter 2026 Earnings Release Date,
    # Conference Call and Webcast" slipped through and minted a false
    # PENDING row (a pending row claims the release is OUT — it wasn't).
    # A date/logistics announcement is never a results release.
    r"release (?:date|schedule)|announces? details|details for the release|"
    r"conference call and webcast|"
    # 2026-09-30 (Q3 date-announcement wave): six first-party shapes slipped
    # through and minted false PENDING rows dated the PR day — COLB "Announces
    # Date of Third Quarter 2026 Earnings Release and Conference Call", MTB
    # "Announces Third Quarter 2026 Earnings Release and Conference Call",
    # TFC "announces third quarter 2026 earnings call details", FBK "Announces
    # 2026 Third Quarter Earnings Call", IBCP/CBK "Announces Date for (Its)
    # Third Quarter 2026 Earnings Release". The release layer then read
    # COLB's row as a REPORT and confirmed a Sep-30 release on the Calendar.
    r"announces? (?:the )?dates?\b|dates? (?:for|of) (?:its |the )?|"
    r"earnings release and (?:conference|webcast)|earnings call\b|call details|"
    # Aggregator preview shapes ("(ABCB) Q2 2026 Preview: EPS Est. $1.66,
    # Reports July 23") — belt behind the first-party source gate.
    r"preview|eps est|forecast|what to expect|ahead of earnings)", re.I)

# A RESULTS release names the results: "Reports Q2 2026 Results", "Announces
# Third Quarter Earnings", "8-K · Results of Operations", "Reports Net Income
# of …", "Reports Second Quarter" (MS). A first-party 'earnings'-typed event
# that names none of these (CAC 2026-09-29: "Announces its Third Quarter 2026
# Dividend", typed earnings by the "third quarter" keyword) is not a report
# and must never mint a row.
_RESULTS_CUE_RE = re.compile(
    r"\b(?:results?|earnings|net income|net loss|net profit)\b"
    r"|\breports?\s+(?:record\s+)?(?:first|second|third|fourth|q[1-4]|fiscal|"
    r"full[- ]year|year[- ]end)\b", re.I)


def is_results_headline(headline: str) -> bool:
    """True only for a headline that reads as a RESULTS release: names the
    results/earnings AND carries no upcoming-announcement cue. Both row
    builders below gate on this — a pending row claims the release is OUT."""
    h = headline or ""
    return bool(_RESULTS_CUE_RE.search(h)) and not _UPCOMING_CUE_RE.search(h)

# Only events from FIRST-PARTY sources may mark a bank reported/pending or
# supply its release link. Aggregator articles typed 'earnings'
# (google_news/yfinance_news: "(ABCB) Q2 2026 Preview … Reports July 23",
# 2026-07-20) minted false pending rows for banks reporting days later —
# no headline filter can contain third-party content shapes. Unknown or
# missing source = NOT first-party.
_FIRST_PARTY_SOURCES = {"sec_8k", "businesswire", "prnewswire",
                        "globenewswire", "fmp_news", "ir_site"}


def first_party_events(events_by_ticker: dict) -> dict:
    """events_by_ticker restricted to first-party sources (order kept)."""
    return {tk: [e for e in evs or []
                 if (e.get("source") or "") in _FIRST_PARTY_SOURCES]
            for tk, evs in (events_by_ticker or {}).items()}


def build_results_rows(fmp_rows, universe, events_by_ticker, today,
                       days_back: int = 30,
                       assets_by_ticker: dict | None = None) -> list[dict]:
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
          eps_basis (both None here — see score_eps), rev_act, rev_est,
          rev_surprise, rev_basis (both None here — see score_rev),
          reaction_session, pr_headline, pr_url, pending}
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
        rev_est = r.get("revenueEstimated")
        if (rev_act is not None and rev_est and rev_est > 0
                and not 0.2 <= rev_act / rev_est <= 5):
            # Scale-junk actual (HWC 2026-07-21: $26,850 posted vs $399M est
            # rendered "-100.0%"; FVCB $2M vs $19M; NPB $9M vs $67M): bank
            # REVENUE never lands an order of magnitude off consensus even in
            # a terrible quarter — that's EPS's job. The release fill still
            # supplies the bank's own stated revenue.
            rev_act = None
        ta = (assets_by_ticker or {}).get(tk)
        if (rev_act is not None and not rev_est and ta
                and rev_act < 0.0025 * ta):
            # ESTIMATE-LESS scale junk (Jul-22: FDBC $1,450; PKBK $3.9M vs
            # its release's stated $39.3M — a dropped digit): with no
            # consensus estimate the 0.2-5x guard above can't fire, so
            # sanity the actual against the bank's own balance sheet. Real
            # quarterly bank revenue never falls below 0.25% of total assets
            # (the lowest-NIM, no-fee banks run ≥0.5%; junk sits orders of
            # magnitude under). Blank → release/AI fill supplies the stated
            # figure; a lost real number renders '—', never a wrong one.
            rev_act = None
        if (rev_act is not None and not rev_est and ta
                and rev_act > 0.05 * ta):
            # ...and the UPPER bound (review 2026-10-06 P1-1: CMTV 2Q26
            # shipped $16.04B against a ~$14M quarter on $1.23B of assets —
            # ×1000). Real quarterly bank revenue runs ~1-3% of assets; 5%
            # leaves headroom for fee-heavy banks.
            rev_act = None
        # No estimate AND no assets anchor: nothing can sanity the FMP figure,
        # so it never displays — the release fill supplies the bank's own
        # stated revenue (labeled), else n/a. The raw value still marks the
        # bank as reported below (row existence is not a displayed number).
        rev_unanchored = rev_act is not None and not rev_est and not ta
        pr = pick_release_pr(events_by_ticker.get(tk) or [], d)
        pending = awaiting = False
        if eps_act is None and rev_act is None:
            if pr is not None and is_results_headline(pr.get("headline") or ""):
                # Bank's own results PR is out; consensus feed hasn't caught up.
                pending = True
            elif (today - d).days <= 2:
                # Scheduled date has arrived but nothing is published yet —
                # the bank still gets a ROW (owner 2026-07-17: "there never
                # can be stragglers"): visible as awaiting, flipping to
                # pending/reported the moment an 8-K, PR, or actual lands.
                # FMP re-projects slipped dates forward, and >2-day-old
                # quiet projections drop with them.
                awaiting = True
            else:
                continue
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
            # Scored only once the actual's GAAP-vs-adjusted basis is
            # confirmed against the release (score_eps, in the release fill).
            "eps_surprise": None,
            "eps_basis": None,
            "rev_act": None if rev_unanchored else rev_act,
            "rev_est": r.get("revenueEstimated"),
            # Same rule as EPS: scored only once the actual's basis is
            # confirmed against the release (score_rev, in the release fill).
            "rev_surprise": None,
            "rev_basis": None,
            "reaction_session": reaction_session(d, when).isoformat(),
            "pr_headline": (pr or {}).get("headline"),
            "pr_url": (pr or {}).get("url"),
            "pending": pending,
            "awaiting": awaiting,
        }
    # FMP's calendar is NOT the universe of reporters: banks FMP omits
    # entirely (MNSB 2026-07-20 — no calendar row in any window) still file
    # their 8-K, which the events feed carries as an 'earnings' event (the
    # sec_8k adapter classifies Item 2.02). Any universe ticker with a
    # RESULTS-shaped earnings event in the window and no FMP row gets a
    # pending row — the release fill then attaches the 8-K and supplies
    # actuals from the bank's own release.
    for tk, evs in (events_by_ticker or {}).items():
        if tk in best or tk not in uni:
            continue
        for e in evs or []:                          # newest-first
            ed = _iso_date(str(e.get("published_at") or "")[:10])
            if ed is None or not (floor <= ed <= today):
                continue
            if not is_results_headline(e.get("headline") or ""):
                continue        # date announcement / dividend / call notice, not results
            best[tk] = {
                "_d": ed,
                "ticker": tk,
                "date": ed.isoformat(),
                "when": None,
                "period_ending": None,
                "eps_act": None, "eps_est": None, "eps_surprise": None,
                "eps_basis": None,
                "rev_act": None, "rev_est": None, "rev_surprise": None,
                "rev_basis": None,
                "reaction_session": reaction_session(ed, None).isoformat(),
                "pr_headline": e.get("headline"),
                "pr_url": e.get("url"),
                "pending": True,
                "awaiting": False,
            }
            break
    rows = sorted(best.values(),
                  key=lambda x: (-x["_d"].toordinal(), x["awaiting"],
                                 x["ticker"]))
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
        if row.get("awaiting"):
            return          # nothing published — today's move is not a reaction
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


# "Equals" for two EPS figures: to the cent (FMP stores 0.65 or 0.6500).
_EPS_MATCH_TOL = 0.0101


def eps_basis(row: dict) -> str | None:
    """The confirmed basis of the row's EPS actual vs the bank's own release
    (review 2026-10-06 P0-1 — FMP's epsActual is GAAP-like in a notable-items
    quarter while epsEstimated is street/adjusted: JPM 2Q26 7.59 vs 5.59
    shipped "+35.8%" against a 6.14 ex-items actual). One of:
      'adjusted'    — the actual IS the release's adjusted / ex-items EPS
                      (the feed's figure equals it to the cent, or it was
                      filled from it);
      'gaap_only'   — the release mentions no adjusted EPS at all and the
                      actual equals its GAAP diluted EPS (when stated);
      'unconfirmed' — anything else: no attached release, an adjusted EPS
                      mentioned but not extractable (NTRS), the feed's figure
                      matching neither (JPM), or the release's GAAP figure
                      filled in (†);
      None          — no actual."""
    act = row.get("eps_act")
    if act is None:
        return None
    src = row.get("eps_act_src")
    if src == "release, adj.":
        return "adjusted"
    if src:
        return "unconfirmed"                  # release GAAP figure (†)
    rel = row.get("rel")
    if not rel:
        return "unconfirmed"
    m = rel.get("metrics") or {}
    adj, gaap = m.get("eps_adj"), m.get("eps_diluted")
    if adj is not None:
        return "adjusted" if abs(act - adj) <= _EPS_MATCH_TOL else "unconfirmed"
    if rel.get("eps_adj_stated") is False and (
            gaap is None or abs(act - gaap) <= _EPS_MATCH_TOL):
        return "gaap_only"
    return "unconfirmed"


def score_eps(row: dict) -> None:
    """Set row['eps_basis'] and row['eps_surprise'] — the surprise exists
    only on a confirmed basis; 'unconfirmed' renders n/a + a flag."""
    row["eps_basis"] = eps_basis(row)
    row["eps_surprise"] = (surprise_pct(row["eps_act"], row.get("eps_est"))
                           if row["eps_basis"] in ("adjusted", "gaap_only")
                           else None)


# "Equals" for two revenue figures: within 0.5% (the feed stores the
# release's thousands/millions figure; FTE-vs-GAAP gaps run ~0.5-2%).
_REV_MATCH_REL = 0.005


def revenue_basis(row: dict) -> str | None:
    """The confirmed basis of the row's revenue actual vs the bank's own
    release (2026-10-06 review follow-up — FMP's revenueActual has EPS's
    GAAP-vs-adjusted problem: ZION 2Q26 scored "+29.6%" on a GAAP-basis
    actual incl. $252M of Visa/SBIC gains — the release's NII + noninterest
    income is $1,137M — vs an ex-items consensus, while the release's
    adjusted revenue was $878M). One of:
      'adjusted'    — the actual matches the release's stated adjusted /
                      ex-items revenue within 0.5% (ONB $726.856M);
      'reported'    — the release mentions no adjusted revenue at all and
                      the actual matches one of its reported revenue lines
                      (GAAP / FTE total, net revenue, NII + noninterest
                      income) within 0.5% (WTFC $738.635M);
      'unconfirmed' — anything else: no attached release or a pre-v23
                      extraction, an adjusted revenue mentioned but not
                      extractable, a match to neither (ZION $1,137M vs
                      adjusted $878M), or the release's own figure filled in;
      None          — no actual."""
    act = row.get("rev_act")
    if act is None:
        return None
    if row.get("rev_act_src"):
        return "unconfirmed"                  # release figure filled in (*)
    rb = (row.get("rel") or {}).get("rev_basis")
    if not rb:
        return "unconfirmed"

    def _hit(v):
        return v is not None and v > 0 and abs(act - v) <= _REV_MATCH_REL * v

    if rb.get("adjusted") is not None:
        return "adjusted" if _hit(rb["adjusted"]) else "unconfirmed"
    if rb.get("adj_stated") is False and any(
            _hit(v) for v in rb.get("reported") or []):
        return "reported"
    return "unconfirmed"


def score_rev(row: dict) -> None:
    """Set row['rev_basis'] and row['rev_surprise'] — the surprise exists
    only on a confirmed basis; 'unconfirmed' renders n/a + a flag."""
    row["rev_basis"] = revenue_basis(row)
    row["rev_surprise"] = (surprise_pct(row["rev_act"], row.get("rev_est"))
                           if row["rev_basis"] in ("adjusted", "reported")
                           else None)


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
            rm = None
        _attach(row, rm)
        score_eps(row)          # every row — no release ⇒ basis unconfirmed
        score_rev(row)

    def _attach(row, rm):
        if rm and release_matches_report(rm.get("filed_date"), row["date"]):
            # An attached release IS the report — an "awaiting" row flips the
            # moment the 8-K lands, ahead of FMP and the wires.
            row["awaiting"] = False
            if not row.get("period_ending") and rm.get("qend"):
                row["period_ending"] = rm["qend"]    # FMP-less rows (MNSB)
            metrics = rm.get("metrics") or {}
            row["rel"] = {"qend": rm.get("qend"),
                          "metrics": metrics,
                          "capital": rm.get("capital") or {},
                          "prior_metrics": rm.get("prior_metrics") or {},
                          "prior_qend": rm.get("prior_qend"),
                          "yoy_metrics": rm.get("yoy_metrics") or {},
                          "yoy_qend": rm.get("yoy_qend"),
                          # None on a pre-v22 extraction → basis unconfirmed
                          "eps_adj_stated": rm.get("eps_adj_stated"),
                          # None on a pre-v23 extraction → basis unconfirmed
                          "rev_basis": rm.get("rev_basis"),
                          "url": rm.get("url")}
            # Actuals fill (owner, 2026-07-13): FMP's consensus feed lags a
            # fresh report ("pending") — the bank's own release already
            # states EPS and total revenue, so fill from it, LABELED via
            # *_src. Adjusted EPS preferred (the street's comparison basis);
            # GAAP marked as such. Values are extraction-guarded upstream.
            # FMP junk-EPS guard (NPB 2026-07-22: FMP posted $0.09 while the
            # bank's own release stated $0.60 diluted — shipped as a "−86.9%
            # surprise"). A GAAP-vs-adjusted gap explains small deltas, so
            # FMP's actual is junk only when it contradicts EVERY EPS figure
            # the release states by more than max($0.25, 50% of the stated
            # value); the release's own (extraction-guarded) number then
            # replaces it, labeled via eps_act_src.
            # Wrong-entity guard (review 2026-10-06 P2-3): PNFP's Q4-25 8-K
            # under the merged CIK is legacy Synovus ($1.22) while FMP's
            # $2.24 is Pinnacle's — and the swap shipped Synovus. The swap
            # needs independent evidence that FMP is the junk side: FMP more
            # than max($0.25, 50%) off the consensus estimate (NPB $0.09 vs
            # $0.677 est). An FMP actual consistent with consensus (PNFP 2.24
            # vs 2.26) that the release contradicts is an unresolved
            # conflict → n/a, never either number. (The spec'd NI/revenue
            # anchor cannot catch this pair: Synovus Q4-25 NI $171.1M /
            # revenue $629.7M vs Pinnacle Bank's Q3-25 FDIC $183.4M /
            # $546.6M — a merger of equals.)
            stated = [v for v in (metrics.get("eps_adj"),
                                  metrics.get("eps_diluted")) if v is not None]
            if (row.get("eps_act") is not None and stated
                    and all(abs(row["eps_act"] - v) > max(0.25, 0.5 * abs(v))
                            for v in stated)):
                est = row.get("eps_est")
                if est is not None and abs(row["eps_act"] - est) > \
                        max(0.25, 0.5 * abs(est)):
                    if metrics.get("eps_adj") is not None:
                        row["eps_act"] = metrics["eps_adj"]
                        row["eps_act_src"] = "release, adj."
                    else:
                        row["eps_act"] = metrics["eps_diluted"]
                        row["eps_act_src"] = "release, GAAP"
                else:
                    row["eps_act"] = None
                    row["eps_conflict"] = True
            if row.get("eps_conflict"):
                # The filing's figures are unproven as THIS bank's (PNFP:
                # legacy Synovus NI $171.1M / revenue $629.7M) — none of them
                # may reach the row, its exhibit or the export. Keep only the
                # link to the filing and the reason; no actuals fill.
                row["rel"] = {"qend": rm.get("qend"), "url": rm.get("url"),
                              "withheld": "conflict"}
                return
            if row.get("eps_act") is None:
                if metrics.get("eps_adj") is not None:
                    row["eps_act"] = metrics["eps_adj"]
                    row["eps_act_src"] = "release, adj."
                elif metrics.get("eps_diluted") is not None:
                    row["eps_act"] = metrics["eps_diluted"]
                    row["eps_act_src"] = "release, GAAP"
                if row.get("eps_act") is not None:
                    row["pending"] = False
            if row.get("rev_act") is None and \
                    metrics.get("total_revenue") is not None:
                row["rev_act"] = metrics["total_revenue"]
                row["rev_act_src"] = "release"     # never scored (score_rev)

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
        from data.bank_mapping import get_fdic_cert
    except Exception:
        return
    # repdte (YYYYMMDD) -> {cert: [(row, bucket), ...]}
    need: dict = {}
    for row in rows:
        rel = row.get("rel") or {}
        # Lookup-time resolver, NOT the universe snapshot: curated cert
        # corrections apply immediately (CBSH 2026-07-16 — the snapshot
        # served the wrong-entity cert until the nightly rebuild).
        try:
            cert = get_fdic_cert(row.get("ticker"))
        except Exception:
            cert = None
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


def _fill_sec_history(rows) -> None:
    """Attach ``row["sec_hist"] = {"prior": {...}, "yoy": {...}}`` with holdco
    TBV/BV per share for reported banks the pre-warmed ALLBANKS grid does NOT
    cover (RF 2026-07-17 — that grid's coverage is observe-only and has
    holes). The grid is read first (free); only the missing banks get a
    scoped live build (small cohorts build in seconds, cohort-cached 36h).
    Any failure leaves rows untouched — blank cells, never wrong ones."""
    try:
        from data.bank_mapping import get_cik
        from data.sec_per_share import sec_per_share_grid
    except Exception:
        return

    def _index(grid) -> dict:
        # {(ticker, 'Qn YYYY'): {"tbv_ps": v, "bv_ps": v}}
        out = {}
        if not grid or not isinstance(grid.get("rows"), list):
            return out
        labels = grid.get("labels") or []
        for r in grid["rows"]:
            tk, series = r.get("ticker"), r.get("series") or {}
            for i, lb in enumerate(labels):
                cell = {}
                for key, skey in (("tbv_ps", "tbvps_hist"), ("bv_ps", "bvps_hist")):
                    v = (series.get(skey) or [None] * len(labels))[i] \
                        if i < len(series.get(skey) or []) else None
                    if v is not None:
                        cell[key] = float(v)
                if cell:
                    out[(tk, lb)] = cell
        return out

    try:
        warmed = _index(sec_per_share_grid({}, 20, build_if_missing=False,
                                           scope_id="ALLBANKS"))
    except Exception:
        warmed = {}

    def _wanted(row):
        rel = row.get("rel") or {}
        for bucket, qend in (("prior", rel.get("prior_qend")),
                             ("yoy", rel.get("yoy_qend"))):
            lb = q_label(qend)
            if not lb:
                continue
            stated = rel.get(f"{bucket}_metrics") or {}
            if stated.get("tbv_ps") is None or stated.get("bv_ps") is None:
                yield bucket, lb

    missing_cohort: dict = {}
    for row in rows:
        tk = row.get("ticker")
        for bucket, lb in _wanted(row):
            if (tk, lb) in warmed:
                row.setdefault("sec_hist", {}).setdefault(bucket, {}).update(
                    warmed[(tk, lb)])
            else:
                try:
                    cik = get_cik(tk)
                except Exception:
                    cik = None
                if cik:
                    missing_cohort[int(cik)] = tk
    if not missing_cohort:
        return
    try:
        built = _index(sec_per_share_grid(missing_cohort, 8,
                                          build_if_missing=True))
    except Exception:
        return
    for row in rows:
        tk = row.get("ticker")
        if tk not in missing_cohort.values():
            continue
        for bucket, lb in _wanted(row):
            if (tk, lb) in built:
                row.setdefault("sec_hist", {}).setdefault(bucket, {}).update(
                    built[(tk, lb)])


def _board_key(days_back: int) -> str:
    # v3: rows gained `pending` (PR-signaled reports without FMP actuals).
    # v4: common-shares-only universe + negative-revenue junk guard.
    # v5: rows gained `fdic_hist` (quarterly-ratio exhibit history).
    # v6: rows gained `awaiting` (scheduled reporters visible pre-release).
    # v7: rows gained `sec_hist` (per-share history for grid-coverage holes).
    # v8: events-only discovery (reporters FMP's calendar omits entirely).
    # v9: first-party source gate (aggregator previews minted false rows).
    # v10: revenue scale-junk guard (HWC $26,850 vs $399M est).
    # v11: release-contradicted EPS guard + mis-itemized 8-K candidates
    #      (NPB filed its Q2 release under Items 2.01/7.01; FMP's $0.09
    #      stood against the release's $0.60).
    # v12: estimate-less revenue junk guard vs bank-sub assets (FDBC
    #      $1,450; PKBK $3.9M vs stated $39.3M — no estimate, so the
    #      0.2-5x guard couldn't fire).
    # v13: EPS surprise scored only on a confirmed GAAP-vs-adjusted basis
    #      (rows gained `eps_basis`, `eps_conflict`; rel gained
    #      `eps_adj_stated`), estimate-less revenue upper bound + no-anchor
    #      blanking, resolver-cert assets (review 2026-10-06).
    # v14: revenue surprise scored only on a confirmed basis (rows gained
    #      `rev_basis`; rel gained `rev_basis` facts), and a feed-vs-release
    #      conflict row's rel carries only {qend, url, withheld} — never the
    #      conflicting filing's figures (PNFP Q4-25 / legacy Synovus).
    return f"earnings_results_board_v14:{days_back}"


def results_board_available(days_back: int = 30) -> bool:
    """Whether a Results-board snapshot has ever been built.

    results_board() returns [] BOTH when no bank has reported in the window
    AND when the build failed with no last-good snapshot to fall back on, so an
    empty board alone cannot tell a genuine quiet window from a source outage.
    Callers that must show "unavailable" instead of a confident "nobody has
    reported" check this (AUDIT-2026-07-02 #34 class; same contract as
    data.estimates.earnings_calendar_available). Presence at ANY age answers
    the question, so this read carries no age ceiling. Never builds."""
    from data import cache as _cache
    try:
        snap = _cache.get(_board_key(days_back), max_age_s=None)
    except Exception:
        return False
    return bool(snap and isinstance(snap.get("value"), list))


def _build_results_board(days_back: int) -> list[dict]:
    """Build the Results-board rows (FMP calendar + events store + EDGAR
    release metrics + FDIC/SEC history). Raises on a total FMP-calendar
    failure so nothing is cached. Called by poll-events every ~30 min
    (refresh_results_board_snapshot) and, as a backstop, by results_board."""
    from data import cache as _cache
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
        # Bank-sub total assets (raw dollars; FDIC reports $thousands)
        # for the estimate-less revenue junk guard. Day-stale is fine —
        # it only anchors an order-of-magnitude sanity check. String
        # keys: the cache JSON round-trip stringifies dict keys.
        def _assets_by_cert():
            from data.fdic_client import list_all_active_institutions
            return {str(int(r["cert"])): float(r["asset"]) * 1000.0
                    for r in list_all_active_institutions()
                    if r.get("cert") and r.get("asset")}
        by_cert = _cache.served_snapshot(
            "fdic_assets_by_cert_v1", 86400, _assets_by_cert) or {}
        # Lookup-time resolver, NOT the universe snapshot's cert (same rule
        # as _fill_fdic_history): a stale snapshot cert anchored CARV/UNB to
        # a ~$115M/$251M charter (real $676M/$1.62B) — under the 5% upper
        # bound that would blank real revenue — and CMTV had no snapshot
        # cert at all.
        from data.bank_mapping import get_fdic_cert
        assets = {}
        for tk in get_universe():
            try:
                c = get_fdic_cert(tk)
            except Exception:
                c = None
            if c and by_cert.get(str(int(c))):
                assets[tk] = by_cert[str(int(c))]
    except Exception:
        assets = {}
    try:
        from data.events.store import get_events_by_type
        events: dict = {}
        for e in get_events_by_type("earnings", limit=800):
            tk = e.get("ticker")
            if tk:
                events.setdefault(tk, []).append(e)   # store order: newest-first
        events = first_party_events(events)
    except Exception:
        events = {}
    rows = build_results_rows(fmp_rows, universe, events, today,
                              days_back=days_back,
                              assets_by_ticker=assets)
    _fill_price_reactions(rows, today)
    _fill_release_metrics(rows)
    _fill_fdic_history(rows)
    _fill_sec_history(rows)
    return rows


# poll-events rebuilds the board every ~30 min (refresh_results_board_snapshot),
# so a render serves the stored board up to this age and never builds it on
# the interactive path while the job is healthy. Past it (the job stopped), the
# render rebuilds inline exactly as before — self-healing, never frozen.
# REVIEW-2026-09-24 P1-9: the old 15-min render TTL meant the first Earnings
# view each 15 min paid the full FMP + EDGAR + FDIC build.
_BOARD_RENDER_MAX_AGE_S = 2 * 3600


def refresh_results_board_snapshot(days_back: int = 30) -> int:
    """Build + persist the Results board (poll-events, every ~30 min). Returns
    the row count. Raises on a total source failure — nothing is written, so the
    last good board stays in place."""
    from datetime import datetime
    from data import cache as _cache
    rows = _build_results_board(days_back)
    _cache.put(_board_key(days_back),
               {"cached_at": datetime.now().isoformat(), "guard": None, "value": rows})
    return len(rows)


def results_board(days_back: int = 30) -> list[dict]:
    """The Results board rows, served from the poll-events snapshot (see
    _BOARD_RENDER_MAX_AGE_S) and rebuilt inline only when it is older than
    that or absent. Empty list when nothing has
    reported in the window; on total source failure the LAST GOOD board is
    served at whatever age (an empty board reads as "nobody reported", which
    is a confident wrong statement during an outage). A genuine FMP-calendar
    failure raises out of the build so it is never cached (house pattern).
    When there is no last-good either, callers must use
    results_board_available() to say "unavailable" rather than "none yet"."""
    from data import cache as _cache

    def _build():
        return _build_results_board(days_back)

    key = _board_key(days_back)
    try:
        return _cache.served_snapshot(key, _BOARD_RENDER_MAX_AGE_S, _build) or []
    except Exception as e:
        # The build raised — by design on a total source failure (an FMP
        # calendar outage), so nothing was cached. Serve the last good board at
        # WHATEVER age instead of []: an empty board renders as "no universe
        # bank has reported", a confident wrong statement mid-outage
        # (AUDIT-2026-07-02 #34 class, reintroduced on this surface and caught
        # by the 2026-07-27 audit). No last-good → [] plus
        # results_board_available() == False, which the UI renders as
        # "unavailable" rather than a quiet window.
        print(f"[results] board build failed: {type(e).__name__}: {e}")
        try:
            snap = _cache.get(key, max_age_s=None)
        except Exception:
            snap = None
        if isinstance(snap, dict) and isinstance(snap.get("value"), list):
            return snap["value"]
        return []
