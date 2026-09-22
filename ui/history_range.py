"""Deep-history range control + depth helpers (DEEP-HISTORY-PLAN.md, UI
phases 1-2; owner scope call 2026-09-22: statement tables + the four
dynamics tabs get a 5Y / 10Y / 20Y / MAX picker, default 5Y, absent fields
annotated + n/a).

One picker, one quarters map, one loader path — shared by six surfaces so a
range label means the same thing everywhere and the default range keeps
each page on exactly the warm-cache read it makes today (zero slower).
Deeper ranges read the backfilled store through data.loaders.load_fdic_hist
(the multi-charter seam is inside it — never bypassed here).

Validation at depth (plan §Validation): `structure_breaks` flags >50% q/q
total-asset jumps (merger / charter change) so a cliff is labeled, not
silently charted; `series_notes` / `first_live_index` annotate series that
begin after the window starts ("from Q1 '10"), never fabricating the gap.
"""
from __future__ import annotations

import pandas as pd

# Label → quarters. None = everything the store holds.
RANGE_QUARTERS = {"2Y": 8, "5Y": 20, "10Y": 40, "20Y": 80, "MAX": None}
# Above the 160-quarter backfill limit: "everything stored", bounded.
MAX_QUARTERS = 200
# Today's warm window — a request at or under it is the untouched hot path.
WARM_QUARTERS = 20

CHART_RANGES = ("5Y", "10Y", "20Y", "MAX")
TABLE_RANGES_ANNUAL = ("5Y", "10Y", "20Y", "MAX")
# Quarterly tables show 8 columns today (= 2Y), so 2Y is their default.
TABLE_RANGES_QUARTERLY = ("2Y", "5Y", "10Y", "20Y", "MAX")
DEFAULT_ANNUAL = "5Y"
DEFAULT_QUARTERLY = "2Y"
DEFAULT_CHART = "5Y"


def range_years(label: str) -> int | None:
    """Years in a range label; None for MAX (unbounded)."""
    q = RANGE_QUARTERS.get(label, RANGE_QUARTERS[DEFAULT_CHART])
    return None if q is None else q // 4


def quarters_needed(label: str, period: str = "Quarterly") -> int:
    """Quarters a surface must load to fill the range. Annual views need N
    December rows, which can sit up to 4N+3 quarters back from a Q3 latest —
    4N+4 keeps the existing 44-quarter statement load for 10Y."""
    q = RANGE_QUARTERS.get(label)
    if q is None:
        return MAX_QUARTERS
    return q + 4 if period == "Annual" else q


def range_picker(key: str, options=CHART_RANGES, default: str = DEFAULT_CHART) -> str:
    """The house segmented control (same widget as the statement-trend
    timeframe picker). A deselected control returns None → default."""
    import streamlit as st
    pick = st.segmented_control("History range", list(options), default=default,
                                key=key, label_visibility="collapsed")
    return pick if pick in options else default


def load_hist_for_range(ticker: str, label: str, period: str = "Quarterly",
                        floor: int = WARM_QUARTERS) -> list[dict]:
    """Group-consolidated FDIC history deep enough for `label`, newest first.

    `floor` is the depth the page loads today regardless of range (statement
    engines load 44 / 36 quarters for their trend charts) so the default
    range never loads LESS than before. A request inside the warm window
    is exactly today's load_fdic_hist(ticker) call. Deeper requests ask the
    store with min_quarters=8: a bank whose whole stored history is shorter
    than the range (listed eight years ago, 10Y asked) gets ALL of it, not a
    20-quarter fallback — honest available depth."""
    from data.loaders import load_fdic_hist
    limit = max(int(floor), quarters_needed(label, period))
    if limit <= WARM_QUARTERS:
        return load_fdic_hist(ticker)
    return load_fdic_hist(ticker, min_quarters=8, limit=limit)


def load_hist_df_for_range(ticker: str, label: str, period: str = "Quarterly",
                           floor: int = WARM_QUARTERS):
    """The statement engines' read: data.loaders.load_fdic_hist_df — the
    exact seam they read today (test fixtures stub it), asked for
    max(floor, quarters the range needs). The default range is therefore
    byte-for-byte today's call (44 / 36 quarters)."""
    import data.loaders as dl
    return dl.load_fdic_hist_df(ticker, max(int(floor), quarters_needed(label, period)))


def first_live_index(live_flags) -> int:
    """Index of the first column that has a value, -1 when none do. A row
    whose leading columns are dead and later ones live is a series that
    begins mid-window — the caller annotates 'from <label>'."""
    for i, live in enumerate(live_flags):
        if live:
            return i
    return -1


def qlabel(ts) -> str:
    """Q3 '16 — the house short quarter label."""
    t = pd.Timestamp(ts)
    return f"Q{(t.month - 1) // 3 + 1} '{str(t.year)[2:]}"


def structure_breaks(records: list[dict], threshold: float = 0.5,
                     field: str = "ASSET", since=None) -> list[dict]:
    """Quarter-over-quarter level jumps ≥ `threshold` (50%) in `field`, in
    date order: [{"repdte": Timestamp, "pct": +1.8}]. Pairs with an absent
    or non-positive value are skipped (no ratio, no flag — never guessed).
    Consecutive stored quarters only: a gap in the series is not a break.
    `since` keeps only breaks on/after that date (the displayed window)."""
    rows = []
    for r in records or []:
        d = pd.to_datetime(r.get("REPDTE"), errors="coerce")
        if pd.isna(d):
            continue
        try:
            v = float(r.get(field)) if r.get(field) is not None else None
        except (TypeError, ValueError):
            v = None
        rows.append((d.normalize(), v))
    rows.sort(key=lambda t: t[0])
    out = []
    for (d0, v0), (d1, v1) in zip(rows, rows[1:]):
        if v0 is None or v1 is None or v0 <= 0 or v1 <= 0:
            continue
        if (d1 - d0).days > 100:      # not consecutive quarters
            continue
        pct = v1 / v0 - 1.0
        if abs(pct) >= threshold:
            out.append({"repdte": d1, "pct": pct})
    if since is not None:
        s = pd.Timestamp(since)
        out = [b for b in out if b["repdte"] >= s]
    return out


def series_notes(df: pd.DataFrame, cols, date_col: str = "date") -> list[str]:
    """'<label> from Q1 '10' for each plotted column whose first non-null
    date is later than the window's first date. Columns absent or entirely
    null are skipped (the chart already omits them)."""
    if df is None or df.empty or date_col not in df.columns:
        return []
    dates = pd.to_datetime(df[date_col], errors="coerce")
    start = dates.min()
    notes = []
    for col, label in cols:
        if col not in df.columns:
            continue
        live = dates[df[col].notna() & dates.notna()]
        if live.empty:
            continue
        first = live.min()
        if pd.notna(start) and first > start:
            notes.append(f"{label} from {qlabel(first)}")
    return notes


def describe_window(span: str, breaks=(), notes=(),
                    entity: str = "bank-subsidiary call reports") -> str:
    """One caption line for a deep view: span, entity, series starts, and
    any structure break — the validation banner the plan calls for.
    `span` is the caller's own count/label text (quarters for charts,
    columns for tables)."""
    parts = []
    if span:
        parts.append(f"{span} · {entity}")
    if notes:
        parts.append("Series begin later: " + "; ".join(notes))
    for b in breaks:
        parts.append(f"Structure break: total assets {b['pct']:+.0%} q/q at "
                     f"{qlabel(b['repdte'])} (merger / charter change) — levels "
                     "before and after are not comparable")
    return " · ".join(parts)


def table_range_picker(period: str, key_base: str) -> tuple[str, str]:
    """The statement-table picker: Annual 5Y/10Y/20Y/MAX (5 FY today),
    Quarterly 2Y/5Y/10Y/20Y/MAX (8 quarters today). Returns (picked,
    default) so the caller knows whether it is on the untouched default."""
    if period == "Annual":
        return (range_picker(f"{key_base}_a", TABLE_RANGES_ANNUAL, DEFAULT_ANNUAL),
                DEFAULT_ANNUAL)
    return (range_picker(f"{key_base}_q", TABLE_RANGES_QUARTERLY, DEFAULT_QUARTERLY),
            DEFAULT_QUARTERLY)


def entity_note(ticker: str) -> str:
    """Label the entity honestly at depth. A multi-charter group's deep
    series is TODAY's charter set summed at every quarter — before a charter
    joined the holdco its figures are that bank's own, so the series is pro
    forma, and the caption says so rather than implying a reported
    consolidated history."""
    try:
        from data.cert_group import get_cert_group
        n = len(get_cert_group(ticker) or [])
    except Exception:
        n = 0
    if n > 1:
        return (f"bank-subsidiary call reports · {n} charters combined "
                "(today's charter group summed at every quarter — pro forma "
                "before each charter joined)")
    return "bank-subsidiary call reports"


def span_quarters(dates) -> str:
    """'40 quarters · Q3 '16 – Q2 '26' for a chart timeline's date column."""
    ds = pd.to_datetime(pd.Series(list(dates)), errors="coerce").dropna()
    if ds.empty:
        return ""
    return f"{len(ds)} quarters · {qlabel(ds.min())} – {qlabel(ds.max())}"


def chart_timeline(ticker: str, rng: str, base_timeline, build, notes_cols=(),
                   **build_kw):
    """(timeline for the charts, caption or None). On the default range the
    page's own 20-quarter timeline is returned untouched (headline metrics,
    alerts and the charts stay exactly today's). Deeper: rebuild the SAME
    timeline from the deep read so the charts extend while the headline
    math above them keeps its 5Y basis; a bank with nothing deeper stored
    keeps the base timeline (honest available depth)."""
    if rng == DEFAULT_CHART:
        return base_timeline, None
    recs = load_hist_for_range(ticker, rng)
    if not recs:
        return base_timeline, None
    tl = build(recs, **build_kw)
    if tl is None or tl.empty:
        return base_timeline, None
    cap = describe_window(span_quarters(tl["date"]),
                          breaks=structure_breaks(recs, since=tl["date"].min()),
                          notes=series_notes(tl, notes_cols),
                          entity=entity_note(ticker))
    return tl, cap
