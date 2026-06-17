"""
FOMC policy + Summary of Economic Projections (SEP), all from FRED.

Three public entry points behind Market & Macro's Fed/policy view:

- `fed_policy_snapshot()` — the current target band, effective rate, the last
  policy move (detected from the DFEDTARU step history), and the next FOMC
  decision day (from the verified static 2026 schedule).
- `sep_projections()` — the latest SEP: the federal-funds dot path (median +
  central-tendency band + full range) per horizon, plus the macro median
  projections (GDP / unemployment / PCE / core PCE) per horizon.
- `sep_dots()` — a best-effort extraction of the individual participant dots
  from the SEP PDF, gated against the FRED medians. Returns None whenever the
  extraction is unavailable or fails the gate — the caller falls back to the
  median+band+range view. We never fabricate dots.

FRED's by-horizon SEP series date each observation at the projection-year start
(2026-01-01, 2027-01-01, ...). The current release's horizons are therefore the
observations dated on/after the current calendar year. Longer-run series carry
one observation per meeting; we take the latest.
"""

import re
from datetime import date

import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup

from data.fred_client import fetch_series
from data.http import get_with_retry


# Verified 2026 FOMC decision days (second/decision day of each meeting).
# Covering 2026 only is sufficient for the current go-live horizon.
FOMC_2026_DECISION_DAYS = [
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 10, 28),
    date(2026, 12, 9),
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _latest(df: pd.DataFrame) -> tuple[date | None, float | None]:
    """Return (date, value) of the most recent non-null observation, or (None, None)."""
    if df is None or df.empty:
        return None, None
    valid = df.dropna(subset=["value"])
    if valid.empty:
        return None, None
    row = valid.iloc[-1]
    d = row["date"]
    d = d.date() if hasattr(d, "date") else d
    return d, float(row["value"])


def _latest_value(series_id: str, years: int = 2) -> float | None:
    return _latest(fetch_series(series_id, years=years))[1]


def _horizon_values(series_id: str, current_year: int) -> list[tuple[str, float]]:
    """
    By-horizon SEP series: observations are dated at each projection year's
    start (YYYY-01-01). Return [(year_label, value), ...] for every observation
    dated on/after `current_year` — i.e. the latest release's horizons, in
    chronological order. Empty list if the series is empty.
    """
    df = fetch_series(series_id, years=2)
    if df is None or df.empty:
        return []
    valid = df.dropna(subset=["value"]).copy()
    if valid.empty:
        return []
    out = []
    for _, row in valid.iterrows():
        d = row["date"]
        yr = d.year
        if yr >= current_year:
            out.append((str(yr), float(row["value"])))
    return out


# ---------------------------------------------------------------------------
# 1) policy snapshot
# ---------------------------------------------------------------------------
def _detect_last_move(df: pd.DataFrame) -> dict:
    """
    Scan an upper-bound (DFEDTARU) history for the most recent step change.
    Returns {"direction", "bps", "date"}.
    """
    if df is None or df.empty:
        return {"direction": "hold", "bps": 0, "date": None}
    valid = df.dropna(subset=["value"]).reset_index(drop=True)
    if valid.empty:
        return {"direction": "hold", "bps": 0, "date": None}

    # Walk backwards to find the most recent date where the level changed from
    # its prior distinct level.
    values = valid["value"].tolist()
    dates = valid["date"].tolist()
    for i in range(len(values) - 1, 0, -1):
        new = values[i]
        old = values[i - 1]
        if new != old:
            d = dates[i]
            d = d.date() if hasattr(d, "date") else d
            bps = round((new - old) * 100)
            direction = "cut" if new < old else "hike"
            return {"direction": direction, "bps": int(bps), "date": d}
    return {"direction": "hold", "bps": 0, "date": None}


def next_meeting(today: date | None = None) -> date | None:
    """Earliest 2026 FOMC decision day strictly after `today` (defaults to today)."""
    if today is None:
        today = date.today()
    upcoming = [d for d in FOMC_2026_DECISION_DAYS if d > today]
    return min(upcoming) if upcoming else None


def fed_policy_snapshot() -> dict:
    """Current target band, effective rate, last move, and next meeting."""
    upper_df = fetch_series("DFEDTARU", years=3)
    upper_date, upper_val = _latest(upper_df)
    lower_val = _latest_value("DFEDTARL", years=3)
    effective = _latest_value("DFF", years=1)

    return {
        "target_upper": upper_val,
        "target_lower": lower_val,
        "effective": effective,
        "as_of": upper_date,
        "last_move": _detect_last_move(upper_df),
        "next_meeting": next_meeting(),
    }


# ---------------------------------------------------------------------------
# 2) SEP projections
# ---------------------------------------------------------------------------
def _funds_horizons(current_year: int) -> list[dict] | None:
    """
    Build the funds dot-path entries (per horizon + Longer run). Returns None
    only if the median series is empty (the required spine).
    """
    median = dict(_horizon_values("FEDTARMD", current_year))
    ct_low = dict(_horizon_values("FEDTARCTL", current_year))
    ct_high = dict(_horizon_values("FEDTARCTH", current_year))
    range_low = dict(_horizon_values("FEDTARRL", current_year))
    range_high = dict(_horizon_values("FEDTARRH", current_year))

    if not median:
        return None

    entries = []
    for yr in sorted(median.keys()):
        entries.append({
            "horizon": yr,
            "median": median.get(yr),
            "ct_low": ct_low.get(yr),
            "ct_high": ct_high.get(yr),
            "range_low": range_low.get(yr),
            "range_high": range_high.get(yr),
        })

    # Longer run: one obs per meeting → take latest of each series.
    lr_median = _latest_value("FEDTARMDLR")
    if lr_median is not None:
        entries.append({
            "horizon": "Longer run",
            "median": lr_median,
            "ct_low": _latest_value("FEDTARCTLLR"),
            "ct_high": _latest_value("FEDTARCTHLR"),
            "range_low": _latest_value("FEDTARRLLR"),
            "range_high": _latest_value("FEDTARRHLR"),
        })
    return entries


def _macro_medians(series_id: str, current_year: int) -> list[dict] | None:
    """[{"horizon": year, "median": value}, ...] or None if the series is empty."""
    vals = _horizon_values(series_id, current_year)
    if not vals:
        return None
    return [{"horizon": yr, "median": v} for yr, v in vals]


def sep_projections() -> dict:
    """The latest Summary of Economic Projections, from FRED."""
    # Release date = latest longer-run obs date (one per meeting). Use it both
    # as the as_of and to derive the current calendar year for horizon filtering.
    lr_df = fetch_series("FEDTARMDLR", years=2)
    as_of, _ = _latest(lr_df)
    current_year = as_of.year if as_of is not None else date.today().year

    return {
        "as_of": as_of,
        "funds": _funds_horizons(current_year),
        "macro": {
            "gdp": _macro_medians("GDPC1CTM", current_year),
            "unemployment": _macro_medians("UNRATEMD", current_year),
            "pce": _macro_medians("PCECTPICTM", current_year),
            "core_pce": _macro_medians("JCXFECTM", current_year),
        },
    }


# ---------------------------------------------------------------------------
# 3) SEP dots (best-effort PDF extraction, gated)
# ---------------------------------------------------------------------------
def _pdf_lib() -> str | None:
    """Return the name of the first installed PDF library, or None."""
    for lib in ("pdfplumber", "fitz", "pypdf", "pdfminer"):
        try:
            __import__(lib)
            return lib
        except Exception:
            continue
    return None


def _extract_dots(as_of: date) -> dict | None:
    """
    Best-effort extraction of individual participant dots from the SEP PDF.

    Returns {"2026":[...], "2027":[...], "2028":[...], "Longer run":[...]} of
    floats, or None if no PDF library is available or extraction is not
    reliably doable.

    The SEP dot plot is a vector figure: the dot markers are not encoded as
    extractable text/numbers, so robustly recovering per-participant values
    from the PDF is not reliably achievable with a text-extraction library.
    We therefore return None here rather than fabricating dots. This helper is
    kept as a separate, monkeypatchable seam so the gate in `sep_dots()` can be
    tested independently and so a future, verified extractor can drop in.
    """
    if _pdf_lib() is None:
        return None
    # No reliable text-based extraction of vector dot markers — do not guess.
    return None


def sep_dots() -> dict | None:
    """
    Attempt to extract the individual SEP participant dots, gated against the
    FRED medians. Returns the dots only if every horizon's extracted median
    matches the FRED funds median within 0.05; otherwise None.
    """
    if _pdf_lib() is None:
        return None

    proj = sep_projections()
    as_of = proj.get("as_of")
    if as_of is None:
        return None

    extracted = _extract_dots(as_of)
    if not extracted:
        return None

    # Gate: extracted per-horizon median must match the FRED funds median.
    fred_median = {}
    for entry in (proj.get("funds") or []):
        m = entry.get("median")
        if m is not None:
            fred_median[entry["horizon"]] = m

    for horizon, dots in extracted.items():
        if not dots:
            return None
        ext_med = _median(dots)
        fred_med = fred_median.get(horizon)
        if fred_med is None:
            return None
        if abs(ext_med - fred_med) > 0.05:
            return None

    return {"as_of": as_of, "dots": extracted}


def _median(values: list[float]) -> float:
    """Median of a non-empty numeric list."""
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return float((s[mid - 1] + s[mid]) / 2.0)


# ---------------------------------------------------------------------------
# 4) latest FOMC policy statement (scraped from federalreserve.gov)
# ---------------------------------------------------------------------------
# Boilerplate fragments that appear as their own <p> in the press release shell
# but are not part of the substantive policy statement.
_STATEMENT_BOILERPLATE = (
    "last update",
    "implementation note",
    "for media inquiries",
    "for release at",
)

# A bare date header line, e.g. "June 17, 2026" (optionally trailed by "Share").
_DATE_HEADER_RE = re.compile(
    r"^[A-Z][a-z]+ \d{1,2}, \d{4}(?:\s+Share)?$"
)

# A vote tally on the "approved ... by a 12-0 vote" page format. The separator
# is some dash/space variant (en-dash often arrives mojibake'd), so match any
# non-digit run between the two counts.
_VOTE_TALLY_RE = re.compile(r"by an?\s+\d+\D{1,6}\d+\s+vote", re.IGNORECASE)


def _statement_date(today: date | None = None) -> date | None:
    """
    Latest FOMC decision day on/before `today` (defaults to today). Returns None
    if `today` precedes the first 2026 meeting.
    """
    if today is None:
        today = date.today()
    past = [d for d in FOMC_2026_DECISION_DAYS if d <= today]
    return max(past) if past else None


def _statement_url(dt: date) -> str:
    """Federal Reserve press-release URL for a given statement date."""
    return (
        "https://www.federalreserve.gov/newsevents/pressreleases/"
        f"monetary{dt.strftime('%Y%m%d')}a.htm"
    )


def _is_boilerplate(text: str) -> bool:
    stripped = text.strip()
    if stripped.lower() == "share":
        return True
    if _DATE_HEADER_RE.match(stripped):
        return True
    low = stripped.lower()
    return any(frag in low for frag in _STATEMENT_BOILERPLATE)


def _parse_statement_html(html: str, url: str, dt: date) -> dict | None:
    """
    Pure parse of a Fed press-release page into the statement dict, or None if
    no substantive paragraphs are found. No network — testable in isolation.
    """
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    # The statement body lives in the main article column. Prefer an explicit
    # #article div; fall back to the Bootstrap content column.
    article = soup.find(id="article")
    if article is None:
        for cls in ("col-md-8", "col-xs-12"):
            article = soup.find("div", class_=lambda c: c and cls in c)
            if article is not None:
                break
    if article is None:
        return None

    paragraphs: list[str] = []
    vote: str | None = None
    vote_is_roster = False  # the canonical "Voting for ... were" roster wins
    for p in article.find_all("p"):
        text = p.get_text(" ", strip=True)
        if not text:
            continue
        if _is_boilerplate(text):
            continue
        low = text.lower()
        # Canonical voting roster (Implementation Note style): capture it and
        # keep it out of the prose list.
        if low.startswith("voting for the monetary policy action were"):
            vote = text
            vote_is_roster = True
            continue
        if vote_is_roster and low.startswith("voting against"):
            vote = f"{vote} {text}"
            continue
        # Fallback vote line on the "approved ... by an N-0 vote" page format.
        # This sentence is genuine statement prose, so it stays in `paragraphs`
        # AND is surfaced as the vote string when no roster line is present.
        if not vote_is_roster and _VOTE_TALLY_RE.search(text):
            vote = text
        paragraphs.append(text)

    if not paragraphs:
        return None

    return {"date": dt, "url": url, "paragraphs": paragraphs, "vote": vote}


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_fomc_statement(today: date | None = None) -> dict | None:
    """
    The latest FOMC policy statement (prose paragraphs + vote line), scraped
    from federalreserve.gov. Returns None on any failure or if no statement
    has been issued yet this year.

    Statements are immutable once published, so a 6h cache is ample.
    """
    dt = _statement_date(today)
    if dt is None:
        return None
    url = _statement_url(dt)
    try:
        resp = get_with_retry(url, timeout=15)
    except Exception:
        return None
    if resp is None:
        return None
    # Fed pages are UTF-8; requests can mis-guess to ISO-8859-1 when the header
    # omits a charset, mojibake'ing en-dashes. Prefer the apparent encoding.
    if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "latin-1"):
        resp.encoding = resp.apparent_encoding or "utf-8"
    return _parse_statement_html(resp.text, url, dt)


__all__ = [
    "fed_policy_snapshot",
    "sep_projections",
    "sep_dots",
    "next_meeting",
    "fetch_fomc_statement",
    "FOMC_2026_DECISION_DAYS",
]
